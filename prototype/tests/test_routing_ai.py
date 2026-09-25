import json
import os
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError, URLError
from fastapi import HTTPException
from pydantic import ValidationError
from prototype.app.ai_provider import AIError, OpenAIProvider, PromptInterpretation
from prototype.app.catalog import CATALOG, TRACK_BY_ID
from prototype.app.models import RecommendRequest
from prototype.app.router import choose_strategy
from prototype.app.service import recommend_playlist

INTENT = dict(preferred_genres=['bachata'], preferred_tags=[], recency='recent',
              preferred_language=None, prefer_discovery=False, excluded_genres=[],
              min_year=None, max_year=None, target_energy=None, summary='Recent bachata.', limitations=[])


def wire_response(intent=None):
    return {'status': 'completed', 'model': 'test-model', 'usage': {'input_tokens': 100, 'output_tokens': 50},
            'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(intent or INTENT)}]}]}


class RoutingTests(unittest.TestCase):
    def test_seed_boundaries(self):
        for n in (1, 2, 3, 5, 10, 25, 50):
            with self.subTest(n=n):
                ids = [t['id'] for t in CATALOG[:n]]
                result = recommend_playlist(RecommendRequest(seed_track_ids=ids))
                self.assertEqual(result.seed_count, n)
                self.assertEqual(len(result.recommendations), 5)
                self.assertTrue(set(ids).isdisjoint(t.id for t in result.recommendations))
        with self.assertRaises(ValidationError):
            RecommendRequest(seed_track_ids=[t['id'] for t in CATALOG[:51]])

    def test_empty_and_whitespace(self):
        for data in ({}, {'prompt': '   '}, {'seed_track_ids': []}):
            with self.assertRaises(ValidationError):
                RecommendRequest(**data)

    def test_prompt_only_and_unique_limit(self):
        self.assertEqual(RecommendRequest(prompt=' bachata ').prompt, 'bachata')
        self.assertEqual(len(RecommendRequest(seed_track_ids=[CATALOG[0]['id']]*60).seed_track_ids), 1)

    def test_router_labelled_examples(self):
        examples = [(1, '', 'recommendation'), (0, 'recent bachata songs', 'ai'),
                    (1, 'recent bachata songs', 'recommendation'),
                    (2, 'recent romantic bachata songs in Spanish', 'recommendation'),
                    (1, 'songs for the last part of a summer party that slowly becomes calmer', 'hybrid'),
                    (1, 'not bachata', 'hybrid'), (1, 'songs after 2022', 'hybrid'),
                    (1, 'without rock', 'hybrid'), (1, 'sad but not romantic', 'hybrid'),
                    (1, 'songs for studying', 'hybrid')]
        for n, prompt, expected in examples:
            with self.subTest(prompt=prompt):
                self.assertEqual(choose_strategy(n, prompt)[0], expected)

    def test_deterministic_route_never_calls_ai(self):
        provider = MagicMock()
        recommend_playlist(RecommendRequest(seed_track_ids=[CATALOG[0]['id']], prompt='recent bachata songs'), provider)
        provider.interpret.assert_not_called()

    def test_verified_prompt_only_and_hybrid(self):
        provider = MagicMock()
        provider.interpret.return_value = (INTENT, {'model': 'test', 'calls': 1})
        for ids, prompt, strategy in (([], 'bachata', 'ai'), ([CATALOG[0]['id']], 'for a summer party', 'hybrid')):
            result = recommend_playlist(RecommendRequest(seed_track_ids=ids, prompt=prompt), provider)
            self.assertEqual(result.strategy_used, strategy)
            self.assertTrue(all(t.id in TRACK_BY_ID for t in result.recommendations))
            if not ids:
                self.assertIsNone(result.metrics.mean_seed_similarity)
                self.assertTrue(all('seed_similarity' not in t.score_components for t in result.recommendations))

    def test_prompt_only_genre_is_not_displaced_by_mood_matches(self):
        provider = MagicMock()
        provider.interpret.return_value = (dict(INTENT, preferred_tags=['chill', 'soft', 'smooth'],
                                                min_year=2020, target_energy=1.0), {})
        result = recommend_playlist(RecommendRequest(prompt='recent bachata for a relaxed dinner'), provider)
        self.assertTrue(result.recommendations)
        for song in result.recommendations:
            track = TRACK_BY_ID[song.id]
            self.assertIn('bachata', [track['genre']] + track.get('subgenres', []))
            self.assertGreaterEqual(track['year'], 2020)

    def test_all_ai_failures_fallback_or_retry(self):
        for category in ('timeout', 'not_configured', 'invalid_response', 'refusal', 'provider_http_429'):
            provider = MagicMock(model='test')
            provider.interpret.side_effect = AIError(category)
            result = recommend_playlist(RecommendRequest(seed_track_ids=[CATALOG[0]['id']], prompt='for my birthday'), provider)
            self.assertTrue(result.fallback_used)
            self.assertEqual(result.strategy_used, 'recommendation')
            with self.assertRaises(HTTPException) as error:
                recommend_playlist(RecommendRequest(prompt='for my birthday'), provider)
            self.assertEqual(error.exception.status_code, 503)

    def test_unknown_seed_fails_before_ai(self):
        provider = MagicMock()
        with self.assertRaises(HTTPException):
            recommend_playlist(RecommendRequest(seed_track_ids=['fake'], prompt='summer party'), provider)
        provider.interpret.assert_not_called()

    def test_year_bounds_and_exclusions(self):
        provider = MagicMock()
        intent = dict(INTENT, excluded_genres=['bachata'], preferred_genres=['pop'], min_year=2020, max_year=2024)
        provider.interpret.return_value = (intent, {})
        result = recommend_playlist(RecommendRequest(prompt='pop from 2020 to 2024 without bachata'), provider)
        for song in result.recommendations:
            self.assertTrue(2020 <= song.year <= 2024)
            self.assertNotIn('bachata', [song.genre] + TRACK_BY_ID[song.id].get('subgenres', []))

    def test_unmatchable_prompt_does_not_invent_results(self):
        provider = MagicMock()
        provider.interpret.return_value = (dict(INTENT, min_year=3000), {})
        with self.assertRaises(HTTPException) as error:
            recommend_playlist(RecommendRequest(prompt='songs from 3000'), provider)
        self.assertEqual(error.exception.status_code, 422)


class ProviderTests(unittest.TestCase):
    def invoke(self, payload):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'}), patch('prototype.app.ai_provider.urlopen', return_value=response) as call:
            result = OpenAIProvider().interpret('bachata', [])
            body = json.loads(call.call_args.args[0].data)
            self.assertFalse(body['store'])
            self.assertTrue(body['text']['format']['strict'])
            self.assertEqual(body['text']['format']['schema']['additionalProperties'], False)
            self.assertGreater(call.call_args.kwargs['timeout'], 0)
            return result

    def test_structured_success(self):
        signals, meta = self.invoke(wire_response())
        self.assertEqual(signals['preferred_genres'], ['bachata'])
        self.assertEqual(meta['input_tokens'], 100)

    def test_schema_hallucination_and_refusal(self):
        bad = [wire_response(dict(INTENT, preferred_genres=['invented-style'])),
               wire_response(dict(INTENT, track_ids=['invented-track'])),
               wire_response(dict(INTENT, prefer_discovery='true')),
               {'status': 'incomplete'},
               {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'refusal'}]}]},
               {'status': 'completed', 'output': []}, []]
        for payload in bad:
            with self.subTest(payload=payload), self.assertRaises(AIError):
                self.invoke(payload)

    def test_transport_errors_are_safe(self):
        for error in (TimeoutError(), URLError('secret-detail'), HTTPError('url', 429, 'secret-detail', {}, None)):
            with patch.dict(os.environ, {'OPENAI_API_KEY': 'secret-key'}), patch('prototype.app.ai_provider.urlopen', side_effect=error):
                with self.assertRaises(AIError) as context:
                    OpenAIProvider().interpret('bachata', [])
                self.assertNotIn('secret', str(context.exception))

    def test_missing_key(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': ''}), self.assertRaises(AIError):
            OpenAIProvider().interpret('bachata', [])
