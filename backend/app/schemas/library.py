from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import LibraryYear


class LibraryStatsResponse(BaseModel):
    indexed_media: int
    indexed_images: int
    indexed_videos: int
    mapped_media: int
    trip_routes: int
    last_indexed_at: datetime | None = None
    available_years: list[LibraryYear] = Field(default_factory=list)


class ScanStatusResponse(BaseModel):
    running: bool
    status: str
    pid: int | None = None
    mode: str | None = None
    event: str | None = None
    scanned: int | None = None
    created: int | None = None
    updated: int | None = None
    skipped: int | None = None
    errors: int | None = None
    last_path: str | None = None
    log_updated_at: datetime | None = None
    detail: str | None = None
