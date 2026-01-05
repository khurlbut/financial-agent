from __future__ import annotations

import sqlite3
from pathlib import Path

from financial_agent.schwab_csv.importer import import_positions_csv


def test_import_positions_csv_handles_preamble_and_cash_row(tmp_path: Path) -> None:
    csv_path = tmp_path / "schwab.csv"
    db_path = tmp_path / "db.sqlite3"

    csv_path.write_text(
        "\n".join(
            [
                "Positions for account My ROTH IRA ...319 as of 07:03 PM ET, 2026/01/04",
                "",
                '"Symbol","Description","Qty (Quantity)","Price","Mkt Val (Market Value)","Security Type",',
                '"AAPL","APPLE INC","3","$1.00","$3.00","Equity",',
                '"Cash & Cash Investments","--","--","--","$490.20","Cash and Money Market",',
                '"Account Total","","--","--","$493.20","--",',
                "",
            ]
        ),
        encoding="utf-8",
    )

    imported = import_positions_csv(db_path=db_path, csv_path=csv_path)
    assert imported.rows_imported == 2

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT symbol, quantity, price, market_value FROM schwab_csv_positions ORDER BY symbol")
        got = rows.fetchall()
    finally:
        conn.close()

    assert got[0][0] == "AAPL"
    assert got[1][0] == "USD"
    assert got[1][1] == "490.20"
    assert got[1][2] == "1"
    assert got[1][3] == "490.20"
