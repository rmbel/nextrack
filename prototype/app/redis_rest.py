"""Small server-only Upstash REST adapter shared by feedback and OAuth storage.

Reference: https://upstash.com/docs/redis/features/restapi
Commands are POSTed as JSON arrays; credentials never appear in URLs or errors.
"""
from __future__ import annotations

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class StorageError(RuntimeError):
    """Safe error category; upstream bodies and credentials are never retained."""
    def __init__(self, category="unavailable"):
        self.category = category
        messages = {
            "configuration": "Persistent storage is not configured correctly.",
            "required": "Persistent storage must be configured before saving online.",
            "invalid_record": "The feedback record could not be stored.",
            "corrupt_record": "Saved feedback could not be read completely.",
        }
        super().__init__(messages.get(category, "Persistent storage is temporarily unavailable."))


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        # Do not forward bearer credentials if an endpoint returns a redirect.
        return None


class RedisRestClient:
    def __init__(self, url, token, prefix="nexttrack:preview", *, opener=None, timeout=5):
        try:
            parts = urlsplit(url)
            valid = (parts.scheme == "https" and bool(parts.hostname)
                     and not parts.username and not parts.password
                     and not parts.query and not parts.fragment
                     and parts.path in ("", "/"))
        except (ValueError, TypeError):
            valid = False
        if not valid or not token or not re.fullmatch(r"[A-Za-z0-9:_-]{1,100}", prefix):
            raise StorageError("configuration")
        self._url = url.rstrip("/")
        self._token = token
        self._prefix = prefix.rstrip(":")
        self._opener = opener if opener is not None else build_opener(_NoRedirects()).open
        self._timeout = timeout

    @classmethod
    def from_environment(cls, environ=None):
        values = os.environ if environ is None else environ
        url = values.get("UPSTASH_REDIS_REST_URL", "").strip()
        token = values.get("UPSTASH_REDIS_REST_TOKEN", "").strip()
        if not url and not token:
            return None
        if not url or not token:
            raise StorageError("configuration")
        return cls(url, token, values.get("NEXTTRACK_STORAGE_PREFIX", "nexttrack:preview").strip())

    def key(self, name):
        return f"{self._prefix}:{name}"

    def command(self, *args):
        if not args:
            raise StorageError("invalid_record")
        try:
            body = json.dumps(args, ensure_ascii=False, allow_nan=False).encode("utf-8")
            request = Request(self._url, data=body, method="POST", headers={
                "Authorization": "Bearer " + self._token,
                "Content-Type": "application/json", "Accept": "application/json",
            })
            # Never automatically retry: a timed-out RPUSH/GETDEL may have been
            # executed already, so replay could duplicate feedback/consume state.
            with self._opener(request, timeout=self._timeout) as response:
                payload = json.load(response)
        except HTTPError as exc:
            exc.close()
            raise StorageError("unavailable") from None
        except (OSError, URLError, ValueError, TypeError):
            raise StorageError("unavailable") from None
        if not isinstance(payload, dict) or "error" in payload or "result" not in payload:
            raise StorageError("invalid_response")
        return payload["result"]
