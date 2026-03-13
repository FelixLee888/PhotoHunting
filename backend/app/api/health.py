from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_vector_store, settings_dependency
from app.core.config import Settings
from app.services.vector_store import VectorStore

router = APIRouter(tags=["health"])


@router.get("/health")
def health(settings: Settings = Depends(settings_dependency), vector_store: VectorStore = Depends(get_vector_store)):
    return {
        "status": "ok",
        "app": settings.app_name,
        "embedding_provider": settings.embedding_provider,
        "qdrant_available": vector_store.is_available(),
    }

