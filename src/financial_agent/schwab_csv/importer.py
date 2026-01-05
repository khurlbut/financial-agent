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
            # Schwab exports often include a preamble line (e.g. "Positions for account … as of …")
            # plus a blank line before the actual header row. We scan for the header row that starts
            # with "Symbol".
            account_from_file: str | None = None

            raw_reader = csv.reader(f)
            fieldnames: list[str] | None = None
            for row in raw_reader:
                if not row or all(not str(c).strip() for c in row):
                    continue

                if account_from_file is None and row and isinstance(row[0], str):
                    account_from_file = _extract_account_name_from_preamble(row[0])

                if str(row[0]).strip().lower() == "symbol":
                    fieldnames = [str(c).strip() for c in row]
                    while fieldnames and fieldnames[-1] == "":
                        fieldnames.pop()
                    break

            if not fieldnames:
                raise ValueError("Could not find Schwab CSV header row (expected a row starting with 'Symbol')")

            reader = csv.DictReader(f, fieldnames=fieldnames)

            for row in reader:
                if _is_empty_row(row) or _is_summary_row(row):
                    continue

                norm = _normalize_row(row, default_account_name=account_from_file)
                if norm.get("symbol") is None:
                    continue
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


def _get(row: dict[Any, Any], *keys: str) -> str | None:
    # csv.DictReader uses key=None for extra columns when a row has more fields than the header.
    # Schwab exports sometimes include a trailing empty column, so ignore None keys.
    by_norm: dict[str, Any] = {}
    for k, v in row.items():
        if k is None:
            continue
        by_norm[_normalize_key(str(k))] = v
    for k in keys:
        v = by_norm.get(_normalize_key(k))
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return None


_PREAMBLE_ACCOUNT_RE = re.compile(r"^positions\s+for\s+account\s+(?P<acct>.+?)\s+as\s+of\s+", re.I)


def _extract_account_name_from_preamble(line: str) -> str | None:
    m = _PREAMBLE_ACCOUNT_RE.search(line.strip())
    if not m:
        return None
    acct = m.group("acct").strip()
    return acct if acct else None


def _is_empty_row(row: dict[Any, Any]) -> bool:
    values = []
    for k, v in row.items():
        if k is None:
            continue
        values.append(str(v).strip() if v is not None else "")
    return not any(values)


def _is_summary_row(row: dict[Any, Any]) -> bool:
    symbol = _get(row, "Symbol", "Ticker", "Security Symbol")
    if symbol and symbol.strip().lower().startswith("account total"):
        return True
    return False


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


def _normalize_row(row: dict[Any, Any], *, default_account_name: str | None = None) -> dict[str, str | None]:
    account = _get(row, "Account", "Account Name", "Account Number") or default_account_name
    symbol = _get(row, "Symbol", "Ticker", "Security Symbol")
    description = _get(row, "Description", "Security Description", "Name")

    quantity = _parse_qty(_get(row, "Quantity", "Qty (Quantity)", "Qty", "Shares"))
    price = _parse_money(_get(row, "Price", "Last Price", "Market Price"))
    market_value = _parse_money(
        _get(row, "Market Value", "Mkt Val (Market Value)", "Mkt Val", "Value", "Current Value")
    )

    currency = _get(row, "Currency")
    if currency is not None:
        currency = currency.strip().upper()

    security_type = _get(row, "Security Type", "Type")

    # Some exports represent cash with an empty symbol but a description containing "Cash".
    if not symbol and description and "cash" in description.lower():
        symbol = "USD"

    # Schwab exports often represent cash as a row like "Cash & Cash Investments".
    # Treat this as USD with quantity == market value and price == 1.
    if symbol and "cash" in symbol.lower():
        symbol = "USD"
        description = "Cash"
        if quantity is None:
            quantity = market_value
        if price is None:
            price = "1"

    if security_type and "cash" in security_type.lower() and symbol == "USD":
        if quantity is None:
            quantity = market_value
        if price is None:
            price = "1"

    return {
        "account_name": account,
        "symbol": symbol.strip().upper() if symbol else None,
        "description": description,
        "quantity": quantity,
        "price": price,
        "market_value": market_value,
        "currency": currency or "USD",
    }
