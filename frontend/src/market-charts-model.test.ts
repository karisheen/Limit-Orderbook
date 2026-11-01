import { test } from 'node:test';
import assert from 'node:assert/strict';
import { CHART_LIMITS, COLORS, boundedSize, intensity, liquidityTileKey, plotNumber, priceRowProjector, rowAt, shiftedRange, sideColor, timeAt, timeFraction, zoomedRange } from './market-charts-model.ts';

test('large source clocks preserve visible nanosecond offsets without wall-clock conversion', () => {
  const start = '9007199254740993000000001';
  const end = '9007199254740993000001001';
  assert.equal(timeFraction('9007199254740993000000501', start, end), 0.5);
  assert.equal(timeAt(0.5, start, end), '9007199254740993000000501');
  assert.equal(timeAt(0, start, end), start);
  assert.equal(timeAt(1, start, end), end);
});

test('panning preserves exact window width and never requests a negative source clock', () => {
  const range = { start_ns: '9007199254740993000000001', end_ns: '9007199254740993000001001' };
  assert.deepEqual(shiftedRange(range, 0.25), { start_ns: '9007199254740993000000251', end_ns: '9007199254740993000001251' });
  assert.deepEqual(shiftedRange({ start_ns: '10', end_ns: '110' }, -1), { start_ns: '0', end_ns: '100' });
});

test('time zoom remains anchored and bounded to one through 600 actual sampling buckets', () => {
  assert.deepEqual(zoomedRange({ start_ns: '1000', end_ns: '2000' }, 0.25, 0.5, '100'), { start_ns: '1125', end_ns: '1625' });
  const minimum = zoomedRange({ start_ns: '1000', end_ns: '2000' }, 0.5, 0.001, '100');
  assert.equal(BigInt(minimum.end_ns) - BigInt(minimum.start_ns), 100n);
  const maximum = zoomedRange({ start_ns: '1000', end_ns: '2000' }, 0.5, 1000, '100');
  assert.equal(BigInt(maximum.end_ns) - BigInt(maximum.start_ns), 60_000n);
  const coarse = zoomedRange({ start_ns: '0', end_ns: '1600000000000' }, 0.5, 1.25, '10000000000');
  assert.equal(BigInt(coarse.end_ns) - BigInt(coarse.start_ns), 1_800_000_000_000n);
});

test('unknown coverage stays distinct from exact observed zero and fixed saturation never follows incoming extrema', () => {
  assert.equal(intensity(null, 10), null);
  assert.equal(intensity('0', 10), 0);
  assert.equal(intensity('5', 10), 0.5);
  assert.equal(intensity('500000', 10), 1);
  assert.equal(plotNumber('1e999'), null);
  assert.equal(sideColor('unknown'), COLORS.unknown);
  assert.notEqual(sideColor('unknown'), sideColor('buy'));
  assert.notEqual(sideColor('unknown'), sideColor('sell'));
});

test('cursor selects existing exact price rows at both viewport boundaries', () => {
  const prices = ['9007199254740993.00000001', '9007199254740993.00000002'];
  assert.equal(prices[rowAt(0, prices.length)], prices[1]);
  assert.equal(prices[rowAt(1, prices.length)], prices[0]);
  assert.equal(rowAt(-10, 256), 255);
  assert.equal(rowAt(10, 256), 0);
});

test('canvas backing allocation stays bounded at high DPR and oversized panes', () => {
  for (const [width, height, dpr] of [[1440, 900, 3], [8000, 8000, 8], [320, 200, 1], [0, 0, 2]]) {
    const size = boundedSize(width!, height!, dpr!);
    assert.ok(size.pixelWidth > 0 && size.pixelWidth <= CHART_LIMITS.width);
    assert.ok(size.pixelHeight > 0 && size.pixelHeight <= CHART_LIMITS.height);
    assert.ok(size.ratio <= CHART_LIMITS.dpr);
  }
});

test('immutable liquidity tiles survive unrelated market updates but not grid, quality or contrast changes', () => {
  const view = { generation: '7', grid_id: 'grid-a', price_min: '100', price_step: '0.5', rows: 32 };
  const column = { segment_id: 'segment-a', liquidity_hash: 'book-evidence-a', eligible: true };
  const original = liquidityTileKey(view, column, 10);
  assert.notEqual(liquidityTileKey(view, { ...column, liquidity_hash: 'book-evidence-b' }, 10), original);
  assert.notEqual(liquidityTileKey(view, { ...column, eligible: false }, 10), original);
  assert.notEqual(liquidityTileKey(view, { ...column, segment_id: 'segment-b' }, 10), original);
  assert.notEqual(liquidityTileKey({ ...view, price_min: '101' }, column, 10), original);
  assert.notEqual(liquidityTileKey({ ...view, generation: '8' }, column, 10), original);
  assert.notEqual(liquidityTileKey(view, column, 20), original);
});

test('stable price projection does not collapse adjacent tiny decimals at a large world origin', () => {
  const row = priceRowProjector('9007199254740993.00000001', '0.00000001');
  assert.equal(row('9007199254740993.00000001'), 0);
  assert.equal(row('9007199254740993.00000002'), 1);
  assert.equal(row('9007199254740993'), -1);
  assert.equal(priceRowProjector('1E-30', '1E-32')('1.01E-30'), 1);
});
