from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class ContainerRef:
    source: str
    container_id: str
    name: str | None = None


@dataclass(frozen=True)
class AccountRef:
    source: str
    container_id: str
    account_id: str
    name: str | None = None


@dataclass(frozen=True)
class Holding:
    source: str
    container_id: str
    account_id: str | None
    asset: str
    quantity: Decimal
    quote_currency: str = "USD"


class HoldingsProvider(Protocol):
    """A container integration that can produce holdings, optionally by account."""

    source: str

    async def list_containers(self) -> list[ContainerRef]:
        ...

    async def list_accounts(self, *, container_id: str) -> list[AccountRef]:
        ...

    async def get_holdings(self, *, container_id: str) -> list[Holding]:
        ...


class PricingProvider(Protocol):
    """Swappable pricing provider (Coinbase, Binance, etc.)."""

    provider_id: str

    async def get_prices(
        self,
        *,
        assets: set[str],
        quote_currency: str = "USD",
    ) -> dict[str, Decimal]:
        ...
