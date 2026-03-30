from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import Integer, Text, and_, case, cast, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import settings_dependency
from app.core.config import Settings
from app.db.session import get_db
from app.models import MediaItem
from app.schemas.common import LibraryYear, MediaCard, MediaDetail, SegmentSummary, TimelineMonth, TripSummary, YearMediaGroup
from app.services.previews import ensure_preview, preview_url_for_media

router = APIRouter(prefix="/media", tags=["media"])
COMPACT_CAPTION_LIMIT = 160


def _build_media_conditions(
    *,
    media_type: str | None = None,
    analysis_status: str | None = None,
    trip_name: str | None = None,
    country: str | None = None,
    region: str | None = None,
    city: str | None = None,
    tag: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    bbox: str | None = None,
) -> list:
    conditions = [MediaItem.deleted_at.is_(None)]
    if media_type:
        conditions.append(MediaItem.media_type == media_type)
    if analysis_status:
        conditions.append(MediaItem.analysis_status == analysis_status)
    if trip_name:
        conditions.append(MediaItem.trip_name == trip_name)
    if country:
        conditions.append(MediaItem.country.ilike(f"%{country}%"))
    if region:
        conditions.append(MediaItem.region.ilike(f"%{region}%"))
    if city:
        conditions.append(or_(MediaItem.city.ilike(f"%{city}%"), MediaItem.place.ilike(f"%{city}%")))
    if tag:
        conditions.append(cast(MediaItem.tags, Text).ilike(f"%{tag}%"))
    if date_from:
        conditions.append(MediaItem.date_taken >= datetime.combine(date_from, time.min))
    if date_to:
        conditions.append(MediaItem.date_taken <= datetime.combine(date_to, time.max))
    if bbox:
        try:
            min_lat, min_lng, max_lat, max_lng = [float(part) for part in bbox.split(",")]
        except ValueError:
            min_lat = min_lng = max_lat = max_lng = None
        if None not in {min_lat, min_lng, max_lat, max_lng}:
            conditions.extend(
                [
                    MediaItem.latitude.is_not(None),
                    MediaItem.longitude.is_not(None),
                    MediaItem.latitude >= min_lat,
                    MediaItem.latitude <= max_lat,
                    MediaItem.longitude >= min_lng,
                    MediaItem.longitude <= max_lng,
                ]
            )
    return conditions


def _row_value(row, key: str, default=None):
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        return mapping.get(key, default)
    return getattr(row, key, default)


def _compact_caption(value: str | None) -> str:
    text = (value or "").strip()
    if len(text) <= COMPACT_CAPTION_LIMIT:
        return text
    return f"{text[: COMPACT_CAPTION_LIMIT - 3].rstrip()}..."


def _recent_ordering():
    return (
        MediaItem.date_taken.desc(),
        MediaItem.indexed_at.desc(),
    )


def _earliest_ordering():
    return (
        case((MediaItem.date_taken.is_(None), 1), else_=0),
        MediaItem.date_taken.asc(),
        MediaItem.indexed_at.asc(),
    )


def _media_card_from_row(row, *, compact: bool = False) -> MediaCard:
    return MediaCard(
        id=_row_value(row, "id"),
        filename=_row_value(row, "filename"),
        source_path=_row_value(row, "source_path"),
        media_type=_row_value(row, "media_type"),
        caption=_compact_caption(_row_value(row, "caption")) if compact else (_row_value(row, "caption") or ""),
        tags=[] if compact else list(_row_value(row, "tags") or []),
        country=_row_value(row, "country"),
        region=_row_value(row, "region"),
        city=_row_value(row, "city"),
        place=_row_value(row, "place"),
        landmark=_row_value(row, "landmark"),
        latitude=_row_value(row, "latitude"),
        longitude=_row_value(row, "longitude"),
        thumbnail_url=preview_url_for_media(
            _row_value(row, "id"),
            _row_value(row, "media_type"),
            _row_value(row, "analysis_status"),
            _row_value(row, "thumbnail_url"),
        ),
        date_taken=_row_value(row, "date_taken"),
        duration=_row_value(row, "duration"),
        transcript=None if compact else _row_value(row, "transcript"),
        ocr_text=None if compact else _row_value(row, "ocr_text"),
        trip_name=_row_value(row, "trip_name"),
        analysis_status=_row_value(row, "analysis_status"),
        analysis_completed_at=_row_value(row, "analysis_completed_at"),
        analysis_model=_row_value(row, "analysis_model"),
        analysis_version=_row_value(row, "analysis_version"),
    )


@router.get("", response_model=list[MediaCard])
def list_media(
    limit: int = Query(default=18, ge=1, le=240),
    offset: int = Query(default=0, ge=0),
    media_type: str | None = None,
    analysis_status: str | None = None,
    trip_name: str | None = None,
    country: str | None = None,
    region: str | None = None,
    city: str | None = None,
    tag: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    bbox: str | None = Query(default=None, description="min_lat,min_lng,max_lat,max_lng"),
    db: Session = Depends(get_db),
):
    conditions = _build_media_conditions(
        media_type=media_type,
        analysis_status=analysis_status,
        trip_name=trip_name,
        country=country,
        region=region,
        city=city,
        tag=tag,
        date_from=date_from,
        date_to=date_to,
        bbox=bbox,
    )

    rows = db.execute(
        select(
            MediaItem.id,
            MediaItem.filename,
            MediaItem.source_path,
            MediaItem.media_type,
            MediaItem.caption,
            MediaItem.country,
            MediaItem.region,
            MediaItem.city,
            MediaItem.place,
            MediaItem.landmark,
            MediaItem.latitude,
            MediaItem.longitude,
            MediaItem.thumbnail_url,
            MediaItem.date_taken,
            MediaItem.duration,
            MediaItem.trip_name,
            MediaItem.analysis_status,
            MediaItem.analysis_completed_at,
            MediaItem.analysis_model,
            MediaItem.analysis_version,
        )
        .where(and_(*conditions))
        .order_by(*_recent_ordering())
        .offset(offset)
        .limit(limit)
    ).all()

    return [_media_card_from_row(row, compact=True) for row in rows]


@router.get("/years", response_model=list[LibraryYear])
def list_media_years(
    media_type: str | None = None,
    analysis_status: str | None = None,
    trip_name: str | None = None,
    country: str | None = None,
    region: str | None = None,
    city: str | None = None,
    tag: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
):
    conditions = _build_media_conditions(
        media_type=media_type,
        analysis_status=analysis_status,
        trip_name=trip_name,
        country=country,
        region=region,
        city=city,
        tag=tag,
        date_from=date_from,
        date_to=date_to,
    )
    conditions.append(MediaItem.date_taken.is_not(None))

    year_expr = cast(func.strftime("%Y", MediaItem.date_taken), Integer)
    rows = db.execute(
        select(year_expr.label("year"), func.count(MediaItem.id).label("count"))
        .where(and_(*conditions))
        .group_by(year_expr)
        .order_by(year_expr.desc())
    ).all()

    return [LibraryYear(year=row.year, count=row.count) for row in rows if row.year is not None]


@router.get("/months", response_model=list[TimelineMonth])
def list_media_months(
    year: int | None = None,
    media_type: str | None = None,
    analysis_status: str | None = None,
    trip_name: str | None = None,
    country: str | None = None,
    region: str | None = None,
    city: str | None = None,
    tag: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
):
    conditions = _build_media_conditions(
        media_type=media_type,
        analysis_status=analysis_status,
        trip_name=trip_name,
        country=country,
        region=region,
        city=city,
        tag=tag,
        date_from=date_from,
        date_to=date_to,
    )
    conditions.append(MediaItem.date_taken.is_not(None))
    if year is not None:
        conditions.extend(
            [
                MediaItem.date_taken >= datetime(year, 1, 1),
                MediaItem.date_taken <= datetime(year, 12, 31, 23, 59, 59),
            ]
        )

    year_expr = cast(func.strftime("%Y", MediaItem.date_taken), Integer)
    month_expr = cast(func.strftime("%m", MediaItem.date_taken), Integer)
    rows = db.execute(
        select(
            year_expr.label("year"),
            month_expr.label("month"),
            func.count(MediaItem.id).label("count"),
        )
        .where(and_(*conditions))
        .group_by(year_expr, month_expr)
        .order_by(year_expr.desc(), month_expr.desc())
    ).all()

    timeline_months: list[TimelineMonth] = []
    for row in rows:
        if row.year is None or row.month is None:
            continue
        month_start = datetime(row.year, row.month, 1)
        timeline_months.append(
            TimelineMonth(
                key=f"{row.year:04d}-{row.month:02d}",
                year=row.year,
                month=row.month,
                label=month_start.strftime("%b %Y"),
                short_label=month_start.strftime("%b"),
                count=row.count,
            )
        )
    return timeline_months


@router.get("/year-groups", response_model=list[YearMediaGroup])
def list_media_year_groups(
    per_year: int = Query(default=14, ge=1, le=36),
    media_type: str | None = None,
    analysis_status: str | None = None,
    trip_name: str | None = None,
    country: str | None = None,
    region: str | None = None,
    city: str | None = None,
    tag: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
):
    base_conditions = _build_media_conditions(
        media_type=media_type,
        analysis_status=analysis_status,
        trip_name=trip_name,
        country=country,
        region=region,
        city=city,
        tag=tag,
        date_from=date_from,
        date_to=date_to,
    )
    year_conditions = [*base_conditions, MediaItem.date_taken.is_not(None)]
    year_expr = cast(func.strftime("%Y", MediaItem.date_taken), Integer)

    year_rows = db.execute(
        select(year_expr.label("year"), func.count(MediaItem.id).label("count"))
        .where(and_(*year_conditions))
        .group_by(year_expr)
        .order_by(year_expr.desc())
    ).all()

    preview_rank = func.row_number().over(
        partition_by=year_expr,
        order_by=list(_recent_ordering()),
    ).label("preview_rank")
    preview_subquery = (
        select(
            year_expr.label("year"),
            MediaItem.id.label("id"),
            MediaItem.filename.label("filename"),
            MediaItem.source_path.label("source_path"),
            MediaItem.media_type.label("media_type"),
            MediaItem.caption.label("caption"),
            MediaItem.country.label("country"),
            MediaItem.region.label("region"),
            MediaItem.city.label("city"),
            MediaItem.place.label("place"),
            MediaItem.landmark.label("landmark"),
            MediaItem.latitude.label("latitude"),
            MediaItem.longitude.label("longitude"),
            MediaItem.thumbnail_url.label("thumbnail_url"),
            MediaItem.date_taken.label("date_taken"),
            MediaItem.duration.label("duration"),
            MediaItem.trip_name.label("trip_name"),
            MediaItem.analysis_status.label("analysis_status"),
            MediaItem.analysis_completed_at.label("analysis_completed_at"),
            MediaItem.analysis_model.label("analysis_model"),
            MediaItem.analysis_version.label("analysis_version"),
            preview_rank,
        )
        .where(and_(*year_conditions))
        .subquery()
    )
    preview_rows = db.execute(
        select(preview_subquery)
        .where(preview_subquery.c.preview_rank <= per_year)
        .order_by(preview_subquery.c.year.desc(), preview_subquery.c.preview_rank.asc())
    ).all()

    items_by_year: dict[int, list[MediaCard]] = {}
    for row in preview_rows:
        year = _row_value(row, "year")
        if year is None:
            continue
        items_by_year.setdefault(year, []).append(_media_card_from_row(row, compact=True))

    groups: list[YearMediaGroup] = []
    for year_row in year_rows:
        year = year_row.year
        if year is None:
            continue
        groups.append(
            YearMediaGroup(
                year=year,
                count=year_row.count,
                items=items_by_year.get(year, []),
            )
        )
    return groups


@router.get("/trips", response_model=list[TripSummary])
def list_media_trips(
    limit: int = Query(default=240, ge=1, le=500),
    media_type: str | None = None,
    analysis_status: str | None = None,
    country: str | None = None,
    region: str | None = None,
    city: str | None = None,
    tag: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
):
    base_conditions = _build_media_conditions(
        media_type=media_type,
        analysis_status=analysis_status,
        country=country,
        region=region,
        city=city,
        tag=tag,
        date_from=date_from,
        date_to=date_to,
    )
    trip_conditions = [*base_conditions, MediaItem.trip_name.is_not(None)]

    latest_date_expr = func.max(func.coalesce(MediaItem.date_taken, MediaItem.indexed_at))
    trip_rows = db.execute(
        select(
            MediaItem.trip_name.label("trip_name"),
            func.count(MediaItem.id).label("count"),
            latest_date_expr.label("latest_date"),
        )
        .where(and_(*trip_conditions))
        .group_by(MediaItem.trip_name)
        .order_by(latest_date_expr.desc(), MediaItem.trip_name.asc())
        .limit(limit)
    ).all()

    trip_names = [row.trip_name for row in trip_rows if row.trip_name]
    if not trip_names:
        return []

    cover_subquery = (
        select(
            MediaItem.id.label("id"),
            MediaItem.filename.label("filename"),
            MediaItem.source_path.label("source_path"),
            MediaItem.media_type.label("media_type"),
            MediaItem.caption.label("caption"),
            MediaItem.country.label("country"),
            MediaItem.region.label("region"),
            MediaItem.city.label("city"),
            MediaItem.place.label("place"),
            MediaItem.landmark.label("landmark"),
            MediaItem.latitude.label("latitude"),
            MediaItem.longitude.label("longitude"),
            MediaItem.thumbnail_url.label("thumbnail_url"),
            MediaItem.date_taken.label("date_taken"),
            MediaItem.duration.label("duration"),
            MediaItem.trip_name.label("trip_name"),
            MediaItem.analysis_status.label("analysis_status"),
            MediaItem.analysis_completed_at.label("analysis_completed_at"),
            MediaItem.analysis_model.label("analysis_model"),
            MediaItem.analysis_version.label("analysis_version"),
            func.row_number()
            .over(partition_by=MediaItem.trip_name, order_by=_earliest_ordering())
            .label("row_number"),
        )
        .where(and_(*trip_conditions), MediaItem.trip_name.in_(trip_names))
        .subquery()
    )

    cover_rows = db.execute(
        select(cover_subquery).where(cover_subquery.c.row_number == 1)
    ).all()
    covers_by_trip = {
        _row_value(row, "trip_name"): _media_card_from_row(row, compact=True)
        for row in cover_rows
        if _row_value(row, "trip_name")
    }

    return [
        TripSummary(
            trip_name=row.trip_name,
            count=row.count,
            latest_date=row.latest_date,
            cover=covers_by_trip.get(row.trip_name),
        )
        for row in trip_rows
        if row.trip_name
    ]


@router.get("/{media_id}", response_model=MediaDetail)
def get_media(media_id: str, db: Session = Depends(get_db)):
    item = db.get(MediaItem, media_id)
    if not item or item.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Media item not found.")
    return MediaDetail(
        id=item.id,
        filename=item.filename,
        source_path=item.source_path,
        media_type=item.media_type,
        caption=item.caption,
        tags=item.tags,
        country=item.country,
        region=item.region,
        city=item.city,
        place=item.place,
        landmark=item.landmark,
        latitude=item.latitude,
        longitude=item.longitude,
        thumbnail_url=preview_url_for_media(item.id, item.media_type, item.analysis_status, item.thumbnail_url),
        date_taken=item.date_taken,
        duration=item.duration,
        transcript=item.transcript,
        ocr_text=item.ocr_text,
        trip_name=item.trip_name,
        analysis_status=item.analysis_status,
        analysis_completed_at=item.analysis_completed_at,
        analysis_model=item.analysis_model,
        analysis_version=item.analysis_version,
        objects=item.objects,
        people=item.people,
        caption_ai=item.caption_ai,
        caption_dense=item.caption_dense,
        tags_json=item.tags_json,
        objects_json=item.objects_json,
        landmarks_json=item.landmarks_json,
        scene_json=item.scene_json,
        analysis_attempts=item.analysis_attempts,
        analysis_error=item.analysis_error,
        metadata_json=item.metadata_json,
        segments=[
            SegmentSummary(
                id=segment.id,
                segment_type=segment.segment_type,
                timestamp_start=segment.timestamp_start,
                timestamp_end=segment.timestamp_end,
                caption=segment.caption,
                content_text=segment.content_text,
                thumbnail_url=segment.thumbnail_url,
            )
            for segment in item.segments
        ],
    )


@router.get("/{media_id}/preview")
def preview_media(
    media_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dependency),
):
    item = db.get(MediaItem, media_id)
    if not item or item.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Media item not found.")
    preview_path = ensure_preview(item, settings)
    if not preview_path:
        raise HTTPException(status_code=404, detail="Preview image is not available.")
    return FileResponse(
        preview_path,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/{media_id}/stream")
def stream_media(media_id: str, db: Session = Depends(get_db)):
    item = db.get(MediaItem, media_id)
    if not item or item.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Media item not found.")
    media_path = Path(item.source_path)
    if not media_path.exists():
        raise HTTPException(status_code=404, detail="Indexed source file is not available on disk.")
    return FileResponse(media_path)
