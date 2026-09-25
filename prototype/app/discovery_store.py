"""Persistent public metadata cache and shared MusicBrainz request gate.

Redis is required online; local runs use SQLite and the importer's rate lock.
Storage failure fails closed: it must not bypass the upstream request limit.
"""
from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .redis_rest import RedisRestClient, StorageError

DATA = Path(__file__).resolve().parents[1] / 'data'
GATE = 'nexttrack:musicbrainz:request-gate:v1'


class DiscoveryStore:
    def __init__(self, client=None, directory=None):
        self.client = client
        self.directory = Path(directory or DATA)

    @contextmanager
    def _connect(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.directory / '.discovery-cache.sqlite3', timeout=2)
        try:
            with connection:
                connection.execute('CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT, expires REAL)')
                yield connection
        finally:
            connection.close()

    def defer(self, seconds):
        self.put_many([('cooldown', True)], seconds)
        if self.client:
            # Retry-After applies across deployment/storage namespaces too.
            script = "local ttl=redis.call('PTTL',KEYS[1]); if ttl<tonumber(ARGV[1]) then redis.call('SET',KEYS[1],'1','PX',ARGV[1]) end return 1"
            self.client.command('EVAL', script, 1, GATE, seconds * 1000)
        else:
            try:
                with (self.directory / '.musicbrainz-api.lock').open('a+') as stream:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                    stream.seek(0)
                    try: previous = float(stream.read() or 0)
                    except ValueError: previous = 0
                    stream.seek(0); stream.truncate()
                    stream.write(str(max(previous, time.time() + seconds - 1.1))); stream.flush()
            except OSError:
                raise StorageError('unavailable') from None

    def get(self, key):
        try:
            if self.client:
                value = self.client.command('GET', self.client.key('discovery:v1:' + key))
            else:
                with self._connect() as db:
                    row = db.execute('SELECT value FROM cache WHERE key=? AND expires>?', (key, time.time())).fetchone()
                    value = row[0] if row else None
            return json.loads(value) if value is not None else None
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise StorageError('corrupt_record') from None

    def put_many(self, entries, ttl):
        values = [(key, json.dumps(value, ensure_ascii=False, allow_nan=False)) for key, value in entries]
        try:
            if self.client:
                # Query and recording records persist together in one round trip.
                script = "for i=1,#KEYS do redis.call('SET',KEYS[i],ARGV[i+1],'EX',ARGV[1]) end return #KEYS"
                result = self.client.command('EVAL', script, len(values),
                    *[self.client.key('discovery:v1:' + key) for key, _ in values], ttl,
                    *[value for _, value in values])
                if result != len(values):
                    raise StorageError('invalid_response')
            else:
                with self._connect() as db:
                    db.execute('DELETE FROM cache WHERE expires<=?', (time.time(),))
                    db.executemany('INSERT OR REPLACE INTO cache VALUES (?,?,?)',
                                   [(key, value, time.time() + ttl) for key, value in values])
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise StorageError('unavailable') from None

    def reserve(self):
        if self.client:
            # Same key across workers, deployments and storage prefixes. One
            # shared Redis service must gate every deployment of this client.
            return self.client.command('SET', GATE, '1', 'NX', 'PX', 1100) == 'OK'
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with (self.directory / '.musicbrainz-api.lock').open('a+') as stream:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                stream.seek(0)
                try:
                    last_start = float(stream.read() or 0)
                except ValueError:
                    last_start = 0
                now = time.time()
                if now < last_start + 1.1:
                    return False
                stream.seek(0); stream.truncate(); stream.write(str(now)); stream.flush()
                return True
        except OSError:
            raise StorageError('unavailable') from None


def discovery_store():
    client = RedisRestClient.from_environment()
    if client:
        client._timeout = 2
    elif os.environ.get('VERCEL') or os.environ.get('VERCEL_ENV'):
        raise StorageError('required')
    return DiscoveryStore(client, os.environ.get('NEXTTRACK_DISCOVERY_CACHE_DIR'))
