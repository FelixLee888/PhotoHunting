from __future__ import annotations

from pydantic import BaseModel, Field


class ScanRequest(BaseModel):
    paths: list[str] = Field(default_factory=list)


class ScanResponse(BaseModel):
    scanned: int
    created: int
    updated: int
    deleted: int
    skipped: int
    warnings: list[str] = Field(default_factory=list)

