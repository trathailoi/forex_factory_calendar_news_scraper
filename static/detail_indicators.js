/*
 * detail_indicators.js — pure indicator math for the card-detail modal.
 *
 * No DOM, no network, no Date.now. Works as BOTH:
 *   - a browser global (loaded via <script src>): functions land on window/self
 *   - a node require() import: module.exports is the api object (for unit tests)
 *
 * candles = array of { time, value, high, low }   (value = close)
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else Object.assign(root, api);
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  function clamp(x, lo, hi) {
    if (x < lo) return lo;
    if (x > hi) return hi;
    return x;
  }

  // True Range_i = max(high_i-low_i, |high_i-prevClose|, |low_i-prevClose|)
  // ATR = mean of the last `period` TR values (or all if fewer).
  function computeATR(candles, period) {
    if (period === undefined) period = 14;
    if (!Array.isArray(candles) || candles.length < 2) return 0;

    const trs = [];
    for (let i = 1; i < candles.length; i++) {
      const cur = candles[i];
      const prevClose = candles[i - 1].value;
      const tr = Math.max(
        cur.high - cur.low,
        Math.abs(cur.high - prevClose),
        Math.abs(cur.low - prevClose)
      );
      trs.push(tr);
    }
    if (trs.length === 0) return 0;

    const take = Math.min(period, trs.length);
    const slice = trs.slice(trs.length - take);
    const sum = slice.reduce(function (a, b) { return a + b; }, 0);
    return sum / slice.length;
  }

  // 0..100. Over last 14 candles: recentRange = max(high) - min(low).
  // If atr>0: compression = clamp(100*(1 - recentRange/(atr*14)), 0, 100); else 0.
  function computeCompression(candles, atr) {
    if (!Array.isArray(candles) || candles.length === 0) return 0;
    if (!(atr > 0)) return 0;

    const slice = candles.slice(Math.max(0, candles.length - 14));
    let hi = -Infinity;
    let lo = Infinity;
    for (let i = 0; i < slice.length; i++) {
      if (slice[i].high > hi) hi = slice[i].high;
      if (slice[i].low < lo) lo = slice[i].low;
    }
    const recentRange = hi - lo;
    return clamp(100 * (1 - recentRange / (atr * 14)), 0, 100);
  }

  // Consider last 12 candles' value; first=oldest, last=newest of that slice.
  // 'bullish' if price>ema && last>first; 'bearish' if price<ema && last<first;
  // else 'neutral'. Short array -> 'neutral'.
  function breakoutDirection(price, ema, candles) {
    if (!Array.isArray(candles) || candles.length < 2) return 'neutral';
    const slice = candles.slice(Math.max(0, candles.length - 12));
    const first = slice[0].value;
    const last = slice[slice.length - 1].value;
    if (price > ema && last > first) return 'bullish';
    if (price < ema && last < first) return 'bearish';
    return 'neutral';
  }

  // 0..100 position of price within [low,high]; clamp to [0,100].
  // If high===low return 50.
  function rangePercentile(price, low, high) {
    if (high === low) return 50;
    return clamp((100 * (price - low)) / (high - low), 0, 100);
  }

  function computeBreakoutProbability(candles, price, ema, rsi) {
    if (!Array.isArray(candles) || candles.length < 2) return 0;

    const atr = computeATR(candles);
    const compression = computeCompression(candles, atr);
    const momentumScore = clamp(2 * Math.abs(rsi - 50), 0, 100);

    const slice = candles.slice(Math.max(0, candles.length - 60));
    let resistance = -Infinity;
    let support = Infinity;
    for (let i = 0; i < slice.length; i++) {
      if (slice[i].high > resistance) resistance = slice[i].high;
      if (slice[i].low < support) support = slice[i].low;
    }

    const distRes = resistance !== 0 ? Math.abs(price - resistance) / resistance : Infinity;
    const distSup = support !== 0 ? Math.abs(price - support) / support : Infinity;
    const minDistPct = Math.min(distRes, distSup) * 100;
    const proximityScore = clamp(100 * (1 - minDistPct / 3), 0, 100); // threshold 3%

    return clamp(
      0.45 * compression + 0.35 * momentumScore + 0.20 * proximityScore,
      0,
      100
    );
  }

  return {
    computeATR,
    computeCompression,
    breakoutDirection,
    rangePercentile,
    computeBreakoutProbability,
  };
});
