"""Export the exact active catalogue as a compact, deterministic deployment snapshot.

Run locally before deployment. Source snapshots stay outside the deployment bundle;
all fields used by search, recommendations, Spotify links and metadata coverage remain.
The runtime snapshot must be regenerated after catalogue/evidence/identity rule changes.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
sys.path.insert(0, str(WORKSPACE))
RUNTIME_FIELDS = (
    'id', 'title', 'artist', 'year', 'genre', 'subgenres', 'tags', 'language', 'languages',
    'energy', 'source', 'source_id', 'source_url', 'artist_credits', 'artist-credit',
    'artists', 'artist_names', 'disambiguation', 'recording_disambiguation',
    'metadata_quality', 'metadata_evidence', 'popularity', 'listener_popularity', 'provider_ids',
)
SOURCE_FILES = ('data/catalog.json', 'data/catalog-extension.json', 'data/catalog-expansion.json',
                'data/metadata-evidence.json', 'data/catalog-quality-overrides.json', 'data/catalog-popular.json')
TRANSFORM_FILES = ('app/catalog_quality.py', 'app/metadata.py')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export(output, *, allow_small_catalogue=False):
    # An export always rebuilds the active catalogue from its authoritative local inputs.
    if os.getenv('NEXTTRACK_CATALOG_MODE') == 'baseline':
        raise SystemExit('Unset NEXTTRACK_CATALOG_MODE=baseline before exporting production data.')
    os.environ.pop('NEXTTRACK_CATALOG_SNAPSHOT', None)
    started = time.perf_counter()
    from prototype.app.catalog import CATALOG, CATALOG_ALIASES, CATALOG_QUALITY, TRACK_ARTISTS, TRACK_BY_ID
    from prototype.app.catalog_quality import artist_keys, artist_names
    from prototype.app.recommender import ALGORITHM_NAME, ALGORITHM_VERSION
    additions_path = ROOT / 'data/catalog-popular.json'
    additions = json.loads(additions_path.read_text()) if additions_path.is_file() else []
    minimum = 1 if allow_small_catalogue else 50000
    if not minimum <= len(CATALOG) <= 50000 + len(additions):
        raise ValueError(f'Unexpected active count after additions: {len(CATALOG)}')
    if any(track['id'] not in TRACK_BY_ID for track in additions):
        raise ValueError('A listener-ranked source song is missing from the active catalogue')
    compact = [{name: track[name] for name in RUNTIME_FIELDS if name in track} for track in CATALOG]
    ids = {track['id'] for track in compact}
    if len(ids) != len(compact):
        raise ValueError('Canonical catalogue contains duplicate IDs')
    for original, track in zip(CATALOG, compact):
        if artist_keys(track) != TRACK_ARTISTS[track['id']] or artist_names(track) != artist_names(original):
            raise ValueError(f'Compaction changed artist identity for {track["id"]}')
    aliases = {alias: canonical for alias, canonical in CATALOG_ALIASES.items()
               if alias != canonical and canonical in ids}
    sources = {name:digest(ROOT/name) for name in SOURCE_FILES if (ROOT/name).is_file()}
    transforms = {name:digest(ROOT/name) for name in TRANSFORM_FILES}
    payload = {'schema_version':1, 'catalogue_mode':'active', 'tracks':compact,
               'aliases':aliases, 'quality':CATALOG_QUALITY,
               'manifest':{'algorithm':f'{ALGORITHM_NAME}/{ALGORITHM_VERSION}',
                           'source_sha256':sources, 'transform_sha256':transforms,
                           'runtime_fields':list(RUNTIME_FIELDS),
                           'notes':'Canonical normalized active metadata; raw source records remain in the local research workspace.'}}
    output.parent.mkdir(parents=True,exist_ok=True)
    temporary = output.with_suffix('.tmp')
    # Streaming avoids another complete JSON byte string. mtime=0 makes repeat builds reproducible.
    with temporary.open('wb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw, compresslevel=9, mtime=0) as compressed:
            with io.TextIOWrapper(compressed,encoding='utf-8') as text:
                json.dump(payload,text,ensure_ascii=False,separators=(',', ':'),sort_keys=True)
                text.write('\n')
    temporary.replace(output)
    with gzip.open(output,'rt',encoding='utf-8') as stream:
        restored=json.load(stream)
    if restored != payload:
        raise ValueError('Snapshot round-trip changed canonical catalogue data')
    report = {'created_at':datetime.now(timezone.utc).isoformat(),'records':len(compact),
              'alias_count':len(aliases),'quality':CATALOG_QUALITY,'snapshot_sha256':digest(output),
              'compressed_bytes':output.stat().st_size,
              'elapsed_seconds':round(time.perf_counter()-started,3),
              'checks':['exact active count','canonical order preserved','accepted aliases preserved',
                        'artist identities preserved','lossless JSON/gzip round trip'],
              'manifest':payload['manifest']}
    report_path=output.with_suffix('').with_suffix('.manifest.json')
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='manifest'},indent=2))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'deployment/data/catalog-runtime.json.gz')
    parser.add_argument('--allow-small-catalogue', action='store_true',
                        help='Export the smaller public sample; keep the full-snapshot count guard by default.')
    args=parser.parse_args()
    export(args.output.resolve(), allow_small_catalogue=args.allow_small_catalogue)

if __name__ == '__main__':
    main()
