import importlib
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient


def _acct(currency: str, available: str = "0", hold: str = "0", uuid: str | None = None):
    return {
        "uuid": uuid or f"{currency}-uuid",
        "currency": currency,
        "available_balance": {"value": available, "currency": currency},
        "hold": {"value": hold, "currency": currency},
    }


@pytest.fixture()
def app_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Ensure agent_api can import without requiring real credentials.
    monkeypatch.setenv("COINBASE_API_KEY", "test")
    monkeypatch.setenv("COINBASE_API_SECRET", "test")

    from financial_agent import agent_api

    importlib.reload(agent_api)
    return TestClient(agent_api.app)


def test_agent_value_sums_cash_and_spot_assets_and_ignores_configured_assets(
    app_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    from financial_agent import agent_api, settings

    # Ignore WLUNA (so we don't attempt pricing it, and it doesn't show up as missing).
    monkeypatch.setenv("FINAGENT_IGNORED_ASSETS", "WLUNA")
    importlib.reload(settings)

    accounts = [
        _acct("USD", available="100", hold="5"),
        _acct("USDC", available="10", hold="0"),
        _acct("BTC", available="0.10", hold="0.02"),  # qty=0.12
        _acct("ETH", available="2", hold="0"),
        _acct("WLUNA", available="999", hold="0"),  # ignored
    ]

    class DummyCoinbase:
        def list_accounts(self):
            return accounts

        def get_spot_price(self, *, symbol_or_product_id: str, quote_currency: str = "USD"):
            prices = {"BTC": 100_000.0, "ETH": 4_000.0}
            return prices.get(symbol_or_product_id)

    monkeypatch.setattr(agent_api, "coinbase_client", DummyCoinbase())

    resp = app_client.get("/agent/value")
    assert resp.status_code == 200
    data = resp.json()

    # Cash: 105 USD + 10 USDC = 115
    # BTC: 0.12 * 100000 = 12000
    # ETH: 2 * 4000 = 8000
    expected = Decimal("115") + Decimal("12000") + Decimal("8000")
    assert Decimal(data["total_value"]) == expected
    assert data["missing_prices"] == []


def test_agent_value_reports_missing_prices_for_non_ignored_assets(
    app_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    from financial_agent import agent_api, settings

    monkeypatch.delenv("FINAGENT_IGNORED_ASSETS", raising=False)
    importlib.reload(settings)

    accounts = [
        _acct("USD", available="1", hold="0"),
        _acct("DOGE", available="10", hold="0"),
    ]

    class DummyCoinbase:
        def list_accounts(self):
            return accounts

        def get_spot_price(self, *, symbol_or_product_id: str, quote_currency: str = "USD"):
            return None

    monkeypatch.setattr(agent_api, "coinbase_client", DummyCoinbase())

    resp = app_client.get("/agent/value")
    assert resp.status_code == 200
    data = resp.json()

    assert Decimal(data["total_value"]) == Decimal("1")
    assert data["missing_prices"] == ["DOGE"]


def test_agent_positions_omits_ignored_assets(app_client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from financial_agent import agent_api

    monkeypatch.setenv("FINAGENT_IGNORED_ASSETS", "WLUNA")

    accounts = [
        _acct("USD", available="1", hold="0"),
        _acct("BTC", available="0.5", hold="0"),
        _acct("WLUNA", available="999", hold="0"),
    ]

    class DummyCoinbase:
        def list_accounts(self):
            return accounts

        def get_spot_prices_for_accounts(self, accounts):
            return {"BTC": 100_000.0, "WLUNA": 0.01}

    monkeypatch.setattr(agent_api, "coinbase_client", DummyCoinbase())

    resp = app_client.get("/agent/positions")
    assert resp.status_code == 200
    data = resp.json()
    symbols = sorted([p["symbol"] for p in data["positions"]])
    assert symbols == ["BTC"]


def test_agent_snapshot_omits_ignored_assets(app_client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from financial_agent import agent_api

    monkeypatch.setenv("FINAGENT_IGNORED_ASSETS", "WLUNA")

    accounts = [
        _acct("USD", available="10", hold="0"),
        _acct("BTC", available="0.1", hold="0.02"),
        _acct("WLUNA", available="999", hold="0"),
    ]

    class DummyCoinbase:
        def list_accounts(self):
            return accounts

        def get_spot_prices_for_accounts(self, accounts):
            return {"BTC": 100_000.0, "WLUNA": 0.01}

    monkeypatch.setattr(agent_api, "coinbase_client", DummyCoinbase())

    resp = app_client.get("/agent/snapshot")
    assert resp.status_code == 200
    data = resp.json()

    pos_assets = sorted([p["asset"] for p in data["positions"]])
    assert pos_assets == ["BTC"]
