from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schwab_csv_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
    container_id TEXT NOT NULL,
  as_of TEXT NOT NULL,
  csv_path TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schwab_csv_positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id INTEGER NOT NULL,
  account_name TEXT,
  symbol TEXT,
  description TEXT,
  quantity TEXT,
  price TEXT,
  market_value TEXT,
  currency TEXT,
  raw_json TEXT,
  FOREIGN KEY(snapshot_id) REFERENCES schwab_csv_snapshots(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_schwab_csv_positions_snapshot ON schwab_csv_positions(snapshot_id);
"""


@dataclass(frozen=True)
class SchwabSnapshot:
    id: int
    as_of: datetime
    csv_path: str


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Best-effort SQLite migrations for existing local DBs."""

    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(schwab_csv_snapshots)").fetchall()}
    except Exception:
        return

    if "container_id" not in cols:
        conn.execute("ALTER TABLE schwab_csv_snapshots ADD COLUMN container_id TEXT")

    # Ensure existing rows have a default container id.
    conn.execute(
        "UPDATE schwab_csv_snapshots SET container_id = 'schwab' WHERE container_id IS NULL OR TRIM(container_id) = ''"
    )

    # Create index once the column exists.
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_schwab_csv_snapshots_container ON schwab_csv_snapshots(container_id)")
    except Exception:
        pass

    conn.commit()


def insert_snapshot(conn: sqlite3.Connection, *, container_id: str, as_of: datetime, csv_path: Path) -> int:
    conn.execute(
        "INSERT INTO schwab_csv_snapshots (container_id, as_of, csv_path, created_at) VALUES (?, ?, ?, ?)",
        (container_id, as_of.isoformat(), str(csv_path), datetime.now(timezone.utc).isoformat()),
    )
    snapshot_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    return snapshot_id


def get_latest_snapshot(conn: sqlite3.Connection, *, container_id: str) -> SchwabSnapshot | None:
    cid = (container_id or "").strip() or "schwab"
    row = conn.execute(
        "SELECT id, as_of, csv_path FROM schwab_csv_snapshots WHERE container_id = ? ORDER BY id DESC LIMIT 1",
        (cid,),
    ).fetchone()
    if row is None:
        return None
    return SchwabSnapshot(
        id=int(row["id"]),
        as_of=datetime.fromisoformat(str(row["as_of"])),
        csv_path=str(row["csv_path"]),
    )


def list_container_ids(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT container_id FROM schwab_csv_snapshots WHERE container_id IS NOT NULL AND TRIM(container_id) != '' ORDER BY container_id"
    ).fetchall()
    return [str(r[0]) for r in rows]
