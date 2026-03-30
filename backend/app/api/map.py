from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_map_service
from app.db.session import get_db
from app.schemas.map import MapResponse
from app.services.map_service import MapService

router = APIRouter(prefix="/map", tags=["map"])


@router.get("/points", response_model=MapResponse)
def get_map_points(
    bbox: str | None = Query(default=None, description="min_lat,min_lng,max_lat,max_lng"),
    zoom: int | None = Query(default=None, ge=1, le=18),
    media_type: str | None = None,
    analysis_status: str | None = None,
    trip_name: str | None = None,
    country: str | None = None,
    region: str | None = None,
    city: str | None = None,
    tag: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
    map_service: MapService = Depends(get_map_service),
):
    return map_service.get_points(
        db,
        bbox=bbox,
        zoom=zoom,
        media_type=media_type,
        analysis_status=analysis_status,
        trip_name=trip_name,
        country=country,
        region=region,
        city=city,
        tag=tag,
        date_from=date_from,
        date_to=date_to,
    )
