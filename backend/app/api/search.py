from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_vector_store, settings_dependency
from app.core.config import Settings
from app.db.session import get_db
from app.schemas.search import SearchRequest, SearchResponse
from app.services.search import SearchService
from app.services.vector_store import VectorStore

router = APIRouter(prefix="/search", tags=["search"])


@router.post("/query", response_model=SearchResponse)
def search_media(
    request: SearchRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dependency),
    vector_store: VectorStore = Depends(get_vector_store),
):
    service = SearchService(settings, vector_store)
    return service.search(db, request)

