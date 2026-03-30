from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()

is_sqlite = settings.database_url.startswith("sqlite")
connect_args = {"check_same_thread": False, "timeout": 30} if is_sqlite else {}
engine_kwargs = {
    "connect_args": connect_args,
    "future": True,
}
if is_sqlite:
    engine_kwargs["poolclass"] = NullPool
else:
    engine_kwargs["pool_pre_ping"] = True
engine = create_engine(settings.database_url, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session, future=True)


if is_sqlite:
    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, _):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        try:
            cursor.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            pass
        cursor.close()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_sqlite_indexes() -> None:
    if not is_sqlite:
        return

    statements = (
        "CREATE INDEX IF NOT EXISTS idx_media_active_date ON media_items (deleted_at, date_taken DESC)",
        "CREATE INDEX IF NOT EXISTS idx_media_active_analysis_date ON media_items (deleted_at, analysis_status, date_taken DESC)",
        "CREATE INDEX IF NOT EXISTS idx_media_active_type_date ON media_items (deleted_at, media_type, date_taken DESC)",
        "CREATE INDEX IF NOT EXISTS idx_media_active_geo ON media_items (deleted_at, latitude, longitude)",
        "CREATE INDEX IF NOT EXISTS idx_media_trip_active_date ON media_items (deleted_at, trip_name, date_taken DESC)",
        "CREATE INDEX IF NOT EXISTS idx_media_analysis_queue ON media_items (deleted_at, media_type, analysis_status, analysis_heartbeat_at)",
        "CREATE INDEX IF NOT EXISTS idx_media_analysis_worker ON media_items (analysis_status, analysis_worker_id, analysis_heartbeat_at)",
        "CREATE INDEX IF NOT EXISTS idx_media_analysis_pending_scan ON media_items (deleted_at, media_type, analysis_status, indexed_at ASC, id)",
        "CREATE INDEX IF NOT EXISTS idx_media_analysis_completed_scan ON media_items (deleted_at, media_type, analysis_status, analysis_completed_at ASC, indexed_at ASC, id)",
        "CREATE INDEX IF NOT EXISTS idx_segments_media_type ON media_segments (media_id, segment_type)",
    )

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def ensure_sqlite_schema() -> None:
    if not is_sqlite:
        return

    media_item_columns = {
        "analysis_status": "ALTER TABLE media_items ADD COLUMN analysis_status VARCHAR(20)",
        "analysis_attempts": "ALTER TABLE media_items ADD COLUMN analysis_attempts INTEGER DEFAULT 0",
        "analysis_worker_id": "ALTER TABLE media_items ADD COLUMN analysis_worker_id VARCHAR(120)",
        "analysis_claimed_at": "ALTER TABLE media_items ADD COLUMN analysis_claimed_at DATETIME",
        "analysis_heartbeat_at": "ALTER TABLE media_items ADD COLUMN analysis_heartbeat_at DATETIME",
        "analysis_model": "ALTER TABLE media_items ADD COLUMN analysis_model VARCHAR(160)",
        "analysis_version": "ALTER TABLE media_items ADD COLUMN analysis_version VARCHAR(80)",
        "analysis_error": "ALTER TABLE media_items ADD COLUMN analysis_error TEXT",
        "analysis_completed_at": "ALTER TABLE media_items ADD COLUMN analysis_completed_at DATETIME",
        "caption_ai": "ALTER TABLE media_items ADD COLUMN caption_ai TEXT",
        "caption_dense": "ALTER TABLE media_items ADD COLUMN caption_dense TEXT",
        "tags_json": "ALTER TABLE media_items ADD COLUMN tags_json JSON",
        "objects_json": "ALTER TABLE media_items ADD COLUMN objects_json JSON",
        "landmarks_json": "ALTER TABLE media_items ADD COLUMN landmarks_json JSON",
        "scene_json": "ALTER TABLE media_items ADD COLUMN scene_json JSON",
    }

    with engine.begin() as connection:
        existing_columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(media_items)")).fetchall()
        }
        for column_name, statement in media_item_columns.items():
            if column_name not in existing_columns:
                connection.execute(text(statement))

        connection.execute(
            text(
                """
                UPDATE media_items
                SET analysis_attempts = COALESCE(analysis_attempts, 0),
                    analysis_status = CASE
                        WHEN analysis_status IS NOT NULL THEN analysis_status
                        WHEN media_type = 'image' AND deleted_at IS NULL THEN 'pending'
                        WHEN media_type = 'video' THEN 'skipped'
                        ELSE analysis_status
                    END
                """
            )
        )
