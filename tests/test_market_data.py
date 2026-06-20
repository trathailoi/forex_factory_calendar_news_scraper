"""Tests for the server-side market-data fallback module (TDD)."""
from __future__ import annotations

from ff_calendar_toolkit import market_data


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


class FakeHttp:
    """Records calls and returns canned responses keyed by URL substring.

    routes: list of (substring, _FakeResp or callable(url, params) -> _FakeResp).
    First matching substring wins.
    """

    def __init__(self, routes):
        self.routes = routes
        self.calls = []  # list of (url, params)

    def __call__(self, url, params=None, timeout=None):
        self.calls.append((url, params or {}))
        for sub, resp in self.routes:
            if sub in url:
                if callable(resp):
                    return resp(url, params or {})
                return resp
        raise AssertionError(f"no fake route for url={url}")


def _td_values_payload():
    # TwelveData returns newest-first values.
    return {
        "values": [
            {"datetime": "2026-06-18 10:10:00", "close": "1.0900", "high": "1.0910", "low": "1.0890"},
            {"datetime": "2026-06-18 10:05:00", "close": "1.0850", "high": "1.0860", "low": "1.0840"},
            {"datetime": "2026-06-18 10:00:00", "close": "1.0800", "high": "1.0810", "low": "1.0790"},
        ],
        "status": "ok",
    }


def test_twelvedata_success(monkeypatch):
    monkeypatch.setattr(market_data, "_td_keys", lambda: ["KEY1"])
    http = FakeHttp([("time_series", _FakeResp(_td_values_payload()))])

    data = market_data.fetch_market("eurusd", "EUR/USD", "forex", http_get=http)

    assert data is not None
    assert data["source"] == "twelvedata"
    assert data["price"] == 1.0900  # newest close
    # change24h = (1.0900 - 1.0800) / 1.0800 * 100
    assert abs(data["change24h"] - ((1.0900 - 1.0800) / 1.0800 * 100)) < 1e-9
    # candles oldest-first
    assert len(data["candles"]) == 3
    assert data["candles"][0]["value"] == 1.0800
    assert data["candles"][-1]["value"] == 1.0900
    assert data["candles"][0]["high"] == 1.0810
    assert data["candles"][0]["low"] == 1.0790
    assert isinstance(data["candles"][0]["time"], int)
    assert data["candles"][0]["time"] < data["candles"][-1]["time"]


def test_twelvedata_rotates_keys_on_429(monkeypatch):
    monkeypatch.setenv("TD_API_KEYS", "k1,k2")
    monkeypatch.delenv("TD_API_KEY", raising=False)

    def td_resp(url, params):
        if params.get("apikey") == "k1":
            return _FakeResp({"code": 429, "message": "You have run out of API credits"})
        return _FakeResp(_td_values_payload())

    http = FakeHttp([("time_series", td_resp)])
    data = market_data.fetch_market("eurusd", "EUR/USD", "forex", http_get=http)

    assert data is not None
    assert data["source"] == "twelvedata"
    keys_tried = [p.get("apikey") for (u, p) in http.calls if "time_series" in u]
    assert "k1" in keys_tried
    assert "k2" in keys_tried


def test_falls_back_to_frankfurter_for_forex(monkeypatch):
    monkeypatch.setattr(market_data, "_td_keys", lambda: ["k1"])

    routes = [
        ("time_series", _FakeResp({"code": 429, "message": "out of credits"})),
        ("frankfurter", _FakeResp({"rates": {"USD": 1.0855}})),
    ]
    http = FakeHttp(routes)
    data = market_data.fetch_market("eurusd", "EUR/USD", "forex", http_get=http)

    assert data is not None
    assert data["source"] == "frankfurter"
    assert data["price"] == 1.0855
    assert data["change24h"] == 0.0
    assert data["candles"] == []


def test_falls_back_to_finnhub_for_stock(monkeypatch):
    monkeypatch.setattr(market_data, "_td_keys", lambda: ["k1"])
    monkeypatch.setenv("FINNHUB_API_KEY", "FH")

    routes = [
        ("time_series", _FakeResp({"code": 429, "message": "limit reached"})),
        ("finnhub", _FakeResp({"c": 192.5, "pc": 190.0, "h": 193, "l": 189})),
    ]
    http = FakeHttp(routes)
    data = market_data.fetch_market("tsla", "TSLA", "stock", http_get=http)

    assert data is not None
    assert data["source"] == "finnhub"
    assert data["price"] == 192.5
    assert abs(data["change24h"] - ((192.5 - 190.0) / 190.0 * 100)) < 1e-9
    assert data["candles"] == []


def test_index_uses_etf_proxy(monkeypatch):
    monkeypatch.setattr(market_data, "_td_keys", lambda: ["k1"])
    monkeypatch.setenv("FINNHUB_API_KEY", "FH")

    routes = [
        ("time_series", _FakeResp({"status": "error", "message": "API credit limit"})),
        ("finnhub", _FakeResp({"c": 500.0, "pc": 495.0, "h": 501, "l": 494})),
    ]
    http = FakeHttp(routes)
    data = market_data.fetch_market("spx", "SPX", "index", http_get=http)

    assert data is not None
    assert data["source"] == "finnhub"
    finnhub_calls = [p for (u, p) in http.calls if "finnhub" in u]
    assert finnhub_calls
    assert finnhub_calls[0].get("symbol") == "SPY"


def test_returns_none_when_all_fail(monkeypatch):
    monkeypatch.setattr(market_data, "_td_keys", lambda: ["k1"])
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)

    routes = [("time_series", _FakeResp({"code": 429, "message": "no credits"}))]
    http = FakeHttp(routes)
    data = market_data.fetch_market("tsla", "TSLA", "stock", http_get=http)

    assert data is None
