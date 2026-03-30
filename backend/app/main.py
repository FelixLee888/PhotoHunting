from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError

from app.api.analysis import router as analysis_router
from app.api.health import router as health_router
from app.api.ingest import router as ingest_router
from app.api.library import router as library_router
from app.api.map import router as map_router
from app.api.media import router as media_router
from app.api.search import router as search_router
from app.api.deps import get_vector_store
from app.core.config import get_settings
from app.db.session import Base, SessionLocal, engine, ensure_sqlite_indexes, ensure_sqlite_schema
from app.models import MediaItem
from app.services.demo_seed import seed_demo_data

logger = logging.getLogger(__name__)


def _is_transient_sqlite_lock(error: OperationalError) -> bool:
    return "database is locked" in str(error).lower()


def _should_run_sqlite_startup_maintenance(settings) -> bool:
    if not settings.database_url.startswith("sqlite:///"):
        return True
    database_path = Path(settings.database_url.removeprefix("sqlite:///")).expanduser()
    return not database_path.exists() or database_path.stat().st_size == 0


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    if _should_run_sqlite_startup_maintenance(settings):
        Base.metadata.create_all(bind=engine)
        try:
            ensure_sqlite_schema()
            ensure_sqlite_indexes()
        except OperationalError as exc:
            if not _is_transient_sqlite_lock(exc):
                raise
            logger.warning("Skipping SQLite startup maintenance because the database is locked: %s", exc)
    else:
        logger.info("Skipping SQLite startup maintenance for existing database %s", settings.database_url)
    vector_store = get_vector_store()
    vector_store.ensure_collections(settings.embedding_dimensions)
    with SessionLocal() as db:
        if settings.seed_demo_data:
            seed_demo_data(db, settings)
        if vector_store.is_available():
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
    app.include_router(analysis_router, prefix=settings.api_prefix)
    app.include_router(ingest_router, prefix=settings.api_prefix)
    app.include_router(library_router, prefix=settings.api_prefix)
    app.include_router(map_router, prefix=settings.api_prefix)
    app.include_router(media_router, prefix=settings.api_prefix)
    app.include_router(search_router, prefix=settings.api_prefix)

    static_frontend_dir = Path(settings.static_frontend_dir).expanduser() if settings.static_frontend_dir else None
    if static_frontend_dir and static_frontend_dir.exists():
        app.mount("/", StaticFiles(directory=static_frontend_dir, html=True), name="frontend")
    else:
        @app.get("/")
        def root():
            return {"name": settings.app_name, "docs": "/docs", "api": settings.api_prefix}

    return app


app = build_app()
