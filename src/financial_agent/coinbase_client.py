from typing import Any, Dict, List
import os
from pathlib import Path

from dotenv import load_dotenv
from coinbase.rest import RESTClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=ENV_PATH)

API_KEY_ID = os.getenv("COINBASE_API_KEY")
API_SECRET = os.getenv("COINBASE_API_SECRET")

if API_SECRET:
    # Turn the literal backslash-n sequences into real newlines for PEM parsing
    API_SECRET = API_SECRET.replace("\\n", "\n")


class CoinbaseClient:
    def __init__(self) -> None:
        if not (API_KEY_ID and API_SECRET):
            raise RuntimeError("COINBASE_API_KEY or COINBASE_API_SECRET not set in environment")

        # Official Advanced Trade REST client.
        self._client = RESTClient(api_key=API_KEY_ID, api_secret=API_SECRET)

    def list_accounts(self) -> List[Dict[str, Any]]:
        """
        Returns the raw Coinbase Advanced Trade accounts list as a list of dicts.

        Backed by GET /api/v3/brokerage/accounts (List Accounts).
        """
        resp = self._client.get_accounts()  # GET /api/v3/brokerage/accounts

        # Depending on SDK version, resp may be a dict-like or a model object.
        if isinstance(resp, dict):
            return resp.get("accounts", [])

        accounts = getattr(resp, "accounts", [])
        return [a.to_dict() if hasattr(a, "to_dict") else dict(a) for a in accounts]

    def get_spot_prices_for_accounts(self, accounts: List[Dict[str, Any]]) -> Dict[str, float]:
        """
        Returns a mapping {asset_symbol: last_trade_price} for non-cash assets
        in the given accounts.

        Uses Advanced Trade market data endpoint:
        GET /api/v3/brokerage/market/products/{product_id}/ticker
        where product_id is assumed to be "{asset}-USD" for v0.
        """
        assets: set[str] = set()
        for acct in accounts:
            cur = acct.get("currency")
            # Skip pure cash wallets in v0.
            if cur and cur not in ("USD", "USDC"):
                assets.add(cur)

        prices: Dict[str, float] = {}

        for asset in assets:
            product_id = f"{asset}-USD"
            try:
                # Public market data: Get Public Market Trades (ticker).
                # Docs: /market/products/{product_id}/ticker
                ticker = self._client.get_public_market_trades(product_id=product_id)

                # Depending on SDK shape, adapt to dict or model.
                # For many SDKs, "price" or "last" is the relevant field.
                price = None

                if isinstance(ticker, dict):
                    # Example shapes to try; adjust if your actual object differs.
                    price = ticker.get("price") or ticker.get("last")
                else:
                    price = getattr(ticker, "price", None) or getattr(ticker, "last", None)

                if price is not None:
                    prices[asset] = float(price)

            except Exception:
                # For v0, swallow per-asset errors and leave price missing.
                # The caller can treat missing entries as "no current_price".
                continue

        return prices
