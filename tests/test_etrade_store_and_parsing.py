from __future__ import annotations

from pathlib import Path


def test_etrade_token_store_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("FINAGENT_ETRADE_TOKENS_PATH", str(tmp_path / ".etrade_tokens.json"))

    from financial_agent.etrade_store import delete_etrade_token, get_etrade_token, save_etrade_token

    assert get_etrade_token(container_id="kev") is None
    save_etrade_token(
        container_id="kev",
        oauth_token="tok",
        oauth_token_secret="sec",
        environment="sandbox",
    )
    tok = get_etrade_token(container_id="kev")
    assert tok is not None
    assert tok.oauth_token == "tok"
    assert tok.oauth_token_secret == "sec"
    assert tok.environment == "sandbox"

    assert delete_etrade_token(container_id="kev") is True
    assert get_etrade_token(container_id="kev") is None


def test_etrade_provider_parsing_accounts_and_positions():
    from financial_agent.providers.etrade_provider import _iter_accounts, _iter_positions

    accounts_resp = {
        "AccountListResponse": {
            "Accounts": {
                "Account": [
                    {"accountIdKey": "A1", "accountDesc": "Brokerage"},
                    {"accountIdKey": "A2", "accountDesc": "IRA"},
                ]
            }
        }
    }
    accounts = list(_iter_accounts(accounts_resp))
    assert [a["accountIdKey"] for a in accounts] == ["A1", "A2"]

    portfolio_resp = {
        "PortfolioResponse": {
            "AccountPortfolio": [
                {"Position": [{"symbol": "AAPL", "quantity": "2"}, {"symbol": "VTI", "quantity": "1"}]}
            ]
        }
    }
    positions = list(_iter_positions(portfolio_resp))
    assert [p["symbol"] for p in positions] == ["AAPL", "VTI"]


def test_etrade_authorize_url_format(monkeypatch):
    from financial_agent.etrade_client import build_etrade_authorize_url

    url = "https://us.etrade.com/e/t/etws/authorize"
    built = build_etrade_authorize_url(authorize_url=url, consumer_key="ck", request_token="reqtok")
    assert built.startswith(url)
    assert "key=ck" in built
    assert "token=reqtok" in built
