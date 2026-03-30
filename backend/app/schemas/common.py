from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Explanation(BaseModel):
    caption_match: str | None = None
    transcript_match: str | None = None
    location_match: str | None = None
    visual_similarity: float | None = None
    matched_tags: list[str] = Field(default_factory=list)


class SegmentSummary(BaseModel):
    id: str
    segment_type: str
    timestamp_start: float | None = None
    timestamp_end: float | None = None
    caption: str | None = None
    content_text: str | None = None
    thumbnail_url: str | None = None


class LibraryYear(BaseModel):
    year: int
    count: int


class TimelineMonth(BaseModel):
    key: str
    year: int
    month: int
    label: str
    short_label: str
    count: int


class MediaCard(BaseModel):
    id: str
    filename: str
    source_path: str
    media_type: str
    caption: str
    tags: list[str]
    country: str | None = None
    region: str | None = None
    city: str | None = None
    place: str | None = None
    landmark: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    thumbnail_url: str | None = None
    date_taken: datetime | None = None
    duration: float | None = None
    transcript: str | None = None
    ocr_text: str | None = None
    explanation: Explanation | None = None
    score: float | None = None
    trip_name: str | None = None
    analysis_status: str | None = None
    analysis_completed_at: datetime | None = None
    analysis_model: str | None = None
    analysis_version: str | None = None


class YearMediaGroup(BaseModel):
    year: int
    count: int
    items: list[MediaCard] = Field(default_factory=list)


class TripSummary(BaseModel):
    trip_name: str
    count: int
    latest_date: datetime | None = None
    cover: MediaCard | None = None


class MediaDetail(MediaCard):
    objects: list[str] = Field(default_factory=list)
    people: list[str] = Field(default_factory=list)
    caption_ai: str | None = None
    caption_dense: str | None = None
    tags_json: list[str] = Field(default_factory=list)
    objects_json: list[str] = Field(default_factory=list)
    landmarks_json: list[str] = Field(default_factory=list)
    scene_json: dict = Field(default_factory=dict)
    analysis_attempts: int = 0
    analysis_error: str | None = None
    metadata_json: dict = Field(default_factory=dict)
    segments: list[SegmentSummary] = Field(default_factory=list)
