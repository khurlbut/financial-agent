from __future__ import annotations

from decimal import Decimal

from fastapi.concurrency import run_in_threadpool

from .coinbase_client import CoinbaseClient
from .providers.protocols import PricingProvider


class CoinbasePricingProvider(PricingProvider):
    provider_id = "coinbase"

    def __init__(self, *, client: CoinbaseClient) -> None:
        self._client = client

    async def get_prices(self, *, assets: set[str], quote_currency: str = "USD") -> dict[str, Decimal]:
        qc = (quote_currency or "USD").strip().upper()

        # Handle price overrides (e.g., ETH2 -> ETH) while preserving the original
        # asset keys so the valuation layer can stay consistent.
        normalized_map: dict[str, str] = {}
        for a in assets:
            base = (a or "").strip().upper()
            if not base:
                continue
            normalized_map[base] = CoinbaseClient._price_symbol_for_asset(base)

        normalized_assets = set(normalized_map.values())

        normalized_prices: dict[str, Decimal] = {}
        for norm in normalized_assets:
            if norm in ("USD", "USDC"):
                normalized_prices[norm] = Decimal("1")
                continue

            price = await run_in_threadpool(
                self._client.get_spot_price,
                symbol_or_product_id=norm,
                quote_currency=qc,
            )
            if price is None:
                continue
            normalized_prices[norm] = Decimal(str(price))

        out: dict[str, Decimal] = {}
        for original, norm in normalized_map.items():
            p = normalized_prices.get(norm)
            if p is not None:
                out[original] = p

        return out
