import importlib
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient


def _coinbase_acct(currency: str, available: str = "0", hold: str = "0", uuid: str | None = None, name: str | None = None):
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

    # Keep Plaid tokens local to this test run.
    monkeypatch.setenv("FINAGENT_PLAID_TOKENS_PATH", str(tmp_path / ".plaid_tokens.json"))

    from financial_agent import agent_api

    importlib.reload(agent_api)
    return TestClient(agent_api.app)


def test_containers_include_schwab_when_plaid_item_exists(app_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path):
    from financial_agent import agent_api
    from financial_agent.plaid_store import save_plaid_item

    # Minimal Coinbase data so the aggregate portfolio can compute.
    class DummyCoinbase:
        def list_accounts(self):
            return [_coinbase_acct("USD", available="1", uuid="usd-1", name="USD Wallet")]

        def get_spot_price(self, *, symbol_or_product_id: str, quote_currency: str = "USD"):
            return None

    monkeypatch.setattr(agent_api, "coinbase_client", DummyCoinbase())

    # Create a Schwab Plaid item in the local token store.
    save_plaid_item(
        container_id="schwab",
        access_token="access-sandbox-123",
        item_id="item-123",
        institution_name="Charles Schwab",
        path=tmp_path / ".plaid_tokens.json",
    )

    # Mock Plaid holdings response (used by Schwab provider).
    def fake_get_investments_holdings(*, access_token: str) -> dict:
        assert access_token == "access-sandbox-123"
        return {
            "accounts": [
                {"account_id": "schwab-acc-1", "name": "Brokerage"},
                {"account_id": "schwab-acc-2", "name": "Roth IRA"},
            ],
            "holdings": [
                {
                    "account_id": "schwab-acc-1",
                    "security_id": "sec-aapl",
                    "quantity": "10",
                    "institution_price": "200",
                    "institution_value": None,
                },
                {
                    "account_id": "schwab-acc-2",
                    "security_id": "sec-msft",
                    "quantity": "20",
                    "institution_price": "150",
                    "institution_value": "3000",
                },
            ],
            "securities": [
                {"security_id": "sec-aapl", "ticker_symbol": "AAPL", "type": "equity"},
                {"security_id": "sec-msft", "ticker_symbol": "MSFT", "type": "equity"},
            ],
        }

    from financial_agent.providers import schwab_plaid_provider

    monkeypatch.setattr(schwab_plaid_provider, "get_investments_holdings", fake_get_investments_holdings)

    resp = app_client.get("/agent/containers")
    assert resp.status_code == 200
    data = resp.json()

    containers = {(c["source"], c.get("container_id")): c for c in data["containers"]}
    assert ("schwab", "schwab") in containers

    schwab = containers[("schwab", "schwab")]
    assert schwab["name"] == "Charles Schwab"
    assert Decimal(schwab["total_value"]) == Decimal("5000")


def test_schwab_container_accounts_are_discoverable(app_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path):
    from financial_agent import agent_api
    from financial_agent.plaid_store import save_plaid_item

    class DummyCoinbase:
        def list_accounts(self):
            return [_coinbase_acct("USD", available="1", uuid="usd-1", name="USD Wallet")]

        def get_spot_price(self, *, symbol_or_product_id: str, quote_currency: str = "USD"):
            return None

    monkeypatch.setattr(agent_api, "coinbase_client", DummyCoinbase())

    save_plaid_item(
        container_id="schwab",
        access_token="access-sandbox-456",
        item_id="item-456",
        institution_name="Schwab",
        path=tmp_path / ".plaid_tokens.json",
    )

    def fake_get_investments_holdings(*, access_token: str) -> dict:
        assert access_token == "access-sandbox-456"
        return {
            "accounts": [
                {"account_id": "schwab-acc-1", "name": "Brokerage"},
                {"account_id": "schwab-acc-2", "name": "Roth IRA"},
            ],
            "holdings": [],
            "securities": [],
        }

    from financial_agent.providers import schwab_plaid_provider

    monkeypatch.setattr(schwab_plaid_provider, "get_investments_holdings", fake_get_investments_holdings)

    resp = app_client.get(
        "/agent/container/accounts",
        params={"source": "schwab", "container_id": "schwab"},
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["source"] == "schwab"
    assert data["container_id"] == "schwab"

    accounts = {(a["account_id"], a.get("name")) for a in data["accounts"]}
    assert accounts == {("schwab-acc-1", "Brokerage"), ("schwab-acc-2", "Roth IRA")}


def test_schwab_container_holdings_use_institution_values(app_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path):
    from financial_agent import agent_api
    from financial_agent.plaid_store import save_plaid_item

    class DummyCoinbase:
        def list_accounts(self):
            return [_coinbase_acct("USD", available="1", uuid="usd-1", name="USD Wallet")]

        def get_spot_price(self, *, symbol_or_product_id: str, quote_currency: str = "USD"):
            return None

    monkeypatch.setattr(agent_api, "coinbase_client", DummyCoinbase())

    save_plaid_item(
        container_id="schwab",
        access_token="access-sandbox-789",
        item_id="item-789",
        institution_name="Schwab",
        path=tmp_path / ".plaid_tokens.json",
    )

    def fake_get_investments_holdings(*, access_token: str) -> dict:
        assert access_token == "access-sandbox-789"
        return {
            "accounts": [
                {"account_id": "schwab-acc-1", "name": "Brokerage"},
                {"account_id": "schwab-acc-2", "name": "Roth IRA"},
            ],
            "holdings": [
                {
                    "account_id": "schwab-acc-1",
                    "security_id": "sec-aapl",
                    "quantity": "10",
                    "institution_price": "200",
                    "institution_value": None,
                },
                {
                    "account_id": "schwab-acc-2",
                    "security_id": "sec-msft",
                    "quantity": "20",
                    "institution_price": "150",
                    "institution_value": "3000",
                },
            ],
            "securities": [
                {"security_id": "sec-aapl", "ticker_symbol": "AAPL", "type": "equity"},
                {"security_id": "sec-msft", "ticker_symbol": "MSFT", "type": "equity"},
            ],
        }

    from financial_agent.providers import schwab_plaid_provider

    monkeypatch.setattr(schwab_plaid_provider, "get_investments_holdings", fake_get_investments_holdings)

    resp = app_client.get(
        "/agent/container/holdings",
        params={"source": "schwab", "container_id": "schwab"},
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["source"] == "schwab"
    assert data["container_id"] == "schwab"

    holdings = {(h["asset"], h.get("account_id")): h for h in data["holdings"]}
    assert ("AAPL", "schwab-acc-1") in holdings
    assert ("MSFT", "schwab-acc-2") in holdings

    assert Decimal(holdings[("AAPL", "schwab-acc-1")]["market_value"]) == Decimal("2000")
    assert Decimal(holdings[("MSFT", "schwab-acc-2")]["market_value"]) == Decimal("3000")

    assert data["missing_prices"] == []


def test_plaid_status_and_unlink(app_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path):
    from financial_agent.plaid_store import save_plaid_item

    # Initially not linked.
    status = app_client.get("/agent/plaid/status").json()
    assert status["container_id"] == "schwab"
    assert status["linked"] is False

    # Save an item, then status reports linked.
    save_plaid_item(
        container_id="schwab",
        access_token="access-sandbox-xyz",
        item_id="item-xyz",
        institution_name="Schwab",
        path=tmp_path / ".plaid_tokens.json",
    )

    status = app_client.get("/agent/plaid/status").json()
    assert status["linked"] is True
    assert status["item_id"] == "item-xyz"
    assert status["institution_name"] == "Schwab"
    assert "created_at" in status

    # Unlink removes it.
    resp = app_client.post("/agent/plaid/unlink").json()
    assert resp["container_id"] == "schwab"
    assert resp["unlinked"] is True

    status = app_client.get("/agent/plaid/status").json()
    assert status["linked"] is False


def test_linked_schwab_container_shows_even_with_no_holdings(app_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path):
    from financial_agent import agent_api
    from financial_agent.plaid_store import save_plaid_item

    # Coinbase minimal so aggregate compute works.
    class DummyCoinbase:
        def list_accounts(self):
            return [_coinbase_acct("USD", available="1", uuid="usd-1", name="USD Wallet")]

        def get_spot_price(self, *, symbol_or_product_id: str, quote_currency: str = "USD"):
            return None

    monkeypatch.setattr(agent_api, "coinbase_client", DummyCoinbase())

    # Linked Schwab item.
    save_plaid_item(
        container_id="schwab",
        access_token="access-sandbox-empty",
        item_id="item-empty",
        institution_name="Schwab",
        path=tmp_path / ".plaid_tokens.json",
    )

    # No holdings returned.
    def fake_get_investments_holdings(*, access_token: str) -> dict:
        assert access_token == "access-sandbox-empty"
        return {
            "accounts": [],
            "holdings": [],
            "securities": [],
        }

    from financial_agent.providers import schwab_plaid_provider

    monkeypatch.setattr(schwab_plaid_provider, "get_investments_holdings", fake_get_investments_holdings)

    containers = app_client.get("/agent/containers").json()
    keys = {(c["source"], c.get("container_id")) for c in containers["containers"]}
    assert ("schwab", "schwab") in keys

    schwab = next(c for c in containers["containers"] if c["source"] == "schwab" and c.get("container_id") == "schwab")
    assert Decimal(schwab["total_value"]) == Decimal("0")
