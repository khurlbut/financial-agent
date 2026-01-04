from __future__ import annotations

import argparse
from pathlib import Path

from . import settings
from .schwab_csv.importer import import_positions_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh Schwab holdings by importing one or more Schwab positions CSVs")
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

    for csv_path in csv_paths:
        if not csv_path.exists():
            raise SystemExit(f"CSV not found: {csv_path}")
        imported = import_positions_csv(db_path=db_path, csv_path=csv_path)
        print(f"Imported {imported.rows_imported} rows from {csv_path} into {db_path}")


if __name__ == "__main__":
    main()
