from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from fastapi import HTTPException, Request
from sqlalchemy.exc import OperationalError
from sqlalchemy import and_, case, distinct, func, or_, select, text, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import AnalysisStatus, MediaItem
from app.schemas.analysis import (
    AnalysisClaimRequest,
    AnalysisHeartbeatResponse,
    AnalysisJob,
    AnalysisJobListResponse,
    AnalysisResultRequest,
    AnalysisResultResponse,
    AnalysisStatsResponse,
)
from app.services.embeddings import build_embedding_provider
from app.services.vector_store import VectorStore


class AnalysisQueueService:
    def __init__(self, settings: Settings, vector_store: VectorStore) -> None:
        self.settings = settings
        self.vector_store = vector_store
        self.embedder = build_embedding_provider(settings)

    def list_jobs(
        self,
        db: Session,
        request: Request,
        *,
        status: str,
        limit: int,
        analysis_model: str | None = None,
        analysis_version: str | None = None,
    ) -> AnalysisJobListResponse:
        lookahead_limit = min(max(limit * 8, 24), 80)
        jobs = self._select_jobs(
            db,
            request,
            clause=MediaItem.analysis_status.in_([AnalysisStatus.PENDING.value, AnalysisStatus.FAILED.value]),
            lookahead_limit=lookahead_limit,
            requested_limit=limit,
            ordering=(MediaItem.indexed_at.asc(), MediaItem.id.asc()),
        )

        if len(jobs) < limit and status.strip().lower() == AnalysisStatus.PENDING.value and (analysis_model or analysis_version):
            jobs.extend(
                self._select_jobs(
                    db,
                    request,
                    clause=and_(
                        MediaItem.analysis_status == AnalysisStatus.COMPLETED.value,
                        self._model_version_mismatch(analysis_model, analysis_version),
                    ),
                    lookahead_limit=min(max((limit - len(jobs)) * 6, 12), 48),
                    requested_limit=limit - len(jobs),
                    ordering=(
                        func.coalesce(MediaItem.analysis_completed_at, MediaItem.indexed_at).asc(),
                        MediaItem.id.asc(),
                    ),
                )
            )

        return AnalysisJobListResponse(jobs=jobs[:limit])

    def _select_jobs(
        self,
        db: Session,
        request: Request,
        *,
        clause,
        lookahead_limit: int,
        requested_limit: int,
        ordering,
    ) -> list[AnalysisJob]:
        statement = (
            select(
                MediaItem.id,
                MediaItem.filename,
                MediaItem.source_path,
                MediaItem.checksum,
                MediaItem.thumbnail_url,
                MediaItem.date_taken,
                MediaItem.width,
                MediaItem.height,
                MediaItem.camera_model,
                MediaItem.country,
                MediaItem.region,
                MediaItem.city,
                MediaItem.place,
                MediaItem.latitude,
                MediaItem.longitude,
                MediaItem.analysis_status,
                MediaItem.analysis_attempts,
                MediaItem.analysis_model,
                MediaItem.analysis_version,
            )
            .where(
                and_(
                    MediaItem.deleted_at.is_(None),
                    MediaItem.media_type == "image",
                    clause,
                )
            )
            .order_by(*ordering)
            .limit(lookahead_limit)
        )
        rows = db.execute(statement).all()
        jobs: list[AnalysisJob] = []
        for row in rows:
            jobs.append(
                AnalysisJob(
                    id=row.id,
                    filename=row.filename,
                    source_path=row.source_path,
                    checksum=row.checksum,
                    stream_url=str(request.url_for("stream_media", media_id=row.id)),
                    thumbnail_url=row.thumbnail_url,
                    date_taken=row.date_taken,
                    width=row.width,
                    height=row.height,
                    camera_model=row.camera_model,
                    country=row.country,
                    region=row.region,
                    city=row.city,
                    place=row.place,
                    latitude=row.latitude,
                    longitude=row.longitude,
                    metadata_json={},
                    analysis_status=row.analysis_status,
                    analysis_attempts=row.analysis_attempts or 0,
                    analysis_model=row.analysis_model,
                    analysis_version=row.analysis_version,
                )
            )
            if len(jobs) >= requested_limit:
                break
        return jobs

    def claim_job(
        self,
        db: Session,
        request: Request,
        *,
        media_id: str,
        body: AnalysisClaimRequest,
    ) -> AnalysisJob:
        stale_claims = self._requeue_stale_claims_best_effort(db)
        if stale_claims:
            db.flush()

        now = datetime.utcnow()
        result = db.execute(
            update(MediaItem)
            .where(
                MediaItem.id == media_id,
                MediaItem.deleted_at.is_(None),
                MediaItem.media_type == "image",
                or_(
                    MediaItem.analysis_status.in_([AnalysisStatus.PENDING.value, AnalysisStatus.FAILED.value]),
                    and_(
                        MediaItem.analysis_status == AnalysisStatus.COMPLETED.value,
                        self._model_version_mismatch(
                            body.analysis_model,
                            body.analysis_version,
                        ),
                    ),
                ),
            )
            .values(
                analysis_status=AnalysisStatus.CLAIMED.value,
                analysis_attempts=func.coalesce(MediaItem.analysis_attempts, 0) + 1,
                analysis_worker_id=body.worker_id,
                analysis_claimed_at=now,
                analysis_heartbeat_at=now,
                analysis_model=body.analysis_model,
                analysis_version=body.analysis_version,
                analysis_error=None,
            )
        )
        if result.rowcount == 0:
            db.rollback()
            raise HTTPException(status_code=409, detail="Media item is not claimable right now.")

        db.commit()
        item = db.get(MediaItem, media_id)
        if not item:
            raise HTTPException(status_code=404, detail="Media item not found.")
        if not Path(item.source_path).exists():
            item.analysis_status = AnalysisStatus.SKIPPED.value
            item.analysis_error = "Indexed source file is not available on disk."
            item.analysis_claimed_at = None
            item.analysis_heartbeat_at = None
            item.analysis_worker_id = None
            db.commit()
            raise HTTPException(status_code=409, detail="Indexed source file is not available on disk.")
        return AnalysisJob(
            id=item.id,
            filename=item.filename,
            source_path=item.source_path,
            checksum=item.checksum,
            stream_url=str(request.url_for("stream_media", media_id=item.id)),
            thumbnail_url=item.thumbnail_url,
            date_taken=item.date_taken,
            width=item.width,
            height=item.height,
            camera_model=item.camera_model,
            country=item.country,
            region=item.region,
            city=item.city,
            place=item.place,
            latitude=item.latitude,
            longitude=item.longitude,
            metadata_json={},
            analysis_status=item.analysis_status,
            analysis_attempts=item.analysis_attempts or 0,
            analysis_model=item.analysis_model,
            analysis_version=item.analysis_version,
        )

    def submit_result(self, db: Session, *, media_id: str, body: AnalysisResultRequest) -> AnalysisResultResponse:
        item = db.get(MediaItem, media_id)
        if not item or item.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Media item not found.")
        if item.media_type != "image":
            raise HTTPException(status_code=400, detail="Only image analysis jobs are supported.")
        if item.analysis_worker_id and item.analysis_worker_id != body.worker_id and item.analysis_status == AnalysisStatus.CLAIMED.value:
            raise HTTPException(status_code=409, detail="Media item is claimed by a different worker.")

        now = datetime.utcnow()
        item.analysis_model = body.analysis_model
        item.analysis_version = body.analysis_version
        item.analysis_worker_id = body.worker_id
        item.analysis_claimed_at = None
        item.analysis_heartbeat_at = None

        if body.status == "failed":
            item.analysis_status = AnalysisStatus.FAILED.value
            item.analysis_error = body.error or "Analysis failed."
            item.analysis_completed_at = None
            db.commit()
            return AnalysisResultResponse(media_id=item.id, analysis_status=item.analysis_status)

        tags_json = normalize_text_list(body.tags_json)
        objects_json = normalize_text_list(body.objects_json)
        landmarks_json = normalize_text_list(body.landmarks_json)
        scene_json = body.scene_json if isinstance(body.scene_json, dict) else {}

        item.caption_ai = clean_text(body.caption_ai)
        item.caption_dense = clean_text(body.caption_dense)
        item.tags_json = tags_json
        item.objects_json = objects_json
        item.landmarks_json = landmarks_json
        item.scene_json = scene_json
        item.analysis_status = AnalysisStatus.COMPLETED.value
        item.analysis_error = None
        item.analysis_completed_at = now

        if item.caption_ai:
            item.caption = item.caption_ai
        if body.ocr_text is not None:
            item.ocr_text = body.ocr_text.strip()
        item.tags = merge_unique(tags_json, landmarks_json, item.tags or [])
        item.objects = merge_unique(objects_json, item.objects or [])
        if landmarks_json:
            item.landmark = item.landmark or landmarks_json[0]

        place_hint = clean_text(scene_json.get("place_hint")) if isinstance(scene_json, dict) else None
        if place_hint and not item.place:
            item.place = place_hint

        metadata_json = dict(item.metadata_json or {})
        metadata_json["analysis_pipeline"] = {
            "worker_id": body.worker_id,
            "analysis_model": body.analysis_model,
            "analysis_version": body.analysis_version,
            "analysis_completed_at": now.isoformat(),
        }
        metadata_json["ai_analysis"] = {
            "caption_ai": item.caption_ai,
            "caption_dense": item.caption_dense,
            "tags_json": tags_json,
            "objects_json": objects_json,
            "landmarks_json": landmarks_json,
            "scene_json": scene_json,
            "ocr_text": item.ocr_text,
        }
        item.metadata_json = metadata_json
        item.embedding = self.embedder.embed_document(build_embedding_source(item))

        db.commit()
        db.refresh(item)
        self.vector_store.upsert_media(item)
        return AnalysisResultResponse(
            media_id=item.id,
            analysis_status=item.analysis_status,
            analysis_completed_at=item.analysis_completed_at,
        )

    def heartbeat(self, db: Session, *, worker_id: str) -> AnalysisHeartbeatResponse:
        now = datetime.utcnow()
        try:
            result = db.execute(
                update(MediaItem)
                .where(
                    MediaItem.analysis_worker_id == worker_id,
                    MediaItem.analysis_status == AnalysisStatus.CLAIMED.value,
                )
                .values(analysis_heartbeat_at=now)
            )
            db.commit()
            updated_jobs = result.rowcount or 0
        except OperationalError as exc:
            db.rollback()
            if not self._is_transient_sqlite_lock(exc):
                raise
            updated_jobs = 0
        return AnalysisHeartbeatResponse(worker_id=worker_id, updated_jobs=updated_jobs, timestamp=now)

    def stats(self, db: Session) -> AnalysisStatsResponse:
        stale_cutoff = datetime.utcnow() - timedelta(minutes=self.settings.analysis_claim_stale_minutes)
        pending, claimed, completed, failed, skipped = db.execute(
            select(
                func.sum(case((MediaItem.analysis_status == AnalysisStatus.PENDING.value, 1), else_=0)),
                func.sum(case((MediaItem.analysis_status == AnalysisStatus.CLAIMED.value, 1), else_=0)),
                func.sum(case((MediaItem.analysis_status == AnalysisStatus.COMPLETED.value, 1), else_=0)),
                func.sum(case((MediaItem.analysis_status == AnalysisStatus.FAILED.value, 1), else_=0)),
                func.sum(case((MediaItem.analysis_status == AnalysisStatus.SKIPPED.value, 1), else_=0)),
            ).where(MediaItem.deleted_at.is_(None))
        ).one()

        active_workers = db.scalar(
            select(func.count(distinct(MediaItem.analysis_worker_id))).where(
                MediaItem.analysis_status == AnalysisStatus.CLAIMED.value,
                MediaItem.analysis_worker_id.is_not(None),
                func.coalesce(MediaItem.analysis_heartbeat_at, MediaItem.analysis_claimed_at) >= stale_cutoff,
            )
        ) or 0
        stale_claims = db.scalar(
            select(func.count(MediaItem.id)).where(
                MediaItem.analysis_status == AnalysisStatus.CLAIMED.value,
                func.coalesce(MediaItem.analysis_heartbeat_at, MediaItem.analysis_claimed_at) < stale_cutoff,
            )
        ) or 0

        return AnalysisStatsResponse(
            pending=pending or 0,
            claimed=claimed or 0,
            completed=completed or 0,
            failed=failed or 0,
            skipped=skipped or 0,
            active_workers=active_workers,
            stale_claims=stale_claims,
        )

    def _requeue_stale_claims(self, db: Session) -> int:
        stale_cutoff = datetime.utcnow() - timedelta(minutes=self.settings.analysis_claim_stale_minutes)
        result = db.execute(
            update(MediaItem)
            .where(
                MediaItem.analysis_status == AnalysisStatus.CLAIMED.value,
                func.coalesce(MediaItem.analysis_heartbeat_at, MediaItem.analysis_claimed_at) < stale_cutoff,
            )
            .values(
                analysis_status=AnalysisStatus.PENDING.value,
                analysis_worker_id=None,
                analysis_claimed_at=None,
                analysis_heartbeat_at=None,
                analysis_error="stale worker claim requeued",
            )
        )
        return result.rowcount or 0

    def _requeue_stale_claims_best_effort(self, db: Session) -> int:
        try:
            return self._requeue_stale_claims(db)
        except OperationalError as exc:
            db.rollback()
            if not self._is_transient_sqlite_lock(exc):
                raise
            return 0

    @staticmethod
    def _status_clause(status: str, *, analysis_model: str | None = None, analysis_version: str | None = None):
        normalized = status.strip().lower() if status else AnalysisStatus.PENDING.value
        if normalized == AnalysisStatus.PENDING.value:
            pending_or_failed = MediaItem.analysis_status.in_([AnalysisStatus.PENDING.value, AnalysisStatus.FAILED.value])
            if analysis_model or analysis_version:
                return or_(
                    pending_or_failed,
                    and_(
                        MediaItem.analysis_status == AnalysisStatus.COMPLETED.value,
                        AnalysisQueueService._model_version_mismatch(analysis_model, analysis_version),
                    ),
                )
            return pending_or_failed
        return MediaItem.analysis_status == normalized

    @staticmethod
    def _model_version_mismatch(analysis_model: str | None, analysis_version: str | None):
        clauses = []
        if analysis_model:
            clauses.append(or_(MediaItem.analysis_model.is_(None), MediaItem.analysis_model != analysis_model))
        if analysis_version:
            clauses.append(or_(MediaItem.analysis_version.is_(None), MediaItem.analysis_version != analysis_version))
        if not clauses:
            return text("0 = 1")
        return or_(*clauses)

    @staticmethod
    def _is_transient_sqlite_lock(error: OperationalError) -> bool:
        message = str(getattr(error, "orig", error)).lower()
        return "database is locked" in message or "database table is locked" in message


def normalize_text_list(values: list[str] | None) -> list[str]:
    normalized: list[str] = []
    for value in values or []:
        if not isinstance(value, str):
            continue
        cleaned = value.strip().lower()
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)
    return normalized


def clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def merge_unique(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    for group in groups:
        for value in group:
            if not value:
                continue
            cleaned = value.strip().lower()
            if cleaned and cleaned not in merged:
                merged.append(cleaned)
    return merged


def build_embedding_source(item: MediaItem) -> str:
    return " ".join(
        filter(
            None,
            [
                item.caption_ai or item.caption,
                item.caption_dense,
                " ".join(item.tags_json or item.tags or []),
                " ".join(item.objects_json or item.objects or []),
                " ".join(item.landmarks_json or []),
                item.ocr_text,
                item.place,
                item.landmark,
                item.country,
                item.city,
                item.region,
            ],
        )
    )
