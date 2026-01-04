from __future__ import annotations

import csv
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from . import db


@dataclass(frozen=True)
class ImportedSnapshot:
    snapshot_id: int
    as_of: datetime
    rows_imported: int


def import_positions_csv(*, db_path: Path, csv_path: Path, as_of: datetime | None = None) -> ImportedSnapshot:
    """Import a Schwab positions CSV into SQLite.

    This is intentionally best-effort: Schwab can change headers/format.
    We store parsed numeric fields when we can, and also store the raw row JSON.
    """

    if as_of is None:
        as_of = datetime.now(timezone.utc)

    conn = db.connect(db_path)
    try:
        snapshot_id = db.insert_snapshot(conn, as_of=as_of, csv_path=csv_path)

        rows_imported = 0
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                raise ValueError("CSV has no header row")

            for row in reader:
                norm = _normalize_row(row)
                conn.execute(
                    """
                    INSERT INTO schwab_csv_positions (
                      snapshot_id, account_name, symbol, description, quantity, price, market_value, currency, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        norm.get("account_name"),
                        norm.get("symbol"),
                        norm.get("description"),
                        norm.get("quantity"),
                        norm.get("price"),
                        norm.get("market_value"),
                        norm.get("currency"),
                        json.dumps(row),
                    ),
                )
                rows_imported += 1

        conn.commit()
        return ImportedSnapshot(snapshot_id=snapshot_id, as_of=as_of, rows_imported=rows_imported)
    finally:
        conn.close()


def _normalize_key(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def _get(row: dict[str, Any], *keys: str) -> str | None:
    by_norm = {_normalize_key(k): v for k, v in row.items()}
    for k in keys:
        v = by_norm.get(_normalize_key(k))
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return None


_MONEY_RE = re.compile(r"[^0-9\-\.]")


def _parse_money(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _MONEY_RE.sub("", value)
    if cleaned in {"", "-", "."}:
        return None
    try:
        # Normalize to a Decimal string.
        d = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
    return str(d)


def _parse_qty(value: str | None) -> str | None:
    return _parse_money(value)


def _normalize_row(row: dict[str, Any]) -> dict[str, str | None]:
    account = _get(row, "Account", "Account Name", "Account Number")
    symbol = _get(row, "Symbol", "Ticker", "Security Symbol")
    description = _get(row, "Description", "Security Description", "Name")

    quantity = _parse_qty(_get(row, "Quantity", "Qty", "Shares"))
    price = _parse_money(_get(row, "Price", "Last Price", "Market Price"))
    market_value = _parse_money(_get(row, "Market Value", "Value", "Current Value"))

    currency = _get(row, "Currency")
    if currency is not None:
        currency = currency.strip().upper()

    # Some exports represent cash with an empty symbol but a description containing "Cash".
    if not symbol and description and "cash" in description.lower():
        symbol = "USD"

    return {
        "account_name": account,
        "symbol": symbol.strip().upper() if symbol else None,
        "description": description,
        "quantity": quantity,
        "price": price,
        "market_value": market_value,
        "currency": currency or "USD",
    }
