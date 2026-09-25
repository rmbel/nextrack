from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from prototype.app.main import (
    CATALOG,
    FeedbackRequest,
    RecommendRequest,
    TRACK_BY_ID,
    build_seed_profile,
    generate_recommendations,
    health,
    parse_prompt,
    save_feedback,
    search_catalog,
)
from prototype.app.recommender import score_candidate, select_with_mmr


BACHATA_SEEDS = [
    "romeo-propuesta-indecente",
    "romeo-eres-mia",
    "juan-luis-bachata-en-fukuoka",
]


class RecommendationTests(unittest.TestCase):
    def test_search_finds_known_track(self) -> None:
        results = search_catalog("La Bachata")
        self.assertEqual(results[0]["id"], "manuel-turizo-la-bachata")

    def test_request_contract_accepts_two_seeds(self) -> None:
        self.assertEqual(len(RecommendRequest(seed_track_ids=BACHATA_SEEDS[:2]).seed_track_ids), 2)

    def test_request_contract_rejects_limits_outside_one_to_ten(self) -> None:
        with self.assertRaises(ValidationError):
            RecommendRequest(seed_track_ids=BACHATA_SEEDS, limit=0)
        with self.assertRaises(ValidationError):
            RecommendRequest(seed_track_ids=BACHATA_SEEDS, limit=11)

    def test_duplicate_seeds_are_normalised(self) -> None:
        request = RecommendRequest(seed_track_ids=[BACHATA_SEEDS[0]] * 3)
        self.assertEqual(generate_recommendations(request).seed_count, 1)

    def test_unknown_seed_is_rejected_with_a_clear_error(self) -> None:
        request = RecommendRequest(
            seed_track_ids=[*BACHATA_SEEDS[:2], "missing-track"],
        )
        with self.assertRaises(HTTPException) as context:
            generate_recommendations(request)
        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("missing-track", context.exception.detail)

    def test_prompt_is_parsed_into_explicit_constraints(self) -> None:
        signals = parse_prompt("recent romantic bachata songs in Spanish")
        self.assertIn("bachata", signals["preferred_genres"])
        self.assertIn("romantic", signals["preferred_tags"])
        self.assertEqual(signals["recency"], "recent")
        self.assertEqual(signals["preferred_language"], "spanish")

    def test_recommendations_are_deterministic_and_exclude_seeds(self) -> None:
        request = RecommendRequest(
            seed_track_ids=BACHATA_SEEDS,
            prompt="recent bachata songs",
            limit=5,
        )
        first = generate_recommendations(request)
        second = generate_recommendations(request)
        self.assertEqual(first.request_id, second.request_id)
        self.assertEqual(
            [item.id for item in first.recommendations],
            [item.id for item in second.recommendations],
        )
        self.assertTrue(set(BACHATA_SEEDS).isdisjoint(item.id for item in first.recommendations))

    def test_requested_limit_controls_the_output_size(self) -> None:
        response = generate_recommendations(
            RecommendRequest(seed_track_ids=BACHATA_SEEDS, limit=10)
        )
        self.assertEqual(len(response.recommendations), 10)

    def test_prompt_free_request_does_not_claim_prompt_adherence(self) -> None:
        response = generate_recommendations(
            RecommendRequest(seed_track_ids=BACHATA_SEEDS, prompt="", limit=5)
        )
        self.assertIsNone(response.metrics.prompt_adherence)

    def test_response_contains_explainability_and_quality_metrics(self) -> None:
        response = generate_recommendations(
            RecommendRequest(
                seed_track_ids=BACHATA_SEEDS,
                prompt="recent bachata songs",
                limit=5,
            )
        )
        self.assertEqual(len(response.recommendations), 5)
        self.assertGreater(response.metrics.mean_seed_similarity, 0.5)
        self.assertIsNotNone(response.metrics.prompt_adherence)
        self.assertEqual(response.metrics.artist_diversity, 1.0)
        self.assertIn("seed_similarity", response.recommendations[0].score_components)
        self.assertTrue(response.recommendations[0].reason)
        self.assertTrue(all(0 <= item.score <= 100 for item in response.recommendations))

    def test_mmr_reduces_artist_repetition_in_a_known_scenario(self) -> None:
        seed_tracks = [TRACK_BY_ID[track_id] for track_id in BACHATA_SEEDS]
        profile = build_seed_profile(seed_tracks)
        prompt_signals = parse_prompt("recent bachata songs")
        scored = [
            score_candidate(track, profile, prompt_signals)
            for track in CATALOG
            if track["id"] not in set(BACHATA_SEEDS)
        ]
        relevance_only = sorted(scored, key=lambda item: item["relevance"], reverse=True)[:5]
        reranked = select_with_mmr(scored, profile, limit=5)

        relevance_artists = {item["track"]["artist"] for item in relevance_only}
        reranked_artists = {item.artist for item in reranked}
        self.assertGreater(len(reranked_artists), len(relevance_artists))

    def test_health_reports_the_catalogue_and_algorithm(self) -> None:
        result = health()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["catalogue_size"], len(CATALOG))
        self.assertEqual(result["algorithm"], "hybrid-content-mmr/2.2")

    def test_feedback_is_stored_without_identity_fields(self) -> None:
        feedback = FeedbackRequest(
            request_id="abcdef123456",
            rating=4,
            comment="More variety",
        )
        with tempfile.TemporaryDirectory() as directory:
            feedback_path = Path(directory) / "feedback.jsonl"
            with patch("prototype.app.main.FEEDBACK_PATH", feedback_path):
                response = save_feedback(feedback)
            stored = feedback_path.read_text(encoding="utf-8")
        self.assertEqual(response.status, "recorded")
        self.assertNotIn("name", stored)
        self.assertNotIn("email", stored)
        self.assertEqual(json.loads(stored)["rating"], 4)
        self.assertNotIn("would_add_to_playlist", stored)


if __name__ == "__main__":
    unittest.main()
