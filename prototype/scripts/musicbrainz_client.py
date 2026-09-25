"""Polite MusicBrainz transport: paced requests, bounded retry and successful-response cache."""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class MusicBrainzError(RuntimeError):
    def __init__(self, category, status=None, retry_after=None):
        self.category, self.status, self.retry_after = category, status, retry_after
        super().__init__(f'MusicBrainz {category}' + (f' (HTTP {status})' if status else ''))


def retry_delay(value, now=None):
    if not value: return None
    try: return max(0.0, float(value))
    except ValueError:
        try: return max(0.0, parsedate_to_datetime(value).timestamp() - (time.time() if now is None else now))
        except (ValueError, TypeError, OverflowError): return None


def configured_user_agent():
    settings = {}
    path = Path(__file__).resolve().parents[1] / '.env'
    if path.exists():
        for line in path.read_text().splitlines():
            name, sep, value = line.partition('=')
            if sep and name in {'MUSICBRAINZ_USER_AGENT', 'MUSICBRAINZ_CONTACT'}:
                settings[name] = value.strip()
    user_agent = os.getenv('MUSICBRAINZ_USER_AGENT', settings.get('MUSICBRAINZ_USER_AGENT', ''))
    if user_agent: return user_agent
    contact = os.getenv('MUSICBRAINZ_CONTACT', settings.get('MUSICBRAINZ_CONTACT', ''))
    return f'NextTrack/0.8 ({contact or "University of London CM3070 student project"})'


class MusicBrainzClient:
    def __init__(self, cache_dir=None, opener=urlopen, clock=time.monotonic, sleep=time.sleep,
                 user_agent=None, attempts=3, shared_rate_path=None):
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self.opener, self.clock, self.sleep = opener, clock, sleep
        self.user_agent = user_agent or configured_user_agent()
        self.attempts = attempts
        self.shared_rate_path = Path(shared_rate_path) if shared_rate_path else None
        self.next_request = 0.0
        self.events = []

    def get(self, entity, parameters):
        if entity != 'recording':
            raise ValueError('Unsupported MusicBrainz entity')
        url = 'https://musicbrainz.org/ws/2/' + entity + '/?' + urlencode({**parameters, 'fmt':'json'})
        cache = self.cache_dir / (hashlib.sha256(url.encode()).hexdigest()+'.json') if self.cache_dir else None
        if cache and cache.exists() and time.time()-cache.stat().st_mtime < 86400:
            try:
                data = json.loads(cache.read_text())
                if isinstance(data,dict) and isinstance(data.get('recordings'),list):
                    self.events.append({'event':'cache_hit'})
                    return data
            except (ValueError, OSError): pass
        for attempt in range(self.attempts):
            self.sleep(max(0, self.next_request-self.clock()))
            self.next_request = self.clock()+1.1
            if self.shared_rate_path:
                self.shared_rate_path.parent.mkdir(parents=True, exist_ok=True)
                with self.shared_rate_path.open("a+") as rate_file:
                    fcntl.flock(rate_file.fileno(), fcntl.LOCK_EX)
                    rate_file.seek(0)
                    try: last_start = float(rate_file.read() or "0")
                    except ValueError: last_start = 0.0
                    self.sleep(max(0.0, last_start+1.1-time.time()))
                    rate_file.seek(0); rate_file.truncate()
                    rate_file.write(str(time.time())); rate_file.flush()
                    fcntl.flock(rate_file.fileno(), fcntl.LOCK_UN)
            error = None
            wait = None
            try:
                request = Request(url, headers={'User-Agent':self.user_agent,'Accept':'application/json'})
                with self.opener(request,timeout=20) as response:
                    data = json.load(response)
                if not isinstance(data,dict) or not isinstance(data.get('recordings'),list):
                    raise MusicBrainzError('invalid_response')
                if cache:
                    cache.parent.mkdir(parents=True,exist_ok=True)
                    tmp=cache.with_suffix('.tmp'); tmp.write_text(json.dumps(data)); tmp.replace(cache)
                self.events.append({'event':'success','attempt':attempt+1})
                return data
            except HTTPError as exc:
                wait = retry_delay(exc.headers.get('Retry-After') if exc.headers else None)
                status = exc.code
                exc.close()
                if status not in (429,500,502,503,504):
                    raise MusicBrainzError('request_rejected',status) from None
                error = MusicBrainzError('temporarily_unavailable',status,wait)
            except (URLError, TimeoutError):
                error = MusicBrainzError('connection_failure')
            except (ValueError, UnicodeError):
                raise MusicBrainzError('invalid_json') from None
            self.events.append({'event':'retryable_failure','attempt':attempt+1,'status':error.status,
                                'category':error.category,'retry_after':wait})
            if attempt+1 == self.attempts or wait is not None and wait > 60:
                raise error
            # Respect server guidance; otherwise use exponential backoff of 5, 10 seconds.
            self.sleep(max(1.1,wait if wait is not None else 5*(2**attempt)))
        raise MusicBrainzError('temporarily_unavailable')

    def recordings(self, genre, offset=0, limit=100):
        return self.get('recording', {'query':f'tag:"{genre}" AND status:official AND NOT video:true',
                                      'limit':limit,'offset':offset})
