"""Expiring, server-only Spotify OAuth state and session storage.

Local runs retain the dictionary adapter; Vercel requires shared Redis storage.
GETDEL makes callback state single use across workers. SET XX prevents a token
refresh finishing after logout from recreating the removed session.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time

from .redis_rest import RedisRestClient, StorageError

STATE_TTL = 600
SESSION_TTL = 86400
_memory_lock = threading.RLock()


def _valid_id(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) is not None


def _active(record):
    if not isinstance(record, dict):
        raise StorageError("corrupt_record")
    deadline = record.get("deadline")
    if isinstance(deadline, bool) or not isinstance(deadline, (float, int)) or not math.isfinite(deadline):
        raise StorageError("corrupt_record")
    return deadline > time.time()


def _encode(record):
    try:
        return json.dumps(record, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError):
        raise StorageError("invalid_record") from None


def _decode(payload):
    if payload is None:
        return None
    try:
        record = json.loads(payload)
    except (ValueError, TypeError):
        raise StorageError("corrupt_record") from None
    return record if _active(record) else None


class MemoryOAuthStore:
    def __init__(self, pending, sessions):
        self.pending = pending
        self.sessions = sessions

    def prune(self):
        with _memory_lock:
            for values in (self.pending, self.sessions):
                for key in list(values):
                    if not _active(values[key]):
                        values.pop(key, None)

    def put_state(self, state, transaction):
        with _memory_lock:
            self.prune()
            if len(self.pending) >= 1000:
                raise StorageError("capacity")
            self.pending[state] = dict(transaction)

    def consume_state(self, state):
        with _memory_lock:
            self.prune()
            return self.pending.pop(state, None)

    def get_session(self, session_id):
        with _memory_lock:
            self.prune()
            session = self.sessions.get(session_id)
            # Return a copy so mutation while refreshing is not a hidden write.
            return dict(session) if session else None

    def put_session(self, session_id, session):
        with _memory_lock:
            self.prune()
            if len(self.sessions) >= 1000:
                raise StorageError("capacity")
            self.sessions[session_id] = dict(session)

    def refresh_session(self, session_id, session):
        with _memory_lock:
            self.prune()
            if session_id not in self.sessions or not _active(session):
                return False
            self.sessions[session_id] = dict(session)
            return True

    def delete_session(self, session_id):
        with _memory_lock:
            self.sessions.pop(session_id, None)


class RedisOAuthStore:
    def __init__(self, client):
        self.client = client

    def _key(self, kind, identifier):
        return self.client.key(f"oauth:{kind}:{identifier}")

    def put_state(self, state, transaction):
        result = self.client.command("SETEX", self._key("state", state), STATE_TTL, _encode(transaction))
        if result != "OK":
            raise StorageError("invalid_response")

    def consume_state(self, state):
        if not _valid_id(state):
            return None
        # A GET followed by DEL would let two callbacks exchange the same code.
        record = _decode(self.client.command("GETDEL", self._key("state", state)))
        if record is not None and not isinstance(record.get("verifier"), str):
            raise StorageError("corrupt_record")
        return record

    def get_session(self, session_id):
        if not _valid_id(session_id):
            return None
        return _decode(self.client.command("GET", self._key("session", session_id)))

    def put_session(self, session_id, session):
        result = self.client.command("SETEX", self._key("session", session_id), SESSION_TTL, _encode(session))
        if result != "OK":
            raise StorageError("invalid_response")

    def refresh_session(self, session_id, session):
        if not _valid_id(session_id) or not _active(session):
            return False
        # The session's original deadline is retained: a refresh is not a new
        # login and must not renew the one-day session lifetime.
        remaining = math.ceil(session["deadline"] - time.time())
        if remaining <= 0:
            return False
        result = self.client.command("SET", self._key("session", session_id), _encode(session),
                                     "XX", "EX", min(remaining, SESSION_TTL))
        if result is None:
            return False
        if result != "OK":
            raise StorageError("invalid_response")
        return True

    def delete_session(self, session_id):
        if _valid_id(session_id):
            result = self.client.command("DEL", self._key("session", session_id))
            if not isinstance(result, int) or isinstance(result, bool) or result not in (0, 1):
                raise StorageError("invalid_response")


def oauth_store(pending, sessions):
    client = RedisRestClient.from_environment()
    if client is not None:
        return RedisOAuthStore(client)
    if os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"):
        raise StorageError("required")
    return MemoryOAuthStore(pending, sessions)
