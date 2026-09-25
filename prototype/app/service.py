"""Coordinate routing, interpretation, verified ranking and safe fallback."""
import hashlib
import json
import logging
import time
from fastapi import HTTPException
from .ai_provider import AIError, OpenAIProvider
from .catalog import TRACK_BY_ID
from .recommender import generate_recommendations, parse_prompt
from .router import choose_strategy
from . import musicbrainz

logger = logging.getLogger('nexttrack.requests')


def recommend_playlist(request, provider=None, candidate_provider=None):
    started = time.perf_counter()
    seed_lookup = {i: TRACK_BY_ID.get(i) or musicbrainz.cached_track(i) for i in request.seed_track_ids}
    unknown = [i for i in request.seed_track_ids if not seed_lookup[i]]
    if unknown:
        raise HTTPException(400, detail='Unknown track id: ' + unknown[0])
    strategy, reason = choose_strategy(len(request.seed_track_ids), request.prompt)
    signals, ai, warnings, fallback = None, None, [], False
    if strategy != 'recommendation':
        provider = provider or OpenAIProvider()
        try:
            signals, ai = provider.interpret(request.prompt, list(seed_lookup.values()))
            warnings.extend(signals.get('limitations', []))
        except AIError as exc:
            category = str(exc)
            logger.info('ai_failure category=%s fallback=%s latency_ms=%.1f',
                        category, bool(request.seed_track_ids), (time.perf_counter()-started)*1000)
            if not request.seed_track_ids:
                raise HTTPException(503, detail='Prompt discovery is temporarily unavailable. Retry or add a song.',
                                    headers={'Retry-After': '30'}) from None
            fallback = True
            strategy = 'recommendation'
            reason = 'Prompt interpretation was unavailable; recommendations use your selected songs and simple prompt cues.'
            warnings.append('Complex prompt details could not be applied.')
            ai = {'provider': 'openai', 'model': provider.model, 'error': category,
                  'calls': 0 if category == 'not_configured' else 1}
    discovery = musicbrainz.DiscoveryResult(status='disabled' if not musicbrainz.enabled() else 'not_requested')
    if request.prompt and not fallback and (candidate_provider is not None or musicbrainz.enabled()):
        discovery = (candidate_provider or musicbrainz.discover)(signals or parse_prompt(request.prompt), list(seed_lookup.values()))
    candidates = {t['id']: t for t in discovery.tracks}
    candidates.update({i: t for i, t in seed_lookup.items() if i not in TRACK_BY_ID})
    try:
        response = generate_recommendations(request, signals=signals, additional_tracks=list(candidates.values()))
    except HTTPException as exc:
        if exc.status_code == 422 and discovery.warning:
            raise HTTPException(503, detail='Live music search is temporarily unavailable and the saved catalogue has no matching songs. Try again or broaden your request.',
                                headers={'Retry-After': '30'}) from None
        raise
    response.verification['discovery'] = discovery.metadata()
    if discovery.warning:
        warnings.append(discovery.warning)
    response.strategy_used = strategy
    response.strategy_reason = reason
    response.ai = ai
    response.fallback_used = fallback
    if response.prompt_interpretation.get("prefer_familiar"):
        warnings.append("Popularity is available for part of the catalogue only, using historical Free Music Archive listening counts.")
    response.warnings = warnings
    response.request_id = hashlib.sha256(json.dumps({
        'base': response.request_id, 'strategy': strategy, 'fallback': fallback,
        'interpretation': signals, 'model': (ai or {}).get('model'),
    }, sort_keys=True).encode()).hexdigest()[:16]
    response.latency_ms = round((time.perf_counter()-started)*1000, 2)
    if signals:
        response.prompt_interpretation = {**signals, "prefer_familiar": response.prompt_interpretation.get("prefer_familiar", False)}
    if len(response.recommendations) < request.limit:
        response.warnings.append('The catalogue has fewer matching tracks than requested.')
    logger.info('recommendation strategy=%s seed_count=%d fallback=%s latency_ms=%s ai_calls=%s',
                strategy, response.seed_count, fallback, response.latency_ms, (ai or {}).get('calls', 0))
    return response
