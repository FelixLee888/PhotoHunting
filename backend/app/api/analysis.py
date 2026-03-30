from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_analysis_queue_service
from app.db.session import get_db
from app.schemas.analysis import (
    AnalysisClaimRequest,
    AnalysisHeartbeatResponse,
    AnalysisJob,
    AnalysisJobListResponse,
    AnalysisResultRequest,
    AnalysisResultResponse,
    AnalysisStatsResponse,
)
from app.services.analysis_queue import AnalysisQueueService

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.get("/jobs", response_model=AnalysisJobListResponse)
def list_analysis_jobs(
    request: Request,
    status: str = Query(default="pending"),
    limit: int = Query(default=10, ge=1, le=100),
    analysis_model: str | None = None,
    analysis_version: str | None = None,
    db: Session = Depends(get_db),
    service: AnalysisQueueService = Depends(get_analysis_queue_service),
):
    return service.list_jobs(
        db,
        request,
        status=status,
        limit=limit,
        analysis_model=analysis_model,
        analysis_version=analysis_version,
    )


@router.post("/jobs/{media_id}/claim", response_model=AnalysisJob)
def claim_analysis_job(
    media_id: str,
    body: AnalysisClaimRequest,
    request: Request,
    db: Session = Depends(get_db),
    service: AnalysisQueueService = Depends(get_analysis_queue_service),
):
    return service.claim_job(db, request, media_id=media_id, body=body)


@router.post("/results/{media_id}", response_model=AnalysisResultResponse)
def submit_analysis_result(
    media_id: str,
    body: AnalysisResultRequest,
    db: Session = Depends(get_db),
    service: AnalysisQueueService = Depends(get_analysis_queue_service),
):
    return service.submit_result(db, media_id=media_id, body=body)


@router.post("/heartbeat/{worker_id}", response_model=AnalysisHeartbeatResponse)
def heartbeat(
    worker_id: str,
    db: Session = Depends(get_db),
    service: AnalysisQueueService = Depends(get_analysis_queue_service),
):
    return service.heartbeat(db, worker_id=worker_id)


@router.get("/stats", response_model=AnalysisStatsResponse)
def analysis_stats(
    db: Session = Depends(get_db),
    service: AnalysisQueueService = Depends(get_analysis_queue_service),
):
    return service.stats(db)
