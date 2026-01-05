from __future__ import annotations

import asyncio
import csv
import io
from datetime import datetime, timezone
import urllib.parse
import urllib.request
from decimal import Decimal

from fastapi.concurrency import run_in_threadpool

from .coinbase_client import CoinbaseClient
from .cold_storage import load_cold_storage_devices
from . import settings
from .providers.protocols import PricingProvider


class CoinbasePricingProvider(PricingProvider):
    provider_id = "coinbase"

    def __init__(self, *, client: CoinbaseClient) -> None:
        self._client = client
        self._allowed_assets_cache: set[str] | None = None
        self._allowed_assets_cache_as_of: datetime | None = None
        self._allowed_assets_lock = asyncio.Lock()

        # Symbols that we have learned are not supported by Coinbase market-data.
        # Cached to avoid repeated 404 spam and Coinbase-side throttling.
        self._unsupported_cache: dict[str, datetime] = {}
        self._unsupported_cache_ttl_seconds = 24 * 60 * 60

    def _is_unsupported(self, symbol: str) -> bool:
        now = datetime.now(timezone.utc)
        ts = self._unsupported_cache.get(symbol)
        if ts is None:
            return False
        if (now - ts).total_seconds() >= self._unsupported_cache_ttl_seconds:
            # Expired; allow a retry.
            try:
                del self._unsupported_cache[symbol]
            except Exception:
                pass
            return False
        return True

    def _mark_unsupported(self, symbol: str) -> None:
        self._unsupported_cache[symbol] = datetime.now(timezone.utc)

    async def _get_allowed_assets(self) -> set[str]:
        """Best-effort allowlist of assets suitable for Coinbase pricing.

        Coinbase market-data product IDs are not available for most equity/ETF
        tickers. Requesting them causes 404 spam and can trigger Coinbase-side
        throttling (403 "Too many errors"), which then breaks real crypto pricing.

        We therefore restrict Coinbase pricing to assets we plausibly hold in
        Coinbase or cold storage.
        """

        cache_ttl_seconds = 10 * 60
        now = datetime.now(timezone.utc)

        if self._allowed_assets_cache is not None and self._allowed_assets_cache_as_of is not None:
            age = (now - self._allowed_assets_cache_as_of).total_seconds()
            if age < cache_ttl_seconds:
                return self._allowed_assets_cache

        async with self._allowed_assets_lock:
            # Double-check after acquiring lock.
            if self._allowed_assets_cache is not None and self._allowed_assets_cache_as_of is not None:
                age = (now - self._allowed_assets_cache_as_of).total_seconds()
                if age < cache_ttl_seconds:
                    return self._allowed_assets_cache

            allowed: set[str] = set()
            ignored = settings.get_ignored_assets()

            # Coinbase-held assets.
            try:
                accounts = await run_in_threadpool(self._client.list_accounts)
                for acct in accounts:
                    if not isinstance(acct, dict):
                        continue
                    cur = acct.get("currency")
                    if not isinstance(cur, str) or not cur:
                        continue
                    sym = cur.strip().upper()
                    if not sym or sym in ignored or sym in ("USD", "USDC"):
                        continue
                    allowed.add(CoinbaseClient._price_symbol_for_asset(sym))
            except Exception:
                # If this fails, fall back to cold storage assets only.
                pass

            # Cold storage assets.
            try:
                devices = await run_in_threadpool(load_cold_storage_devices, settings.get_cold_storage_path())
                for d in devices:
                    for asset in (d.holdings or {}).keys():
                        sym = (asset or "").strip().upper()
                        if not sym or sym in ignored or sym in ("USD", "USDC"):
                            continue
                        allowed.add(CoinbaseClient._price_symbol_for_asset(sym))
            except Exception:
                pass

            # Always allow common USD pegs.
            allowed.update({"USD", "USDC"})

            self._allowed_assets_cache = allowed
            self._allowed_assets_cache_as_of = now
            return allowed

    async def get_prices(self, *, assets: set[str], quote_currency: str = "USD") -> dict[str, Decimal]:
        qc = (quote_currency or "USD").strip().upper()

        allowed_assets = await self._get_allowed_assets()

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
            if norm not in allowed_assets:
                continue
            if self._is_unsupported(norm):
                continue
            if norm in ("USD", "USDC"):
                normalized_prices[norm] = Decimal("1")
                continue

            try:
                price = await run_in_threadpool(
                    self._client.get_spot_price,
                    symbol_or_product_id=norm,
                    quote_currency=qc,
                )
            except Exception:
                # Coinbase may not support the product (e.g., equity tickers).
                # Treat as missing price instead of failing the whole request.
                self._mark_unsupported(norm)
                price = None
            if price is None:
                continue
            normalized_prices[norm] = Decimal(str(price))

        out: dict[str, Decimal] = {}
        for original, norm in normalized_map.items():
            p = normalized_prices.get(norm)
            if p is not None:
                out[original] = p

        return out


class StooqPricingProvider(PricingProvider):
    """Pricing provider for US-listed tickers via stooq.com.

    Notes:
    - Stooq data may be delayed and/or EOD depending on symbol.
    - This is intended as a simple, no-API-key fallback for equities/ETFs.
    """

    provider_id = "stooq"

    async def get_prices(self, *, assets: set[str], quote_currency: str = "USD") -> dict[str, Decimal]:
        # Stooq is USD-centric for US listings; we currently only support USD output.
        qc = (quote_currency or "USD").strip().upper()
        if qc != "USD":
            return {}

        symbols: list[str] = []
        for asset in assets:
            sym = (asset or "").strip().upper()
            if not sym or sym in ("USD", "USDC"):
                continue
            symbols.append(sym)

        if not symbols:
            return {}

        # Fetch in parallel to keep /agent/containers responsive for portfolios
        # with many equities.
        sem = asyncio.Semaphore(12)

        async def _one(sym: str) -> tuple[str, Decimal | None]:
            async with sem:
                price = await run_in_threadpool(_fetch_stooq_last_close_usd, sym)
                return (sym, price)

        results = await asyncio.gather(*[_one(s) for s in symbols])
        out: dict[str, Decimal] = {}
        for sym, price in results:
            if price is not None:
                out[sym] = price
        return out


class CompositePricingProvider(PricingProvider):
    """Query multiple pricing providers and merge results.

    First provider that returns a price wins per-asset.
    """

    provider_id = "composite"

    def __init__(self, *, providers: list[PricingProvider]) -> None:
        self._providers = providers

    async def get_prices(self, *, assets: set[str], quote_currency: str = "USD") -> dict[str, Decimal]:
        remaining = {a for a in assets if (a or "").strip()}
        out: dict[str, Decimal] = {}

        for p in self._providers:
            if not remaining:
                break
            got = await p.get_prices(assets=remaining, quote_currency=quote_currency)
            for k, v in got.items():
                if k not in out:
                    out[k] = v
            remaining = remaining - set(out.keys())

        return out


def _fetch_stooq_last_close_usd(symbol: str) -> Decimal | None:
    """Fetch the last close for a US ticker from stooq.com.

    Tries both the raw symbol and the ".us" suffixed variant.
    """

    sym = (symbol or "").strip().lower()
    if not sym:
        return None

    candidates = [sym]
    if not sym.endswith(".us"):
        candidates.append(f"{sym}.us")

    for candidate in candidates:
        url = "https://stooq.com/q/l/?" + urllib.parse.urlencode(
            {
                "s": candidate,
                "f": "sd2t2ohlcv",
                "h": "",
                "e": "csv",
            }
        )
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:  # nosec - controlled domain
                raw = resp.read().decode("utf-8", errors="replace")
        except Exception:
            continue

        reader = csv.DictReader(io.StringIO(raw))
        row = next(reader, None)
        if not row:
            continue

        close_s = (row.get("Close") or "").strip()
        if not close_s or close_s.upper() == "N/D":
            continue

        try:
            return Decimal(close_s)
        except Exception:
            continue

    return None
