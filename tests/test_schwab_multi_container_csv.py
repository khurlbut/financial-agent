from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest


def _write_minimal_schwab_csv(path, *, account_name: str, symbol: str):
    lines = [
        f"Positions for account {account_name} as of 01/04/2026\n",
        "\n",
        "Symbol,Description,Quantity,Price,Market Value,\n",
        f"{symbol},Test,1,10,10,\n",
    ]
    path.write_text("".join(lines), encoding="utf-8")


def test_schwab_csv_supports_multiple_containers(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FINAGENT_DB_PATH", str(tmp_path / "financial_agent_test.sqlite3"))

    from financial_agent.schwab_csv.importer import import_positions_csv
    from financial_agent.providers.schwab_provider import SchwabHoldingsProvider

    kev_csv = tmp_path / "kev.csv"
    deb_csv = tmp_path / "deb.csv"
    legacy_csv = tmp_path / "legacy.csv"
    _write_minimal_schwab_csv(kev_csv, account_name="Kev IRA ...111", symbol="AAPL")
    _write_minimal_schwab_csv(deb_csv, account_name="Deb IRA ...222", symbol="MSFT")
    _write_minimal_schwab_csv(legacy_csv, account_name="Legacy ...333", symbol="GOOG")

    import_positions_csv(db_path=tmp_path / "financial_agent_test.sqlite3", csv_path=kev_csv, container_id="kev")
    import_positions_csv(db_path=tmp_path / "financial_agent_test.sqlite3", csv_path=deb_csv, container_id="deb")
    # Simulate an older import that used the default container id.
    import_positions_csv(db_path=tmp_path / "financial_agent_test.sqlite3", csv_path=legacy_csv, container_id="schwab")

    provider = SchwabHoldingsProvider()

    containers = asyncio.run(provider.list_containers())
    cids = sorted(c.container_id for c in containers)
    assert cids == ["deb", "kev"]

    kev_accounts = asyncio.run(provider.list_accounts(container_id="kev"))
    deb_accounts = asyncio.run(provider.list_accounts(container_id="deb"))

    assert [a.name for a in kev_accounts] == ["Kev IRA ...111"]
    assert [a.name for a in deb_accounts] == ["Deb IRA ...222"]
