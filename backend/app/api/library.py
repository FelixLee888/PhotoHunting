from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
from time import monotonic
from typing import Callable

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.api.deps import settings_dependency
from app.core.config import Settings
from app.db.session import get_db
from app.models import MediaItem
from app.schemas.library import LibraryStatsResponse, ScanStatusResponse
from app.services.summary_store import get_materialized_totals

router = APIRouter(prefix="/library", tags=["library"])
STATS_CACHE_TTL_SECONDS = 900.0
_stats_cache: tuple[float, LibraryStatsResponse] | None = None


def invalidate_library_stats_cache() -> None:
    global _stats_cache
    _stats_cache = None


@router.get("/stats", response_model=LibraryStatsResponse)
def get_library_stats(db: Session = Depends(get_db)):
    global _stats_cache
    now = monotonic()
    if _stats_cache and now - _stats_cache[0] < STATS_CACHE_TTL_SECONDS:
        return _stats_cache[1]

    materialized_totals = get_materialized_totals(db)
    if materialized_totals:
        response = LibraryStatsResponse(
            indexed_media=int(materialized_totals.get("indexed_media") or 0),
            indexed_images=int(materialized_totals.get("indexed_images") or 0),
            indexed_videos=int(materialized_totals.get("indexed_videos") or 0),
            mapped_media=int(materialized_totals.get("mapped_media") or 0),
            trip_routes=int(materialized_totals.get("trip_routes") or 0),
            last_indexed_at=materialized_totals.get("last_indexed_at"),
            available_years=[],
        )
        _stats_cache = (now, response)
        return response

    active_items = MediaItem.deleted_at.is_(None)
    mapped_items = MediaItem.latitude.is_not(None) & MediaItem.longitude.is_not(None)

    indexed_media, indexed_images, indexed_videos, mapped_media, last_indexed_at = db.execute(
        select(
            func.count(MediaItem.id),
            func.sum(case((MediaItem.media_type == "image", 1), else_=0)),
            func.sum(case((MediaItem.media_type == "video", 1), else_=0)),
            func.sum(case((mapped_items, 1), else_=0)),
            func.max(func.coalesce(MediaItem.last_seen_at, MediaItem.indexed_at)),
        ).where(active_items)
    ).one()

    route_groups = (
        select(MediaItem.trip_name)
        .where(active_items, MediaItem.trip_name.is_not(None))
        .group_by(MediaItem.trip_name)
        .having(func.count(MediaItem.id) > 1)
        .subquery()
    )
    trip_routes = db.scalar(select(func.count()).select_from(route_groups)) or 0
    response = LibraryStatsResponse(
        indexed_media=indexed_media or 0,
        indexed_images=indexed_images or 0,
        indexed_videos=indexed_videos or 0,
        mapped_media=mapped_media or 0,
        trip_routes=trip_routes,
        last_indexed_at=last_indexed_at,
        available_years=[],
    )
    _stats_cache = (now, response)
    return response


@router.get("/scan-status", response_model=ScanStatusResponse)
def get_scan_status(settings: Settings = Depends(settings_dependency)):
    app_root = _app_root(settings)
    pid_file = app_root / "scan.pid"
    log_file = app_root / "logs" / "scan.log"
    pid = _read_pid(pid_file)
    running = _pid_is_running(pid)
    latest_entry = _latest_log_entry(log_file)
    mode_entry = _mode_log_entry(log_file)
    status = "running" if running else "idle"
    detail = None

    if pid and not running:
        status = "stale"
        detail = "scan pid file exists, but the worker is not running"
    if latest_entry and latest_entry.get("event") == "complete":
        status = "idle"
        detail = "last photo scan completed"
    if latest_entry and latest_entry.get("event") == "error":
        status = "error" if running else "idle"
        detail = latest_entry.get("error") or detail

    return ScanStatusResponse(
        running=running,
        status=status,
        pid=pid,
        mode=(mode_entry or latest_entry or {}).get("mode"),
        event=(latest_entry or {}).get("event"),
        scanned=_coerce_int((latest_entry or {}).get("scanned")),
        created=_coerce_int((latest_entry or {}).get("created")),
        updated=_coerce_int((latest_entry or {}).get("updated")),
        skipped=_coerce_int((latest_entry or {}).get("skipped")),
        errors=_coerce_int((latest_entry or {}).get("errors")),
        last_path=(latest_entry or {}).get("last_path") or (latest_entry or {}).get("path"),
        log_updated_at=_mtime(log_file),
        detail=detail,
    )


def _app_root(settings: Settings) -> Path:
    if settings.database_url.startswith("sqlite:///"):
        raw_path = settings.database_url.removeprefix("sqlite:///")
        db_path = Path(raw_path)
        if not db_path.is_absolute():
            db_path = Path.cwd() / db_path
        resolved = db_path.resolve()
        return resolved.parent.parent
    if settings.static_frontend_dir:
        static_dir = Path(settings.static_frontend_dir).expanduser()
        if static_dir.exists():
            return static_dir.parent.parent
    return Path.cwd().resolve().parent


def _read_pid(pid_file: Path) -> int | None:
    if not pid_file.exists():
        return None
    try:
        return int(pid_file.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _pid_is_running(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _latest_log_entry(log_file: Path) -> dict[str, object] | None:
    return _scan_log_entry(log_file, reverse=True, predicate=lambda entry: "event" in entry)


def _mode_log_entry(log_file: Path) -> dict[str, object] | None:
    return _scan_log_entry(log_file, reverse=False, predicate=lambda entry: "mode" in entry)


def _scan_log_entry(
    log_file: Path, *, reverse: bool, predicate: Callable[[dict[str, object]], bool]
) -> dict[str, object] | None:
    if not log_file.exists():
        return None
    try:
        lines = log_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return None

    iterator = reversed(lines) if reverse else lines
    for line in iterator:
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and predicate(payload):
            return payload
    return None


def _mtime(path: Path) -> datetime | None:
    if not path.exists():
        return None
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except Exception:
        return None


def _coerce_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
