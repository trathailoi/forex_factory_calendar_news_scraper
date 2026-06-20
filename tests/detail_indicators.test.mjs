import { test } from 'node:test';
import assert from 'node:assert';
import { createRequire } from 'module';

const require = createRequire(import.meta.url);
const ind = require('../static/detail_indicators.js');

// Helper to build a candle
function c(value, high, low, time = 0) {
  return { time, value, high, low };
}

test('computeATR on a known hand-computed series', () => {
  // candles: close/high/low
  // i0: close=10  (no TR, used as prevClose)
  // i1: high=12 low=9  prevClose=10 -> TR = max(12-9=3, |12-10|=2, |9-10|=1) = 3
  // i2: high=13 low=11 prevClose=11 -> TR = max(13-11=2, |13-11|=2, |11-11|=0) = 2
  // i3: high=11 low=8  prevClose=12 -> TR = max(11-8=3, |11-12|=1, |8-12|=4) = 4
  const candles = [
    c(10, 11, 9),
    c(11, 12, 9),
    c(12, 13, 11),
    c(9, 11, 8),
  ];
  // TR values: [3, 2, 4]; ATR = mean = 9/3 = 3
  const atr = ind.computeATR(candles);
  assert.ok(Math.abs(atr - 3) < 1e-9, `expected 3, got ${atr}`);
});

test('computeATR respects period (last N TR values)', () => {
  const candles = [
    c(10, 11, 9),
    c(11, 12, 9), // TR=3
    c(12, 13, 11), // TR=2
    c(9, 11, 8), // TR=4
  ];
  // period=2 -> last 2 TR values [2,4] mean = 3
  const atr = ind.computeATR(candles, 2);
  assert.ok(Math.abs(atr - 3) < 1e-9, `expected 3, got ${atr}`);
});

test('computeATR length < 2 returns 0', () => {
  assert.strictEqual(ind.computeATR([]), 0);
  assert.strictEqual(ind.computeATR([c(10, 11, 9)]), 0);
});

test('computeCompression within [0,100]; tight recent range > wide', () => {
  // Compression compares the recent 14-candle range against the ATR-implied
  // range (atr*14). To make a meaningful tight-vs-wide comparison we hold ATR
  // fixed (same atr arg) and vary only the recent range of the candles.
  const atr = 1; // atr*14 = 14 of implied range

  // Tight: recent range = 2  -> compression = 100*(1 - 2/14) high
  const tight = [];
  for (let i = 0; i < 14; i++) tight.push(c(100, 101, 99));

  // Wide: recent range = 12 -> compression = 100*(1 - 12/14) low
  const wide = [];
  for (let i = 0; i < 14; i++) wide.push(c(100, 106, 94));

  const compTight = ind.computeCompression(tight, atr);
  const compWide = ind.computeCompression(wide, atr);

  for (const v of [compTight, compWide]) {
    assert.ok(v >= 0 && v <= 100, `compression out of range: ${v}`);
  }
  assert.ok(compTight > compWide, `tight (${compTight}) should be > wide (${compWide})`);
});

test('computeCompression with atr=0 returns 0', () => {
  const candles = [c(100, 101, 99), c(100, 101, 99)];
  assert.strictEqual(ind.computeCompression(candles, 0), 0);
});

test('computeBreakoutProbability within [0,100]; tighter => higher prob', () => {
  // Hold rsi/price/ema constant; only the candle range changes.
  const rsi = 70;
  const price = 100;
  const ema = 99;

  const tight = [];
  for (let i = 0; i < 60; i++) tight.push(c(100, 100.2, 99.8));
  const wide = [];
  for (let i = 0; i < 60; i++) wide.push(c(100, 110, 90));

  const pTight = ind.computeBreakoutProbability(tight, price, ema, rsi);
  const pWide = ind.computeBreakoutProbability(wide, price, ema, rsi);

  for (const v of [pTight, pWide]) {
    assert.ok(v >= 0 && v <= 100, `prob out of range: ${v}`);
  }
  assert.ok(pTight > pWide, `tight (${pTight}) should be > wide (${pWide})`);
});

test('computeBreakoutProbability length < 2 returns 0', () => {
  assert.strictEqual(ind.computeBreakoutProbability([], 100, 99, 70), 0);
  assert.strictEqual(ind.computeBreakoutProbability([c(100, 101, 99)], 100, 99, 70), 0);
});

test('rangePercentile basic cases', () => {
  assert.strictEqual(ind.rangePercentile(50, 0, 100), 50);
  assert.strictEqual(ind.rangePercentile(0, 0, 100), 0);
  assert.strictEqual(ind.rangePercentile(100, 0, 100), 100);
  assert.strictEqual(ind.rangePercentile(42, 5, 5), 50); // high===low
});

test('rangePercentile clamps out-of-range', () => {
  assert.strictEqual(ind.rangePercentile(-10, 0, 100), 0);
  assert.strictEqual(ind.rangePercentile(200, 0, 100), 100);
});

test('breakoutDirection bull/bear/neutral fixtures', () => {
  // Bullish: rising values, price>ema
  const bull = [];
  for (let i = 0; i < 12; i++) bull.push(c(90 + i, 90 + i, 88 + i));
  assert.strictEqual(ind.breakoutDirection(105, 100, bull), 'bullish');

  // Bearish: falling values, price<ema
  const bear = [];
  for (let i = 0; i < 12; i++) bear.push(c(110 - i, 112 - i, 108 - i));
  assert.strictEqual(ind.breakoutDirection(95, 100, bear), 'bearish');

  // Mixed/neutral: price>ema but values falling -> neutral
  assert.strictEqual(ind.breakoutDirection(105, 100, bear), 'neutral');
});

test('breakoutDirection short array -> neutral', () => {
  assert.strictEqual(ind.breakoutDirection(105, 100, [c(100, 101, 99)]), 'neutral');
  assert.strictEqual(ind.breakoutDirection(105, 100, []), 'neutral');
});

test('all functions never throw on empty / single-element arrays', () => {
  const empty = [];
  const single = [c(100, 101, 99)];

  for (const arr of [empty, single]) {
    assert.doesNotThrow(() => ind.computeATR(arr));
    assert.doesNotThrow(() => ind.computeCompression(arr, ind.computeATR(arr)));
    assert.doesNotThrow(() => ind.breakoutDirection(100, 99, arr));
    assert.doesNotThrow(() => ind.computeBreakoutProbability(arr, 100, 99, 70));
  }
  assert.doesNotThrow(() => ind.rangePercentile(50, 0, 100));
});
