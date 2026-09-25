import unittest
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch
from fastapi.testclient import TestClient
from prototype.app.main import app
from prototype.app import spotify

class SpotifyTests(unittest.TestCase):
    def setUp(self):
        spotify.pending.clear(); spotify.sessions.clear()
        self.client=TestClient(app)
        self.config=patch.object(spotify,'config',return_value=('test-client','http://127.0.0.1:8017/auth/spotify/callback'))
        self.config.start()
        self.addCleanup(self.config.stop)

    def authorize(self):
        response=self.client.get('/auth/spotify/login',follow_redirects=False)
        params=parse_qs(urlparse(response.headers['location']).query)
        self.assertEqual(params['code_challenge_method'],['S256'])
        self.assertIn('HttpOnly',response.headers['set-cookie'])
        self.assertNotIn('verifier',response.headers['location'])
        return params['state'][0]

    def test_callback_tokens_stay_server_side_and_state_is_single_use(self):
        state=self.authorize()
        with patch.object(spotify,'json_request',return_value={'access_token':'private-token','refresh_token':'private-refresh','expires_in':3600}):
            response=self.client.get('/auth/spotify/callback',params={'state':state,'code':'code'},follow_redirects=False)
        self.assertEqual(response.status_code,303)
        self.assertNotIn('private-token',str(response.headers))
        self.assertTrue(self.client.get('/auth/spotify/status').json()['connected'])
        self.assertEqual(self.client.get('/auth/spotify/callback',params={'state':state,'code':'code'}).status_code,400)
        self.client.post('/auth/spotify/logout')
        self.assertFalse(self.client.get('/auth/spotify/status').json()['connected'])

    def test_invalid_state_cannot_exchange_tokens(self):
        self.authorize()
        with patch.object(spotify,'json_request') as call:
            self.assertEqual(self.client.get('/auth/spotify/callback?state=wrong&code=x').status_code,400)
            call.assert_not_called()

    def test_denial_does_not_connect(self):
        state=self.authorize()
        response=self.client.get('/auth/spotify/callback',params={'state':state,'error':'access_denied'},follow_redirects=False)
        self.assertIn('cancelled',response.headers['location'])
        self.assertFalse(self.client.get('/auth/spotify/status').json()['connected'])

    def test_unconfigured_login(self):
        with patch.object(spotify,'config',return_value=('','http://127.0.0.1:8017/auth/spotify/callback')):
            response=self.client.get('/auth/spotify/login',follow_redirects=False)
        self.assertIn('unconfigured',response.headers['location'])

    def test_song_fallback_and_exact_match_and_refresh(self):
        track=next(iter(spotify.TRACK_BY_ID.values()))
        route='/spotify/open/'+track['id']
        self.assertIn('https://open.spotify.com/search/',self.client.get(route,follow_redirects=False).headers['location'])
        spotify.sessions['test-session']={'deadline':spotify.time.time()+100,'expires':0,'refresh_token':'refresh'}
        self.client.cookies.set(spotify.COOKIE,'test-session')
        item={'id':'A'*22,'name':track['title'],'artists':[{'name':track['artist']}]}
        with patch.object(spotify,'json_request',side_effect=[{'access_token':'refreshed','expires_in':3600},{'tracks':{'items':[item]}}]) as call:
            response=self.client.get(route,follow_redirects=False)
        self.assertEqual(call.call_count,2)
        self.assertEqual(response.headers['location'],'https://open.spotify.com/track/'+'A'*22)
        item['name']='Wrong song'
        with patch.object(spotify,'json_request',return_value={'tracks':{'items':[item]}}):
            self.assertIn('/search/',self.client.get(route,follow_redirects=False).headers['location'])

    def test_unknown_song_and_cross_origin_logout(self):
        self.assertEqual(self.client.get('/spotify/open/nonexistent').status_code,404)
        self.assertEqual(self.client.post('/auth/spotify/logout',headers={'Origin':'https://evil.example'}).status_code,403)
