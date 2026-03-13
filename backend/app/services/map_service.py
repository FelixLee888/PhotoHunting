from __future__ import annotations

from datetime import datetime, time

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models import MediaItem
from app.schemas.map import MapPoint, MapResponse, MapRoute


class MapService:
    def get_points(
        self,
        db: Session,
        *,
        bbox: str | None,
        media_type: str | None,
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
        if country:
            conditions.append(MediaItem.country.ilike(f"%{country}%"))
        if region:
            conditions.append(MediaItem.region.ilike(f"%{region}%"))
        if city:
            conditions.append(MediaItem.city.ilike(f"%{city}%"))
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

        items = db.scalars(select(MediaItem).where(and_(*conditions)).order_by(MediaItem.date_taken)).all()
        if tag:
            items = [item for item in items if tag.lower() in {value.lower() for value in item.tags}]
        points = [
            MapPoint(
                media_id=item.id,
                latitude=item.latitude,
                longitude=item.longitude,
                thumbnail=item.thumbnail_url,
                caption=item.caption,
                date=item.date_taken,
                media_type=item.media_type,
                tags=item.tags,
                location=", ".join(filter(None, [item.place, item.city, item.country])),
                trip_name=item.trip_name,
            )
            for item in items
            if item.latitude is not None and item.longitude is not None
        ]

        route_groups: dict[str, list[MediaItem]] = {}
        for item in items:
            if item.trip_name:
                route_groups.setdefault(item.trip_name, []).append(item)

        routes = [
            MapRoute(
                trip_name=trip_name,
                media_ids=[item.id for item in route_items],
                coordinates=[
                    [item.latitude, item.longitude]
                    for item in route_items
                    if item.latitude is not None and item.longitude is not None
                ],
            )
            for trip_name, route_items in route_groups.items()
            if len(route_items) > 1
        ]
        return MapResponse(points=points, routes=routes)
