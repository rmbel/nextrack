import copy
import hashlib
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from fastapi.testclient import TestClient
from prototype.app import musicbrainz as mb
from prototype.app.catalog import CATALOG, TRACK_BY_ID, TRACK_VECTORS
from prototype.app.discovery_store import DiscoveryStore, GATE, discovery_store
from prototype.app.main import app
from prototype.app.models import RecommendRequest
from prototype.app.recommender import generate_recommendations, parse_prompt, matches_style_constraints
from prototype.app.redis_rest import StorageError
from prototype.app.service import recommend_playlist

INTENT = dict(preferred_genres=['jazz','instrumental'], preferred_tags=[], recency=None,
              preferred_language=None, prefer_discovery=True, excluded_genres=[],
              min_year=2021, max_year=2026, target_energy=None, summary='Instrumental jazz.', limitations=[])


def recording(number=1, tags=('jazz','instrumental'), year='2025-01-01', title=None, artist=None):
    return {'id':f'aaaaaaaa-aaaa-aaaa-aaaa-{number:012d}', 'title':title or f'New fixture recording {number}',
            'artist-credit':[{'name':artist or f'Fixture Artist {number}', 'artist':{
                'id':f'bbbbbbbb-bbbb-bbbb-bbbb-{number:012d}', 'name':artist or f'Fixture Artist {number}'}}],
            'first-release-date':year,'tags':[{'name':t,'count':1} for t in tags]}


class MemoryStore:
    def __init__(self): self.values={}; self.reserved=0; self.deferred=None
    def get(self,key): return copy.deepcopy(self.values.get(key))
    def put_many(self,entries,ttl): self.values.update(copy.deepcopy(dict(entries)))
    def reserve(self): self.reserved+=1; return True
    def defer(self,seconds): self.values['cooldown']=True; self.deferred=seconds


def opener_for(items):
    return MagicMock(side_effect=lambda *a,**kw: io.BytesIO(json.dumps({'count':len(items),'recordings':items}).encode()))


class OnlineDiscoveryTests(unittest.TestCase):
    def test_query_uses_conjunction_and_first_release_range(self):
        query=mb.build_query(INTENT)
        self.assertIn('(tag:"jazz") AND tag:"instrumental"',query)
        self.assertIn('firstreleasedate:[2021-01-01 TO 2026-12-31]',query)
        self.assertNotIn('OR tag:"instrumental"',query)
        self.assertNotIn(' OR *',mb.quote_term('jazz" OR *'))
        self.assertIsNone(mb.build_query(dict(INTENT,preferred_genres=[],preferred_tags=[])))

    def test_live_response_persists_and_second_call_uses_cache(self):
        store=MemoryStore(); opener=opener_for([recording()])
        first=mb.discover(INTENT,store=store,opener=opener)
        second=mb.discover(INTENT,store=store,opener=opener)
        self.assertEqual((first.status,second.status),('live','cache_hit'))
        self.assertEqual(opener.call_count,1)
        self.assertEqual(store.reserved,1)
        track=first.tracks[0]
        self.assertEqual(store.get('track:'+track['id']),track)
        self.assertEqual(track['energy'],None)
        self.assertEqual(track['language'],'unknown')
        self.assertIn('instrumental',track['tags'])
        self.assertEqual(track['metadata_quality']['mood'],'unknown')
        self.assertTrue(matches_style_constraints(track,INTENT))

    def test_stale_cache_survives_timeout_without_retry(self):
        store=MemoryStore(); mb.discover(INTENT,store=store,opener=opener_for([recording()]))
        for key,value in store.values.items():
            if key.startswith('query:'): value['fetched_at']-=90000
        failing=MagicMock(side_effect=TimeoutError())
        result=mb.discover(INTENT,store=store,opener=failing)
        self.assertEqual(result.status,'stale_cache'); self.assertEqual(len(result.tracks),1)
        self.assertEqual(failing.call_count,1); self.assertIn('saved catalogue',result.warning)

    def test_429_obeys_retry_after_and_suppresses_next_request(self):
        store=MemoryStore(); opener=MagicMock(side_effect=HTTPError('url',429,'busy',{'Retry-After':'45'},io.BytesIO(b'')))
        result=mb.discover(INTENT,store=store,opener=opener)
        self.assertEqual(result.status,'upstream_unavailable');self.assertEqual(store.deferred,45)
        mb.discover(INTENT,store=store,opener=opener)
        self.assertEqual(opener.call_count,1)

    def test_storage_failure_prevents_unpaced_network_call(self):
        store=MemoryStore(); store.get=MagicMock(side_effect=StorageError())
        opener=opener_for([recording()]); result=mb.discover(INTENT,store=store,opener=opener)
        self.assertEqual(result.status,'unavailable');opener.assert_not_called()

    def test_empty_results_are_cached_and_malformed_results_are_not(self):
        store=MemoryStore(); opener=opener_for([])
        self.assertEqual(mb.discover(INTENT,store=store,opener=opener).status,'live')
        self.assertEqual(mb.discover(INTENT,store=store,opener=opener).status,'cache_hit')
        self.assertEqual(opener.call_count,1)
        bad=MemoryStore(); result=mb.discover(INTENT,store=bad,opener=opener_for([{'id':'made-up'}]))
        self.assertEqual(result.status,'unavailable');self.assertFalse(bad.values)

    def test_corrupt_cache_falls_back_without_returning_invalid_tracks(self):
        store=MemoryStore(); key='query:'+hashlib.sha256(mb.build_query(INTENT).encode()).hexdigest()
        store.values[key]={'tracks':[None],'fetched_at':time.time()}
        result=mb.discover(INTENT,store=store,opener=opener_for([recording()]))
        self.assertEqual(result.tracks,[]);self.assertEqual(result.status,'unavailable')

    def test_duplicate_recordings_and_known_seed_aliases_are_canonicalised(self):
        first=mb.normalise_recording(recording(1,title='Same',artist='Same Artist'))
        second=mb.normalise_recording(recording(2,title='Same',artist='Same Artist'))
        self.assertEqual(len(mb.canonical_candidates([first,second])),1)
        known=next(t for t in CATALOG if t.get('source')=='musicbrainz')
        self.assertEqual(mb.canonical_candidates([dict(known,energy=5)])[0],known)

    def test_dynamic_ranking_is_isolated_across_requests(self):
        before_ids=set(TRACK_BY_ID);before_vectors=set(TRACK_VECTORS)
        def run(n):
            tracks=[mb.normalise_recording(recording(n+i)) for i in range(5)]
            response=generate_recommendations(RecommendRequest(prompt='instrumental jazz'),INTENT,tracks)
            return {r.id for r in response.recommendations},{t['id'] for t in tracks}
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(run,[100,200]))
        for actual,expected in results:self.assertEqual(actual,expected)
        self.assertEqual(set(TRACK_BY_ID),before_ids);self.assertEqual(set(TRACK_VECTORS),before_vectors)

    def test_genre_and_descriptor_remain_required_for_local_fallback(self):
        wrong=mb.normalise_recording(recording(tags=('rock','instrumental')))
        vocal=mb.normalise_recording(recording(tags=('jazz',)))
        self.assertFalse(matches_style_constraints(wrong,INTENT))
        self.assertFalse(matches_style_constraints(vocal,INTENT))

    def test_service_interprets_before_fetch_and_preserves_discovery_evidence(self):
        order=[]; provider=MagicMock()
        provider.interpret.side_effect=lambda *a:(order.append('interpret') or dict(INTENT),{'calls':1})
        def retrieve(signals,seeds):
            order.append('retrieve');self.assertEqual(signals,INTENT)
            return mb.DiscoveryResult(tracks=[mb.normalise_recording(recording(i)) for i in range(1,6)],status='live',network_calls=1)
        result=recommend_playlist(RecommendRequest(prompt='instrumental jazz'),provider,retrieve)
        self.assertEqual(order,['interpret','retrieve']);self.assertEqual(len(result.recommendations),5)
        self.assertEqual(result.verification['discovery']['network_calls'],1)

    def test_unavailable_provider_returns_saved_catalogue_results(self):
        provider=MagicMock();provider.interpret.return_value=(dict(INTENT,min_year=None,max_year=None),{'calls':1})
        result=recommend_playlist(RecommendRequest(prompt='instrumental jazz'),provider,
            lambda *a:mb.DiscoveryResult(status='unavailable',warning='Live music search is temporarily unavailable.'))
        self.assertTrue(result.recommendations)
        self.assertTrue(all(t.id in TRACK_BY_ID for t in result.recommendations))
        self.assertEqual(result.verification['discovery']['status'],'unavailable')
        self.assertIn('unavailable',result.warnings[0])

    def test_persistent_new_track_supports_spotify_after_store_recreation(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'NEXTTRACK_DISCOVERY_CACHE_DIR':directory}):
            store=DiscoveryStore(directory=directory); track=mb.normalise_recording(recording())
            store.put_many([('track:'+track['id'],track)],3600)
            self.assertEqual(DiscoveryStore(directory=directory).get('track:'+track['id']),track)
            response=TestClient(app).get('/spotify/open/'+track['id'],follow_redirects=False)
            self.assertEqual(response.status_code,303);self.assertIn('open.spotify.com/search/',response.headers['location'])

    def test_sqlite_rate_gate_is_shared_across_clients_and_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            def reserve(_):return DiscoveryStore(directory=directory).reserve()
            with ThreadPoolExecutor(max_workers=6) as pool: self.assertEqual(sum(pool.map(reserve,range(6))),1)
            DiscoveryStore(directory=directory).defer(30)
            self.assertFalse(DiscoveryStore(directory=directory).reserve())

    def test_online_without_shared_storage_fails_closed(self):
        with patch.dict(os.environ,{'VERCEL':'1','UPSTASH_REDIS_REST_URL':'','UPSTASH_REDIS_REST_TOKEN':''}):
            with self.assertRaises(StorageError):discovery_store()

    def test_redis_gate_is_shared_across_storage_prefixes(self):
        clients=[MagicMock(),MagicMock()]
        for client in clients:
            client.command.return_value='OK'; self.assertTrue(DiscoveryStore(client).reserve())
            self.assertEqual(client.command.call_args.args,('SET',GATE,'1','NX','PX',1100))

    def test_latin_pop_does_not_request_latin_language(self):
        self.assertIsNone(parse_prompt('summer latin pop songs')['preferred_language'])

    def test_outage_without_saved_matches_reports_unavailability(self):
        from fastapi import HTTPException
        provider=MagicMock();provider.interpret.return_value=(dict(INTENT,min_year=3000,max_year=3001),{'calls':1})
        with self.assertRaises(HTTPException) as error:
            recommend_playlist(RecommendRequest(prompt='jazz in year 3000'),provider,
                lambda *a:mb.DiscoveryResult(status='unavailable',warning='Live music search is temporarily unavailable.'))
        self.assertEqual(error.exception.status_code,503)
        self.assertIn('saved catalogue',error.exception.detail)
