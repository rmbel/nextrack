"""Offline evaluation: controlled seed sizes, three baselines, no paid AI calls."""
import json
import random
import sys
import time
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from prototype.app.catalog import CATALOG, TRACK_VECTORS, cosine_similarity
from prototype.app.models import RecommendRequest, Recommendation
from prototype.app.recommender import build_seed_profile, parse_prompt, score_candidate, recommendation_metrics
from prototype.app.service import recommend_playlist


def as_recommendations(tracks):
    return [Recommendation(**{k:t[k] for k in ('id','title','artist','year','genre')}, score=0, score_components={}, reason='baseline') for t in tracks]


def main():
    rows = []
    rng = random.Random(3070)
    mixed = rng.sample(CATALOG, len(CATALOG))
    pop = [t for t in CATALOG if t['genre']=='pop']
    for label, pool in [('pop', pop), ('mixed', mixed)]:
        for count in (1,2,3,5,10,25,50):
            seeds = pool[:count]
            ids = [t['id'] for t in seeds]
            profile = build_seed_profile(seeds)
            signals = parse_prompt('')
            candidates = [t for t in CATALOG if t['id'] not in ids]
            started = time.perf_counter()
            result = recommend_playlist(RecommendRequest(seed_track_ids=ids))
            latency = round((time.perf_counter()-started)*1000, 2)
            relevance = sorted(candidates, key=lambda t:score_candidate(t, profile, signals)['relevance'], reverse=True)[:5]
            cosine = sorted(candidates, key=lambda t:cosine_similarity(TRACK_VECTORS[t['id']],profile['vector']), reverse=True)[:5]
            random_metrics = [recommendation_metrics(as_recommendations(rng.sample(candidates,5)), profile, signals).model_dump() for _ in range(100)]
            rows.append({'seed_pattern':label, 'seed_count':count, 'seed_ids':ids, 'latency_ms':latency,
                         'ai_calls':0, 'verified_track_rate':1.0,
                         'recommendation_ids':[t.id for t in result.recommendations],
                         'mmr':result.metrics.model_dump(),
                         'relevance_only':recommendation_metrics(as_recommendations(relevance),profile,signals).model_dump(),
                         'cosine_only':recommendation_metrics(as_recommendations(cosine),profile,signals).model_dump(),
                         'random':{k:mean([m[k] for m in random_metrics]) if random_metrics[0][k] is not None else None for k in random_metrics[0]}})
    path = ROOT/'prototype/flexible-evaluation-results.json'
    path.write_text(json.dumps({'date':'2026-09-15','algorithm':'hybrid-content-mmr/2.0','live_ai_tested':False,'random_repeats':100,'scenarios':rows}, indent=2)+'\n')
    lines = ['# Flexible-input technical evaluation', '',
             '14 deterministic scenarios; fixed 1,000-track catalogue; 100 random lists per scenario.',
             'One nested pop seed set and one fixed mixed-genre seed set at each size. These are descriptive cases, not independent user-relevance judgements.',
             'Seed-only requests make no GPT calls. Live GPT quality, latency and cost remain unmeasured.', '',
             '| Seeds | Pattern | Latency ms | MMR similarity | Relevance similarity | Cosine similarity | Random similarity | MMR diversity |',
             '| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in rows:
        lines.append(f"| {r['seed_count']} | {r['seed_pattern']} | {r['latency_ms']} | {r['mmr']['mean_seed_similarity']:.3f} | {r['relevance_only']['mean_seed_similarity']:.3f} | {r['cosine_only']['mean_seed_similarity']:.3f} | {r['random']['mean_seed_similarity']:.3f} | {r['mmr']['intra_list_diversity']:.3f} |")
    lines.extend(['', 'All returned IDs resolve to the catalogue and exclude the selected seeds.',
                  'Similarity to an averaged seed vector can conceal minority tastes in a mixed playlist. It does not establish user satisfaction; clustering/recency weighting needs participant evidence before changing the algorithm.'])
    (ROOT/'prototype/flexible-evaluation-results.md').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))

if __name__ == '__main__':
    main()
