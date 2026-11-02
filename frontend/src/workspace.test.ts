import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mergeVisualGrid, validatePreferences } from './workspace.ts';
import type { VisualColumn, VisualGrid } from './api.ts';

test('persisted workspace bounds reject unsafe identity, time, quantum and dimensions without retaining sessions', () => {
  const settings = validatePreferences({ version: 1, session_id: 'must-not-persist', source: { mode: 'live', id: 'native-live' },
    instrument: 'x'.repeat(513), leftWidth: -5000, rightWidth: Infinity, bottomHeight: 1e8,
    domRows: 1000, priceOffset: -1e9, contrast: NaN, priceStep: 'Infinity', range: { start_ns: '1', end_ns: '99999999999999999999999' } });
  assert.deepEqual(settings.source, { mode: 'live', id: 'native-live' });
  assert.equal(settings.instrument, '');
  assert.ok(settings.leftWidth >= 180 && settings.rightWidth <= 500 && settings.bottomHeight <= 460);
  assert.ok(settings.domRows <= 100 && settings.priceOffset >= -10000 && Number.isFinite(settings.contrast));
  assert.equal(settings.priceStep, '');
  assert.equal(settings.range, null);
  assert.equal('session_id' in settings, false);
});

test('persisted valid exact quantum and source-monotonic range survive without floating point conversion', () => {
  const range = { start_ns: '9007199254740993123', end_ns: '9007199264740993123' };
  const settings = validatePreferences({ version: 1, range, priceStep: '0.0000000000000000000000001', follow: false, paused: true,
    source: { mode: 'replay', id: 'managed-recording' }, instrument: 'native/exact/token', mode: 'footprint' });
  assert.deepEqual(settings.range, range);
  assert.equal(settings.priceStep, '0.0000000000000000000000001');
  assert.equal(settings.follow, false);
  assert.equal(settings.paused, true);
  assert.equal(settings.mode, 'footprint');
  assert.equal(settings.source?.mode, 'replay');
});

test('fixed liquidity saturation survives reload for large and small base quantities', () => {
  for (const contrast of [1000, 0.00001]) {
    const saved = JSON.stringify(validatePreferences({ version: 1, contrast }));
    assert.equal(validatePreferences(JSON.parse(saved)).contrast, contrast);
  }
});

test('DOM grouping and quantity units persist independently of the chart quantum', () => {
  const settings = validatePreferences({ version: 1, priceStep: '10', domPriceStep: '0.0000000000000000001', domUnit: 'quote', domCumulative: true });
  const restored = validatePreferences(JSON.parse(JSON.stringify(settings)));
  assert.equal(restored.priceStep, '10');
  assert.equal(restored.domPriceStep, '0.0000000000000000001');
  assert.equal(restored.domUnit, 'quote');
  assert.equal(restored.domCumulative, true);
  const invalid = validatePreferences({ ...settings, domPriceStep: 'NaN', domUnit: 'USD', domCumulative: 'yes' });
  assert.equal(invalid.domPriceStep, '');
  assert.equal(invalid.domUnit, 'base');
  assert.equal(invalid.domCumulative, false);
});

function column(time: number, hash = `immutable-${time}`): VisualColumn {
  return { id: String(time), time_ns: String(time), end_ns: String(time + 1), segment_id: 'segment', asof: null,
    eligible: false, reasons: ['missing_depth'], liquidity_hash: hash, cells: [[null, '0']], best_bid: null, best_ask: null,
    cvd: null, ofi: null, candle: null };
}
function grid(columns: VisualColumn[], overrides: Partial<VisualGrid> = {}): VisualGrid {
  return { version: '1', generation: '1', view_id: 'view', published_mono_ns: '1', grid_id: 'grid', instrument_id: 'exact', clock_id: 'clock',
    quantity_unit: 'base', sampling: 'fixed', price_step: '1', price_min: '0', prices: ['0'], rows: 1, bucket_ns: '1',
    start_ns: '0', end_ns: '1000', now_ns: '1000', retained_start_ns: '0', step_options: ['1'], columns, trades: [], profile: [], footprints: [],
    reasons: ['partial_history'], truncated: false, complete_window: false, replace: false, next_after: null, ...overrides };
}

test('incremental grid preserves immutable history and unknown cells while bounding the visible retained range', () => {
  const closed = column(500);
  const previous = grid([...Array.from({ length: 500 }, (_, i) => column(i)), closed]);
  const next = grid(Array.from({ length: 300 }, (_, i) => column(i + 501)), { start_ns: '100', end_ns: '800', retained_start_ns: '100', truncated: true });
  const merged = mergeVisualGrid(previous, next);
  assert.equal(merged.columns.length, 600);
  assert.equal(merged.columns.find(item => item.id === '500'), closed);
  assert.deepEqual(closed.cells, [[null, '0']]);
  assert.equal(merged.truncated, true);
  assert.equal(merged.complete_window, false);
  assert.ok(merged.columns.every(item => BigInt(item.time_ns) >= 100n && BigInt(item.time_ns) < 800n));
  assert.equal(previous.columns.length, 501);
});

test('seek, quantum replacement and explicit replace cannot retain future or foreign grid columns', () => {
  const previous = grid([column(90), column(100)]);
  for (const replacement of [grid([column(1)], { generation: '2' }), grid([column(2)], { grid_id: 'new-quantum' }), grid([], { replace: true })]) {
    assert.equal(mergeVisualGrid(previous, replacement), replacement);
  }
  const panned = mergeVisualGrid(previous, grid([], { start_ns: '0', end_ns: '90' }));
  assert.equal(panned.columns.length, 0);
});
