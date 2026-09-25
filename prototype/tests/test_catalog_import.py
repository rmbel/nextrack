import unittest
from prototype.scripts.import_catalog import apple_track, musicbrainz_track, identity
from prototype.app.catalog import normalize, track_feature_vector, CATALOG
from prototype.app.models import RecommendRequest
from prototype.app.service import recommend_playlist
from prototype.app.recommender import build_seed_profile, score_candidate, parse_prompt


class CatalogImportTests(unittest.TestCase):
    def test_apple_source_and_missing_features_are_honest(self):
        item={'kind':'song','trackId':123,'trackName':'New Song','artistName':'Test Artist',
              'releaseDate':'2024-05-01','primaryGenreName':'Pop','trackViewUrl':'https://music.apple.com/us/song/123'}
        track=apple_track(item)
        self.assertEqual(track['id'],'itunes:123')
        self.assertIsNone(track['energy'])
        self.assertEqual(track['language'],'unknown')
        self.assertEqual(track['tags'],[])
        self.assertNotIn('language:unknown',track_feature_vector(track))
        self.assertEqual(track['source'],'itunes')

    def test_incomplete_and_non_song_records_are_skipped(self):
        self.assertIsNone(apple_track({'kind':'music-video','trackName':'Video'}))
        self.assertIsNone(apple_track({'kind':'song','trackId':123,'trackName':'Song','artistName':'Artist'}))
        self.assertIsNone(musicbrainz_track({'id':'recording','title':'Untitled'}))

    def test_musicbrainz_recording_mapping(self):
        track=musicbrainz_track({'id':'abc','title':'Example','artist-credit':[{'name':'Artist','joinphrase':' & '},{'name':'Guest'}],
                                'first-release-date':'2023-01-01','tags':[{'name':'bachata','count':5}]})
        self.assertEqual(track['artist'],'Artist & Guest')
        self.assertEqual(track['genre'],'bachata')
        self.assertEqual(track['source_url'],'https://musicbrainz.org/recording/abc')
        self.assertIsNone(track['energy'])

    def test_unicode_names_remain_searchable_and_distinct(self):
        self.assertEqual(normalize('Beyoncé'),'beyonce')
        self.assertEqual(normalize('怪獣の花唄'),'怪獣の花唄')
        self.assertNotEqual(identity('怪獣の花唄','Artist'),identity('夜に駆ける','Artist'))
        self.assertEqual(identity('Canción','Artista'),identity('Cancion','Artista'))

    def test_empty_profile_has_no_invented_energy(self):
        self.assertIsNone(build_seed_profile([])['avg_energy'])

    def test_imported_song_can_seed_real_recommendations(self):
        imported = next((t for t in CATALOG if t.get('source') in {'itunes', 'musicbrainz', 'fma'}), None)
        if imported is None:
            self.skipTest('Baseline-only catalogue')
        result = recommend_playlist(RecommendRequest(seed_track_ids=[imported['id']]))
        self.assertEqual(result.seed_tracks[0].source, imported['source'])
        self.assertEqual(len(result.recommendations), 5)
        self.assertNotIn(imported['id'], [t.id for t in result.recommendations])
        self.assertTrue(all(t.id in {c['id'] for c in CATALOG} for t in result.recommendations))
