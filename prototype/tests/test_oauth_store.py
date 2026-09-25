"""OAuth integration tests use an in-process REST fixture, never live Spotify."""
import io
import json
import os
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from prototype.app import spotify
from prototype.app.oauth_store import (
    MemoryOAuthStore, RedisOAuthStore, SESSION_TTL, STATE_TTL, oauth_store,
)
from prototype.app.redis_rest import RedisRestClient, StorageError


class RedisRESTFixture:
    """Shared Redis command semantics behind separate REST client instances."""
    def __init__(self):
        self.now = time.time()
        self.values = {}
        self.commands = []
        self.lock = threading.Lock()

    def client(self):
        return RedisRestClient('https://storage.example', 'fixture-storage-token',
                               prefix='nexttrack:test', opener=self.open)

    def open(self, request, timeout):
        command = json.loads(request.data)
        with self.lock:
            self.commands.append(command)
            op, key = command[:2]
            if key in self.values and self.values[key][1] <= self.now:
                del self.values[key]
            current = self.values.get(key)
            if op == 'SETEX':
                self.values[key] = (command[3], self.now + command[2])
                result = 'OK'
            elif op == 'GETDEL':
                result = self.values.pop(key, (None, None))[0]
            elif op == 'GET':
                result = current[0] if current else None
            elif op == 'DEL':
                result = int(self.values.pop(key, None) is not None)
            elif op == 'SET':
                self.assert_refresh_options(command)
                result = 'OK' if current else None
                if current:
                    self.values[key] = (command[2], self.now + command[5])
            else:
                raise AssertionError(f'Unexpected command: {op}')
            return io.BytesIO(json.dumps({'result': result}).encode())

    @staticmethod
    def assert_refresh_options(command):
        if command[3:5] != ['XX', 'EX']:
            raise AssertionError('Refresh must be conditional and expiring')


class SharedSpotifyTests(unittest.TestCase):
    def setUp(self):
        self.backend = RedisRESTFixture()
        self.app = FastAPI()
        self.app.include_router(spotify.router)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        for context in (
            patch.dict(os.environ, {'VERCEL': '1'}, clear=True),
            patch.object(spotify, 'config', return_value=('fixture-client', 'http://testserver/auth/spotify/callback')),
            patch.object(RedisRestClient, 'from_environment', side_effect=self.backend.client),
            patch('prototype.app.oauth_store.time.time', side_effect=lambda: self.backend.now),
        ):
            context.start()
            self.addCleanup(context.stop)

    def authorize(self):
        response = self.client.get('/auth/spotify/login', follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        state = parse_qs(urlparse(response.headers['location']).query)['state'][0]
        self.assertEqual(self.backend.commands[-1][0], 'SETEX')
        self.assertEqual(self.backend.commands[-1][2], STATE_TTL)
        return state

    def connect(self):
        state = self.authorize()
        with patch.object(spotify, 'json_request', return_value={
            'access_token': 'fixture-access', 'refresh_token': 'fixture-refresh', 'expires_in': 3600,
        }):
            response = self.client.get('/auth/spotify/callback', params={'state': state, 'code': 'fixture-code'},
                                       follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        return self.client.cookies.get(spotify.COOKIE)

    def test_cross_instance_callback_session_and_replay(self):
        state = self.authorize()
        # A fresh worker can consume the state without the original dictionaries.
        with TestClient(self.app) as other:
            other.cookies.set(spotify.STATE_COOKIE, state)
            with patch.object(spotify, 'pending', {}), patch.object(spotify, 'sessions', {}), \
                 patch.object(spotify, 'json_request', return_value={
                     'access_token': 'fixture-access', 'refresh_token': 'fixture-refresh', 'expires_in': 3600,
                 }) as exchange:
                response = other.get('/auth/spotify/callback', params={'state': state, 'code': 'fixture-code'},
                                     follow_redirects=False)
                self.assertEqual(response.status_code, 303)
                self.assertNotIn('fixture-access', str(response.headers))
                self.assertNotIn('fixture-refresh', response.text)
                self.assertTrue(other.get('/auth/spotify/status').json()['connected'])
                # Original browser still has the state cookie; Redis rejects reuse.
                retry = self.client.get('/auth/spotify/callback', params={'state': state, 'code': 'fixture-code'},
                                        follow_redirects=False)
                self.assertEqual(retry.status_code, 400)
                self.assertEqual(exchange.call_count, 1)
                session_id = other.cookies.get(spotify.COOKIE)
                self.client.cookies.set(spotify.COOKIE, session_id)
                self.assertTrue(self.client.get('/auth/spotify/status').json()['connected'])
        writes = [command for command in self.backend.commands if command[0] == 'SETEX']
        self.assertEqual(writes[-1][2], SESSION_TTL)
        self.assertTrue(any(command[0] == 'GETDEL' for command in self.backend.commands))

    def test_state_expiry_prevents_token_exchange(self):
        state = self.authorize()
        self.backend.now += STATE_TTL
        with patch.object(spotify, 'json_request') as exchange:
            response = self.client.get('/auth/spotify/callback', params={'state': state, 'code': 'fixture-code'},
                                       follow_redirects=False)
        self.assertEqual(response.status_code, 400)
        exchange.assert_not_called()

    def test_cookie_mismatch_does_not_consume_valid_state(self):
        state = self.authorize()
        with patch.object(spotify, 'json_request') as exchange:
            response = self.client.get('/auth/spotify/callback', params={'state': 'incorrect-é', 'code': 'fixture-code'},
                                       follow_redirects=False)
        self.assertEqual(response.status_code, 400)
        self.assertIn('nexttrack:test:oauth:state:' + state, self.backend.values)
        exchange.assert_not_called()

    def test_session_expiry_and_logout_are_shared(self):
        session_id = self.connect()
        with TestClient(self.app) as other:
            other.cookies.set(spotify.COOKIE, session_id)
            self.assertTrue(other.get('/auth/spotify/status').json()['connected'])
            self.client.post('/auth/spotify/logout')
            self.assertFalse(other.get('/auth/spotify/status').json()['connected'])
        self.connect()
        self.backend.now += SESSION_TTL
        self.assertFalse(self.client.get('/auth/spotify/status').json()['connected'])

    def test_refresh_token_rotation_persists_without_extending_session(self):
        session_id = self.connect()
        store = RedisOAuthStore(self.backend.client())
        original = store.get_session(session_id)
        self.backend.now += 3600
        track = next(iter(spotify.TRACK_BY_ID.values()))
        with patch.object(spotify, 'json_request', side_effect=[
            {'access_token': 'fixture-new-access', 'refresh_token': 'fixture-rotated', 'expires_in': 3600},
            {'tracks': {'items': []}},
        ]):
            response = self.client.get('/spotify/open/' + track['id'], follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        persisted = RedisOAuthStore(self.backend.client()).get_session(session_id)
        self.assertEqual(persisted['refresh_token'], 'fixture-rotated')
        self.assertEqual(persisted['access_token'], 'fixture-new-access')
        self.assertEqual(persisted['deadline'], original['deadline'])
        refresh_write = next(command for command in self.backend.commands if command[0] == 'SET')
        self.assertEqual(refresh_write[3:], ['XX', 'EX', SESSION_TTL - 3600])
        # Spotify may omit a refresh token; the existing one must survive.
        self.backend.now += 3600
        with patch.object(spotify, 'json_request', side_effect=[
            {'access_token': 'fixture-third-access', 'expires_in': 3600}, {'tracks': {'items': []}},
        ]) as request:
            self.client.get('/spotify/open/' + track['id'], follow_redirects=False)
        self.assertEqual(request.call_args_list[0].args[1]['refresh_token'], 'fixture-rotated')
        self.assertEqual(store.get_session(session_id)['refresh_token'], 'fixture-rotated')

    def test_logout_during_refresh_cannot_resurrect_session(self):
        session_id = self.connect()
        self.backend.now += 3600
        store = RedisOAuthStore(self.backend.client())
        track = next(iter(spotify.TRACK_BY_ID.values()))

        def finish_refresh_after_logout(*args, **kwargs):
            store.delete_session(session_id)
            return {'access_token': 'fixture-after-logout', 'expires_in': 3600}

        with patch.object(spotify, 'json_request', side_effect=finish_refresh_after_logout) as request:
            response = self.client.get('/spotify/open/' + track['id'], follow_redirects=False)
        self.assertIn('/search/', response.headers['location'])
        self.assertEqual(request.call_count, 1)
        self.assertIsNone(store.get_session(session_id))

    def test_concurrent_state_consumption_has_only_one_winner(self):
        state = self.authorize()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: RedisOAuthStore(self.backend.client()).consume_state(state), range(2)))
        self.assertEqual(sum(result is not None for result in results), 1)

    def test_storage_outage_is_safe_and_search_links_still_work(self):
        session_id = self.connect()
        track = next(iter(spotify.TRACK_BY_ID.values()))
        with patch.object(RedisRestClient, 'command', side_effect=StorageError('unavailable')):
            for method, route in [('GET', '/auth/spotify/status'), ('GET', '/auth/spotify/login'),
                                  ('POST', '/auth/spotify/logout')]:
                response = self.client.request(method, route, follow_redirects=False)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.headers['cache-control'], 'no-store')
                self.assertNotIn(session_id, response.text)
                self.assertNotIn('fixture-', response.text)
            response = self.client.get('/spotify/open/' + track['id'], follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.assertIn('/search/', response.headers['location'])


class OAuthStoreSelectionTests(unittest.TestCase):
    def test_vercel_requires_shared_storage_even_if_only_vercel_env_is_set(self):
        for setting in ('VERCEL', 'VERCEL_ENV'):
            with self.subTest(setting=setting), patch.dict(os.environ, {setting: 'preview'}, clear=True):
                with self.assertRaises(StorageError) as error:
                    oauth_store({}, {})
                self.assertEqual(error.exception.category, 'required')

    def test_partial_configuration_never_silently_uses_memory(self):
        with patch.dict(os.environ, {'UPSTASH_REDIS_REST_URL': 'https://storage.example'}, clear=True):
            with self.assertRaises(StorageError) as error:
                oauth_store({}, {})
        self.assertEqual(error.exception.category, 'configuration')

    def test_vercel_auth_routes_fail_closed_and_anonymous_links_work(self):
        app = FastAPI()
        app.include_router(spotify.router)
        with patch.dict(os.environ, {'VERCEL': '1'}, clear=True), \
             patch.object(spotify, 'config', return_value=('fixture-client', 'http://testserver/auth/spotify/callback')), \
             TestClient(app) as client:
            for method, route in [('GET', '/auth/spotify/status'), ('GET', '/auth/spotify/login'),
                                  ('GET', '/auth/spotify/callback'), ('POST', '/auth/spotify/logout')]:
                response = client.request(method, route, follow_redirects=False)
                self.assertEqual(response.status_code, 503)
                self.assertIn('temporarily unavailable', response.json()['detail'])
            track = next(iter(spotify.TRACK_BY_ID.values()))
            with patch.object(spotify, 'json_request') as request:
                response = client.get('/spotify/open/' + track['id'], follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.assertIn('/search/', response.headers['location'])
            request.assert_not_called()

    def test_local_session_copy_and_conditional_refresh_prevent_resurrection(self):
        with patch.dict(os.environ, {}, clear=True):
            pending, sessions = {}, {}
            store = oauth_store(pending, sessions)
        self.assertIsInstance(store, MemoryOAuthStore)
        store.put_session('local-session', {'deadline': time.time() + 20, 'access_token': 'old'})
        session = store.get_session('local-session')
        session['access_token'] = 'new'
        self.assertEqual(sessions['local-session']['access_token'], 'old')
        store.delete_session('local-session')
        self.assertFalse(store.refresh_session('local-session', session))
        self.assertEqual(sessions, {})
