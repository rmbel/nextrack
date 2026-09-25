"""Deployment snapshots preserve catalogue identities and fail closed on damage."""
import gzip
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from prototype.app.catalog import _load_runtime_snapshot


class RuntimeCatalogTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "catalog.json.gz"
        self.payload = {
            "schema_version": 1, "catalogue_mode": "active",
            "tracks": [{"id": "canonical", "title": "Song", "artist": "Artist", "year": 2024,
                        "genre": "pop", "language": "unknown", "energy": None}],
            "aliases": {"old-seed": "canonical"},
            "quality": {"raw_records": 2, "accepted_records": 1,
                        "duplicate_records": 1, "quarantined_records": 0},
            "manifest": {},
        }

    def write_snapshot(self):
        with gzip.open(self.path, "wt", encoding="utf-8") as stream:
            json.dump(self.payload, stream)

    def test_roundtrip_restores_canonical_seed_ids_and_quality(self):
        self.write_snapshot()
        tracks, aliases, quality = _load_runtime_snapshot(self.path)
        self.assertEqual(tracks, self.payload["tracks"])
        self.assertEqual(aliases, {"canonical": "canonical", "old-seed": "canonical"})
        self.assertEqual(quality, self.payload["quality"])

    def test_rejects_unsupported_schema_and_nonactive_mode(self):
        for field, value in (("schema_version", 2), ("catalogue_mode", "baseline")):
            original = self.payload[field]
            self.payload[field] = value
            self.write_snapshot()
            with self.assertRaisesRegex(ValueError, "schema or mode"):
                _load_runtime_snapshot(self.path)
            self.payload[field] = original

    def test_rejects_duplicate_canonical_ids(self):
        self.payload["tracks"] *= 2
        self.write_snapshot()
        with self.assertRaisesRegex(ValueError, "duplicate canonical IDs"):
            _load_runtime_snapshot(self.path)

    def test_rejects_dangling_or_conflicting_aliases(self):
        for aliases in ({"old-seed": "missing"}, {"canonical": "other"}, {"old-seed": []}):
            self.payload["aliases"] = aliases
            self.write_snapshot()
            with self.assertRaisesRegex(ValueError, "invalid canonical alias"):
                _load_runtime_snapshot(self.path)

    def test_rejects_inconsistent_quality_counts(self):
        self.payload["quality"]["accepted_records"] = 2
        self.write_snapshot()
        with self.assertRaisesRegex(ValueError, "inconsistent quality counts"):
            _load_runtime_snapshot(self.path)

    def test_snapshot_startup_never_reads_raw_sources_or_reprocesses_metadata(self):
        self.write_snapshot()
        script = '''
from pathlib import Path
from unittest.mock import patch
import prototype.app.catalog_quality as quality
import prototype.app.metadata as metadata
original_open, original_is_file = Path.open, Path.is_file
def checked_open(path, *args, **kwargs):
    if path.name in {"catalog.json", "catalog-extension.json", "catalog-expansion.json", "catalog-popular.json", "metadata-evidence.json"}:
        raise AssertionError("Snapshot startup read raw input: " + str(path))
    return original_open(path, *args, **kwargs)
def is_file(path):
    return True if path.name == "catalog-popular.json" else original_is_file(path)
with patch.object(Path, "open", checked_open), patch.object(Path, "is_file", is_file), patch.object(quality, "prepare_catalog", side_effect=AssertionError("Deduplicated again")), patch.object(metadata, "apply_metadata", side_effect=AssertionError("Enriched again")):
    from prototype.app.catalog import CATALOG, TRACK_BY_ID
    assert len(CATALOG) == 1
    assert TRACK_BY_ID["old-seed"] is TRACK_BY_ID["canonical"]
'''
        result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[2],
                                env={**os.environ, "NEXTTRACK_CATALOG_MODE": "active",
                                     "NEXTTRACK_CATALOG_SNAPSHOT": str(self.path)},
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_baseline_ignores_snapshot_configuration(self):
        script = '''
import json
from prototype.app.catalog import CATALOG, CATALOG_PATH, CATALOG_ALIASES
assert CATALOG == json.loads(CATALOG_PATH.read_text())
assert CATALOG_ALIASES == {track["id"]: track["id"] for track in CATALOG}
'''
        result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[2],
                                env={**os.environ, "NEXTTRACK_CATALOG_MODE": "baseline",
                                     "NEXTTRACK_CATALOG_SNAPSHOT": "/missing/snapshot.json.gz"},
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
