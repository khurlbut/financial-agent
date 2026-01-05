from __future__ import annotations

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


def test_yahoo_batch_fetch_parses_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    from financial_agent import pricing_providers

    chart_payload = (
        '{"chart":{"result":[{"meta":{"symbol":"AAPL","currency":"USD","regularMarketPrice":267.27}}],"error":null}}'
    )

    def fake_urlopen(req, timeout=15):
        # Ensure we are passing a Request object with headers.
        assert hasattr(req, "headers")
        return _FakeResp(chart_payload)

    monkeypatch.setattr(pricing_providers.urllib.request, "urlopen", fake_urlopen)

    p = pricing_providers._fetch_yahoo_chart_price_usd("AAPL")
    assert p is not None
    assert str(p) == "267.27"
