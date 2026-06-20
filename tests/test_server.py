import time

from fastapi.testclient import TestClient

import server


def test_root_serves_index_html():
    client = TestClient(server.app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Sigaotes" in resp.text


def test_api_news_returns_list():
    client = TestClient(server.app)
    resp = client.get("/api/news")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_api_news_records_have_expected_shape(monkeypatch):
    sample = [{
        "title": "Federal Funds Rate", "currency": "USD", "impact": "red",
        "source": "ForexFactory", "cat": "FX",
        "scheduledTime": int(time.time() * 1000) + 3_600_000,
    }]
    monkeypatch.setattr(server, "load_dashboard_news", lambda *a, **k: sample)
    client = TestClient(server.app)
    resp = client.get("/api/news")
    assert resp.json() == sample


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


def test_td_quote_injects_key(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResp({"close": "1.0850"})

    monkeypatch.setattr(server, "TD_API_KEY", "SECRET_TD")
    monkeypatch.setattr(server.requests, "get", fake_get)
    client = TestClient(server.app)
    resp = client.get("/api/td/quote?symbol=EUR/USD")
    assert resp.status_code == 200
    assert resp.json() == {"close": "1.0850"}
    assert captured["params"]["apikey"] == "SECRET_TD"
    assert captured["params"]["symbol"] == "EUR/USD"
    assert captured["url"].endswith("/quote")


def test_cmc_quotes_injects_header(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["headers"] = headers
        captured["params"] = params
        return _FakeResp({"data": {}})

    monkeypatch.setattr(server, "CMC_API_KEY", "SECRET_CMC")
    monkeypatch.setattr(server.requests, "get", fake_get)
    client = TestClient(server.app)
    resp = client.get("/api/cmc/quotes?symbol=PI,GAS")
    assert resp.status_code == 200
    assert captured["headers"]["X-CMC_PRO_API_KEY"] == "SECRET_CMC"
    assert captured["params"]["symbol"] == "PI,GAS"
    assert captured["params"]["convert"] == "USD"


def test_static_serves_detail_indicators():
    client = TestClient(server.app)
    resp = client.get("/static/detail_indicators.js")
    assert resp.status_code == 200
    assert "computeBreakoutProbability" in resp.text
