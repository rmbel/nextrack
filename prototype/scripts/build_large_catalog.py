from __future__ import annotations

import json
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
CATALOG_PATH = BASE_DIR / "data" / "catalog.json"
TARGET_COUNT = 1000
TIMEOUT = 30

APPLE_CHART_COUNTRIES = [
    "us",
    "gb",
    "ca",
    "au",
    "ie",
    "nz",
    "mx",
    "es",
    "ar",
    "co",
    "cl",
    "br",
    "fr",
    "de",
    "it",
    "nl",
    "se",
    "jp",
    "kr",
    "in",
]

SEARCH_TERMS = [
    "bachata",
    "reggaeton",
    "latin pop",
    "salsa",
    "merengue",
    "pop hits",
    "dance hits",
    "indie songs",
    "rock songs",
    "hip hop hits",
    "r&b songs",
    "country songs",
    "afrobeats",
    "k-pop",
    "j-pop",
    "jazz standards",
    "classical essentials",
    "summer songs",
    "party songs",
    "chill songs",
    "Romeo Santos",
    "Prince Royce",
    "Aventura",
    "Shakira",
    "Bad Bunny",
    "Taylor Swift",
    "The Weeknd",
    "Dua Lipa",
    "Drake",
    "Coldplay",
]

GENRE_PROFILES: list[tuple[list[str], str, list[str], list[str], int, str | None]] = [
    (["bachata"], "bachata", ["latin", "tropical"], ["romantic", "smooth", "dance"], 3, "spanish"),
    (["reggaeton", "latin urban", "urbano latino"], "reggaeton", ["latin", "dance"], ["dance", "party", "energetic"], 5, "spanish"),
    (
        [
            "latin pop",
            "pop latino",
            "musica latina",
            "latin",
            "regional mexican",
            "corridos",
            "musica mexicana",
            "musica",
        ],
        "latin",
        ["latin pop"],
        ["catchy", "mainstream", "dance"],
        4,
        "spanish",
    ),
    (["salsa"], "salsa", ["latin", "tropical"], ["dance", "warm", "energetic"], 4, "spanish"),
    (["merengue"], "merengue", ["latin", "tropical"], ["dance", "party", "energetic"], 5, "spanish"),
    (["indie pop"], "indie pop", ["indie", "dream pop"], ["chill", "warm", "soft"], 2, "english"),
    (["indie rock"], "indie rock", ["indie", "alternative"], ["moody", "guitar", "night"], 3, "english"),
    (["indie"], "indie", ["alternative"], ["chill", "dreamy", "soft"], 2, "english"),
    (["alternativa", "alternativo", "alternative"], "rock", ["alternative"], ["moody", "guitar", "night"], 3, "english"),
    (["baile funk"], "dance", ["brazilian funk", "latin"], ["dance", "party", "energetic"], 5, "portuguese"),
    (["sertanejo"], "country", ["brazilian", "sertanejo"], ["warm", "storytelling", "soft"], 2, "portuguese"),
    (["dance", "electronic", "edm", "house", "disco"], "dance", ["electronic", "pop"], ["dance", "party", "energetic"], 5, "english"),
    (["hip-hop", "hip hop", "rap"], "hip hop", ["rap", "urban"], ["energetic", "night", "confident"], 4, "english"),
    (["r b", "soul"], "r&b", ["soul", "pop"], ["smooth", "night", "romantic"], 2, "english"),
    (["country"], "country", ["folk", "pop"], ["warm", "storytelling", "soft"], 2, "english"),
    (["afrobeats", "afrobeat", "afro-fusion"], "afrobeats", ["dance", "pop"], ["summer", "dance", "bright"], 4, "english"),
    (["k-pop"], "k-pop", ["pop", "dance"], ["energetic", "bright", "mainstream"], 5, "korean"),
    (["j-pop"], "j-pop", ["pop", "dance"], ["bright", "mainstream", "energetic"], 4, "japanese"),
    (["jazz"], "jazz", ["instrumental"], ["smooth", "warm", "night"], 1, "english"),
    (["classical", "orchestral"], "classical", ["instrumental"], ["soft", "calm", "melodic"], 1, None),
    (["variete francaise", "musique", "chanson francaise"], "pop", ["french pop"], ["mainstream", "smooth", "night"], 3, "french"),
    (["musik", "muziek", "music", "barnmusik"], "pop", ["mainstream"], ["catchy", "bright", "mainstream"], 4, "english"),
    (["musica religiosa", "gospel", "christian"], "pop", ["inspirational"], ["calm", "soft", "uplifting"], 2, "english"),
    (["singer songwriter"], "indie pop", ["acoustic"], ["soft", "warm", "storytelling"], 2, "english"),
    (["rock", "alternative"], "rock", ["alternative"], ["energetic", "guitar", "anthemic"], 4, "english"),
    (["pop"], "pop", ["mainstream"], ["catchy", "bright", "mainstream"], 4, "english"),
]

GENERIC_SUBGENRES = {
    "music",
    "musica",
    "musique",
    "musik",
    "muziek",
    "musica latina",
    "barnmusik",
}


def fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "NextTrackCatalogBuilder/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.load(response)


def slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")


def dedupe_key(title: str, artist: str) -> str:
    return f"{slugify(artist)}::{slugify(title)}"


def normalize_genre_label(text: str) -> str:
    return slugify(text).replace("-", " ")


def infer_from_genres(genre_names: list[str]) -> tuple[str, list[str], list[str], int, str | None]:
    normalized_genres = [normalize_genre_label(name) for name in genre_names if name]
    joined = " ".join(normalized_genres)
    for keywords, genre, subgenres, tags, energy, language in GENRE_PROFILES:
        if any(keyword in joined for keyword in keywords):
            return genre, list(dict.fromkeys(subgenres)), list(dict.fromkeys(tags)), energy, language

    fallback = normalized_genres[0] if normalized_genres else "pop"
    return fallback or "pop", [], ["mainstream"], 3, None


def infer_language(genre_names: list[str], fallback: str | None) -> str:
    if fallback:
        return fallback
    normalized_genres = [normalize_genre_label(name) for name in genre_names if name]
    joined = " ".join(normalized_genres)
    if any(word in joined for word in ["latin", "bachata", "reggaeton", "salsa", "merengue", "regional mexican", "musica"]):
        return "spanish"
    if any(word in joined for word in ["french", "francaise", "musique"]):
        return "french"
    if any(word in joined for word in ["brazilian", "mpb", "sertanejo", "baile funk"]):
        return "portuguese"
    return "english"


def add_year_tags(tags: list[str], year: int) -> list[str]:
    extra = list(tags)
    if year >= 2020:
        extra.append("recent")
    elif year <= 2010:
        extra.append("classic")
    else:
        extra.append("modern")
    return list(dict.fromkeys(extra))


def build_track(title: str, artist: str, year: int, genre_names: list[str]) -> dict[str, Any]:
    genre, subgenres, tags, energy, language_hint = infer_from_genres(genre_names)
    language = infer_language(genre_names, language_hint)
    tags = add_year_tags(tags, year)

    extra_subgenres = []
    for name in genre_names:
        cleaned = normalize_genre_label(name)
        if cleaned and cleaned != genre and cleaned not in subgenres and cleaned not in GENERIC_SUBGENRES:
            extra_subgenres.append(cleaned)

    return {
        "id": f"{slugify(artist)}-{slugify(title)}"[:100],
        "title": title.strip(),
        "artist": artist.strip(),
        "year": year,
        "genre": genre,
        "subgenres": list(dict.fromkeys([*subgenres, *extra_subgenres]))[:4],
        "tags": tags[:5],
        "language": language,
        "energy": max(1, min(5, energy)),
    }


def apple_chart_tracks() -> list[dict[str, Any]]:
    tracks: list[dict[str, Any]] = []
    for country in APPLE_CHART_COUNTRIES:
        url = f"https://rss.applemarketingtools.com/api/v2/{country}/music/most-played/100/songs.json"
        payload = fetch_json(url)
        for entry in payload.get("feed", {}).get("results", []):
            genre_names = [genre["name"] for genre in entry.get("genres", []) if genre.get("name")]
            year = int(entry.get("releaseDate", "2000")[:4])
            tracks.append(
                build_track(
                    title=entry["name"],
                    artist=entry["artistName"],
                    year=year,
                    genre_names=genre_names,
                )
            )
    return tracks


def itunes_search_tracks() -> list[dict[str, Any]]:
    tracks: list[dict[str, Any]] = []
    for term in SEARCH_TERMS:
        query = urllib.parse.urlencode(
            {
                "term": term,
                "entity": "song",
                "limit": 200,
            }
        )
        url = f"https://itunes.apple.com/search?{query}"
        payload = fetch_json(url)
        for entry in payload.get("results", []):
            title = entry.get("trackName")
            artist = entry.get("artistName")
            release_date = entry.get("releaseDate", "")
            if not title or not artist or len(release_date) < 4:
                continue
            year = int(release_date[:4])
            genre_names = [entry.get("primaryGenreName", "pop")]
            tracks.append(build_track(title=title, artist=artist, year=year, genre_names=genre_names))
    return tracks


def main() -> None:
    existing_catalog = json.loads(CATALOG_PATH.read_text())
    merged = list(existing_catalog)
    seen = {dedupe_key(track["title"], track["artist"]) for track in existing_catalog}

    harvested: list[dict[str, Any]] = []
    if len(merged) < TARGET_COUNT:
        harvested.extend(apple_chart_tracks())
        if len(merged) + len(harvested) < TARGET_COUNT:
            harvested.extend(itunes_search_tracks())

    for track in harvested:
        if len(merged) >= TARGET_COUNT:
            break
        key = dedupe_key(track["title"], track["artist"])
        if key in seen:
            continue
        merged.append(track)
        seen.add(key)

    if len(merged) < TARGET_COUNT:
        raise RuntimeError(f"Only collected {len(merged)} tracks; target is {TARGET_COUNT}.")

    CATALOG_PATH.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {len(merged)} tracks to {CATALOG_PATH}")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as exc:
        raise SystemExit(f"Network error while building catalog: {exc}") from exc
