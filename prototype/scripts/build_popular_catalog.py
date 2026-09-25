"""Build the listener-ranked addition from the historical Taste Profile cohort.

Requires rank_taste_profile.py outputs and the published tagtraum CD2 annotations.
Missing dates and genres remain unknown. No user identifiers enter this output.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / 'data/source-cache/taste-profile'
SOURCE_URL = 'http://millionsongdataset.com/tasteprofile'
GENRE_URL = 'https://www.tagtraum.com/msd_genre_datasets.html'
GENRE_NAMES = {'Rap': 'hip hop', 'RnB': 'r&b', 'New Age': 'new age',
               'World': 'world', 'Electronic': 'electronic'}


def read_genres(path):
    annotations = {}
    with zipfile.ZipFile(path) as archive:
        with archive.open('msd_tagtraum_cd2.cls') as stream:
            for raw in stream:
                line = raw.decode('utf-8').strip()
                if not line or line.startswith('#'):
                    continue
                track_id, *labels = line.split('\t')
                if not labels or track_id in annotations:
                    raise ValueError('Invalid or duplicate genre annotation')
                annotations[track_id] = labels
    return annotations


def convert(row, annotations, retrieved_at):
    candidates = row['candidate_tracks']
    # Prefer an annotated source track but never join on artist name alone.
    selected = min(candidates, key=lambda t: (
        t['track_id'] not in annotations, not bool(t.get('year')), t['track_id']))
    labels = annotations.get(selected['track_id'], [])
    genres = [GENRE_NAMES.get(label, label.casefold()) for label in labels]
    year = selected.get('year')
    year = year if type(year) is int and 1000 <= year <= 2011 else None
    song_id = row['song_id']
    year_evidence = ({'source': 'million-song-dataset-release-year', 'track_id': selected['track_id'],
                      'source_url': 'http://millionsongdataset.com/sites/default/files/AdditionalFiles/tracks_per_year.txt',
                      'limitation': 'Provider release-year metadata; may identify an edition.'} if year else None)
    metadata_evidence = {}
    if labels:
        metadata_evidence['genre'] = {'source': 'tagtraum-cd2', 'source_url': GENRE_URL,
                                      'track_id': selected['track_id'], 'labels': labels,
                                      'method': 'Published majority and optional minority genre labels'}
    if year_evidence:
        metadata_evidence['year'] = year_evidence
    return {
        'id': 'taste-profile:' + song_id, 'title': selected['title'], 'artist': selected['artist'],
        'year': year, 'genre': genres[0] if genres else 'unknown', 'subgenres': genres[1:],
        'tags': [], 'language': 'unknown', 'energy': None,
        'source': 'taste-profile', 'source_id': song_id, 'source_url': SOURCE_URL,
        'provider_ids': {'taste-profile': [song_id], 'msd': [t['track_id'] for t in candidates]},
        'metadata_quality': {'genre': 'published-community-consensus' if labels else 'unknown',
                             'year': 'provider' if year else 'unknown', 'language': 'unknown',
                             'energy': 'unknown', 'mood': 'unknown'},
        'metadata_evidence': metadata_evidence,
        'listener_popularity': {'source': 'taste-profile', 'metric': 'unique_listeners',
                               'value': row['listener_count'], 'rank': row['rank'],
                               'play_count': row['play_count'], 'snapshot_date': retrieved_at,
                               'snapshot_date_meaning': 'Import date, not listening observation date',
                               'dataset_release_year': 2011,
                               'source_period': 'Historical dataset; individual listening timestamps are not supplied',
                               'scope': 'Taste Profile research cohort; valid song-to-track mappings only',
                               'source_url': SOURCE_URL},
    }


def build(ranking_path, output, genre_path):
    ranking = json.loads(ranking_path.read_text())
    rows = ranking if isinstance(ranking, list) else ranking['songs']
    if len(rows) != 20000 or {r['rank'] for r in rows} != set(range(1, 20001)):
        raise ValueError('Expected all 20,000 listener ranks')
    if len({row['song_id'] for row in rows}) != 20000:
        raise ValueError('Duplicate source song IDs')
    if any(a['listener_count'] < b['listener_count'] for a, b in zip(rows, rows[1:])):
        raise ValueError('Ranks are not ordered by listener count')
    annotations = read_genres(genre_path)
    retrieved_at = datetime.now(timezone.utc).isoformat()
    tracks = [convert(row, annotations, retrieved_at) for row in rows]
    if any(not t['title'] or not t['artist'] for t in tracks):
        raise ValueError('Missing source title or artist')
    temporary = output.with_suffix('.tmp')
    temporary.write_text(json.dumps(tracks, ensure_ascii=False, separators=(',', ':')) + '\n')
    temporary.replace(output)
    report = {'imported_at': retrieved_at, 'records': len(tracks), 'dataset_release_year': 2011,
              'ranking_metric': 'Distinct users per song within the historical Taste Profile research cohort',
              'known_years': sum(t['year'] is not None for t in tracks),
              'genre_annotations': sum(t['genre'] != 'unknown' for t in tracks),
              'genres': dict(Counter(t['genre'] for t in tracks)),
              'listener_threshold': rows[-1]['listener_count'],
              'source_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in (ranking_path, genre_path)},
              'catalog_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
              'source_url': SOURCE_URL, 'genre_source_url': GENRE_URL,
              'limitations': ['Historical research cohort, not a current worldwide or Spotify chart.',
                              'Missing genre, year, language, mood and energy are unknown.',
                              'Known invalid source mappings are excluded before the top 20,000 selection.',
                              'Dataset licence is non-commercial research use.']}
    (ROOT / 'data/catalog-popular-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ranking', type=Path, default=CACHE / 'listener-ranking.json')
    parser.add_argument('--genres', type=Path, default=CACHE / 'msd_tagtraum_cd2.cls.zip')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/catalog-popular.json')
    args = parser.parse_args()
    build(args.ranking, args.output, args.genres)
