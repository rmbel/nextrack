from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .catalog import (
    CATALOG,
    FEEDBACK_PATH,
    TRACK_BY_ID,
    WEB_DIR,
    search_catalog,
    track_to_search_result,
)
from .service import recommend_playlist
from .feedback_store import save_feedback_record
from .redis_rest import StorageError
from .musicbrainz import enabled as online_catalogue_enabled

from .models import (
    FeedbackRequest,
    FeedbackResponse,
    RecommendRequest,
    Recommendation,
    RecommendationResponse,
    SearchResult,
)
from .recommender import (
    ALGORITHM_NAME,
    ALGORITHM_VERSION,
    DIVERSITY_LAMBDA,
    build_seed_profile,
    generate_recommendations,
    parse_prompt,
    recommendation_metrics,
)


logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="NextTrack Prototype API",
    description="Recommends verified tracks from up to 50 songs, a prompt, or both.",
    version="0.6.0",
)
from .spotify import router as spotify_router
app.include_router(spotify_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "catalogue_size": len(CATALOG),
        "algorithm": f"{ALGORITHM_NAME}/{ALGORITHM_VERSION}",
        "online_catalogue": {"provider": "musicbrainz", "enabled": online_catalogue_enabled()},
    }


@app.get("/search", response_model=list[SearchResult])
def search(q: str = "") -> list[SearchResult]:
    return [track_to_search_result(item) for item in search_catalog(q)]


@app.post("/recommend", response_model=RecommendationResponse)
def recommend(request: RecommendRequest) -> RecommendationResponse:
    return recommend_playlist(request)


@app.post("/feedback", response_model=FeedbackResponse, status_code=201)
def save_feedback(feedback: FeedbackRequest) -> FeedbackResponse:
    record = {
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        **feedback.model_dump(),
    }
    try:
        save_feedback_record(record, FEEDBACK_PATH)
    except StorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    return FeedbackResponse(status="recorded")
