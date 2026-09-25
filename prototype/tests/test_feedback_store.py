from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from prototype.app.feedback_store import (
    export_feedback_jsonl,
    read_feedback_records,
    save_feedback_record,
)
from prototype.app.redis_rest import RedisRestClient, StorageError


class FeedbackStoreTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "feedback.jsonl"
        self.record = {
            "request_id": "test-request",
            "timestamp": "2026-09-17T12:00:00+00:00",
            "rating": 4,
            "comment": "[AGENT TRIAL UT01 run=test] Música; no listening",
            "evaluator_type": "agent",
            "evaluation": {"run_id": "test", "listened": False},
        }

    def assert_storage_error(self, operation, category):
        with self.assertRaises(StorageError) as caught:
            operation()
        self.assertEqual(caught.exception.category, category)
        self.assertNotIn("secret", str(caught.exception))

    def test_environment_configuration_selects_remote_or_local(self):
        self.assertIsNone(RedisRestClient.from_environment({}))
        client = RedisRestClient.from_environment({
            "UPSTASH_REDIS_REST_URL": " https://example.upstash.io/ ",
            "UPSTASH_REDIS_REST_TOKEN": " secret-token ",
            "NEXTTRACK_STORAGE_PREFIX": "nexttrack:test",
        })
        self.assertEqual(client.key("feedback"), "nexttrack:test:feedback")

    def test_partial_configuration_never_falls_back_to_local(self):
        for name in ("UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN"):
            with self.subTest(name=name), patch.dict(os.environ, {name: "secret"}):
                self.assert_storage_error(lambda: save_feedback_record(self.record, self.path), "configuration")
                self.assert_storage_error(lambda: read_feedback_records(self.path), "configuration")
                self.assertFalse(self.path.exists())

    def test_url_and_namespace_configuration_are_restricted(self):
        for url in ("http://example.com", "https://secret:pass@example.com", "https://example.com/secret", "https://example.com/?token=secret", "https://example.com/#secret", ""):
            with self.subTest(url=url):
                self.assert_storage_error(lambda: RedisRestClient(url, "secret-token"), "configuration")
        self.assert_storage_error(lambda: RedisRestClient("https://example.com", "secret-token", "bad namespace"), "configuration")

    def test_rest_command_posts_credentials_only_in_header(self):
        opener = Mock(return_value=io.BytesIO(b'{"result":1}'))
        client = RedisRestClient("https://example.upstash.io", "secret-token", opener=opener)
        self.assertEqual(client.command("RPUSH", client.key("feedback"), json.dumps(self.record)), 1)
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.upstash.io")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-token")
        self.assertEqual(json.loads(request.data)[0], "RPUSH")
        self.assertEqual(opener.call_count, 1)

    def test_rest_failures_are_safe_and_not_retried(self):
        failures = (
            HTTPError("https://example.com", 500, "secret-body", {}, io.BytesIO(b"secret-body")),
            URLError("secret-token"), OSError("secret-token"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                opener = Mock(side_effect=failure)
                client = RedisRestClient("https://example.com", "secret-token", opener=opener)
                self.assert_storage_error(lambda: client.command("RPUSH", "key", "value"), "unavailable")
                self.assertEqual(opener.call_count, 1)
        client = RedisRestClient("https://example.com", "secret-token", opener=Mock(return_value=io.BytesIO(b"secret-invalid-json")))
        self.assert_storage_error(lambda: client.command("GET", "key"), "unavailable")

    def test_invalid_rest_responses_do_not_expose_provider_bodies(self):
        for response in ({"error": "secret-provider-message"}, {}, ["secret"], {"result": 1, "error": "secret"}):
            with self.subTest(response=response):
                client = RedisRestClient("https://example.com", "secret-token", opener=Mock(return_value=io.BytesIO(json.dumps(response).encode())))
                self.assert_storage_error(lambda: client.command("GET", "key"), "invalid_response")

    def test_local_concurrent_appends_preserve_complete_records_and_provenance(self):
        rows = [dict(self.record, request_id=f"request-{index}") for index in range(32)]
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(lambda row: save_feedback_record(row, self.path), rows))
        actual = read_feedback_records(self.path)
        self.assertEqual(len(actual), len(rows))
        self.assertEqual({row["request_id"] for row in actual}, {row["request_id"] for row in rows})
        self.assertTrue(all(row["evaluation"] == self.record["evaluation"] for row in actual))
        self.assertTrue(all(row["comment"] == self.record["comment"] for row in actual))

    def test_missing_local_store_is_empty(self):
        self.assertEqual(read_feedback_records(self.path), [])
        self.assertFalse(self.path.exists())

    def test_serverless_requires_durable_storage_for_both_vercel_markers(self):
        for name, value in (("VERCEL", "1"), ("VERCEL_ENV", "preview"), ("VERCEL_ENV", "production")):
            with self.subTest(name=name, value=value), patch.dict(os.environ, {name: value}):
                self.assert_storage_error(lambda: save_feedback_record(self.record, self.path), "required")
                self.assert_storage_error(lambda: read_feedback_records(self.path), "required")
                self.assertFalse(self.path.exists())

    def test_remote_save_is_one_atomic_append_with_original_provenance(self):
        client = Mock()
        client.key.return_value = "nexttrack:test:feedback"
        client.command.return_value = 1
        with patch("prototype.app.feedback_store._client", return_value=client):
            save_feedback_record(self.record, self.path)
        self.assertEqual(client.command.call_count, 1)
        operation, key, payload = client.command.call_args.args
        self.assertEqual((operation, key), ("RPUSH", "nexttrack:test:feedback"))
        self.assertEqual(json.loads(payload), self.record)
        self.assertFalse(self.path.exists())

    def test_invalid_records_and_unacknowledged_appends_fail(self):
        for record in ([], "secret", {"rating": float("nan")}, {"secret": object()}):
            with self.subTest(record_type=type(record).__name__):
                self.assert_storage_error(lambda: save_feedback_record(record, self.path), "invalid_record")
                self.assertFalse(self.path.exists())
        client = Mock()
        for response in (None, False, True, 0, -1, "1"):
            with self.subTest(response=response), patch("prototype.app.feedback_store._client", return_value=client):
                client.command.return_value = response
                self.assert_storage_error(lambda: save_feedback_record(self.record, self.path), "invalid_response")

    def test_remote_read_pages_complete_initial_prefix_in_order(self):
        rows = [dict(self.record, request_id=str(index)) for index in range(1001)]
        client = Mock()
        client.key.return_value = "nexttrack:test:feedback"
        client.command.side_effect = [1001] + [[json.dumps(row) for row in rows[start:start + 500]] for start in (0, 500, 1000)]
        with patch("prototype.app.feedback_store._client", return_value=client):
            self.assertEqual(read_feedback_records(self.path), rows)
        self.assertEqual([call.args for call in client.command.call_args_list], [
            ("LLEN", "nexttrack:test:feedback"),
            ("LRANGE", "nexttrack:test:feedback", 0, 499),
            ("LRANGE", "nexttrack:test:feedback", 500, 999),
            ("LRANGE", "nexttrack:test:feedback", 1000, 1000),
        ])

    def test_corrupt_or_incomplete_reads_fail_without_partial_export(self):
        destination = Path(self.temporary.name) / "export.jsonl"
        destination.write_text("existing export", encoding="utf-8")
        for content in (b"secret-not-json\n", b"[]\n", b"\xff\n"):
            with self.subTest(content=content):
                self.path.write_bytes(content)
                self.assert_storage_error(lambda: export_feedback_jsonl(self.path, destination), "corrupt_record")
                self.assertEqual(destination.read_text(), "existing export")
        for response, category in (([], "invalid_response"), (["secret-invalid-json"], "corrupt_record"), (["[]"], "corrupt_record")):
            with self.subTest(response=response):
                client = Mock()
                client.command.side_effect = [1, response]
                with patch("prototype.app.feedback_store._client", return_value=client):
                    self.assert_storage_error(lambda: export_feedback_jsonl(self.path, destination), category)
                self.assertEqual(destination.read_text(), "existing export")
        for length in (True, -1, "1", None):
            client = Mock()
            client.command.return_value = length
            with self.subTest(length=length), patch("prototype.app.feedback_store._client", return_value=client):
                self.assert_storage_error(lambda: read_feedback_records(self.path), "invalid_response")

    def test_operator_export_preserves_all_feedback_fields_and_source(self):
        rows = [self.record, {"request_id": "earlier", "relevance_rating": 3, "comment": "Unclassified"}]
        for row in rows:
            save_feedback_record(row, self.path)
        original = self.path.read_bytes()
        destination = Path(self.temporary.name) / "exports" / "feedback.jsonl"
        self.assertEqual(export_feedback_jsonl(self.path, destination), 2)
        self.assertEqual([json.loads(line) for line in destination.read_text().splitlines()], rows)
        self.assertEqual(self.path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
