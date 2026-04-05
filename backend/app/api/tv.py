from __future__ import annotations

import json
from datetime import date
from time import monotonic

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.api.media import (
    _build_media_conditions,
    _earliest_ordering,
    _media_card_from_row,
    _recent_ordering,
    _row_value,
    _trip_summary_cover_rows,
    _load_media_cards_by_ids,
)
from app.db.session import get_db
from app.models import MediaItem
from app.schemas.tv import TVHomeResponse, TVMediaCard, TVPlaylistResponse, TVTripSummary
from app.services.summary_store import list_materialized_trips

router = APIRouter(prefix="/tv", tags=["tv"])
TV_HOME_CACHE_TTL_SECONDS = 900.0
TV_TRIP_SUMMARY_CACHE_TTL_SECONDS = 900.0
_tv_home_cache: dict[tuple[object, ...], tuple[float, TVHomeResponse]] = {}
_tv_trip_summary_cache: dict[tuple[object, ...], tuple[float, list[TVTripSummary]]] = {}


def invalidate_tv_home_caches() -> None:
    _tv_home_cache.clear()
    _tv_trip_summary_cache.clear()


def _tv_card_from_media_card(card) -> TVMediaCard:
    return TVMediaCard(
        id=card.id,
        filename=card.filename,
        caption=card.caption or "",
        country=card.country,
        city=card.city,
        place=card.place,
        thumbnail_url=card.thumbnail_url,
        date_taken=card.date_taken,
        trip_name=card.trip_name,
    )


def _tv_json_response(payload) -> Response:
    return Response(
        content=json.dumps(
            jsonable_encoder(payload),
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ),
        media_type="application/json; charset=utf-8",
    )


def _prune_cache_entries(cache: dict[tuple[object, ...], tuple[float, object]], ttl_seconds: float) -> None:
    now = monotonic()
    expired_keys = [
        key
        for key, (saved_at, _) in cache.items()
        if now - saved_at >= ttl_seconds
    ]
    for key in expired_keys:
        cache.pop(key, None)


def _tv_media_query(conditions: list):
    return (
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
    )


def _load_cards(db: Session, conditions: list, *, limit: int, offset: int = 0, ordering=None):
    rows = db.execute(
        _tv_media_query(conditions)
        .order_by(*(ordering or _recent_ordering()))
        .offset(offset)
        .limit(limit)
    ).all()
    return [_tv_card_from_media_card(_media_card_from_row(row, compact=True)) for row in rows]


def _load_diverse_recent_cards(db: Session, conditions: list, *, limit: int):
    lookahead_limit = min(max(limit * 40, limit + 48), 1200)
    rows = db.execute(
        _tv_media_query(conditions)
        .order_by(*_recent_ordering())
        .limit(lookahead_limit)
    ).all()

    primary_cards: list[TVMediaCard] = []
    seen_trip_names: set[str] = set()

    for row in rows:
        card = _tv_card_from_media_card(_media_card_from_row(row, compact=True))
        trip_name = (card.trip_name or "").strip().lower()
        if trip_name and trip_name not in seen_trip_names:
            seen_trip_names.add(trip_name)
            primary_cards.append(card)
        elif not trip_name:
            primary_cards.append(card)
        if len(primary_cards) >= limit:
            break

    return primary_cards[:limit]


def _load_trip_summaries(
    db: Session,
    *,
    limit: int,
    offset: int,
    analysis_status: str | None,
    date_from: date | None,
    date_to: date | None,
):
    cache_key = (
        limit,
        offset,
        analysis_status,
        date_from.isoformat() if date_from else None,
        date_to.isoformat() if date_to else None,
    )
    now = monotonic()
    cached = _tv_trip_summary_cache.get(cache_key)
    if cached and now - cached[0] < TV_TRIP_SUMMARY_CACHE_TTL_SECONDS:
        summaries = cached[1]
        return summaries[offset: offset + limit]

    if date_from is None and date_to is None:
        summary_rows = list_materialized_trips(
            db,
            media_type="image",
            analysis_status=analysis_status,
            limit=limit,
            offset=offset,
        )
        if summary_rows:
            cover_cards = _load_media_cards_by_ids(
                db,
                [str(row["cover_media_id"]) for row in summary_rows if row.get("cover_media_id")],
            )
            summaries = [
                TVTripSummary(
                    trip_name=str(row["trip_name"]),
                    count=int(row.get("item_count") or 0),
                    latest_date=row.get("latest_date"),
                    cover=_tv_card_from_media_card(cover_cards[str(row["cover_media_id"])]),
                )
                for row in summary_rows
                if row.get("cover_media_id") and str(row["cover_media_id"]) in cover_cards
            ]
            _prune_cache_entries(_tv_trip_summary_cache, TV_TRIP_SUMMARY_CACHE_TTL_SECONDS)
            _tv_trip_summary_cache[cache_key] = (now, summaries)
            return summaries

    base_conditions = _build_media_conditions(
        media_type="image",
        analysis_status=analysis_status,
        date_from=date_from,
        date_to=date_to,
    )
    summary_rows = db.execute(
        _trip_summary_cover_rows(base_conditions, limit=limit, offset=offset)
    ).all()
    if not summary_rows:
        _prune_cache_entries(_tv_trip_summary_cache, TV_TRIP_SUMMARY_CACHE_TTL_SECONDS)
        _tv_trip_summary_cache[cache_key] = (now, [])
        return []

    summaries = [
        TVTripSummary(
            trip_name=_row_value(row, "summary_trip_name"),
            count=int(_row_value(row, "summary_count", 0) or 0),
            latest_date=_row_value(row, "summary_latest_date"),
            cover=_tv_card_from_media_card(_media_card_from_row(row, compact=True)),
        )
        for row in summary_rows
    ]
    _prune_cache_entries(_tv_trip_summary_cache, TV_TRIP_SUMMARY_CACHE_TTL_SECONDS)
    _tv_trip_summary_cache[cache_key] = (now, summaries)
    return summaries


@router.get("/home", response_model=TVHomeResponse)
def get_tv_home(
    recent_limit: int = Query(default=50, ge=12, le=80),
    trip_limit: int = Query(default=12, ge=1, le=24),
    trip_offset: int = Query(default=0, ge=0),
    analysis_status: str | None = Query(default="completed"),
    db: Session = Depends(get_db),
):
    cache_key = (
        recent_limit,
        trip_limit,
        trip_offset,
        analysis_status,
    )
    now = monotonic()
    cached = _tv_home_cache.get(cache_key)
    if cached and now - cached[0] < TV_HOME_CACHE_TTL_SECONDS:
        return _tv_json_response(cached[1])

    recent_trips = _load_trip_summaries(
        db,
        limit=trip_limit + 1,
        offset=trip_offset,
        analysis_status=analysis_status,
        date_from=None,
        date_to=None,
    )
    trips_has_more = len(recent_trips) > trip_limit
    response = TVHomeResponse(
        recent_photos=[],
        recent_trips=recent_trips[:trip_limit],
        trips_has_more=trips_has_more,
    )
    _prune_cache_entries(_tv_home_cache, TV_HOME_CACHE_TTL_SECONDS)
    _tv_home_cache[cache_key] = (now, response)
    return _tv_json_response(response)


@router.get("/playlists/recent", response_model=TVPlaylistResponse)
def get_recent_playlist(
    limit: int = Query(default=120, ge=1, le=240),
    offset: int = Query(default=0, ge=0),
    analysis_status: str | None = Query(default="completed"),
    db: Session = Depends(get_db),
):
    conditions = _build_media_conditions(
        media_type="image",
        analysis_status=analysis_status,
    )
    total_count = db.scalar(
        select(func.count(MediaItem.id)).where(and_(*conditions))
    ) or 0
    items = _load_cards(db, conditions, limit=limit, offset=offset)
    return _tv_json_response(TVPlaylistResponse(
        playlist_id="recent",
        title="Recently added",
        subtitle="Latest photos from your library",
        total_count=total_count,
        items=items,
    ))


@router.get("/playlists/trips/{trip_name}", response_model=TVPlaylistResponse)
def get_trip_playlist(
    trip_name: str,
    limit: int = Query(default=120, ge=1, le=240),
    offset: int = Query(default=0, ge=0),
    analysis_status: str | None = Query(default="completed"),
    db: Session = Depends(get_db),
):
    conditions = _build_media_conditions(
        media_type="image",
        analysis_status=analysis_status,
        trip_name=trip_name,
    )
    total_count = db.scalar(
        select(func.count(MediaItem.id)).where(and_(*conditions))
    ) or 0
    items = _load_cards(db, conditions, limit=limit, offset=offset, ordering=_earliest_ordering())
    first_seen = db.scalar(
        select(func.min(MediaItem.date_taken)).where(and_(*conditions), MediaItem.date_taken.is_not(None))
    )
    last_seen = db.scalar(
        select(func.max(MediaItem.date_taken)).where(and_(*conditions), MediaItem.date_taken.is_not(None))
    )
    subtitle = None
    if first_seen and last_seen:
        if first_seen.date() == last_seen.date():
            subtitle = first_seen.strftime("%d %b %Y")
        else:
            subtitle = f"{first_seen.strftime('%d %b %Y')} to {last_seen.strftime('%d %b %Y')}"
    elif first_seen:
        subtitle = first_seen.strftime("%d %b %Y")

    return _tv_json_response(TVPlaylistResponse(
        playlist_id=f"trip:{trip_name}",
        title=trip_name,
        subtitle=subtitle,
        total_count=total_count,
        items=items,
    ))
