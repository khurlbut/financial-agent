from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from financial_agent.morgan_stanley_csv import db as ms_db
from financial_agent.morgan_stanley_csv.importer import import_positions_csv


def test_morgan_stanley_xlsx_import(tmp_path: Path) -> None:
    # Create a minimal Morgan Stanley-like .xlsx export.
    try:
        from openpyxl import Workbook
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("openpyxl must be installed to run this test") from exc

    xlsx_path = tmp_path / "ms_positions.xlsx"
    wb = Workbook()
    ws = wb.active

    ws.append(["Account Name", "Symbol", "Description", "Quantity", "Price", "Market Value", "Currency"])
    ws.append(["Brokerage 1", "AAPL", "Apple Inc.", "2", "150.00", "300.00", "USD"])
    ws.append(["Brokerage 1", "VTI", "Vanguard Total Stock Market ETF", "1", "240.00", "240.00", "USD"])

    wb.save(xlsx_path)
    wb.close()

    db_path = tmp_path / "test.db"
    # connect() creates schema and runs migrations.
    conn = ms_db.connect(db_path)
    conn.close()

    # Create a snapshot row first.
    conn = ms_db.connect(db_path)
    snapshot_id = ms_db.insert_snapshot(
        conn,
        container_id="kev",
        as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
        csv_path=xlsx_path,
    )
    conn.commit()
    conn.close()

    res = import_positions_csv(db_path=db_path, csv_path=xlsx_path, snapshot_id=snapshot_id)
    assert res.rows_imported == 2

    # Verify rows landed.
    conn = ms_db.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT symbol, quantity, price, market_value, currency FROM ms_csv_positions WHERE snapshot_id = ? ORDER BY symbol",
            (snapshot_id,),
        ).fetchall()
    finally:
        conn.close()

    # Note: ms_csv_positions stores numeric fields as TEXT; conversions happen downstream.
    assert [(r[0], r[1], r[2], r[3], r[4]) for r in rows] == [
        ("AAPL", "2", "150.00", "300.00", "USD"),
        ("VTI", "1", "240.00", "240.00", "USD"),
    ]
