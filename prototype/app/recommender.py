from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, ChainMap
from types import SimpleNamespace
from typing import Any, Optional

from fastapi import HTTPException

from .catalog import (
    CATALOG,
    CATALOG_GENRES,
    CATALOG_TAGS,
    POPULARITY_SCORES,
    TRACK_BY_ID,
    TRACK_VECTORS,
    TRACK_VECTOR_NORMS,
    TRACK_ARTISTS,
    TRACK_ARTIST_NAMES,
    clamp,
    contains_term,
    cosine_similarity,
    mean_vector,
    normalize,
    tokenize,
    track_to_search_result,
    track_feature_vector,
)
from .metadata import LANGUAGE_NAMES
from .catalog_quality import artist_keys, artist_names

from .models import (
    RecommendRequest,
    Recommendation,
    RecommendationMetrics,
    RecommendationResponse,
)


ALGORITHM_NAME = "hybrid-content-mmr"
ALGORITHM_VERSION = "2.2"
DIVERSITY_LAMBDA = 0.82


def ranking_index(additional_tracks=()):
    extra = {t["id"]: t for t in additional_tracks if t["id"] not in TRACK_BY_ID}
    vectors = {key: track_feature_vector(t) for key, t in extra.items()}
    return SimpleNamespace(
        tracks=ChainMap(extra, TRACK_BY_ID), vectors=ChainMap(vectors, TRACK_VECTORS),
        norms=ChainMap({k: math.sqrt(sum(v*v for v in values.values())) for k, values in vectors.items()}, TRACK_VECTOR_NORMS),
        artists=ChainMap({k: artist_keys(t) for k, t in extra.items()}, TRACK_ARTISTS),
        names=ChainMap({k: artist_names(t) for k, t in extra.items()}, TRACK_ARTIST_NAMES))


def build_seed_profile(seed_tracks: list[dict[str, Any]], index=None) -> dict[str, Any]:
    index = index or ranking_index()
    genres: Counter[str] = Counter()
    tags: Counter[str] = Counter()
    languages: Counter[str] = Counter()

    for track in seed_tracks:
        if track.get("genre") and track["genre"] != "unknown":
            genres[track["genre"]] += 1
        for subgenre in track.get("subgenres", []):
            if subgenre and subgenre != "unknown":
                genres[subgenre] += 1
        tags.update(track.get("tags", []))
        languages[track["language"]] += 1

    known_energies = [track["energy"] for track in seed_tracks if track.get("energy") is not None]
    known_years = [track["year"] for track in seed_tracks if track.get("year") is not None]
    vector = mean_vector([index.vectors[track["id"]] for track in seed_tracks])
    return {
        "genres": genres,
        "tags": tags,
        "artists": {track["artist"] for track in seed_tracks},
        "artist_keys": set().union(*(index.artists[track["id"]] for track in seed_tracks)),
        "languages": languages,
        "avg_year": sum(known_years) / len(known_years) if known_years else None,
        "avg_energy": sum(known_energies) / len(known_energies) if known_energies else None,
        "vector": vector,
        "vector_norm": math.sqrt(sum(value * value for value in vector.values())),
    }


def parse_prompt(prompt: str) -> dict[str, Any]:
    tokens = tokenize(prompt)
    matched_genres = sorted(
        (genre for genre in CATALOG_GENRES if contains_term(prompt, genre)),
        key=lambda genre: (len(tokenize(genre)), len(genre)),
        reverse=True,
    )
    preferred_genres: list[str] = []
    covered_genre_tokens: set[str] = set()
    for genre in matched_genres:
        genre_tokens = tokenize(genre)
        if genre_tokens <= covered_genre_tokens:
            continue
        preferred_genres.append(genre)
        covered_genre_tokens.update(genre_tokens)

    control_terms = {"recent", "new", "latest", "modern", "current", "old", "older", "classic"}
    preferred_tags = sorted(
        tag
        for tag in CATALOG_TAGS
        if tag not in control_terms
        and not tokenize(tag) <= covered_genre_tokens
        and contains_term(prompt, tag)
    )

    recency = None
    if tokens & {"recent", "new", "latest", "modern", "current"}:
        recency = "recent"
    elif tokens & {"old", "older", "classic", "throwback"}:
        recency = "classic"

    preferred_language = None
    if tokens & {"spanish", "espanol"}:
        preferred_language = "spanish"
    else:
        preferred_language = next((name for name in sorted(LANGUAGE_NAMES)
                                   if name != "instrumental" and contains_term(prompt, name)
                                   and not (name == 'latin' and 'latin' in covered_genre_tokens)), None)

    normalized_prompt = normalize(prompt)
    familiarity_matches = re.finditer(r"\b(?:popular|familiar|hits|well known)\b", normalized_prompt)
    prefer_familiar = any(
        not re.search(r"\b(?:not|without|avoid|less|no)\s+(?:(?:too|very|much|more|any|the|only)\s+)?$",
                      normalized_prompt[:match.start()])
        for match in familiarity_matches
    )

    return {
        "tokens": sorted(tokens),
        "preferred_genres": preferred_genres,
        "preferred_tags": preferred_tags,
        "recency": recency,
        "preferred_language": preferred_language,
        "prefer_discovery": bool(tokens & {"discovery", "discover", "newartist", "variety"}),
        "prefer_familiar": prefer_familiar,
    }


def year_similarity(candidate_year: Optional[int], average_year: Optional[float]) -> float:
    if candidate_year is None or average_year is None:
        return 0.0
    gap = abs(candidate_year - average_year)
    return clamp(1 - min(gap, 25) / 25)


def energy_similarity(candidate_energy: float, average_energy: float) -> float:
    if candidate_energy is None or average_energy is None:
        return 0.0
    gap = abs(candidate_energy - average_energy)
    return clamp(1 - min(gap, 4) / 4)


def prompt_adherence(
    track: dict[str, Any], prompt_signals: dict[str, Any]
) -> tuple[Optional[float], list[str]]:
    checks: list[bool] = []
    reasons: list[str] = []
    candidate_genres = {track["genre"], *track.get("subgenres", [])}
    candidate_tags = set(track.get("tags", []))

    if prompt_signals["preferred_genres"]:
        matched_genres = candidate_genres & set(prompt_signals["preferred_genres"])
        genre_match = bool(matched_genres)
        checks.append(genre_match)
        if genre_match:
            reasons.append(f"matches the `{sorted(matched_genres)[0]}` request")

    if prompt_signals["preferred_tags"]:
        matched_tags = candidate_tags & set(prompt_signals["preferred_tags"])
        tag_match = bool(matched_tags)
        checks.append(tag_match)
        if tag_match:
            reasons.append(f"fits the `{sorted(matched_tags)[0]}` mood")

    if prompt_signals["recency"] == "recent":
        recent_match = track["year"] is not None and track["year"] >= 2020
        checks.append(recent_match)
        if recent_match:
            reasons.append("fits the `recent` request")
    elif prompt_signals["recency"] == "classic":
        classic_match = track["year"] is not None and track["year"] <= 2010
        checks.append(classic_match)
        if classic_match:
            reasons.append("fits the `classic` request")

    preferred_language = prompt_signals["preferred_language"]
    if preferred_language:
        language_match = track["language"] == preferred_language or preferred_language in track.get("languages", [])
        checks.append(language_match)
        if language_match:
            reasons.append(f"is in {preferred_language}")

    if prompt_signals.get("target_energy") is not None:
        energy_match = track.get("energy") is not None and abs(track["energy"] - prompt_signals["target_energy"]) <= 1
        checks.append(energy_match)
        if energy_match:
            reasons.append("fits the requested energy")
    for key, matches in (("min_year", track["year"] is not None and track["year"] >= (prompt_signals.get("min_year") or 0)),
                         ("max_year", track["year"] is not None and track["year"] <= (prompt_signals.get("max_year") or 9999))):
        if prompt_signals.get(key) is not None:
            checks.append(matches)
            if matches:
                reasons.append("fits the requested year range")

    if not checks:
        return None, reasons
    return sum(checks) / len(checks), reasons


def score_candidate(
    track: dict[str, Any],
    profile: dict[str, Any],
    prompt_signals: dict[str, Any],
    index=None,
) -> dict[str, Any]:
    index = index or ranking_index()
    seed_similarity = cosine_similarity(index.vectors[track["id"]], profile["vector"],
                                        index.norms[track["id"]], profile.get("vector_norm"))
    year_score = year_similarity(track["year"], profile["avg_year"])
    year_available = track["year"] is not None and profile["avg_year"] is not None
    energy_score = energy_similarity(track["energy"], profile["avg_energy"])
    energy_available = track.get("energy") is not None and profile["avg_energy"] is not None
    available_weight = (1 if energy_available else 0.85) - (0 if year_available else 0.20)
    base_relevance = (0.65 * seed_similarity + 0.20 * year_score + 0.15 * energy_score) / available_weight
    adherence, prompt_reasons = prompt_adherence(track, prompt_signals)

    relevance = base_relevance if adherence is None else 0.78 * base_relevance + 0.22 * adherence
    if prompt_signals.get("prefer_familiar") and track["id"] in POPULARITY_SCORES:
        relevance += 0.05 * POPULARITY_SCORES[track["id"]]
    if prompt_signals["prefer_discovery"] and index.artists[track["id"]] & profile.get("artist_keys", set()):
        relevance -= 0.08

    return {
        "track": track,
        "relevance": clamp(relevance),
        "seed_similarity": seed_similarity,
        "year_similarity": year_score,
        "energy_similarity": energy_score,
        "prompt_adherence": adherence,
        "prompt_reasons": prompt_reasons,
    }


def build_reason(candidate: dict[str, Any], profile: dict[str, Any]) -> str:
    track = candidate["track"]
    parts: list[str] = []
    candidate_genres = {track["genre"], *track.get("subgenres", [])}
    shared_genres = candidate_genres & set(profile["genres"])
    shared_tags = set(track.get("tags", [])) & set(profile["tags"])

    if shared_genres:
        parts.append(f"shares the `{sorted(shared_genres)[0]}` style")
    if shared_tags:
        parts.append(f"shares the `{sorted(shared_tags)[0]}` mood")
    if candidate["year_similarity"] >= 0.75:
        parts.append("is close to the seed songs in release period")
    if candidate["energy_similarity"] >= 0.75:
        parts.append("has a similar energy level")
    parts.extend(candidate["prompt_reasons"][:2])

    if not parts:
        parts.append("has a strong combined metadata match")
    return "; ".join(dict.fromkeys(parts))


def select_with_mmr(
    scored_candidates: list[dict[str, Any]],
    profile: dict[str, Any],
    limit: int,
    diversity_lambda: float = DIVERSITY_LAMBDA,
    index=None,
) -> list[Recommendation]:
    index = index or ranking_index()
    ranked = sorted(scored_candidates, key=lambda item: item["relevance"], reverse=True)
    # Retain high-scoring options while bringing in alternative artists that would
    # otherwise disappear below a popular artist's many near-identical releases.
    remaining = ranked[:120]
    included = {item["track"]["id"] for item in remaining}
    artist_counts = Counter()
    alternatives = 0
    for item in ranked:
        keys = index.artists[item["track"]["id"]]
        if any(artist_counts[key] >= 2 for key in keys):
            continue
        artist_counts.update(keys)
        alternatives += 1
        if item["track"]["id"] not in included:
            remaining.append(item)
            included.add(item["track"]["id"])
        if alternatives >= 120:
            break
    selected: list[dict[str, Any]] = []

    while remaining and len(selected) < limit:
        best_candidate = None
        best_mmr_score = float("-inf")
        best_redundancy = 0.0

        used_artists = set().union(*(index.artists[item["track"]["id"]] for item in selected))
        best_relevance = max(item["relevance"] for item in remaining)
        fresh = [item for item in remaining
                 if not index.artists[item["track"]["id"]] & used_artists
                 and item["relevance"] >= best_relevance * 0.9]
        eligible = fresh if selected and fresh else remaining
        for candidate in eligible:
            candidate_vector = index.vectors[candidate["track"]["id"]]
            redundancy = max(
                (
                    cosine_similarity(candidate_vector, index.vectors[item["track"]["id"]],
                                      index.norms[candidate["track"]["id"]],
                                      index.norms[item["track"]["id"]])
                    for item in selected
                ),
                default=0.0,
            )
            same_artist_penalty = 0.12 if index.artists[candidate["track"]["id"]] & used_artists else 0.0
            mmr_score = (
                diversity_lambda * candidate["relevance"]
                - (1 - diversity_lambda) * redundancy
                - same_artist_penalty
            )
            if mmr_score > best_mmr_score:
                best_candidate = candidate
                best_mmr_score = mmr_score
                best_redundancy = redundancy

        assert best_candidate is not None
        remaining.remove(best_candidate)
        best_candidate["mmr_score"] = best_mmr_score
        best_candidate["redundancy"] = best_redundancy
        selected.append(best_candidate)

    return [
        Recommendation(
            id=item["track"]["id"],
            title=item["track"]["title"],
            artist=item["track"]["artist"],
            year=item["track"]["year"],
            genre=item["track"]["genre"],
            source=item["track"].get("source", "catalogue"),
            source_url=item["track"].get("source_url"),
            score=round(item["relevance"] * 100, 2),
            score_components={
                "seed_similarity": round(item["seed_similarity"], 3),
                "year_similarity": round(item["year_similarity"], 3),
                "energy_similarity": round(item["energy_similarity"], 3),
                "prompt_adherence": round(item["prompt_adherence"], 3)
                if item["prompt_adherence"] is not None
                else 0.0,
                "diversity_penalty": round(item["redundancy"], 3),
            },
            reason=build_reason(item, profile),
        )
        for item in selected
    ]


def recommendation_metrics(
    recommendations: list[Recommendation],
    profile: dict[str, Any],
    prompt_signals: dict[str, Any],
    index=None,
) -> RecommendationMetrics:
    index = index or ranking_index()
    if not recommendations:
        return RecommendationMetrics(
            mean_seed_similarity=0.0,
            prompt_adherence=None,
            intra_list_diversity=0.0,
            artist_diversity=0.0,
        )

    tracks = [index.tracks[item.id] for item in recommendations]
    seed_scores = [
        cosine_similarity(index.vectors[track["id"]], profile["vector"],
                                        index.norms[track["id"]], profile.get("vector_norm")) for track in tracks
    ]
    adherence_scores = [prompt_adherence(track, prompt_signals)[0] for track in tracks]
    measured_adherence = [score for score in adherence_scores if score is not None]
    pairwise_distances = [
        1 - cosine_similarity(index.vectors[left["id"]], index.vectors[right["id"]])
        for position, left in enumerate(tracks)
        for right in tracks[position + 1 :]
    ]

    return RecommendationMetrics(
        mean_seed_similarity=round(sum(seed_scores) / len(seed_scores), 3),
        prompt_adherence=round(sum(measured_adherence) / len(measured_adherence), 3)
        if measured_adherence
        else None,
        intra_list_diversity=round(
            sum(pairwise_distances) / len(pairwise_distances) if pairwise_distances else 0.0,
            3,
        ),
        artist_diversity=round(
            len(set().union(*(index.names[t["id"]] for t in tracks))) /
            max(1, sum(len(index.names[t["id"]]) for t in tracks)), 3),
    )


def make_request_id(request: RecommendRequest, recommendation_ids: list[str]) -> str:
    content = json.dumps(
        {
            "algorithm": ALGORITHM_VERSION,
            "seed_track_ids": request.seed_track_ids,
            "prompt": normalize(request.prompt),
            "limit": request.limit,
            "recommendation_ids": recommendation_ids,
        },
        sort_keys=True,
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def matches_year_constraints(track: dict[str, Any], signals: dict[str, Any]) -> bool:
    year = track.get("year")
    if year is None:
        # Missing dates cannot substantiate a requested period.
        return (signals.get("min_year") is None and signals.get("max_year") is None
                and signals.get("recency") not in {"recent", "classic"})
    return ((signals.get("min_year") is None or year >= signals["min_year"])
            and (signals.get("max_year") is None or year <= signals["max_year"])
            and (signals.get('recency') != 'recent' or year >= 2020)
            and (signals.get('recency') != 'classic' or year <= 2010))


def matches_style_constraints(track, signals):
    requested = set(signals.get('preferred_genres', []))
    descriptors = requested & {'instrumental', 'acoustic'}
    descriptors.update(set(signals.get('preferred_tags', [])) & {'instrumental', 'acoustic'})
    if signals.get('preferred_language') == 'instrumental':
        descriptors.add('instrumental')
    genres = requested - descriptors
    styles = {track['genre'], *track.get('subgenres', [])}
    evidence = styles | set(track.get('tags', [])) | set(track.get('languages', []))
    # Descriptors are additional requirements, not alternative music genres.
    return (not genres or bool(genres & styles)) and descriptors <= evidence


def generate_recommendations(request: RecommendRequest, signals: Optional[dict[str, Any]] = None,
                             additional_tracks=()) -> RecommendationResponse:
    index = ranking_index(additional_tracks)
    try:
        seed_tracks = list({index.tracks[track_id]["id"]: index.tracks[track_id]
                            for track_id in request.seed_track_ids}.values())
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown track id: {exc.args[0]}") from exc

    profile = build_seed_profile(seed_tracks, index)
    prompt_signals = dict(signals) if signals is not None else parse_prompt(request.prompt)
    prompt_signals["prefer_familiar"] = parse_prompt(request.prompt)["prefer_familiar"]
    seed_ids = {track["id"] for track in seed_tracks}
    extra_ids = {t["id"] for t in additional_tracks}
    candidate_tracks = list(additional_tracks) + [t for t in CATALOG if t["id"] not in extra_ids]
    scored_candidates = [
        score_candidate(track, profile, prompt_signals, index)
        for track in candidate_tracks
        if track["id"] not in seed_ids
        and not ({track["genre"], *track.get("subgenres", [])} & set(prompt_signals.get("excluded_genres", [])))
        and matches_year_constraints(track, prompt_signals)
        and matches_style_constraints(track, prompt_signals)
    ]
    if not seed_tracks:
        # With no seed context, genre defines the discovery pool. Mood/year matches
        # must not displace that style merely by accumulating more soft checks.
        requested_genres = set(prompt_signals["preferred_genres"])
        scored_candidates = [
            item for item in scored_candidates
            if ((item["prompt_adherence"] or 0) > 0
                or (prompt_signals["prefer_familiar"] and item["prompt_adherence"] is None
                    and item["track"]["id"] in POPULARITY_SCORES))
            and (not requested_genres or requested_genres & {
                item["track"]["genre"], *item["track"].get("subgenres", [])
            })
        ]
        if not scored_candidates:
            raise HTTPException(status_code=422, detail="No catalogue matches for this prompt. Try another style or add a song.")
        for item in scored_candidates:
            adherence = item["prompt_adherence"] or 0.0
            if prompt_signals["prefer_familiar"]:
                item["relevance"] = 0.95 * adherence + 0.05 * POPULARITY_SCORES.get(item["track"]["id"], 0)
            else:
                item["relevance"] = adherence
    if not scored_candidates:
        raise HTTPException(status_code=422, detail="No catalogue matches for these constraints. Try a broader prompt.")
    recommendations = select_with_mmr(scored_candidates, profile, request.limit, index=index)
    metrics = recommendation_metrics(recommendations, profile, prompt_signals, index)

    if not seed_tracks:
        metrics.mean_seed_similarity = None
        for item in recommendations:
            for key in ("seed_similarity", "year_similarity", "energy_similarity"):
                item.score_components.pop(key, None)

    return RecommendationResponse(
        seed_count=len(seed_tracks),
        prompt_interpretation=prompt_signals,
        verification={"source": "local_catalogue_and_musicbrainz" if additional_tracks else "local_catalogue", "verified_track_rate": 1.0},
        request_id=make_request_id(request, [item.id for item in recommendations]),
        algorithm={
            "name": ALGORITHM_NAME,
            "version": ALGORITHM_VERSION,
            "candidate_catalogue_size": len(candidate_tracks),
            "diversity_lambda": DIVERSITY_LAMBDA,
        },
        seed_tracks=[track_to_search_result(track) for track in seed_tracks],
        prompt=request.prompt,
        prompt_signals={
            "preferred_genres": prompt_signals["preferred_genres"],
            "preferred_tags": prompt_signals["preferred_tags"],
            "recency": prompt_signals["recency"],
            "preferred_language": prompt_signals["preferred_language"],
            "prefer_discovery": prompt_signals["prefer_discovery"],
        },
        recommendations=recommendations,
        metrics=metrics,
    )
