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
def app_client(monkeypatch: pytest.MonkeyPatch, tmp_path) -> TestClient:
    # Ensure agent_api can import without requiring real credentials.
    monkeypatch.setenv("COINBASE_API_KEY", "test")
    monkeypatch.setenv("COINBASE_API_SECRET", "test")
    # Avoid accidentally reading the repo-root cold_storage.json during tests.
    monkeypatch.setenv("FINAGENT_COLD_STORAGE_PATH", str(tmp_path / "missing_cold_storage.json"))

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

    assert data["source"] == "aggregate"

    # Total value = cash + BTC + ETH
    expected_total = Decimal("100") + Decimal("0.12") * Decimal("100000") + Decimal("2") * Decimal("4000")
    assert Decimal(data["total_value"]) == expected_total

    # By-asset rollup includes BTC and ETH, not WLUNA
    by_asset = {row["asset"]: row for row in data["by_asset"]}
    assert set(by_asset.keys()) == {"BTC", "ETH"}
    assert Decimal(by_asset["BTC"]["total_quantity"]) == Decimal("0.12")
    assert Decimal(by_asset["BTC"]["market_value"]) == Decimal("12000")

    # BTC should show a single Coinbase container breakdown
    btc_accounts = [(a.get("source"), a.get("account_id"), Decimal(a["quantity"])) for a in by_asset["BTC"]["accounts"]]
    assert btc_accounts == [("coinbase", "coinbase", Decimal("0.12"))]

    # By-account rollup should include a single Coinbase container.
    by_account = {(row["source"], row.get("account_id")): row for row in data["by_account"]}
    assert ("coinbase", "coinbase") in by_account
    assert Decimal(by_account[("coinbase", "coinbase")]["total_value"]) == expected_total

    assert data["missing_prices"] == []


def test_agent_portfolio_includes_cold_storage_holdings(app_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path):
    from financial_agent import agent_api

    cold_path = tmp_path / "cold_storage.json"
    cold_path.write_text(
        '{"devices": [{"name": "Trezor 2022", "holdings": {"BTC": "1.5"}}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("FINAGENT_COLD_STORAGE_PATH", str(cold_path))

    accounts = [
        _acct("USD", available="100", hold="0", uuid="usd-1"),
    ]

    class DummyCoinbase:
        def list_accounts(self):
            return accounts

        def get_spot_prices_for_accounts(self, accounts):
            return {}

        def get_spot_price(self, *, symbol_or_product_id: str, quote_currency: str = "USD"):
            assert quote_currency == "USD"
            if symbol_or_product_id == "BTC":
                return 100_000.0
            return None

    monkeypatch.setattr(agent_api, "coinbase_client", DummyCoinbase())

    resp = app_client.get("/agent/portfolio")
    assert resp.status_code == 200
    data = resp.json()

    # $100 cash + 1.5 BTC @ $100k
    assert Decimal(data["total_value"]) == Decimal("100") + (Decimal("1.5") * Decimal("100000"))

    by_asset = {row["asset"]: row for row in data["by_asset"]}
    assert "BTC" in by_asset
    btc_accounts = {(a.get("source"), a.get("account_id")): Decimal(a["quantity"]) for a in by_asset["BTC"]["accounts"]}
    assert btc_accounts[("cold_storage", "Trezor 2022")] == Decimal("1.5")

    by_account = {(row["source"], row["account_id"]): row for row in data["by_account"]}
    assert ("cold_storage", "Trezor 2022") in by_account
    assert Decimal(by_account[("cold_storage", "Trezor 2022")]["total_value"]) == Decimal("150000")


def test_agent_networth_and_container_endpoints(app_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path):
    from financial_agent import agent_api

    cold_path = tmp_path / "cold_storage.json"
    cold_path.write_text(
        '{"devices": [{"name": "Trezor 2022", "holdings": {"BTC": "1.5"}}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("FINAGENT_COLD_STORAGE_PATH", str(cold_path))

    accounts = [
        _acct("USD", available="100", hold="0", uuid="usd-1", name="USD Wallet"),
        _acct("ETH", available="2", hold="0", uuid="eth-1", name="ETH Wallet"),
    ]

    class DummyCoinbase:
        def list_accounts(self):
            return accounts

        def get_spot_prices_for_accounts(self, accounts):
            return {"ETH": 4_000.0}

        def get_spot_price(self, *, symbol_or_product_id: str, quote_currency: str = "USD"):
            if symbol_or_product_id == "BTC":
                return 100_000.0
            return None

    monkeypatch.setattr(agent_api, "coinbase_client", DummyCoinbase())

    # Net worth = $100 cash + 2 ETH @ $4k + 1.5 BTC @ $100k
    expected_total = Decimal("100") + (Decimal("2") * Decimal("4000")) + (Decimal("1.5") * Decimal("100000"))

    networth = app_client.get("/agent/networth").json()
    assert Decimal(networth["total_value"]) == expected_total

    containers = app_client.get("/agent/containers").json()
    # Should include at least: coinbase (normalized), Trezor 2022
    keys = {(c["source"], c.get("account_id")) for c in containers["containers"]}
    assert ("coinbase", "coinbase") in keys
    assert ("cold_storage", "Trezor 2022") in keys

    trezor_value = app_client.get(
        "/agent/container/value",
        params={"source": "cold_storage", "account_id": "Trezor 2022"},
    ).json()
    assert Decimal(trezor_value["total_value"]) == Decimal("150000")

    trezor_holdings = app_client.get(
        "/agent/container/holdings",
        params={"source": "cold_storage", "account_id": "Trezor 2022"},
    ).json()
    holdings = {h["asset"]: h for h in trezor_holdings["holdings"]}
    assert Decimal(holdings["BTC"]["quantity"]) == Decimal("1.5")
    assert Decimal(holdings["BTC"]["market_value"]) == Decimal("150000")
