from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import MediaItem, MediaSegment
from app.schemas.ingest import ScanResponse
from app.services.embeddings import build_embedding_provider
from app.services.metadata import (
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    caption_and_tags_from_path,
    compute_checksum,
    extract_image_metadata,
    extract_video_metadata,
    infer_location_from_path,
)
from app.services.vector_store import VectorStore


class IngestionService:
    def __init__(self, settings: Settings, vector_store: VectorStore) -> None:
        self.settings = settings
        self.vector_store = vector_store
        self.embedder = build_embedding_provider(settings)

    def scan(self, db: Session, paths: list[str]) -> ScanResponse:
        created = 0
        updated = 0
        skipped = 0
        deleted = 0
        warnings: list[str] = []
        scanned = 0
        seen_paths: set[str] = set()

        roots = [Path(path).expanduser() for path in (paths or self.settings.media_roots)]
        if not roots:
            return ScanResponse(scanned=0, created=0, updated=0, deleted=0, skipped=0, warnings=["No media roots configured."])

        for root in roots:
            if not root.exists():
                warnings.append(f"Path not found: {root}")
                continue
            for file_path in root.rglob("*"):
                if not file_path.is_file():
                    continue
                extension = file_path.suffix.lower()
                if extension not in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS:
                    continue
                scanned += 1
                seen_paths.add(str(file_path))
                status = self._upsert_file(db, file_path)
                if status == "created":
                    created += 1
                elif status == "updated":
                    updated += 1
                else:
                    skipped += 1

        if seen_paths:
            for item in db.scalars(select(MediaItem).where(MediaItem.deleted_at.is_(None))).all():
                root_match = any(item.source_path.startswith(str(root)) for root in roots)
                if root_match and item.source_path not in seen_paths:
                    item.deleted_at = datetime.utcnow()
                    deleted += 1

        db.commit()
        return ScanResponse(
            scanned=scanned,
            created=created,
            updated=updated,
            deleted=deleted,
            skipped=skipped,
            warnings=warnings,
        )

    def _upsert_file(self, db: Session, file_path: Path) -> str:
        checksum = compute_checksum(file_path)
        existing_by_path = db.scalar(select(MediaItem).where(MediaItem.source_path == str(file_path)))
        moved_item = db.scalar(select(MediaItem).where(MediaItem.checksum == checksum))

        metadata = (
            extract_image_metadata(file_path)
            if file_path.suffix.lower() in IMAGE_EXTENSIONS
            else extract_video_metadata(file_path)
        )
        media_type = metadata.get("media_type", "image")
        caption, tags, objects = caption_and_tags_from_path(file_path, media_type)
        location = infer_location_from_path(file_path)
        stat = file_path.stat()
        embedding_source = " ".join(
            filter(
                None,
                [
                    caption,
                    " ".join(tags),
                    location.get("place"),
                    location.get("country"),
                ],
            )
        )
        embedding = self.embedder.embed_text(embedding_source)

        item = existing_by_path or moved_item
        created = item is None
        if item is None:
            item = MediaItem(checksum=checksum, source_path=str(file_path), filename=file_path.name, media_type=media_type)
            db.add(item)

        item.checksum = checksum
        item.source_path = str(file_path)
        item.filename = file_path.name
        item.media_type = media_type
        item.file_size = stat.st_size
        item.created_at_fs = datetime.fromtimestamp(stat.st_ctime)
        item.modified_at_fs = datetime.fromtimestamp(stat.st_mtime)
        item.date_taken = metadata.get("date_taken") or item.modified_at_fs
        item.width = metadata.get("width")
        item.height = metadata.get("height")
        item.duration = metadata.get("duration")
        item.frame_rate = metadata.get("frame_rate")
        item.codec = metadata.get("codec")
        item.camera_model = metadata.get("camera_model")
        item.lens = metadata.get("lens")
        item.focal_length = metadata.get("focal_length")
        item.orientation = metadata.get("orientation")
        item.caption = caption
        item.tags = tags
        item.objects = objects
        item.latitude = (
            metadata.get("latitude")
            if metadata.get("latitude") is not None
            else location.get("latitude")
            if location.get("latitude") is not None
            else item.latitude
        )
        item.longitude = (
            metadata.get("longitude")
            if metadata.get("longitude") is not None
            else location.get("longitude")
            if location.get("longitude") is not None
            else item.longitude
        )
        item.country = location.get("country") or item.country
        item.region = location.get("region") or item.region
        item.city = location.get("city") or item.city
        item.place = location.get("place") or item.place
        item.landmark = location.get("landmark") or item.landmark
        item.embedding = embedding
        item.metadata_json = metadata
        item.last_seen_at = datetime.utcnow()
        item.deleted_at = None

        db.flush()
        item.segments.clear()
        if item.media_type == "video":
            self._build_video_segments(item)

        self.vector_store.upsert_media(item)
        self.vector_store.upsert_segments(item)
        return "created" if created else "updated"

    def _build_video_segments(self, item: MediaItem) -> None:
        if not item.duration:
            return
        checkpoints = [round(item.duration * ratio, 1) for ratio in (0.15, 0.5, 0.85)]
        for checkpoint in checkpoints:
            caption = f"Keyframe around {checkpoint:.1f}s for {item.filename}."
            item.segments.append(
                MediaSegment(
                    segment_type="keyframe",
                    timestamp_start=checkpoint,
                    timestamp_end=checkpoint,
                    caption=caption,
                    content_text=" ".join(item.tags),
                    embedding=self.embedder.embed_text(" ".join([caption, item.caption, item.place or ""])),
                )
            )
