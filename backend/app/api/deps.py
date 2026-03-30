from __future__ import annotations

from functools import lru_cache

from app.core.config import Settings, get_settings
from app.services.analysis_queue import AnalysisQueueService
from app.services.map_service import MapService
from app.services.vector_store import VectorStore


@lru_cache
def get_vector_store() -> VectorStore:
    return VectorStore(get_settings())


@lru_cache
def get_map_service() -> MapService:
    return MapService()


@lru_cache
def get_analysis_queue_service() -> AnalysisQueueService:
    return AnalysisQueueService(get_settings(), get_vector_store())


def settings_dependency() -> Settings:
    return get_settings()
