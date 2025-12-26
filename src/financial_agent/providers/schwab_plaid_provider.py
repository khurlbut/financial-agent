from __future__ import annotations

from decimal import Decimal

from fastapi.concurrency import run_in_threadpool

from .. import settings
from ..plaid_client import get_investments_holdings
from ..plaid_store import load_plaid_items
from .protocols import AccountRef, ContainerRef, Holding, HoldingsProvider


class SchwabPlaidHoldingsProvider(HoldingsProvider):
    """Read-only Schwab holdings via Plaid Investments."""

    source = "schwab"

    def __init__(self, *, container_id: str | None = None) -> None:
        self._container_id = container_id or settings.get_schwab_container_id()

    async def list_containers(self) -> list[ContainerRef]:
        items = load_plaid_items()
        item = items.get(self._container_id)
        if item is None:
            return []
        name = item.institution_name or "Schwab"
        return [ContainerRef(source=self.source, container_id=self._container_id, name=name)]

    async def list_accounts(self, *, container_id: str) -> list[AccountRef]:
        if container_id != self._container_id:
            return []

        item = load_plaid_items().get(self._container_id)
        if item is None:
            return []

        data = await run_in_threadpool(get_investments_holdings, access_token=item.access_token)
        accounts = data.get("accounts") or []

        out: list[AccountRef] = []
        for a in accounts:
            if not isinstance(a, dict):
                continue
            account_id = a.get("account_id")
            if not isinstance(account_id, str) or not account_id:
                continue
            name = a.get("name")
            out.append(
                AccountRef(
                    source=self.source,
                    container_id=self._container_id,
                    account_id=account_id,
                    name=str(name) if name is not None else None,
                )
            )

        return out

    async def get_holdings(self, *, container_id: str) -> list[Holding]:
        if container_id != self._container_id:
            return []

        ignored = settings.get_ignored_assets()

        item = load_plaid_items().get(self._container_id)
        if item is None:
            return []

        data = await run_in_threadpool(get_investments_holdings, access_token=item.access_token)

        holdings = data.get("holdings") or []
        securities = data.get("securities") or []

        sec_by_id: dict[str, dict] = {}
        for s in securities:
            if isinstance(s, dict) and isinstance(s.get("security_id"), str):
                sec_by_id[s["security_id"]] = s

        out: list[Holding] = []
        for h in holdings:
            if not isinstance(h, dict):
                continue

            account_id = h.get("account_id")
            security_id = h.get("security_id")
            if not isinstance(account_id, str) or not account_id:
                continue
            if not isinstance(security_id, str) or not security_id:
                continue

            sec = sec_by_id.get(security_id, {})
            ticker = sec.get("ticker_symbol")

            # Prefer ticker symbol; fallback to security_id (still valued via institution_value).
            asset = (ticker if isinstance(ticker, str) and ticker else security_id).strip().upper()
            if not asset or asset in ignored:
                continue

            qty = _parse_decimal(h.get("quantity"))
            if qty <= 0:
                continue

            price = _maybe_decimal(h.get("institution_price"))
            mv = _maybe_decimal(h.get("institution_value"))

            if mv is None and price is not None:
                mv = qty * price

            # Normalize cash to USD when possible.
            sec_type = (sec.get("type") or "").strip().lower() if isinstance(sec.get("type"), str) else ""
            is_cash = sec_type == "cash" or bool(sec.get("is_cash_equivalent"))
            if is_cash and asset not in ("USD", "USDC"):
                # If Plaid calls it cash-equivalent but no ticker, keep as-is.
                pass

            out.append(
                Holding(
                    source=self.source,
                    container_id=self._container_id,
                    account_id=account_id,
                    asset=asset,
                    quantity=qty,
                    quote_currency="USD",
                    price=price,
                    market_value=mv,
                )
            )

        return out


def _parse_decimal(value) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _maybe_decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None
