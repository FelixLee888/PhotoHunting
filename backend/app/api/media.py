from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import MediaItem
from app.schemas.common import MediaCard, MediaDetail, SegmentSummary

router = APIRouter(prefix="/media", tags=["media"])


@router.get("", response_model=list[MediaCard])
def list_media(db: Session = Depends(get_db)):
    items = db.scalars(select(MediaItem).where(MediaItem.deleted_at.is_(None)).order_by(MediaItem.date_taken.desc())).all()
    return [
        MediaCard(
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
            thumbnail_url=item.thumbnail_url,
            date_taken=item.date_taken,
            duration=item.duration,
            transcript=item.transcript,
            ocr_text=item.ocr_text,
            trip_name=item.trip_name,
        )
        for item in items
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
        thumbnail_url=item.thumbnail_url,
        date_taken=item.date_taken,
        duration=item.duration,
        transcript=item.transcript,
        ocr_text=item.ocr_text,
        trip_name=item.trip_name,
        objects=item.objects,
        people=item.people,
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


@router.get("/{media_id}/stream")
def stream_media(media_id: str, db: Session = Depends(get_db)):
    item = db.get(MediaItem, media_id)
    if not item or item.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Media item not found.")
    media_path = Path(item.source_path)
    if not media_path.exists():
        raise HTTPException(status_code=404, detail="Indexed source file is not available on disk.")
    return FileResponse(media_path)
