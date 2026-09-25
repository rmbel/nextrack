"""Resumable MusicBrainz metadata expansion; never invent missing audio/language traits.

MusicBrainz core metadata is CC0; community tags/genres are CC BY-NC-SA 3.0.
The original catalogue and extension are not modified by this importer.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time

try:
    from .musicbrainz_client import MusicBrainzClient, MusicBrainzError
    from .import_catalog import identity
except ImportError:
    from musicbrainz_client import MusicBrainzClient, MusicBrainzError
    from import_catalog import identity

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'data/catalog-expansion.json'
REPORT = ROOT / 'data/catalog-expansion-report.json'
GENRES = ['bachata','salsa','merengue','reggaeton','afrobeats','afrobeat','latin pop','bossa nova',
          'j-pop','k-pop','jazz','blues','ambient','metal','folk','classical','reggae','house',
          'pop','rock','hip hop','r&b','country','indie pop','soul','electronic','dance',
          'funk','disco','punk','alternative rock','indie rock','synth-pop','techno',
          'samba','mpb','flamenco','cumbia','tango','gospel','world','reggaeton']
GENRES = list(dict.fromkeys(GENRES))
ALIASES = {'hip-hop':'hip hop','rhythm and blues':'r&b','synthpop':'synth-pop','afro-pop':'afropop',
           'afro pop':'afropop','k pop':'k-pop','j pop':'j-pop'}
NOISE = re.compile(r'\b(?:white noise|pink noise|brown noise|noise generator|test tone|binaural beats|nature sounds|rain sounds|ocean sounds)\b', re.I)


def convert_recording(item, queried_genre):
    """Preserve recording facts and explicit community tags, never release language."""
    if item.get('video') is True:
        return None, 'video'
    title = item.get('title', '').strip()
    credits = [c for c in item.get('artist-credit', []) if isinstance(c, dict)]
    artist = ''.join(c.get('name', c.get('artist', {}).get('name', '')) + c.get('joinphrase', '') for c in credits).strip()
    date = item.get('first-release-date', '')
    if not title or not artist or not item.get('id') or not date[:4].isdigit():
        return None, 'missing_core_metadata'
    year = int(date[:4])
    if not 1900 <= year <= datetime.now().year:
        return None, 'invalid_year'
    if NOISE.search(title) or artist.casefold() in ('[unknown]', '[no artist]', 'various artists'):
        return None, 'non_music_or_unknown_artist'
    raw_genres = item.get('genres', [])
    raw_tags = item.get('tags', [])
    tags = list(dict.fromkeys(ALIASES.get(t['name'].casefold().strip(), t['name'].casefold().strip())
                             for t in sorted(raw_genres+raw_tags, key=lambda t: t.get('count', 0), reverse=True)
                             if t.get('name')))
    known = [g for g in tags if g in GENRES or g in ALIASES.values() or any(g.endswith(' '+base) for base in GENRES)]
    if not known:
        return None, 'missing_genre'
    genre = known[0]
    result = {'id':'musicbrainz:'+item['id'], 'title':title, 'artist':artist, 'year':year,
              'genre':genre, 'subgenres':known[1:], 'tags':[], 'language':'unknown', 'energy':None,
              'source':'musicbrainz', 'source_url':'https://musicbrainz.org/recording/'+item['id'],
              'source_id':item['id'], 'recording_id':item['id'],
              'artist_ids':[c['artist']['id'] for c in credits if c.get('artist', {}).get('id')],
              'artist_credits':[{'id':c.get('artist', {}).get('id'), 'name':c.get('artist', {}).get('name', c.get('name', '')), 'credited_name':c.get('name', ''), 'joinphrase':c.get('joinphrase', '')} for c in credits],
              'isrcs':item.get('isrcs', []), 'first_release_date':date,
              'duration_ms':item.get('length'), 'disambiguation':item.get('disambiguation', ''),
              'source_genres':raw_genres, 'source_tags':raw_tags,
              'metadata_quality':{'genre':'community-tag', 'language':'unknown', 'energy':'unknown', 'mood':'unknown'},
              'provenance':{'provider':'MusicBrainz', 'metadata_license':'CC0-1.0', 'tags_license':'CC-BY-NC-SA-3.0', 'retrieved_at':datetime.now(timezone.utc).isoformat()}}
    return result, None


def atomic_json(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':'))+'\n')
    temp.replace(path)


FMA_URL = 'https://os.unil.cloud.switch.ch/fma/fma_metadata.zip'
FMA_SHA1 = 'f0df49ffe5f2a6008d7dc83c6915b31835dfe733'
FMA_CACHE = ROOT/'data/source-cache/fma'
LANGUAGES = dict(en='english', es='spanish', fr='french', pt='portuguese', de='german', ru='russian', it='italian', tr='turkish', sr='serbian', ar='arabic', pl='polish', he='hebrew', el='greek', bg='bulgarian', ee='ewe', fi='finnish', sw='swahili', ja='japanese', nl='dutch', cs='czech', id='indonesian', hi='hindi', zh='chinese', ms='malay', hy='armenian', vi='vietnamese', az='azerbaijani', tw='twi', eu='basque', ty='tahitian', tl='tagalog', my='burmese', gu='gujarati', uk='ukrainian', lt='lithuanian', th='thai', no='norwegian', ko='korean', uz='uzbek', ka='georgian', ha='hausa', sk='slovak', bm='bambara', la='latin')
FMA_GENRE_ALIASES = {'hip-hop':'hip hop','soul-rnb':'r&b','ambient electronic':'ambient','old-time / historic':'old-time','electroacoustic':'electronic','afrobeat':'afrobeat'}


def import_fma(target):
    """Import attributed metadata from the official verified FMA research snapshot."""
    import ast
    import csv
    import io
    import math
    import zipfile
    from collections import defaultdict, deque
    from urllib.request import urlopen
    FMA_CACHE.mkdir(parents=True, exist_ok=True)
    archive = FMA_CACHE/'fma_metadata.zip'
    if not archive.exists():
        with urlopen(FMA_URL, timeout=180) as response, archive.with_suffix('.tmp').open('wb') as out:
            while True:
                block = response.read(1024*1024)
                if not block: break
                out.write(block)
        archive.with_suffix('.tmp').replace(archive)
    if hashlib.sha1(archive.read_bytes()).hexdigest() != FMA_SHA1:
        raise ValueError('FMA archive does not match the checksum published by its authors')
    csv.field_size_limit(10_000_000)
    with zipfile.ZipFile(archive) as z:
        # Read only metadata: no audio downloads or audio feature recalculation.
        def rows(filename):
            return csv.reader(io.TextIOWrapper(z.open('fma_metadata/'+filename), encoding='utf-8'))
        genre_rows = csv.DictReader(io.TextIOWrapper(z.open('fma_metadata/genres.csv'), encoding='utf-8'))
        genres = {int(g['genre_id']):g for g in genre_rows}
        raw_rows = csv.DictReader(io.TextIOWrapper(z.open('fma_metadata/raw_tracks.csv'), encoding='utf-8'))
        raw_tracks = {r['track_id']:{k:r[k] for k in ('track_url','track_instrumental','track_language_code')} for r in raw_rows}
        echo_reader = rows('echonest.csv')
        eh1,eh2,eh3 = next(echo_reader),next(echo_reader),next(echo_reader)
        next(echo_reader)
        audio_columns = [(i,c) for i,(a,b,c) in enumerate(zip(eh1,eh2,eh3)) if b=='audio_features']
        audio_features = {}
        for row in echo_reader:
            features = {name:float(row[i]) for i,name in audio_columns if row[i] and math.isfinite(float(row[i]))}
            audio_features[row[0]] = features
        track_reader = rows('tracks.csv')
        fields = list(zip(next(track_reader),next(track_reader)))
        next(track_reader)
        def field(row,section,name): return row[fields.index((section,name))]
        candidates = []
        rejected = Counter()
        for row in track_reader:
            source_id = row[0]
            title, artist = field(row,'track','title').strip(), field(row,'artist','name').strip()
            release_date = field(row,'album','date_released')
            if not title or not artist or not release_date[:4].isdigit() or not 1900<=int(release_date[:4])<=datetime.now().year:
                rejected['missing_title_artist_or_release_year'] += 1; continue
            if NOISE.search(title) or artist.casefold() in ('[unknown]','[no artist]','various artists'):
                rejected['non_music_or_unknown_artist'] += 1; continue
            supplied_genres = [genres[g] for g in ast.literal_eval(field(row,'track','genres')) if g in genres]
            all_genres = [genres[g] for g in ast.literal_eval(field(row,'track','genres_all')) if g in genres]
            if not supplied_genres:
                rejected['missing_genre'] += 1; continue
            names = list(dict.fromkeys(FMA_GENRE_ALIASES.get(g['title'].casefold(),g['title'].casefold()) for g in supplied_genres+all_genres))
            if any(g in names for g in ('spoken','spoken word','poetry','talk','radio','radio theater','comedy')):
                rejected['spoken_or_non_song'] += 1; continue
            primary = FMA_GENRE_ALIASES.get(field(row,'track','genre_top').casefold(),field(row,'track','genre_top').casefold())
            if not primary:
                primary = names[0]
            raw = raw_tracks.get(source_id,{})
            language_code = field(row,'track','language_code')
            language = LANGUAGES.get(language_code,'unknown')
            features = audio_features.get(source_id,{})
            count = int(field(row,'track','listens') or '0')
            artist_id = field(row,'artist','id')
            tag_values = ast.literal_eval(field(row,'track','tags'))
            record = {'id':'fma:'+source_id,'title':title,'artist':artist,'year':int(release_date[:4]),
                      'genre':primary,'subgenres':[g for g in names if g != primary], 'tags':[], 'language':language,'energy':None,
                      'source':'fma','source_id':source_id,'source_url':raw.get('track_url','').replace('http://','https://',1),
                      'artist_ids':['fma:'+artist_id], 'artist_credits':[{'id':'fma:'+artist_id,'name':artist,'source':'fma'}],
                      'language_code':language_code,'source_language_code':language_code,'source_instrumental':raw.get('track_instrumental'),
                      'instrumental': str(raw.get('track_instrumental','')).lower() in ('1','true') if str(raw.get('track_instrumental','')).lower() in ('1','true','0','false') else None,
                      'source_tags':[{'name':str(t),'count':1} for t in tag_values],
                      'source_genres':[{'name':g['title'],'id':g['genre_id']} for g in all_genres],
                      'duration_ms':int(field(row,'track','duration') or '0')*1000,
                      'source_audio_features':features,
                      'audio_features':dict(features, source='echonest', source_url='https://github.com/mdeff/fma') if features else {},
                      'popularity':{'value':count,'metric':'track_listens','source':'fma','snapshot':'2017-05-09','scope':'FMA catalogue'},
                      'source_favorites':int(field(row,'track','favorites') or '0'),
                      'release_date':release_date[:10], 'release_date_scope':'album',
                      'metadata_quality':{'genre':'provider','language':'provider' if language!='unknown' else 'unknown','energy':'unknown','mood':'unknown'},
                      'provenance':{'provider':'FMA','metadata_license':'CC-BY-4.0','dataset':'FMA: A Dataset For Music Analysis',
                                    'dataset_url':'https://github.com/mdeff/fma','archive_sha1':FMA_SHA1,
                                    'snapshot_release':'2017-05-09','retrieved_at':datetime.now(timezone.utc).isoformat()}}
            if features:
                record['source_audio_features_provenance'] = {'provider':'Echo Nest','via':'FMA 2017 metadata snapshot','kind':'algorithmic_audio_features','metadata_license':'CC-BY-4.0'}
            candidates.append(record)
    existing = []
    for name in ('catalog.json','catalog-extension.json'):
        existing.extend(json.loads((ROOT/'data'/name).read_text()))
    expansion = json.loads(OUTPUT.read_text()) if OUTPUT.exists() else []
    # Migrate the earliest MusicBrainz source field spelling without changing facts.
    for track in expansion:
        if 'raw_tags' in track: track['source_tags'] = track.pop('raw_tags')
        if 'raw_genres' in track: track['source_genres'] = track.pop('raw_genres')
    seen = {identity(t['title'],t['artist']) for t in existing+expansion}
    seen_ids = {t['id'] for t in existing+expansion}
    groups = defaultdict(list)
    for track in candidates:
        groups[track['genre']].append(track)
    # Balance genres; within each, prefer documented features/language and actual FMA listens.
    for genre in groups:
        groups[genre].sort(key=lambda t:(bool(t['source_audio_features']), t['language']!='unknown',t['popularity']['value']),reverse=True)
        groups[genre] = deque(groups[genre])
    before = len(expansion)
    while groups and len(existing)+len(expansion)<target:
        for genre in list(groups):
            queue = groups[genre]
            track = queue.popleft()
            if not queue: del groups[genre]
            key = identity(track['title'],track['artist'])
            if key in seen or track['id'] in seen_ids:
                rejected['duplicate'] += 1; continue
            expansion.append(track);seen.add(key);seen_ids.add(track['id'])
            if len(existing)+len(expansion)>=target:break
    atomic_json(OUTPUT,expansion)
    fma_records = [t for t in expansion if t.get('source')=='fma']
    report = {'provider':'FMA and MusicBrainz','updated_at':datetime.now(timezone.utc).isoformat(), 'target':target,
              'total':len(existing)+len(expansion),'expansion_count':len(expansion),'fma_added':len(expansion)-before,
              'fma_count':len(fma_records),'musicbrainz_count':sum(t.get('source')=='musicbrainz' for t in expansion),
              'target_reached':len(existing)+len(expansion)>=target,'source_candidates':len(candidates),'rejections':dict(rejected),
              'known_language':sum(t['language']!='unknown' for t in fma_records),
              'audio_features':sum(bool(t.get('source_audio_features')) for t in fma_records),
              'genres':dict(Counter(t['genre'] for t in fma_records)),
              'metadata_license':'CC-BY-4.0','dataset_snapshot':'2017-05-09','archive_url':FMA_URL,'archive_sha1':FMA_SHA1,
              'expansion_sha256':hashlib.sha256(OUTPUT.read_bytes()).hexdigest(),
              'sampling':'Round robin primary genre; within genre prioritize documented audio features, explicit language, then FMA track listens.',
              'limitations':['FMA metadata is a 2017 snapshot, not current release coverage.','Album release year is not necessarily first recording year.','FMA listens measure popularity within FMA only.','Echo Nest audio features are algorithmic estimates, not human listening labels.']}
    atomic_json(ROOT/'data/catalog-expansion-fma-report.json',report)
    print(json.dumps({k:v for k,v in report.items() if k not in ('genres',)},indent=2))
    if not report['target_reached']: raise SystemExit(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider',choices=['musicbrainz','fma'],default='musicbrainz')
    parser.add_argument('--target', type=int, default=52000, help='Total catalogue size before final cross-source cleaning')
    parser.add_argument('--max-offset', type=int, default=6000)
    parser.add_argument('--genres', nargs='+', default=GENRES)
    parser.add_argument('--checkpoint-every', type=int, default=10)
    args = parser.parse_args()
    if args.provider == 'fma':
        return import_fma(args.target)
    existing = []
    for name in ('catalog.json','catalog-extension.json'):
        existing.extend(json.loads((ROOT/'data'/name).read_text()))
    expansion = json.loads(OUTPUT.read_text()) if OUTPUT.exists() else []
    seen_ids = {t['id'] for t in existing+expansion}
    seen = {identity(t['title'],t['artist']) for t in existing+expansion}
    report = json.loads(REPORT.read_text()) if REPORT.exists() else {'started_at':datetime.now(timezone.utc).isoformat(), 'provider':'MusicBrainz', 'queries':[], 'failures':[]}
    report.update({'target':args.target, 'base_count':len(existing), 'metadata_license':'CC0-1.0', 'tags_license':'CC-BY-NC-SA-3.0', 'rate_limit_seconds':1.1})
    completed = {(r['genre'], r['offset']) for r in report['queries']}
    exhausted = {r['genre'] for r in report['queries'] if r.get('exhausted')}
    rejected = Counter(report.get('rejections', {}))
    client = MusicBrainzClient(cache_dir=ROOT/'data/.musicbrainz-cache', attempts=4, shared_rate_path=ROOT/'data/.musicbrainz-api.lock')
    since_save = 0
    def save():
        # Retain targeted live MusicBrainz additions ahead of the older FMA snapshot.
        expansion.sort(key=lambda track: track.get('source') != 'musicbrainz')
        report.update({'updated_at':datetime.now(timezone.utc).isoformat(), 'expansion_count':len(expansion), 'total':len(existing)+len(expansion), 'target_reached':len(existing)+len(expansion)>=args.target, 'rejections':dict(rejected)})
        atomic_json(OUTPUT, expansion)
        report['expansion_sha256'] = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
        atomic_json(REPORT, report)
    try:
        for offset in range(0, args.max_offset, 100):
            for genre in args.genres:
                if len(existing)+len(expansion) >= args.target:
                    save(); print(f'Target reached: {len(existing)+len(expansion)} total; {len(expansion)} new records', flush=True); return
                if genre in exhausted or (genre,offset) in completed:
                    continue
                query = f'tag:"{genre}" AND status:official AND NOT video:true'
                try:
                    data = client.get('recording', {'query':query, 'limit':100, 'offset':offset})
                except MusicBrainzError as exc:
                    report['failures'].append({'genre':genre, 'offset':offset, 'category':exc.category, 'status':exc.status, 'time':datetime.now(timezone.utc).isoformat()})
                    save()
                    if exc.status in (400,404):
                        exhausted.add(genre)
                        continue
                    print(f'Temporary source failure for {genre}; checkpoint saved. Cooling down 30 seconds.', flush=True)
                    time.sleep(30)
                    continue
                added = 0
                for item in data['recordings']:
                    track, reason = convert_recording(item, genre)
                    if not track:
                        rejected[reason] += 1
                        continue
                    key = identity(track['title'],track['artist'])
                    if track['id'] in seen_ids or key in seen:
                        rejected['duplicate'] += 1
                        continue
                    expansion.append(track); seen_ids.add(track['id']); seen.add(key); added += 1
                    if len(existing)+len(expansion) >= args.target: break
                is_exhausted = offset+len(data['recordings']) >= data.get('count',0) or not data['recordings']
                if is_exhausted: exhausted.add(genre)
                report['queries'].append({'genre':genre, 'query':query, 'offset':offset, 'count':data.get('count'), 'received':len(data['recordings']), 'added':added, 'exhausted':is_exhausted})
                since_save += 1
                print(f'{genre} offset={offset} +{added}; total={len(existing)+len(expansion)} source_count={data.get("count")}',flush=True)
                if since_save >= args.checkpoint_every:
                    save(); since_save=0
    finally:
        save()
    print(f'Finished available queries: {len(existing)+len(expansion)} total.',flush=True)
    if not report['target_reached']: raise SystemExit(2)

if __name__ == '__main__':
    main()
