from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from statistics import mean


PROTOTYPE_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = PROTOTYPE_DIR.parent
SCENARIOS_PATH = PROTOTYPE_DIR / "data" / "evaluation_scenarios.json"
JSON_OUTPUT_PATH = PROTOTYPE_DIR / "evaluation-results.json"
MARKDOWN_OUTPUT_PATH = PROTOTYPE_DIR / "evaluation-results.md"
RANDOM_REPEATS = 100

sys.path.insert(0, str(PROJECT_DIR))

from prototype.app.main import (  # noqa: E402
    ALGORITHM_NAME,
    ALGORITHM_VERSION,
    CATALOG,
    TRACK_BY_ID,
    Recommendation,
    RecommendRequest,
    build_seed_profile,
    generate_recommendations,
    parse_prompt,
    recommendation_metrics,
)
from prototype.app.recommender import score_candidate  # noqa: E402


def random_recommendations(
    seed_track_ids: list[str], limit: int, random_generator: random.Random
) -> list[Recommendation]:
    candidates = [track for track in CATALOG if track["id"] not in set(seed_track_ids)]
    return [
        Recommendation(
            id=track["id"],
            title=track["title"],
            artist=track["artist"],
            year=track["year"],
            genre=track["genre"],
            score=0.0,
            score_components={},
            reason="random baseline",
        )
        for track in random_generator.sample(candidates, limit)
    ]


def average_random_metrics(scenario: dict, scenario_index: int) -> dict[str, float | None]:
    seed_tracks = [TRACK_BY_ID[track_id] for track_id in scenario["seed_track_ids"]]
    profile = build_seed_profile(seed_tracks)
    prompt_signals = parse_prompt(scenario["prompt"])
    random_generator = random.Random(3070 + scenario_index)
    measurements = []

    for _ in range(RANDOM_REPEATS):
        recommendations = random_recommendations(
            scenario["seed_track_ids"], scenario["limit"], random_generator
        )
        measurements.append(
            recommendation_metrics(recommendations, profile, prompt_signals).model_dump()
        )

    output: dict[str, float | None] = {}
    for metric_name in measurements[0]:
        values = [item[metric_name] for item in measurements if item[metric_name] is not None]
        output[metric_name] = round(mean(values), 3) if values else None
    return output


def relevance_only_recommendations(scenario: dict) -> list[Recommendation]:
    """Rank with the hybrid relevance score but without MMR reranking."""
    seed_ids = set(scenario["seed_track_ids"])
    seed_tracks = [TRACK_BY_ID[track_id] for track_id in scenario["seed_track_ids"]]
    profile = build_seed_profile(seed_tracks)
    prompt_signals = parse_prompt(scenario["prompt"])
    candidates = sorted(
        (
            score_candidate(track, profile, prompt_signals)
            for track in CATALOG
            if track["id"] not in seed_ids
        ),
        key=lambda item: item["relevance"],
        reverse=True,
    )[: scenario["limit"]]
    return [
        Recommendation(
            id=item["track"]["id"],
            title=item["track"]["title"],
            artist=item["track"]["artist"],
            year=item["track"]["year"],
            genre=item["track"]["genre"],
            score=round(item["relevance"] * 100, 2),
            score_components={},
            reason="relevance-only baseline",
        )
        for item in candidates
    ]


def relevance_only_metrics(scenario: dict) -> dict[str, float | None]:
    seed_tracks = [TRACK_BY_ID[track_id] for track_id in scenario["seed_track_ids"]]
    profile = build_seed_profile(seed_tracks)
    prompt_signals = parse_prompt(scenario["prompt"])
    recommendations = relevance_only_recommendations(scenario)
    return recommendation_metrics(recommendations, profile, prompt_signals).model_dump()


def percentage(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def main() -> None:
    scenarios = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
    results = []

    for index, scenario in enumerate(scenarios):
        request = RecommendRequest(**scenario)
        response = generate_recommendations(request)
        hybrid_metrics = response.metrics.model_dump()
        relevance_metrics = relevance_only_metrics(scenario)
        baseline_metrics = average_random_metrics(scenario, index)
        results.append(
            {
                "scenario": scenario["name"],
                "seed_track_ids": scenario["seed_track_ids"],
                "prompt": scenario["prompt"],
                "recommendation_ids": [item.id for item in response.recommendations],
                "hybrid": hybrid_metrics,
                "relevance_only_baseline": relevance_metrics,
                "random_baseline": baseline_metrics,
                "difference": {
                    metric_name: round(hybrid_metrics[metric_name] - baseline_metrics[metric_name], 3)
                    if hybrid_metrics[metric_name] is not None
                    and baseline_metrics[metric_name] is not None
                    else None
                    for metric_name in hybrid_metrics
                },
                "mmr_effect": {
                    metric_name: round(hybrid_metrics[metric_name] - relevance_metrics[metric_name], 3)
                    if hybrid_metrics[metric_name] is not None
                    and relevance_metrics[metric_name] is not None
                    else None
                    for metric_name in hybrid_metrics
                },
            }
        )

    summary = {}
    for system_name in ("hybrid", "relevance_only_baseline", "random_baseline"):
        summary[system_name] = {
            metric_name: round(
                mean(
                    result[system_name][metric_name]
                    for result in results
                    if result[system_name][metric_name] is not None
                ),
                3,
            )
            for metric_name in results[0][system_name]
        }

    payload = {
        "algorithm": f"{ALGORITHM_NAME}/{ALGORITHM_VERSION}",
        "catalogue_size": len(CATALOG),
        "baselines": ["relevance-only ranking", "random selection"],
        "random_baseline_repetitions_per_scenario": RANDOM_REPEATS,
        "scenario_count": len(results),
        "summary": summary,
        "scenarios": results,
    }
    JSON_OUTPUT_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# NextTrack automated evaluation",
        "",
        f"Algorithm: `{payload['algorithm']}`  ",
        f"Catalogue: `{payload['catalogue_size']}` tracks  ",
        "Baselines: relevance-only ranking and the mean of "
        f"`{RANDOM_REPEATS}` deterministic random lists per scenario",
        "",
        "| System | Seed similarity | Prompt adherence | Intra-list diversity | Artist diversity |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for label, system_name in (
        ("NextTrack with MMR", "hybrid"),
        ("Relevance only", "relevance_only_baseline"),
        ("Random selection", "random_baseline"),
    ):
        metrics = summary[system_name]
        lines.append(
            f"| {label} | {percentage(metrics['mean_seed_similarity'])} | "
            f"{percentage(metrics['prompt_adherence'])} | "
            f"{percentage(metrics['intra_list_diversity'])} | "
            f"{percentage(metrics['artist_diversity'])} |"
        )

    lines.extend(
        [
            "",
            "## Scenario results",
            "",
            "| Scenario | Seed similarity | Relevance only | Prompt adherence | Relevance only | "
            "List diversity | Relevance only | Top recommendations |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for result in results:
        top_tracks = ", ".join(TRACK_BY_ID[track_id]["title"] for track_id in result["recommendation_ids"])
        lines.append(
            f"| {result['scenario']} | {percentage(result['hybrid']['mean_seed_similarity'])} | "
            f"{percentage(result['relevance_only_baseline']['mean_seed_similarity'])} | "
            f"{percentage(result['hybrid']['prompt_adherence'])} | "
            f"{percentage(result['relevance_only_baseline']['prompt_adherence'])} | "
            f"{percentage(result['hybrid']['intra_list_diversity'])} | "
            f"{percentage(result['relevance_only_baseline']['intra_list_diversity'])} | "
            f"{top_tracks} |"
        )

    MARKDOWN_OUTPUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(JSON_OUTPUT_PATH)
    print(MARKDOWN_OUTPUT_PATH)


if __name__ == "__main__":
    main()
