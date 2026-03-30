from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

from sqlalchemy import Text, and_, cast, func, or_, select
from sqlalchemy.orm import Session

from app.models import MediaItem
from app.schemas.map import MapPoint, MapResponse, MapRoute
from app.services.previews import preview_url_for_media


@dataclass(slots=True)
class RawMapPoint:
    media_id: str
    latitude: float
    longitude: float
    thumbnail: str | None
    caption: str | None
    date: datetime | None
    media_type: str
    tags: list[str]
    location: str | None
    trip_name: str | None
    cluster_size: int = 1


class MapService:
    def get_points(
        self,
        db: Session,
        *,
        bbox: str | None,
        zoom: int | None,
        media_type: str | None,
        analysis_status: str | None,
        trip_name: str | None,
        country: str | None,
        region: str | None,
        city: str | None,
        tag: str | None,
        date_from,
        date_to,
    ) -> MapResponse:
        conditions = [MediaItem.deleted_at.is_(None), MediaItem.latitude.is_not(None), MediaItem.longitude.is_not(None)]
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
            min_lat, min_lng, max_lat, max_lng = [float(part) for part in bbox.split(",")]
            conditions.extend(
                [
                    MediaItem.latitude >= min_lat,
                    MediaItem.latitude <= max_lat,
                    MediaItem.longitude >= min_lng,
                    MediaItem.longitude <= max_lng,
                ]
            )

        effective_zoom = zoom or 2
        if effective_zoom < 7:
            points = self._clustered_points(db, conditions, effective_zoom)
            return MapResponse(
                points=[
                    MapPoint(
                        media_id=point.media_id,
                        latitude=point.latitude,
                        longitude=point.longitude,
                        cluster_size=point.cluster_size,
                        thumbnail=point.thumbnail,
                        caption=point.caption,
                        date=point.date,
                        media_type=point.media_type,
                        tags=point.tags,
                        location=point.location,
                        trip_name=point.trip_name,
                    )
                    for point in points
                ],
                routes=[],
            )

        rows = db.execute(
            select(
                MediaItem.id,
                MediaItem.latitude,
                MediaItem.longitude,
                MediaItem.thumbnail_url,
                MediaItem.analysis_status,
                MediaItem.caption,
                MediaItem.date_taken,
                MediaItem.media_type,
                MediaItem.tags,
                MediaItem.place,
                MediaItem.city,
                MediaItem.country,
                MediaItem.trip_name,
            )
            .where(and_(*conditions))
            .order_by(MediaItem.date_taken)
        ).all()

        raw_points = [
            RawMapPoint(
                media_id=row.id,
                latitude=row.latitude,
                longitude=row.longitude,
                thumbnail=preview_url_for_media(row.id, row.media_type, row.analysis_status, row.thumbnail_url),
                caption=row.caption,
                date=row.date_taken,
                media_type=row.media_type,
                tags=list(row.tags or []),
                location=", ".join(filter(None, [row.place, row.city, row.country])),
                trip_name=row.trip_name,
            )
            for row in rows
            if row.latitude is not None and row.longitude is not None
        ]

        routes = []
        if effective_zoom >= 7:
            route_groups: dict[str, list[RawMapPoint]] = {}
            for point in raw_points:
                if point.trip_name:
                    route_groups.setdefault(point.trip_name, []).append(point)

            routes = [
                MapRoute(
                    trip_name=trip_name,
                    media_ids=[point.media_id for point in route_points],
                    coordinates=[[point.latitude, point.longitude] for point in route_points],
                )
                for trip_name, route_points in route_groups.items()
                if len(route_points) > 1
            ]

        return MapResponse(
            points=[
                MapPoint(
                    media_id=point.media_id,
                    latitude=point.latitude,
                    longitude=point.longitude,
                    cluster_size=point.cluster_size,
                    thumbnail=point.thumbnail,
                    caption=point.caption,
                    date=point.date,
                    media_type=point.media_type,
                    tags=point.tags,
                    location=point.location,
                    trip_name=point.trip_name,
                )
                for point in raw_points
            ],
            routes=routes,
        )

    @staticmethod
    def _clustered_points(db: Session, conditions, zoom: int) -> list[RawMapPoint]:
        decimals = 0 if zoom <= 2 else 1 if zoom <= 4 else 2
        lat_bucket = func.round(MediaItem.latitude, decimals)
        lng_bucket = func.round(MediaItem.longitude, decimals)
        rows = db.execute(
            select(
                func.min(MediaItem.id).label("media_id"),
                lat_bucket.label("latitude"),
                lng_bucket.label("longitude"),
                func.count(MediaItem.id).label("cluster_size"),
                func.max(MediaItem.date_taken).label("date"),
                func.min(MediaItem.media_type).label("media_type"),
                func.min(MediaItem.caption).label("caption"),
                func.min(MediaItem.place).label("place"),
                func.min(MediaItem.city).label("city"),
                func.min(MediaItem.country).label("country"),
            )
            .where(and_(*conditions))
            .group_by(lat_bucket, lng_bucket)
            .order_by(func.max(MediaItem.date_taken).desc())
        ).all()
        return [
            RawMapPoint(
                media_id=row.media_id,
                latitude=row.latitude,
                longitude=row.longitude,
                thumbnail=None,
                caption=row.caption,
                date=row.date,
                media_type=row.media_type,
                tags=[],
                location=", ".join(filter(None, [row.place, row.city, row.country])),
                trip_name=None,
                cluster_size=int(row.cluster_size or 1),
            )
            for row in rows
            if row.latitude is not None and row.longitude is not None
        ]
