import { test } from 'node:test';
import assert from 'node:assert/strict';
import { anchorLogicalRange, chartTime, exactPlotScale, latestLogicalRange, pixelAnchoredRange, priceSeriesData, seriesUpdate } from './price-series.ts';
import type { ExactPlotScale, PriceSeriesData } from './price-series.ts';
import type { PriceCandle } from './candle-model.ts';

const minute = 60_000_000_000n;
const origin = 1_704_067_200_000_000_000n;
const colors = { up: '#00aa00', down: '#aa0000' };
function bar(index: number, overrides: Partial<PriceCandle> = {}): PriceCandle {
  return { start_ns: (origin + BigInt(index) * minute).toString(), end_ns: (origin + BigInt(index + 1) * minute).toString(),
    open: '100.01', high: '101.03', low: '99.99', close: '100.02', volume: '12.000000000000000001', closed: true, ...overrides };
}

function assertRendererQuantum(scale: ExactPlotScale): void {
  assert.ok(Number.isFinite(scale.minMove) && scale.minMove > 0);
  assert.ok(Number.isFinite(scale.base) && Number.isInteger(scale.base) && scale.base >= 1);
  // lightweight-charts' isBaseDecimal accepts bases above 1e18 directly.
  // Below that, PriceTickSpanCalculator requires only factors of 2 and 5.
  if (scale.base > 1e18) return;
  let remainder = scale.base;
  while (remainder % 2 === 0) remainder /= 2;
  while (remainder % 5 === 0) remainder /= 5;
  assert.equal(remainder, 1);
}

test('large adjacent exact prices remain distinct and inspect without floating-point rewriting', () => {
  const candle = bar(0, { open: '9007199254740993.00000001', high: '9007199254740993.00000004', low: '9007199254740993.00000000', close: '9007199254740993.00000002' });
  const data = priceSeriesData([candle], { ...colors, tick: '0.00000001' });
  const point = data.prices[0]!;
  assert.ok('open' in point);
  assert.ok(point.low < point.open && point.open < point.close && point.close < point.high);
  assert.equal(data.priceScale.format(point.close), candle.close);
  assert.equal(data.exact.get(point.time), candle);
  assert.equal(data.exact.get(point.time)?.volume, '12.000000000000000001');
  assert.ok(Math.abs(point.high) < 1e12);
  const empty = priceSeriesData([], { ...colors, tick: '0.00000001' });
  const loaded = priceSeriesData([candle], { ...colors, tick: '0.00000001', previous: empty });
  assert.deepEqual(loaded.prices, data.prices);
});

test('prepended decimal precision and float-adjacent prices keep renderer-compatible quanta through scale changes', () => {
  const recent = [bar(4, { volume: '123.456789' }), bar(5, { volume: '124.456789' })];
  const older = [
    bar(2, { open: '100.01000000000002', close: '100.01000000000004', volume: '123.4567890000001' }),
    bar(3, { volume: '125.4567890000001' }),
  ];
  const oldest = [bar(0, { volume: '123.4567890000002' }), bar(1, { volume: '125.4567890000002' })];
  const pages = [recent, [...older, ...recent], [...oldest, ...older, ...recent]];
  let previous: PriceSeriesData | undefined;
  for (const candles of pages) {
    const data = priceSeriesData(candles, { ...colors, tick: '0.01', previous });
    assert.equal(data.prices.length, candles.length);
    assert.equal(data.volumes.length, candles.length);
    assertRendererQuantum(data.priceScale);
    assertRendererQuantum(data.volumeScale);
    assert.equal(data.volumeScale.minMove, candles === recent ? 1e-3 : 1e-10);
    assert.equal(data.volumeScale.base, candles === recent ? 1e3 : 1e10);
    for (const candle of candles) {
      assert.equal(data.exact.get(chartTime(candle.start_ns)), candle);
      assert.equal(data.priceScale.format(data.priceScale.coordinate(candle.close)), candle.close);
      assert.equal(data.volumeScale.format(data.volumeScale.coordinate(candle.volume)), candle.volume);
    }
    if (previous && candles === pages[2]) {
      assert.equal(data.volumeScale.key, previous.volumeScale.key);
      assert.equal(data.priceScale.key, previous.priceScale.key);
    }
    previous = data;
  }
  const candles = pages[2]!;
  for (const log of [true, false, true, false]) {
    const data = priceSeriesData(candles, { ...colors, tick: '0.000000000000000001', log, previous });
    assertRendererQuantum(data.priceScale);
    assertRendererQuantum(data.volumeScale);
    assert.equal(data.priceScale.minMove, log ? 1e-18 : 1e-13);
    assert.equal(data.priceScale.base, log ? 1e18 : 1e13);
    assert.ok(data.priceScale.coordinate('100.01') < data.priceScale.coordinate('100.01000000000002'));
    assert.ok(data.priceScale.coordinate('100.01000000000002') < data.priceScale.coordinate('100.01000000000004'));
    assert.equal(data.exact.size, candles.length);
    assert.equal(data.volumeScale.coordinate('0'), 0);
    for (const candle of candles) {
      assert.equal(data.priceScale.format(data.priceScale.coordinate(candle.close)), candle.close);
      assert.equal(data.volumeScale.format(data.volumeScale.coordinate(candle.volume)), candle.volume);
    }
    previous = data;
  }
});

test('native decimal ticks and zero-baseline axes use explicit decimal bases even at tiny quanta', () => {
  const shifted = exactPlotScale(['9007199254740993.10', '9007199254740993.15'], { tick: '0.05' });
  assert.equal(shifted.minMove, 1);
  assert.equal(shifted.base, 1);
  assert.equal(shifted.coordinate('9007199254740993.15'), 5);
  const cases: [ExactPlotScale, number, number][] = [
    [exactPlotScale(['1.25', '2.5'], { log: true, tick: '0.05' }), 1e-2, 1e2],
    [exactPlotScale(['1e-18', '2e-18'], { log: true, tick: '1e-18' }), 1e-18, 1e18],
    [exactPlotScale(['12.0000000000000000000001'], { zero: true }), 1e-18, 1e18],
    [exactPlotScale(['-1250000000000000001e-23', '0', '2500000000000000001e-23'], { zero: true }), 1e-13, 1e13],
    [exactPlotScale(['1e-100'], { log: true, tick: '1e-100' }), 1e-100, 1e100],
  ];
  for (const [scale, minMove, base] of cases) {
    assertRendererQuantum(scale);
    assert.equal(scale.minMove, minMove);
    assert.equal(scale.base, base);
    if (!scale.logarithmic) assert.equal(scale.coordinate('0'), 0);
  }
  assert.throws(() => exactPlotScale(['1'], { log: true, tick: '1e-309' }), /display tick is outside chart bounds/);
});

test('unrepresentable precision is explicit rather than silently collapsing distinct prices', () => {
  assert.throws(() => exactPlotScale(['0', '100000000000000000000', '100000000000000000001']), /distinct exact values collide/);
  assert.throws(() => exactPlotScale(['9007199254740992', '9007199254740993'], { log: true }), /distinct exact values collide/);
  assert.throws(() => exactPlotScale(['1e1001']), /outside display bounds/);
});

test('linear accepts zero and negative prices; log rejects nonpositive values without offset substitution', () => {
  const linear = exactPlotScale(['-1.5', '0', '2.5']);
  assert.ok(linear.coordinate('-1.5') < linear.coordinate('0'));
  for (const value of ['0', '-0.01', '1e-200']) assert.throws(() => exactPlotScale([value], { log: true }), /Log scale requires positive/);
  const log = exactPlotScale(['10', '100', '1000'], { log: true });
  assert.equal(log.coordinate('100'), 100);
  assert.equal(log.format(10020000), '10020000');
});

test('zero-baseline volume and signed funding retain exact inspection and sign', () => {
  const funding = exactPlotScale(['-0.0000125', '0', '0.000025'], { zero: true });
  assert.equal(funding.coordinate('0'), 0);
  assert.ok(funding.coordinate('-0.0000125') < 0);
  assert.ok(funding.coordinate('0.000025') > 0);
  assert.equal(funding.format(funding.coordinate('-0.0000125')), '-0.0000125');
  const data = priceSeriesData([bar(0)], colors);
  assert.equal(data.volumeScale.coordinate('0'), 0);
  assert.equal(data.volumeScale.format(data.volumeScale.coordinate(bar(0).volume)), bar(0).volume);
});

test('timestamps stay native UTC seconds; unrepresentable boundaries and duplicate buckets fail', () => {
  assert.equal(chartTime(origin.toString()), 1704067200);
  assert.throws(() => chartTime((origin + 1n).toString()), /subsecond candle boundary/);
  assert.throws(() => chartTime('999999999999999999999000000000'), /timestamp outside/);
  assert.throws(() => priceSeriesData([bar(0), bar(0)], colors), /duplicate or unordered/);
  assert.throws(() => priceSeriesData([bar(1), bar(0)], colors), /duplicate or unordered/);
});

test('gaps create whitespace, never fabricated candles or volume', () => {
  const data = priceSeriesData([bar(0), bar(4)], colors);
  assert.equal(data.prices.length, 3);
  assert.deepEqual(data.prices[1], { time: chartTime(bar(0).end_ns) });
  assert.deepEqual(data.volumes[1], data.prices[1]);
  assert.equal(data.exact.has(chartTime(bar(0).end_ns)), false);
  assert.equal(data.exact.size, 2);
});

test('forming revisions update in place, historical corrections target old bar, rollover appends once', () => {
  const initial = priceSeriesData([bar(0), bar(1, { closed: false })], colors);
  const revised = priceSeriesData([bar(0), bar(1, { close: '100.03', volume: '13.000000000000000001', closed: false })], { ...colors, previous: initial });
  assert.equal(revised.priceScale.key, initial.priceScale.key);
  const update = seriesUpdate(initial.prices, revised.prices);
  assert.equal(update.reset, false);
  assert.equal(update.updates.length, 1);
  assert.equal(update.updates[0]!.historical, false);
  assert.equal(update.updates[0]!.point.time, chartTime(bar(1).start_ns));
  const corrected = priceSeriesData([bar(0, { high: '101.04' }), bar(1, { close: '100.03', volume: '13.000000000000000001' }), bar(2)], { ...colors, previous: revised });
  const correction = seriesUpdate(revised.prices, corrected.prices);
  assert.equal(correction.reset, false);
  assert.deepEqual(correction.updates.map(item => [item.point.time, item.historical]), [[chartTime(bar(0).start_ns), true], [chartTime(bar(2).start_ns), false]]);
  assert.deepEqual(seriesUpdate(corrected.prices, corrected.prices), { reset: false, updates: [] });
});

test('prepend anchors the same candle and fractional viewport while detached append does not move it', () => {
  const initial = priceSeriesData([bar(4), bar(5), bar(6)], colors);
  const older = priceSeriesData([bar(2), bar(3), bar(4), bar(5), bar(6)], { ...colors, previous: initial });
  assert.equal(older.priceScale.key, initial.priceScale.key);
  assert.equal(seriesUpdate(initial.prices, older.prices).reset, true);
  const range = { from: 0.25, to: 1.75 };
  assert.deepEqual(anchorLogicalRange(initial.times, older.times, range), { from: 2.25, to: 3.75 });
  const appended = priceSeriesData([bar(4), bar(5), bar(6), bar(7)], { ...colors, previous: initial });
  assert.deepEqual(anchorLogicalRange(initial.times, appended.times, range), range);
  assert.deepEqual(anchorLogicalRange(initial.times, older.times, { from: -2.5, to: 0.5 }), { from: -0.5, to: 2.5 });
});

test('deferred axis width changes retain candle pixels and spacing after precision-changing prepends', () => {
  const initial = priceSeriesData([bar(4), bar(5), bar(6)], colors);
  const older = priceSeriesData([bar(2, { close: '100.01000000000002', volume: '12.01234567890123456789' }), bar(3), bar(4), bar(5), bar(6)], { ...colors, previous: initial });
  assert.notEqual(older.priceScale.key, initial.priceScale.key);
  assert.notEqual(older.volumeScale.key, initial.volumeScale.key);
  const spacing = 8;
  const before = pixelAnchoredRange(-2.25, spacing, 974);
  const anchored = anchorLogicalRange(initial.times, older.times, before);
  // LWC's index-to-coordinate formula includes half a bar and a one-pixel inset.
  const pixel = (index: number, range: { from: number; to: number }, width: number) =>
    width - (range.to - index + 0.5) * (width / (range.to - range.from + 1)) - 1;
  for (const width of [942, 918, 974]) {
    const after = pixelAnchoredRange(anchored.from, spacing, width);
    assert.equal(width / (after.to - after.from + 1), spacing);
    for (let index = 0; index < initial.times.length; index++) {
      assert.equal(pixel(index + 2, after, width), pixel(index, before, 974));
    }
  }
});

test('replay truncation and gap replacement reset only the structural series', () => {
  const initial = priceSeriesData([bar(0), bar(2)], colors);
  const filled = priceSeriesData([bar(0), bar(1), bar(2)], { ...colors, previous: initial });
  const fill = seriesUpdate(initial.prices, filled.prices);
  assert.equal(fill.reset, false);
  assert.equal(fill.updates.length, 1);
  assert.equal(fill.updates[0]!.historical, true);
  assert.equal(seriesUpdate(filled.prices, filled.prices.slice(0, 1)).reset, true);
});

test('latest uses viewport density and persisted right margin, including sparse observed history', () => {
  assert.deepEqual(latestLogicalRange(300, 800, 5), { from: 204, to: 304 });
  assert.deepEqual(latestLogicalRange(300, 400, 12), { from: 261, to: 311 });
  const sparse = latestLogicalRange(2, 800, 5);
  assert.equal(sparse.to - sparse.from, 100);
  assert.equal(sparse.to, 6);
});
