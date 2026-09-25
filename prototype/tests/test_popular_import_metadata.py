from copy import deepcopy
import unittest

from prototype.scripts.build_popular_catalog import convert


class PopularMetadataTests(unittest.TestCase):
    def row(self):
        return {'song_id': 'SO_FIXTURE', 'listener_count': 20, 'play_count': 900, 'rank': 1,
                'candidate_tracks': [{'track_id': 'TR_FIXTURE', 'title': 'Song', 'artist': 'Artist', 'year': 2005}]}

    def test_import_preserves_listeners_separately_from_plays_and_dates(self):
        row = self.row(); before = deepcopy(row)
        item = convert(row, {'TR_FIXTURE': ['Rap', 'Pop']}, '2026-09-17')
        self.assertEqual(item['listener_popularity']['value'], 20)
        self.assertEqual(item['listener_popularity']['play_count'], 900)
        self.assertEqual(item['listener_popularity']['dataset_release_year'], 2011)
        self.assertEqual((item['genre'], item['subgenres'], item['year']), ('hip hop', ['pop'], 2005))
        self.assertEqual(row, before)

    def test_missing_metadata_remains_unknown(self):
        row = self.row(); row['candidate_tracks'][0]['year'] = 0
        item = convert(row, {}, '2026-09-17')
        self.assertIsNone(item['year'])
        self.assertEqual(item['genre'], 'unknown')
        self.assertEqual(item['language'], 'unknown')
        self.assertIsNone(item['energy'])
        self.assertEqual(item['tags'], [])
        self.assertEqual(item['metadata_evidence'], {})

    def test_annotations_join_by_exact_track_id_without_artist_inference(self):
        row = self.row()
        item = convert(row, {'unrelated-track': ['Rock']}, '2026-09-17')
        self.assertEqual(item['genre'], 'unknown')
        self.assertNotIn('genre', item['metadata_evidence'])


if __name__ == '__main__':
    unittest.main()
