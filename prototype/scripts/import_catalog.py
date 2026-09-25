"""Import verified public metadata into a separate, reproducible catalogue extension.

Original catalogue is never overwritten. Provider failures leave the last good snapshot intact.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

try:
    from .musicbrainz_client import MusicBrainzClient, MusicBrainzError
except ImportError:
    from musicbrainz_client import MusicBrainzClient, MusicBrainzError

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'data' / 'catalog-extension.json'
GENRES = ['bachata','reggaeton','salsa','merengue','pop','rock','indie pop','hip hop','r&b','jazz','country','classical','afrobeats','k-pop','j-pop','dance']
ARTISTS = ['Romeo Santos','Prince Royce','Aventura','Juan Luis Guerra','Dani J','Leslie Grace',
           'Manuel Turizo','Shakira','Bad Bunny','Karol G','Marc Anthony','Daddy Yankee',
           'Taylor Swift','Dua Lipa','The Weeknd','Adele','Billie Eilish','Bruno Mars',
           'Coldplay','Radiohead','Arctic Monkeys','Fleetwood Mac','Queen','The Beatles',
           'Kendrick Lamar','Drake','SZA','Frank Ocean','Beyonce','Rihanna',
           'Miles Davis','John Coltrane','Ella Fitzgerald','Nina Simone','Norah Jones',
           'Dolly Parton','Chris Stapleton','Kacey Musgraves','Willie Nelson',
           'Burna Boy','Wizkid','Rema','BTS','Blackpink','Twice','YOASOBI','Fujii Kaze',
           'Daft Punk','Calvin Harris','Avicii','Ludovico Einaudi','Max Richter','Olafur Arnalds',
           'Clairo','Men I Trust','Tame Impala','Lana Del Rey','Sabrina Carpenter','Chappell Roan']


def identity(title, artist):
    def normal(value):
        value = ''.join(c for c in unicodedata.normalize('NFKD',value.casefold()) if not unicodedata.combining(c))
        return ' '.join(re.sub(r'[^\w\s]', ' ', value).split())
    return normal(artist)+'::'+normal(title)


def apple_track(item):
    title, artist = item.get('trackName','').strip(), item.get('artistName','').strip()
    year = item.get('releaseDate','')[:4]
    if item.get('kind') != 'song' or not title or not artist or not year.isdigit() or not item.get('trackId'):
        return None
    label = item.get('primaryGenreName','').casefold()
    aliases = {'pop latino':'latin pop','urbano latino':'reggaeton','hip-hop/rap':'hip hop',
               'r&b/soul':'r&b','alternative':'alternative','singer/songwriter':'folk',
               'música tropical':'tropical','musica tropical':'tropical','electronic':'electronic'}
    genre = aliases.get(label, label)
    if not genre: return None
    return {'id': 'itunes:'+str(item['trackId']), 'title':title, 'artist':artist, 'year':int(year),
            'genre':genre, 'subgenres':[], 'tags':[], 'language':'unknown', 'energy':None,
            'source':'itunes', 'source_url':item.get('trackViewUrl',''),
            'source_id':str(item['trackId']), 'metadata_quality':{'genre':'provider','language':'unknown','energy':'unknown','mood':'unknown'}}


def musicbrainz_track(item):
    if item.get('video') is True: return None
    title = item.get('title','').strip()
    artist = ''.join(c.get('name',c.get('artist',{}).get('name',''))+c.get('joinphrase','')
                     for c in item.get('artist-credit',[]) if isinstance(c,dict)).strip()
    year = item.get('first-release-date','')[:4]
    raw_tags = item.get('genres', []) + item.get('tags', [])
    tags = list(dict.fromkeys(t['name'].casefold().strip() for t in sorted(raw_tags,key=lambda t:t.get('count',0),reverse=True) if t.get('name')))
    genres = [g for g in tags if g in GENRES or any(g.endswith(' '+base) for base in GENRES)]
    if not title or not artist or not year.isdigit() or not genres or not item.get('id'):
        return None
    return {'id':'musicbrainz:'+item['id'],'title':title,'artist':artist,'year':int(year),
            'genre':genres[0], 'subgenres':genres[1:4],'tags':[], 'language':'unknown','energy':None,
            'source':'musicbrainz','source_url':'https://musicbrainz.org/recording/'+item['id'],
            'source_id':item['id'],'metadata_quality':{'genre':'community-tag','language':'unknown','energy':'unknown','mood':'unknown'}}


def fetch(url, user_agent):
    with urlopen(Request(url,headers={'User-Agent':user_agent,'Accept':'application/json'}),timeout=25) as response:
        return json.load(response)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--provider', choices=['musicbrainz','itunes'],required=True)
    parser.add_argument('--target',type=int)
    parser.add_argument('--genres',nargs='+',choices=GENRES,default=GENRES)
    args=parser.parse_args()
    baseline=json.loads((ROOT/'data/catalog.json').read_text())
    extension=json.loads(OUTPUT.read_text()) if OUTPUT.exists() else []
    seen={identity(t['title'],t['artist']) for t in baseline+extension}
    seen_ids={t['id'] for t in baseline+extension}
    target = args.target if args.target is not None else (len(seen_ids)+1000 if args.provider == 'musicbrainz' else 5000)
    before_extension_count = len(extension)
    client = MusicBrainzClient(cache_dir=ROOT/'data/.musicbrainz-cache') if args.provider == 'musicbrainz' else None
    exhausted = set()
    report={'provider':args.provider,'started_at':datetime.now(timezone.utc).isoformat(),'before':len(seen_ids),'queries':[], 'failures':[]}
    queries = [(term, 0) for term in ARTISTS] if args.provider == 'itunes' else [(term, offset) for offset in range(0, 500, 100) for term in args.genres]
    failures=0
    for term, offset in queries:
        if term in exhausted: continue
        if len(seen_ids)>=target: break
        if args.provider=='itunes':
            url='https://itunes.apple.com/search?'+urlencode({'term':term,'entity':'song','attribute':'artistTerm','limit':200,'country':'US'})
            delay=3.2; converter=apple_track; field='results'
        else:
            url='https://musicbrainz.org/ws/2/recording/?'+urlencode({'query':f'tag:"{term}" AND status:official AND NOT video:true','limit':100,'offset':offset,'fmt':'json'})
            delay=1.1; converter=musicbrainz_track; field='recordings'
        started=time.monotonic()
        try:
            data = client.recordings(term, offset) if client else fetch(url, 'NextTrack/0.8')
            if client and (offset+len(data['recordings']) >= data.get('count', offset+len(data['recordings']))):
                exhausted.add(term)
            added=0
            for raw in data.get(field,[]):
                track=converter(raw)
                if not track or not 1900<=track['year']<=datetime.now().year: continue
                key=identity(track['title'],track['artist'])
                if key in seen or track['id'] in seen_ids: continue
                extension.append(track); seen.add(key); seen_ids.add(track['id']); added+=1
                if len(seen_ids)>=target: break
            report['queries'].append({'query':term,'offset':offset,'received':len(data.get(field,[])),'added':added})
            failures=0
            print(f'{args.provider}: {term}: +{added}; total {len(seen_ids)}',flush=True)
        except (HTTPError,URLError,TimeoutError,ValueError,MusicBrainzError) as exc:
            report['failures'].append({'query':term,'category':getattr(exc,'category',type(exc).__name__),'status':getattr(exc,'status',getattr(exc,'code',None)), 'retry_after':getattr(exc,'retry_after',None)})
            print(f'{args.provider}: request unavailable ({type(exc).__name__}); preserving prior data',flush=True)
            failures+=1
            if client or failures>=2: break
        time.sleep(max(0,delay-(time.monotonic()-started)))
    report['after']=len(seen_ids)
    report['target_reached']=len(seen_ids)>=target
    report['target'] = target
    if client: report['transport_events'] = client.events
    if len(extension) > before_extension_count:
        temp=OUTPUT.with_suffix('.tmp')
        temp.write_text(json.dumps(extension,ensure_ascii=False,indent=2)+'\n')
        temp.replace(OUTPUT)
        report['extension_sha256']=hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    (ROOT/'data'/f'import-{args.provider}-report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(f'Finished: {len(seen_ids)} total, {len(extension)} extension tracks; target reached: {report["target_reached"]}',flush=True)
    if report['failures'] and len(extension) == before_extension_count:
        raise SystemExit(1)

if __name__=='__main__': main()
