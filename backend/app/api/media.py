from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from time import monotonic

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import Integer, Text, and_, case, cast, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import settings_dependency
from app.core.config import Settings
from app.db.session import get_db
from app.models import MediaItem
from app.schemas.common import LibraryYear, MediaCard, MediaDetail, SegmentSummary, TimelineMonth, TripSummary, YearMediaGroup
from app.services.metadata import infer_trip_from_path
from app.services.previews import ensure_preview, preview_url_for_media
from app.services.summary_store import list_materialized_months, list_materialized_trips, list_materialized_years

router = APIRouter(prefix="/media", tags=["media"])
COMPACT_CAPTION_LIMIT = 160
YEARS_CACHE_TTL_SECONDS = 900.0
MONTHS_CACHE_TTL_SECONDS = 300.0
YEAR_GROUPS_CACHE_TTL_SECONDS = 900.0
TRIPS_CACHE_TTL_SECONDS = 900.0
_years_cache: dict[tuple[object, ...], tuple[float, list[LibraryYear]]] = {}
_months_cache: dict[tuple[object, ...], tuple[float, list[TimelineMonth]]] = {}
_year_groups_cache: dict[tuple[object, ...], tuple[float, list[YearMediaGroup]]] = {}
_trips_cache: dict[tuple[object, ...], tuple[float, list[TripSummary]]] = {}


def invalidate_media_summary_caches() -> None:
    _years_cache.clear()
    _months_cache.clear()
    _year_groups_cache.clear()
    _trips_cache.clear()


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


def _prune_timed_cache(cache: dict[tuple[object, ...], tuple[float, object]], ttl_seconds: float) -> None:
    now = monotonic()
    expired_keys = [
        key
        for key, entry in cache.items()
        if now - entry[0] >= ttl_seconds
    ]
    for key in expired_keys:
        cache.pop(key, None)


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


def _trip_display_name(summary_name: str | None, cover: MediaCard | None) -> str | None:
    source_path = getattr(cover, "source_path", None)
    if source_path:
        inferred = infer_trip_from_path(Path(source_path)).get("trip_name")
        if inferred:
            return inferred
    return summary_name


def _trip_cover_sort_value(cover: MediaCard | None) -> tuple[int, datetime | None, str]:
    if cover is None:
        return (1, None, "")
    return (0, cover.date_taken, cover.id)


def _trip_summary_sort_value(summary: TripSummary) -> tuple[str, str]:
    trip_name = summary.trip_name or ""
    if len(trip_name) >= 10 and trip_name[4:5] == "-" and trip_name[7:8] == "-" and trip_name[10:11] == " ":
        return (trip_name[:10], trip_name)
    if len(trip_name) >= 7 and trip_name[4:5] == "-" and trip_name[7:8] == " ":
        return (f"{trip_name[:7]}-01", trip_name)
    latest_date = summary.latest_date.isoformat()[:10] if summary.latest_date else "0000-00-00"
    return (latest_date, trip_name)


def _trip_effective_date(summary: TripSummary) -> date | None:
    trip_name = summary.trip_name or ""
    try:
        if len(trip_name) >= 10 and trip_name[4:5] == "-" and trip_name[7:8] == "-" and trip_name[10:11] == " ":
            return datetime.strptime(trip_name[:10], "%Y-%m-%d").date()
        if len(trip_name) >= 7 and trip_name[4:5] == "-" and trip_name[7:8] == " ":
            return datetime.strptime(f"{trip_name[:7]}-01", "%Y-%m-%d").date()
    except ValueError:
        pass
    if summary.cover and summary.cover.date_taken:
        return summary.cover.date_taken.date()
    if summary.latest_date:
        return summary.latest_date.date()
    return None


def _merge_trip_summaries(summaries: list[TripSummary]) -> list[TripSummary]:
    merged: dict[str, TripSummary] = {}
    for summary in summaries:
        effective_name = _trip_display_name(summary.trip_name, summary.cover)
        if not effective_name:
            continue
        normalized_cover = summary.cover
        if normalized_cover is not None and normalized_cover.trip_name != effective_name:
            normalized_cover = normalized_cover.model_copy(update={"trip_name": effective_name})
        existing = merged.get(effective_name)
        if existing is None:
            merged[effective_name] = TripSummary(
                trip_name=effective_name,
                count=summary.count,
                latest_date=summary.latest_date,
                cover=normalized_cover,
            )
            continue
        existing.count += summary.count
        if summary.latest_date and (existing.latest_date is None or summary.latest_date > existing.latest_date):
            existing.latest_date = summary.latest_date
        if _trip_cover_sort_value(normalized_cover) < _trip_cover_sort_value(existing.cover):
            existing.cover = normalized_cover
    ordered = sorted(merged.values(), key=lambda summary: _trip_summary_sort_value(summary)[1])
    ordered.sort(key=lambda summary: _trip_summary_sort_value(summary)[0], reverse=True)
    return ordered


def _filter_trip_summaries_by_date(
    summaries: list[TripSummary],
    *,
    date_from: date | None,
    date_to: date | None,
) -> list[TripSummary]:
    if date_from is None and date_to is None:
        return summaries
    filtered: list[TripSummary] = []
    for summary in summaries:
        effective_date = _trip_effective_date(summary)
        if effective_date is None:
            continue
        if date_from is not None and effective_date < date_from:
            continue
        if date_to is not None and effective_date > date_to:
            continue
        filtered.append(summary)
    return filtered


def _trip_summary_cover_rows(conditions: list, *, limit: int, offset: int = 0):
    effective_limit = max(1, limit)
    trip_date_expr = func.coalesce(MediaItem.date_taken, MediaItem.indexed_at)
    trip_conditions = [*conditions, MediaItem.trip_name.is_not(None)]
    trip_sort_date = case(
        (
            MediaItem.trip_name.like("____-__-__ %"),
            func.substr(MediaItem.trip_name, 1, 10),
        ),
        (
            MediaItem.trip_name.like("____-__ %"),
            func.substr(MediaItem.trip_name, 1, 7) + "-01",
        ),
        else_=func.coalesce(func.strftime("%Y-%m-%d", func.min(trip_date_expr)), "0000-00-00"),
    )

    summary_subquery = (
        select(
            MediaItem.trip_name.label("summary_trip_name"),
            func.count(MediaItem.id).label("summary_count"),
            func.min(trip_date_expr).label("summary_earliest_date"),
            func.max(trip_date_expr).label("summary_latest_date"),
        )
        .where(and_(*trip_conditions))
        .group_by(MediaItem.trip_name)
        .order_by(
            trip_sort_date.desc(),
            MediaItem.trip_name.asc(),
        )
        .offset(offset)
        .limit(effective_limit)
        .subquery()
    )

    cover_rank = func.row_number().over(
        partition_by=summary_subquery.c.summary_trip_name,
        order_by=list(_earliest_ordering()),
    ).label("cover_rank")

    cover_subquery = (
        select(
            summary_subquery.c.summary_trip_name,
            summary_subquery.c.summary_count,
            summary_subquery.c.summary_latest_date,
            summary_subquery.c.summary_earliest_date,
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
            cover_rank,
        )
        .join(summary_subquery, summary_subquery.c.summary_trip_name == MediaItem.trip_name)
        .where(and_(*trip_conditions))
        .subquery()
    )

    return (
        select(cover_subquery)
        .where(cover_subquery.c.cover_rank == 1)
        .order_by(
            case(
                (
                    cover_subquery.c.summary_trip_name.like("____-__-__ %"),
                    func.substr(cover_subquery.c.summary_trip_name, 1, 10),
                ),
                (
                    cover_subquery.c.summary_trip_name.like("____-__ %"),
                    func.substr(cover_subquery.c.summary_trip_name, 1, 7) + "-01",
                ),
                else_=func.coalesce(
                    func.strftime("%Y-%m-%d", cover_subquery.c.summary_earliest_date),
                    "0000-00-00",
                ),
            ).desc(),
            cover_subquery.c.summary_trip_name.asc(),
        )
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


def _supports_materialized_summary(
    *,
    trip_name: str | None = None,
    country: str | None = None,
    region: str | None = None,
    city: str | None = None,
    tag: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    bbox: str | None = None,
) -> bool:
    return not any([trip_name, country, region, city, tag, date_from, date_to, bbox])


def _media_card_select():
    return select(
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


def _load_media_cards_by_ids(db: Session, media_ids: list[str]) -> dict[str, MediaCard]:
    if not media_ids:
        return {}
    rows = db.execute(
        _media_card_select().where(MediaItem.id.in_(media_ids))
    ).all()
    cards = {
        _row_value(row, "id"): _media_card_from_row(row, compact=True)
        for row in rows
    }
    return cards


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
        _media_card_select()
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
    cache_key = (
        media_type,
        analysis_status,
        trip_name,
        country,
        region,
        city,
        tag,
        date_from.isoformat() if date_from else None,
        date_to.isoformat() if date_to else None,
    )
    now = monotonic()
    cached_entry = _years_cache.get(cache_key)
    if cached_entry and now - cached_entry[0] < YEARS_CACHE_TTL_SECONDS:
        return cached_entry[1]

    if _supports_materialized_summary(
        trip_name=trip_name,
        country=country,
        region=region,
        city=city,
        tag=tag,
        date_from=date_from,
        date_to=date_to,
    ):
        rows = list_materialized_years(db, media_type=media_type, analysis_status=analysis_status)
        if rows:
            years = [
                LibraryYear(year=int(row["year_value"]), count=int(row["item_count"]))
                for row in rows
                if row.get("year_value") is not None
            ]
            _prune_timed_cache(_years_cache, YEARS_CACHE_TTL_SECONDS)
            _years_cache[cache_key] = (now, years)
            return years

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

    years = [LibraryYear(year=row.year, count=row.count) for row in rows if row.year is not None]
    _prune_timed_cache(_years_cache, YEARS_CACHE_TTL_SECONDS)
    _years_cache[cache_key] = (now, years)
    return years


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
    cache_key = (
        year,
        media_type,
        analysis_status,
        trip_name,
        country,
        region,
        city,
        tag,
        date_from.isoformat() if date_from else None,
        date_to.isoformat() if date_to else None,
    )
    now = monotonic()
    cached_entry = _months_cache.get(cache_key)
    if cached_entry and now - cached_entry[0] < MONTHS_CACHE_TTL_SECONDS:
        return cached_entry[1]

    if _supports_materialized_summary(
        trip_name=trip_name,
        country=country,
        region=region,
        city=city,
        tag=tag,
        date_from=date_from,
        date_to=date_to,
    ):
        rows = list_materialized_months(
            db,
            media_type=media_type,
            analysis_status=analysis_status,
            year=year,
        )
        if rows:
            timeline_months: list[TimelineMonth] = []
            for row in rows:
                year_value = row.get("year_value")
                month_value = row.get("month_value")
                if year_value is None or month_value is None:
                    continue
                month_start = datetime(int(year_value), int(month_value), 1)
                timeline_months.append(
                    TimelineMonth(
                        key=f"{int(year_value):04d}-{int(month_value):02d}",
                        year=int(year_value),
                        month=int(month_value),
                        label=month_start.strftime("%b %Y"),
                        short_label=month_start.strftime("%b"),
                        count=int(row.get("item_count") or 0),
                    )
                )
            if len(_months_cache) > 48:
                expired_keys = [
                    key
                    for key, entry in _months_cache.items()
                    if now - entry[0] >= MONTHS_CACHE_TTL_SECONDS
                ]
                for key in expired_keys:
                    _months_cache.pop(key, None)
            _months_cache[cache_key] = (now, timeline_months)
            return timeline_months

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
    if len(_months_cache) > 48:
        expired_keys = [
            key
            for key, entry in _months_cache.items()
            if now - entry[0] >= MONTHS_CACHE_TTL_SECONDS
        ]
        for key in expired_keys:
            _months_cache.pop(key, None)
    _months_cache[cache_key] = (now, timeline_months)
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
    cache_key = (
        per_year,
        media_type,
        analysis_status,
        trip_name,
        country,
        region,
        city,
        tag,
        date_from.isoformat() if date_from else None,
        date_to.isoformat() if date_to else None,
    )
    now = monotonic()
    cached_entry = _year_groups_cache.get(cache_key)
    if cached_entry and now - cached_entry[0] < YEAR_GROUPS_CACHE_TTL_SECONDS:
        return cached_entry[1]

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
    _prune_timed_cache(_year_groups_cache, YEAR_GROUPS_CACHE_TTL_SECONDS)
    _year_groups_cache[cache_key] = (now, groups)
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
    cache_key = (
        limit,
        media_type,
        analysis_status,
        country,
        region,
        city,
        tag,
        date_from.isoformat() if date_from else None,
        date_to.isoformat() if date_to else None,
    )
    now = monotonic()
    cached_entry = _trips_cache.get(cache_key)
    if cached_entry and now - cached_entry[0] < TRIPS_CACHE_TTL_SECONDS:
        return cached_entry[1][:limit]

    if _supports_materialized_summary(
        country=country,
        region=region,
        city=city,
        tag=tag,
    ):
        trip_rows = list_materialized_trips(
            db,
            media_type=media_type,
            analysis_status=analysis_status,
            limit=max(limit * 6, 500),
            offset=0,
        )
        if trip_rows:
            cover_cards = _load_media_cards_by_ids(
                db,
                [str(row["cover_media_id"]) for row in trip_rows if row.get("cover_media_id")],
            )
            summaries = _merge_trip_summaries([
                TripSummary(
                    trip_name=str(row["trip_name"]),
                    count=int(row.get("item_count") or 0),
                    latest_date=row.get("latest_date"),
                    cover=cover_cards.get(str(row.get("cover_media_id"))),
                )
                for row in trip_rows
            ])
            summaries = _filter_trip_summaries_by_date(
                summaries,
                date_from=date_from,
                date_to=date_to,
            )
            _prune_timed_cache(_trips_cache, TRIPS_CACHE_TTL_SECONDS)
            _trips_cache[cache_key] = (now, summaries)
            return summaries[:limit]

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
    summary_rows = db.execute(
        _trip_summary_cover_rows(base_conditions, limit=limit)
    ).all()

    if not summary_rows:
        _prune_timed_cache(_trips_cache, TRIPS_CACHE_TTL_SECONDS)
        _trips_cache[cache_key] = (now, [])
        return []

    summaries = _merge_trip_summaries([
        TripSummary(
            trip_name=_row_value(row, "summary_trip_name"),
            count=int(_row_value(row, "summary_count", 0) or 0),
            latest_date=_row_value(row, "summary_latest_date"),
            cover=_media_card_from_row(row, compact=True),
        )
        for row in summary_rows
    ])
    _prune_timed_cache(_trips_cache, TRIPS_CACHE_TTL_SECONDS)
    _trips_cache[cache_key] = (now, summaries)
    return summaries[:limit]


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


@router.get("/{media_id}/preview/tv")
def preview_media_tv(
    media_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dependency),
):
    item = db.get(MediaItem, media_id)
    if not item or item.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Media item not found.")
    preview_path = ensure_preview(item, settings, variant="tv")
    if not preview_path:
        raise HTTPException(status_code=404, detail="TV preview image is not available.")
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
