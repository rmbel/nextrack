"""Unknown release dates and genres must not manufacture recommendation evidence."""
from contextlib import ExitStack
import json
import math
import os
import unittest
from unittest.mock import MagicMock, patch

from prototype.app import catalog as c, recommender as r
from prototype.app.ai_provider import OpenAIProvider
from prototype.app.catalog_quality import artist_keys, artist_names
from prototype.app.models import RecommendRequest


def track(identifier, year, *, genre="pop", energy=None):
    return {"id": identifier, "title": "Fixture song", "artist": "Artist " + identifier,
            "year": year, "genre": genre, "subgenres": [], "tags": [],
            "language": "unknown", "energy": energy}


class UnknownMetadataTests(unittest.TestCase):
    def setUp(self):
        self.tracks = [track("seed", 2022), track("undated", None),
                       track("recent", 2024), track("classic", 1998)]
        vectors = {item["id"]: c.track_feature_vector(item) for item in self.tracks}
        stack = ExitStack()
        self.addCleanup(stack.close)
        for name, value in {
                "CATALOG": self.tracks,
                "TRACK_BY_ID": {item["id"]: item for item in self.tracks},
                "TRACK_VECTORS": vectors,
                "TRACK_VECTOR_NORMS": {key: math.sqrt(sum(x*x for x in vector.values()))
                                       for key, vector in vectors.items()},
                "TRACK_ARTISTS": {item["id"]: artist_keys(item) for item in self.tracks},
                "TRACK_ARTIST_NAMES": {item["id"]: artist_names(item) for item in self.tracks},
                "POPULARITY_SCORES": {},
        }.items():
            stack.enter_context(patch.object(r, name, value))

    def test_seed_average_ignores_unknown_dates_and_is_none_when_no_dates_known(self):
        self.assertEqual(r.build_seed_profile(self.tracks[:2])["avg_year"], 2022)
        self.assertIsNone(r.build_seed_profile([self.tracks[1]])["avg_year"])
        self.assertIsNone(r.build_seed_profile([])["avg_year"])

    def test_unknown_dates_supply_zero_similarity_and_no_release_period_reason(self):
        profile = r.build_seed_profile([self.tracks[0]])
        score = r.score_candidate(self.tracks[1], profile, r.parse_prompt(""))
        self.assertEqual(score["year_similarity"], 0)
        self.assertAlmostEqual(score["relevance"], score["seed_similarity"])
        self.assertNotIn("release period", r.build_reason(score, profile))
        profile = r.build_seed_profile([self.tracks[1]])
        score = r.score_candidate(self.tracks[0], profile, r.parse_prompt(""))
        self.assertEqual(score["year_similarity"], 0)
        self.assertAlmostEqual(score["relevance"], score["seed_similarity"])

    def test_known_years_preserve_original_similarity_and_weighting(self):
        for energy in (None, 3):
            self.tracks[0]["energy"] = self.tracks[2]["energy"] = energy
            profile = r.build_seed_profile([self.tracks[0]])
            score = r.score_candidate(self.tracks[2], profile, r.parse_prompt(""))
            expected = (0.65 * score["seed_similarity"] + 0.20 * (1 - 2/25)
                        + 0.15 * (1 if energy is not None else 0)) / (1 if energy is not None else .85)
            self.assertEqual(score["relevance"], expected)

    def test_unknown_dates_never_pass_range_or_recency_checks(self):
        for constraint in ({"min_year": 2000}, {"max_year": 2025},
                           {"recency": "recent"}, {"recency": "classic"}):
            with self.subTest(constraint=constraint):
                signals = {**r.parse_prompt(""), **constraint}
                adherence, reasons = r.prompt_adherence(self.tracks[1], signals)
                self.assertEqual(adherence, 0)
                self.assertEqual(reasons, [])
                result = r.generate_recommendations(RecommendRequest(seed_track_ids=["seed"], limit=10), signals)
                self.assertNotIn("undated", [item.id for item in result.recommendations])

    def test_unknown_dates_can_be_recommended_and_used_as_seeds_without_date_request(self):
        response = r.generate_recommendations(RecommendRequest(seed_track_ids=["seed"], limit=10))
        item = next(item for item in response.recommendations if item.id == "undated")
        self.assertIsNone(item.year)
        self.assertIsNone(item.model_dump()["year"])
        response = r.generate_recommendations(RecommendRequest(seed_track_ids=["undated"], limit=10))
        self.assertIsNone(response.seed_tracks[0].year)
        self.assertTrue(all(item.score_components["year_similarity"] == 0 for item in response.recommendations))

    def test_search_date_tie_break_places_unknown_last_without_fabricated_zero(self):
        rows = [(item, "fixture song", "fixture artist", "fixture song") for item in self.tracks]
        with patch.object(c, "SEARCH_INDEX", rows):
            results = c.search_catalog("fixture")
        self.assertEqual([item["year"] for item in results], [2024, 2022, 1998, None])
        self.assertIsNone(c.track_to_search_result(results[-1]).year)

    def test_unknown_genre_is_not_a_shared_feature_or_reason(self):
        left, right = [track(name, None, genre="unknown") for name in ("left", "right")]
        left["subgenres"] = ["", "unknown"]
        for item in (left, right):
            self.assertNotIn("genre:unknown", c.track_feature_vector(item))
            self.assertNotIn("genre:", c.track_feature_vector(item))
        self.assertEqual(c.cosine_similarity(c.track_feature_vector(left), c.track_feature_vector(right)), 0)
        self.tracks[0]["genre"] = "unknown"
        profile = r.build_seed_profile([self.tracks[0]])
        self.assertNotIn("unknown", profile["genres"])
        reason = r.build_reason({"track": left, "year_similarity": 0, "energy_similarity": 0,
                                 "prompt_reasons": []}, profile)
        self.assertNotIn("unknown", reason)

    def test_ai_catalogue_range_ignores_unknown_dates_and_preserves_null_seed_date(self):
        intent = {"preferred_genres": [], "preferred_tags": [], "recency": None,
                  "preferred_language": None, "prefer_discovery": False, "excluded_genres": [],
                  "min_year": None, "max_year": None, "target_energy": None,
                  "summary": "Fixture", "limitations": []}
        payload = {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps(intent)}]}]}
        for tracks, expected in ((self.tracks, [1998, 2024]), ([self.tracks[1]], None)):
            response = MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
            with patch.dict(os.environ, {"OPENAI_API_KEY": "fixture-key"}), \
                 patch("prototype.app.ai_provider.CATALOG", tracks), \
                 patch("prototype.app.ai_provider.urlopen", return_value=response) as call:
                OpenAIProvider().interpret("Fixture", [self.tracks[1]])
            body = json.loads(call.call_args.args[0].data)
            data = json.loads(body["instructions"][body["instructions"].index('{"genres":'):])
            self.assertEqual(data["catalogue_year_range"], expected)
            self.assertIsNone(json.loads(body["input"])["seed_tracks"][0]["year"])


if __name__ == "__main__":
    unittest.main()
