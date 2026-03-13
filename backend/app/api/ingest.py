from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_vector_store, settings_dependency
from app.core.config import Settings
from app.db.session import get_db
from app.schemas.ingest import ScanRequest, ScanResponse
from app.services.ingestion import IngestionService
from app.services.vector_store import VectorStore

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("/scan", response_model=ScanResponse)
def scan_media(
    request: ScanRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dependency),
    vector_store: VectorStore = Depends(get_vector_store),
):
    service = IngestionService(settings, vector_store)
    return service.scan(db, request.paths)

