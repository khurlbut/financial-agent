from __future__ import annotations

import argparse
from pathlib import Path
from datetime import datetime, timezone

from . import settings
from .schwab_csv import db as schwab_db
from .schwab_csv.importer import import_positions_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh Schwab holdings by importing one or more Schwab positions CSVs")
    parser.add_argument(
        "--container-id",
        type=str,
        default=None,
        help="Schwab container id (e.g., kev, deb). This represents a Schwab login; accounts within the login are discovered from each CSV.",
    )
    parser.add_argument(
        "--csv",
        type=str,
        action="append",
        default=[],
        help="Path to a Schwab positions CSV to import (repeatable)",
    )
    parser.add_argument(
        "--csv-dir",
        type=str,
        default=None,
        help="Directory containing Schwab positions CSVs; imports all *.csv files (sorted by mtime)",
    )
    args = parser.parse_args()

    db_path = settings.get_finagent_db_path()

    # Create one snapshot per refresh run so multiple CSVs (accounts) are visible together.
    as_of = datetime.now(timezone.utc)
    container_id = (args.container_id or settings.get_schwab_container_id()).strip() or "schwab"

    csv_paths: list[Path] = []

    for raw in args.csv:
        csv_paths.append(Path(raw).expanduser())

    if args.csv_dir:
        csv_dir = Path(args.csv_dir).expanduser()
        if not csv_dir.exists() or not csv_dir.is_dir():
            raise SystemExit(f"CSV dir not found: {csv_dir}")
        candidates = sorted(csv_dir.glob("*.csv"), key=lambda p: p.stat().st_mtime)
        if not candidates:
            raise SystemExit(f"No .csv files found in: {csv_dir}")
        csv_paths.extend(candidates)

    if not csv_paths:
        default_dir = settings.get_schwab_downloads_dir()
        raise SystemExit(
            "No CSV provided. Download a Schwab Positions CSV manually, then run one of:\n\n"
            f"  python -m financial_agent.schwab_refresh --csv /path/to/positions.csv\n"
            f"  python -m financial_agent.schwab_refresh --csv-dir {default_dir}\n"
        )

    snapshot_hint = args.csv_dir or str(csv_paths[0])
    conn = schwab_db.connect(db_path)
    try:
        snapshot_id = schwab_db.insert_snapshot(
            conn,
            container_id=container_id,
            as_of=as_of,
            csv_path=Path(snapshot_hint).expanduser(),
        )
        conn.commit()
    finally:
        conn.close()

    total_rows = 0
    for csv_path in csv_paths:
        if not csv_path.exists():
            raise SystemExit(f"CSV not found: {csv_path}")
        imported = import_positions_csv(db_path=db_path, csv_path=csv_path, as_of=as_of, snapshot_id=snapshot_id)
        total_rows += imported.rows_imported
        print(f"Imported {imported.rows_imported} rows from {csv_path} into {db_path}")

    print(
        f"Schwab refresh complete: container_id={container_id}, snapshot_id={snapshot_id}, files={len(csv_paths)}, rows={total_rows}"
    )


if __name__ == "__main__":
    main()
