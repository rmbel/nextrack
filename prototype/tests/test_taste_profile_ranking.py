import json
from pathlib import Path
import tempfile
import unittest

from prototype.scripts.rank_taste_profile import (
    aggregate_triplets, atomic_json, build_ranking, load_metadata,
)


def song(number):
    return "SO" + str(number).zfill(16)


def track(number):
    return "TR" + str(number).zfill(16)


def row(user, item, plays=1):
    return f"{user:040x}\t{song(item)}\t{plays}\n".encode()


class TasteProfileRankingTests(unittest.TestCase):
    def test_distinct_listeners_are_not_plays_or_repeated_rows(self):
        listeners, plays, summary = aggregate_triplets([
            row(1, 1, 500), row(1, 1, 2), row(1, 2), row(2, 2), row(3, 2),
        ])
        self.assertEqual(listeners, {song(1): 1, song(2): 3})
        self.assertEqual(plays, {song(1): 502, song(2): 3})
        self.assertEqual(summary["duplicate_user_song_rows"], 1)
        self.assertEqual(summary["unique_user_song_pairs"], 4)
        self.assertEqual(summary["users"], 3)

    def test_noncontiguous_users_fail_instead_of_double_counting(self):
        with self.assertRaisesRegex(ValueError, "Noncontiguous user"):
            aggregate_triplets([row(1, 1), row(2, 1), row(1, 2)])

    def test_malformed_and_nonpositive_counts_fail(self):
        for value in [row(1, 1, 0), row(1, 1, -3), b"bad\trow\n", b"x\t" + song(1).encode() + b"\t1\n"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                aggregate_triplets([value])

    def test_rank_uses_listener_count_and_stable_id_ties_with_complete_size(self):
        listeners = {song(1): 2, song(2): 3, song(3): 3, song(4): 10}
        plays = {song(1): 9999, song(2): 4, song(3): 7, song(4): 20}
        candidates = {song(i): [{"track_id": track(i), "artist": "Artist", "title": str(i)}]
                      for i in [1, 2, 3]}
        ranked, summary, _ = build_ranking(listeners, plays, candidates, 2)
        self.assertEqual([item["song_id"] for item in ranked], [song(2), song(3)])
        self.assertEqual([item["rank"] for item in ranked], [1, 2])
        self.assertEqual(summary["raw_top_requested_without_valid_metadata_count"], 1)
        self.assertEqual(summary["raw_rank_of_last_selected"], 3)
        with self.assertRaisesRegex(ValueError, "eligible songs"):
            build_ranking(listeners, plays, candidates, 4)

    def test_invalid_mapping_is_excluded_but_other_mapping_and_accepted_pair_survive(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "sid_mismatches.txt").write_text(
                f"ERROR: <{song(1)} {track(1)}> Wrong != Right\n"
                f"ERROR: <{song(2)} {track(3)}> Match != Accepted\n")
            (root / "sid_matches_manually_accepted.txt").write_text(
                f"4d2\n< ERROR: <{song(2)} {track(3)}> Match != Accepted\n")
            (root / "unique_tracks.txt").write_text(
                f"{track(1)}<SEP>{song(1)}<SEP>Wrong<SEP>Wrong\n"
                f"{track(2)}<SEP>{song(1)}<SEP>Artist<SEP>Title\n"
                f"{track(3)}<SEP>{song(2)}<SEP>Accepted<SEP>Title\n")
            (root / "tracks_per_year.txt").write_text(f"2001<SEP>{track(2)}<SEP>Artist<SEP>Title\n")
            candidates, summary = load_metadata(root, {song(1), song(2), song(3)})
            self.assertEqual([item["track_id"] for item in candidates[song(1)]], [track(2)])
            self.assertEqual(candidates[song(1)][0]["year"], 2001)
            self.assertIsNone(candidates[song(2)][0]["year"])
            self.assertEqual(summary["accepted_pairs_overriding_mismatch_list"], 1)
            self.assertEqual(summary["songs_without_valid_metadata"], 1)

    def test_failed_serialization_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ranking.json"
            atomic_json(path, {"version": "original"})
            with self.assertRaises(TypeError):
                atomic_json(path, {"invalid": set()})
            self.assertEqual(json.loads(path.read_text()), {"version": "original"})
            self.assertEqual(list(Path(folder).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
