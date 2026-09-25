"""Reproduce a historical Taste Profile ranking by distinct listeners.

The source has one user/song/play-count row. Count a user only once per song,
even if a repeated pair appears, and fail if a user reappears out of order.
Only aggregate counts and track metadata are written; user identifiers stay
in the original research source and are never included in the output.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Iterable
import zipfile


SOURCE_URL = "http://millionsongdataset.com/tasteprofile/"
SOURCE_SCOPE = (
    "Historical Echo Nest Taste Profile research cohort, released in 2011. "
    "The source does not supply listen timestamps or an observation window; "
    "these are not current worldwide listener counts."
)
EXPECTED_SOURCE = {"rows": 48_373_586, "users": 1_019_318, "songs": 384_546}
ID_PAIR = re.compile(r"<(SO[A-Z0-9]{16}) (TR[A-Z0-9]{16})>")


def aggregate_triplets(lines: Iterable[bytes], progress_every: int = 0):
    """Count distinct users per song with bounded per-user deduplication."""
    listeners: Counter[bytes] = Counter()
    plays: Counter[bytes] = Counter()
    seen_users: set[bytes] = set()
    current_user = None
    current_songs: set[bytes] = set()
    row_count = duplicate_pairs = 0
    for row_count, line in enumerate(lines, 1):
        parts = line.rstrip(b"\r\n").split(b"\t")
        if len(parts) != 3:
            raise ValueError(f"Invalid triplet columns at row {row_count}")
        user, song, count_text = parts
        if len(song) != 18 or not song.startswith(b"SO") or not song.isalnum():
            raise ValueError(f"Invalid song ID at row {row_count}")
        try:
            count = int(count_text)
        except ValueError as exc:
            raise ValueError(f"Invalid play count at row {row_count}") from exc
        if count <= 0:
            raise ValueError(f"Non-positive play count at row {row_count}")
        if user != current_user:
            if len(user) != 40 or any(c not in b"0123456789abcdef" for c in user):
                raise ValueError(f"Invalid user ID at row {row_count}")
            if user in seen_users:
                raise ValueError(f"Noncontiguous user group at row {row_count}")
            seen_users.add(user)
            current_user = user
            current_songs = set()
        if song not in current_songs:
            listeners[song] += 1
            current_songs.add(song)
        else:
            duplicate_pairs += 1
        plays[song] += count
        if progress_every and row_count % progress_every == 0:
            print(json.dumps({"rows_read": row_count, "users": len(seen_users),
                              "songs": len(listeners)}), flush=True)
    summary = {"rows": row_count, "users": len(seen_users), "songs": len(listeners),
               "duplicate_user_song_rows": duplicate_pairs,
               "unique_user_song_pairs": sum(listeners.values()),
               "total_play_count": sum(plays.values())}
    return ({key.decode("ascii"): value for key, value in listeners.items()},
            {key.decode("ascii"): value for key, value in plays.items()}, summary)


def load_pair_file(path: Path) -> set[tuple[str, str]]:
    """Both the final error list and accepted-pair diff contain <SO... TR...>."""
    return set(ID_PAIR.findall(path.read_text(encoding="utf-8")))


def load_metadata(source_dir: Path, song_ids: set[str]):
    final_mismatches = load_pair_file(source_dir / "sid_mismatches.txt")
    accepted_pairs = load_pair_file(source_dir / "sid_matches_manually_accepted.txt")
    excluded_pairs = final_mismatches - accepted_pairs
    candidates: dict[str, list[dict]] = {}
    invalid_pairs = empty_metadata = 0
    with (source_dir / "unique_tracks.txt").open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            row = line.rstrip("\r\n").split("<SEP>")
            if len(row) != 4:
                raise ValueError(f"Invalid track metadata at row {line_number}")
            track_id, song_id, artist, title = row
            if song_id not in song_ids:
                continue
            if (song_id, track_id) in excluded_pairs:
                invalid_pairs += 1
                continue
            if not artist.strip() or not title.strip():
                empty_metadata += 1
                continue
            candidates.setdefault(song_id, []).append({
                "track_id": track_id, "artist": artist, "title": title, "year": None,
            })
    by_track = {track["track_id"]: track for group in candidates.values() for track in group}
    with (source_dir / "tracks_per_year.txt").open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            row = line.rstrip("\r\n").split("<SEP>")
            if len(row) != 4:
                raise ValueError(f"Invalid year metadata at row {line_number}")
            year, track_id, _artist, _title = row
            if track_id in by_track:
                by_track[track_id]["year"] = int(year)
    for group in candidates.values():
        group.sort(key=lambda item: item["track_id"])
    metadata_summary = {
        "documented_mismatch_pairs": len(final_mismatches),
        "documented_accepted_pairs": len(accepted_pairs),
        "accepted_pairs_overriding_mismatch_list": len(final_mismatches & accepted_pairs),
        "excluded_source_track_mappings": invalid_pairs,
        "excluded_empty_metadata_mappings": empty_metadata,
        "songs_with_valid_metadata": len(candidates),
        "songs_without_valid_metadata": len(song_ids - candidates.keys()),
    }
    return candidates, metadata_summary


def build_ranking(listeners: dict[str, int], plays: dict[str, int], candidates: dict, limit: int):
    if limit < 1:
        raise ValueError("Ranking limit must be positive")
    raw_order = sorted(listeners, key=lambda song: (-listeners[song], song))
    eligible = [song for song in raw_order if candidates.get(song)]
    if len(eligible) < limit:
        raise ValueError(f"Only {len(eligible)} eligible songs; {limit} required")
    raw_top_excluded = [song for song in raw_order[:limit] if not candidates.get(song)]
    raw_ranks = {song: rank for rank, song in enumerate(raw_order, 1)}
    tracks = []
    for rank, song in enumerate(eligible[:limit], 1):
        group = candidates[song]
        representative = group[0]
        tracks.append({
            "song_id": song, "rank": rank, "raw_listener_rank": raw_ranks[song],
            "listener_count": listeners[song], "play_count": plays[song],
            "artist": representative["artist"], "title": representative["title"],
            "year": representative.get("year"), "candidate_tracks": group,
            "source": "echo_nest_taste_profile", "source_url": SOURCE_URL,
        })
    summary = {
        "requested_songs": limit, "eligible_songs": len(eligible), "selected_songs": len(tracks),
        "sort_order": "listener_count descending; song_id ascending for ties",
        "listener_threshold": tracks[-1]["listener_count"],
        "raw_rank_of_last_selected": tracks[-1]["raw_listener_rank"],
        "raw_top_requested_without_valid_metadata_count": len(raw_top_excluded),
        "raw_top_requested_without_valid_metadata": [
            {"song_id": song, "raw_rank": raw_ranks[song], "listener_count": listeners[song]}
            for song in raw_top_excluded
        ],
    }
    return tracks, summary, raw_order


def file_evidence(path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return {"filename": path.name, "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="." + path.name + ".", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=Path(__file__).resolve().parents[1] /
                        "data/source-cache/taste-profile")
    parser.add_argument("--limit", type=int, default=20_000)
    parser.add_argument("--allow-different-source-counts", action="store_true",
                        help="For deliberately selected research source variants; keep provenance explicit.")
    args = parser.parse_args()
    source_dir = args.source_dir
    source_zip = source_dir / "train_triplets.txt.zip"
    with zipfile.ZipFile(source_zip) as archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
        if len(members) != 1 or members[0].filename != "train_triplets.txt":
            raise ValueError("Expected a single train_triplets.txt ZIP member")
        member = members[0]
        with archive.open(member) as source:
            listeners, plays, aggregate_summary = aggregate_triplets(source, progress_every=5_000_000)
    if not args.allow_different_source_counts:
        for field, expected in EXPECTED_SOURCE.items():
            if aggregate_summary[field] != expected:
                raise ValueError(f"Source {field}: expected {expected}, got {aggregate_summary[field]}")
    candidates, metadata_summary = load_metadata(source_dir, set(listeners))
    tracks, rank_summary, raw_order = build_ranking(listeners, plays, candidates, args.limit)
    filenames = ["train_triplets.txt.zip", "unique_tracks.txt", "tracks_per_year.txt",
                 "sid_mismatches.txt", "sid_matches_manually_accepted.txt", "MSD-LICENSE.txt"]
    report = {
        "schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "echo_nest_taste_profile", "source_url": SOURCE_URL, "scope": SOURCE_SCOPE,
        "metric": "Unique anonymous users per song in the historical source cohort",
        "license": "Echo Nest API non-commercial research terms; see MSD-LICENSE.txt",
        "aggregate": aggregate_summary, "metadata": metadata_summary, "ranking": rank_summary,
        "zip_member": {"filename": member.filename, "uncompressed_bytes": member.file_size,
                       "crc32": f"{member.CRC:08x}", "crc_verified_by_complete_read": True},
        "source_files": [file_evidence(source_dir / name) for name in filenames],
        "songs": tracks,
    }
    all_counts = {
        "schema_version": 1, "source": report["source"], "scope": SOURCE_SCOPE,
        "aggregate": aggregate_summary,
        "songs": [{"song_id": song, "raw_listener_rank": rank,
                   "listener_count": listeners[song], "play_count": plays[song],
                   "metadata_eligible": bool(candidates.get(song)),
                   "candidate_track_ids": [item["track_id"] for item in candidates.get(song, [])]}
                  for rank, song in enumerate(raw_order, 1)],
    }
    atomic_json(source_dir / "listener-counts.json", all_counts)
    atomic_json(source_dir / "listener-ranking.json", report)
    print(json.dumps({"aggregate": aggregate_summary, "metadata": metadata_summary,
                      "ranking": {key: value for key, value in rank_summary.items()
                                  if key != "raw_top_requested_without_valid_metadata"},
                      "output": str(source_dir / "listener-ranking.json")}, indent=2))


if __name__ == "__main__":
    main()
