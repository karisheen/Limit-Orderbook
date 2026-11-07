import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Frame } from './api.ts';
import { candleAtTime, candleDate, candleIdentity, candlePriceScale, candleRange, candleTimeTicks, followCandleRange, fractionAt, matchesCandleHistory, panCandles, timeAtFraction, validateCandles, zoomCandles } from './candle-model.ts';
import type { CandleHistory, PriceCandle } from './candle-model.ts';

const hour = 3_600_000_000_000n;
const origin = 1_704_067_200_000_000_000n;
function bar(offset: bigint, overrides: Partial<PriceCandle> = {}): PriceCandle {
  return { start_ns: (origin + offset * hour).toString(), end_ns: (origin + (offset + 1n) * hour).toString(), open: '100.10', high: '102.00', low: '99.90', close: '101.20', volume: '12.34567890123456789', closed: true, ...overrides };
}
function history(candles: PriceCandle[] = [bar(0n)], overrides: Partial<CandleHistory> = {}): CandleHistory {
  return { version: '1', session_id: 'session', generation: '4', instrument_id: 'native', interval: '1h', status: 'ready', source: 'venue_ohlcv', venue: 'venue', time_basis: 'exchange_utc', fetched_at_ns: origin.toString(), supported_intervals: ['1m', '1h', '1M'], candles, reasons: [], volume_unit: 'base', coverage_start_ns: candles[0]?.start_ns ?? null, coverage_end_ns: candles[candles.length - 1]?.end_ns ?? null, ...overrides };
}

test('timestamp positions retain missing hour gaps instead of compressing the series', () => {
  const candles = [bar(0n), bar(1n), bar(4n)];
  const range = { start: origin, end: origin + 5n * hour };
  assert.equal(fractionAt(candles[1]!.start_ns, range), 0.2);
  assert.equal(fractionAt(candles[2]!.start_ns, range), 0.8);
  assert.equal(candleAtTime(candles, origin + 2n * hour), -1);
  assert.equal(candleAtTime(candles, origin + 4n * hour), 2);
  assert.equal(candleAtTime(candles, origin + 5n * hour), -1);
  assert.equal(candleAtTime(candles, origin - 1n), -1);
});

test('viewport precision is relative to the timestamp origin, not Number(epoch nanoseconds)', () => {
  const range = { start: 9_007_199_254_740_993_000_000_001n, end: 9_007_199_254_740_993_000_001_001n };
  assert.equal(fractionAt(range.start + 501n, range), 0.501);
  assert.equal(timeAtFraction(0.501, range), range.start + 501n);
  assert.deepEqual(panCandles(range, 0.25), { start: range.start + 250n, end: range.end + 250n });
});

test('active bar updates do not shift time viewport; following advances only on a new boundary', () => {
  const initial = [bar(0n), bar(1n, { closed: false })];
  const range = candleRange(initial, true)!;
  const previousEnd = BigInt(initial[1]!.end_ns);
  const update = [initial[0]!, bar(1n, { close: '101.90', volume: '22.01', closed: false })];
  assert.deepEqual(candleRange(update, true), range);
  assert.deepEqual(followCandleRange(range, previousEnd, update), range);
  const advanced = [...update, bar(2n, { closed: false })];
  assert.deepEqual(followCandleRange(range, previousEnd, advanced), { start: range.start + hour, end: range.end + hour });
  assert.deepEqual(followCandleRange(range, previousEnd, [bar(0n)]), range);
});

test('fit includes every actual bucket and latest opens a bounded recent window', () => {
  const candles = Array.from({ length: 300 }, (_, index) => bar(BigInt(index)));
  const fit = candleRange(candles)!;
  const latest = candleRange(candles, true)!;
  assert.equal(fit.start, origin - hour);
  assert.equal(fit.end, origin + 303n * hour);
  assert.equal(latest.start, origin + 209n * hour);
  assert.equal(latest.end, fit.end);
  assert.equal(candleRange([]), null);
});

test('zoom retains its timestamp anchor and enforces finite useful span bounds', () => {
  const candles = Array.from({ length: 100 }, (_, index) => bar(BigInt(index)));
  const range = { start: origin, end: origin + 100n * hour };
  const next = zoomCandles(range, 0.5, 0.25, candles);
  assert.equal(timeAtFraction(0.25, next), timeAtFraction(0.25, range));
  const min = zoomCandles(range, 0.00001, 0.5, candles);
  assert.equal(min.end - min.start, 3n * hour);
  const max = zoomCandles(range, 1e6, 0.5, candles);
  assert.equal(max.end - max.start, 300n * hour);
});

test('calendar month display respects actual provider boundaries, not a fixed 30-day duration', () => {
  const start = BigInt(Date.parse('2024-02-01T00:00:00Z')) * 1_000_000n;
  const end = BigInt(Date.parse('2024-03-01T00:00:00Z')) * 1_000_000n;
  const candle = bar(0n, { start_ns: start.toString(), end_ns: end.toString() });
  assert.equal(candleAtTime([candle], end - 1n), 0);
  assert.equal(candleAtTime([candle], end), -1);
  assert.equal(candleDate(start, true), '2024-02-01 00:00:00 UTC');
  const range = candleRange([candle])!;
  assert.equal(range.start, start - 29n * 24n * hour);
});

test('exact OHLCV validation never rounds adjacent prices or modifies source strings', () => {
  const exact = bar(0n, { open: '9007199254740993.00000001', high: '9007199254740993.00000004', low: '9007199254740993.00000000', close: '9007199254740993.00000003' });
  const response = history([exact]);
  const before = JSON.stringify(response);
  validateCandles(response);
  assert.equal(JSON.stringify(response), before);
  const scale = candlePriceScale([exact]);
  assert.ok(scale.fraction(exact.close) > scale.fraction(exact.open));
  assert.ok(scale.fraction(exact.high) > scale.fraction(exact.close));
  assert.equal(scale.label(0.5), '9007199254740993.00000002');
  assert.throws(() => validateCandles(history([{ ...exact, high: exact.open }])), /OHLCV/);
});

test('price projection handles mixed decimal scales, constant price, and outside-view last-price precision', () => {
  const scale = candlePriceScale([bar(0n, { low: '1', high: '2', open: '1.1234', close: '1.1235' })]);
  assert.ok(scale.fraction('1.1235') > scale.fraction('1.1234'));
  assert.ok(Number.isFinite(scale.fraction('1.1234567890123456')));
  const constant = candlePriceScale([bar(0n, { low: '100', high: '100', open: '100', close: '100' })]);
  assert.equal(constant.fraction('100'), 0.5);
  assert.deepEqual([0, .2, .4, .6, .8, 1].map(constant.label), ['99', '99.4', '99.8', '100.2', '100.6', '101']);
  const scientific = candlePriceScale([bar(0n, { low: '1e-30', high: '1.02e-30', open: '1e-30', close: '1.01e-30' })]);
  assert.ok(scientific.fraction('1.01e-30') > scientific.fraction('1e-30'));
});

test('invalid envelopes, duplicates, negative volume and overlapping buckets cannot render', () => {
  assert.throws(() => validateCandles(history([bar(0n), bar(0n)])), /overlapping/);
  assert.throws(() => validateCandles(history([bar(0n, { low: '0' })])), /OHLCV/);
  assert.throws(() => validateCandles(history([bar(0n, { volume: '-0.00000000001' })])), /OHLCV/);
  assert.throws(() => validateCandles(history([bar(0n, { open: 'NaN' })])), /decimal/);
  assert.throws(() => validateCandles(history([bar(0n, { end_ns: origin.toString() })])), /overlapping/);
  assert.throws(() => validateCandles(history([], { status: 'ready' })), /status/);
  validateCandles(history([], { status: 'empty' }));
  validateCandles(history([], { status: 'unavailable', interval: '1w', reasons: ['unsupported_interval'] }));
});

test('request ownership fences include generation, source, native instrument and interval, never book revisions', () => {
  const frame = { session_id: 'session', generation: '4', source: { id: 'source-a', mode: 'live' }, selection: { instrument_id: 'native' } } as Frame;
  const identity = candleIdentity(frame, '1h');
  assert.equal(candleIdentity({ ...frame, frame_id: '999' }, '1h'), identity);
  assert.notEqual(candleIdentity({ ...frame, generation: '5' }, '1h'), identity);
  assert.notEqual(candleIdentity({ ...frame, session_id: 'new' }, '1h'), identity);
  assert.notEqual(candleIdentity({ ...frame, source: { ...frame.source, id: 'source-b' } }, '1h'), identity);
  assert.notEqual(candleIdentity({ ...frame, selection: { instrument_id: 'other' } }, '1h'), identity);
  assert.notEqual(candleIdentity(frame, '4h'), identity);
  const response = history();
  assert.equal(matchesCandleHistory(response, 'session', '4', 'native', '1h'), true);
  for (const [session, generation, instrument, interval] of [['other', '4', 'native', '1h'], ['session', '3', 'native', '1h'], ['session', '4', 'other', '1h'], ['session', '4', 'native', '1m']]) {
    assert.equal(matchesCandleHistory(response, session!, generation!, instrument!, interval!), false);
  }
});

test('time ticks stay clock-aligned under panning instead of displaying arbitrary fractions', () => {
  const start = origin + 4n * hour;
  const first = candleTimeTicks({ start, end: start + 94n * hour }, 1066);
  assert.deepEqual(first.map(tick => (tick.time - origin) / hour), [12n, 24n, 36n, 48n, 60n, 72n, 84n, 96n]);
  const panned = candleTimeTicks({ start: start + hour, end: start + 95n * hour }, 1066);
  assert.deepEqual(panned, first);
  assert.equal(first[0]!.label, '01-01 12:00');
});

test('wide candle axes use calendar year boundaries across leap years', () => {
  const start = BigInt(Date.parse('2023-03-01T00:00:00Z')) * 1_000_000n;
  const end = BigInt(Date.parse('2028-03-01T00:00:00Z')) * 1_000_000n;
  const ticks = candleTimeTicks({ start, end }, 960);
  assert.deepEqual(ticks.map(tick => tick.label), ['2024-01-01', '2025-01-01', '2026-01-01', '2027-01-01', '2028-01-01']);
  assert.equal(ticks[1]!.time - ticks[0]!.time, 366n * 24n * hour);
});
