from typing import Any, Dict, List
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import os

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool

from .coinbase_client import CoinbaseClient
from .cold_storage import load_cold_storage_devices
from . import settings
from .portfolio_service import PortfolioService
from .pricing_providers import CoinbasePricingProvider
from .providers.coinbase_provider import CoinbaseHoldingsProvider
from .providers.cold_storage_provider import ColdStorageHoldingsProvider
from .models import (
    Account,
    AccountValuation,
    AssetAccountBreakdown,
    AssetValuation,
    CashBalance,
    ContainerAccount,
    ContainerAccounts,
    ContainerHoldings,
    ContainerSummaries,
    ContainerSummary,
    HoldingLine,
    NetWorthSummary,
    PricingInfo,
    PriceQuote,
    PortfolioValuation,
    PortfolioValue,
    PortfolioSnapshot,
    Position,
    TradeExecutionResponse,
    TradePreview,
    TradeRequest,
)

app = FastAPI(title="Financial Agent API")

coinbase_client = CoinbaseClient()


def _get_portfolio_service() -> PortfolioService:
    # Providers (holdings sources)
    providers = [
        CoinbaseHoldingsProvider(client=coinbase_client, container_id="coinbase"),
        ColdStorageHoldingsProvider(),
    ]

    # Pricing provider
    provider_id = settings.get_price_provider_id()
    if provider_id != "coinbase":
        # Keep v1 conservative: only Coinbase pricing implemented.
        raise HTTPException(status_code=500, detail=f"Unsupported pricing provider: {provider_id}")

    pricer = CoinbasePricingProvider(client=coinbase_client)
    return PortfolioService(providers=providers, pricer=pricer)


async def _compute_portfolio_valuation() -> PortfolioValuation:
    """Compute the aggregate portfolio valuation (Coinbase + cold storage).

    Centralizing this keeps /agent/portfolio, /agent/networth, and container endpoints
    consistent.
    """

    try:
        accounts: List[Dict[str, Any]] = await run_in_threadpool(coinbase_client.list_accounts)
        prices: Dict[str, float] = await run_in_threadpool(
            coinbase_client.get_spot_prices_for_accounts,
            accounts,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Coinbase error: {exc}")

    # Cold storage devices (user-maintained local file).
    try:
        cold_devices = load_cold_storage_devices(settings.get_cold_storage_path())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Cold storage file error: {exc}")

    ignored = settings.get_ignored_assets()
    as_of = datetime.now(timezone.utc)

    cash: list[CashBalance] = []
    positions: list[Position] = []

    # Coinbase (normalized into a single container).
    COINBASE_CONTAINER_ID = "coinbase"

    coinbase_cash_totals: dict[str, Decimal] = {}
    coinbase_position_qty: dict[str, Decimal] = {}

    for acct in accounts:
        currency = acct.get("currency")
        if not isinstance(currency, str) or not currency:
            continue

        asset_upper = currency.strip().upper()
        if asset_upper in ignored:
            continue

        available_balance = (acct.get("available_balance") or {}).get("value")
        hold_balance = (acct.get("hold") or {}).get("value")
        qty = _parse_decimal(available_balance) + _parse_decimal(hold_balance)
        if qty <= 0:
            continue

        if asset_upper in ("USD", "USDC"):
            coinbase_cash_totals[asset_upper] = coinbase_cash_totals.get(asset_upper, Decimal("0")) + qty
            continue

        coinbase_position_qty[asset_upper] = coinbase_position_qty.get(asset_upper, Decimal("0")) + qty

    # Coinbase cash balances as holdings.
    for cur, total_amt in sorted(coinbase_cash_totals.items(), key=lambda kv: kv[0]):
        cash.append(
            CashBalance(
                source="coinbase",
                account_id=COINBASE_CONTAINER_ID,
                currency=cur,
                available=None,
                total=str(total_amt),
            )
        )

    # Coinbase positions as holdings.
    for asset, total_qty in sorted(coinbase_position_qty.items(), key=lambda kv: kv[0]):
        price = prices.get(asset)
        mv: Decimal | None = None
        if price is not None:
            mv = total_qty * Decimal(str(price))

        positions.append(
            Position(
                source="coinbase",
                account_id=COINBASE_CONTAINER_ID,
                symbol=asset,
                asset=asset,
                quantity=str(total_qty),
                cost_basis=None,
                current_price=None if price is None else str(price),
                market_value=None if mv is None else str(mv),
                quote_currency="USD",
            )
        )

    # Cold storage positions (valued using Coinbase spot prices).
    cold_prices: dict[str, float] = {}
    cold_assets = sorted({asset for d in cold_devices for asset in d.holdings.keys()})
    for asset in cold_assets:
        if asset.strip().upper() in ignored:
            continue
        if asset in ("USD", "USDC"):
            cold_prices[asset] = 1.0
            continue
        try:
            p = await run_in_threadpool(
                coinbase_client.get_spot_price,
                symbol_or_product_id=asset,
                quote_currency="USD",
            )
        except Exception:
            p = None
        if p is not None:
            cold_prices[asset] = float(p)

    for device in cold_devices:
        for asset, qty_s in device.holdings.items():
            if asset.strip().upper() in ignored:
                continue
            qty = _parse_decimal(qty_s)
            if qty <= 0:
                continue

            price = cold_prices.get(asset)
            mv: Decimal | None = None
            if price is not None:
                mv = qty * Decimal(str(price))

            positions.append(
                Position(
                    source="cold_storage",
                    account_id=device.name,
                    symbol=asset,
                    asset=asset,
                    quantity=str(qty),
                    cost_basis=None,
                    current_price=None if price is None else str(price),
                    market_value=None if mv is None else str(mv),
                    quote_currency="USD",
                )
            )

    cash_total = sum((_parse_decimal(c.total) for c in cash), start=Decimal("0"))
    positions_total = sum(
        (_parse_decimal(p.market_value) for p in positions if p.market_value is not None),
        start=Decimal("0"),
    )
    total = cash_total + positions_total

    missing_prices = sorted({p.asset for p in positions if p.asset and p.market_value is None})

    # Rollup by asset.
    by_asset_map: dict[str, dict[str, Any]] = {}
    for p in positions:
        if not p.asset:
            continue
        asset = p.asset
        entry = by_asset_map.setdefault(
            asset,
            {
                "asset": asset,
                "quote_currency": p.quote_currency or "USD",
                "total_quantity": Decimal("0"),
                "price": p.current_price,
                "market_value": Decimal("0"),
                "has_price": False,
                "accounts": [],
            },
        )

        qty = _parse_decimal(p.quantity)
        entry["total_quantity"] += qty

        mv = _parse_decimal(p.market_value) if p.market_value is not None else None
        if mv is not None:
            entry["market_value"] += mv
            entry["has_price"] = True
            if entry.get("price") is None:
                entry["price"] = p.current_price

        entry["accounts"].append(
            AssetAccountBreakdown(
                source=p.source,
                account_id=p.account_id,
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

    # Rollup by account/device.
    cash_by_acct: dict[tuple[str, str | None], list[CashBalance]] = {}
    for c in cash:
        cash_by_acct.setdefault((c.source, c.account_id), []).append(c)

    positions_by_acct: dict[tuple[str, str | None], list[Position]] = {}
    for p in positions:
        positions_by_acct.setdefault((p.source, p.account_id), []).append(p)

    by_account: list[AccountValuation] = []

    # Coinbase container.
    acct_cash = cash_by_acct.get(("coinbase", COINBASE_CONTAINER_ID), [])
    acct_positions = positions_by_acct.get(("coinbase", COINBASE_CONTAINER_ID), [])
    acct_total = Decimal("0")
    for c in acct_cash:
        acct_total += _parse_decimal(c.total)
    for p in acct_positions:
        if p.market_value is not None:
            acct_total += _parse_decimal(p.market_value)

    if acct_cash or acct_positions:
        by_account.append(
            AccountValuation(
                source="coinbase",
                account_id=COINBASE_CONTAINER_ID,
                name="Coinbase",
                currency="USD",
                total_value=str(acct_total),
                cash=acct_cash,
                positions=acct_positions,
            )
        )

    # Cold storage devices.
    for device in cold_devices:
        acct_id = device.name
        acct_positions = positions_by_acct.get(("cold_storage", acct_id), [])
        if not acct_positions:
            continue

        acct_total = Decimal("0")
        for p in acct_positions:
            if p.market_value is not None:
                acct_total += _parse_decimal(p.market_value)

        by_account.append(
            AccountValuation(
                source="cold_storage",
                account_id=acct_id,
                name=device.name,
                currency="USD",
                total_value=str(acct_total),
                cash=[],
                positions=acct_positions,
            )
        )

    return PortfolioValuation(
        source="aggregate",
        as_of=as_of,
        currency="USD",
        total_value=str(total),
        cash_value=str(cash_total),
        positions_value=str(positions_total),
        by_asset=by_asset,
        by_account=by_account,
        missing_prices=missing_prices,
    )


def _parse_positive_decimal(value: str, field_name: str, errors: list[str]) -> Decimal | None:
    try:
        dec = Decimal(value)
    except (InvalidOperation, TypeError):
        errors.append(f"{field_name} must be a valid decimal string")
        return None

    if dec <= 0:
        errors.append(f"{field_name} must be > 0")
        return None

    return dec


def _parse_decimal(value: str | None) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return Decimal("0")


def _validate_trade_request(req: TradeRequest) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if req.source != "coinbase":
        errors.append("only source='coinbase' is supported in v1")

    if not req.symbol or not req.symbol.strip():
        errors.append("symbol is required")

    qty = _parse_positive_decimal(req.quantity, "quantity", errors)

    if req.order_type == "limit":
        if req.limit_price is None:
            errors.append("limit_price is required for limit orders")
        else:
            _parse_positive_decimal(req.limit_price, "limit_price", errors)
    else:
        if req.limit_price is not None:
            warnings.append("limit_price is ignored for market orders")

    if req.quote_currency != "USD":
        warnings.append("v1 assumes USD quote currency for Coinbase spot pricing")

    if qty is not None and qty > Decimal("1000000"):
        warnings.append("quantity is very large; double-check units")

    return errors, warnings


def normalize_coinbase_account(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Map Coinbase account JSON into the agent's normalized schema.
    """
    available = raw.get("available_balance", {}) or {}
    hold = raw.get("hold", {}) or {}
    total = raw.get("total_balance", {}) or {}

    available_value = available.get("value")
    hold_value = hold.get("value")
    total_value = total.get("value")
    if total_value is None:
        total_value = str(_parse_decimal(available_value) + _parse_decimal(hold_value))

    return Account(
        source="coinbase",
        container_id="coinbase",
        account_id=raw.get("uuid"),
        name=raw.get("name"),
        asset=raw.get("currency"),
        available=available_value,
        total=total_value,
    ).model_dump()


def normalize_coinbase_position(raw: Dict[str, Any], price: float | None) -> Dict[str, Any]:
    """
    Map a Coinbase account into a position-style record.

    For v0, quantity comes from available_balance.value, symbol == asset,
    cost_basis is not reconstructed, and current_price is optional.
    """
    available_balance = (raw.get("available_balance") or {}).get("value")
    hold_balance = (raw.get("hold") or {}).get("value")
    asset = raw.get("currency")
    if asset is None:
        asset = ""

    qty = _parse_decimal(available_balance) + _parse_decimal(hold_balance)

    market_value: Decimal | None = None
    if price is not None:
        market_value = qty * Decimal(str(price))

    return Position(
        source="coinbase",
        container_id="coinbase",
        account_id=raw.get("uuid"),
        symbol=asset,  # v0 assumption: spot symbol == asset code
        asset=asset,
        quantity=str(qty),
        cost_basis=None,  # v0: no cost basis reconstruction yet
        current_price=None if price is None else str(price),
        market_value=None if market_value is None else str(market_value),
        quote_currency="USD",
    ).model_dump()


def normalize_coinbase_cash_balance(raw: Dict[str, Any]) -> Dict[str, Any] | None:
    currency = raw.get("currency")
    if currency not in ("USD", "USDC"):
        return None

    available = (raw.get("available_balance") or {}).get("value")
    hold = (raw.get("hold") or {}).get("value")
    total = (raw.get("total_balance") or {}).get("value")
    # Treat empty/zero cash balances as absent.
    computed_total = _parse_decimal(total) if total is not None else (_parse_decimal(available) + _parse_decimal(hold))
    if computed_total <= 0:
        return None

    return CashBalance(
        source="coinbase",
        container_id="coinbase",
        account_id=raw.get("uuid"),
        currency=currency,
        available=available,
        total=str(computed_total),
    ).model_dump()


@app.get("/agent/accounts")
async def get_agent_accounts() -> Dict[str, Any]:
    """
    Unified accounts view, currently only 'coinbase' for v0.
    """
    try:
        accounts = await run_in_threadpool(coinbase_client.list_accounts)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Coinbase error: {exc}")

    normalized = [normalize_coinbase_account(a) for a in accounts]
    return {"source": "coinbase", "accounts": normalized}


@app.get("/agent/positions")
async def get_agent_positions() -> Dict[str, Any]:
    """
    Normalized positions view for Coinbase spot holdings.

    Positions are derived from account balances and decorated with a simple
    USD price per asset when available.
    """
    try:
        accounts: List[Dict[str, Any]] = await run_in_threadpool(coinbase_client.list_accounts)
        prices: Dict[str, float] = await run_in_threadpool(
            coinbase_client.get_spot_prices_for_accounts,
            accounts,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Coinbase error: {exc}")

    positions: List[Dict[str, Any]] = []
    ignored = settings.get_ignored_assets()

    for acct in accounts:
        # Skip empty or cash-only accounts in v0.
        available_balance = (acct.get("available_balance") or {}).get("value")
        hold_balance = (acct.get("hold") or {}).get("value")
        asset = acct.get("currency")
        if not isinstance(asset, str) or not asset:
            continue

        if asset.strip().upper() in ignored:
            continue

        if asset in ("USD", "USDC"):
            continue

        qty = _parse_decimal(available_balance) + _parse_decimal(hold_balance)
        if qty <= 0:
            continue

        price = prices.get(asset)
        positions.append(normalize_coinbase_position(acct, price))

    return {"source": "coinbase", "positions": positions}


@app.post("/agent/trades/preview", response_model=TradePreview)
async def preview_trade(req: TradeRequest) -> TradePreview:
    """
    Human-in-the-loop trade scaffold.

    This endpoint validates a proposed trade request (typically LLM-generated)
    using deterministic rules. It does NOT place trades.
    """
    errors, warnings = _validate_trade_request(req)
    is_valid = len(errors) == 0

    return TradePreview(
        source="coinbase",
        as_of=datetime.now(timezone.utc),
        request=req,
        is_valid=is_valid,
        errors=errors,
        warnings=warnings,
        requires_human_confirmation=True,
        execution_ready=False,
    )


@app.post("/agent/trades/execute", response_model=TradeExecutionResponse)
async def execute_trade(req: TradeRequest, confirm: bool = False) -> TradeExecutionResponse:
    """
    Human-in-the-loop execution scaffold.

    Requires `confirm=true` and a `client_order_id` for idempotency.
    Until Coinbase order placement is implemented, this returns HTTP 501.
    """
    if not confirm:
        raise HTTPException(
            status_code=409,
            detail="human confirmation required; re-call with ?confirm=true",
        )

    errors, warnings = _validate_trade_request(req)
    if not req.client_order_id:
        errors.append("client_order_id is required for execute (idempotency)")

    # Execution policy (local safety rails).
    allowed_symbols = settings.get_allowed_symbols()
    if not allowed_symbols:
        errors.append(
            "FINAGENT_ALLOWED_SYMBOLS must be set (comma-separated), e.g. 'BTC,ETH'"
        )

    symbol_upper = (req.symbol or "").strip().upper()
    if allowed_symbols and symbol_upper not in allowed_symbols:
        errors.append(f"symbol '{symbol_upper}' not in FINAGENT_ALLOWED_SYMBOLS")

    max_notional = settings.get_max_notional_usd()
    if max_notional is None:
        errors.append("FINAGENT_MAX_NOTIONAL_USD must be a valid decimal string")
        max_notional = Decimal("0")

    if max_notional <= 0:
        errors.append("FINAGENT_MAX_NOTIONAL_USD must be set to > 0")

    # v1 execution supports limit orders only (safer, explicit price).
    if req.order_type != "limit":
        return TradeExecutionResponse(
            source="coinbase",
            as_of=datetime.now(timezone.utc),
            request=req,
            status="not_implemented",
            message="v1 execution supports limit orders only",
            errors=[*errors, "order_type must be 'limit' for execute"],
            warnings=warnings,
            requires_human_confirmation=True,
            execution_ready=False,
        )

    # Notional check (qty * limit_price).
    qty_dec = _parse_positive_decimal(req.quantity, "quantity", errors)
    price_dec: Decimal | None = None
    if req.limit_price is not None:
        price_dec = _parse_positive_decimal(req.limit_price, "limit_price", errors)
    if qty_dec is not None and price_dec is not None:
        notional = qty_dec * price_dec
        if max_notional > 0 and notional > max_notional:
            errors.append(
                f"order notional {notional} exceeds FINAGENT_MAX_NOTIONAL_USD={max_notional}"
            )

    if errors:
        return TradeExecutionResponse(
            source="coinbase",
            as_of=datetime.now(timezone.utc),
            request=req,
            status="rejected",
            message="trade request rejected by deterministic validation",
            errors=errors,
            warnings=warnings,
            requires_human_confirmation=True,
            execution_ready=False,
        )

    product_id = f"{symbol_upper}-{(req.quote_currency or 'USD').strip().upper()}"
    base_size = req.quantity
    limit_price = req.limit_price or ""

    # Optional: preview with Coinbase first (server-side sanity check).
    try:
        await run_in_threadpool(
            coinbase_client.preview_limit_order_gtc,
            symbol_or_product_id=product_id,
            side=req.side,
            base_size=base_size,
            limit_price=limit_price,
            quote_currency=req.quote_currency,
        )
    except Exception as exc:
        return TradeExecutionResponse(
            source="coinbase",
            as_of=datetime.now(timezone.utc),
            request=req,
            status="rejected",
            message=f"Coinbase preview rejected the order: {exc}",
            errors=["coinbase preview failed"],
            warnings=warnings,
            requires_human_confirmation=True,
            execution_ready=False,
        )

    try:
        resp = await run_in_threadpool(
            coinbase_client.place_limit_order_gtc,
            client_order_id=req.client_order_id,
            symbol_or_product_id=product_id,
            side=req.side,
            base_size=base_size,
            limit_price=limit_price,
            quote_currency=req.quote_currency,
            post_only=False,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Coinbase execution error: {exc}")

    broker_order_id = None
    # Best-effort extraction from common SDK response shapes.
    for key in ("order_id", "orderId", "id"):
        if isinstance(resp, dict) and key in resp and resp.get(key):
            broker_order_id = str(resp.get(key))
            break
    if broker_order_id is None and isinstance(resp, dict):
        # Some responses nest fields under 'success_response'.
        sr = resp.get("success_response")
        if isinstance(sr, dict):
            for key in ("order_id", "orderId", "id"):
                if sr.get(key):
                    broker_order_id = str(sr.get(key))
                    break

    return TradeExecutionResponse(
        source="coinbase",
        as_of=datetime.now(timezone.utc),
        request=req,
        status="submitted",
        message="order submitted to Coinbase",
        broker_order_id=broker_order_id,
        raw=resp if isinstance(resp, dict) else {"repr": repr(resp)},
        errors=[],
        warnings=warnings,
        requires_human_confirmation=True,
        execution_ready=True,
    )


@app.get("/agent/snapshot", response_model=PortfolioSnapshot)
async def get_agent_snapshot() -> PortfolioSnapshot:
    """
    Normalized portfolio snapshot for Coinbase.

    Includes accounts, positions (non-cash assets), and cash balances (USD/USDC).
    """
    try:
        accounts: List[Dict[str, Any]] = await run_in_threadpool(coinbase_client.list_accounts)
        prices: Dict[str, float] = await run_in_threadpool(
            coinbase_client.get_spot_prices_for_accounts,
            accounts,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Coinbase error: {exc}")

    normalized_accounts = [normalize_coinbase_account(a) for a in accounts]

    positions: List[Dict[str, Any]] = []
    cash: List[Dict[str, Any]] = []
    ignored = settings.get_ignored_assets()

    for acct in accounts:
        maybe_cash = normalize_coinbase_cash_balance(acct)
        if maybe_cash is not None:
            cash.append(maybe_cash)
            continue

        available_balance = (acct.get("available_balance") or {}).get("value")
        hold_balance = (acct.get("hold") or {}).get("value")
        asset = acct.get("currency")
        if not isinstance(asset, str) or not asset:
            continue

        if asset.strip().upper() in ignored:
            continue

        # Skip empty accounts.
        qty = _parse_decimal(available_balance) + _parse_decimal(hold_balance)
        if qty <= 0:
            continue

        price = prices.get(asset)
        positions.append(normalize_coinbase_position(acct, price))

    return PortfolioSnapshot(
        source="coinbase",
        as_of=datetime.now(timezone.utc),
        accounts=[Account(**a) for a in normalized_accounts],
        positions=[Position(**p) for p in positions],
        cash=[CashBalance(**c) for c in cash],
    )


@app.get("/agent/price", response_model=PriceQuote)
async def get_agent_price(symbol: str, quote_currency: str = "USD") -> PriceQuote:
    """Get a spot/ticker price for a symbol even if you don't hold it."""
    sym = (symbol or "").strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol is required")

    qc = (quote_currency or "USD").strip().upper()
    product_id = f"{sym}-{qc}" if "-" not in sym else sym

    try:
        price = await run_in_threadpool(
            coinbase_client.get_spot_price,
            symbol_or_product_id=product_id,
            quote_currency=qc,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Coinbase error: {exc}")

    if price is None:
        raise HTTPException(status_code=502, detail=f"No price available for {product_id}")

    return PriceQuote(
        source="coinbase",
        as_of=datetime.now(timezone.utc),
        product_id=product_id,
        price=str(price),
    )


@app.get("/agent/value", response_model=PortfolioValue)
async def get_agent_value() -> PortfolioValue:
    """Compute total Coinbase holdings value in USD (cash + spot assets)."""
    try:
        accounts: List[Dict[str, Any]] = await run_in_threadpool(coinbase_client.list_accounts)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Coinbase error: {exc}")

    total_usd = Decimal("0")
    missing: list[str] = []
    ignored = settings.get_ignored_assets()

    # Cash wallets (treat USD + USDC as USD equivalent for v1).
    for acct in accounts:
        cur = acct.get("currency")
        if cur in ("USD", "USDC"):
            available = (acct.get("available_balance") or {}).get("value")
            hold = (acct.get("hold") or {}).get("value")
            total_usd += _parse_decimal(available) + _parse_decimal(hold)

    # Spot assets.
    for acct in accounts:
        cur = acct.get("currency")
        if not isinstance(cur, str) or not cur or cur in ("USD", "USDC"):
            continue

        if cur.strip().upper() in ignored:
            continue

        available = (acct.get("available_balance") or {}).get("value")
        hold = (acct.get("hold") or {}).get("value")
        qty = _parse_decimal(available) + _parse_decimal(hold)
        if qty <= 0:
            continue

        try:
            price = await run_in_threadpool(
                coinbase_client.get_spot_price,
                symbol_or_product_id=cur,
                quote_currency="USD",
            )
        except Exception:
            price = None

        if price is None:
            missing.append(cur)
            continue

        total_usd += qty * Decimal(str(price))

    return PortfolioValue(
        source="coinbase",
        as_of=datetime.now(timezone.utc),
        currency="USD",
        total_value=str(total_usd),
        missing_prices=sorted(set(missing)),
    )


@app.get("/agent/portfolio", response_model=PortfolioValuation)
async def get_agent_portfolio() -> PortfolioValuation:
    """Valued portfolio view (snapshot + rollups).

    Returns:
    - total_value (cash + priced positions)
    - by_asset (aggregated across accounts)
    - by_account (account totals with holdings)

    Assets in FINAGENT_IGNORED_ASSETS are omitted from positions/rollups.
    """

    svc = _get_portfolio_service()
    computed = await svc.compute_portfolio()
    return computed.portfolio


@app.get("/agent/pricing", response_model=PricingInfo)
async def get_agent_pricing() -> PricingInfo:
    svc = _get_portfolio_service()
    return PricingInfo(
        as_of=datetime.now(timezone.utc),
        pricing_provider_id=svc.pricing_provider_id,
        quote_currency="USD",
    )


@app.get("/agent/networth", response_model=NetWorthSummary)
async def get_agent_networth() -> NetWorthSummary:
    svc = _get_portfolio_service()
    return await svc.get_networth()


@app.get("/agent/containers", response_model=ContainerSummaries)
async def get_agent_containers() -> ContainerSummaries:
    svc = _get_portfolio_service()
    computed = await svc.compute_portfolio()
    containers = computed.container_totals
    return ContainerSummaries(
        source="aggregate",
        as_of=computed.as_of,
        currency=computed.currency,
        containers=containers,
    )


@app.get("/agent/container/accounts", response_model=ContainerAccounts)
async def get_agent_container_accounts(
    source: str,
    container_id: str | None = None,
    account_id: str | None = None,
) -> ContainerAccounts:
    src = (source or "").strip()
    if not src:
        raise HTTPException(status_code=400, detail="source is required")

    # Back-compat: older callers used account_id to refer to a container.
    if container_id is None:
        container_id = account_id
        account_id = None

    if not container_id:
        raise HTTPException(status_code=400, detail="container_id is required")

    svc = _get_portfolio_service()
    try:
        accounts = await svc.list_accounts(source=src, container_id=container_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="container not found")

    return ContainerAccounts(
        source=src,  # type: ignore[arg-type]
        container_id=container_id,
        accounts=[
            ContainerAccount(
                source=src,  # type: ignore[arg-type]
                container_id=container_id,
                account_id=a.account_id,
                name=a.name,
            )
            for a in accounts
        ],
    )


@app.get("/agent/container/value", response_model=ContainerSummary)
async def get_agent_container_value(
    source: str,
    container_id: str | None = None,
    account_id: str | None = None,
) -> ContainerSummary:
    src = (source or "").strip()
    if not src:
        raise HTTPException(status_code=400, detail="source is required")

    # Back-compat: older callers used account_id to refer to a container.
    if container_id is None:
        container_id = account_id
        account_id = None

    if not container_id:
        raise HTTPException(status_code=400, detail="container_id is required")

    svc = _get_portfolio_service()

    # If an account_id is provided, return the specific account valuation.
    if account_id is not None:
        computed = await svc.compute_portfolio()
        for a in computed.portfolio.by_account:
            if a.source == src and a.container_id == container_id and a.account_id == account_id:
                return ContainerSummary(
                    source=a.source,
                    container_id=container_id,
                    account_id=a.account_id,
                    name=a.name,
                    currency=a.currency,
                    total_value=a.total_value,
                )
        raise HTTPException(status_code=404, detail="account not found")

    try:
        return await svc.get_container_value(source=src, container_id=container_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="container not found")


@app.get("/agent/container/holdings", response_model=ContainerHoldings)
async def get_agent_container_holdings(
    source: str,
    container_id: str | None = None,
    account_id: str | None = None,
) -> ContainerHoldings:
    src = (source or "").strip()
    if not src:
        raise HTTPException(status_code=400, detail="source is required")

    # Back-compat: older callers used account_id to refer to a container.
    if container_id is None:
        container_id = account_id
        account_id = None

    if not container_id:
        raise HTTPException(status_code=400, detail="container_id is required")

    svc = _get_portfolio_service()
    try:
        return await svc.get_container_holdings(
            source=src,
            container_id=container_id,
            account_id=account_id,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="container not found")
