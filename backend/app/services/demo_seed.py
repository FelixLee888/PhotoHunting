from __future__ import annotations

from datetime import datetime
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.demo_media import DEMO_MEDIA
from app.models import MediaItem, MediaSegment
from app.services.embeddings import build_embedding_provider


def seed_demo_data(db: Session, settings) -> None:
    if db.scalar(select(MediaItem.id).limit(1)):
        return

    embedder = build_embedding_provider(settings)

    for item_data in DEMO_MEDIA:
        media = MediaItem(
            checksum=sha256(item_data["source_path"].encode("utf-8")).hexdigest(),
            source_path=item_data["source_path"],
            filename=item_data["filename"],
            media_type=item_data["media_type"],
            caption=item_data["caption"],
            tags=item_data.get("tags", []),
            objects=item_data.get("objects", []),
            people=item_data.get("people", []),
            country=item_data.get("country"),
            region=item_data.get("region"),
            city=item_data.get("city"),
            place=item_data.get("place"),
            landmark=item_data.get("landmark"),
            latitude=item_data.get("latitude"),
            longitude=item_data.get("longitude"),
            date_taken=datetime.fromisoformat(item_data["date_taken"]) if item_data.get("date_taken") else None,
            thumbnail_url=item_data.get("thumbnail_url"),
            trip_name=item_data.get("trip_name"),
            duration=item_data.get("duration"),
            transcript=item_data.get("transcript", ""),
            ocr_text=item_data.get("ocr_text", ""),
            embedding=embedder.embed_document(
                " ".join(
                    filter(
                        None,
                        [
                            item_data.get("caption"),
                            " ".join(item_data.get("tags", [])),
                            item_data.get("place"),
                            item_data.get("country"),
                            item_data.get("transcript"),
                            item_data.get("ocr_text"),
                        ],
                    )
                )
            ),
            metadata_json={"demo": True},
        )
        db.add(media)
        db.flush()
        for segment_data in item_data.get("segments", []):
            segment_text = " ".join(
                filter(None, [segment_data.get("caption"), segment_data.get("content_text"), media.caption])
            )
            db.add(
                MediaSegment(
                    media_id=media.id,
                    segment_type=segment_data["segment_type"],
                    timestamp_start=segment_data.get("timestamp_start"),
                    timestamp_end=segment_data.get("timestamp_end"),
                    caption=segment_data.get("caption", ""),
                    content_text=segment_data.get("content_text", ""),
                    embedding=embedder.embed_document(segment_text),
                    thumbnail_url=media.thumbnail_url,
                )
            )
    db.commit()
