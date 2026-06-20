# Realtime Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `index.html` realtime for crypto via Binance WebSocket, move API keys behind a FastAPI proxy, and feed the news sidebar from the real scraped ForexFactory JSON.

**Architecture:** A new `server.py` (FastAPI) serves `index.html` and exposes `/api/td/*`, `/api/cmc/quotes`, and `/api/news`. Keys live in `.env` (loaded via the existing `ff_calendar_toolkit.runtime.load_env_file`) and never reach the browser. The client opens one Binance combined WebSocket for crypto (keyless, realtime) and polls the proxy every 5 min for forex/stocks/indices/commodities and CMC-fallback tokens.

**Tech Stack:** Python 3, FastAPI 0.115 + uvicorn (already in `requirements.txt`), `requests` for upstream calls, `pytz` for timezone parsing (already present), `pytest` + FastAPI `TestClient` for tests. Vanilla JS in `index.html` (no framework).

---

## File Structure

- `server.py` (new, repo root) — FastAPI app: static index, TD proxy, CMC proxy, news endpoint. One file; small and cohesive.
- `ff_calendar_toolkit/news_api.py` (new) — pure function `load_dashboard_news(news_dir, now_ms) -> list[dict]`: find latest `last_run/*.json`, filter high-impact, parse date+time+tz → epoch ms, filter future, sort. Kept separate from `server.py` so it is unit-testable without HTTP.
- `tests/test_news_api.py` (new) — unit tests for `load_dashboard_news`.
- `tests/test_server.py` (new) — endpoint tests with mocked upstream.
- `tests/fixtures/dashboard_news.json` (new) — small fixture matching the scraper schema.
- `index.html` (modify) — remove keys + fake news; add Binance WS module, kline seeding, proxy-relative REST, real-news rendering.
- `requirements.txt` (modify) — add `httpx` (FastAPI TestClient dependency).

---

## Task 1: Add httpx test dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add httpx**

Append to `requirements.txt` (FastAPI's `TestClient` requires `httpx`):

```
httpx==0.28.1
```

- [ ] **Step 2: Install**

Run: `pip install -r requirements.txt`
Expected: installs httpx (and confirms fastapi/uvicorn present).

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "build: add httpx for FastAPI test client"
```

---

## Task 2: News parsing function (`load_dashboard_news`)

**Files:**
- Create: `ff_calendar_toolkit/news_api.py`
- Create: `tests/fixtures/dashboard_news.json`
- Test: `tests/test_news_api.py`

- [ ] **Step 1: Create the fixture**

Create `tests/fixtures/dashboard_news.json` (matches real scraper schema — `red`/`orange`/`gray` impacts, `DD/MM/YYYY` date, `HH:MM` time, IANA timezone):

```json
[
  {"time": "15:01", "timezone": "Asia/Karachi", "currency": "EUR", "impact": "gray", "event": "French Bank Holiday", "day": "Mon", "date": "25/05/2026"},
  {"time": "12:30", "timezone": "Asia/Karachi", "currency": "USD", "impact": "red", "event": "Federal Funds Rate", "day": "Wed", "date": "27/05/2026"},
  {"time": "09:00", "timezone": "Asia/Karachi", "currency": "GBP", "impact": "orange", "event": "GDP q/q", "day": "Thu", "date": "28/05/2026"},
  {"time": "08:00", "timezone": "Asia/Karachi", "currency": "JPY", "impact": "red", "event": "BOJ Policy Rate", "day": "Mon", "date": "18/05/2026"}
]
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_news_api.py`:

```python
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from ff_calendar_toolkit.news_api import load_dashboard_news

FIXTURE = Path(__file__).parent / "fixtures" / "dashboard_news.json"


def _make_news_dir(tmp_path):
    last_run = tmp_path / "last_run"
    last_run.mkdir()
    (last_run / "2026-05.json").write_text(FIXTURE.read_text(), encoding="utf-8")
    return tmp_path


def _ms(dt_str, tz):
    dt = datetime.strptime(dt_str, "%d/%m/%Y %H:%M").replace(tzinfo=ZoneInfo(tz))
    return int(dt.timestamp() * 1000)


def test_filters_out_gray_impact(tmp_path):
    news_dir = _make_news_dir(tmp_path)
    now_ms = _ms("20/05/2026 00:00", "Asia/Karachi")  # before Wed/Thu events
    events = load_dashboard_news(news_dir, now_ms)
    titles = [e["title"] for e in events]
    assert "French Bank Holiday" not in titles  # gray dropped
    assert "Federal Funds Rate" in titles        # red kept
    assert "GDP q/q" in titles                    # orange kept


def test_drops_past_events(tmp_path):
    news_dir = _make_news_dir(tmp_path)
    now_ms = _ms("20/05/2026 00:00", "Asia/Karachi")
    events = load_dashboard_news(news_dir, now_ms)
    titles = [e["title"] for e in events]
    assert "BOJ Policy Rate" not in titles  # 18/05 is in the past


def test_builds_scheduled_time_ms_and_sorts(tmp_path):
    news_dir = _make_news_dir(tmp_path)
    now_ms = _ms("20/05/2026 00:00", "Asia/Karachi")
    events = load_dashboard_news(news_dir, now_ms)
    assert events[0]["title"] == "Federal Funds Rate"  # 27/05 before 28/05
    assert events[0]["scheduledTime"] == _ms("27/05/2026 12:30", "Asia/Karachi")
    assert events[0]["currency"] == "USD"
    assert events[0]["source"] == "ForexFactory"


def test_missing_dir_returns_empty(tmp_path):
    assert load_dashboard_news(tmp_path / "nope", 0) == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_news_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ff_calendar_toolkit.news_api'`

- [ ] **Step 4: Write the implementation**

Create `ff_calendar_toolkit/news_api.py`:

```python
"""Build dashboard-ready news from the scraper's last_run JSON output."""
from __future__ import annotations

import glob
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HIGH_IMPACT = {"red", "orange"}


def _latest_last_run_file(news_dir: Path) -> Path | None:
    last_run = Path(news_dir) / "last_run"
    if not last_run.is_dir():
        return None
    files = glob.glob(str(last_run / "*.json"))
    if not files:
        return None
    return Path(max(files, key=os.path.getmtime))


def _scheduled_ms(date_str: str, time_str: str, tz_name: str) -> int | None:
    try:
        dt = datetime.strptime(f"{date_str} {time_str}", "%d/%m/%Y %H:%M")
        dt = dt.replace(tzinfo=ZoneInfo(tz_name))
        return int(dt.timestamp() * 1000)
    except (ValueError, KeyError):
        return None


def load_dashboard_news(news_dir, now_ms: int) -> list[dict]:
    """Return future high-impact events as dashboard records, sorted ascending.

    Each record: {title, currency, impact, source, cat, scheduledTime}.
    """
    path = _latest_last_run_file(Path(news_dir))
    if path is None:
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []

    out: list[dict] = []
    for rec in raw:
        impact = str(rec.get("impact", "")).lower()
        if impact not in HIGH_IMPACT:
            continue
        sched = _scheduled_ms(rec.get("date", ""), rec.get("time", ""), rec.get("timezone", "UTC"))
        if sched is None or sched < now_ms:
            continue
        out.append({
            "title": rec.get("event", ""),
            "currency": rec.get("currency", ""),
            "impact": impact,
            "source": "ForexFactory",
            "cat": "FX",
            "scheduledTime": sched,
        })
    out.sort(key=lambda e: e["scheduledTime"])
    return out
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_news_api.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add ff_calendar_toolkit/news_api.py tests/test_news_api.py tests/fixtures/dashboard_news.json
git commit -m "feat(news): dashboard news loader from scraped last_run JSON"
```

---

## Task 3: FastAPI server skeleton + static index

**Files:**
- Create: `server.py`
- Test: `tests/test_server.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_server.py`:

```python
from fastapi.testclient import TestClient

import server


def test_root_serves_index_html():
    client = TestClient(server.app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Sigaotes" in resp.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'server'`

- [ ] **Step 3: Write minimal implementation**

Create `server.py`:

```python
"""FastAPI server: serves the dashboard and proxies keyed market/news APIs."""
from __future__ import annotations

import os
from pathlib import Path

import requests
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse

from ff_calendar_toolkit.news_api import load_dashboard_news
from ff_calendar_toolkit.runtime import load_env_file

load_env_file()

ROOT = Path(__file__).parent
TD_API_KEY = os.environ.get("TD_API_KEY", "")
CMC_API_KEY = os.environ.get("CMC_API_KEY", "")
NEWS_DIR = Path(os.environ.get("FF_OUTPUT_DIR", "news"))

TD_BASE = "https://api.twelvedata.com"
CMC_QUOTES_URL = "https://pro-api.coinmarketcap.com/v2/cryptocurrency/quotes/latest"

app = FastAPI()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "index.html", media_type="text/html")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_server.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat(server): FastAPI app serving dashboard index"
```

---

## Task 4: `/api/news` endpoint

**Files:**
- Modify: `server.py`
- Test: `tests/test_server.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_server.py`:

```python
import time


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_server.py -k news -v`
Expected: FAIL — 404 on `/api/news`

- [ ] **Step 3: Add the endpoint**

Append to `server.py`:

```python
@app.get("/api/news")
def api_news() -> JSONResponse:
    now_ms = int(time.time() * 1000)
    return JSONResponse(load_dashboard_news(NEWS_DIR, now_ms))
```

Add `import time` to the imports at the top of `server.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_server.py -k news -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat(server): /api/news endpoint from scraped calendar"
```

---

## Task 5: TwelveData + CMC proxy endpoints

**Files:**
- Modify: `server.py`
- Test: `tests/test_server.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_server.py`:

```python
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
    assert captured["params"]["apikey"] == "SECRET_TD"   # key injected server-side
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_server.py -k "td_quote or cmc" -v`
Expected: FAIL — 404 on the proxy routes

- [ ] **Step 3: Add the proxy endpoints**

Append to `server.py`:

```python
def _passthrough(resp: requests.Response) -> JSONResponse:
    try:
        body = resp.json()
    except ValueError:
        body = {"error": "upstream returned non-JSON"}
    return JSONResponse(body, status_code=resp.status_code)


@app.get("/api/td/quote")
def td_quote(request: Request) -> JSONResponse:
    params = dict(request.query_params)
    params["apikey"] = TD_API_KEY
    resp = requests.get(f"{TD_BASE}/quote", params=params, timeout=15)
    return _passthrough(resp)


@app.get("/api/td/time_series")
def td_time_series(request: Request) -> JSONResponse:
    params = dict(request.query_params)
    params["apikey"] = TD_API_KEY
    resp = requests.get(f"{TD_BASE}/time_series", params=params, timeout=15)
    return _passthrough(resp)


@app.get("/api/cmc/quotes")
def cmc_quotes(request: Request) -> JSONResponse:
    params = dict(request.query_params)
    params.setdefault("convert", "USD")
    headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}
    resp = requests.get(CMC_QUOTES_URL, params=params, headers=headers, timeout=15)
    return _passthrough(resp)
```

Note: the proxy reads the module-level `TD_API_KEY`/`CMC_API_KEY` at call time. Because tests `monkeypatch.setattr(server, "TD_API_KEY", ...)`, reference them via the module global inside the functions (they already do — the functions close over the module namespace, so patching `server.TD_API_KEY` takes effect). To guarantee this, the functions must read `TD_API_KEY` as a global lookup, not capture a local — which the code above does.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_server.py -v`
Expected: PASS (all server tests)

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat(server): TwelveData + CMC proxy endpoints (keys server-side)"
```

---

## Task 6: `uvicorn` entrypoint + `.env` keys + run docs

**Files:**
- Modify: `server.py`
- Modify: `requirements.txt` (no change if already done) — skip
- Create/Modify: `.env.example`

- [ ] **Step 1: Add the run block to `server.py`**

Append to `server.py`:

```python
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
```

- [ ] **Step 2: Document required env keys**

Create `.env.example` (or append these lines if it exists):

```
# Rotate the previously-committed keys before using these.
TD_API_KEY=your_twelvedata_key_here
CMC_API_KEY=your_coinmarketcap_key_here
# Optional: override scraper output dir (defaults to ./news)
# FF_OUTPUT_DIR=news
```

- [ ] **Step 3: Verify server boots**

Run: `python server.py` (Ctrl-C after it prints `Uvicorn running on http://127.0.0.1:8000`)
Expected: starts without error. Then `curl -s localhost:8000/api/news` returns a JSON array (run server in background or a second shell).

- [ ] **Step 4: Commit**

```bash
git add server.py .env.example
git commit -m "feat(server): uvicorn entrypoint + .env.example"
```

---

## Task 7: index.html — remove leaked keys + fake news data

**Files:**
- Modify: `index.html` (lines ~146-147 key consts; ~193-218 `NEWS_ITEMS`; ~241-264 `getMonday`/`getWeekSchedule`)

- [ ] **Step 1: Remove the key constants**

In `index.html`, delete these two lines (top of `<script>`):

```js
const CMC_API_KEY = 'a8be4ab5a9864e47aee6abd0d9704100';
const TD_API_KEY = '5e55149c50a24d19b043ce689216d0dc';
```

Leave `const CG_BASE = 'https://api.coingecko.com/api/v3';` (CoinGecko is keyless, stays direct).

- [ ] **Step 2: Remove the fake news array and scheduler**

Delete the entire `const NEWS_ITEMS = [ ... ];` array (the ~24 hardcoded items).
Delete `function getMonday(d) { ... }` and `function getWeekSchedule() { ... }` (both fully — `buildNews` will be rewritten in Task 10 to fetch from the API).

- [ ] **Step 3: Verify no remaining references**

Run: `grep -nE "CMC_API_KEY|TD_API_KEY|NEWS_ITEMS|getWeekSchedule|getMonday" index.html`
Expected: only references that Task 8–10 will rewrite remain (the old `fetchCMCQuotes` header use of `CMC_API_KEY`, `fetchTD*` use of `TD_API_KEY`, and `buildNews`'s call to `getWeekSchedule`). These are fixed in the next tasks — do NOT leave them broken at commit; this step is just to inventory. **Defer the commit to Task 10** so index.html is never committed in a broken state.

(No commit this task.)

---

## Task 8: index.html — route REST through the proxy

**Files:**
- Modify: `index.html` (`tdFetch`, `fetchCMCQuotes`, remove `fetchCMCListings`, `TD_BASE`)

- [ ] **Step 1: Repoint TwelveData fetch at the proxy**

Replace the `const TD_BASE = 'https://api.twelvedata.com';` line and `tdFetch` with proxy-relative calls. Change `tdFetch`:

```js
async function tdFetch(endpoint) {
  try {
    const r = await fetch(`/api/td${endpoint}`);
    if (!r.ok) return null;
    return await r.json();
  } catch(e) { return null; }
}
```

And change the callers to drop the `&apikey=...` and use the proxy path shape. Replace the four `fetchTD*` functions' URL strings:

```js
async function fetchTDQuote(symbol) {
  return await tdFetch(`/quote?symbol=${encodeURIComponent(symbol)}`);
}
async function fetchTDPrice(symbol) {
  return await tdFetch(`/quote?symbol=${encodeURIComponent(symbol)}`);
}
async function fetchTDTimeSeries(symbol, interval='5min', outputsize=200) {
  return await tdFetch(`/time_series?symbol=${encodeURIComponent(symbol)}&interval=${interval}&outputsize=${outputsize}`);
}
async function fetchTDBatchQuote(symbols) {
  const symStr = Array.isArray(symbols) ? symbols.join(',') : symbols;
  return await tdFetch(`/quote?symbol=${encodeURIComponent(symStr)}`);
}
```

Delete the now-unused `const TD_BASE = ...;` line.

- [ ] **Step 2: Repoint CMC at the proxy and drop the key/header**

Replace `fetchCMCQuotes` body:

```js
async function fetchCMCQuotes() {
  try {
    const symbols = ASSETS.filter(a => a.cmcId && !a.cgId).map(a => a.symbol.replace('USDT','')).join(',');
    if (!symbols) return null;
    const r = await fetch(`/api/cmc/quotes?symbol=${encodeURIComponent(symbols)}`);
    if (!r.ok) return null;
    const j = await r.json();
    const map = {};
    if (j.data) {
      Object.keys(j.data).forEach(sym => {
        const entries = j.data[sym];
        if (entries && entries.length > 0) {
          const d = entries[0];
          map[sym.toLowerCase()] = { price: d.quote.USD.price, change24h: d.quote.USD.percent_change_24h || 0 };
        }
      });
    }
    return map;
  } catch(e) { return null; }
}
```

- [ ] **Step 3: Remove `fetchCMCListings` and its caller**

Delete the entire `async function fetchCMCListings() { ... }`.
In `refreshAll`, remove `fetchCMCListings()` from the `Promise.all`, and remove the `cmcMap` it produced. Update `refreshAll`'s destructure + the `loadAsset` calls accordingly:

```js
async function refreshAll() {
  const cgIds = ASSETS.filter(a => a.cgId).map(a => a.cgId);
  const [cgPrices, cgMarkets, cmcQuotes] = await Promise.all([
    cgIds.length > 0 ? fetchCGPrices(cgIds) : null,
    fetchCGMarkets(250, 1),
    fetchCMCQuotes()
  ]);
  for (let i = 0; i < REST_ASSETS.length; i += 4) {
    const batch = REST_ASSETS.slice(i, i + 4);
    await Promise.all(batch.map(a => loadAsset(a, cgPrices, cgMarkets, null, cmcQuotes)));
    if (i + 4 < REST_ASSETS.length) await new Promise(r => setTimeout(r, 800));
  }
  updateTopBannerSummary();
}
```

Note `REST_ASSETS` is introduced in Task 9 (crypto-with-Binance-pairs are removed from the REST loop). `loadAsset`'s `cmcMap` param is now always `null` — leave the param in the signature for minimal diff; its branch simply never fires.

(No commit this task — index.html still references WS pieces added in Task 9.)

---

## Task 9: index.html — Binance WebSocket for crypto

**Files:**
- Modify: `index.html` (add WS module; split crypto out of REST loop; add kline seeding)

- [ ] **Step 1: Tag which crypto have Binance pairs and build asset partitions**

Near the top of `<script>` after `ASSETS`, add:

```js
// Crypto symbols available on Binance spot (USDT pairs). Others fall back to CMC proxy.
const BINANCE_PAIRS = new Set(['btc','eth','bnb','sol','xrp','doge','ada','trx','shib','hbar','link','avax','sui','pepe','xlm']);
const WS_ASSETS = ASSETS.filter(a => BINANCE_PAIRS.has(a.id));
const REST_ASSETS = ASSETS.filter(a => !BINANCE_PAIRS.has(a.id));
function binanceSymbol(asset) { return asset.symbol.toLowerCase(); } // e.g. 'btcusdt'
```

- [ ] **Step 2: Add a per-asset candle buffer and kline seeding**

Add:

```js
// Rolling candle buffer per WS asset: [{time, value, high, low}]
const candleBuffers = new Map();

async function seedKlines(asset) {
  const sym = asset.symbol.toUpperCase(); // BTCUSDT
  try {
    const r = await fetch(`https://api.binance.com/api/v3/klines?symbol=${sym}&interval=5m&limit=300`);
    if (!r.ok) throw new Error('klines ' + r.status);
    const rows = await r.json(); // [ [openTime, open, high, low, close, ...], ... ]
    const candles = rows.map(k => ({
      time: Math.floor(k[0] / 1000),
      value: parseFloat(k[4]),
      high: parseFloat(k[2]),
      low: parseFloat(k[3])
    })).filter(c => !isNaN(c.value));
    if (candles.length >= 2) candleBuffers.set(asset.id, candles);
  } catch (e) {
    console.warn(`[${asset.id}] kline seed failed, using simulation`, e);
  }
}
```

- [ ] **Step 3: Add a render-from-state path reusing existing card-update code**

`loadAsset` already takes a price + builds candles/EMA/markers/SVG/alerts. Add a lightweight wrapper that feeds it WS data using the seeded buffer instead of fetching:

```js
function renderCryptoFromWS(asset, price, change24h) {
  const buf = candleBuffers.get(asset.id);
  let data;
  if (buf && buf.length >= 2) {
    // update last candle's close to the live price
    data = buf.slice();
    data[data.length - 1] = { ...data[data.length - 1], value: price };
  } else {
    data = generateSimulatedCandles(price, 300);
  }
  loadAssetFromData(asset, data, price, change24h);
}
```

This requires factoring the post-fetch half of `loadAsset` into `loadAssetFromData(asset, data, price, change24h)`. In `loadAsset`, after `price`/`change24h`/`data` are resolved (just before `const ema200Data = calculateEMA(...)`), cut everything from `const ema200Data` through the final `assetState.set(...)` into a new function:

```js
function loadAssetFromData(asset, data, price, change24h) {
  const state = assetState.get(asset.id) || {};
  // ... (the moved block: EMA, support/resistance, bias, DOM updates, SVG, alerts) ...
  assetState.set(asset.id, { ...state, lastPrice: price, lastEma200, support, resistance, d1Bias, h1Bias, change24h });
}
```

Then `loadAsset` ends with `return loadAssetFromData(asset, data, price, change24h);`. (Mechanical extraction — keep the moved code identical.)

- [ ] **Step 4: Open the combined WebSocket with reconnect**

Add:

```js
let ws = null;
let wsBackoff = 1000;

function openCryptoWS() {
  const streams = WS_ASSETS.map(a => `${binanceSymbol(a)}@ticker`).join('/');
  ws = new WebSocket(`wss://stream.binance.com:9443/stream?streams=${streams}`);

  ws.onopen = () => { wsBackoff = 1000; };

  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    const d = msg.data;
    if (!d || !d.s) return;
    const id = d.s.toLowerCase().replace('usdt', '');
    const asset = WS_ASSETS.find(a => a.id === id);
    if (!asset) return;
    const price = parseFloat(d.c);        // last price
    const change24h = parseFloat(d.P);    // 24h change percent
    if (isNaN(price)) return;
    renderCryptoFromWS(asset, price, change24h);
  };

  ws.onclose = () => {
    const txt = document.getElementById('bannerText');
    if (txt) txt.textContent = 'Reconnecting to live feed…';
    setTimeout(openCryptoWS, wsBackoff);
    wsBackoff = Math.min(wsBackoff * 2, 30000);
  };

  ws.onerror = () => { try { ws.close(); } catch (e) {} };
}
```

- [ ] **Step 5: Wire startup**

Replace the bottom startup block:

```js
buildGrid();
buildNews(true);
(async () => {
  await Promise.all(WS_ASSETS.map(seedKlines));  // real chart history
  openCryptoWS();                                 // live crypto
})();
refreshAll();                                     // forex/stocks/metals/CMC tokens
setInterval(refreshAll, 5 * 60 * 1000);
setInterval(updateNewsTimers, 1000);
setInterval(() => buildNews(false), 60000);
```

- [ ] **Step 6: Sanity check (manual, after Task 10 commit)**

Deferred to Task 11 verification.

(No commit this task — buildNews still calls removed `getWeekSchedule` until Task 10.)

---

## Task 10: index.html — real news rendering + commit the client

**Files:**
- Modify: `index.html` (`buildNews`, legend text)

- [ ] **Step 1: Rewrite `buildNews` to fetch the API**

`buildNews` becomes async and pulls from `/api/news`. It caches the last fetched events so the 30s throttle path can re-render without refetching:

```js
let cachedNews = [];

async function buildNews(force) {
  const now = Date.now();
  if (!force && now - lastNewsBuild < 30000) return;
  lastNewsBuild = now;

  try {
    const r = await fetch('/api/news');
    cachedNews = r.ok ? await r.json() : [];
  } catch (e) {
    cachedNews = [];
  }

  const list = document.getElementById('newsList');
  list.innerHTML = '';
  const active = cachedNews
    .map(n => ({ ...n, endTime: n.scheduledTime + EVENT_DURATION_MS }))
    .filter(e => now < e.endTime)
    .sort((a, b) => a.scheduledTime - b.scheduledTime);
  document.getElementById('eventCount').textContent = active.length;

  if (active.length === 0) {
    list.innerHTML = `<div class="news-item" style="justify-content:center;opacity:0.5"><div class="news-body"><div class="news-title" style="color:#64748b">No events scheduled</div><div class="news-meta">High-impact events appear here</div></div></div>`;
    return;
  }

  const display = [...active, ...active];
  display.forEach((n, idx) => {
    const item = document.createElement('div');
    item.className = 'news-item';
    item.id = `news-${idx}`;
    item.dataset.scheduled = n.scheduledTime;
    item.dataset.end = n.endTime;
    const t = new Date(n.scheduledTime);
    const timeStr = t.toLocaleDateString('en-US', {weekday:'short', month:'short', day:'numeric'}) + ' ' + t.toLocaleTimeString('en-US', {hour:'2-digit', minute:'2-digit', hour12:false});
    const impactLabel = n.impact === 'red' ? 'High' : 'Med';
    item.innerHTML = `<div class="news-folder">▶</div><div class="news-body"><div class="news-title"><span class="news-impact">${impactLabel}</span>${n.title} (${n.currency})</div><div class="news-meta">${n.source} · ${n.cat} · ${timeStr}</div><div class="news-timer" id="timer-${idx}"></div></div>`;
    list.appendChild(item);
  });
  list.style.animation = 'none';
  void list.offsetHeight;
  list.style.animation = 'scrollNews 50s linear infinite';
}
```

Note `updateNewsTimers` already reads `dataset.scheduled`/`dataset.end` and re-queries `.news-timer` by element — it keeps working unchanged. Its `if (visible.length === 0) buildNews(true)` call now refetches the API, which is fine.

- [ ] **Step 2: Update the legend data line**

Replace the legend "Data:" line text:

```html
Data: Binance WS (crypto) · Twelve Data · CoinGecko · ForexFactory
```

- [ ] **Step 3: Verify no dangling references**

Run: `grep -nE "CMC_API_KEY|TD_API_KEY|NEWS_ITEMS|getWeekSchedule|getMonday|fetchCMCListings|pro-api.coinmarketcap|api.twelvedata.com" index.html`
Expected: NO matches (all removed/replaced).

- [ ] **Step 4: Commit the full client rewrite**

```bash
git add index.html
git commit -m "feat(dashboard): Binance WS crypto, proxy REST, real news; remove leaked keys"
```

---

## Task 11: End-to-end manual verification

**Files:** none (verification only)

- [ ] **Step 1: Put real (rotated) keys in `.env`**

Ensure `.env` has freshly-rotated `TD_API_KEY` and `CMC_API_KEY` (the old ones are public — rotate at the vendor dashboards first).

- [ ] **Step 2: Run the server**

Run: `python server.py`
Open `http://127.0.0.1:8000` in a browser.

- [ ] **Step 3: Verify each requirement**

- View page source → confirm NO API keys present (`Ctrl-U`, search `apikey`/the old key strings — none).
- Crypto cards (BTC/ETH/SOL…) prices tick live within a few seconds (WS).
- Crypto charts show real historical shape (not flat simulation).
- Forex/stock cards (EURUSD/TSLA/NVDA/SPX) populate within ~5s (proxy REST).
- News sidebar lists real high-impact events with real dates/times, or "No events scheduled".
- DevTools Network: `/api/td/...`, `/api/cmc/quotes`, `/api/news` return 200; one `wss://stream.binance.com` connection open.

- [ ] **Step 4: Verify reconnect**

Toggle network off ~5s then on (or DevTools offline). Banner shows "Reconnecting to live feed…", then crypto resumes ticking.

- [ ] **Step 5: Run the full test suite**

Run: `python -m pytest tests/test_news_api.py tests/test_server.py -v`
Expected: all PASS.

---

## Self-Review Notes

- **Spec coverage:** WS crypto (Task 9), kline history seed (Task 9), proxy TD (Task 5/8), proxy CMC (Task 5/8), `/api/news` real data (Task 2/4/10), key removal (Task 7), fake-news removal (Task 7/10), reconnect (Task 9), metals left on CoinGecko (untouched in `loadAsset`/`REST_ASSETS` — they have `cgId`, no Binance pair, so land in `REST_ASSETS` ✓), drop `fetchCMCListings` (Task 8). All covered.
- **Broken-state avoidance:** index.html is only committed once (Task 10) after Tasks 7–10 land together, so it is never committed half-rewritten.
- **Type/name consistency:** `loadAssetFromData(asset, data, price, change24h)` defined in Task 9 Step 3, called from `renderCryptoFromWS` (Task 9) and `loadAsset` tail (Task 9). `REST_ASSETS`/`WS_ASSETS` defined Task 9 Step 1, used in `refreshAll` (Task 8) — Task 9 runs conceptually before the combined commit, and both land before the single index.html commit, so order within the uncommitted edits is fine. `cachedNews` (Task 10) self-contained.
- **Metals note:** XAUUSD/XAGUSD have `cgId` (`pax-gold`/`kinesis-silver`) and no Binance pair → `REST_ASSETS`, fetched via existing keyless CoinGecko path in `loadAsset`. Unchanged, as spec requires.
