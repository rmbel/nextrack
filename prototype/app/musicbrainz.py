"""Bounded live candidate retrieval from MusicBrainz recording metadata.

Queries contain validated musical preferences only. Provider search scores are
not recommendation scores; final selection stays in NextTrack's ranker.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from functools import lru_cache
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .catalog import CATALOG, CATALOG_GENRES, TRACK_BY_ID, normalize
from .catalog_quality import recording_key, prepare_catalog
from .discovery_store import discovery_store
from .metadata import enrich_track, recording_metadata
from .redis_rest import StorageError
from .settings import server_settings

FRESH_SECONDS = 86400
RETAIN_SECONDS = 30 * 86400
MBID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
DESCRIPTORS = {'instrumental', 'acoustic'}


@dataclass
class DiscoveryResult:
    tracks: list = field(default_factory=list)
    status: str = 'not_requested'
    query: str | None = None
    total_matches: int | None = None
    network_calls: int = 0
    elapsed_ms: float = 0
    warning: str | None = None

    def metadata(self):
        return {'provider': 'musicbrainz', 'status': self.status, 'query': self.query,
                'total_matches': self.total_matches, 'candidate_count': len(self.tracks),
                'network_calls': self.network_calls, 'elapsed_ms': self.elapsed_ms}


def enabled():
    return server_settings().get('NEXTTRACK_ONLINE_CATALOG', '1').lower() not in {'0', 'false', 'off'}


def quote_term(value):
    # Normalised vocabulary, not raw prompt text or executable query syntax.
    return '"' + normalize(value) + '"'


def build_query(signals, seeds=()):
    genres = list(dict.fromkeys(signals.get('preferred_genres', [])))
    descriptors = set(genres) & DESCRIPTORS
    descriptors.update(set(signals.get('preferred_tags', [])) & DESCRIPTORS)
    if signals.get('preferred_language') == 'instrumental':
        descriptors.add('instrumental')
    genres = [g for g in genres if g not in DESCRIPTORS]
    if not genres:
        genres = list(dict.fromkeys(t['genre'] for t in seeds if t.get('genre') not in {'unknown', *DESCRIPTORS}))[:4]
    terms = []
    if genres:
        terms.append('(' + ' OR '.join('tag:' + quote_term(g) for g in genres[:8]) + ')')
    terms.extend('tag:' + quote_term(d) for d in sorted(descriptors))
    # A mood-only prompt can still discover explicitly tagged recordings.
    if not terms and signals.get('preferred_tags'):
        terms.append('(' + ' OR '.join('tag:' + quote_term(t) for t in signals['preferred_tags'][:4]) + ')')
    if not terms:
        return None
    for genre in signals.get('excluded_genres', [])[:8]:
        terms.append('NOT tag:' + quote_term(genre))
    lower, upper = signals.get('min_year'), signals.get('max_year')
    if signals.get('recency') == 'recent':
        lower = max(lower or 0, 2020)
    elif signals.get('recency') == 'classic':
        upper = min(upper or 9999, 2010)
    if lower is not None or upper is not None:
        terms.append(f'firstreleasedate:[{str(lower) + "-01-01" if lower is not None else "*"} TO {str(upper) + "-12-31" if upper is not None else "*"}]')
    return ' AND '.join([*terms, 'status:official', 'NOT video:true'])


def normalise_recording(item, preferred_genres=()):
    if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not MBID.fullmatch(item['id']):
        return None
    title = item.get('title')
    credits = item.get('artist-credit')
    if not isinstance(title, str) or not title.strip() or len(title) > 1000 or not isinstance(credits, list):
        return None
    valid = [c for c in credits if isinstance(c, dict) and isinstance(c.get('artist'), dict)
             and isinstance(c.get('name', c['artist'].get('name')), str)]
    artist = ''.join(c.get('name', c['artist'].get('name', '')) + (c.get('joinphrase') or '') for c in valid)
    if not artist.strip() or len(artist) > 2000:
        return None
    tags = [t for t in item.get('tags', []) if isinstance(t, dict) and isinstance(t.get('name'), str)
            and type(t.get('count')) in (int, float) and t['count'] > 0]
    vocabulary = {normalize(g): g for g in CATALOG_GENRES}
    genres = list(dict.fromkeys(vocabulary[normalize(t['name'])] for t in tags if normalize(t['name']) in vocabulary))
    order = {g: i for i, g in enumerate(preferred_genres)}
    genres.sort(key=lambda g: (g in DESCRIPTORS, order.get(g, 999), g))
    date = item.get('first-release-date', '')
    year = int(date[:4]) if isinstance(date, str) and re.match(r'^\d{4}(?:-|$)', date) and 0 < int(date[:4]) <= 9999 else None
    track = {'id': 'musicbrainz:' + item['id'], 'source': 'musicbrainz', 'source_id': item['id'],
             'source_url': 'https://musicbrainz.org/recording/' + item['id'], 'title': title.strip(),
             'artist': artist.strip(), 'artist_credits': [c['artist'] | {'source': 'musicbrainz'} for c in valid],
             'genre': genres[0] if genres else 'unknown', 'subgenres': genres[1:], 'year': year,
             'recording_disambiguation': item.get('disambiguation', ''), 'source_tags': tags,
             'metadata_quality': {'genre': 'community-tag' if genres else 'unknown'},
             'metadata_evidence': {'year': {'source': 'musicbrainz-first-release-date', 'value': date}} if year else {}}
    return enrich_track(track, recording_metadata({**item, 'tags': tags}))


@lru_cache(maxsize=1)
def local_recordings():
    return {recording_key(t): t for t in CATALOG}


def canonical_candidates(tracks):
    # Preserve richer existing traits on known identities; aliases also exclude
    # selected seed recordings. Conservative version labels remain distinct.
    by_key = local_recordings()
    result = []
    seen = set()
    for track in prepare_catalog(tracks).tracks:
        existing = TRACK_BY_ID.get(track['id']) or by_key.get(recording_key(track))
        chosen = existing or track
        if chosen['id'] not in seen:
            seen.add(chosen['id']); result.append(chosen)
    return result


def cached_track(track_id):
    if not track_id.startswith('musicbrainz:') or not MBID.fullmatch(track_id.split(':', 1)[1]):
        return None
    try:
        value = discovery_store().get('track:' + track_id)
        return value if valid_cached_tracks([value]) and value.get('id') == track_id else None
    except StorageError:
        return None


def valid_cached_tracks(value):
    return isinstance(value, list) and len(value) <= 100 and all(
        isinstance(t, dict) and all(isinstance(t.get(k), str) and t[k] for k in ('id', 'title', 'artist', 'genre'))
        and 'year' in t and (t['year'] is None or type(t['year']) is int)
        and isinstance(t.get('tags'), list) and isinstance(t.get('subgenres'), list)
        and isinstance(t.get('language'), str) for t in value)


def discover(signals, seeds=(), *, store=None, opener=None, clock=time.monotonic, sleep=time.sleep):
    started = clock()
    query = build_query(signals, seeds)
    result = DiscoveryResult(query=query)
    if query is None:
        result.status = 'no_supported_query'
        return result
    cache_key = 'query:' + hashlib.sha256(query.encode()).hexdigest()
    cached = None
    try:
        store = store or discovery_store()
        cached = store.get(cache_key)
        if cached is not None and (not isinstance(cached, dict) or not valid_cached_tracks(cached.get('tracks'))
                or not isinstance(cached.get('fetched_at'), (float, int))):
            cached = None
            raise StorageError('corrupt_record')
        if cached and time.time() - cached['fetched_at'] < FRESH_SECONDS:
            result.tracks = canonical_candidates(cached['tracks']); result.total_matches = cached.get('total_matches')
            result.status = 'cache_hit'
            return result
        cooldown = store.get('cooldown')
        if cooldown:
            raise StorageError('rate_limited')
        deadline = clock() + 2.2
        while not store.reserve():
            if clock() >= deadline:
                raise StorageError('busy')
            sleep(0.15)
        settings = server_settings()
        user_agent = settings.get('MUSICBRAINZ_USER_AGENT') or ('NextTrack/0.9 (' +
            settings.get('MUSICBRAINZ_CONTACT', 'https://nexttrack-test-rmbels-projects.vercel.app') + ')')
        url = 'https://musicbrainz.org/ws/2/recording/?' + urlencode({'query': query, 'limit': 100, 'fmt': 'json'})
        result.network_calls = 1
        with (opener or urlopen)(Request(url, headers={'User-Agent': user_agent, 'Accept': 'application/json'}), timeout=10) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError('oversized')
            payload = json.loads(raw)
        if not isinstance(payload, dict) or not isinstance(payload.get('recordings'), list):
            raise ValueError('invalid_response')
        tracks = [normalise_recording(r, signals.get('preferred_genres', [])) for r in payload['recordings'][:100]]
        tracks = canonical_candidates([t for t in tracks if t is not None])
        if payload['recordings'] and not tracks:
            raise ValueError('invalid_recordings')
        total = payload.get('count') if type(payload.get('count')) is int else None
        record = {'fetched_at': time.time(), 'total_matches': total, 'tracks': tracks}
        store.put_many([(cache_key, record), *[('track:' + t['id'], t) for t in tracks]], RETAIN_SECONDS)
        result.tracks, result.total_matches, result.status = tracks, total, 'live'
    except HTTPError as exc:
        retry = exc.headers.get('Retry-After') if exc.headers else None
        seconds = 30
        if retry:
            try:
                seconds = max(1, int(retry))
            except ValueError:
                try:
                    seconds = max(1, int(parsedate_to_datetime(retry).timestamp() - time.time()))
                except (TypeError, ValueError, OverflowError):
                    pass
        if exc.code in {429, 503}:
            try:
                store.defer(seconds)
            except StorageError:
                pass
        exc.close()
        result.status = 'upstream_unavailable'
    except (StorageError, OSError, URLError, ValueError, TypeError, KeyError, AttributeError):
        result.status = 'unavailable'
    finally:
        result.elapsed_ms = round((clock() - started) * 1000, 2)
    if result.status in {'unavailable', 'upstream_unavailable'}:
        if isinstance(cached, dict) and isinstance(cached.get('tracks'), list):
            result.tracks = canonical_candidates(cached['tracks']); result.total_matches = cached.get('total_matches')
            result.status = 'stale_cache'
        result.warning = 'Live music search is temporarily unavailable; results use saved catalogue data.'
    return result
