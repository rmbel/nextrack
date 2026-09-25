"""Evidence-aware metadata normalisation; no traits inferred from musical genre.

MusicBrainz tags are community descriptions, not audio measurements. Lyrics
language comes only from a recording's linked work, with performance exceptions
handled explicitly. Older prototype genre-derived traits are withdrawn at load.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "metadata-evidence.json"
MOOD_ALIASES = {
    "calm": "calm", "relaxing": "calm", "relaxed": "calm", "peaceful": "calm",
    "chill": "chill", "chilled": "chill", "happy": "happy", "joyful": "happy",
    "sad": "sad", "melancholic": "melancholic", "melancholy": "melancholic",
    "romantic": "romantic", "romance": "romantic", "dreamy": "dreamy",
    "energetic": "energetic", "high energy": "energetic", "upbeat": "upbeat",
    "low energy": "calm", "mellow": "mellow", "soft": "soft", "smooth": "smooth",
    "dark": "dark", "uplifting": "uplifting", "aggressive": "aggressive",
    "party": "party", "summer": "summer", "warm": "warm", "sensual": "sensual",
    "bright": "bright", "moody": "moody", "anthemic": "anthemic",
}
DESCRIPTIVE_TAGS = {"instrumental", "acoustic", "piano", "guitar", "vocal", "vocals", "dance"}
# Ordinal estimates from explicit labels. Genre and tempo are never substitutes.
ENERGY_LABELS = {"high energy": 5, "energetic": 5, "upbeat": 4,
                 "low energy": 1, "calm": 1, "relaxing": 1, "relaxed": 1,
                 "mellow": 2, "chill": 2, "chilled": 2}
LANGUAGES = {
    "en": "english", "es": "spanish", "pt": "portuguese", "fr": "french",
    "de": "german", "it": "italian", "ja": "japanese", "ko": "korean",
    "zh": "chinese", "ar": "arabic", "hi": "hindi", "ru": "russian",
    "nl": "dutch", "sv": "swedish", "fi": "finnish", "no": "norwegian",
    "da": "danish", "pl": "polish", "tr": "turkish", "el": "greek",
    "ca": "catalan", "he": "hebrew", "uk": "ukrainian", "ro": "romanian",
    "th": "thai", "vi": "vietnamese", "id": "indonesian", "yo": "yoruba",
    "sw": "swahili", "am": "amharic", "te": "telugu", "ta": "tamil",
    "la": "latin", "cs": "czech", "et": "estonian", "sr": "serbian",
    "bg": "bulgarian", "ee": "ewe", "ms": "malay", "hy": "armenian",
    "az": "azerbaijani", "tw": "twi", "eu": "basque", "ty": "tahitian",
    "tl": "tagalog", "my": "burmese", "gu": "gujarati", "lt": "lithuanian",
    "uz": "uzbek", "ka": "georgian", "ha": "hausa", "sk": "slovak", "bm": "bambara",
    "eng": "english", "spa": "spanish", "por": "portuguese", "fra": "french",
    "fre": "french", "deu": "german", "ger": "german", "ita": "italian",
    "jpn": "japanese", "kor": "korean", "zho": "chinese", "chi": "chinese",
    "ara": "arabic", "hin": "hindi", "rus": "russian", "nld": "dutch",
    "dut": "dutch", "swe": "swedish", "fin": "finnish", "nor": "norwegian",
    "dan": "danish", "pol": "polish", "tur": "turkish", "ell": "greek",
    "cat": "catalan", "heb": "hebrew", "ukr": "ukrainian", "ron": "romanian",
    "tha": "thai", "vie": "vietnamese", "ind": "indonesian", "yor": "yoruba",
    "swa": "swahili", "amh": "amharic", "tel": "telugu", "tam": "tamil",
    "lat": "latin", "ces": "czech", "est": "estonian", "zxx": "instrumental",
}
SUPPORTED_TAGS = set(MOOD_ALIASES.values()) | DESCRIPTIVE_TAGS
LANGUAGE_NAMES = set(LANGUAGES.values())


def normalized_tag(value: str) -> str:
    return " ".join(value.casefold().replace("-", " ").split())


def recording_metadata(recording: dict[str, Any]) -> dict[str, Any]:
    """Extract evidence from an actual MusicBrainz recording lookup response."""
    recording_id = recording["id"]
    works = []
    for relation in recording.get("relations", []):
        if relation.get("type") != "performance" or relation.get("target-type") != "work":
            continue
        work = relation.get("work", {})
        codes = work.get("languages") or ([work["language"]] if work.get("language") else [])
        if not work.get("id"):
            continue
        works.append({"work_id": work["id"], "languages": codes,
                      "attributes": relation.get("attributes", []),
                      "source_url": "https://musicbrainz.org/work/" + work["id"]})
    return {"recording_mbid": recording_id,
            "recording_url": "https://musicbrainz.org/recording/" + recording_id,
            "source_tags": recording.get("tags", []), "works": works}


def enrich_track(track: dict[str, Any], evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a copy with sourced traits and explicit unknowns; never mutate input."""
    result = dict(track)
    evidence = evidence or {}
    quality = dict(track.get("metadata_quality", {}))
    provenance = dict(track.get("metadata_evidence", {}))
    result.update(language="unknown", languages=[], tags=[], energy=None)
    quality.update(language="unknown", mood="unknown", energy="unknown")
    for field in ("language", "mood", "energy", "tags"):
        provenance.pop(field, None)
    # Legacy baseline traits came from GENRE_PROFILES, not per-recording evidence.
    if not track.get("source"):
        quality["legacy_traits"] = "withdrawn-unsupported-genre-inference"
        result["subgenres"] = [g for g in track.get("subgenres", []) if g != "instrumental"]
    use_recording_tags = bool(evidence.get("recording_mbid")) and not evidence.get("match_method")
    raw_tags = evidence.get("source_tags", track.get("source_tags", [])) if use_recording_tags else track.get("source_tags", [])
    source_url = evidence.get("recording_url") or track.get("source_url")
    tag_url = evidence.get("recording_url") if use_recording_tags else track.get("source_url")
    tag_source = "musicbrainz-recording-community-tags" if track.get("source") == "musicbrainz" or use_recording_tags else "fma-track-tags"
    can_use_tags = track.get("source") in {"musicbrainz", "fma"} or use_recording_tags
    tag_votes = {}
    if can_use_tags:
        for item in raw_tags:
            if isinstance(item, str) and track.get("source") == "fma":
                tag_votes[normalized_tag(item)] = 1
                continue
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                continue
            # Do not revive tags the community has voted down.
            count = item.get("count", 0)
            if not isinstance(count, (int, float)) or count <= 0:
                continue
            tag = normalized_tag(item["name"])
            tag_votes[tag] = max(tag_votes.get(tag, 0), count)
    mapped = {MOOD_ALIASES.get(tag, tag) for tag in tag_votes if tag in MOOD_ALIASES or tag in DESCRIPTIVE_TAGS}
    result["tags"] = sorted(mapped)
    tag_evidence = [{"tag": tag, "votes": votes} for tag, votes in sorted(tag_votes.items())
                    if tag in MOOD_ALIASES or tag in DESCRIPTIVE_TAGS]
    if tag_evidence:
        if tag_source == "fma-track-tags":
            tag_evidence = [{"tag": item["tag"]} for item in tag_evidence]
        provenance["tags"] = {"source": tag_source, "source_url": tag_url,
                              "labels": tag_evidence}
    if any(tag in MOOD_ALIASES for tag in tag_votes):
        quality["mood"] = "community-tag"
        provenance["mood"] = provenance["tags"]
    energy_labels = {tag: ENERGY_LABELS[tag] for tag in tag_votes if tag in ENERGY_LABELS}
    if energy_labels:
        values = list(energy_labels.values())
        if max(values) - min(values) <= 1:
            result["energy"] = round(sum(values) / len(values))
            quality["energy"] = "community-label-estimate"
        else:
            quality["energy"] = "conflicting-community-labels"
        provenance["energy"] = {"source": tag_source,
                                "source_url": tag_url, "labels": energy_labels,
                                "scale": "ordinal 1–5; label-derived, not audio-measured"}
    # FMA's published Echo Nest analysis contains a documented numeric energy
    # feature. Keep the original value and distinguish it from community labels.
    audio_features = evidence.get("audio_features", track.get("audio_features", {}))
    measured_energy = audio_features.get("energy") if isinstance(audio_features, dict) else None
    if (track.get("source") == "fma" and isinstance(audio_features, dict) and audio_features.get("source") == "echonest"
            and isinstance(measured_energy, (int, float)) and not isinstance(measured_energy, bool)
            and 0 <= measured_energy <= 1):
        result["energy"] = 1 + 4 * measured_energy
        quality["energy"] = "audio-analysis"
        provenance["energy"] = {"source": "fma-echonest-audio-analysis", "raw_value": measured_energy,
                                "raw_scale": "0–1", "scale": "1 + 4 * raw_value",
                                "source_url": audio_features.get("source_url", "https://github.com/mdeff/fma")}
    languages = set()
    language_evidence = []
    # FMA's track.language_code is explicitly per-track, never artist location.
    fma_code = track.get("language_code")
    if track.get("source") == "fma" and track.get("instrumental") is True:
        languages.add("instrumental")
        quality["language"] = "fma-track-instrumental"
        provenance["language"] = {"source": "fma-track-instrumental", "source_url": source_url,
                                  "field": "track_instrumental", "raw_value": True}
    elif track.get("source") == "fma" and fma_code in LANGUAGES:
        languages.add(LANGUAGES[fma_code])
        quality["language"] = "fma-track-language"
        provenance["language"] = {"source": "fma-track-language-code", "source_url": source_url,
                                  "field": "track.language_code", "raw_value": fma_code}
    for work in evidence.get("works", []):
        attributes = {normalized_tag(str(a)) for a in work.get("attributes", [])}
        if attributes & {"translated", "translated lyrics", "karaoke", "medley"}:
            continue
        codes = ["zxx"] if "instrumental" in attributes else work.get("languages", [])
        known = {LANGUAGES[code] for code in codes if code in LANGUAGES}
        if known:
            languages.update(known)
            language_evidence.append(work)
    if languages:
        result["languages"] = sorted(languages)
        result["language"] = next(iter(languages)) if len(languages) == 1 else "multilingual"
        if language_evidence:
            quality["language"] = "musicbrainz-work-language"
            provenance["language"] = {"source": "musicbrainz-recording-work-relationship",
                                      "source_url": source_url, "works": language_evidence}
            if evidence.get("match_method"):
                provenance["language"]["match_method"] = evidence["match_method"]
        if languages == {"instrumental"}:
            result["tags"] = sorted(set(result["tags"]) | {"instrumental"})
    result["metadata_quality"] = quality
    result["metadata_evidence"] = provenance
    return result


def apply_metadata(catalog: list[dict[str, Any]], evidence_by_id: dict | None = None) -> list[dict[str, Any]]:
    if evidence_by_id is None:
        evidence_by_id = json.loads(DATA_PATH.read_text()) if DATA_PATH.is_file() else {}
    return [enrich_track(track, evidence_by_id.get(track["id"])) for track in catalog]


def metadata_coverage(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    return {"tracks": len(catalog), "language_known": sum(bool(t.get("languages")) for t in catalog),
            "mood_labelled": sum(t.get("metadata_quality", {}).get("mood") == "community-tag" for t in catalog),
            "energy_labelled_estimate": sum(t.get("metadata_quality", {}).get("energy") == "community-label-estimate" for t in catalog),
            "audio_measured_energy": sum(t.get("metadata_quality", {}).get("energy") == "audio-analysis" for t in catalog),
            "languages": dict(Counter(lang for t in catalog for lang in t.get("languages", [])))}
