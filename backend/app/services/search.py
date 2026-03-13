from __future__ import annotations

from datetime import datetime, time

from math import asin, cos, radians, sin, sqrt

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import MediaItem, MediaSegment
from app.schemas.common import Explanation, MediaCard
from app.schemas.search import SearchRequest, SearchResponse
from app.services.embeddings import build_embedding_provider, cosine_similarity
from app.services.vector_store import VectorStore


class SearchService:
    def __init__(self, settings: Settings, vector_store: VectorStore) -> None:
        self.settings = settings
        self.vector_store = vector_store
        self.embedder = build_embedding_provider(settings)

    def search(self, db: Session, request: SearchRequest) -> SearchResponse:
        query = request.query.strip()
        interpreted_filters = self._interpreted_filters(request)
        candidates = db.scalars(self._base_query(request)).all()
        if request.tags:
            lowered_tags = {tag.lower() for tag in request.tags}
            candidates = [
                item for item in candidates if lowered_tags.intersection({tag.lower() for tag in (item.tags or [])})
            ]
        if request.near_latitude is not None and request.near_longitude is not None and request.near_radius_km:
            candidates = [
                item
                for item in candidates
                if item.latitude is not None
                and item.longitude is not None
                and _haversine_km(
                    request.near_latitude,
                    request.near_longitude,
                    item.latitude,
                    item.longitude,
                )
                <= request.near_radius_km
            ]
        if not candidates:
            return SearchResponse(
                query=query,
                interpreted_filters=interpreted_filters,
                explanation="No indexed media matched the current filters.",
                results=[],
            )

        query_vector = self.embedder.embed_text(query or "all media")
        qdrant_scores = self.vector_store.search_media(query_vector, request.media_type, request.limit)
        segment_scores = self.vector_store.search_segments(query_vector, request.limit)
        segment_rows = db.scalars(select(MediaSegment)).all()

        ranked: list[tuple[float, MediaItem, Explanation]] = []
        for item in candidates:
            local_similarity = cosine_similarity(query_vector, item.embedding or [])
            vector_score = qdrant_scores.get(item.id, local_similarity)
            text_score, explanation = self._text_score(item, query)
            segment_score, segment_caption = self._segment_score(item.id, segment_rows, query_vector, query)
            if item.id in segment_scores and segment_scores[item.id].get("score", 0.0) > segment_score:
                segment_score = segment_scores[item.id]["score"]
                segment_caption = segment_scores[item.id].get("caption") or segment_scores[item.id].get("content_text")
            score = (vector_score * 0.55) + (text_score * 0.3) + (segment_score * 0.15)
            explanation.visual_similarity = round(vector_score, 3)
            if segment_caption:
                explanation.transcript_match = segment_caption
            ranked.append((score, item, explanation))

        ranked.sort(key=lambda row: row[0], reverse=True)
        results = [self._to_card(item, score, explanation) for score, item, explanation in ranked[: request.limit]]
        summary = self._summary(query, results, interpreted_filters)
        return SearchResponse(
            query=query,
            interpreted_filters=interpreted_filters,
            explanation=summary,
            results=results,
        )

    def _base_query(self, request: SearchRequest):
        conditions = [MediaItem.deleted_at.is_(None)]
        if request.media_type:
            conditions.append(MediaItem.media_type == request.media_type)
        if request.country:
            conditions.append(MediaItem.country.ilike(f"%{request.country}%"))
        if request.region:
            conditions.append(MediaItem.region.ilike(f"%{request.region}%"))
        if request.city:
            conditions.append(MediaItem.city.ilike(f"%{request.city}%"))
        if request.date_from:
            conditions.append(MediaItem.date_taken >= datetime.combine(request.date_from, time.min))
        if request.date_to:
            conditions.append(MediaItem.date_taken <= datetime.combine(request.date_to, time.max))
        if request.bbox:
            parts = [float(part) for part in request.bbox.split(",")]
            if len(parts) == 4:
                min_lat, min_lng, max_lat, max_lng = parts
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
        return select(MediaItem).where(and_(*conditions))

    def _text_score(self, item: MediaItem, query: str) -> tuple[float, Explanation]:
        if not query:
            return 0.55, Explanation(caption_match=item.caption)
        lowered = query.lower()
        parts = [part for part in lowered.split() if part]
        haystacks = {
            "caption": item.caption.lower(),
            "transcript": (item.transcript or "").lower(),
            "ocr": (item.ocr_text or "").lower(),
            "location": " ".join(filter(None, [item.country, item.region, item.city, item.place, item.landmark])).lower(),
            "tags": " ".join(item.tags).lower(),
        }
        matches = 0
        matched_tags = [tag for tag in item.tags if tag.lower() in parts]
        for token in parts:
            if any(token in value for value in haystacks.values()):
                matches += 1
        score = matches / max(len(parts), 1)
        explanation = Explanation(
            caption_match=item.caption if any(token in haystacks["caption"] for token in parts) else None,
            transcript_match=item.transcript if any(token in haystacks["transcript"] for token in parts) else None,
            location_match=item.place or item.city if any(token in haystacks["location"] for token in parts) else None,
            matched_tags=matched_tags,
        )
        return score, explanation

    def _segment_score(
        self, media_id: str, segments: list[MediaSegment], query_vector: list[float], query: str
    ) -> tuple[float, str | None]:
        best_score = 0.0
        best_label = None
        for segment in segments:
            if segment.media_id != media_id:
                continue
            score = cosine_similarity(query_vector, segment.embedding or [])
            if query and query.lower() in (segment.content_text or "").lower():
                score += 0.2
            if score > best_score:
                best_score = score
                best_label = segment.caption or segment.content_text
        return min(best_score, 1.0), best_label

    @staticmethod
    def _to_card(item: MediaItem, score: float, explanation: Explanation) -> MediaCard:
        return MediaCard(
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
            score=round(score, 3),
            explanation=explanation,
            trip_name=item.trip_name,
        )

    @staticmethod
    def _interpreted_filters(request: SearchRequest) -> dict:
        return {
            "media_type": request.media_type,
            "country": request.country,
            "region": request.region,
            "city": request.city,
            "tags": request.tags,
            "date_from": request.date_from.isoformat() if request.date_from else None,
            "date_to": request.date_to.isoformat() if request.date_to else None,
        }

    @staticmethod
    def _summary(query: str, results: list[MediaCard], interpreted_filters: dict) -> str:
        if not results:
            return "No results matched the current query."
        top = results[0]
        location = ", ".join(filter(None, [top.place, top.city, top.country]))
        filter_summary = ", ".join(f"{key}={value}" for key, value in interpreted_filters.items() if value)
        base = f"Top match: {top.filename} because it aligns with the caption, tags, and geo context"
        if location:
            base += f" around {location}"
        if query:
            base += f" for '{query}'."
        else:
            base += "."
        if filter_summary:
            base += f" Active filters: {filter_summary}."
        return base


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_radius_km = 6371.0
    delta_lat = radians(lat2 - lat1)
    delta_lon = radians(lon2 - lon1)
    a = (
        sin(delta_lat / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(delta_lon / 2) ** 2
    )
    return 2 * earth_radius_km * asin(sqrt(a))
