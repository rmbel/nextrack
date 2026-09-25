"""Compare raw and compact catalogue behavior in isolated, fixed-hash processes.

This performs offline ranking only, never GPT requests or feedback writes.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

WORKSPACE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKSPACE))


def digest(value):
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def peak_rss_bytes():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == "darwin" else value * 1024


def probe():
    started = time.perf_counter()
    from prototype.app.catalog import (CATALOG, CATALOG_ALIASES, CATALOG_QUALITY, TRACK_BY_ID,
                                       TRACK_ARTISTS, TRACK_ARTIST_NAMES, TRACK_VECTORS,
                                       TRACK_VECTOR_NORMS, POPULARITY_SCORES, search_catalog)
    from prototype.app.models import RecommendRequest
    from prototype.app.recommender import generate_recommendations
    imported_seconds, import_peak = time.perf_counter() - started, peak_rss_bytes()
    aliases = {alias: canonical for alias, canonical in CATALOG_ALIASES.items()
               if alias != canonical and canonical in TRACK_BY_ID}
    assert all(TRACK_BY_ID[alias] is TRACK_BY_ID[canonical] for alias, canonical in aliases.items())
    cases = [
        {"name": "bachata", "seed_track_ids": ["romeo-propuesta-indecente", "romeo-eres-mia"],
         "prompt": "recent bachata songs", "limit": 5},
        {"name": "pop", "seed_track_ids": ["dua-lipa-levitating"], "prompt": "", "limit": 10},
        {"name": "rock", "seed_track_ids": ["coldplay-viva-la-vida"], "prompt": "rock", "limit": 5},
        {"name": "fma", "seed_track_ids": ["fma:22295"], "prompt": "", "limit": 5},
        {"name": "alias", "seed_track_ids": [sorted(aliases)[0]], "prompt": "", "limit": 5},
    ]
    recommendations = []
    for case in cases:
        request = {key: value for key, value in case.items() if key != "name"}
        result = generate_recommendations(RecommendRequest(**request))
        recommendations.append({"case": case, "ids": [track.id for track in result.recommendations],
                                "scores": [track.score for track in result.recommendations],
                                "metrics": result.metrics.model_dump()})
    behavior = {
        "record_count": len(CATALOG), "quality": CATALOG_QUALITY,
        "canonical_order_sha256": digest([track["id"] for track in CATALOG]),
        "vectors_sha256": digest(TRACK_VECTORS), "vector_norms_sha256": digest(TRACK_VECTOR_NORMS),
        "artists_sha256": digest({key: sorted(value) for key, value in TRACK_ARTISTS.items()}),
        "artist_names_sha256": digest({key: sorted(value) for key, value in TRACK_ARTIST_NAMES.items()}),
        "popularity_sha256": digest(POPULARITY_SCORES), "nonidentity_aliases": aliases,
        "listener_popularity_sha256": digest({track['id']: track.get('listener_popularity') for track in CATALOG}),
        "searches": {query: [track["id"] for track in search_catalog(query)]
                     for query in ("La Bachata", "Beyoncé", "怪獣の花唄", "Queen", "jazz")},
        "recommendations": recommendations,
    }
    return {"behavior": behavior, "import_seconds": round(imported_seconds, 3),
            "import_peak_rss_bytes": import_peak, "total_peak_rss_bytes": peak_rss_bytes()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--output", type=Path, default=WORKSPACE / "development/runtime-catalog-equivalence-2026-09-17.json")
    args = parser.parse_args()
    if args.probe:
        print(json.dumps(probe(), ensure_ascii=False))
        return
    runs = {}
    for mode in ("raw", "compact"):
        environment = {**os.environ, "PYTHONHASHSEED": "0", "NEXTTRACK_CATALOG_MODE": "active"}
        environment.pop("NEXTTRACK_CATALOG_SNAPSHOT", None)
        if mode == "compact":
            environment["NEXTTRACK_CATALOG_SNAPSHOT"] = "deployment/data/catalog-runtime.json.gz"
        completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--probe"],
                                   cwd=WORKSPACE, env=environment, text=True, capture_output=True,
                                   check=True, timeout=120)
        runs[mode] = json.loads(completed.stdout)
    equivalent = runs["raw"]["behavior"] == runs["compact"]["behavior"]
    report = {"verified_at": datetime.now(timezone.utc).isoformat(), "equivalent": equivalent,
              "python": sys.version, "pythonhashseed": "0", "platform": sys.platform,
              "scope": "Local offline catalogue/ranking equivalence, not live GPT or Vercel performance",
              "runs": runs}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"equivalent": equivalent, "output": str(args.output),
                      "performance": {mode: {key: value for key, value in result.items() if key != "behavior"}
                                      for mode, result in runs.items()}}, indent=2))
    if not equivalent:
        raise SystemExit("Runtime snapshot changed catalogue behavior; inspect comparison artifact")


if __name__ == "__main__":
    main()
