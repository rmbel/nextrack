import unittest
from unittest.mock import patch
from prototype.app import recommender as r
from prototype.app.catalog import CATALOG, TRACK_BY_ID, CATALOG_ALIASES, SEARCH_INDEX, POPULARITY_SCORES
from prototype.app.models import RecommendRequest
from prototype.app.catalog_quality import artist_keys, artist_names

class CatalogRankingTests(unittest.TestCase):
    def test_alias_seeds_exclude_the_whole_recording(self):
        pairs=[(alias,canonical) for alias,canonical in CATALOG_ALIASES.items()
               if alias!=canonical and canonical in TRACK_BY_ID]
        self.assertTrue(pairs)
        alias,canonical=pairs[0]
        result=r.generate_recommendations(RecommendRequest(seed_track_ids=[alias,canonical]))
        self.assertEqual(result.seed_count,1)
        self.assertNotIn(canonical,[x.id for x in result.recommendations])

    def test_collaboration_does_not_evade_artist_variety(self):
        tracks=[dict(id=str(i),title='Song'+str(i),artist=artist,genre='pop',year=2020,
                     language='unknown',energy=None,tags=[],subgenres=[])
                for i,artist in enumerate(['Drake','Drake feat. SZA','SZA','Adele','Prince','Beyoncé'])]
        candidates=[dict(track=t,relevance=1-i*.01,seed_similarity=.8,year_similarity=1,
                         energy_similarity=0,prompt_adherence=None,prompt_reasons=[])
                    for i,t in enumerate(tracks)]
        vectors={t['id']:{'genre:pop':1} for t in tracks}
        keys={t['id']:artist_keys(t) for t in tracks}
        names={t['id']:artist_names(t) for t in tracks}
        with patch.object(r,'TRACK_ARTISTS',keys),patch.object(r,'TRACK_ARTIST_NAMES',names), \
             patch.object(r,'TRACK_VECTORS',vectors),patch.object(r,'TRACK_VECTOR_NORMS',{t['id']:1 for t in tracks}):
            result=r.select_with_mmr(candidates,{'genres':{'pop'},'tags':{}},5)
        self.assertEqual(len(result),5)
        self.assertNotIn('1',[t.id for t in result])

    def test_quarantined_white_noise_never_recommended(self):
        bad=[t for t in CATALOG if 'white noise 3 hour long' in t['title'].casefold() and 'white noise therapy' in t['artist'].casefold()]
        self.assertEqual(bad,[])

    def test_language_parsing_supports_portuguese_and_french(self):
        self.assertEqual(r.parse_prompt('Portuguese songs')['preferred_language'],'portuguese')
        self.assertEqual(r.parse_prompt('French songs')['preferred_language'],'french')

    def test_search_index_covers_only_accepted_records(self):
        self.assertEqual({row[0]['id'] for row in SEARCH_INDEX},{t['id'] for t in CATALOG})

    def test_familiarity_requires_an_explicit_non_negated_request(self):
        for prompt in ['popular songs', 'familiar hits', 'well-known jazz',
                       'popular jazz without vocals', 'popular jazz without rock', 'familiar pop but not sad']:
            self.assertTrue(r.parse_prompt(prompt)['prefer_familiar'])
        for prompt in ['', 'jazz songs', 'avoid popular songs', 'less familiar music',
                       'not too familiar music', 'without any hits', 'unfamiliar artists']:
            self.assertFalse(r.parse_prompt(prompt)['prefer_familiar'])

    def test_popularity_scores_require_documented_provider_counts(self):
        self.assertTrue(POPULARITY_SCORES)
        for track_id, score in POPULARITY_SCORES.items():
            evidence = TRACK_BY_ID[track_id]['popularity']
            self.assertEqual((evidence['source'], evidence['metric']), ('fma', 'track_listens'))
            self.assertGreaterEqual(score, 0)
            self.assertLessEqual(score, 1)

    def test_familiarity_boost_is_small_and_only_applies_with_evidence(self):
        candidate = TRACK_BY_ID[next(iter(POPULARITY_SCORES))]
        profile = r.build_seed_profile([TRACK_BY_ID['romeo-propuesta-indecente']])
        ordinary = r.parse_prompt('')
        familiar = r.parse_prompt('familiar songs')
        with patch.dict(r.POPULARITY_SCORES, {candidate['id']: 0.8}):
            base = r.score_candidate(candidate, profile, ordinary)['relevance']
            boosted = r.score_candidate(candidate, profile, familiar)['relevance']
        self.assertAlmostEqual(boosted, min(1, base + 0.04))
        with patch.object(r, 'POPULARITY_SCORES', {}):
            self.assertEqual(r.score_candidate(candidate, profile, ordinary)['relevance'],
                             r.score_candidate(candidate, profile, familiar)['relevance'])

    def test_prompt_only_familiarity_keeps_the_popularity_signal(self):
        candidates = [TRACK_BY_ID[track_id] for track_id in list(POPULARITY_SCORES)[:2]]
        with patch.object(r, 'CATALOG', candidates), \
             patch.object(r, 'POPULARITY_SCORES', {candidates[0]['id']: 0.1, candidates[1]['id']: 1.0}):
            result = r.generate_recommendations(RecommendRequest(prompt='familiar songs', limit=1))
        self.assertEqual(result.recommendations[0].id, candidates[1]['id'])
