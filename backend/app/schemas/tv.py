from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TVMediaCard(BaseModel):
    id: str
    filename: str
    caption: str = ""
    country: str | None = None
    city: str | None = None
    place: str | None = None
    thumbnail_url: str | None = None
    date_taken: datetime | None = None
    trip_name: str | None = None


class TVTripSummary(BaseModel):
    trip_name: str
    count: int
    latest_date: datetime | None = None
    cover: TVMediaCard | None = None



class TVHomeResponse(BaseModel):
    recent_photos: list[TVMediaCard] = Field(default_factory=list)
    recent_trips: list[TVTripSummary] = Field(default_factory=list)
    trips_has_more: bool = False


class TVPlaylistResponse(BaseModel):
    playlist_id: str
    title: str
    subtitle: str | None = None
    total_count: int
    items: list[TVMediaCard] = Field(default_factory=list)
