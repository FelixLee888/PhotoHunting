from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import MediaItem
from app.services.metadata import infer_trip_from_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill trip_name from the nearest meaningful parent folder.")
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
                select(MediaItem.id, MediaItem.source_path, MediaItem.trip_name)
                .order_by(MediaItem.id)
                .limit(args.batch_size)
            )
            if last_id is not None:
                statement = statement.where(MediaItem.id > last_id)

            rows = db.execute(statement).all()
            if not rows:
                break

            for row in rows:
                if args.max_items and scanned >= args.max_items:
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

                item = db.get(MediaItem, row.id)
                if item is None:
                    continue

                metadata_json = dict(item.metadata_json or {})
                item.trip_name = trip_name
                if trip_info:
                    metadata_json["trip_folder"] = trip_info
                else:
                    metadata_json.pop("trip_folder", None)
                item.metadata_json = metadata_json
                updated += 1

            db.commit()

    print(f"scanned={scanned}")
    print(f"updated={updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
