# Card-Detail View — Design Spec

**Date:** 2026-06-20
**Topic:** Click a dashboard card to open a full-screen detail "artifact" view for that asset, with a multi-timeframe chart, key levels, a breakout-probability gauge with derived indicators, and 12-month/52-week ranges — all backed by real data where the source allows, with honest N/A for what free data cannot supply.

## Goal

Let the user click any asset card on the dashboard grid and open a rich single-asset detail view (modal overlay) showing price action, key levels, derived indicators, and ranges. No fabricated numbers: every metric is either real, computed from real candles, or visibly marked N/A.

## Decisions (from brainstorming)

- **Data realism:** real where possible, derive the rest from real candles, never fake. (Continues the SIM-honesty work already shipped.)
- **View mechanism:** modal overlay inside `index.html` — reuses the live in-memory `assetState` and WebSocket; close returns to grid. No second page, no full refetch.
- **Timeframes:** crypto gets real M15/H1/H4(/1D) from Binance klines; non-crypto shows its native interval and greys timeframes it has no data for.
- **Panels v1:** Price chart + TF tabs; Key Levels (R/S/current + bias); Breakout gauge + indicators; Ranges (12mo/52wk). No equity header / nav tabs / Market Pulse sidebar (deferred — they imply a bigger app shell and a portfolio concept that does not exist).
- **Indicator math:** extracted to a served static file `static/detail_indicators.js` so it is node-unit-testable.
- **Breakout formula:** compression + momentum blend (documented heuristic).

## Data Feasibility (verified)

- **Binance klines** (`GET /api/v3/klines`): max 1000 candles/request; intervals include 15m, 1h, 4h, 1d, 1w. So crypto can fetch real M15/H1/H4 charts AND `1d&limit=365` for 12-month high/low + 52-week range.
- **TD-assets** (forex/stock/index/commodity via `/api/market`): 5-min candles when TwelveData succeeds; price-only (rolling buffer) on Frankfurter/Finnhub fallback. No long history → 12mo/52wk = N/A; TF tabs other than the native interval = greyed.
- **Metals (CoinGecko) / CMC tokens:** price + limited history via existing paths → ranges N/A, single timeframe.

## Architecture

Everything client-side in `index.html`, plus one new served static JS file for testable indicator math. No new server endpoints (the existing `/api/market`, Binance klines direct from browser, and live WS already provide the data).

```
Card click → openDetail(assetId)
  → show modal shell, populate header from assetState[assetId] (already live)
  → load chart for default TF:
       crypto    → fetchDetailKlines(asset, interval) [Binance, cached per (id, interval)]
       TD-asset  → reuse tdBuffers/candleBuffers (native interval only)
  → compute panels via detail_indicators.js (ATR, compression, breakout prob, R/S, ranges)
  → live WS ticks keep updating the open crypto detail
Close (X / Esc / backdrop click) → hide modal, clear detail-only state
```

### Capability flags (drive greying / N/A)
- `hasMultiTF(asset)` → true only for crypto (Binance pairs).
- `hasLongHistory(asset)` → true only for crypto (1d klines).
Non-crypto: TF tabs except native are disabled; Ranges panel shows "Needs price history" placeholder.

## Components (all in `index.html` unless noted)

### 1. Detail modal shell
- Full-screen overlay `<div id="detailModal">`, hidden by default (`display:none`).
- Header: symbol name, live price, 24h change (coloured), close button (×).
- Body: responsive grid of the four panels below.
- Reuses existing dark aesthetic but at the richer "v2" density of the mockup (larger chart, ring gauge).

### 2. Panel — Price chart + TF tabs
- Large SVG line chart; reuse `buildSmoothSVGPath` / `buildDynamicPricePath`.
- Tabs (crypto): M15 / H1 / H4 / 1D. Active TF highlighted.
- Crypto: on tab click, `fetchDetailKlines(asset, interval)` (cached per asset+interval), redraw.
- Non-crypto: the chart shows the asset's native ~5-min buffer under a single enabled "5M" tab; M15/H1/H4/1D tabs are rendered greyed/disabled (CSS `.tf-tab.disabled`) since free data cannot supply them. "Native interval" = the 5-min series TwelveData returns, or the rolling 5-min-spaced fallback buffer.
- Live crypto WS tick updates the last point of the currently-shown TF if it is the finest (M15/5min) view.

### 3. Panel — Key Levels
- Resistance = max(high) over the chart window; Support = min(low); Current = live price.
- "Inside range" flag when support < price < resistance.
- Long-term bias = reuse `getBias` (D1 + H1 EMA alignment) → Bullish / Bearish / Neutral, coloured.
- Show % distance of current to R and to S (already computable as in `loadAsset`'s `distSup`/`distRes`).

### 4. Panel — Breakout gauge + indicators
- Circular ring gauge showing breakout probability 0–100% with a Bullish/Bearish/Neutral label.
- Indicators row: Compression %, ATR(14).
- **Buy/Sell liquidity is omitted** — no free data source; not faked.
- All values from `detail_indicators.js` (see below).

### 5. Panel — Ranges
- Crypto: 12-month high/low (from `1d` klines, limit 365) and a 52-week range bar with a marker at the current price's percentile in the range.
- Non-crypto: placeholder "Needs price history" (greyed), no fabricated numbers.

## New file — `static/detail_indicators.js`

Pure functions, no DOM, no network — importable in the browser (`<script src>`) and in node tests. Served by FastAPI as a static asset.

```js
// Each takes plain candle arrays [{time, value, high, low}] and returns numbers.
function computeATR(candles, period = 14)            // average true range (absolute)
function computeCompression(candles, atr)             // 0..100: how tight recent range is vs ATR (squeeze)
function computeBreakoutProbability(candles, price, ema, rsi)
  // 0..100 blend: weighted(compression, |rsi-50| momentum, proximity to nearest R/S)
function breakoutDirection(price, ema, candles)       // 'bullish' | 'bearish' | 'neutral'
function rangePercentile(price, low, high)            // 0..100 position of price within [low,high]
```

### Breakout probability formula (documented heuristic)
`prob = clamp( 0.45*compression + 0.35*momentumScore + 0.20*proximityScore , 0, 100 )`
- `compression` (0–100): tighter recent range relative to ATR → higher (Bollinger-squeeze style: `100 * (1 - recentRange / (atr * k))`, clamped).
- `momentumScore` (0–100): `2 * |rsi - 50|` — distance from neutral RSI (strong momentum either way raises breakout odds).
- `proximityScore` (0–100): how close price sits to the nearest of R/S (closer to a level → higher breakout odds): `100 * (1 - minDistPct / threshold)`, clamped.
- Direction from `breakoutDirection` (price vs EMA + recent slope), used only to colour/label the gauge, not the magnitude.

The `static/` directory is served by mounting it in `server.py` (e.g. `app.mount("/static", StaticFiles(directory=ROOT/"static"))`), and `index.html` loads `/static/detail_indicators.js`.

## Server change

- Mount static files: `from fastapi.staticfiles import StaticFiles; app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")`. Create `static/` with `detail_indicators.js`. No other server change; no new API endpoints.

## Data flow detail

- **Open:** `openDetail(id)` reads `assetState[id]` for instant header/levels, then async-loads chart + ranges.
- **Crypto klines cache:** `Map` keyed `"${id}:${interval}"`; TTL ~60s so reopening is instant but refreshes.
- **Live update:** existing WS `onmessage` calls a `if (detailOpen && detailAssetId===id) updateDetailLive(...)` hook to refresh header + finest-TF last point + recompute gauge.
- **Close:** hide modal, set `detailOpen=false`, leave caches (cheap).

## Error handling

- Klines fetch fail → chart shows last-known buffer or "Chart data unavailable"; other panels still render from `assetState`.
- Open before any live tick (cold asset) → header shows "—"/spinner until first data; panels fill when data arrives.
- Esc key, backdrop click, and × all close. Body scroll locked while open.
- Indicator functions guard against short/empty candle arrays (return safe defaults, never throw).

## Testing

- **`tests/detail_indicators.test.mjs`** (node): unit-test each pure function in `static/detail_indicators.js` — ATR on a known series, compression bounds 0–100, breakout probability bounds + monotonicity (tighter range ⇒ higher), rangePercentile endpoints (low→0, high→100, mid→50), empty/short-array safety. Run via `node --test`.
- **JS parse check** for `index.html` (as in prior work).
- **Manual:** click each asset type (crypto, forex, stock, index, metal, CMC token) → verify panels populate or grey correctly; TF tabs switch (crypto) / greyed (others); live crypto ticks update open detail; Esc/backdrop/× close.

## What gets added

- `static/detail_indicators.js` (new, pure indicator math).
- `tests/detail_indicators.test.mjs` (new, node tests).
- `index.html`: detail modal markup + CSS, `openDetail`/`closeDetail`, card click handlers, `fetchDetailKlines` + cache, panel renderers, live-update hook, `<script src="/static/detail_indicators.js">`.
- `server.py`: mount `/static`.

## Out of Scope (deferred)

- Equity header, Scanner/Journal/Alerts nav, Market Pulse sidebar (need an app shell + portfolio concept).
- Buy/Sell liquidity metric (no free data source).
- Multi-timeframe + long history for non-crypto assets (free data does not provide it).
- Persisting any detail state.
