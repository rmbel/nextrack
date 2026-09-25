from __future__ import annotations

import gzip
import json
import math
import os
import unicodedata
import re
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import SearchResult
from .catalog_quality import prepare_catalog, artist_keys, artist_names, recording_key, _provider_keys
from .metadata import apply_metadata, SUPPORTED_TAGS


BASE_DIR = Path(__file__).resolve().parent.parent
CATALOG_PATH = BASE_DIR / "data" / "catalog.json"
FEEDBACK_PATH = BASE_DIR / "data" / "user_feedback.jsonl"
WEB_DIR = BASE_DIR / "web"
SEARCHABLE_FIELDS = ("title", "artist", "genre", "language")

BASELINE_MODE = os.getenv("NEXTTRACK_CATALOG_MODE") == "baseline"


def _listener_snapshot_order(popularity: dict[str, Any]) -> tuple[datetime, int, int]:
    """Compare observations without adding listener counts from separate records."""
    if (popularity.get("source") not in {"mlhd-plus", "taste-profile"}
            or popularity.get("metric") != "unique_listeners"
            or type(popularity.get("value")) is not int or popularity["value"] < 0
            or type(popularity.get("rank")) is not int or popularity["rank"] < 1
            or not isinstance(popularity.get("source_url"), str)
            or not popularity["source_url"].startswith(("https://", "http://"))
            or not isinstance(popularity.get("snapshot_date"), str)):
        raise ValueError("Popular catalogue has invalid listener provenance")
    try:
        observed = datetime.fromisoformat(popularity["snapshot_date"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Popular catalogue has an invalid snapshot date") from exc
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return observed, -popularity["rank"], popularity["value"]


def _extend_with_popular_tracks(
        tracks: list[dict[str, Any]], aliases: dict[str, str], quality: dict[str, int],
        additions: list[dict[str, Any]], *, evidence_by_id: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, int]]:
    """Append listener-ranked records after the base cap, preserving base identities.

    Existing records keep their complete metadata and canonical order. Only the
    separate listener observation can change on a match. Ambiguous additions
    that connect two base recordings are excluded rather than merging the base.
    """
    if not isinstance(additions, list):
        raise ValueError("Popular catalogue must be a list of tracks")
    if not additions:
        return tracks, aliases, quality
    for track in additions:
        if (not isinstance(track, dict)
                or any(not isinstance(track.get(field), str) or not track[field].strip()
                       for field in ("id", "title", "artist", "genre"))
                or "year" not in track
                or (track["year"] is not None and (type(track["year"]) is not int or track["year"] <= 0))
                or not isinstance(track.get("listener_popularity"), dict)):
            raise ValueError("Popular catalogue has an incomplete track")
        _listener_snapshot_order(track["listener_popularity"])

    enriched = apply_metadata(additions, evidence_by_id=evidence_by_id or {})
    prepared = prepare_catalog(enriched)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for track in enriched:
        canonical = prepared.aliases.get(track["id"])
        if canonical is not None:
            groups[canonical].append(track)

    result = list(tracks)
    result_aliases = dict(aliases)
    positions = {track["id"]: index for index, track in enumerate(tracks)}
    provider_index: dict[tuple[str, str], set[str]] = defaultdict(set)
    recording_index: dict[tuple[str, tuple[str, ...]], set[str]] = defaultdict(set)
    for track in tracks:
        for key in _provider_keys(track):
            provider_index[key].add(track["id"])
        key = recording_key(track)
        if all(key):
            recording_index[key].add(track["id"])
    for alias, canonical in aliases.items():
        if canonical in positions:
            for key in _provider_keys({"id": alias}):
                provider_index[key].add(canonical)

    quarantined = len(prepared.quarantined)
    added = 0
    for candidate in prepared.tracks:
        group = groups[candidate["id"]]
        matches: set[str] = set()
        for track in group:
            for key in _provider_keys(track):
                matches.update(provider_index.get(key, ()))
            key = recording_key(track)
            if all(key):
                matches.update(recording_index.get(key, ()))
        if len(matches) > 1:
            quarantined += len(group)
            continue
        canonical = next(iter(matches)) if matches else candidate["id"]
        if matches:
            index = positions[canonical]
            # Copy on write; other metadata and caller-owned base objects stay intact.
            target = dict(result[index])
            result[index] = target
        else:
            target = candidate
            result.append(target)
            positions[canonical] = len(result) - 1
            added += 1
        observations = [track["listener_popularity"] for track in group]
        if target.get("listener_popularity"):
            observations.append(target["listener_popularity"])
        target["listener_popularity"] = deepcopy(max(observations, key=_listener_snapshot_order))
        for track in group:
            result_aliases[track["id"]] = canonical

    return result, result_aliases, {
        **quality,
        "raw_records": quality["raw_records"] + len(additions),
        "accepted_records": len(result),
        "duplicate_records": quality["duplicate_records"] + len(additions) - added - quarantined,
        "quarantined_records": quality["quarantined_records"] + quarantined,
    }


def _load_runtime_snapshot(path: Path) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, int]]:
    """Load already-normalized deployment data without raw enrichment copies.

    Snapshots are produced by scripts/export_runtime_catalog.py. An invalid or
    stale schema fails startup explicitly instead of silently serving other data.
    """
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        payload = json.load(stream)
    if (not isinstance(payload, dict) or payload.get("schema_version") != 1
            or payload.get("catalogue_mode") != "active"):
        raise ValueError("Unsupported runtime catalogue snapshot schema or mode")
    tracks, aliases, quality = (payload.get(name) for name in ("tracks", "aliases", "quality"))
    if not isinstance(tracks, list) or not tracks:
        raise ValueError("Runtime catalogue snapshot must contain tracks")
    if not isinstance(aliases, dict) or not isinstance(quality, dict):
        raise ValueError("Runtime catalogue snapshot is missing aliases or quality counts")
    required = {"id", "title", "artist", "year", "genre", "language", "energy"}
    if any(not isinstance(track, dict) or not required.issubset(track)
           or not isinstance(track["id"], str) or not track["id"] for track in tracks):
        raise ValueError("Runtime catalogue snapshot has an incomplete track")
    ids = {track["id"] for track in tracks}
    if len(ids) != len(tracks):
        raise ValueError("Runtime catalogue snapshot has duplicate canonical IDs")
    if any(not isinstance(alias, str) or not isinstance(canonical, str) or canonical not in ids
           or (alias in ids and alias != canonical) for alias, canonical in aliases.items()):
        raise ValueError("Runtime catalogue snapshot has an invalid canonical alias")
    count_fields = ("raw_records", "accepted_records", "duplicate_records", "quarantined_records")
    if (any(type(quality.get(field)) is not int or quality[field] < 0 for field in count_fields)
            or quality["accepted_records"] != len(tracks)):
        raise ValueError("Runtime catalogue snapshot has inconsistent quality counts")
    # Export stores only non-identity aliases; preserve normal runtime semantics.
    return tracks, {**{track_id: track_id for track_id in ids}, **aliases}, quality


snapshot_name = os.getenv("NEXTTRACK_CATALOG_SNAPSHOT")
if snapshot_name and not BASELINE_MODE:
    snapshot_path = Path(snapshot_name)
    if not snapshot_path.is_absolute():
        snapshot_path = BASE_DIR / snapshot_path
    CATALOG, CATALOG_ALIASES, CATALOG_QUALITY = _load_runtime_snapshot(snapshot_path)
else:
    with CATALOG_PATH.open(encoding="utf-8") as catalog_file:
        raw_catalog = json.load(catalog_file)

if not snapshot_name and not BASELINE_MODE:
    for filename in ("catalog-extension.json", "catalog-expansion.json"):
        path = BASE_DIR / "data" / filename
        if path.is_file():
            raw_catalog.extend(json.loads(path.read_text(encoding="utf-8")))
    evidence_path = BASE_DIR / "data" / "metadata-evidence.json"
    evidence = json.loads(evidence_path.read_text()) if evidence_path.is_file() else {}
    enriched = apply_metadata(raw_catalog, evidence_by_id=evidence)
    quality = prepare_catalog(enriched)
    CATALOG = quality.tracks[:50000]
    CATALOG_ALIASES = quality.aliases
    CATALOG_QUALITY = {"raw_records": len(raw_catalog), "accepted_records": len(CATALOG),
                       "duplicate_records": sum(len(ids)-1 for ids in quality.duplicate_groups.values()),
                       "quarantined_records": len(quality.quarantined)}
    popular_path = BASE_DIR / "data" / "catalog-popular.json"
    if popular_path.is_file():
        CATALOG, CATALOG_ALIASES, CATALOG_QUALITY = _extend_with_popular_tracks(
            CATALOG, CATALOG_ALIASES, CATALOG_QUALITY,
            json.loads(popular_path.read_text(encoding="utf-8")), evidence_by_id=evidence)
elif BASELINE_MODE:
    CATALOG = raw_catalog
    CATALOG_ALIASES = {track["id"]: track["id"] for track in CATALOG}
    CATALOG_QUALITY = {"raw_records": len(CATALOG), "accepted_records": len(CATALOG),
                       "duplicate_records": 0, "quarantined_records": 0}

TRACK_BY_ID = {track["id"]: track for track in CATALOG}
for alias, canonical in CATALOG_ALIASES.items():
    if canonical in TRACK_BY_ID:
        TRACK_BY_ID[alias] = TRACK_BY_ID[canonical]
TRACK_ARTISTS = {track["id"]: artist_keys(track) for track in CATALOG}
TRACK_ARTIST_NAMES = {key: frozenset(name for name in keys if not name.startswith("artist-id:"))
                      for key, keys in TRACK_ARTISTS.items()}

# Historical provider-local listening counts are not a global popularity scale.
_popularity_groups = {}
for track in CATALOG:
    popularity = track.get("popularity") or {}
    if (popularity.get("source") == "fma" and popularity.get("metric") == "track_listens"
            and isinstance(popularity.get("value"), (int, float)) and popularity["value"] >= 0):
        _popularity_groups.setdefault(track["genre"], []).append((popularity["value"], track["id"]))
POPULARITY_SCORES = {}
for group in _popularity_groups.values():
    maximum = max(value for value, _ in group)
    for value, track_id in group:
        POPULARITY_SCORES[track_id] = math.log1p(value) / math.log1p(maximum) if maximum else 0.0

CATALOG_GENRES = {
    genre
    for track in CATALOG
    for genre in [track["genre"], *track.get("subgenres", [])]
    if genre and genre != "unknown"
}
CATALOG_TAGS = {tag for track in CATALOG for tag in track.get("tags", [])} | SUPPORTED_TAGS


def normalize(text: str) -> str:
    text = "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))
    return " ".join("".join(c if c.isalnum() or c.isspace() else " " for c in text).split())


def tokenize(text: str) -> set[str]:
    return {token for token in normalize(text).split() if token}


def contains_term(text: str, term: str) -> bool:
    normalized_text = f" {normalize(text)} "
    normalized_term = f" {normalize(term)} "
    return normalized_term in normalized_text


def clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def track_to_search_result(track: dict[str, Any]) -> SearchResult:
    return SearchResult(
        id=track["id"],
        title=track["title"],
        artist=track["artist"],
        year=track["year"],
        genre=track["genre"],
        source=track.get("source", "catalogue"),
        source_url=track.get("source_url"),
    )


def track_feature_vector(track: dict[str, Any]) -> dict[str, float]:
    vector: dict[str, float] = {}
    if track.get("genre") and track["genre"] != "unknown":
        vector[f"genre:{track['genre']}"] = 3.0
    for name in (TRACK_ARTIST_NAMES.get(track["id"]) or artist_names(track)):
        vector[f"artist:{name}"] = 0.35
    if track.get("language") not in (None, "unknown", ""):
        vector[f"language:{track['language']}"] = 0.6
    for subgenre in track.get("subgenres", []):
        if subgenre and subgenre != "unknown":
            vector[f"genre:{subgenre}"] = 1.8
    for tag in track.get("tags", []):
        vector[f"tag:{tag}"] = 1.25
    return vector


TRACK_VECTORS = {track["id"]: track_feature_vector(track) for track in CATALOG}
TRACK_VECTOR_NORMS = {key: math.sqrt(sum(value * value for value in vector.values()))
                      for key, vector in TRACK_VECTORS.items()}


def mean_vector(vectors: list[dict[str, float]]) -> dict[str, float]:
    combined: Counter[str] = Counter()
    for vector in vectors:
        combined.update(vector)
    count = len(vectors)
    return {feature: value / count for feature, value in combined.items()}


def cosine_similarity(left: dict[str, float], right: dict[str, float],
                      left_norm=None, right_norm=None) -> float:
    if not left or not right:
        return 0.0
    common_features = left.keys() & right.keys()
    dot_product = sum(left[feature] * right[feature] for feature in common_features)
    if left_norm is None: left_norm = math.sqrt(sum(value * value for value in left.values()))
    if right_norm is None: right_norm = math.sqrt(sum(value * value for value in right.values()))
    if not left_norm or not right_norm:
        return 0.0
    return dot_product / (left_norm * right_norm)


SEARCH_INDEX = [
    (track, normalize(track["title"]), normalize(track["artist"]),
     " ".join(normalize(str(track.get(field, ""))) for field in SEARCHABLE_FIELDS))
    for track in CATALOG
]


def search_catalog(query: str) -> list[dict[str, Any]]:
    if not query.strip():
        return []

    normalized_query = normalize(query)
    query_tokens = set(normalized_query.split())
    scored: list[tuple[float, dict[str, Any]]] = []

    for track, title, artist, haystack in SEARCH_INDEX:
        token_hits = sum(1 for token in query_tokens if token in haystack)
        phrase_hit = int(normalized_query in haystack)
        prefix_hit = int(title.startswith(normalized_query) or artist.startswith(normalized_query))
        score = token_hits * 2 + phrase_hit * 3 + prefix_hit * 2
        if score > 0:
            scored.append((score, track))

    scored.sort(key=lambda item: (item[0], item[1]["year"] if item[1]["year"] is not None else float("-inf")),
                reverse=True)
    return [item[1] for item in scored[:8]]
