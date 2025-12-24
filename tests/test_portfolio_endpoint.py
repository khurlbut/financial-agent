import importlib
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient


def _acct(currency: str, available: str = "0", hold: str = "0", uuid: str | None = None, name: str | None = None):
    return {
        "uuid": uuid or f"{currency}-uuid",
        "name": name or f"{currency} Wallet",
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


def test_agent_portfolio_rolls_up_by_asset_and_by_account(app_client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from financial_agent import agent_api

    # Ignore WLUNA in portfolio views.
    monkeypatch.setenv("FINAGENT_IGNORED_ASSETS", "WLUNA")

    accounts = [
        _acct("USD", available="100", hold="0", uuid="usd-1"),
        _acct("BTC", available="0.10", hold="0.00", uuid="btc-a", name="BTC Wallet A"),
        _acct("BTC", available="0.02", hold="0.00", uuid="btc-b", name="BTC Wallet B"),
        _acct("ETH", available="2", hold="0", uuid="eth-1"),
        _acct("WLUNA", available="999", hold="0", uuid="wluna-1"),
    ]

    class DummyCoinbase:
        def list_accounts(self):
            return accounts

        def get_spot_prices_for_accounts(self, accounts):
            return {"BTC": 100_000.0, "ETH": 4_000.0}

    monkeypatch.setattr(agent_api, "coinbase_client", DummyCoinbase())

    resp = app_client.get("/agent/portfolio")
    assert resp.status_code == 200
    data = resp.json()

    # Total value = cash + BTC + ETH
    expected_total = Decimal("100") + Decimal("0.12") * Decimal("100000") + Decimal("2") * Decimal("4000")
    assert Decimal(data["total_value"]) == expected_total

    # By-asset rollup includes BTC and ETH, not WLUNA
    by_asset = {row["asset"]: row for row in data["by_asset"]}
    assert set(by_asset.keys()) == {"BTC", "ETH"}
    assert Decimal(by_asset["BTC"]["total_quantity"]) == Decimal("0.12")
    assert Decimal(by_asset["BTC"]["market_value"]) == Decimal("12000")

    # BTC should show account breakdown across both BTC wallets
    btc_accounts = sorted(
        [(a.get("account_id"), Decimal(a["quantity"])) for a in by_asset["BTC"]["accounts"]],
        key=lambda x: x[0],
    )
    assert btc_accounts == [("btc-a", Decimal("0.10")), ("btc-b", Decimal("0.02"))]

    # By-account rollup should include the two BTC accounts and the USD/ETH accounts.
    by_account = {row["account_id"]: row for row in data["by_account"]}
    assert set(by_account.keys()) >= {"usd-1", "btc-a", "btc-b", "eth-1"}

    # Spot-only accounts should reflect their totals.
    assert Decimal(by_account["btc-a"]["total_value"]) == Decimal("0.10") * Decimal("100000")
    assert Decimal(by_account["btc-b"]["total_value"]) == Decimal("0.02") * Decimal("100000")

    assert data["missing_prices"] == []
