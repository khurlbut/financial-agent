from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from . import settings
from .models import (
    AccountValuation,
    AssetAccountBreakdown,
    AssetValuation,
    CashBalance,
    ContainerHoldings,
    ContainerSummary,
    HoldingLine,
    NetWorthSummary,
    PortfolioValuation,
    Position,
)
from .providers.protocols import AccountRef, ContainerRef, Holding, HoldingsProvider, PricingProvider


@dataclass(frozen=True)
class PortfolioComputed:
    as_of: datetime
    currency: str
    portfolio: PortfolioValuation
    container_totals: list[ContainerSummary]


class PortfolioService:
    def __init__(
        self,
        *,
        providers: list[HoldingsProvider],
        pricer: PricingProvider,
    ) -> None:
        self._providers = providers
        self._pricer = pricer

    @property
    def pricing_provider_id(self) -> str:
        return self._pricer.provider_id

    async def list_containers(self) -> list[ContainerRef]:
        out: list[ContainerRef] = []
        for p in self._providers:
            out.extend(await p.list_containers())
        return out

    async def list_accounts(self, *, source: str, container_id: str) -> list[AccountRef]:
        provider = self._get_provider(source)
        return await provider.list_accounts(container_id=container_id)

    async def compute_portfolio(self) -> PortfolioComputed:
        ignored = settings.get_ignored_assets()
        as_of = datetime.now(timezone.utc)

        all_holdings: list[Holding] = []
        containers: list[ContainerRef] = []
        account_names: dict[tuple[str, str, str], str | None] = {}

        for provider in self._providers:
            provider_containers = await provider.list_containers()
            containers.extend(provider_containers)
            for container in provider_containers:
                # Best-effort account discovery for name annotation.
                try:
                    for a in await provider.list_accounts(container_id=container.container_id):
                        account_names[(a.source, a.container_id, a.account_id)] = a.name
                except Exception:
                    pass

                all_holdings.extend(await provider.get_holdings(container_id=container.container_id))

        # Normalize/clean holdings.
        cleaned: list[Holding] = []
        for h in all_holdings:
            asset = (h.asset or "").strip().upper()
            if not asset or asset in ignored:
                continue
            if h.quantity <= 0:
                continue
            cleaned.append(
                Holding(
                    source=h.source,
                    container_id=h.container_id,
                    account_id=h.account_id,
                    asset=asset,
                    quantity=h.quantity,
                    quote_currency=(h.quote_currency or "USD").strip().upper(),
                    price=h.price,
                    market_value=h.market_value,
                )
            )

        # Prices for non-cash assets where we *don't* already have an institution-provided value.
        price_assets = {
            h.asset
            for h in cleaned
            if h.asset not in ("USD", "USDC") and h.market_value is None and h.price is None
        }
        prices = await self._pricer.get_prices(assets=price_assets, quote_currency="USD")

        cash: list[CashBalance] = []
        positions: list[Position] = []

        for h in cleaned:
            if h.asset in ("USD", "USDC"):
                cash.append(
                    CashBalance(
                        source=h.source,  # type: ignore[arg-type]
                        container_id=h.container_id,
                        account_id=h.account_id,
                        currency=h.asset,
                        available=None,
                        total=str(h.quantity),
                    )
                )
                continue

            price = h.price if h.price is not None else prices.get(h.asset)
            mv: Decimal | None = h.market_value
            if mv is None and price is not None:
                mv = h.quantity * price

            positions.append(
                Position(
                    source=h.source,  # type: ignore[arg-type]
                    container_id=h.container_id,
                    account_id=h.account_id,
                    symbol=h.asset,
                    asset=h.asset,
                    quantity=str(h.quantity),
                    cost_basis=None,
                    current_price=None if price is None else str(price),
                    market_value=None if mv is None else str(mv),
                    quote_currency="USD",
                )
            )

        cash_total = sum((Decimal(c.total or "0") for c in cash), start=Decimal("0"))
        positions_total = sum(
            (Decimal(p.market_value) for p in positions if p.market_value is not None),
            start=Decimal("0"),
        )
        total = cash_total + positions_total

        missing_prices = sorted({p.asset for p in positions if p.asset and p.market_value is None})

        by_asset_map: dict[str, dict] = {}
        for p in positions:
            if not p.asset:
                continue
            entry = by_asset_map.setdefault(
                p.asset,
                {
                    "asset": p.asset,
                    "quote_currency": p.quote_currency or "USD",
                    "total_quantity": Decimal("0"),
                    "price": p.current_price,
                    "market_value": Decimal("0"),
                    "has_price": False,
                    "accounts": [],
                },
            )

            qty = Decimal(p.quantity or "0")
            entry["total_quantity"] += qty

            mv = Decimal(p.market_value) if p.market_value is not None else None
            if mv is not None:
                entry["market_value"] += mv
                entry["has_price"] = True
                if entry.get("price") is None:
                    entry["price"] = p.current_price

            entry["accounts"].append(
                AssetAccountBreakdown(
                    source=p.source,
                    account_id=p.account_id,
                    container_id=p.container_id,
                    quantity=str(qty),
                    market_value=None if mv is None else str(mv),
                )
            )

        by_asset: list[AssetValuation] = []
        for asset, entry in sorted(by_asset_map.items(), key=lambda kv: kv[0]):
            by_asset.append(
                AssetValuation(
                    asset=asset,
                    quote_currency=entry["quote_currency"],
                    total_quantity=str(entry["total_quantity"]),
                    price=entry.get("price"),
                    market_value=str(entry["market_value"]) if entry.get("has_price") else None,
                    accounts=entry["accounts"],
                )
            )

        # Account-level rollup (sub-accounts within a container).
        by_account_map: dict[tuple[str, str, str | None], dict] = {}

        def _acct_key(source: str, container_id: str, account_id: str | None) -> tuple[str, str, str | None]:
            return (source, container_id, account_id)

        for c in cash:
            key = _acct_key(c.source, c.container_id or "", c.account_id)
            by_account_map.setdefault(
                key,
                {
                    "source": c.source,
                    "container_id": c.container_id,
                    "account_id": c.account_id,
                    "name": None,
                    "currency": "USD",
                    "cash": [],
                    "positions": [],
                },
            )["cash"].append(c)

        for p in positions:
            key = _acct_key(p.source, p.container_id or "", p.account_id)
            by_account_map.setdefault(
                key,
                {
                    "source": p.source,
                    "container_id": p.container_id,
                    "account_id": p.account_id,
                    "name": None,
                    "currency": "USD",
                    "cash": [],
                    "positions": [],
                },
            )["positions"].append(p)

        by_account: list[AccountValuation] = []
        for (_src, _container, _acct), entry in sorted(by_account_map.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or "")):
            if entry.get("name") is None and entry.get("account_id") is not None:
                maybe_name = account_names.get((entry["source"], entry["container_id"], entry["account_id"]))
                if maybe_name is not None:
                    entry["name"] = maybe_name

            total_value = Decimal("0")
            for c in entry["cash"]:
                total_value += Decimal(c.total or "0")
            for p in entry["positions"]:
                if p.market_value is not None:
                    total_value += Decimal(p.market_value)

            by_account.append(
                AccountValuation(
                    source=entry["source"],
                    container_id=entry.get("container_id"),
                    account_id=entry["account_id"],
                    name=entry.get("name"),
                    currency=entry.get("currency") or "USD",
                    total_value=str(total_value),
                    cash=entry["cash"],
                    positions=entry["positions"],
                )
            )

        # Container totals rollup.
        container_totals_map: dict[tuple[str, str], Decimal] = {}
        container_names: dict[tuple[str, str], str | None] = {(c.source, c.container_id): c.name for c in containers}

        for a in by_account:
            if not a.container_id:
                continue
            key = (a.source, a.container_id)
            container_totals_map[key] = container_totals_map.get(key, Decimal("0")) + Decimal(a.total_value)

        # Include containers even if they currently have no holdings.
        for c in containers:
            key = (c.source, c.container_id)
            container_totals_map.setdefault(key, Decimal("0"))

        container_totals: list[ContainerSummary] = []
        for (src, cid), total_value in sorted(container_totals_map.items(), key=lambda kv: (kv[0][0], kv[0][1])):
            container_totals.append(
                ContainerSummary(
                    source=src,  # type: ignore[arg-type]
                    container_id=cid,
                    account_id=None,
                    name=container_names.get((src, cid)),
                    currency="USD",
                    total_value=str(total_value),
                )
            )

        portfolio = PortfolioValuation(
            source="aggregate",
            as_of=as_of,
            currency="USD",
            total_value=str(total),
            cash_value=str(cash_total),
            positions_value=str(positions_total),
            by_asset=by_asset,
            by_account=by_account,
            by_container=container_totals,
            missing_prices=missing_prices,
        )

        return PortfolioComputed(as_of=as_of, currency="USD", portfolio=portfolio, container_totals=container_totals)

    async def get_networth(self) -> NetWorthSummary:
        computed = await self.compute_portfolio()
        return NetWorthSummary(
            source="aggregate",
            as_of=computed.as_of,
            currency=computed.currency,
            total_value=computed.portfolio.total_value,
        )

    async def get_container_value(self, *, source: str, container_id: str) -> ContainerSummary:
        computed = await self.compute_portfolio()
        for c in computed.container_totals:
            if c.source == source and c.container_id == container_id:
                return c
        raise KeyError("container not found")

    async def get_container_holdings(
        self,
        *,
        source: str,
        container_id: str,
        account_id: str | None = None,
    ) -> ContainerHoldings:
        computed = await self.compute_portfolio()

        # Pick container total.
        container_total: ContainerSummary | None = None
        for c in computed.container_totals:
            if c.source == source and c.container_id == container_id:
                container_total = c
                break
        if container_total is None:
            raise KeyError("container not found")

        # Build holdings from underlying account valuations, optionally filtered.
        holdings: list[HoldingLine] = []
        missing_prices: set[str] = set()
        total_value = Decimal("0")

        for a in computed.portfolio.by_account:
            if a.source != source:
                continue
            if getattr(a, "container_id", None) != container_id:
                continue
            if account_id is not None and a.account_id != account_id:
                continue

            total_value += Decimal(a.total_value)

            for c in a.cash:
                qty = Decimal(c.total or "0")
                if qty <= 0:
                    continue
                holdings.append(
                    HoldingLine(
                        asset=c.currency,
                        quantity=str(qty),
                        quote_currency="USD",
                        price="1",
                        market_value=str(qty),
                        account_id=a.account_id,
                    )
                )

            for p in a.positions:
                if not p.asset:
                    continue
                if p.market_value is None:
                    missing_prices.add(p.asset)
                holdings.append(
                    HoldingLine(
                        asset=p.asset,
                        quantity=p.quantity,
                        quote_currency=p.quote_currency or "USD",
                        price=p.current_price,
                        market_value=p.market_value,
                        account_id=a.account_id,
                    )
                )

        name = container_total.name
        return ContainerHoldings(
            source=source,  # type: ignore[arg-type]
            as_of=computed.as_of,
            container_id=container_id,
            account_id=account_id,
            name=name,
            currency="USD",
            total_value=str(total_value if account_id is not None else Decimal(container_total.total_value)),
            holdings=holdings,
            missing_prices=sorted(missing_prices),
        )

    def _get_provider(self, source: str) -> HoldingsProvider:
        for p in self._providers:
            if getattr(p, "source", None) == source:
                return p
        raise KeyError(f"unknown source: {source}")
