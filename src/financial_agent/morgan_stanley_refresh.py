from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import re

from . import settings
from .morgan_stanley_csv import db as ms_db
from .morgan_stanley_csv.importer import import_positions_csv


_WS_RE = re.compile(r"\s+")


def _infer_account_name_from_path(path: Path) -> str | None:
    stem = (path.stem or "").strip()
    if not stem:
        return None
    # Friendly normalization: underscores/dashes -> spaces; collapse whitespace.
    stem = stem.replace("_", " ").replace("-", " ")
    stem = _WS_RE.sub(" ", stem).strip()
    return stem or None


def _iter_import_files(paths: list[str], directory: str | None) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        out.append(Path(p).expanduser())
    if directory:
        d = Path(directory).expanduser()
        out.extend(sorted(d.glob("*.csv")))
        out.extend(sorted(d.glob("*.xlsx")))
    # De-dupe while preserving order.
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in out:
        rp = p.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        unique.append(p)
    return unique


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Morgan Stanley CSV position exports into the local DB")
    parser.add_argument(
        "--container-id",
        default="morgan_stanley",
        help="Logical login/container id (e.g., 'kev', 'deb', 'joint').",
    )
    parser.add_argument(
        "--csv",
        action="append",
        default=[],
        help="Path to a Morgan Stanley export (.csv or .xlsx) (repeatable)",
    )
    parser.add_argument(
        "--csv-dir",
        default=None,
        help="Directory containing Morgan Stanley exports (*.csv, *.xlsx)",
    )
    parser.add_argument(
        "--account-name",
        default=None,
        help="Optional account label to apply when the export doesn't include an account identifier (e.g. 'alternatives').",
    )

    args = parser.parse_args()

    csv_dir = args.csv_dir
    if csv_dir:
        d = Path(csv_dir).expanduser()
        # Common layout: downloads/morgan_stanley/<container_id>/*.xlsx
        maybe_container_dir = d / args.container_id
        if maybe_container_dir.exists() and maybe_container_dir.is_dir():
            csv_dir = str(maybe_container_dir)

    files = _iter_import_files(args.csv, csv_dir)
    if not files:
        raise SystemExit("No files provided. Use --csv or --csv-dir")

    db_path = settings.get_finagent_db_path()
    as_of = datetime.now(timezone.utc)
    container_id = (args.container_id or "").strip() or "morgan_stanley"
    account_name = (args.account_name or "").strip() or None

    # One snapshot per refresh run so multiple account CSVs appear together.
    try:
        # IMPORTANT: create the snapshot in its own transaction and commit before importing.
        # The importer opens its own SQLite connection; leaving this uncommitted will lock the DB.
        conn = ms_db.connect(db_path)
        snapshot_id = ms_db.insert_snapshot(conn, container_id=container_id, as_of=as_of, csv_path=files[0])
        conn.commit()
        conn.close()

        total_rows = 0
        for p in files:
            inferred = _infer_account_name_from_path(p) if account_name is None else None
            res = import_positions_csv(
                db_path=db_path,
                csv_path=p,
                as_of=as_of,
                snapshot_id=snapshot_id,
                container_id=container_id,
                account_name_override=account_name or inferred,
            )
            total_rows += res.rows_imported
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower():
            raise SystemExit(
                "SQLite database is locked. If the API server is running, stop it, run the import, then restart the server."
            )
        raise

    print(
        f"Imported {len(files)} file(s) into snapshot {snapshot_id} (container_id={container_id!r}); rows={total_rows}"
    )


if __name__ == "__main__":
    main()
