"""Spotify PKCE sign-in and outbound links; tokens stay in server-only storage."""
import base64
import hashlib
import json
import re
import secrets
import time
from contextlib import contextmanager
from urllib.parse import urlencode, quote, urlparse
from urllib.request import Request as URLRequest, urlopen
from urllib.error import HTTPError, URLError

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from .settings import server_settings
from .catalog import TRACK_BY_ID, normalize
from .oauth_store import MemoryOAuthStore, oauth_store, STATE_TTL, SESSION_TTL
from .redis_rest import StorageError

router = APIRouter()
pending = {}
sessions = {}
COOKIE = 'nexttrack_spotify'
STATE_COOKIE = 'nexttrack_spotify_state'


def config():
    settings = server_settings()
    return (settings.get('SPOTIFY_CLIENT_ID', ''),
            settings.get('SPOTIFY_REDIRECT_URI', 'http://127.0.0.1:8017/auth/spotify/callback'))


def prune():
    MemoryOAuthStore(pending, sessions).prune()


@contextmanager
def auth_store():
    try:
        yield oauth_store(pending, sessions)
    except StorageError:
        # Never expose storage credentials, saved tokens or upstream bodies.
        raise HTTPException(503, 'Spotify sign-in is temporarily unavailable. Please try again later.',
                            headers={'Cache-Control': 'no-store'}) from None


def cookie(response, name, value, max_age):
    response.set_cookie(name, value, max_age=max_age, httponly=True, samesite='lax',
                        secure=config()[1].startswith('https://'), path='/')


def json_request(url, data=None, token=None):
    headers = {'Accept': 'application/json'}
    if token: headers['Authorization'] = 'Bearer ' + token
    if data is not None: headers['Content-Type'] = 'application/x-www-form-urlencoded'
    request = URLRequest(url, data=urlencode(data).encode() if data is not None else None, headers=headers)
    with urlopen(request, timeout=10) as response:
        return json.load(response)


def update_tokens(session, data):
    if not isinstance(data.get('access_token'), str) or not data['access_token']:
        raise ValueError('Missing access token')
    session['access_token'] = data['access_token']
    session['expires'] = time.time() + int(data.get('expires_in', 3600)) - 30
    if data.get('refresh_token'): session['refresh_token'] = data['refresh_token']


@router.get('/auth/spotify/status')
def status(request: Request):
    with auth_store() as store:
        connected = bool(store.get_session(request.cookies.get(COOKIE, '')))
        return JSONResponse({'configured': bool(config()[0]), 'connected': connected},
                            headers={'Cache-Control': 'no-store'})


@router.get('/auth/spotify/login')
def login():
    with auth_store() as store:
        client_id, redirect_uri = config()
        if not client_id:
            return RedirectResponse('/?spotify=unconfigured', status_code=303)
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        store.put_state(state, {'verifier': verifier, 'deadline': time.time() + STATE_TTL})
        params = {'client_id': client_id, 'response_type': 'code', 'redirect_uri': redirect_uri,
                  'state': state, 'code_challenge_method': 'S256', 'code_challenge': challenge}
        response = RedirectResponse('https://accounts.spotify.com/authorize?' + urlencode(params), status_code=303)
        response.headers['Cache-Control'] = 'no-store'
        cookie(response, STATE_COOKIE, state, STATE_TTL)
        return response


@router.get('/auth/spotify/callback')
def callback(request: Request, state: str = '', code: str = '', error: str = ''):
    with auth_store() as store:
        expected = request.cookies.get(STATE_COOKIE, '')
        if not state or not expected or not secrets.compare_digest(state.encode(), expected.encode()):
            raise HTTPException(400, 'Spotify sign-in expired. Please try again.')
        transaction = store.consume_state(state)
        if transaction is None:
            raise HTTPException(400, 'Spotify sign-in expired. Please try again.')
        response = RedirectResponse('/?spotify=cancelled' if error else '/?spotify=connected', status_code=303)
        response.delete_cookie(STATE_COOKIE, path='/')
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        if error: return response
        if not code: raise HTTPException(400, 'Missing Spotify authorization code')
        client_id, redirect_uri = config()
        try:
            data = json_request('https://accounts.spotify.com/api/token',
                                {'client_id': client_id, 'grant_type': 'authorization_code', 'code': code,
                                 'redirect_uri': redirect_uri, 'code_verifier': transaction['verifier']})
            session = {'deadline': time.time() + SESSION_TTL}
            update_tokens(session, data)
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError, TypeError):
            response.headers['location'] = '/?spotify=failed'
            return response
        session_id = secrets.token_urlsafe(32)
        store.put_session(session_id, session)
        store.delete_session(request.cookies.get(COOKIE, ''))
        cookie(response, COOKIE, session_id, SESSION_TTL)
        return response


@router.post('/auth/spotify/logout')
def logout(request: Request):
    origin = request.headers.get('origin')
    expected = urlparse(config()[1])
    if origin and origin != f'{expected.scheme}://{expected.netloc}':
        raise HTTPException(403, 'Invalid origin')
    with auth_store() as store:
        store.delete_session(request.cookies.get(COOKIE, ''))
    response = JSONResponse({'connected': False}, headers={'Cache-Control': 'no-store'})
    response.delete_cookie(COOKIE, path='/')
    return response


@router.get('/spotify/open/{track_id:path}')
def open_track(track_id: str, request: Request):
    from .musicbrainz import cached_track
    track = TRACK_BY_ID.get(track_id) or cached_track(track_id)
    if not track: raise HTTPException(404, 'Song not found')
    destination = 'https://open.spotify.com/search/' + quote(track['title'] + ' ' + track['artist'], safe='')
    session_id = request.cookies.get(COOKIE, '')
    store, session = None, None
    if session_id:
        try:
            store = oauth_store(pending, sessions)
            session = store.get_session(session_id)
        except StorageError:
            pass
    if session:
        try:
            if session['expires'] <= time.time():
                update_tokens(session, json_request('https://accounts.spotify.com/api/token',
                    {'client_id': config()[0], 'grant_type': 'refresh_token', 'refresh_token': session['refresh_token']}))
                if not store.refresh_session(session_id, session):
                    # A logout or expiry won the race with this refresh.
                    return RedirectResponse(destination, status_code=303,
                                            headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})
            data = json_request('https://api.spotify.com/v1/search?' + urlencode({
                'q': f'track:{track["title"]} artist:{track["artist"]}', 'type': 'track', 'limit': 10}),
                token=session['access_token'])
            for item in data.get('tracks', {}).get('items', []):
                artists = [normalize(a.get('name', '')) for a in item.get('artists', [])]
                # Never silently redirect to a merely similar recording.
                if (normalize(item.get('name', '')) == normalize(track['title']) and
                    normalize(track['artist']) in artists and re.fullmatch(r'[A-Za-z0-9]{22}', item.get('id', ''))):
                    destination = 'https://open.spotify.com/track/' + item['id']
                    break
        except HTTPError as exc:
            if exc.code == 401:
                try:
                    store.delete_session(session_id)
                except StorageError:
                    pass
        except (URLError, TimeoutError, ValueError, KeyError, TypeError, StorageError):
            pass
    return RedirectResponse(destination, status_code=303, headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})
