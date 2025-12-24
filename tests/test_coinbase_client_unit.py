import importlib

import pytest


def test_coinbase_list_accounts_paginates_and_dedupes(monkeypatch: pytest.MonkeyPatch):
    # Import module after ensuring credentials exist.
    monkeypatch.setenv("COINBASE_API_KEY", "test")
    monkeypatch.setenv("COINBASE_API_SECRET", "test")

    from financial_agent import coinbase_client

    class DummyREST:
        def __init__(self, api_key: str, api_secret: str):
            self.calls = []

        def get_accounts(self, limit=None, cursor=None, retail_portfolio_id=None, **kwargs):
            self.calls.append({"limit": limit, "cursor": cursor})
            if cursor is None:
                return {
                    "accounts": [
                        {"uuid": "A", "currency": "USD"},
                        {"uuid": "B", "currency": "BTC"},
                    ],
                    "has_next": True,
                    "cursor": "next",
                }
            return {
                "accounts": [
                    {"uuid": "B", "currency": "BTC"},  # duplicate
                    {"uuid": "C", "currency": "ETH"},
                ],
                "has_next": False,
                "cursor": None,
            }

    monkeypatch.setattr(coinbase_client, "RESTClient", DummyREST)

    client = coinbase_client.CoinbaseClient()
    accounts = client.list_accounts()

    assert [a.get("uuid") for a in accounts] == ["A", "B", "C"]


def test_coinbase_get_spot_price_applies_eth2_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COINBASE_API_KEY", "test")
    monkeypatch.setenv("COINBASE_API_SECRET", "test")

    from financial_agent import coinbase_client

    class DummyREST:
        def __init__(self, api_key: str, api_secret: str):
            self.last_product_id = None

        def get_public_market_trades(self, *, product_id: str, limit: int):
            self.last_product_id = product_id
            return {"trades": [{"price": "123.45"}]}

    monkeypatch.setattr(coinbase_client, "RESTClient", DummyREST)

    client = coinbase_client.CoinbaseClient()
    price = client.get_spot_price(symbol_or_product_id="ETH2", quote_currency="USD")

    assert price == 123.45
    assert client._client.last_product_id == "ETH-USD"
