from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


def _write_minimal_ms_csv(path, *, account_name: str, symbol: str):
    # Minimal generic export (header includes Symbol).
    lines = [
        "Symbol,Description,Quantity,Price,Market Value,Currency,Account\n",
        f"{symbol},Test,1,10,10,USD,{account_name}\n",
    ]
    path.write_text("".join(lines), encoding="utf-8")


def test_morgan_stanley_csv_supports_multiple_containers(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FINAGENT_DB_PATH", str(tmp_path / "financial_agent_test.sqlite3"))

    from financial_agent.morgan_stanley_csv.importer import import_positions_csv
    from financial_agent.providers.morgan_stanley_provider import MorganStanleyHoldingsProvider

    kev_csv = tmp_path / "kev.csv"
    deb_csv = tmp_path / "deb.csv"
    _write_minimal_ms_csv(kev_csv, account_name="Kev Brokerage ...111", symbol="AAPL")
    _write_minimal_ms_csv(deb_csv, account_name="Deb Brokerage ...222", symbol="MSFT")

    import_positions_csv(db_path=tmp_path / "financial_agent_test.sqlite3", csv_path=kev_csv, container_id="kev")
    import_positions_csv(db_path=tmp_path / "financial_agent_test.sqlite3", csv_path=deb_csv, container_id="deb")

    provider = MorganStanleyHoldingsProvider()

    containers = asyncio.run(provider.list_containers())
    cids = sorted(c.container_id for c in containers)
    assert cids == ["deb", "kev"]

    kev_accounts = asyncio.run(provider.list_accounts(container_id="kev"))
    deb_accounts = asyncio.run(provider.list_accounts(container_id="deb"))

    assert [a.name for a in kev_accounts] == ["Kev Brokerage ...111"]
    assert [a.name for a in deb_accounts] == ["Deb Brokerage ...222"]


def test_morgan_stanley_live_mode_keeps_value_for_non_ticker_assets(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FINAGENT_DB_PATH", str(tmp_path / "financial_agent_test.sqlite3"))
    monkeypatch.setenv("FINAGENT_MORGAN_STANLEY_CSV_PRICE_MODE", "live")

    from financial_agent.morgan_stanley_csv.importer import import_positions_csv
    from financial_agent.providers.morgan_stanley_csv_provider import MorganStanleyCsvHoldingsProvider

    csv_path = tmp_path / "kev.csv"
    csv_path.write_text(
        """Symbol,Description,Quantity,Price,Market Value,Currency,Account\n"
        "BCQ04,BLUE OWL CREDIT INC CORP,10,,1000,USD,Kev\n"
        "MSBNK,BANK DEPOSIT PROGRAM | MORGAN STANLEY BANK N.A.,, ,500,USD,Kev\n"
        """,
        encoding="utf-8",
    )

    import_positions_csv(db_path=tmp_path / "financial_agent_test.sqlite3", csv_path=csv_path, container_id="kev")

    provider = MorganStanleyCsvHoldingsProvider()
    holdings = asyncio.run(provider.get_holdings(container_id="kev"))
    by_asset = {h.asset: h for h in holdings}

    # Non-public/internal code should keep institution market value in live mode.
    assert "BCQ04" in by_asset
    assert by_asset["BCQ04"].market_value is not None
    assert str(by_asset["BCQ04"].market_value) == "1000"

    # Bank deposit program should be treated as USD cash using market value.
    assert "USD" in by_asset
    assert str(by_asset["USD"].quantity) == "500"
    assert str(by_asset["USD"].market_value) == "500"


def test_morgan_stanley_account_name_override(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FINAGENT_DB_PATH", str(tmp_path / "financial_agent_test.sqlite3"))

    from financial_agent.morgan_stanley_csv.importer import import_positions_csv
    from financial_agent.providers.morgan_stanley_provider import MorganStanleyHoldingsProvider

    csv_path = tmp_path / "kev.csv"
    # No Account column here (mimics certain Morgan exports)
    csv_path.write_text(
        """Symbol,Description,Quantity,Price,Market Value,Currency\n"
        "AAPL,Apple,1,100,100,USD\n"
        """,
        encoding="utf-8",
    )

    import_positions_csv(
        db_path=tmp_path / "financial_agent_test.sqlite3",
        csv_path=csv_path,
        container_id="kev",
        account_name_override="alternatives",
    )

    provider = MorganStanleyHoldingsProvider()
    accounts = asyncio.run(provider.list_accounts(container_id="kev"))
    assert [a.name for a in accounts] == ["alternatives"]


def test_morgan_stanley_account_name_override_when_account_blank(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FINAGENT_DB_PATH", str(tmp_path / "financial_agent_test.sqlite3"))

    from financial_agent.morgan_stanley_csv.importer import import_positions_csv
    from financial_agent.providers.morgan_stanley_provider import MorganStanleyHoldingsProvider

    csv_path = tmp_path / "kev.csv"
    # Account column exists but is blank; override should still apply.
    csv_path.write_text(
        """Symbol,Description,Quantity,Price,Market Value,Currency,Account\n"
        "AAPL,Apple,1,100,100,USD,\n"
        """,
        encoding="utf-8",
    )

    import_positions_csv(
        db_path=tmp_path / "financial_agent_test.sqlite3",
        csv_path=csv_path,
        container_id="kev",
        account_name_override="affiliates",
    )

    provider = MorganStanleyHoldingsProvider()
    accounts = asyncio.run(provider.list_accounts(container_id="kev"))
    assert [a.name for a in accounts] == ["affiliates"]


def test_morgan_stanley_force_account_name_override_overwrites_existing(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FINAGENT_DB_PATH", str(tmp_path / "financial_agent_test.sqlite3"))

    from financial_agent.morgan_stanley_csv.importer import import_positions_csv
    from financial_agent.providers.morgan_stanley_provider import MorganStanleyHoldingsProvider

    csv_path = tmp_path / "stocks.csv"
    csv_path.write_text(
        """Symbol,Description,Quantity,Price,Market Value,Currency,Account Name\n"
        "AAPL,Apple,1,100,100,USD,Select UMA IRA - 1156\n"
        """,
        encoding="utf-8",
    )

    import_positions_csv(
        db_path=tmp_path / "financial_agent_test.sqlite3",
        csv_path=csv_path,
        container_id="kev",
        account_name_override="stocks-options",
        force_account_name_override=True,
    )

    provider = MorganStanleyHoldingsProvider()
    accounts = asyncio.run(provider.list_accounts(container_id="kev"))
    assert [a.name for a in accounts] == ["stocks-options"]


def test_morgan_stanley_multiple_accounts_across_snapshots(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """If account exports are imported in separate refresh runs, the API should still surface both accounts.

    The provider should use the latest snapshot per account_name rather than only the latest snapshot for the container.
    """

    monkeypatch.setenv("FINAGENT_DB_PATH", str(tmp_path / "financial_agent_test.sqlite3"))

    from financial_agent.morgan_stanley_csv.importer import import_positions_csv
    from financial_agent.providers.morgan_stanley_provider import MorganStanleyHoldingsProvider

    affiliates_path = tmp_path / "affiliates.csv"
    stocks_path = tmp_path / "stocks-options.csv"

    # No Account column (mimics certain Morgan exports). We rely on account_name_override.
    affiliates_path.write_text(
        """Symbol,Description,Quantity,Price,Market Value,Currency\n"
        "AAPL,Apple,1,100,100,USD\n"
        """,
        encoding="utf-8",
    )
    stocks_path.write_text(
        """Symbol,Description,Quantity,Price,Market Value,Currency\n"
        "MSFT,Microsoft,2,10,20,USD\n"
        """,
        encoding="utf-8",
    )

    # Import in two separate calls (separate snapshots).
    import_positions_csv(
        db_path=tmp_path / "financial_agent_test.sqlite3",
        csv_path=affiliates_path,
        container_id="kev",
        account_name_override="affiliates",
    )
    import_positions_csv(
        db_path=tmp_path / "financial_agent_test.sqlite3",
        csv_path=stocks_path,
        container_id="kev",
        account_name_override="stocks-options",
    )

    provider = MorganStanleyHoldingsProvider()
    accounts = asyncio.run(provider.list_accounts(container_id="kev"))
    assert [a.name for a in accounts] == ["affiliates", "stocks-options"]

    holdings = asyncio.run(provider.get_holdings(container_id="kev"))
    by_account = {}
    for h in holdings:
        by_account.setdefault(h.account_id, set()).add(h.asset)

    assert "affiliates" in by_account
    assert "stocks-options" in by_account
    assert "AAPL" in by_account["affiliates"]
    assert "MSFT" in by_account["stocks-options"]


def test_morgan_stanley_refresh_infers_account_name_from_filename() -> None:
    from financial_agent.morgan_stanley_refresh import _infer_account_name_from_path

    assert _infer_account_name_from_path(Path("Holdings Ungrouped.xlsx")) == "Holdings Ungrouped"
    assert _infer_account_name_from_path(Path("Holdings ETFs.xlsx")) == "etfs-cefs"
    assert _infer_account_name_from_path(Path("alt_account-2026_01_05.csv")) == "alt account 2026 01 05"
