from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class MediaType(StrEnum):
    IMAGE = "image"
    VIDEO = "video"


class SegmentType(StrEnum):
    KEYFRAME = "keyframe"
    TRANSCRIPT = "transcript"
    OCR = "ocr"


class MediaItem(Base):
    __tablename__ = "media_items"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid4().hex)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    source_path: Mapped[str] = mapped_column(String(2048), unique=True, index=True)
    filename: Mapped[str] = mapped_column(String(255), index=True)
    media_type: Mapped[str] = mapped_column(String(20), index=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at_fs: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    modified_at_fs: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    date_taken: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True, index=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    frame_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    codec: Mapped[str | None] = mapped_column(String(120), nullable=True)
    camera_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lens: Mapped[str | None] = mapped_column(String(120), nullable=True)
    focal_length: Mapped[str | None] = mapped_column(String(80), nullable=True)
    orientation: Mapped[str | None] = mapped_column(String(80), nullable=True)
    caption: Mapped[str] = mapped_column(Text, default="")
    transcript: Mapped[str] = mapped_column(Text, default="")
    ocr_text: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    objects: Mapped[list[str]] = mapped_column(JSON, default=list)
    people: Mapped[list[str]] = mapped_column(JSON, default=list)
    embedding: Mapped[list[float]] = mapped_column(JSON, default=list)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    country: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    region: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    place: Mapped[str | None] = mapped_column(String(120), nullable=True)
    landmark: Mapped[str | None] = mapped_column(String(120), nullable=True)
    trip_name: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    thumbnail_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True, index=True)

    segments: Mapped[list["MediaSegment"]] = relationship(
        back_populates="media_item",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class MediaSegment(Base):
    __tablename__ = "media_segments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid4().hex)
    media_id: Mapped[str] = mapped_column(ForeignKey("media_items.id", ondelete="CASCADE"), index=True)
    segment_type: Mapped[str] = mapped_column(String(20), index=True)
    timestamp_start: Mapped[float | None] = mapped_column(Float, nullable=True)
    timestamp_end: Mapped[float | None] = mapped_column(Float, nullable=True)
    caption: Mapped[str] = mapped_column(Text, default="")
    content_text: Mapped[str] = mapped_column(Text, default="")
    embedding: Mapped[list[float]] = mapped_column(JSON, default=list)
    thumbnail_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    media_item: Mapped["MediaItem"] = relationship(back_populates="segments")

