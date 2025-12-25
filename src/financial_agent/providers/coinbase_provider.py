from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi.concurrency import run_in_threadpool

from ..coinbase_client import CoinbaseClient
from .. import settings
from .protocols import AccountRef, ContainerRef, Holding, HoldingsProvider


class CoinbaseHoldingsProvider(HoldingsProvider):
    source = "coinbase"

    def __init__(self, *, client: CoinbaseClient, container_id: str = "coinbase") -> None:
        self._client = client
        self._container_id = container_id

    async def list_containers(self) -> list[ContainerRef]:
        return [ContainerRef(source=self.source, container_id=self._container_id, name="Coinbase")]

    async def list_accounts(self, *, container_id: str) -> list[AccountRef]:
        if container_id != self._container_id:
            return []

        accounts = await run_in_threadpool(self._client.list_accounts)
        ignored = settings.get_ignored_assets()

        out: list[AccountRef] = []
        for acct in accounts:
            if not isinstance(acct, dict):
                continue
            uuid = acct.get("uuid")
            if not isinstance(uuid, str) or not uuid:
                continue

            asset = acct.get("currency")
            if isinstance(asset, str) and asset.strip().upper() in ignored:
                continue

            name = acct.get("name")
            out.append(
                AccountRef(
                    source=self.source,
                    container_id=self._container_id,
                    account_id=uuid,
                    name=str(name) if name is not None else None,
                )
            )

        return out

    async def get_holdings(self, *, container_id: str) -> list[Holding]:
        if container_id != self._container_id:
            return []

        accounts: list[dict[str, Any]] = await run_in_threadpool(self._client.list_accounts)
        ignored = settings.get_ignored_assets()

        holdings: list[Holding] = []
        for acct in accounts:
            currency = acct.get("currency")
            if not isinstance(currency, str) or not currency:
                continue

            asset_upper = currency.strip().upper()
            if asset_upper in ignored:
                continue

            uuid = acct.get("uuid")
            if not isinstance(uuid, str) or not uuid:
                continue

            available_balance = (acct.get("available_balance") or {}).get("value")
            hold_balance = (acct.get("hold") or {}).get("value")

            qty = _parse_decimal(available_balance) + _parse_decimal(hold_balance)
            if qty <= 0:
                continue

            holdings.append(
                Holding(
                    source=self.source,
                    container_id=self._container_id,
                    account_id=uuid,
                    asset=asset_upper,
                    quantity=qty,
                    quote_currency="USD",
                )
            )

        return holdings


def _parse_decimal(value: str | None) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")
