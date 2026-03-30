from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class AnalysisJob(BaseModel):
    id: str
    filename: str
    source_path: str
    checksum: str
    stream_url: str
    thumbnail_url: str | None = None
    date_taken: datetime | None = None
    width: int | None = None
    height: int | None = None
    camera_model: str | None = None
    country: str | None = None
    region: str | None = None
    city: str | None = None
    place: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    analysis_status: str | None = None
    analysis_attempts: int = 0
    analysis_model: str | None = None
    analysis_version: str | None = None


class AnalysisJobListResponse(BaseModel):
    jobs: list[AnalysisJob] = Field(default_factory=list)


class AnalysisClaimRequest(BaseModel):
    worker_id: str
    analysis_model: str = "microsoft/Florence-2-base-ft"
    analysis_version: str = "florence2-base-ft-v1"


class AnalysisResultRequest(BaseModel):
    worker_id: str
    status: Literal["completed", "failed"] = "completed"
    analysis_model: str = "microsoft/Florence-2-base-ft"
    analysis_version: str = "florence2-base-ft-v1"
    caption_ai: str | None = None
    caption_dense: str | None = None
    ocr_text: str | None = None
    tags_json: list[str] = Field(default_factory=list)
    objects_json: list[str] = Field(default_factory=list)
    landmarks_json: list[str] = Field(default_factory=list)
    scene_json: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class AnalysisResultResponse(BaseModel):
    media_id: str
    analysis_status: str
    analysis_completed_at: datetime | None = None


class AnalysisHeartbeatResponse(BaseModel):
    worker_id: str
    updated_jobs: int
    timestamp: datetime


class AnalysisStatsResponse(BaseModel):
    pending: int = 0
    claimed: int = 0
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    active_workers: int = 0
    stale_claims: int = 0
