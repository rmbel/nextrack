import copy
import unittest

from prototype.app.metadata import enrich_track, metadata_coverage, recording_metadata


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.track = {'id': 'musicbrainz:test', 'source': 'musicbrainz',
                      'source_url': 'https://musicbrainz.org/recording/test',
                      'title': 'Test', 'artist': 'Artist', 'genre': 'jazz',
                      'tags': [], 'language': 'unknown', 'energy': None}

    def test_genre_never_implies_language_mood_or_energy(self):
        track = dict(self.track, source='itunes', genre='latin pop', language='spanish',
                     energy=5, tags=['energetic'])
        output = enrich_track(track)
        self.assertEqual(output['language'], 'unknown')
        self.assertEqual(output['tags'], [])
        self.assertIsNone(output['energy'])

    def test_legacy_inferred_traits_withdrawn_without_mutation(self):
        track = dict(self.track, source=None, subgenres=['instrumental', 'soul'], tags=['calm'], energy=1)
        original = copy.deepcopy(track)
        output = enrich_track(track)
        self.assertEqual(track, original)
        self.assertEqual(output['subgenres'], ['soul'])
        self.assertEqual(output['metadata_quality']['legacy_traits'], 'withdrawn-unsupported-genre-inference')

    def test_explicit_positive_tags_normalized_and_labelled(self):
        output = enrich_track(dict(self.track, source_tags=[{'name': 'High-Energy', 'count': 3},
                                                          {'name': 'Romantic', 'count': 1},
                                                          {'name': 'calm', 'count': 0}]))
        self.assertEqual(output['energy'], 5)
        self.assertEqual(output['tags'], ['energetic', 'romantic'])
        self.assertEqual(output['metadata_quality']['energy'], 'community-label-estimate')
        self.assertIn('not audio-measured', output['metadata_evidence']['energy']['scale'])

    def test_conflicting_energy_is_unknown(self):
        output = enrich_track(dict(self.track, source_tags=[{'name': 'energetic', 'count': 5}, {'name': 'calm', 'count': 1}]))
        self.assertIsNone(output['energy'])
        self.assertEqual(output['metadata_quality']['energy'], 'conflicting-community-labels')

    def test_language_tags_are_not_lyric_language(self):
        output = enrich_track(dict(self.track, source_tags=[{'name': 'spanish', 'count': 4}]))
        self.assertEqual(output['language'], 'unknown')

    def test_work_languages_support_multilingual(self):
        evidence = {'works': [{'work_id': 'work1', 'languages': ['por', 'eng'], 'attributes': []}]}
        output = enrich_track(self.track, evidence)
        self.assertEqual(output['language'], 'multilingual')
        self.assertEqual(output['languages'], ['english', 'portuguese'])

    def test_instrumental_performance_overrides_work_lyrics(self):
        evidence = {'works': [{'work_id': 'work1', 'languages': ['eng'], 'attributes': ['instrumental']}]}
        output = enrich_track(self.track, evidence)
        self.assertEqual(output['language'], 'instrumental')
        self.assertIn('instrumental', output['tags'])

    def test_translated_or_karaoke_performances_do_not_inherit_language(self):
        for attribute in ['translated', 'karaoke', 'medley']:
            with self.subTest(attribute=attribute):
                result = enrich_track(self.track, {'works': [{'languages': ['eng'], 'attributes': [attribute]}]})
                self.assertEqual(result['language'], 'unknown')

    def test_release_text_and_artist_country_are_never_used(self):
        recording = {'id': 'test', 'releases': [{'text-representation': {'language': 'eng'}}],
                     'artist-credit': [{'artist': {'country': 'GB'}}], 'relations': []}
        result = enrich_track(self.track, recording_metadata(recording))
        self.assertEqual(result['language'], 'unknown')

    def test_fma_language_and_energy_have_distinct_evidence(self):
        output = enrich_track(dict(self.track, source='fma', language_code='pt',
                                   audio_features={'source': 'echonest', 'energy': 0.75}))
        self.assertEqual(output['language'], 'portuguese')
        self.assertEqual(output['energy'], 4)
        self.assertEqual(output['metadata_quality']['energy'], 'audio-analysis')
        self.assertEqual(output['metadata_evidence']['energy']['raw_value'], 0.75)
        self.assertEqual(metadata_coverage([output])['audio_measured_energy'], 1)
        self.assertEqual(metadata_coverage([output])['energy_labelled_estimate'], 0)

    def test_out_of_bounds_energy_does_not_become_measurement(self):
        for value in [-1, 1.1, True, '0.7', None]:
            with self.subTest(value=value):
                output = enrich_track(dict(self.track, source='fma', audio_features={'source': 'echonest', 'energy': value}))
                self.assertIsNone(output['energy'])

    def test_fma_instrumental_flag_not_language_default(self):
        output = enrich_track(dict(self.track, source='fma', language_code='en', instrumental=True))
        self.assertEqual(output['language'], 'instrumental')
        self.assertIn('instrumental', output['tags'])

    def test_fma_per_track_mood_tags_have_provider_provenance(self):
        output = enrich_track(dict(self.track, source='fma', source_tags=['calm', 'romantic', 'jazz']))
        self.assertEqual(output['tags'], ['calm', 'romantic'])
        self.assertEqual(output['metadata_evidence']['mood']['source'], 'fma-track-tags')
        self.assertNotIn('votes', output['metadata_evidence']['tags']['labels'][0])


if __name__ == '__main__':
    unittest.main()
