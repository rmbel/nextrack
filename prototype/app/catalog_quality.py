"""Conservative catalogue identity and credited-artist helpers.

These are metadata identities, not audio fingerprints. Full version labels and
the complete credited-artist set must agree for the title-based fallback.
Explicit provider recording IDs and reviewed overrides are stronger evidence.
The source snapshots are never changed; aliases keep existing seed IDs usable.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re
import unicodedata
from typing import Any, Iterable, Mapping, Optional


OVERRIDES_PATH = Path(__file__).resolve().parents[1] / "data" / "catalog-quality-overrides.json"


@lru_cache(maxsize=131072)
def _normal(value: str) -> str:
    value = "".join(char for char in unicodedata.normalize("NFKD", value.casefold())
                    if not unicodedata.combining(char))
    return " ".join("".join(char if char.isalnum() else " " for char in value).split())


def _load_overrides() -> dict[str, Any]:
    if OVERRIDES_PATH.is_file():
        return json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    return {}


DEFAULT_OVERRIDES = _load_overrides()
# These are indivisible artist names, not a list of individual performers.
PROTECTED_ARTIST_NAMES = (
    "Earth, Wind & Fire", "Earth Wind & Fire", "Florence + The Machine",
    "Florence and the Machine", "Tyler, The Creator", "AC/DC", "KC & The Sunshine Band",
    "Kool & The Gang", "Bob Marley & The Wailers", "Huey Lewis & The News",
    "Tom Petty & The Heartbreakers", "Simon & Garfunkel", "Hall & Oates",
    "Daryl Hall & John Oates", "Crosby, Stills & Nash", "Crosby, Stills, Nash & Young",
    "Emerson, Lake & Palmer", "Peter, Paul & Mary", "Angus & Julia Stone",
    "Of Monsters and Men", "Sly & The Family Stone", "Prince & The Revolution",
)
_SEPARATOR = re.compile(r"\s*(?:,\s*|\s+&\s+|\s+(?:feat\.?|ft\.?|featuring|with|vs\.?)\s+)\s*", re.I)
_FEATURE = re.compile(r"[\[(]\s*(?:feat\.?|ft\.?|featuring)\s+([^\])]+)[\])]", re.I)


def _config(overrides: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    return {**DEFAULT_OVERRIDES, **(overrides or {})}


def _canonical_name(name: str, config: Mapping[str, Any]) -> str:
    aliases = {_normal(alias): _normal(canonical)
               for alias, canonical in config.get("artist_aliases", {}).items()}
    key = _normal(name)
    visited: set[str] = set()
    while key in aliases and key not in visited:
        visited.add(key)
        key = aliases[key]
    return key


def _split_credit(value: str, config: Mapping[str, Any]) -> list[str]:
    # Protect compound names inside a longer collaboration too.
    protected = (*PROTECTED_ARTIST_NAMES, *config.get("protected_artist_names", []))
    replacements: dict[str, str] = {}
    for name in sorted(protected, key=len, reverse=True):
        pattern = re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)", re.I)
        token = "ARTISTPLACEHOLDER" + str(len(replacements))
        if pattern.search(value):
            replacements[token] = name
            value = pattern.sub(token, value)
    result = []
    for part in _SEPARATOR.split(value):
        part = part.strip()
        if part:
            result.append(replacements.get(part, part))
    return result


def _structured_credits(track: Mapping[str, Any]) -> list[Any]:
    for field in ("artist_credits", "artist-credit", "artists", "artist_names"):
        credits = track.get(field)
        if isinstance(credits, list) and credits:
            return credits
    return []


def _artist_identities(track: Mapping[str, Any], config: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    names: set[str] = set()
    identifiers: set[str] = set()
    credits = _structured_credits(track)
    for credit in credits:
        if isinstance(credit, str):
            names.add(_canonical_name(credit, config))
        elif isinstance(credit, dict):
            artist = credit.get("artist") or credit
            name = artist.get("name") or credit.get("name")
            if name:
                names.add(_canonical_name(name, config))
            provider_id = artist.get("id") or credit.get("id")
            # MusicBrainz's raw credit has an artist object; a generic ID needs
            # its explicit provider so IDs from different services cannot collide.
            source = artist.get("source") or credit.get("source") or track.get("source")
            if not source and "artist" in credit:
                source = "musicbrainz"
            if provider_id and source:
                identifiers.add("artist-id:" + str(source).casefold() + ":" + str(provider_id))
    if not names:
        names.update(_canonical_name(name, config)
                     for name in _split_credit(str(track.get("artist", "")), config))
    # Some providers place a featured performer in the title instead of credits.
    for match in _FEATURE.finditer(str(track.get("title", ""))):
        names.update(_canonical_name(name, config) for name in _split_credit(match.group(1), config))
    names.discard("")
    return names, identifiers


def artist_names(track: Mapping[str, Any], *, overrides: Optional[Mapping[str, Any]] = None) -> frozenset[str]:
    """Canonical constituent names, once each, for artist-diversity metrics."""
    return frozenset(_artist_identities(track, _config(overrides))[0])


def artist_keys(track: Mapping[str, Any], *, overrides: Optional[Mapping[str, Any]] = None) -> frozenset[str]:
    """Names plus provider artist IDs for overlap; names bridge old/new data."""
    names, identifiers = _artist_identities(track, _config(overrides))
    return frozenset(names | identifiers)


def recording_key(track: Mapping[str, Any], *, overrides: Optional[Mapping[str, Any]] = None) -> tuple[str, tuple[str, ...]]:
    """Strict title + full unordered credits; live/remix/acoustic labels survive.

    Only featured-artist annotations move out of the title into the credit set.
    No fuzzy title, subset-artist, transliteration or punctuation-free slug match
    is used. Distinct provider IDs can still represent the same recording.
    """
    title = _normal(_FEATURE.sub("", str(track.get("title", ""))))
    disambiguation = track.get("recording_disambiguation") or track.get("disambiguation")
    if disambiguation:
        title += " | version: " + _normal(str(disambiguation))
    return title, tuple(sorted(artist_names(track, overrides=overrides)))


def _provider_keys(track: Mapping[str, Any]) -> set[tuple[str, str]]:
    keys = {("id", str(track["id"]))}
    if track.get("source") and track.get("source_id"):
        keys.add((str(track["source"]).casefold(), str(track["source_id"])))
    # ID prefixes also bridge snapshots that omitted source_id.
    if ":" in str(track["id"]):
        source, identifier = str(track["id"]).split(":", 1)
        if source in {"musicbrainz", "itunes", "spotify"}:
            keys.add((source, identifier))
    for source, identifiers in track.get("provider_ids", {}).items():
        if not isinstance(identifiers, (list, tuple, set)):
            identifiers = [identifiers]
        keys.update((str(source).casefold(), str(identifier)) for identifier in identifiers if identifier)
    for field in ("recording_mbid", "musicbrainz_recording_id"):
        if track.get(field):
            keys.add(("musicbrainz", str(track[field])))
    return keys


@dataclass
class CatalogQualityResult:
    tracks: list[dict[str, Any]]
    aliases: dict[str, str]
    quarantined: list[dict[str, str]]
    duplicate_groups: dict[str, list[str]]


def _fill_missing(canonical: dict[str, Any], other: Mapping[str, Any]) -> None:
    copied = []
    for field in ("language", "energy", "tags", "artist_credits", "artist-credit", "artist_names"):
        missing = canonical.get(field) in (None, "", "unknown", [])
        value = other.get(field)
        if missing and value not in (None, "", "unknown", []):
            canonical[field] = deepcopy(value)
            copied.append(field)
            quality_field = "mood" if field == "tags" else field
            if quality_field in other.get("metadata_quality", {}):
                canonical.setdefault("metadata_quality", {})[quality_field] = other["metadata_quality"][quality_field]
            # Preserve field-level evidence if this snapshot provides it.
            for evidence_key in ("metadata_evidence", "evidence"):
                evidence = other.get(evidence_key)
                if isinstance(evidence, dict) and field in evidence:
                    canonical.setdefault(evidence_key, {})[field] = deepcopy(evidence[field])
    if not canonical.get("languages") and other.get("languages") and canonical.get("language") == other.get("language"):
        canonical["languages"] = deepcopy(other["languages"])
        copied.append("languages")
    if copied:
        provenance = {"from_track_id": other["id"], "fields": copied}
        for field in ("source", "source_url", "metadata_evidence"):
            if other.get(field):
                provenance[field] = deepcopy(other[field])
        canonical.setdefault("metadata_inherited", []).append(provenance)


def prepare_catalog(tracks: Iterable[Mapping[str, Any]], *,
                    overrides: Optional[Mapping[str, Any]] = None) -> CatalogQualityResult:
    """Deduplicate in near-linear time; first input wins and old IDs remain aliases.

    Overrides can supply artist_aliases, protected_artist_names, quarantine (ID
    to reason/record), and recording_groups (explicit reviewed equivalent IDs).
    Quarantined IDs are deliberately absent from aliases and accepted tracks.
    """
    config = _config(overrides)
    quarantine = config.get("quarantine", {})
    accepted: list[Mapping[str, Any]] = []
    quarantined = []
    for track in tracks:
        track_id = str(track["id"])
        if track_id in quarantine:
            item = quarantine[track_id]
            reason = item.get("reason", "Reviewed catalogue exclusion") if isinstance(item, dict) else str(item)
            quarantined.append({"id": track_id, "reason": reason})
        else:
            accepted.append(track)
    parents = list(range(len(accepted)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left, right = find(left), find(right)
        parents[max(left, right)] = min(left, right)

    seen_provider: dict[tuple[str, str], int] = {}
    seen_recording: dict[tuple[str, tuple[str, ...]], int] = {}
    by_id: dict[str, int] = {}
    for index, track in enumerate(accepted):
        by_id[str(track["id"])] = index
        for provider_key in _provider_keys(track):
            if provider_key in seen_provider:
                union(index, seen_provider[provider_key])
            else:
                seen_provider[provider_key] = index
        key = recording_key(track, overrides=config)
        if all(key):
            if key in seen_recording:
                union(index, seen_recording[key])
            else:
                seen_recording[key] = index
    for group in config.get("recording_groups", []):
        ids = group.get("ids", []) if isinstance(group, dict) else group
        indices = [by_id[identifier] for identifier in ids if identifier in by_id]
        for index in indices[1:]:
            union(indices[0], index)

    canonical_by_index: dict[int, dict[str, Any]] = {}
    aliases: dict[str, str] = {}
    groups: dict[str, list[str]] = {}
    for index, track in enumerate(accepted):
        canonical_index = find(index)
        if canonical_index not in canonical_by_index:
            canonical_by_index[canonical_index] = deepcopy(accepted[canonical_index])
        canonical = canonical_by_index[canonical_index]
        track_id, canonical_id = str(track["id"]), str(canonical["id"])
        aliases[track_id] = canonical_id
        if track_id not in groups.setdefault(canonical_id, []):
            groups[canonical_id].append(track_id)
        if index != canonical_index:
            _fill_missing(canonical, track)
    return CatalogQualityResult(
        tracks=list(canonical_by_index.values()), aliases=aliases, quarantined=quarantined,
        duplicate_groups={key: ids for key, ids in groups.items() if len(ids) > 1},
    )
