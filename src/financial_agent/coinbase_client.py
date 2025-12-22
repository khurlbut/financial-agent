from typing import Any, Dict, List, Optional
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
                ticker = self._client.get_public_market_trades(product_id=product_id, limit=1)
                price = self._extract_last_trade_price(ticker)
                if price is not None:
                    prices[asset] = float(price)
            except Exception:
                continue

        return prices

    @staticmethod
    def _normalize_product_id(symbol_or_product_id: str, quote_currency: str = "USD") -> str:
        s = (symbol_or_product_id or "").strip().upper()
        q = (quote_currency or "USD").strip().upper()
        if "-" in s:
            return s
        return f"{s}-{q}"

    @staticmethod
    def _to_dict(resp: Any) -> Dict[str, Any]:
        if resp is None:
            return {}
        if isinstance(resp, dict):
            return resp
        if hasattr(resp, "to_dict"):
            try:
                return resp.to_dict()  # type: ignore[no-any-return]
            except Exception:
                pass
        # Best-effort fallback.
        try:
            return dict(resp)
        except Exception:
            return {"repr": repr(resp)}

    def preview_limit_order_gtc(
        self,
        *,
        symbol_or_product_id: str,
        side: str,
        base_size: str,
        limit_price: str,
        quote_currency: str = "USD",
    ) -> Dict[str, Any]:
        product_id = self._normalize_product_id(symbol_or_product_id, quote_currency=quote_currency)
        side_upper = side.strip().lower()

        if side_upper == "buy":
            resp = self._client.preview_limit_order_gtc_buy(
                product_id=product_id,
                base_size=base_size,
                limit_price=limit_price,
            )
        elif side_upper == "sell":
            resp = self._client.preview_limit_order_gtc_sell(
                product_id=product_id,
                base_size=base_size,
                limit_price=limit_price,
            )
        else:
            raise ValueError("side must be 'buy' or 'sell'")

        return self._to_dict(resp)

    def place_limit_order_gtc(
        self,
        *,
        client_order_id: str,
        symbol_or_product_id: str,
        side: str,
        base_size: str,
        limit_price: str,
        quote_currency: str = "USD",
        post_only: bool = False,
    ) -> Dict[str, Any]:
        product_id = self._normalize_product_id(symbol_or_product_id, quote_currency=quote_currency)
        side_upper = side.strip().lower()

        if side_upper == "buy":
            resp = self._client.limit_order_gtc_buy(
                client_order_id=client_order_id,
                product_id=product_id,
                base_size=base_size,
                limit_price=limit_price,
                post_only=post_only,
            )
        elif side_upper == "sell":
            resp = self._client.limit_order_gtc_sell(
                client_order_id=client_order_id,
                product_id=product_id,
                base_size=base_size,
                limit_price=limit_price,
                post_only=post_only,
            )
        else:
            raise ValueError("side must be 'buy' or 'sell'")

        return self._to_dict(resp)

    @staticmethod
    def _extract_last_trade_price(market_trades: Any) -> Optional[str]:
        """Best-effort extraction of a last trade price from get_public_market_trades."""
        if market_trades is None:
            return None

        if isinstance(market_trades, dict):
            trades = market_trades.get("trades")
            if isinstance(trades, list) and trades:
                first = trades[0]
                if isinstance(first, dict):
                    return first.get("price") or first.get("trade_price")

            trade = market_trades.get("trade")
            if isinstance(trade, dict):
                return trade.get("price") or trade.get("trade_price")

            return market_trades.get("price") or market_trades.get("last")

        trades_attr = getattr(market_trades, "trades", None)
        if isinstance(trades_attr, list) and trades_attr:
            first = trades_attr[0]
            if isinstance(first, dict):
                return first.get("price") or first.get("trade_price")
            return getattr(first, "price", None) or getattr(first, "trade_price", None)

        return getattr(market_trades, "price", None) or getattr(market_trades, "last", None)

    def get_spot_price(
        self,
        *,
        symbol_or_product_id: str,
        quote_currency: str = "USD",
    ) -> Optional[float]:
        """Return the latest observed trade price for a product.

        Uses the public market data endpoint via the SDK.
        """
        product_id = self._normalize_product_id(symbol_or_product_id, quote_currency=quote_currency)
        ticker = self._client.get_public_market_trades(product_id=product_id, limit=1)
        price = self._extract_last_trade_price(ticker)
        if price is None:
            return None
        return float(price)
