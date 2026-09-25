import io
import json
import tempfile
import unittest
from urllib.error import HTTPError, URLError
from prototype.scripts.musicbrainz_client import MusicBrainzClient, MusicBrainzError, retry_delay
from prototype.scripts.import_catalog import musicbrainz_track


class MusicBrainzClientTests(unittest.TestCase):
    def client(self, responses, cache=None):
        self.now = 0
        self.calls = []
        def sleep(seconds): self.now += seconds
        def opener(request, timeout):
            self.calls.append((self.now, request))
            item = responses.pop(0)
            if isinstance(item, Exception): raise item
            return io.BytesIO(json.dumps(item).encode())
        return MusicBrainzClient(cache_dir=cache, opener=opener, clock=lambda:self.now,
                                sleep=sleep, user_agent='NextTrack-test/1.0')

    def test_busy_retries_then_caches_success(self):
        with tempfile.TemporaryDirectory() as cache:
            client=self.client([HTTPError('url',503,'busy',{},io.BytesIO()),{'recordings':[]}],cache)
            self.assertEqual(client.recordings('pop'),{'recordings':[]})
            self.assertEqual(len(self.calls),2)
            self.assertGreaterEqual(self.calls[1][0],5)
            client.recordings('pop')
            self.assertEqual(len(self.calls),2)
            self.assertEqual(client.events[-1]['event'],'cache_hit')

    def test_retry_after_is_honoured(self):
        client=self.client([HTTPError('url',429,'slow',{'Retry-After':'12'},io.BytesIO()),{'recordings':[]}])
        client.recordings('pop')
        self.assertGreaterEqual(self.calls[1][0],12)
        self.assertEqual(retry_delay('Thu, 01 Jan 1970 00:02:00 GMT',now=100),20)

    def test_long_retry_after_defers(self):
        client=self.client([HTTPError('url',503,'busy',{'Retry-After':'120'},io.BytesIO())])
        with self.assertRaises(MusicBrainzError) as error: client.recordings('pop')
        self.assertEqual(error.exception.retry_after,120)
        self.assertEqual(len(self.calls),1)

    def test_invalid_request_is_not_retried(self):
        client=self.client([HTTPError('url',400,'invalid',{},io.BytesIO())])
        with self.assertRaises(MusicBrainzError) as error: client.recordings('pop')
        self.assertEqual(error.exception.status,400)
        self.assertEqual(len(self.calls),1)

    def test_connection_failures_are_bounded(self):
        client=self.client([URLError('offline') for _ in range(3)])
        with self.assertRaises(MusicBrainzError): client.recordings('pop')
        self.assertEqual(len(self.calls),3)

    def test_invalid_response_is_not_cached(self):
        with tempfile.TemporaryDirectory() as cache:
            client=self.client([{'error':'bad'}, {'recordings':[]}],cache)
            with self.assertRaises(MusicBrainzError): client.recordings('pop')
            client.recordings('pop')
            self.assertEqual(len(self.calls),2)

    def test_requests_are_paced_and_video_excluded(self):
        client=self.client([{'recordings':[]},{'recordings':[]}])
        client.recordings('pop'); client.recordings('rock')
        self.assertGreaterEqual(self.calls[1][0]-self.calls[0][0],1.1)
        self.assertIn('NOT+video%3Atrue',self.calls[0][1].full_url)

    def test_genres_are_supported_and_videos_skipped(self):
        item={'id':'abc','title':'Song','artist-credit':[{'name':'Artist'}],
              'first-release-date':'2020','genres':[{'name':'pop','count':2}],
              'tags':[{'name':'pop','count':1}]}
        self.assertEqual(musicbrainz_track(item)['genre'],'pop')
        item['video']=True
        self.assertIsNone(musicbrainz_track(item))
