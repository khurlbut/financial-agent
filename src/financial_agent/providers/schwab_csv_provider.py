from __future__ import annotations

import sqlite3
from decimal import Decimal

from .. import settings
from ..schwab_csv import db as schwab_db
from .protocols import AccountRef, ContainerRef, Holding, HoldingsProvider


class SchwabCsvHoldingsProvider(HoldingsProvider):
    """Holdings provider backed by imported Schwab positions CSV snapshots."""

    source = "schwab_csv"

    def __init__(self, *, container_id: str = "schwab") -> None:
        self._container_id = container_id

    async def list_containers(self) -> list[ContainerRef]:
        # Always advertise the container so clients see it's available,
        # even if no snapshot has been imported yet.
        return [ContainerRef(source=self.source, container_id=self._container_id, name="Schwab (CSV)")]

    async def list_accounts(self, *, container_id: str) -> list[AccountRef]:
        if container_id != self._container_id:
            return []

        conn = schwab_db.connect(settings.get_finagent_db_path())
        try:
            snap = schwab_db.get_latest_snapshot(conn)
            if snap is None:
                return []

            rows = conn.execute(
                """
                SELECT DISTINCT account_name
                FROM schwab_csv_positions
                WHERE snapshot_id = ? AND account_name IS NOT NULL AND TRIM(account_name) != ''
                ORDER BY account_name
                """,
                (snap.id,),
            ).fetchall()

            out: list[AccountRef] = []
            for r in rows:
                name = str(r[0])
                out.append(
                    AccountRef(
                        source=self.source,
                        container_id=self._container_id,
                        account_id=name,
                        name=name,
                    )
                )
            return out
        finally:
            conn.close()

    async def get_holdings(self, *, container_id: str) -> list[Holding]:
        if container_id != self._container_id:
            return []

        conn = schwab_db.connect(settings.get_finagent_db_path())
        try:
            snap = schwab_db.get_latest_snapshot(conn)
            if snap is None:
                return []

            rows = conn.execute(
                """
                SELECT account_name, symbol, quantity, price, market_value, currency
                FROM schwab_csv_positions
                WHERE snapshot_id = ?
                """,
                (snap.id,),
            ).fetchall()

            holdings: list[Holding] = []
            for r in rows:
                account_name = r[0]
                symbol = r[1]
                quantity_s = r[2]
                price_s = r[3]
                mv_s = r[4]
                currency = (r[5] or "USD")

                asset = (str(symbol).strip().upper() if symbol is not None else "")
                if not asset:
                    continue

                qty = _parse_decimal(quantity_s)
                if qty <= 0:
                    continue

                price = _parse_decimal(price_s) if price_s is not None else None
                mv = _parse_decimal(mv_s) if mv_s is not None else None

                holdings.append(
                    Holding(
                        source=self.source,
                        container_id=self._container_id,
                        account_id=str(account_name) if account_name is not None and str(account_name).strip() else None,
                        asset=asset,
                        quantity=qty,
                        quote_currency=str(currency).strip().upper() or "USD",
                        price=price,
                        market_value=mv,
                    )
                )

            return holdings
        finally:
            conn.close()


def _parse_decimal(value: str | None) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")
