"""Append-only feedback with durable online storage and local JSONL fallback.

Records retain their timestamp, request ID, stars, comment and any evaluator
provenance. This module has no public read route: export is an operator action.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path

from .redis_rest import RedisRestClient, StorageError


def _client():
    client = RedisRestClient.from_environment()
    if client is None and (os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV")):
        # A serverless filesystem is not durable and must never report success.
        raise StorageError("required")
    return client


def _decode_record(value):
    try:
        record = json.loads(value)
    except (ValueError, TypeError):
        raise StorageError("corrupt_record") from None
    if not isinstance(record, dict):
        raise StorageError("corrupt_record")
    return record


def save_feedback_record(record, local_path):
    """Persist one complete record; successful return means acknowledged append."""
    if not isinstance(record, dict):
        raise StorageError("invalid_record")
    try:
        payload = json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (ValueError, TypeError):
        raise StorageError("invalid_record") from None
    client = _client()
    if client is not None:
        # One atomic operation: concurrent saves cannot overwrite one another.
        length = client.command("RPUSH", client.key("feedback"), payload)
        if not isinstance(length, int) or isinstance(length, bool) or length < 1:
            raise StorageError("invalid_response")
        return
    try:
        path = Path(local_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            stream.write(payload + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        raise StorageError("unavailable") from None


def read_feedback_records(local_path):
    """Read all records in append order; fail rather than silently omit corrupt data."""
    client = _client()
    if client is not None:
        key = client.key("feedback")
        count = client.command("LLEN", key)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise StorageError("invalid_response")
        records = []
        # LLEN fixes the prefix to read; newer concurrent appends remain for the
        # next export. Bounded pages avoid one oversized REST response.
        for start in range(0, count, 500):
            end = min(start + 499, count - 1)
            page = client.command("LRANGE", key, start, end)
            if not isinstance(page, list) or len(page) != end - start + 1:
                raise StorageError("invalid_response")
            records.extend(_decode_record(item) for item in page)
        return records
    path = Path(local_path)
    try:
        with path.open(encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_SH)
            return [_decode_record(line) for line in stream if line.strip()]
    except FileNotFoundError:
        return []
    except UnicodeError:
        raise StorageError("corrupt_record") from None
    except OSError:
        raise StorageError("unavailable") from None


def export_feedback_jsonl(local_path, destination):
    """Operator export; the destination is replaced only after a complete read."""
    records = read_feedback_records(local_path)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8")
    return len(records)
