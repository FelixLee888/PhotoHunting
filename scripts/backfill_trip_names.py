from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import bindparam, select, update

from app.db.session import SessionLocal
from app.models import MediaItem
from app.services.metadata import infer_trip_from_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill trip_name from dated trip folders like 'YYYY-MM-DD Trip Name' or "
            "'YYYY-MM Trip Name', falling back to the nearest meaningful parent folder."
        )
    )
    parser.add_argument("--batch-size", type=int, default=250)
    parser.add_argument("--max-items", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    updated = 0
    scanned = 0
    last_id: str | None = None

    with SessionLocal() as db:
        while True:
            statement = (
                select(MediaItem.id, MediaItem.source_path, MediaItem.trip_name, MediaItem.metadata_json)
                .order_by(MediaItem.id)
                .limit(args.batch_size)
            )
            if last_id is not None:
                statement = statement.where(MediaItem.id > last_id)

            rows = db.execute(statement).all()
            if not rows:
                break

            updates: list[dict[str, object]] = []
            for row in rows:
                if args.max_items and scanned >= args.max_items:
                    if updates:
                        db.execute(
                            update(MediaItem)
                            .where(MediaItem.id == bindparam("target_id"))
                            .values(
                                trip_name=bindparam("trip_name"),
                                metadata_json=bindparam("metadata_json"),
                            ),
                            updates,
                        )
                    db.commit()
                    print(f"scanned={scanned}")
                    print(f"updated={updated}")
                    return 0

                scanned += 1
                last_id = row.id
                trip_info = infer_trip_from_path(Path(row.source_path))
                trip_name = trip_info.get("trip_name")

                if row.trip_name == trip_name:
                    continue

                metadata_json = dict(row.metadata_json or {})
                if trip_info:
                    metadata_json["trip_folder"] = trip_info
                else:
                    metadata_json.pop("trip_folder", None)
                updates.append(
                    {
                        "target_id": row.id,
                        "trip_name": trip_name,
                        "metadata_json": metadata_json,
                    }
                )
                updated += 1

            if updates:
                db.execute(
                    update(MediaItem)
                    .where(MediaItem.id == bindparam("target_id"))
                    .values(
                        trip_name=bindparam("trip_name"),
                        metadata_json=bindparam("metadata_json"),
                    ),
                    updates,
                )
            db.commit()

    print(f"scanned={scanned}")
    print(f"updated={updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
