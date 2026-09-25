"""Exercise the actual ASGI request boundary without extra test dependencies."""
import asyncio
import json
import unittest
from unittest.mock import patch
from prototype.app.main import app
from prototype.app.catalog import CATALOG


async def post(payload):
    output = []
    body = json.dumps(payload).encode()
    scope = {'type':'http', 'asgi':{'version':'3.0'}, 'http_version':'1.1', 'method':'POST',
             'scheme':'http','path':'/recommend','raw_path':b'/recommend','query_string':b'',
             'root_path':'','headers':[(b'content-type',b'application/json')],
             'server':('test',80),'client':('test',123)}
    async def receive():
        return {'type':'http.request','body':body,'more_body':False}
    async def send(message):
        output.append(message)
    await app(scope,receive,send)
    return output[0]['status'], json.loads(b''.join(m.get('body',b'') for m in output))


class APITests(unittest.TestCase):
    def test_validation_through_http(self):
        for payload in ({}, {'prompt':' '}, {'seed_track_ids':[t['id'] for t in CATALOG[:51]]},
                        {'prompt':'bachata','limit':11}, {'prompt':'bachata','strategy':'ai'}):
            self.assertEqual(asyncio.run(post(payload))[0],422)

    def test_http_unique_seeds_and_metadata(self):
        status, body = asyncio.run(post({'seed_track_ids':[CATALOG[0]['id']]*3}))
        self.assertEqual(status,200)
        self.assertEqual(body['seed_count'],1)
        self.assertEqual(body['strategy_used'],'recommendation')
        self.assertEqual(body['verification']['verified_track_rate'],1)

    def test_http_prompt_only_missing_key(self):
        with patch.dict('os.environ',{'OPENAI_API_KEY':''}):
            status, body = asyncio.run(post({'prompt':'bachata'}))
        self.assertEqual(status,503)
        self.assertIn('Retry', body['detail'])

    def test_http_unknown_seed(self):
        self.assertEqual(asyncio.run(post({'seed_track_ids':['missing']}))[0],400)
