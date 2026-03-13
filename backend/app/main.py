from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.ingest import router as ingest_router
from app.api.map import router as map_router
from app.api.media import router as media_router
from app.api.search import router as search_router
from app.api.deps import get_vector_store
from app.core.config import get_settings
from app.db.session import Base, SessionLocal, engine
from app.models import MediaItem
from app.services.demo_seed import seed_demo_data


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    Base.metadata.create_all(bind=engine)
    vector_store = get_vector_store()
    vector_store.ensure_collections(settings.embedding_dimensions)
    with SessionLocal() as db:
        if settings.seed_demo_data:
            seed_demo_data(db, settings)
        for item in db.query(MediaItem).all():
            vector_store.upsert_media(item)
            vector_store.upsert_segments(item)
    yield


def build_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.backend_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router, prefix=settings.api_prefix)
    app.include_router(ingest_router, prefix=settings.api_prefix)
    app.include_router(map_router, prefix=settings.api_prefix)
    app.include_router(media_router, prefix=settings.api_prefix)
    app.include_router(search_router, prefix=settings.api_prefix)

    @app.get("/")
    def root():
        return {"name": settings.app_name, "docs": "/docs", "api": settings.api_prefix}

    return app


app = build_app()
