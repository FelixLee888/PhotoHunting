from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.models import AnalysisStatus, MediaItem

ALL_SCOPE = "*"
TOTALS_SCOPE_KEY = "all_active"


def ensure_materialized_summary_schema(connection: Connection) -> None:
    statements = (
        """
        CREATE TABLE IF NOT EXISTS media_summary_years (
            media_scope TEXT NOT NULL,
            status_scope TEXT NOT NULL,
            year_value INTEGER NOT NULL,
            item_count INTEGER NOT NULL,
            refreshed_at DATETIME NOT NULL,
            PRIMARY KEY (media_scope, status_scope, year_value)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS media_summary_months (
            media_scope TEXT NOT NULL,
            status_scope TEXT NOT NULL,
            year_value INTEGER NOT NULL,
            month_value INTEGER NOT NULL,
            item_count INTEGER NOT NULL,
            refreshed_at DATETIME NOT NULL,
            PRIMARY KEY (media_scope, status_scope, year_value, month_value)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS media_summary_trips (
            media_scope TEXT NOT NULL,
            status_scope TEXT NOT NULL,
            trip_name TEXT NOT NULL,
            item_count INTEGER NOT NULL,
            earliest_date DATETIME,
            latest_date DATETIME,
            cover_media_id TEXT,
            refreshed_at DATETIME NOT NULL,
            PRIMARY KEY (media_scope, status_scope, trip_name)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS media_summary_totals (
            scope_key TEXT PRIMARY KEY,
            indexed_media INTEGER NOT NULL,
            indexed_images INTEGER NOT NULL,
            indexed_videos INTEGER NOT NULL,
            mapped_media INTEGER NOT NULL,
            trip_routes INTEGER NOT NULL,
            last_indexed_at DATETIME,
            refreshed_at DATETIME NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_media_summary_years_scope ON media_summary_years (media_scope, status_scope, year_value DESC)",
        "CREATE INDEX IF NOT EXISTS idx_media_summary_months_scope ON media_summary_months (media_scope, status_scope, year_value DESC, month_value DESC)",
        "CREATE INDEX IF NOT EXISTS idx_media_summary_trips_scope ON media_summary_trips (media_scope, status_scope, earliest_date DESC, trip_name ASC)",
        "CREATE INDEX IF NOT EXISTS idx_media_summary_trips_cover ON media_summary_trips (cover_media_id)",
    )
    for statement in statements:
        connection.execute(text(statement))


def materialized_summaries_need_bootstrap(connection: Connection) -> bool:
    ensure_materialized_summary_schema(connection)
    totals_count = connection.execute(
        text("SELECT COUNT(*) FROM media_summary_totals WHERE scope_key = :scope_key"),
        {"scope_key": TOTALS_SCOPE_KEY},
    ).scalar_one()
    years_count = connection.execute(text("SELECT COUNT(*) FROM media_summary_years")).scalar_one()
    months_count = connection.execute(text("SELECT COUNT(*) FROM media_summary_months")).scalar_one()
    trips_count = connection.execute(text("SELECT COUNT(*) FROM media_summary_trips")).scalar_one()
    return totals_count == 0 or years_count == 0 or months_count == 0 or trips_count == 0


def rebuild_materialized_summaries(connection: Connection) -> None:
    ensure_materialized_summary_schema(connection)
    connection.execute(text("DELETE FROM media_summary_years"))
    connection.execute(text("DELETE FROM media_summary_months"))
    connection.execute(text("DELETE FROM media_summary_trips"))

    year_counts: dict[tuple[str, str, int], int] = defaultdict(int)
    month_counts: dict[tuple[str, str, int, int], int] = defaultdict(int)
    trip_summaries: dict[tuple[str, str, str], dict[str, Any]] = {}

    rows = connection.execute(
        text(
            """
            SELECT
                id,
                media_type,
                COALESCE(analysis_status, '') AS analysis_status,
                trip_name,
                date_taken,
                indexed_at
            FROM media_items
            WHERE deleted_at IS NULL
            """
        )
    ).mappings()

    for row in rows:
        media_scopes = (str(row["media_type"]), ALL_SCOPE)
        status_scopes = (str(row["analysis_status"] or ""), ALL_SCOPE)
        date_taken = _as_datetime(row["date_taken"])
        indexed_at = _as_datetime(row["indexed_at"])

        if date_taken is not None:
            year_value = date_taken.year
            month_value = date_taken.month
            for media_scope in media_scopes:
                for status_scope in status_scopes:
                    year_counts[(media_scope, status_scope, year_value)] += 1
                    month_counts[(media_scope, status_scope, year_value, month_value)] += 1

        trip_name = row["trip_name"]
        if not trip_name:
            continue

        summary_date = date_taken or indexed_at
        if summary_date is None:
            continue
        cover_sort = (
            1 if date_taken is None else 0,
            date_taken or indexed_at,
            indexed_at,
            str(row["id"]),
        )
        for media_scope in media_scopes:
            for status_scope in status_scopes:
                key = (media_scope, status_scope, str(trip_name))
                summary = trip_summaries.get(key)
                if summary is None:
                    trip_summaries[key] = {
                        "count": 1,
                        "earliest_date": summary_date,
                        "latest_date": summary_date,
                        "cover_media_id": str(row["id"]),
                        "cover_sort": cover_sort,
                    }
                    continue
                summary["count"] = int(summary["count"]) + 1
                if summary_date < summary["earliest_date"]:
                    summary["earliest_date"] = summary_date
                if summary_date > summary["latest_date"]:
                    summary["latest_date"] = summary_date
                if cover_sort < summary["cover_sort"]:
                    summary["cover_sort"] = cover_sort
                    summary["cover_media_id"] = str(row["id"])

    if year_counts:
        connection.execute(
            text(
                """
                INSERT INTO media_summary_years (
                    media_scope,
                    status_scope,
                    year_value,
                    item_count,
                    refreshed_at
                ) VALUES (
                    :media_scope,
                    :status_scope,
                    :year_value,
                    :item_count,
                    CURRENT_TIMESTAMP
                )
                """
            ),
            [
                {
                    "media_scope": media_scope,
                    "status_scope": status_scope,
                    "year_value": year_value,
                    "item_count": item_count,
                }
                for (media_scope, status_scope, year_value), item_count in year_counts.items()
            ],
        )

    if month_counts:
        connection.execute(
            text(
                """
                INSERT INTO media_summary_months (
                    media_scope,
                    status_scope,
                    year_value,
                    month_value,
                    item_count,
                    refreshed_at
                ) VALUES (
                    :media_scope,
                    :status_scope,
                    :year_value,
                    :month_value,
                    :item_count,
                    CURRENT_TIMESTAMP
                )
                """
            ),
            [
                {
                    "media_scope": media_scope,
                    "status_scope": status_scope,
                    "year_value": year_value,
                    "month_value": month_value,
                    "item_count": item_count,
                }
                for (media_scope, status_scope, year_value, month_value), item_count in month_counts.items()
            ],
        )

    if trip_summaries:
        connection.execute(
            text(
                """
                INSERT INTO media_summary_trips (
                    media_scope,
                    status_scope,
                    trip_name,
                    item_count,
                    earliest_date,
                    latest_date,
                    cover_media_id,
                    refreshed_at
                ) VALUES (
                    :media_scope,
                    :status_scope,
                    :trip_name,
                    :item_count,
                    :earliest_date,
                    :latest_date,
                    :cover_media_id,
                    CURRENT_TIMESTAMP
                )
                """
            ),
            [
                {
                    "media_scope": media_scope,
                    "status_scope": status_scope,
                    "trip_name": trip_name,
                    "item_count": int(summary["count"]),
                    "earliest_date": summary["earliest_date"],
                    "latest_date": summary["latest_date"],
                    "cover_media_id": summary["cover_media_id"],
                }
                for (media_scope, status_scope, trip_name), summary in trip_summaries.items()
            ],
        )

    refresh_materialized_totals(connection)


def refresh_media_item_summaries(
    session: Session,
    media_id: str,
    *,
    previous_analysis_status: str | None = None,
) -> None:
    row = session.execute(
        select(
            MediaItem.id,
            MediaItem.media_type,
            MediaItem.analysis_status,
            MediaItem.trip_name,
            MediaItem.date_taken,
            MediaItem.deleted_at,
        ).where(MediaItem.id == media_id)
    ).one_or_none()
    if row is None:
        return

    media_scope = row.media_type or ALL_SCOPE
    status_scopes = {ALL_SCOPE, _status_scope(previous_analysis_status), _status_scope(row.analysis_status)}
    year_value = row.date_taken.year if row.date_taken else None
    month_value = row.date_taken.month if row.date_taken else None

    connection = session.connection()
    for status_scope in status_scopes:
        _refresh_year_summary(connection, media_scope=media_scope, status_scope=status_scope, year_value=year_value)
        _refresh_year_summary(connection, media_scope=ALL_SCOPE, status_scope=status_scope, year_value=year_value)
        _refresh_month_summary(
            connection,
            media_scope=media_scope,
            status_scope=status_scope,
            year_value=year_value,
            month_value=month_value,
        )
        _refresh_month_summary(
            connection,
            media_scope=ALL_SCOPE,
            status_scope=status_scope,
            year_value=year_value,
            month_value=month_value,
        )
        _refresh_trip_summary(
            connection,
            media_scope=media_scope,
            status_scope=status_scope,
            trip_name=row.trip_name,
        )
        _refresh_trip_summary(
            connection,
            media_scope=ALL_SCOPE,
            status_scope=status_scope,
            trip_name=row.trip_name,
        )
    refresh_materialized_totals(connection)
    session.commit()

    invalidate_runtime_caches()


def refresh_materialized_totals(connection: Connection) -> None:
    connection.execute(text("DELETE FROM media_summary_totals WHERE scope_key = :scope_key"), {"scope_key": TOTALS_SCOPE_KEY})
    connection.execute(
        text(
            """
            INSERT INTO media_summary_totals (
                scope_key,
                indexed_media,
                indexed_images,
                indexed_videos,
                mapped_media,
                trip_routes,
                last_indexed_at,
                refreshed_at
            )
            SELECT
                :scope_key,
                COUNT(*),
                SUM(CASE WHEN media_type = 'image' THEN 1 ELSE 0 END),
                SUM(CASE WHEN media_type = 'video' THEN 1 ELSE 0 END),
                SUM(CASE WHEN latitude IS NOT NULL AND longitude IS NOT NULL THEN 1 ELSE 0 END),
                COALESCE((
                    SELECT COUNT(*)
                    FROM (
                        SELECT trip_name
                        FROM media_items
                        WHERE deleted_at IS NULL
                          AND trip_name IS NOT NULL
                        GROUP BY trip_name
                        HAVING COUNT(id) > 1
                    )
                ), 0),
                MAX(COALESCE(last_seen_at, indexed_at)),
                CURRENT_TIMESTAMP
            FROM media_items
            WHERE deleted_at IS NULL
            """
        ),
        {"scope_key": TOTALS_SCOPE_KEY},
    )


def list_materialized_years(db: Session, *, media_type: str | None, analysis_status: str | None) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            """
            SELECT year_value, item_count
            FROM media_summary_years
            WHERE media_scope = :media_scope
              AND status_scope = :status_scope
            ORDER BY year_value DESC
            """
        ),
        {
            "media_scope": _scope(media_type),
            "status_scope": _status_scope_for_query(analysis_status),
        },
    ).mappings().all()
    return [dict(row) for row in rows]


def list_materialized_months(
    db: Session,
    *,
    media_type: str | None,
    analysis_status: str | None,
    year: int | None = None,
) -> list[dict[str, Any]]:
    params = {
        "media_scope": _scope(media_type),
        "status_scope": _status_scope_for_query(analysis_status),
    }
    if year is None:
        rows = db.execute(
            text(
                """
                SELECT year_value, month_value, item_count
                FROM media_summary_months
                WHERE media_scope = :media_scope
                  AND status_scope = :status_scope
                ORDER BY year_value DESC, month_value DESC
                """
            ),
            params,
        ).mappings().all()
    else:
        params["year_value"] = year
        rows = db.execute(
            text(
                """
                SELECT year_value, month_value, item_count
                FROM media_summary_months
                WHERE media_scope = :media_scope
                  AND status_scope = :status_scope
                  AND year_value = :year_value
                ORDER BY month_value DESC
                """
            ),
            params,
        ).mappings().all()
    return [dict(row) for row in rows]


def list_materialized_trips(
    db: Session,
    *,
    media_type: str | None,
    analysis_status: str | None,
    limit: int,
    offset: int = 0,
) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            """
            SELECT trip_name, item_count, earliest_date, latest_date, cover_media_id
            FROM media_summary_trips
            WHERE media_scope = :media_scope
              AND status_scope = :status_scope
            ORDER BY earliest_date DESC, trip_name ASC
            LIMIT :limit_value OFFSET :offset_value
            """
        ),
        {
            "media_scope": _scope(media_type),
            "status_scope": _status_scope_for_query(analysis_status),
            "limit_value": limit,
            "offset_value": offset,
        },
    ).mappings().all()
    return [dict(row) for row in rows]


def get_materialized_totals(db: Session) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT
                indexed_media,
                indexed_images,
                indexed_videos,
                mapped_media,
                trip_routes,
                last_indexed_at
            FROM media_summary_totals
            WHERE scope_key = :scope_key
            """
        ),
        {"scope_key": TOTALS_SCOPE_KEY},
    ).mappings().one_or_none()
    return dict(row) if row else None


def has_active_analysis_workers(db: Session, stale_minutes: int) -> bool:
    cutoff = int(datetime.utcnow().timestamp() - (stale_minutes * 60))
    claimed = db.scalar(
        select(func.count(MediaItem.id)).where(
            MediaItem.analysis_status == AnalysisStatus.CLAIMED.value,
            func.strftime("%s", func.coalesce(MediaItem.analysis_heartbeat_at, MediaItem.analysis_claimed_at)) >= cutoff,
        )
    ) or 0
    return claimed > 0


def invalidate_runtime_caches() -> None:
    from app.api import library as library_api
    from app.api import media as media_api
    from app.api import tv as tv_api

    media_api.invalidate_media_summary_caches()
    tv_api.invalidate_tv_home_caches()
    library_api.invalidate_library_stats_cache()


def prewarm_runtime_caches(db: Session) -> None:
    from app.api import library as library_api
    from app.api import media as media_api
    from app.api import tv as tv_api

    library_api.get_library_stats(db=db)
    media_api.list_media_months(media_type=None, analysis_status="completed", db=db)
    media_api.list_media_trips(limit=60, media_type=None, analysis_status="completed", db=db)
    tv_api.get_tv_home(recent_limit=50, trip_limit=12, trip_offset=0, analysis_status="completed", db=db)
    tv_api.get_tv_home(recent_limit=50, trip_limit=12, trip_offset=12, analysis_status="completed", db=db)


def run_sqlite_optimize(connection: Connection) -> None:
    connection.execute(text("PRAGMA optimize"))


def run_sqlite_analyze(connection: Connection) -> None:
    connection.execute(text("ANALYZE"))


def _refresh_year_summary(
    connection: Connection,
    *,
    media_scope: str,
    status_scope: str,
    year_value: int | None,
) -> None:
    if year_value is None:
        return

    params = {
        "media_scope": media_scope,
        "status_scope": status_scope,
        "year_value": year_value,
    }
    count_value = connection.execute(
        text(
            f"""
            SELECT COUNT(*)
            FROM media_items
            WHERE deleted_at IS NULL
              AND date_taken IS NOT NULL
              AND CAST(strftime('%Y', date_taken) AS INTEGER) = :year_value
              {_scope_predicate('media_type', 'media_scope')}
              {_scope_predicate("COALESCE(analysis_status, '')", 'status_scope')}
            """
        ),
        params,
    ).scalar_one()
    if count_value == 0:
        connection.execute(
            text(
                """
                DELETE FROM media_summary_years
                WHERE media_scope = :media_scope
                  AND status_scope = :status_scope
                  AND year_value = :year_value
                """
            ),
            params,
        )
        return
    connection.execute(
        text(
            """
            INSERT OR REPLACE INTO media_summary_years (
                media_scope,
                status_scope,
                year_value,
                item_count,
                refreshed_at
            ) VALUES (
                :media_scope,
                :status_scope,
                :year_value,
                :item_count,
                CURRENT_TIMESTAMP
            )
            """
        ),
        {
            **params,
            "item_count": int(count_value),
        },
    )


def _refresh_month_summary(
    connection: Connection,
    *,
    media_scope: str,
    status_scope: str,
    year_value: int | None,
    month_value: int | None,
) -> None:
    if year_value is None or month_value is None:
        return

    params = {
        "media_scope": media_scope,
        "status_scope": status_scope,
        "year_value": year_value,
        "month_value": month_value,
    }
    count_value = connection.execute(
        text(
            f"""
            SELECT COUNT(*)
            FROM media_items
            WHERE deleted_at IS NULL
              AND date_taken IS NOT NULL
              AND CAST(strftime('%Y', date_taken) AS INTEGER) = :year_value
              AND CAST(strftime('%m', date_taken) AS INTEGER) = :month_value
              {_scope_predicate('media_type', 'media_scope')}
              {_scope_predicate("COALESCE(analysis_status, '')", 'status_scope')}
            """
        ),
        params,
    ).scalar_one()
    if count_value == 0:
        connection.execute(
            text(
                """
                DELETE FROM media_summary_months
                WHERE media_scope = :media_scope
                  AND status_scope = :status_scope
                  AND year_value = :year_value
                  AND month_value = :month_value
                """
            ),
            params,
        )
        return
    connection.execute(
        text(
            """
            INSERT OR REPLACE INTO media_summary_months (
                media_scope,
                status_scope,
                year_value,
                month_value,
                item_count,
                refreshed_at
            ) VALUES (
                :media_scope,
                :status_scope,
                :year_value,
                :month_value,
                :item_count,
                CURRENT_TIMESTAMP
            )
            """
        ),
        {
            **params,
            "item_count": int(count_value),
        },
    )


def _refresh_trip_summary(
    connection: Connection,
    *,
    media_scope: str,
    status_scope: str,
    trip_name: str | None,
) -> None:
    if not trip_name:
        return

    params = {
        "media_scope": media_scope,
        "status_scope": status_scope,
        "trip_name": trip_name,
    }
    aggregate = connection.execute(
        text(
            f"""
            SELECT
                COUNT(*) AS item_count,
                MIN(COALESCE(date_taken, indexed_at)) AS earliest_date,
                MAX(COALESCE(date_taken, indexed_at)) AS latest_date
            FROM media_items
            WHERE deleted_at IS NULL
              AND trip_name = :trip_name
              {_scope_predicate('media_type', 'media_scope')}
              {_scope_predicate("COALESCE(analysis_status, '')", 'status_scope')}
            """
        ),
        params,
    ).mappings().one()
    item_count = int(aggregate["item_count"] or 0)
    if item_count == 0:
        connection.execute(
            text(
                """
                DELETE FROM media_summary_trips
                WHERE media_scope = :media_scope
                  AND status_scope = :status_scope
                  AND trip_name = :trip_name
                """
            ),
            params,
        )
        return

    cover_media_id = connection.execute(
        text(
            f"""
            SELECT id
            FROM media_items
            WHERE deleted_at IS NULL
              AND trip_name = :trip_name
              {_scope_predicate('media_type', 'media_scope')}
              {_scope_predicate("COALESCE(analysis_status, '')", 'status_scope')}
            ORDER BY
                CASE WHEN date_taken IS NULL THEN 1 ELSE 0 END,
                date_taken ASC,
                indexed_at ASC,
                id ASC
            LIMIT 1
            """
        ),
        params,
    ).scalar_one_or_none()

    connection.execute(
        text(
            """
            INSERT OR REPLACE INTO media_summary_trips (
                media_scope,
                status_scope,
                trip_name,
                item_count,
                earliest_date,
                latest_date,
                cover_media_id,
                refreshed_at
            ) VALUES (
                :media_scope,
                :status_scope,
                :trip_name,
                :item_count,
                :earliest_date,
                :latest_date,
                :cover_media_id,
                CURRENT_TIMESTAMP
            )
            """
        ),
        {
            **params,
            "item_count": item_count,
            "earliest_date": aggregate["earliest_date"],
            "latest_date": aggregate["latest_date"],
            "cover_media_id": cover_media_id,
        },
    )


def _scope(value: str | None) -> str:
    return value or ALL_SCOPE


def _status_scope(value: str | None) -> str:
    return value if value is not None else ""


def _status_scope_for_query(value: str | None) -> str:
    return value if value is not None else ALL_SCOPE


def _scope_predicate(column_sql: str, scope_param: str) -> str:
    return f"AND (:{scope_param} = '{ALL_SCOPE}' OR {column_sql} = :{scope_param})"


def _as_datetime(value):
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None
    return None
