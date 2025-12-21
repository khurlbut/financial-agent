from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException

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


@app.get("/agent/accounts")
async def get_agent_accounts() -> Dict[str, Any]:
    """
    Unified accounts view, currently only 'coinbase' for v0.
    """
    try:
        accounts = await coinbase_client.list_accounts()
    except Exception as exc:  # you can tighten this later
        raise HTTPException(status_code=502, detail=f"Coinbase error: {exc}")

    normalized = [normalize_coinbase_account(a) for a in accounts]

    return {
        "source": "coinbase",
        "accounts": normalized,
    }
