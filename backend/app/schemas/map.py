from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class MapPoint(BaseModel):
    media_id: str
    latitude: float
    longitude: float
    thumbnail: str | None = None
    caption: str | None = None
    date: datetime | None = None
    media_type: str
    tags: list[str] = Field(default_factory=list)
    location: str | None = None
    trip_name: str | None = None


class MapRoute(BaseModel):
    trip_name: str
    media_ids: list[str]
    coordinates: list[list[float]]


class MapResponse(BaseModel):
    points: list[MapPoint]
    routes: list[MapRoute] = Field(default_factory=list)

