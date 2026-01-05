from __future__ import annotations

import re
from decimal import Decimal

from .. import settings
from ..morgan_stanley_csv import db as ms_db
from .protocols import AccountRef, ContainerRef, Holding, HoldingsProvider


class MorganStanleyCsvHoldingsProvider(HoldingsProvider):
    """Holdings provider backed by imported Morgan Stanley CSV snapshots."""

    source = "morgan_stanley_csv"

    async def list_containers(self) -> list[ContainerRef]:
        conn = ms_db.connect(settings.get_finagent_db_path())
        try:
            cids = ms_db.list_container_ids(conn)
        finally:
            conn.close()

        return [ContainerRef(source=self.source, container_id=cid, name=f"Morgan Stanley (CSV: {cid})") for cid in cids]

    async def list_accounts(self, *, container_id: str) -> list[AccountRef]:
        cid = (container_id or "").strip()
        if not cid:
            return []

        conn = ms_db.connect(settings.get_finagent_db_path())
        try:
            snap = ms_db.get_latest_snapshot(conn, container_id=cid)
            if snap is None:
                return []

            rows = conn.execute(
                """
                SELECT DISTINCT account_name
                FROM ms_csv_positions
                WHERE snapshot_id = ? AND account_name IS NOT NULL AND TRIM(account_name) != ''
                ORDER BY account_name
                """,
                (snap.id,),
            ).fetchall()

            out: list[AccountRef] = []
            for r in rows:
                name = str(r[0])
                out.append(AccountRef(source=self.source, container_id=cid, account_id=name, name=name))
            return out
        finally:
            conn.close()

    async def get_holdings(self, *, container_id: str) -> list[Holding]:
        cid = (container_id or "").strip()
        if not cid:
            return []

        price_mode = settings.get_morgan_stanley_csv_price_mode()

        conn = ms_db.connect(settings.get_finagent_db_path())
        try:
            snap = ms_db.get_latest_snapshot(conn, container_id=cid)
            if snap is None:
                return []

            rows = conn.execute(
                """
                SELECT account_name, symbol, description, quantity, price, market_value, currency
                FROM ms_csv_positions
                WHERE snapshot_id = ?
                """,
                (snap.id,),
            ).fetchall()

            holdings: list[Holding] = []
            for r in rows:
                account_name = r[0]
                symbol = r[1]
                description = r[2]
                quantity_s = r[3]
                price_s = r[4]
                mv_s = r[5]
                currency = (r[6] or "USD")

                asset = (str(symbol).strip().upper() if symbol is not None else "")
                if not asset:
                    continue

                # Morgan Stanley exports sometimes include non-public, internally-coded holdings
                # (e.g., private funds/alternatives). In live pricing mode, keep the institution
                # market value for assets that are unlikely to be priceable via Stooq/Coinbase.
                #
                # Also normalize the common bank sweep program to USD cash.
                mv_from_csv = _parse_decimal_opt(mv_s)
                desc_lower = str(description).strip().lower() if description is not None else ""
                if asset == "MSBNK" or (desc_lower and "bank deposit program" in desc_lower):
                    if mv_from_csv is not None and mv_from_csv > 0:
                        holdings.append(
                            Holding(
                                source=self.source,
                                container_id=cid,
                                account_id=str(account_name)
                                if account_name is not None and str(account_name).strip()
                                else None,
                                asset="USD",
                                quantity=mv_from_csv,
                                quote_currency="USD",
                                price=Decimal("1"),
                                market_value=mv_from_csv,
                            )
                        )
                    continue

                qty = _parse_decimal(quantity_s)
                if qty <= 0:
                    continue

                price: Decimal | None = None
                mv: Decimal | None = None

                # In "live" mode, prefer third-party pricing for public tickers.
                # For non-ticker assets, fall back to the broker-provided valuation.
                if price_mode != "live" or asset in ("USD", "USDC") or not _is_probably_public_ticker(asset):
                    price = _parse_decimal_opt(price_s)
                    mv = mv_from_csv

                holdings.append(
                    Holding(
                        source=self.source,
                        container_id=cid,
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


def _parse_decimal_opt(value: str | None) -> Decimal | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except Exception:
        return None


_PUBLIC_TICKER_RE = re.compile(r"^[A-Z]{1,6}([.-][A-Z]{1,2})?$")


def _is_probably_public_ticker(asset: str) -> bool:
    a = (asset or "").strip().upper()
    if not a or a in {"USD", "USDC"}:
        return False
    return _PUBLIC_TICKER_RE.match(a) is not None
