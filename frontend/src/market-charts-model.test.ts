import { test } from 'node:test';
import assert from 'node:assert/strict';
import { CHART_LIMITS, COLORS, boundedSize, chartRowViewport, footprintCenterRow, intensity, liquidityTileKey, plotNumber, priceCellIndex, priceRowProjector, rowAt, shiftedRange, sideColor, timeAt, timeFraction, zoomedRange } from './market-charts-model.ts';
import type { VisualGrid } from './api.ts';

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

test('linked native prices select their containing chart bucket without rounding across viewport boundaries', () => {
  const view = { price_min: '9007199254740993.00000001', price_step: '0.00000002', rows: 2 };
  assert.equal(priceCellIndex(view, '9007199254740993.00000001'), 0);
  assert.equal(priceCellIndex(view, '9007199254740993.00000002'), 0);
  assert.equal(priceCellIndex(view, '9007199254740993.00000003'), 1);
  assert.equal(priceCellIndex(view, '9007199254740993.000000049999999999'), 1);
  assert.equal(priceCellIndex(view, '9007199254740993.000000009999999999'), -1);
  assert.equal(priceCellIndex(view, '9007199254740993.00000005'), -1);
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

function footprintGrid(footprints: VisualGrid['footprints'] = [], overrides: Partial<VisualGrid> = {}): VisualGrid {
  return {
    version: '1', generation: '1', grid_id: 'grid', instrument_id: 'exact', clock_id: 'clock', view_id: 'view', published_mono_ns: '1',
    quantity_unit: 'base', sampling: 'fixed', price_step: '1', price_min: '100', prices: Array.from({ length: 32 }, (_, row) => String(100 + row)), rows: 32,
    bucket_ns: '1000000000', start_ns: '0', end_ns: '6000000000', now_ns: '6000000000', retained_start_ns: '0',
    step_options: ['1'], columns: [], trades: [], profile: [], footprints,
    time_axis: { version: '1', basis: 'receive_mono_highwater', projection: 'piecewise_receive_pair_v1', source_identity: 'source',
      clock_id: 'clock', scope_id: null, available_start_ns: null, available_end_ns: null, offset_change_threshold_ns: '1000000',
      segments: [], last_unapplied_pair: null, reasons: ['clock_time_unavailable'] },
    reasons: [], truncated: false, complete_window: true, replace: true, next_after: null, ...overrides,
  };
}

function footprint(price: string, quantities: Partial<VisualGrid['footprints'][number]> = {}): VisualGrid['footprints'][number] {
  return { time_ns: '1000000000', price, buy_base: '1', sell_base: '0', unknown_base: '0', ...quantities };
}

test('initial footprint crop centers the actual occupied span without weighting, aggregation or invented cells', () => {
  const view = footprintGrid([footprint('104', { buy_base: '0.00000000000000000001' }), footprint('110', { buy_base: '999999999999999999999' })]);
  const before = structuredClone(view);
  assert.equal(footprintCenterRow(view), 7);
  assert.deepEqual(chartRowViewport(view, 'footprint', 240, null, false), { first: 2, count: 12 });
  assert.deepEqual(view, before);
  const empty = footprintGrid();
  assert.equal(footprintCenterRow(empty), null);
  assert.deepEqual(chartRowViewport(empty, 'footprint', 240, null, false), { first: 10, count: 12 });
});

test('an omitted older outlier cannot center the initial footprint crop away from paintable cells', () => {
  const paintable = Array.from({ length: CHART_LIMITS.footprints }, () => footprint('100'));
  const view = footprintGrid([footprint('131'), ...paintable]);
  const crop = chartRowViewport(view, 'footprint', 240, null, false);
  assert.equal(footprintCenterRow(view), 0);
  assert.deepEqual(crop, { first: 0, count: 12 });
  assert.deepEqual(crop, chartRowViewport(footprintGrid(paintable), 'footprint', 240, null, false));
  const visibleCells = view.footprints.slice(-CHART_LIMITS.footprints).filter((cell) => {
    const row = priceCellIndex(view, cell.price);
    return row >= crop.first && row < crop.first + crop.count;
  });
  assert.equal(visibleCells.length, CHART_LIMITS.footprints);
  assert.deepEqual(visibleCells, paintable);
});

test('footprint occupancy ignores malformed, empty and off-grid cells using exact native price membership', () => {
  const view = footprintGrid([
    footprint('invalid'), footprint('99.99999999999999999999'), footprint('132'),
    footprint('100', { buy_base: '0', sell_base: '0', unknown_base: '0' }),
    footprint('101', { buy_base: 'NaN' }),
    footprint('109.99999999999999999999', { buy_base: '0', unknown_base: '1e-30' }),
  ]);
  assert.equal(footprintCenterRow(view), 9);
  assert.equal(footprintCenterRow(footprintGrid(view.footprints.slice(0, -1))), null);
  const large = footprintGrid([footprint('9007199254740993.00000002'), footprint('9007199254740993.00000004')], {
    price_min: '9007199254740993.00000001', price_step: '0.00000001',
  });
  assert.equal(footprintCenterRow(large), 2);
});

test('footprint resize changes row count around a held native center rather than refitting incoming flow', () => {
  const view = footprintGrid([footprint('127')]);
  assert.deepEqual(chartRowViewport(view, 'footprint', 240, 15.5, false), { first: 10, count: 12 });
  assert.deepEqual(chartRowViewport(view, 'footprint', 280, 15.5, false), { first: 9, count: 14 });
  assert.deepEqual(chartRowViewport(view, 'footprint', 360, 15.5, false), { first: 7, count: 18 });
  assert.deepEqual(chartRowViewport(view, 'footprint', 240, null, false), { first: 20, count: 12 });
  assert.deepEqual(chartRowViewport(view, 'footprint', 640, 15.5, false), { first: 0, count: 32 });
});

test('footprint crop clamps at both edges while navigation and hit geometry can reach every native row', () => {
  const view = footprintGrid();
  const low = chartRowViewport(view, 'footprint', 240, -10, false);
  const high = chartRowViewport(view, 'footprint', 240, 100, false);
  assert.deepEqual(low, { first: 0, count: 12 });
  assert.deepEqual(high, { first: 20, count: 12 });
  assert.equal(low.first + rowAt(1, low.count), 0);
  assert.equal(high.first + rowAt(0, high.count), 31);
  for (const height of [0, -20, 19, NaN, Infinity]) {
    assert.deepEqual(chartRowViewport(view, 'footprint', height, 31, false), { first: 31, count: 1 });
  }
  assert.deepEqual(chartRowViewport(view, 'footprint', 259, NaN, false), { first: 10, count: 12 });
  for (let center = 0; center < view.rows; center++) {
    const crop = chartRowViewport(view, 'footprint', 240, center, false);
    assert.ok(crop.first <= center && center < crop.first + crop.count);
  }
});

test('explicit footprint overview and other modes retain the complete native grid at any pane height', () => {
  const view = footprintGrid([footprint('104')]);
  assert.deepEqual(chartRowViewport(view, 'footprint', 1, 4, true), { first: 0, count: 32 });
  for (const mode of ['heatmap', 'candles', 'profile', 'cvd', 'ofi'] as const) {
    assert.deepEqual(chartRowViewport(view, mode, 1, 4, false), { first: 0, count: 32 });
  }
});
