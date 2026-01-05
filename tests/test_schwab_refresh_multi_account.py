from __future__ import annotations

import asyncio
import importlib
import sys

import pytest


def _write_minimal_schwab_csv(path, *, account_name: str, rows: list[tuple[str, str, str, str, str]]):
    # Schwab exporter includes a preamble and a blank line before the header.
    # Header must start with "Symbol" for our importer.
    lines = [
        f"Positions for account {account_name} as of 01/04/2026\n",
        "\n",
        "Symbol,Description,Quantity,Price,Market Value,\n",
    ]
    for sym, desc, qty, price, mv in rows:
        lines.append(f"{sym},{desc},{qty},{price},{mv},\n")
    path.write_text("".join(lines), encoding="utf-8")


def test_schwab_refresh_imports_multiple_csvs_into_one_snapshot(tmp_path, monkeypatch: pytest.MonkeyPatch):
    # Isolate DB.
    monkeypatch.setenv("FINAGENT_DB_PATH", str(tmp_path / "financial_agent_test.sqlite3"))

    csv1 = tmp_path / "acct1.csv"
    csv2 = tmp_path / "acct2.csv"

    _write_minimal_schwab_csv(
        csv1,
        account_name="Acct One ...111",
        rows=[("AAPL", "Apple", "10", "200", "2000")],
    )
    _write_minimal_schwab_csv(
        csv2,
        account_name="Acct Two ...222",
        rows=[("MSFT", "Microsoft", "5", "300", "1500")],
    )

    from financial_agent import schwab_refresh

    importlib.reload(schwab_refresh)

    argv = [
        "schwab_refresh",
        "--csv",
        str(csv1),
        "--csv",
        str(csv2),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    schwab_refresh.main()

    # Latest snapshot should include both accounts.
    from financial_agent.providers.schwab_csv_provider import SchwabCsvHoldingsProvider

    provider = SchwabCsvHoldingsProvider()
    accounts = asyncio.run(provider.list_accounts(container_id="schwab"))

    names = sorted(a.name for a in accounts)
    assert names == ["Acct One ...111", "Acct Two ...222"]
