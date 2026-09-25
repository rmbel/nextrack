"""Listener-ranked additions expand the catalogue without replacing existing seeds."""
from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys
import unittest

from prototype.app.catalog import _extend_with_popular_tracks


def track(identifier, *, title="Fixture song", artist="Fixture artist", **extra):
    return {"id": identifier, "title": title, "artist": artist, "year": 2001,
            "genre": "rock", "language": "unknown", "energy": None, **extra}


def listeners(value=1000, rank=1, snapshot_date="2026-09-17"):
    return {"source": "mlhd-plus", "metric": "unique_listeners", "value": value,
            "rank": rank, "snapshot_date": snapshot_date,
            "source_url": "https://example.org/fixture-listener-snapshot"}


class PopularCatalogTests(unittest.TestCase):
    def setUp(self):
        self.base = [track("old", tags=["preserved"], language="english",
                           metadata_evidence={"language": {"source": "fixture"}})]
        self.aliases = {"old": "old", "old-alias": "old"}
        self.quality = {"raw_records": 2, "accepted_records": 1,
                        "duplicate_records": 1, "quarantined_records": 0}

    def merge(self, additions):
        return _extend_with_popular_tracks(self.base, self.aliases, self.quality, additions)

    def test_empty_additions_preserve_exact_existing_objects(self):
        tracks, aliases, quality = self.merge([])
        self.assertIs(tracks, self.base)
        self.assertIs(aliases, self.aliases)
        self.assertIs(quality, self.quality)

    def test_duplicate_keeps_old_id_metadata_aliases_and_listener_provenance(self):
        original = deepcopy((self.base, self.aliases, self.quality))
        addition = track("popular-1", language="spanish", tags=["invented"],
                         listener_popularity=listeners())
        tracks, aliases, quality = self.merge([addition])
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0], {**self.base[0], "listener_popularity": listeners()})
        self.assertEqual(aliases, {**self.aliases, "popular-1": "old"})
        self.assertEqual(quality["duplicate_records"], 2)
        self.assertEqual((self.base, self.aliases, self.quality), original)

    def test_duplicate_source_rows_do_not_sum_listeners_or_lose_best_rank(self):
        additions = [track("popular-1", title="New song", listener_popularity=listeners(700, 4)),
                     track("popular-2", title="New song", listener_popularity=listeners(900, 2))]
        tracks, aliases, quality = self.merge(additions)
        self.assertEqual([item["id"] for item in tracks], ["old", "popular-1"])
        self.assertEqual(tracks[-1]["listener_popularity"], listeners(900, 2))
        self.assertEqual(aliases["popular-2"], "popular-1")
        self.assertEqual(quality["duplicate_records"], 2)
        self.assertEqual(quality["accepted_records"], 2)

    def test_taste_profile_historical_dataset_provenance_survives_deduplication(self):
        provenance = {**listeners(), "source": "taste-profile", "dataset_release_year": 2011,
                      "source_period": "historical; listening dates not supplied",
                      "source_url": "http://millionsongdataset.com/tasteprofile/"}
        tracks, aliases, _ = self.merge([track("taste-profile:fixture", listener_popularity=provenance)])
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]["listener_popularity"], provenance)
        self.assertEqual(aliases["taste-profile:fixture"], "old")

    def test_newer_snapshot_wins_without_accumulating_or_comparing_counts_across_dates(self):
        self.base[0]["listener_popularity"] = listeners(9000, 1, "2026-08-01")
        tracks, _, _ = self.merge([track("popular-1", listener_popularity=listeners(500, 5))])
        self.assertEqual(tracks[0]["listener_popularity"], listeners(500, 5))
        tracks, _, _ = self.merge([track("popular-1", listener_popularity=listeners(12000, 1, "2026-07-01"))])
        self.assertEqual(tracks[0]["listener_popularity"], self.base[0]["listener_popularity"])

    def test_new_records_apply_metadata_without_changing_base_metadata(self):
        tracks, _, _ = self.merge([track("popular-1", title="New song", tags=["happy"],
                                        language="spanish", energy=5, listener_popularity=listeners())])
        self.assertEqual(tracks[0], self.base[0])
        self.assertEqual(tracks[1]["language"], "unknown")
        self.assertEqual(tracks[1]["tags"], [])
        self.assertIsNone(tracks[1]["energy"])
        self.assertEqual(tracks[1]["metadata_quality"]["language"], "unknown")

    def test_unknown_year_is_retained_but_zero_or_invalid_year_is_rejected(self):
        addition = track("popular-1", title="New song", year=None, listener_popularity=listeners())
        tracks, _, _ = self.merge([addition])
        self.assertIsNone(tracks[-1]["year"])
        for invalid in (0, -1, True, "2001"):
            with self.subTest(year=invalid), self.assertRaisesRegex(ValueError, "incomplete track"):
                self.merge([{**addition, "year": invalid}])

    def test_provider_and_existing_alias_matches_preserve_canonical_id(self):
        self.base[0]["provider_ids"] = {"musicbrainz": "fixture-recording"}
        for extra in ({"recording_mbid": "fixture-recording"}, {"id": "old-alias"}):
            identifier = extra.pop("id", "popular-1")
            addition = track(identifier, title="Different source spelling", artist="Alternate credit",
                             listener_popularity=listeners(), **extra)
            tracks, aliases, _ = self.merge([addition])
            self.assertEqual(len(tracks), 1)
            self.assertEqual(aliases[identifier], "old")

    def test_versions_and_distinct_full_artist_credits_are_not_merged(self):
        additions = [track("live", title="Fixture song (live)", listener_popularity=listeners()),
                     track("collab", artist="Fixture artist feat. Guest", listener_popularity=listeners())]
        tracks, _, _ = self.merge(additions)
        self.assertEqual([item["id"] for item in tracks], ["old", "live", "collab"])

    def test_ambiguous_provider_bridge_never_merges_two_existing_canonical_records(self):
        self.base[0]["provider_ids"] = {"musicbrainz": "recording-a"}
        self.base.append(track("old-b", title="Different song", provider_ids={"musicbrainz": "recording-b"}))
        self.aliases["old-b"] = "old-b"
        self.quality.update(raw_records=3, accepted_records=2)
        addition = track("ambiguous", title="Third source title", listener_popularity=listeners(),
                         provider_ids={"musicbrainz": ["recording-a", "recording-b"]})
        tracks, aliases, quality = self.merge([addition])
        self.assertEqual(tracks, self.base)
        self.assertEqual(aliases, self.aliases)
        self.assertEqual(quality["quarantined_records"], 1)
        self.assertEqual(quality["duplicate_records"], 1)

    def test_addition_to_fifty_thousand_base_is_not_capped_or_reordered(self):
        self.base = [track(f"old-{i}", title=f"Fixture song {i}") for i in range(50000)]
        self.aliases = {item["id"]: item["id"] for item in self.base}
        self.aliases["original-alias"] = "old-0"
        self.quality = {"raw_records": 50001, "accepted_records": 50000,
                        "duplicate_records": 1, "quarantined_records": 0}
        additions = [track("popular-1", title="Additional popular song", listener_popularity=listeners())]
        tracks, aliases, quality = self.merge(additions)
        self.assertEqual(len(tracks), 50001)
        self.assertEqual(tracks[:50000], self.base)
        self.assertEqual(tracks[-1]["id"], "popular-1")
        self.assertEqual(aliases["original-alias"], "old-0")
        self.assertEqual(quality["accepted_records"], 50001)

    def test_invalid_counts_and_missing_provenance_fail_explicitly(self):
        for changes in ({"value": -1}, {"value": True}, {"rank": 0},
                        {"snapshot_date": "not a date"}, {"source_url": ""}, {"metric": "streams"}):
            with self.subTest(changes=changes):
                addition = track("popular-1", listener_popularity={**listeners(), **changes})
                with self.assertRaises(ValueError):
                    self.merge([addition])
        with self.assertRaisesRegex(ValueError, "incomplete track"):
            self.merge([track("popular-1")])
        with self.assertRaisesRegex(ValueError, "list of tracks"):
            self.merge({})

    def test_raw_active_startup_loads_additions_after_cap_and_retains_all_aliases(self):
        script = '''
from io import StringIO
import json
from pathlib import Path
from unittest.mock import patch
def fixture(i):
    return {"id": "base-"+str(i), "title": "Base "+str(i), "artist": "Fixture", "year": 2001, "genre": "rock"}
base = [fixture(i) for i in range(50001)]
popular = [dict(fixture(50000), id="popular", year=None, genre="unknown", listener_popularity={
    "source":"mlhd-plus", "metric":"unique_listeners", "value":123, "rank":1,
    "snapshot_date":"2026-09-17", "source_url":"https://example.org/fixture"})]
original_open, original_is_file = Path.open, Path.is_file
def opened(path, *args, **kwargs):
    if path.name == "catalog.json": return StringIO(json.dumps(base))
    if path.name == "catalog-popular.json": return StringIO(json.dumps(popular))
    return original_open(path, *args, **kwargs)
def is_file(path):
    if path.name == "catalog-popular.json": return True
    if path.name in {"catalog-extension.json", "catalog-expansion.json", "metadata-evidence.json"}: return False
    return original_is_file(path)
with patch.object(Path, "open", opened), patch.object(Path, "is_file", is_file):
    from prototype.app.catalog import CATALOG, TRACK_BY_ID
assert len(CATALOG) == 50001, len(CATALOG)
assert [row["id"] for row in CATALOG[:50000]] == [row["id"] for row in base[:50000]]
assert CATALOG[-1]["id"] == "popular"
assert CATALOG[-1]["year"] is None
assert TRACK_BY_ID["popular"]["listener_popularity"]["value"] == 123
assert "unknown" not in __import__("prototype.app.catalog", fromlist=["CATALOG_GENRES"]).CATALOG_GENRES
'''
        result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[2],
                                env={**os.environ, "NEXTTRACK_CATALOG_MODE": "active",
                                     "NEXTTRACK_CATALOG_SNAPSHOT": ""},
                                capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
