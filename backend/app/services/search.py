from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from math import asin, cos, radians, sin, sqrt
import re

from sqlalchemy import Text, and_, case, cast, func, literal, or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import MediaItem, MediaSegment
from app.schemas.common import Explanation, MediaCard
from app.schemas.search import SearchRequest, SearchResponse
from app.services.embeddings import build_embedding_provider, cosine_similarity
from app.services.previews import preview_url_for_media
from app.services.vector_store import VectorStore

TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
QUERY_STOP_WORDS = {
    "the", "a", "an", "me", "my", "show", "find", "search", "searching", "photos", "photo", "images", "image",
    "videos", "video", "pictures", "picture", "of", "for", "please", "some", "with", "from", "where",
}
PHRASE_FILLER_WORDS = {"show", "find", "search", "photos", "photo", "images", "image", "videos", "video", "pictures", "picture", "please"}
SEMANTIC_SYNONYMS = {
    "cat": {"cats", "kitten", "kittens", "kitty", "feline"},
    "dog": {"dogs", "puppy", "puppies", "canine"},
    "hike": {"hiking", "trail", "trek", "walk", "walking"},
    "mountain": {"mountains", "peak", "peaks", "ridge", "ridges", "summit", "cliff", "cliffs"},
    "snow": {"snowy", "winter", "icy", "ice"},
    "beach": {"shore", "coast", "coastal", "seaside"},
    "boat": {"boats", "kayak", "canoe", "sailing", "ship"},
    "sunset": {"sunrise", "dusk", "golden", "evening"},
    "food": {"meal", "dinner", "lunch", "breakfast", "restaurant"},
    "receipt": {"receipts", "invoice", "bill", "document"},
}


@dataclass(slots=True)
class CandidateMedia:
    id: str
    filename: str
    source_path: str
    media_type: str
    caption: str
    tags: list[str]
    country: str | None
    region: str | None
    city: str | None
    place: str | None
    landmark: str | None
    latitude: float | None
    longitude: float | None
    thumbnail_url: str | None
    date_taken: datetime | None
    duration: float | None
    transcript: str | None
    ocr_text: str | None
    trip_name: str | None
    analysis_status: str | None
    analysis_completed_at: datetime | None
    analysis_model: str | None
    analysis_version: str | None
    caption_ai: str | None
    caption_dense: str | None
    tags_json: list[str]
    objects_json: list[str]
    landmarks_json: list[str]
    scene_json: dict
    sql_score: float = 0.0


class SearchService:
    FAST_CANDIDATE_LIMIT = 720
    VECTOR_RERANK_LIMIT = 60
    SEGMENT_RERANK_LIMIT = 24

    def __init__(self, settings: Settings, vector_store: VectorStore) -> None:
        self.settings = settings
        self.vector_store = vector_store
        self.embedder = build_embedding_provider(settings)

    def search(self, db: Session, request: SearchRequest) -> SearchResponse:
        query = request.query.strip()
        interpreted_filters = self._interpreted_filters(request)
        page_offset = max(request.offset, 0)
        page_limit = max(request.limit, 1)

        if not query:
            recent_items = self._recent_candidates(db, request, page_limit)
            visible_items = recent_items[page_offset: page_offset + page_limit]
            return SearchResponse(
                query="",
                interpreted_filters=interpreted_filters,
                explanation=self._summary("", [self._to_card(item) for item in visible_items], interpreted_filters),
                total_count=len(recent_items),
                results=[self._to_card(item) for item in visible_items],
            )

        total_count = self._search_total_count(db, request, query)
        if total_count == 0:
            return SearchResponse(
                query=query,
                interpreted_filters=interpreted_filters,
                explanation="No results matched the current query.",
                total_count=0,
                results=[],
            )

        candidate_limit = min(max((page_limit + page_offset) * 8, 80), self.FAST_CANDIDATE_LIMIT)
        candidates = self._search_candidates(db, request, query, candidate_limit)
        if not candidates:
            return SearchResponse(
                query=query,
                interpreted_filters=interpreted_filters,
                explanation="No results matched the current query.",
                total_count=0,
                results=[],
            )

        query_vector = self.embedder.embed_query(query)
        qdrant_scores = (
            self.vector_store.search_media(query_vector, request.media_type, min(request.limit * 3, self.VECTOR_RERANK_LIMIT))
            if query_vector and self.vector_store.is_available()
            else {}
        )

        candidate_ids_for_vectors = [item.id for item in candidates[: self.VECTOR_RERANK_LIMIT] if item.id not in qdrant_scores]
        local_vector_scores = self._local_vector_scores(db, candidate_ids_for_vectors, query_vector)

        pre_ranked: list[tuple[float, CandidateMedia, Explanation]] = []
        for item in candidates:
            text_score, explanation = self._text_score(item, query)
            vector_score = qdrant_scores.get(item.id, local_vector_scores.get(item.id, 0.0))
            blended_text = max(text_score, item.sql_score)
            score = (blended_text * 0.78) + (vector_score * 0.22)
            if vector_score > 0:
                explanation.visual_similarity = round(vector_score, 3)
            pre_ranked.append((score, item, explanation))

        pre_ranked.sort(key=lambda row: row[0], reverse=True)
        segment_ids = [item.id for _, item, _ in pre_ranked[: self.SEGMENT_RERANK_LIMIT]]
        segment_rows = self._segment_rows(db, segment_ids)

        ranked: list[tuple[float, CandidateMedia, Explanation]] = []
        for score, item, explanation in pre_ranked:
            segment_score, segment_caption = self._segment_score(item.id, segment_rows, query_vector, query)
            if segment_caption and not explanation.transcript_match:
                explanation.transcript_match = segment_caption
            ranked.append(((score * 0.9) + (segment_score * 0.1), item, explanation))

        ranked.sort(key=lambda row: row[0], reverse=True)
        paged_ranked = ranked[page_offset: page_offset + page_limit]
        results = [self._to_card(item, score, explanation) for score, item, explanation in paged_ranked]
        summary = self._summary(query, results, interpreted_filters)
        return SearchResponse(
            query=query,
            interpreted_filters=interpreted_filters,
            explanation=summary,
            total_count=total_count,
            results=results,
        )

    def _recent_candidates(self, db: Session, request: SearchRequest, limit: int) -> list[CandidateMedia]:
        statement = (
            select(*self._projection_columns(), literal(0.0).label("sql_score"))
            .where(and_(*self._base_conditions(request)))
            .order_by(func.coalesce(MediaItem.date_taken, MediaItem.indexed_at).desc())
            .limit(max(limit * 3, limit))
        )
        rows = db.execute(statement).all()
        candidates = [self._candidate_from_row(row) for row in rows]
        candidates = self._apply_near_filter(candidates, request)
        return candidates[:limit]

    def _search_candidates(self, db: Session, request: SearchRequest, query: str, limit: int) -> list[CandidateMedia]:
        tokens = self._query_terms(query, include_synonyms=False)
        phrases = self._query_phrases(query)
        signals = [*tokens, *phrases]
        if not signals:
            return self._recent_candidates(db, request, limit)

        pattern_matches = []
        score_expr = literal(0.0)
        for token in tokens:
            pattern = f"%{token}%"
            pattern_matches.append(self._pattern_match(pattern))
            score_expr = score_expr + self._weighted_pattern_score(pattern)

        statement = (
            select(*self._projection_columns(), score_expr.label("sql_score"))
            .where(and_(*self._base_conditions(request)))
            .where(or_(*pattern_matches))
            .order_by(score_expr.desc(), func.coalesce(MediaItem.date_taken, MediaItem.indexed_at).desc())
            .limit(limit)
        )
        rows = db.execute(statement).all()
        candidates = [self._candidate_from_row(row, signal_count=len(signals)) for row in rows]
        return self._apply_near_filter(candidates, request)

    def _search_total_count(self, db: Session, request: SearchRequest, query: str) -> int:
        tokens = self._query_terms(query, include_synonyms=False)
        if not tokens:
            return 0
        pattern_matches = [self._pattern_match(f"%{token}%") for token in tokens]
        count_statement = (
            select(func.count(MediaItem.id))
            .where(and_(*self._base_conditions(request)))
            .where(or_(*pattern_matches))
        )
        return int(db.execute(count_statement).scalar() or 0)

    @staticmethod
    def _projection_columns():
        return (
            MediaItem.id,
            MediaItem.filename,
            MediaItem.source_path,
            MediaItem.media_type,
            MediaItem.caption,
            MediaItem.tags,
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
            MediaItem.transcript,
            MediaItem.ocr_text,
            MediaItem.trip_name,
            MediaItem.analysis_status,
            MediaItem.analysis_completed_at,
            MediaItem.analysis_model,
            MediaItem.analysis_version,
            MediaItem.caption_ai,
            MediaItem.caption_dense,
            MediaItem.tags_json,
            MediaItem.objects_json,
            MediaItem.landmarks_json,
            MediaItem.scene_json,
        )

    @staticmethod
    def _search_fields():
        return (
            MediaItem.filename,
            MediaItem.caption,
            MediaItem.caption_ai,
            MediaItem.caption_dense,
            MediaItem.transcript,
            MediaItem.ocr_text,
            MediaItem.country,
            MediaItem.region,
            MediaItem.city,
            MediaItem.place,
            MediaItem.landmark,
            cast(MediaItem.tags, Text),
            cast(MediaItem.tags_json, Text),
            cast(MediaItem.objects_json, Text),
            cast(MediaItem.landmarks_json, Text),
            cast(MediaItem.scene_json, Text),
        )

    @classmethod
    def _pattern_match(cls, pattern: str):
        return or_(*(field.ilike(pattern) for field in cls._search_fields()))

    @staticmethod
    def _weighted_pattern_score(pattern: str, *, phrase: bool = False):
        base_weights = (
            (MediaItem.caption_ai, 7.5),
            (MediaItem.caption_dense, 7.0),
            (MediaItem.caption, 5.5),
            (cast(MediaItem.objects_json, Text), 6.8),
            (cast(MediaItem.landmarks_json, Text), 7.2),
            (cast(MediaItem.tags_json, Text), 6.2),
            (cast(MediaItem.tags, Text), 5.2),
            (cast(MediaItem.scene_json, Text), 5.2),
            (MediaItem.place, 4.8),
            (MediaItem.landmark, 5.0),
            (MediaItem.city, 4.2),
            (MediaItem.region, 3.8),
            (MediaItem.country, 3.8),
            (MediaItem.filename, 3.4),
            (MediaItem.transcript, 2.8),
            (MediaItem.ocr_text, 2.8),
        )
        weight_multiplier = 1.25 if phrase else 1.0
        score_expr = literal(0.0)
        for field, weight in base_weights:
            score_expr = score_expr + case((field.ilike(pattern), weight * weight_multiplier), else_=0.0)
        return score_expr

    def _base_conditions(self, request: SearchRequest):
        conditions = [MediaItem.deleted_at.is_(None)]
        if request.media_type:
            conditions.append(MediaItem.media_type == request.media_type)
        if request.analysis_status:
            conditions.append(MediaItem.analysis_status == request.analysis_status)
        if request.trip_name:
            conditions.append(MediaItem.trip_name == request.trip_name)
        if request.country:
            conditions.append(MediaItem.country.ilike(f"%{request.country}%"))
        if request.region:
            conditions.append(MediaItem.region.ilike(f"%{request.region}%"))
        if request.city:
            conditions.append(or_(MediaItem.city.ilike(f"%{request.city}%"), MediaItem.place.ilike(f"%{request.city}%")))
        if request.tags:
            for tag in request.tags:
                conditions.append(cast(MediaItem.tags, Text).ilike(f"%{tag}%"))
        if request.date_from:
            conditions.append(MediaItem.date_taken >= datetime.combine(request.date_from, time.min))
        if request.date_to:
            conditions.append(MediaItem.date_taken <= datetime.combine(request.date_to, time.max))
        if request.bbox:
            try:
                min_lat, min_lng, max_lat, max_lng = [float(part) for part in request.bbox.split(",")]
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

    def _apply_near_filter(self, candidates: list[CandidateMedia], request: SearchRequest) -> list[CandidateMedia]:
        if request.near_latitude is None or request.near_longitude is None or not request.near_radius_km:
            return candidates
        return [
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

    @staticmethod
    def _candidate_from_row(row, *, signal_count: int = 1) -> CandidateMedia:
        mapping = row._mapping
        normalized_sql_score = float(mapping.get("sql_score") or 0.0)
        if signal_count > 0:
            normalized_sql_score = min(normalized_sql_score / max(signal_count * 10.0, 1.0), 1.0)
        return CandidateMedia(
            id=mapping["id"],
            filename=mapping["filename"],
            source_path=mapping["source_path"],
            media_type=mapping["media_type"],
            caption=mapping["caption"] or "",
            tags=list(mapping["tags"] or []),
            country=mapping["country"],
            region=mapping["region"],
            city=mapping["city"],
            place=mapping["place"],
            landmark=mapping["landmark"],
            latitude=mapping["latitude"],
            longitude=mapping["longitude"],
            thumbnail_url=mapping["thumbnail_url"],
            date_taken=mapping["date_taken"],
            duration=mapping["duration"],
            transcript=mapping["transcript"],
            ocr_text=mapping["ocr_text"],
            trip_name=mapping["trip_name"],
            analysis_status=mapping["analysis_status"],
            analysis_completed_at=mapping["analysis_completed_at"],
            analysis_model=mapping["analysis_model"],
            analysis_version=mapping["analysis_version"],
            caption_ai=mapping["caption_ai"],
            caption_dense=mapping["caption_dense"],
            tags_json=list(mapping["tags_json"] or []),
            objects_json=list(mapping["objects_json"] or []),
            landmarks_json=list(mapping["landmarks_json"] or []),
            scene_json=dict(mapping["scene_json"] or {}),
            sql_score=normalized_sql_score,
        )

    def _local_vector_scores(self, db: Session, candidate_ids: list[str], query_vector: list[float]) -> dict[str, float]:
        if not candidate_ids or not query_vector:
            return {}
        rows = db.execute(select(MediaItem.id, MediaItem.embedding).where(MediaItem.id.in_(candidate_ids))).all()
        return {
            row._mapping["id"]: cosine_similarity(query_vector, row._mapping["embedding"] or [])
            for row in rows
        }

    def _segment_rows(self, db: Session, candidate_ids: list[str]) -> list[MediaSegment]:
        if not candidate_ids:
            return []
        return db.scalars(select(MediaSegment).where(MediaSegment.media_id.in_(candidate_ids))).all()

    @staticmethod
    def _query_terms(query: str, *, include_synonyms: bool = True) -> list[str]:
        raw_terms = TOKEN_PATTERN.findall(query.lower())
        expanded_terms: list[str] = []
        for token in raw_terms:
            if len(token) <= 1 or token in QUERY_STOP_WORDS:
                continue
            expansions = {token}
            if token.endswith("ies") and len(token) > 4:
                expansions.add(f"{token[:-3]}y")
            elif token.endswith("es") and len(token) > 3:
                expansions.add(token[:-2])
            elif token.endswith("s") and len(token) > 3:
                expansions.add(token[:-1])
            if include_synonyms:
                for root, synonyms in SEMANTIC_SYNONYMS.items():
                    if token == root or token in synonyms or any(form == root or form in synonyms for form in expansions):
                        expansions.add(root)
                        expansions.update(synonyms)
            expanded_terms.extend(sorted(expansions))
        return list(dict.fromkeys(expanded_terms))

    @staticmethod
    def _query_phrases(query: str) -> list[str]:
        raw_terms = [
            token
            for token in TOKEN_PATTERN.findall(query.lower())
            if len(token) > 1 and token not in PHRASE_FILLER_WORDS
        ]
        phrases: list[str] = []
        if len(raw_terms) >= 2:
            for size in (4, 3, 2):
                for index in range(0, max(len(raw_terms) - size + 1, 0)):
                    phrase = " ".join(raw_terms[index:index + size]).strip()
                    if len(phrase) >= 6:
                        phrases.append(phrase)
        return list(dict.fromkeys(phrases[:8]))

    @staticmethod
    def _semantic_tags(item: CandidateMedia) -> list[str]:
        merged = [
            *item.tags,
            *item.tags_json,
            *item.objects_json,
            *item.landmarks_json,
        ]
        return list(dict.fromkeys(tag for tag in merged if tag))

    @staticmethod
    def _scene_text(item: CandidateMedia) -> str:
        if not item.scene_json:
            return ""
        return " ".join(f"{key} {value}" for key, value in item.scene_json.items()).lower()

    def _text_score(self, item: CandidateMedia, query: str) -> tuple[float, Explanation]:
        parts = self._query_terms(query)
        phrases = self._query_phrases(query)
        semantic_tags = self._semantic_tags(item)
        haystacks = {
            "caption": " ".join(filter(None, [item.caption_ai, item.caption_dense, item.caption])).lower(),
            "transcript": (item.transcript or "").lower(),
            "ocr": (item.ocr_text or "").lower(),
            "location": " ".join(filter(None, [item.country, item.region, item.city, item.place, item.landmark])).lower(),
            "tags": " ".join(semantic_tags).lower(),
            "filename": item.filename.lower(),
            "scene": self._scene_text(item),
        }
        token_hits = 0
        phrase_hits = 0
        matched_tags = [tag for tag in semantic_tags if any(term in tag.lower() or tag.lower() in term for term in parts)]
        for token in parts:
            if any(token in value for value in haystacks.values()):
                token_hits += 1
        for phrase in phrases:
            if any(phrase in value for value in haystacks.values()):
                phrase_hits += 1

        score_numerator = (token_hits * 0.7) + (phrase_hits * 1.2) + (min(len(matched_tags), 4) * 0.15)
        score_denominator = max((len(parts) * 0.7) + (len(phrases) * 1.2), 1.0)
        score = min(score_numerator / score_denominator, 1.0)

        caption_match = next(
            (
                value
                for value in [item.caption_ai, item.caption_dense, item.caption]
                if value and (
                    any(token in value.lower() for token in parts)
                    or any(phrase in value.lower() for phrase in phrases)
                )
            ),
            None,
        )
        transcript_match = next(
            (
                value
                for value in [item.transcript, item.ocr_text]
                if value and (
                    any(token in value.lower() for token in parts)
                    or any(phrase in value.lower() for phrase in phrases)
                )
            ),
            None,
        )
        location_match = None
        if any(token in haystacks["location"] for token in parts) or any(phrase in haystacks["location"] for phrase in phrases):
            location_match = item.place or item.landmark or item.city or item.country

        explanation = Explanation(
            caption_match=caption_match,
            transcript_match=transcript_match,
            location_match=location_match,
            matched_tags=matched_tags,
        )
        return score, explanation

    @staticmethod
    def _segment_score(
        media_id: str, segments: list[MediaSegment], query_vector: list[float], query: str
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
    def _to_card(item: CandidateMedia, score: float | None = None, explanation: Explanation | None = None) -> MediaCard:
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
            thumbnail_url=preview_url_for_media(item.id, item.media_type, item.analysis_status, item.thumbnail_url),
            date_taken=item.date_taken,
            duration=item.duration,
            transcript=item.transcript,
            ocr_text=item.ocr_text,
            score=round(score, 3) if score is not None else None,
            explanation=explanation,
            trip_name=item.trip_name,
            analysis_status=item.analysis_status,
            analysis_completed_at=item.analysis_completed_at,
            analysis_model=item.analysis_model,
            analysis_version=item.analysis_version,
        )

    @staticmethod
    def _interpreted_filters(request: SearchRequest) -> dict:
        return {
            "media_type": request.media_type,
            "analysis_status": request.analysis_status,
            "trip_name": request.trip_name,
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
        if not query:
            base = "Showing recent indexed media"
            if filter_summary:
                base += f" with filters: {filter_summary}."
            else:
                base += "."
            return base

        base = f"Top match: {top.filename} because it aligns with the caption, tags, and geo context"
        if location:
            base += f" around {location}"
        base += f" for '{query}'."
        if filter_summary:
            base += f" Active filters: {filter_summary}."
        return base

    @staticmethod
    def _tokens(query: str) -> list[str]:
        return SearchService._query_terms(query)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_radius_km = 6371.0
    delta_lat = radians(lat2 - lat1)
    delta_lon = radians(lon2 - lon1)
    a = (
        sin(delta_lat / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(delta_lon / 2) ** 2
    )
    return 2 * earth_radius_km * asin(sqrt(a))
