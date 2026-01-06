from __future__ import annotations

import asyncio
import csv
import io
import json
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

        # IMPORTANT: fetch in batches.
        # One-request-per-symbol is slow and can lead to timeouts, which then
        # surface as widespread missing_prices (even for liquid US equities).
        chunk_size = 80
        chunks = [symbols[i : i + chunk_size] for i in range(0, len(symbols), chunk_size)]

        async def _one_chunk(chunk: list[str]) -> dict[str, Decimal]:
            # Prefer .us; retry without suffix for anything missing.
            got_us = await run_in_threadpool(_fetch_stooq_last_close_usd_batch, chunk, True)
            remaining = [s for s in chunk if s not in got_us]
            if not remaining:
                return got_us
            got_raw = await run_in_threadpool(_fetch_stooq_last_close_usd_batch, remaining, False)
            out = dict(got_us)
            out.update(got_raw)
            return out

        results = await asyncio.gather(*[_one_chunk(c) for c in chunks])
        out: dict[str, Decimal] = {}
        for d in results:
            out.update(d)
        return out


class YahooPricingProvider(PricingProvider):
    """Pricing provider for equities via Yahoo Finance quote endpoint.

    Notes:
    - No API key required.
    - Intended primarily as a fallback when Stooq rate-limits.
    """

    provider_id = "yahoo"

    def __init__(self) -> None:
        # Small in-memory cache to avoid hammering Yahoo on repeated API calls.
        self._cache: dict[str, tuple[datetime, Decimal]] = {}
        self._cache_ttl_seconds = 5 * 60

    async def get_prices(self, *, assets: set[str], quote_currency: str = "USD") -> dict[str, Decimal]:
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

        now = datetime.now(timezone.utc)
        out: dict[str, Decimal] = {}
        remaining: list[str] = []

        for sym in symbols:
            hit = self._cache.get(sym)
            if hit is None:
                remaining.append(sym)
                continue
            ts, price = hit
            if (now - ts).total_seconds() >= self._cache_ttl_seconds:
                remaining.append(sym)
                continue
            out[sym] = price

        if not remaining:
            return out

        sem = asyncio.Semaphore(8)

        async def _one(sym: str) -> tuple[str, Decimal | None]:
            async with sem:
                price = await run_in_threadpool(_fetch_yahoo_chart_price_usd, sym)
                return (sym, price)

        results = await asyncio.gather(*[_one(s) for s in remaining])
        for sym, price in results:
            if price is None:
                continue
            out[sym] = price
            self._cache[sym] = (now, price)

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


def _fetch_stooq_last_close_usd_batch(symbols: list[str], prefer_us_suffix: bool) -> dict[str, Decimal]:
    """Fetch last close prices from stooq.com for multiple tickers.

    Stooq supports comma-separated symbols in the `s` parameter.
    Returns a mapping keyed by the *original* symbols provided (uppercased).
    """

    cleaned: list[str] = []
    key_by_request_sym: dict[str, str] = {}

    for s in symbols:
        base = (s or "").strip().upper()
        if not base or base in {"USD", "USDC"}:
            continue

        req = base.lower()
        if prefer_us_suffix and not req.endswith(".us"):
            req = f"{req}.us"

        cleaned.append(req)
        key_by_request_sym[req] = base

    if not cleaned:
        return {}

    url = "https://stooq.com/q/l/?" + urllib.parse.urlencode(
        {
            "s": ",".join(cleaned),
            "f": "sd2t2ohlcv",
            "h": "",
            "e": "csv",
        }
    )

    try:
        with urllib.request.urlopen(url, timeout=15) as resp:  # nosec - controlled domain
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return {}

    reader = csv.DictReader(io.StringIO(raw))
    out: dict[str, Decimal] = {}
    for row in reader:
        if not row:
            continue
        sym = (row.get("Symbol") or "").strip().lower()
        close_s = (row.get("Close") or "").strip()
        if not sym or not close_s or close_s.upper() == "N/D":
            continue

        base = key_by_request_sym.get(sym)
        if base is None:
            # If we requested without suffix but got a suffix back (or vice versa), normalize.
            if sym.endswith(".us"):
                base = key_by_request_sym.get(sym[:-3])
            else:
                base = key_by_request_sym.get(f"{sym}.us")
        if base is None:
            continue

        try:
            out[base] = Decimal(close_s)
        except Exception:
            continue

    return out


def _fetch_yahoo_chart_price_usd(symbol: str) -> Decimal | None:
    """Fetch a single symbol price from Yahoo chart endpoint.

    The v7 quote endpoint is often restricted (401). The chart endpoint is more
    reliably accessible with a User-Agent header.
    """

    sym = (symbol or "").strip().upper()
    if not sym or sym in {"USD", "USDC"}:
        return None

    url = "https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(sym) + "?" + urllib.parse.urlencode(
        {"interval": "1d", "range": "5d"}
    )

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            "Accept": "application/json",
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # nosec - public endpoint
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None

    try:
        data = json.loads(raw)
    except Exception:
        return None

    chart = (data or {}).get("chart") or {}
    results = chart.get("result") or []
    if not isinstance(results, list) or not results:
        return None

    meta = (results[0] or {}).get("meta") or {}
    p = meta.get("regularMarketPrice")
    if p is None:
        return None
    try:
        return Decimal(str(p))
    except Exception:
        return None
