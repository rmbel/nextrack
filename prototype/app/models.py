from __future__ import annotations

from typing import Any, Optional, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class SearchResult(BaseModel):
    id: str
    title: str
    artist: str
    year: Optional[int]
    genre: str
    source: str = "catalogue"
    source_url: Optional[str] = None


class RecommendRequest(BaseModel):
    seed_track_ids: list[str] = Field(default_factory=list, max_length=50)
    prompt: str = Field(default="", max_length=500)
    limit: int = Field(default=5, ge=1, le=10)
    strategy: Literal["auto"] = "auto"

    @field_validator("seed_track_ids", mode="before")
    @classmethod
    def unique_seeds(cls, value):
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return list(dict.fromkeys(value))
        return value

    @field_validator("prompt")
    @classmethod
    def strip_prompt(cls, value):
        return value.strip()

    @model_validator(mode="after")
    def require_input(self):
        if not self.seed_track_ids and not self.prompt:
            raise ValueError("Add at least one song or a playlist prompt.")
        return self


class Recommendation(BaseModel):
    id: str
    title: str
    artist: str
    year: Optional[int]
    genre: str
    source: str = "catalogue"
    source_url: Optional[str] = None
    score: float
    score_components: dict[str, float]
    reason: str


class RecommendationMetrics(BaseModel):
    mean_seed_similarity: Optional[float]
    prompt_adherence: Optional[float]
    intra_list_diversity: float
    artist_diversity: float


class RecommendationResponse(BaseModel):
    request_id: str
    strategy_used: Literal["recommendation", "ai", "hybrid"] = "recommendation"
    strategy_reason: str = "Seed-based catalogue ranking."
    seed_count: int = 0
    prompt_interpretation: dict[str, Any] = Field(default_factory=dict)
    fallback_used: bool = False
    warnings: list[str] = Field(default_factory=list)
    verification: dict[str, Any] = Field(default_factory=dict)
    ai: Optional[dict[str, Any]] = None
    latency_ms: float = 0
    algorithm: dict[str, Any]
    seed_tracks: list[SearchResult]
    prompt: str
    prompt_signals: dict[str, Any]
    recommendations: list[Recommendation]
    metrics: RecommendationMetrics


class FeedbackRequest(BaseModel):
    request_id: str = Field(min_length=6, max_length=64)
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=500)


class FeedbackResponse(BaseModel):
    status: str
