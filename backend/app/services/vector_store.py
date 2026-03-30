from __future__ import annotations

from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from app.core.config import Settings
from app.models import MediaItem, MediaSegment


class VectorStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client: QdrantClient | None = None
        if not settings.qdrant_url or settings.qdrant_url.lower() in {"none", "disabled", "local"}:
            return
        try:
            self.client = QdrantClient(url=settings.qdrant_url, timeout=3.0)
        except Exception:
            self.client = None

    def is_available(self) -> bool:
        return self.client is not None

    def ensure_collections(self, dimensions: int) -> None:
        if not self.client:
            return
        vector_config = qmodels.VectorParams(size=dimensions, distance=qmodels.Distance.COSINE)
        for suffix in ("image_items", "video_items", "video_keyframes", "text_chunks"):
            name = f"{self.settings.qdrant_collection_prefix}_{suffix}"
            try:
                self.client.get_collection(name)
            except Exception:
                try:
                    self.client.create_collection(collection_name=name, vectors_config=vector_config)
                except Exception:
                    self.client = None
                    return

    def upsert_media(self, item: MediaItem) -> None:
        if not self.client or not item.embedding:
            return
        collection = f"{self.settings.qdrant_collection_prefix}_{'image_items' if item.media_type == 'image' else 'video_items'}"
        self.client.upsert(
            collection_name=collection,
            points=[
                qmodels.PointStruct(
                    id=item.id,
                    vector=item.embedding,
                    payload=self._media_payload(item),
                )
            ],
        )

    def upsert_segments(self, item: MediaItem) -> None:
        if not self.client:
            return
        keyframe_points = []
        text_points = []
        for segment in item.segments:
            if not segment.embedding:
                continue
            payload = {
                "media_id": item.id,
                "segment_type": segment.segment_type,
                "caption": segment.caption,
                "content_text": segment.content_text,
                "timestamp_start": segment.timestamp_start,
                "timestamp_end": segment.timestamp_end,
                "country": item.country,
                "city": item.city,
            }
            point = qmodels.PointStruct(id=segment.id, vector=segment.embedding, payload=payload)
            if segment.segment_type == "keyframe":
                keyframe_points.append(point)
            else:
                text_points.append(point)
        if keyframe_points:
            self.client.upsert(
                collection_name=f"{self.settings.qdrant_collection_prefix}_video_keyframes",
                points=keyframe_points,
            )
        if text_points:
            self.client.upsert(
                collection_name=f"{self.settings.qdrant_collection_prefix}_text_chunks",
                points=text_points,
            )

    def search_media(self, query_vector: list[float], media_type: str | None, limit: int) -> dict[str, float]:
        if not self.client or not query_vector:
            return {}
        collections = []
        if media_type in {None, "", "image"}:
            collections.append(f"{self.settings.qdrant_collection_prefix}_image_items")
        if media_type in {None, "", "video"}:
            collections.append(f"{self.settings.qdrant_collection_prefix}_video_items")
        scores: dict[str, float] = {}
        for collection in collections:
            try:
                results = self.client.search(collection_name=collection, query_vector=query_vector, limit=limit)
            except Exception:
                continue
            for point in results:
                scores[str(point.id)] = max(scores.get(str(point.id), 0.0), float(point.score or 0.0))
        return scores

    def search_segments(self, query_vector: list[float], limit: int) -> dict[str, dict[str, Any]]:
        if not self.client or not query_vector:
            return {}
        segment_matches: dict[str, dict[str, Any]] = {}
        for collection in (
            f"{self.settings.qdrant_collection_prefix}_video_keyframes",
            f"{self.settings.qdrant_collection_prefix}_text_chunks",
        ):
            try:
                points = self.client.search(collection_name=collection, query_vector=query_vector, limit=limit)
            except Exception:
                continue
            for point in points:
                payload = point.payload or {}
                media_id = str(payload.get("media_id"))
                existing = segment_matches.get(media_id, {"score": 0.0})
                if float(point.score or 0.0) > existing["score"]:
                    segment_matches[media_id] = {
                        "score": float(point.score or 0.0),
                        "caption": payload.get("caption"),
                        "content_text": payload.get("content_text"),
                    }
        return segment_matches

    @staticmethod
    def _media_payload(item: MediaItem) -> dict[str, Any]:
        return {
            "file_id": item.id,
            "source_path": item.source_path,
            "media_type": item.media_type,
            "caption": item.caption,
            "tags": item.tags,
            "objects": item.objects,
            "people": item.people,
            "location_name": item.place,
            "country": item.country,
            "city": item.city,
            "latitude": item.latitude,
            "longitude": item.longitude,
            "date_taken": item.date_taken.isoformat() if item.date_taken else None,
            "duration": item.duration,
            "transcript": item.transcript,
            "ocr_text": item.ocr_text,
            "checksum": item.checksum,
        }
