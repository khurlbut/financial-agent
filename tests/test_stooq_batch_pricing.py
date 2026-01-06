from __future__ import annotations

import io

import pytest


class _FakeResp:
    def __init__(self, data: str) -> None:
        self._data = data.encode("utf-8")

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_stooq_batch_fetch_parses_multiple_symbols(monkeypatch: pytest.MonkeyPatch) -> None:
    # Import here so we can monkeypatch urlopen in the module namespace.
    from financial_agent import pricing_providers

    csv_body = (
        "Symbol,Date,Time,Open,High,Low,Close,Volume\n"
        "aa.us,2026-01-05,00:00:00,0,0,0,61.44,0\n"
        "aapl.us,2026-01-05,00:00:00,0,0,0,267.27,0\n"
        "msft.us,2026-01-05,00:00:00,0,0,0,472.92,0\n"
    )

    def fake_urlopen(url, timeout=15):
        return _FakeResp(csv_body)

    monkeypatch.setattr(pricing_providers.urllib.request, "urlopen", fake_urlopen)

    out = pricing_providers._fetch_stooq_last_close_usd_batch(["AA", "AAPL", "MSFT"], True)
    assert str(out["AA"]) == "61.44"
    assert str(out["AAPL"]) == "267.27"
    assert str(out["MSFT"]) == "472.92"
