from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest


@pytest.fixture
def finagent_db_path(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "finagent-test.sqlite3"
    monkeypatch.setenv("FINAGENT_DB_PATH", str(db_path))
    return db_path


def test_schwab_csv_live_mode_omits_csv_price_and_market_value(finagent_db_path, monkeypatch: pytest.MonkeyPatch):
    from financial_agent.schwab_csv import db as schwab_db
    from financial_agent.providers.schwab_csv_provider import SchwabCsvHoldingsProvider

    monkeypatch.setenv("FINAGENT_SCHWAB_CSV_PRICE_MODE", "live")

    conn = schwab_db.connect(finagent_db_path)
    try:
        snapshot_id = schwab_db.insert_snapshot(
            conn,
            container_id="schwab",
            as_of=datetime(2026, 1, 4, tzinfo=timezone.utc),
            csv_path=finagent_db_path.parent / "dummy.csv",
        )
        conn.execute(
            """
            INSERT INTO schwab_csv_positions (
              snapshot_id, account_name, symbol, quantity, price, market_value, currency
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (snapshot_id, "Test Account", "AAPL", "10", "200", "2000", "USD"),
        )
        conn.execute(
            """
            INSERT INTO schwab_csv_positions (
              snapshot_id, account_name, symbol, quantity, price, market_value, currency
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (snapshot_id, "Test Account", "USD", "123.45", "1", "123.45", "USD"),
        )
        conn.commit()
    finally:
        conn.close()

    provider = SchwabCsvHoldingsProvider()
    holdings = asyncio.run(provider.get_holdings(container_id="schwab"))

    by_asset = {h.asset: h for h in holdings}

    assert by_asset["AAPL"].price is None
    assert by_asset["AAPL"].market_value is None

    # Cash can remain valued as before (not required for the live-pricing behavior,
    # but it should not be forced into missing-prices state).
    assert by_asset["USD"].price is not None
    assert by_asset["USD"].market_value is not None
