"""Build auditable metadata evidence; retain original catalogue files unchanged.

Uses cached search recording tags and optional MusicBrainz artist browse calls
with work relationships. Every request shares the importer rate lock. Exact
recording IDs receive work language; other-provider IDs only receive work
language when exact artist/title matches agree (no live/remix/version transfer).
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from prototype.app.metadata import apply_metadata, metadata_coverage, recording_metadata
from prototype.scripts.musicbrainz_client import configured_user_agent, retry_delay

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CACHE = DATA / ".metadata-cache"
OUTPUT = DATA / "metadata-evidence.json"


def normal(value):
    value = ''.join(c for c in unicodedata.normalize('NFKD', value.casefold()) if not unicodedata.combining(c))
    return ' '.join(re.sub(r'[^\w\s]', ' ', value).split())


def artist_credit(recording):
    return ''.join(c.get('name', c.get('artist', {}).get('name', '')) + c.get('joinphrase', '')
                   for c in recording.get('artist-credit', []) if isinstance(c, dict))


def fetch_artist(artist_id, offset=0):
    url = 'https://musicbrainz.org/ws/2/recording?' + urlencode({
        'artist': artist_id, 'inc': 'work-rels tags artist-credits', 'limit': 100, 'offset': offset, 'fmt': 'json'})
    cache = CACHE / (hashlib.sha256(url.encode()).hexdigest() + '.json')
    if cache.exists():
        return json.loads(cache.read_text())
    for attempt in range(3):
        with (DATA / '.musicbrainz-api.lock').open('a+') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            lock.seek(0)
            try: last = float(lock.read() or 0)
            except ValueError: last = 0
            # Wall-clock is shared across macOS processes; older Python's
            # monotonic clock may have a separate origin in each process.
            time.sleep(max(0, min(1.1, last + 1.1 - time.time())))
            lock.seek(0); lock.truncate(); lock.write(str(time.time())); lock.flush()
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        try:
            with urlopen(Request(url, headers={'User-Agent': configured_user_agent(), 'Accept': 'application/json'}), timeout=25) as response:
                data = json.load(response)
            if not isinstance(data.get('recordings'), list):
                raise ValueError('Invalid recording browse response')
            CACHE.mkdir(exist_ok=True)
            cache.write_text(json.dumps(data))
            return data
        except HTTPError as exc:
            delay = retry_delay(exc.headers.get('Retry-After'))
            status = exc.code; exc.close()
            if status not in (429, 500, 502, 503, 504) or attempt == 2 or delay is not None and delay > 60:
                raise RuntimeError(f'MusicBrainz HTTP {status}') from None
            time.sleep(max(1.1, delay if delay is not None else 5 * (2 ** attempt)))
        except (URLError, TimeoutError):
            if attempt == 2: raise RuntimeError('MusicBrainz connection unavailable') from None
            time.sleep(5 * (2 ** attempt))


def cached_recordings():
    records = {}
    for directory in (DATA / '.musicbrainz-cache', CACHE):
        for path in directory.glob('*.json'):
            try: payload = json.loads(path.read_text())
            except (ValueError, OSError): continue
            for recording in payload.get('recordings', []):
                old = records.get(recording['id'], {})
                if 'relations' in old and 'relations' not in recording: continue
                records[recording['id']] = recording
    return records


def build_evidence(catalog, recordings):
    result = {}
    dated = datetime.now(timezone.utc).isoformat()
    by_name = defaultdict(list)
    for recording_id, recording in recordings.items():
        evidence = recording_metadata(recording)
        evidence['assembled_at'] = dated
        result['musicbrainz:' + recording_id] = evidence
        if recording.get('video') is True or recording.get('disambiguation'):
            continue
        by_name[(normal(recording.get('title', '')), normal(artist_credit(recording)))].append(evidence)
    crossmatches = 0
    for track in catalog:
        if track['id'] in result: continue
        matches = by_name.get((normal(track['title']), normal(track['artist'])), [])
        # Exact title/credit and consensus of all linked-work language sets.
        known = [m for m in matches if any(w.get('languages') for w in m['works'])]
        sets = {tuple(sorted({code for w in m['works'] for code in w.get('languages', [])})) for m in known}
        if known and len(sets) == 1:
            evidence = dict(known[0])
            evidence['match_method'] = 'exact-normalised-title-and-full-artist-credit; work-language-consensus'
            # Cross-provider matching proves composition language, not shared
            # audio energy or subjective recording tags.
            evidence['source_tags'] = []
            result[track['id']] = evidence
            crossmatches += 1
    return result, crossmatches


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--artists', type=int, default=0, help='Maximum live artist browse calls; 0 rebuilds from caches only')
    args = parser.parse_args()
    catalog = json.loads((DATA / 'catalog.json').read_text()) + json.loads((DATA / 'catalog-extension.json').read_text())
    if (DATA / 'catalog-expansion.json').is_file():
        catalog += json.loads((DATA / 'catalog-expansion.json').read_text())
    records = cached_recordings()
    artist_counts = Counter()
    for recording in records.values():
        for credit in recording.get('artist-credit', []):
            artist = credit.get('artist', {})
            if artist.get('id'): artist_counts[artist['id']] += 1
    # Prioritise artists represented by Spanish/French/Portuguese requests while
    # browsing each artist's real recordings; their nationality is never used.
    preferred_names = {'Romeo Santos', 'Prince Royce', 'Aventura', 'Manuel Turizo', 'Dani J', 'Shakira', 'KAROL G', 'João Gilberto', 'Édith Piaf'}
    preferred = []
    for r in records.values():
        for c in r.get('artist-credit', []):
            a = c.get('artist', {})
            if a.get('name') in preferred_names and a.get('id') not in preferred: preferred.append(a['id'])
    artists = list(dict.fromkeys(preferred + [artist for artist, _ in artist_counts.most_common()]))
    failures = []
    for index, artist_id in enumerate(artists[:args.artists]):
        try:
            data = fetch_artist(artist_id)
            for recording in data['recordings']: records[recording['id']] = recording
            print(f'work metadata: artist {index + 1}/{min(len(artists), args.artists)}, {len(data["recordings"])} recordings', flush=True)
        except RuntimeError as exc:
            failures.append({'artist_id': artist_id, 'error': str(exc)})
            print(str(exc), flush=True)
            if len(failures) >= 2: break
    evidence, crossmatches = build_evidence(catalog, records)
    tmp = OUTPUT.with_suffix('.tmp'); tmp.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n'); tmp.replace(OUTPUT)
    report = {'generated_at': datetime.now(timezone.utc).isoformat(), 'evidence_records': len(evidence),
              'cross_provider_language_matches': crossmatches, 'coverage': metadata_coverage(apply_metadata(catalog, evidence)),
              'failures': failures,
              'sources': ['https://musicbrainz.org/doc/How_to_Use_Works', 'https://musicbrainz.org/doc/Folksonomy_Tagging', 'https://github.com/mdeff/fma']}
    (DATA / 'metadata-enrichment-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
