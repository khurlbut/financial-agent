from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool

from .coinbase_client import CoinbaseClient

app = FastAPI(title="Financial Agent API")

coinbase_client = CoinbaseClient()


def normalize_coinbase_account(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Map Coinbase account JSON into the agent's normalized schema.
    """
    available = raw.get("available_balance", {}) or {}
    return {
        "account_id": raw.get("uuid"),
        "name": raw.get("name"),
        "asset": raw.get("currency"),
        "available": available.get("value"),
        "total": available.get("value"),  # simple for v0
    }


def normalize_coinbase_position(raw: Dict[str, Any], price: float | None) -> Dict[str, Any]:
    """
    Map a Coinbase account into a position-style record.

    For v0, quantity comes from available_balance.value, symbol == asset,
    cost_basis is not reconstructed, and current_price is optional.
    """
    available_balance = (raw.get("available_balance") or {}).get("value")
    asset = raw.get("currency")

    qty_float: float = 0.0
    if available_balance is not None:
        try:
            qty_float = float(available_balance)
        except (TypeError, ValueError):
            qty_float = 0.0

    market_value: float | None = None
    if price is not None:
        market_value = qty_float * price

    return {
        "account_id": raw.get("uuid"),
        "symbol": asset,  # v0 assumption: spot symbol == asset code
        "asset": asset,
        "quantity": str(qty_float),
        "cost_basis": None,  # v0: no cost basis reconstruction yet
        "current_price": None if price is None else str(price),
        "market_value": None if market_value is None else str(market_value),
    }


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

    for acct in accounts:
        # Skip empty or cash-only accounts in v0.
        available_balance = (acct.get("available_balance") or {}).get("value")
        asset = acct.get("currency")

        if asset in ("USD", "USDC"):
            continue

        if available_balance in (None, "0", "0.0"):
            continue

        price = prices.get(asset)
        positions.append(normalize_coinbase_position(acct, price))

    return {"source": "coinbase", "positions": positions}
