# Realtime Dashboard — Design Spec

**Date:** 2026-06-18
**Topic:** Fix `index.html` market dashboard — realtime crypto via WebSocket, remove leaked API keys via a FastAPI proxy, and wire the news sidebar to real scraped ForexFactory data.

## Problem

The current `index.html` is a standalone client-side dashboard with several issues:

1. **Leaked API keys** — `CMC_API_KEY` and `TD_API_KEY` are hardcoded in page source. Anyone viewing source steals them.
2. **CMC calls broken** — `pro-api.coinmarketcap.com` rejects browser calls (CORS) and would leak the key anyway.
3. **Rate limits** — REST polling every 5 min against TwelveData (8/min cap) and keyless CoinGecko (throttled). Easy to exhaust.
4. **Fake news** — sidebar uses a hardcoded `NEWS_ITEMS` array and a synthetic weekly schedule (`getWeekSchedule`/`getMonday`), despite the repo having a real ForexFactory scraper writing `news/last_run/*.json`.

## Decisions (from brainstorming)

- **Crypto realtime:** Binance combined WebSocket stream — keyless, no rate limit, ~1 update/sec/symbol.
- **Non-crypto + keyed data:** small **FastAPI** proxy holds keys server-side (TwelveData for forex/metals/stocks/indices/commodities; CMC for tokens with no Binance pair). Fixes the CORS + key-leak problems.
- **News:** FastAPI serves the scraper's real JSON via `/api/news`.
- **Stack:** Python / FastAPI (matches repo; can reuse `ff_calendar_toolkit` `.env` loading).

## Architecture

```
Browser (index.html)
 ├── WebSocket → wss://stream.binance.com   (crypto, realtime, keyless, direct)
 └── REST → FastAPI server (localhost:8000)
              ├── GET /               → serves index.html
              ├── GET /api/td/quote        → proxy TwelveData /quote        (key from env)
              ├── GET /api/td/time_series  → proxy TwelveData /time_series  (key from env)
              ├── GET /api/cmc/quotes      → proxy CMC quotes/latest         (key from env header)
              └── GET /api/news            → latest news/last_run/*.json, filtered
```

## Components

### 1. `server.py` (new, repo root)

FastAPI app. Keys read from environment (`TD_API_KEY`, `CMC_API_KEY`); load `.env` the same way `ff_calendar_toolkit.config` does so a single `.env` serves both. Keys must NOT have defaults that embed real values.

Endpoints:

- `GET /` → return `index.html` (FileResponse). Also serve any static assets from repo root.
- `GET /api/td/quote?symbol=...` → forward to `https://api.twelvedata.com/quote`, inject `apikey` from env. Pass through JSON + upstream status.
- `GET /api/td/time_series?symbol=...&interval=...&outputsize=...` → forward to `/time_series`, inject `apikey`.
- `GET /api/cmc/quotes?symbol=...` → forward to `https://pro-api.coinmarketcap.com/v2/cryptocurrency/quotes/latest` with `X-CMC_PRO_API_KEY` env header. Server-to-server, so no browser CORS.
- `GET /api/news` → see below.

Implementation notes:
- Use `httpx` (async) or reuse the toolkit's `requests.Session` from `_http.py` in a threadpool. Prefer `httpx.AsyncClient` for an async FastAPI app.
- On upstream error, return the upstream status + body (no silent 200). Client already null-guards.
- Bind `127.0.0.1:8000` by default (local-only; keys never exposed to LAN unless user opts in).

### 2. `/api/news` endpoint

- Locate newest file in `news/last_run/` (single month file today; glob + pick latest by name/mtime).
- Each record: `{time:"HH:MM", date:"DD/MM/YYYY", timezone:"Asia/Karachi", currency, impact:"red|orange|gray", event, ...}`.
- Filter: `impact in {"red","orange"}` (high impact). Drop `gray`.
- Build an epoch-ms `scheduledTime` from `date` + `time` interpreted in the record's `timezone` (use `zoneinfo`). Convert to UTC ms.
- Keep events whose `scheduledTime` is in the future OR ended within the last `EVENT_DURATION` window (so "LIVE NOW" still shows). Sort ascending.
- Return clean list: `[{title:event, currency, impact, source:"ForexFactory", cat, scheduledTime}]`. `cat` derived from currency (FX) — keep simple: `"FX"`.
- Empty/missing file → return `[]`.

### 3. `index.html` — crypto WebSocket module

- Asset routing:
  - **Binance WS (has pair):** BTC, ETH, BNB, SOL, XRP, DOGE, ADA, TRX, SHIB, HBAR, LINK, AVAX, SUI, PEPE, XLM.
  - **CMC proxy fallback (no Binance pair):** PI, WLFI, WFLI, XPL, XMONEY, GAS.
  - **Metals (XAUUSD/XAGUSD):** unchanged — stay on existing keyless CoinGecko REST (`pax-gold`/`kinesis-silver`). Out of scope to re-source.
  - **TwelveData proxy:** EURUSD, WTI, SPX, NASDAQ, TSLA, NVDA.
- Open ONE combined stream: `wss://stream.binance.com:9443/stream?streams=btcusdt@ticker/ethusdt@ticker/...` for all has-pair symbols.
- `@ticker` payload fields used: `c` (last price), `P` (24h change %). On message → update `assetState` for that asset and repaint its card.
- **Reconnect:** on `close`/`error`, reconnect with exponential backoff (1s → max 30s). Banner shows a "reconnecting" note while down.

### 4. `index.html` — crypto chart history (real candles)

- On startup, for each has-pair symbol, fetch Binance `GET https://api.binance.com/api/v3/klines?symbol=...&interval=5m&limit=300` (keyless) → seed the candle buffer (`{time, value:close, high, low}`), replacing simulated data.
- Maintain a rolling in-memory buffer per symbol; each WS tick updates the last point's value (and appends a new candle when the 5-min boundary rolls over — simplest: just update last point's value live, periodic re-seed every N minutes for fresh candles).

### 5. `index.html` — REST through proxy

- `tdFetch`/`fetchTD*` → call `/api/td/...` (relative), drop `apikey` from client.
- `fetchCMCQuotes` → call `/api/cmc/quotes?symbol=...`, drop `X-CMC_PRO_API_KEY` header from client.
- Remove `fetchCMCListings` (the `/listings/latest` path) — quotes endpoint covers the fallback tokens; listing-scan is unnecessary and heavy. (If a token isn't found, card falls back to simulation as today.)
- Keep 5-min poll cadence for TD + CMC + metals (CoinGecko) — within free limits at this asset count.
- Remove hardcoded `CMC_API_KEY`, `TD_API_KEY` consts and `TD_BASE`/`pro-api` direct URLs.

### 6. `index.html` — real news sidebar

- Remove `NEWS_ITEMS`, `getWeekSchedule`, `getMonday`, and the synthetic scheduling logic in `getWeekSchedule`.
- `buildNews()` → `fetch('/api/news')`, render returned events with existing scroll/timer/flash markup. Timer logic (`updateNewsTimers`) keyed off `scheduledTime`/`endTime` stays; `endTime = scheduledTime + EVENT_DURATION_MS`.
- Empty list → existing "No events scheduled" placeholder.
- Update the legend "Data:" line to reflect real sources (Binance WS + TwelveData + CoinGecko + ForexFactory).

## Data Flow

- **Startup:** `buildGrid()` → seed crypto charts from Binance klines → open WS → `fetch /api/news` → start REST poll loop (forex/stocks/indices/commodities/CMC-tokens/metals) + WS live.
- **WS tick:** update price/change/markers/chart for that crypto card immediately.
- **REST poll (5 min):** non-crypto cards + CMC-fallback tokens.

## Error Handling

- WS closed → exponential-backoff reconnect; banner notes reconnecting.
- Proxy 4xx/5xx → card keeps last value; existing null-guards prevent crash.
- Missing/empty news file → `/api/news` returns `[]`; sidebar shows placeholder.
- Binance klines seed failure → fall back to existing simulated candles for that symbol (chart still renders).

## Testing

- `server.py`: unit-test `/api/news` parsing (date+time+tz → ms, impact filter, future filter, empty file) against a fixture JSON. Smoke-test proxy endpoints with a mocked upstream (assert key injected, status passthrough).
- `index.html`: manual verification — run `python server.py`, open `localhost:8000`, confirm: crypto prices tick live, charts show real history, no keys in page source, forex/stock cards populate via proxy, news sidebar shows real high-impact events with correct times. Reconnect: kill network briefly, confirm WS recovers.

## What Gets Removed

- Hardcoded `CMC_API_KEY`, `TD_API_KEY` constants.
- `NEWS_ITEMS` array, `getWeekSchedule()`, `getMonday()`.
- Direct vendor URLs + auth in client (`TD_BASE` direct, `pro-api.coinmarketcap.com` direct, `fetchCMCListings`).

## Trade-offs

- **No longer pure double-click HTML** — requires a running `python server.py`. This is the only way to hide keys and fix CMC CORS (both user-chosen goals).
- **Forex/stocks/metals stay REST** — no free WebSocket exists for them; low asset count keeps within free limits.
- **Existing leaked keys must be rotated** — they are already public in git/source history; user must regenerate TD + CMC keys and put new ones in `.env`.

## Out of Scope

- Re-sourcing metals (XAU/XAG) data — leave on current CoinGecko proxy path.
- Persisting tick history to disk.
- Auth on the proxy (local-only bind is sufficient for now).
