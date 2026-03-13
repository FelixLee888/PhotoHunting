from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from app.schemas.common import MediaCard


class SearchRequest(BaseModel):
    query: str = ""
    media_type: str | None = None
    tags: list[str] = Field(default_factory=list)
    country: str | None = None
    region: str | None = None
    city: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    bbox: str | None = None
    near_latitude: float | None = None
    near_longitude: float | None = None
    near_radius_km: float | None = None
    similar_to_id: str | None = None
    limit: int = 24


class SearchResponse(BaseModel):
    query: str
    interpreted_filters: dict
    explanation: str
    results: list[MediaCard]

