import { test } from 'node:test';
import assert from 'node:assert/strict';
import { applyLayoutSettings, changeWorkspaceLayout, chartFollowSpan, chartSettings, layoutName, layoutSettings, loadPreferences, MAX_LAYOUTS, mergeVisualGrid, migrateWorkspaceSlots, navigateChartTime, preferenceKey, retainedChartRange, validatePreferences, validateWorkspaceSlots, workspaceSlotsKey } from './workspace.ts';
import type { VisualColumn, VisualGrid } from './api.ts';
import { changeGrid, GRID_BASE_ROWS, GRID_COLUMNS, GRID_MIN_ROW_HEIGHT, GRID_PRESETS, GRID_ROWS, gridPreset, gridReadingOrder, gridRowHeight, migrateShellGrid, overlaps, PANEL_IDS, PANEL_MINIMUM, validateGrid } from './grid/layout.ts';
import type { GridPresetId } from './grid/layout.ts';

test('legacy workspaces open hourly price action without losing their existing controls', () => {
  const range = { start_ns: '9007199254740993123', end_ns: '9007199255740993123' };
  const legacy = { version: 1, source: { mode: 'replay', id: 'recording' }, sourceExplicit: true,
    instrument: 'exact/native', rightWidth: 314, leftOpen: true, bottomOpen: true,
    mode: 'heatmap', priceStep: '0.00000000000001', range, follow: false, paused: true,
    domFrozen: true, domUnit: 'quote', tapeSide: 'sell', links: { dom: false },
    secondary: { mode: 'candles', bucket: '5000000000', range, follow: false } };
  const migrated = validatePreferences(JSON.parse(JSON.stringify(legacy)));
  assert.equal(migrated.desktopView, 'price');
  assert.equal(migrated.candleInterval, '1h');
  assert.deepEqual(migrated.source, legacy.source);
  assert.equal(migrated.sourceExplicit, true);
  assert.equal(migrated.instrument, legacy.instrument);
  assert.equal(migrated.rightWidth, 314);
  assert.equal(migrated.rightWidthMode, 'manual');
  assert.equal(migrated.leftOpen, true);
  assert.equal(migrated.bottomOpen, true);
  assert.equal(migrated.priceStep, legacy.priceStep);
  assert.deepEqual(migrated.range, range);
  assert.equal(migrated.follow, false);
  assert.equal(migrated.paused, true);
  assert.equal(migrated.domFrozen, true);
  assert.equal(migrated.domUnit, 'quote');
  assert.equal(migrated.tapeSide, 'sell');
  assert.equal(migrated.links.dom, false);
  assert.equal(migrated.secondary.mode, 'candles');
  assert.equal(migrated.secondary.bucket, '5000000000');
  assert.deepEqual(migrated.secondary.range, range);
});

test('desktop workspace and candle intervals survive reload and saved workspace slots', () => {
  for (const desktopView of ['price', 'orderflow'] as const) {
    for (const candleInterval of ['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '8h', '12h', '1d', '3d', '1w', '1M']) {
      const saved = validatePreferences({ version: 1, desktopView, candleInterval, bucket: '5000000000' });
      const reloaded = validatePreferences(JSON.parse(JSON.stringify(saved)));
      const slots = validateWorkspaceSlots([reloaded]);
      const restored = applyLayoutSettings(validatePreferences(null), migrateWorkspaceSlots(slots)[0]!.settings);
      assert.equal(restored.desktopView, desktopView);
      assert.equal(restored.candleInterval, candleInterval);
      assert.equal(restored.bucket, '5000000000');
      assert.equal(validatePreferences({ ...saved, grid: gridPreset('balanced') }).candleInterval, candleInterval);
    }
  }
});

test('invalid candle durations and workspace names fall back independently to hourly price action', () => {
  for (const invalid of [undefined, null, '', '1H', '1s', '60m', '30d', 3600, {}, ['1h']]) {
    const restored = validatePreferences({ version: 1, desktopView: invalid, candleInterval: invalid, domRows: 40 });
    assert.equal(restored.desktopView, 'price');
    assert.equal(restored.candleInterval, '1h');
    assert.equal(restored.domRows, 40);
  }
});

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

test('v1 workspaces migrate to independent chart, tape and cursor settings', () => {
  const old = validatePreferences({ version: 1, mode: 'candles', priceStep: '0.5', bucket: '1000000000', leftWidth: 240 });
  assert.equal(old.mode, 'candles');
  assert.equal(old.secondary.mode, 'footprint');
  assert.equal(old.secondary.priceStep, '');
  assert.equal(old.leftWidth, 240);
  assert.deepEqual(old.links, { heatmap: true, flow: true, dom: true, tape: true });
  const independent = validatePreferences({ ...old, secondary: { ...old.secondary, mode: 'candles', priceStep: '0.00000000000001', rangeSeconds: 60 }, tapeRows: 25, tapeSide: 'unknown', links: { ...old.links, flow: false } });
  const restored = validatePreferences(JSON.parse(JSON.stringify(independent)));
  assert.equal(restored.priceStep, '0.5');
  assert.equal(restored.secondary.priceStep, '0.00000000000001');
  assert.equal(restored.secondary.rangeSeconds, 60);
  assert.equal(restored.tapeRows, 25);
  assert.equal(restored.tapeSide, 'unknown');
  assert.equal(restored.links.flow, false);
});

test('independent chart time zones survive serialization and workspace slots without changing source-time ranges', () => {
  const range = { start_ns: '9007199254740993123', end_ns: '9007199255740993123' };
  for (const [primary, secondary] of [['local', 'UTC'], ['UTC', 'local']] as const) {
    const saved = validatePreferences({ version: 1, timeZone: primary, range, follow: false, paused: true,
      secondary: { timeZone: secondary, range, follow: false, paused: true } });
    const restored = validatePreferences(JSON.parse(JSON.stringify(saved)));
    assert.equal(restored.timeZone, primary);
    assert.equal(chartSettings(restored.secondary).timeZone, secondary);
    assert.deepEqual(restored.range, range);
    assert.deepEqual(restored.secondary.range, range);
    assert.equal(restored.paused, true);
    assert.equal(restored.secondary.paused, true);
    const slots = validateWorkspaceSlots(JSON.parse(JSON.stringify([saved])));
    const applied = applyLayoutSettings(validatePreferences({ version: 1 }), migrateWorkspaceSlots(slots)[0]!.settings);
    assert.equal(applied.timeZone, primary);
    assert.equal(applied.secondary.timeZone, secondary);
  }
});

test('legacy and invalid saved chart time zones migrate safely without discarding unrelated preferences', () => {
  for (const timeZone of [undefined, null, '', 'utc', 'America/New_York', 1, {}, ['UTC']]) {
    const restored = validatePreferences({ version: 1, timeZone, priceStep: '0.00001', secondary: { timeZone, bucket: '10000000000' } });
    assert.equal(restored.timeZone, 'UTC');
    assert.equal(restored.secondary.timeZone, 'UTC');
    assert.equal(restored.priceStep, '0.00001');
    assert.equal(restored.secondary.bucket, '10000000000');
  }
});

test('saved footprint intervals and explicit overlays survive migration to detailed row preferences', () => {
  for (const enabled of [true, false]) {
    const legacy = { version: 1, bucket: '10000000000', showTrades: !enabled, showQuotes: !enabled,
      secondary: { mode: 'footprint', bucket: '5000000000', rangeSeconds: 180, showTrades: enabled, showQuotes: enabled } };
    const migrated = validatePreferences(JSON.parse(JSON.stringify(legacy)));
    assert.equal(migrated.bucket, '10000000000');
    assert.equal(migrated.showTrades, !enabled);
    assert.equal(migrated.showQuotes, !enabled);
    assert.equal(migrated.secondary.bucket, '5000000000');
    assert.equal(migrated.secondary.rangeSeconds, 180);
    assert.equal(migrated.secondary.showTrades, enabled);
    assert.equal(migrated.secondary.showQuotes, enabled);
    assert.equal(migrated.footprintOverview, false);
    assert.equal(migrated.secondary.footprintOverview, false);
  }
});

test('explicit overview and six-second follow preferences survive presets, reload and workspace slots independently', () => {
  const saved = validatePreferences({ version: 1, footprintOverview: true,
    secondary: { footprintOverview: false, bucket: '1000000000', rangeSeconds: 6, showTrades: false, showQuotes: true } });
  const preset = validatePreferences({ ...saved, grid: gridPreset('liquidity') });
  const reloaded = validatePreferences(JSON.parse(JSON.stringify(preset)));
  assert.equal(reloaded.footprintOverview, true);
  assert.equal(reloaded.secondary.footprintOverview, false);
  assert.equal(reloaded.secondary.rangeSeconds, 6);
  assert.equal(reloaded.secondary.showQuotes, true);
  const slots = validateWorkspaceSlots([reloaded]);
  const restored = applyLayoutSettings(validatePreferences(null), migrateWorkspaceSlots(slots)[0]!.settings);
  assert.equal(restored.footprintOverview, true);
  assert.deepEqual(chartSettings(restored.secondary), chartSettings(reloaded.secondary));
  const inverse = validatePreferences({ ...saved, footprintOverview: false, secondary: { ...saved.secondary, footprintOverview: true } });
  assert.equal(inverse.footprintOverview, false);
  assert.equal(inverse.secondary.footprintOverview, true);
  for (const invalid of ['true', 1, null]) {
    const rejected = validatePreferences({ ...saved, footprintOverview: invalid, secondary: { ...saved.secondary, footprintOverview: invalid } });
    assert.equal(rejected.footprintOverview, false);
    assert.equal(rejected.secondary.footprintOverview, false);
  }
});

test('pane boundaries reject unsafe splits and secondary settings without retaining foreign fields', () => {
  const settings = validatePreferences({ version: 1, chartSplit: 1e9, tapeSplit: -20, tapeRows: 10000, tapeSide: 'invented',
    links: { heatmap: 'yes', dom: false }, secondary: { mode: 'ofi', contrast: NaN, priceStep: 'Infinity', rangeSeconds: 999,
      session_id: 'foreign', domRows: 999, source: { mode: 'live', id: 'foreign' }, range: { start_ns: '8', end_ns: '2' } } });
  assert.equal(settings.chartSplit, 70);
  assert.equal(settings.tapeSplit, 30);
  assert.equal(settings.tapeRows, 50);
  assert.equal(settings.tapeSide, 'all');
  assert.equal(settings.links.heatmap, true);
  assert.equal(settings.links.dom, false);
  assert.equal(settings.secondary.mode, 'footprint');
  assert.equal(settings.secondary.priceStep, '');
  assert.equal(settings.secondary.contrast, 1);
  assert.equal(settings.secondary.range, null);
  assert.equal('session_id' in settings.secondary, false);
  assert.equal('source' in settings.secondary, false);
  assert.equal('domRows' in settings.secondary, false);
  assert.deepEqual(chartSettings(settings.secondary), settings.secondary);
});

test('four bounded workspace slots restore layout and pane settings without source or session contamination', () => {
  const current = validatePreferences({ version: 1, source: { mode: 'live', id: 'current-live' }, sourceExplicit: true, instrument: 'exact/live' });
  const saved = validatePreferences({ version: 1, preset: 'ladder', rightWidth: 420, chartSplit: 64, tapeSplit: 70, domRows: 40,
    tapeRows: 100, tapeSide: 'sell', domFrozen: true, tapeFrozen: true, paused: true,
    range: { start_ns: '1', end_ns: '10' }, secondary: { mode: 'candles', bucket: '10000000000', priceStep: '0.01', paused: true },
    source: { mode: 'demo', id: 'old-demo' }, instrument: 'old/instrument', session_id: 'never-store' });
  const slots = validateWorkspaceSlots([saved, null, { version: 99 }, saved, saved]);
  assert.equal(slots.length, 4);
  assert.equal(slots[1], null);
  assert.equal(slots[2], null);
  assert.equal(slots[0]?.source, null);
  assert.equal(slots[0]?.instrument, '');
  assert.ok(!JSON.stringify(slots).includes('never-store'));
  const restored = applyLayoutSettings(current, migrateWorkspaceSlots(slots)[0]!.settings);
  assert.deepEqual(restored.source, current.source);
  assert.equal(restored.sourceExplicit, true);
  assert.equal(restored.instrument, current.instrument);
  assert.equal(restored.chartSplit, 64);
  assert.equal(restored.tapeSplit, 70);
  assert.equal(restored.rightWidth, saved.rightWidth);
  assert.equal(restored.domRows, 40);
  assert.equal(restored.tapeRows, 100);
  assert.equal(restored.tapeSide, 'sell');
  assert.equal(restored.secondary.mode, 'candles');
  assert.equal(restored.secondary.bucket, '10000000000');
  assert.equal(restored.secondary.priceStep, '0.01');
  assert.equal(restored.paused, false);
  assert.equal(restored.domFrozen, false);
  assert.equal(restored.tapeFrozen, false);
  assert.equal(restored.secondary.paused, false);
  assert.equal(restored.range, null);
});

test('legacy numeric widths conservatively migrate to manual, including old default widths', () => {
  for (const rightWidth of [250, 300, 314, 420, 487]) {
    const migrated = validatePreferences({ version: 1, rightWidth });
    assert.equal(migrated.rightWidthMode, 'manual');
    assert.equal(migrated.rightWidth, rightWidth);
    const reloaded = validatePreferences(JSON.parse(JSON.stringify(migrated)));
    assert.equal(reloaded.rightWidthMode, 'manual');
    assert.equal(reloaded.rightWidth, rightWidth);
  }
  for (const rightWidth of [undefined, null, Infinity, '420']) {
    assert.equal(validatePreferences({ version: 1, rightWidth }).rightWidthMode, 'auto');
  }
  const wide = migrateShellGrid(undefined, 487, 248);
  const narrow = migrateShellGrid(undefined, 250, 248);
  assert.equal(wide.cells.find(item => item.id === 'chart')!.w, 8);
  assert.equal(narrow.cells.find(item => item.id === 'chart')!.w, 9);
});

test('analysis retains its manual 300px pane through reload and workspace slots', () => {
  const settings = validatePreferences({ version: 1, preset: 'analysis', rightWidth: 300, rightWidthMode: 'manual' });
  const reloaded = validatePreferences(JSON.parse(JSON.stringify(settings)));
  const slots = validateWorkspaceSlots([reloaded]);
  const restored = applyLayoutSettings(validatePreferences(null), migrateWorkspaceSlots(slots)[0]!.settings);
  for (const preferences of [settings, reloaded, restored]) {
    assert.equal(preferences.rightWidthMode, 'manual');
    assert.equal(preferences.rightWidth, 300);
  }
});

test('legacy resize bounds survive migration and narrow reading order never rewrites desktop geometry', () => {
  for (const [requested, expected] of [[-1, 250], [250, 250], [300, 300], [500, 500], [9999, 500]]) {
    const settings = validatePreferences({ version: 1, rightWidth: requested, rightWidthMode: 'manual' });
    assert.equal(settings.rightWidth, expected);
    const migrated = migrateWorkspaceSlots(validateWorkspaceSlots([settings]))[0]!;
    const saved = JSON.stringify(migrated);
    const mobile = gridReadingOrder(migrated.settings.grid);
    assert.equal(mobile[0]!.id, 'chart');
    assert.equal(JSON.stringify(migrated), saved);
    const reloaded = validatePreferences({ version: 2, layouts: [migrated] });
    assert.deepEqual(reloaded.layouts[0]!.settings.grid, migrated.settings.grid);
    assert.equal(reloaded.layouts[0]!.settings.rightWidth, expected);
  }
});

test('unlinked pin and follow gestures isolate the other chart even with all cursor links enabled', () => {
  const initial = validatePreferences({ version: 1, followSpanNs: '9999999999', priceStep: '0.01', priceOffset: 48, paused: true,
    domFrozen: true, tapeFrozen: true, secondary: { priceStep: '25', priceOffset: -13, bucket: '10000000000', rangeSeconds: 30 } });
  const range = { start_ns: '9007199254740993123', end_ns: '9007199264740993124' };
  const pinned = navigateChartTime(initial, 'flow', { kind: 'pin', range });
  assert.deepEqual(pinned, { ...initial, secondary: { ...initial.secondary, follow: false, range, followSpanNs: null } });
  const following = navigateChartTime(pinned, 'heatmap', { kind: 'follow', span_ns: '10000000001' });
  assert.deepEqual(following, { ...pinned, range: null, follow: true, followSpanNs: '10000000001' });
  assert.equal(following.secondary, pinned.secondary);
});

test('enabling time link aligns to the actor then mirrors only time intent, not scales, overlays or independent freezes', () => {
  const initial = validatePreferences({ version: 1, mode: 'heatmap', paused: true, priceStep: '0.0001', priceOffset: 87,
    contrast: 100, bucket: '5000000000', timeZone: 'local', domFrozen: true, tapeFrozen: false, domRows: 40,
    links: { heatmap: false, flow: false }, secondary: { mode: 'candles', priceStep: '50', priceOffset: -21,
      rangeSeconds: 30, showTrades: false, showQuotes: false, paused: false } });
  const range = { start_ns: '9007199254740993123', end_ns: '9007199264740993124' };
  const enabled = { ...initial, rangeLinked: true };
  const pinned = navigateChartTime(enabled, 'heatmap', { kind: 'pin', range });
  const pinFields = { follow: false, range, followSpanNs: null };
  assert.deepEqual(pinned, { ...enabled, ...pinFields, secondary: { ...enabled.secondary, ...pinFields } });
  const followFields = { follow: true, range: null, followSpanNs: '6000000000', rangeSeconds: 6 };
  const followed = navigateChartTime(pinned, 'flow', { kind: 'follow', span_ns: '6000000000', rangeSeconds: 6 });
  assert.deepEqual(followed, { ...pinned, ...followFields, secondary: { ...pinned.secondary, ...followFields } });
  const disabled = { ...followed, rangeLinked: false };
  const independent = navigateChartTime(disabled, 'flow', { kind: 'follow', span_ns: '12345678901' });
  assert.deepEqual(independent, { ...disabled, secondary: { ...disabled.secondary, followSpanNs: '12345678901' } });
});

test('time intents reject malformed coordinates and durations rather than treating an absolute clock as a width', () => {
  const initial = validatePreferences({ version: 1, rangeLinked: true, paused: true });
  for (const span of ['0', '-1', '1.5', '1e9', '', 'NaN', '9007199254740993123', '1800000000001']) {
    assert.equal(chartFollowSpan(span), null);
    assert.equal(navigateChartTime(initial, 'heatmap', { kind: 'follow', span_ns: span }), initial);
  }
  assert.equal(chartFollowSpan('000000001'), '1');
  assert.equal(chartFollowSpan('1800000000000'), '1800000000000');
  for (const range of [
    { start_ns: '9', end_ns: '9' }, { start_ns: '10', end_ns: '9' }, { start_ns: '-1', end_ns: '9' },
    { start_ns: '1e3', end_ns: '2000' }, { start_ns: '0', end_ns: '1800000000001' },
  ]) assert.equal(navigateChartTime(initial, 'flow', { kind: 'pin', range }), initial);
  assert.equal(navigateChartTime(initial, 'flow', { kind: 'follow', span_ns: '1', rangeSeconds: 7 }), initial);
});

test('custom exact follow spans and time-link choice survive presets, serialization and source-independent slots', () => {
  const saved = validatePreferences({ version: 1, rangeLinked: true, followSpanNs: '12345678901',
    secondary: { followSpanNs: '9876543210', rangeSeconds: 30 }, links: { heatmap: false, flow: true } });
  const reloaded = validatePreferences(JSON.parse(JSON.stringify({ ...saved, grid: gridPreset('execution') })));
  assert.equal(reloaded.followSpanNs, '12345678901');
  assert.equal(reloaded.secondary.followSpanNs, '9876543210');
  assert.equal(reloaded.rangeLinked, true);
  assert.deepEqual(reloaded.links, saved.links);
  const current = validatePreferences({ version: 1, source: { mode: 'live', id: 'different-native-clock' } });
  const restored = applyLayoutSettings(current, migrateWorkspaceSlots(validateWorkspaceSlots([reloaded]))[0]!.settings);
  assert.equal(restored.followSpanNs, saved.followSpanNs);
  assert.equal(restored.secondary.followSpanNs, saved.secondary.followSpanNs);
  assert.equal(restored.rangeLinked, true);
  assert.deepEqual(restored.source, current.source);
  const invalid = validatePreferences({ ...saved, followSpanNs: '9007199254740993123', secondary: { ...saved.secondary, followSpanNs: '-1' } });
  assert.equal(invalid.followSpanNs, null);
  assert.equal(invalid.secondary.followSpanNs, null);
  const pinned = validatePreferences({ ...saved, follow: false, range: { start_ns: '1', end_ns: '3' } });
  assert.equal(pinned.followSpanNs, null);
  assert.deepEqual(pinned.range, { start_ns: '1', end_ns: '3' });
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
    time_axis: { version: '1', basis: 'receive_mono_highwater', projection: 'piecewise_receive_pair_v1', source_identity: 'source',
      clock_id: 'clock', scope_id: null, available_start_ns: null, available_end_ns: null, offset_change_threshold_ns: '1000000',
      segments: [], last_unapplied_pair: null, reasons: ['clock_time_unavailable'] },
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

test('Fit time uses actual retained depth, not a requested window or receive-clock projection coverage', () => {
  const view = grid([], { start_ns: '9000000000000000000', end_ns: '9000000001000000000',
    retained_start_ns: '9000000000123456789', now_ns: '9000000001987654321' });
  assert.deepEqual(retainedChartRange(view, 'heatmap'), { start_ns: '9000000000123456789', end_ns: '9000000001987654321' });
  const noDepth = { ...view, retained_start_ns: null, time_axis: { ...view.time_axis,
    available_start_ns: '9000000000000000000', available_end_ns: '9000000001000000000' } };
  assert.equal(retainedChartRange(noDepth, 'heatmap'), null);
  assert.equal(retainedChartRange(noDepth, 'footprint'), null);
  const capped = grid([], { retained_start_ns: '1', now_ns: '1800000000100' });
  assert.deepEqual(retainedChartRange(capped, 'heatmap'), { start_ns: '100', end_ns: '1800000000100' });
});

test('flow Fit time bounds actual native aggregates without filling absent history or extending beyond source now', () => {
  const view = grid([], { start_ns: '9007199254740993123', end_ns: '9007199259740993123',
    now_ns: '9007199258740993123', retained_start_ns: '1', bucket_ns: '1000000000',
    footprints: [{ time_ns: '9007199255000000000', price: '0', buy_base: '1', sell_base: '0', unknown_base: '0' },
      { time_ns: '9007199258000000000', price: '0', buy_base: '0', sell_base: '2', unknown_base: '0' }] });
  assert.deepEqual(retainedChartRange(view, 'footprint'), { start_ns: '9007199255000000000', end_ns: '9007199258740993123' });
  assert.equal(retainedChartRange({ ...view, footprints: [] }, 'footprint'), null);
  assert.equal(retainedChartRange(view, 'candles'), null);
  assert.deepEqual(retainedChartRange(view, 'profile'), retainedChartRange(view, 'footprint'));
});

test('v2 price preferences migrate independently of exact source identity and unsupported historical interval choices', () => {
  const legacy = validatePreferences({ version: 1, source: { mode: 'replay', id: 'capture/exact' }, sourceExplicit: true,
    instrument: 'venue/native', candleInterval: '1M', domUnit: 'quote', nativeView: 'price' });
  assert.equal(legacy.version, 2);
  assert.deepEqual(legacy.priceChart, { source: 'auto', scale: 'linear', autoScale: true, rightOffset: 5, positioning: false });
  for (const source of ['auto', 'session_trades', 'venue_ohlcv'] as const) {
    const saved = validatePreferences({ ...legacy, priceChart: { source, scale: 'log', autoScale: false, rightOffset: 17, positioning: true } });
    const reloaded = validatePreferences(JSON.parse(JSON.stringify(saved)));
    assert.deepEqual(reloaded.priceChart, saved.priceChart);
    assert.deepEqual(reloaded.source, legacy.source);
    assert.equal(reloaded.sourceExplicit, true);
    assert.equal(reloaded.instrument, 'venue/native');
    assert.equal(reloaded.candleInterval, '1M');
    assert.equal(reloaded.domUnit, 'quote');
  }
  const invalid = validatePreferences({ ...legacy, priceChart: { source: 'other', scale: 'symlog', autoScale: 'false', rightOffset: Infinity, positioning: 1 } });
  assert.deepEqual(invalid.priceChart, legacy.priceChart);
  assert.equal(validatePreferences({ ...legacy, priceChart: { rightOffset: -2 } }).priceChart.rightOffset, 0);
  assert.equal(validatePreferences({ ...legacy, priceChart: { rightOffset: 1e6 } }).priceChart.rightOffset, 50);
});

test('five presets have unique mounted panels, collision-free geometry and bounded minimum sizes', () => {
  assert.equal(Object.keys(GRID_PRESETS).length, 5);
  for (const id of Object.keys(GRID_PRESETS) as GridPresetId[]) {
    const layout = gridPreset(id);
    assert.equal(layout.cells.filter(item => item.id === 'chart').length, 1);
    assert.equal(new Set(layout.cells.map(item => item.id)).size, layout.cells.length);
    assert.deepEqual(validateGrid(layout), { cells: gridReadingOrder(layout) });
    for (const item of layout.cells) {
      assert.ok(item.w >= PANEL_MINIMUM[item.id].w && item.h >= PANEL_MINIMUM[item.id].h);
      assert.ok(item.x >= 0 && item.y >= 0 && item.x + item.w <= GRID_COLUMNS && item.y + item.h <= GRID_ROWS);
      assert.ok(!layout.cells.some(other => other !== item && overlaps(item, other)));
    }
    layout.cells[0]!.x = 11;
    assert.equal(gridPreset(id).cells[0]!.x, 0, 'preset templates are never mutated by editing');
  }
});

test('desktop row sizing fits a fixed logical page as available surface space changes', () => {
  const gap = 6;
  for (const height of [420, 490, 768]) {
    const row = gridRowHeight(height, gap);
    const extent = (rows: number) => rows * row + (rows - 1) * gap;
    assert.ok(Math.abs(extent(GRID_BASE_ROWS) - height) < 0.001);
    for (const id of Object.keys(GRID_PRESETS) as GridPresetId[]) {
      const rows = Math.max(...gridPreset(id).cells.map(item => item.y + item.h));
      assert.ok(extent(rows) <= height + 0.001, `${id} fits the desktop page`);
    }
    const price = gridPreset('price');
    assert.ok(Math.abs(extent(price.cells[0]!.h) - height) < 0.001, 'Price focus fills the available page');
    const smaller = changeGrid(price, { kind: 'resize', id: 'chart', dx: 0, dy: -1 });
    assert.equal(smaller.error, null);
    assert.ok(extent(smaller.layout.cells[0]!.h) < height, 'resizing a lone panel does not refit its extent');
    const larger = changeGrid(price, { kind: 'resize', id: 'chart', dx: 0, dy: 1 });
    assert.equal(larger.error, null);
    assert.ok(extent(larger.layout.cells[0]!.h) > height, 'custom layouts can deliberately overflow');
    assert.deepEqual(validateGrid(JSON.parse(JSON.stringify(larger.layout))), larger.layout);
  }
  assert.ok(gridRowHeight(420, gap) < gridRowHeight(490, gap), 'expanded source controls reduce row pixels');
  assert.equal(gridRowHeight(0, gap), GRID_MIN_ROW_HEIGHT);
  assert.equal(gridRowHeight(200, gap), GRID_MIN_ROW_HEIGHT, 'short viewports scroll instead of collapsing panels');
  assert.equal(gridRowHeight(480, 0), 40, 'row gaps are accounted for independently of panel geometry');
});

test('persisted grid geometry rejects foreign panels, clamps minimum sizes and relocates collisions deterministically', () => {
  const dirty = { cells: [
    { id: 'chart', x: -99, y: 0, w: -2, h: 2 },
    { id: 'chart', x: 8, y: 1, w: 2, h: 2 },
    { id: 'orderBook', x: 0, y: 0, w: 3, h: 4 },
    { id: 'foreign', x: 0, y: 0, w: 1, h: 1 },
    { id: 'cvd', x: Infinity, y: -3, w: NaN, h: Infinity },
  ] };
  const safe = validateGrid(dirty);
  assert.equal(safe.cells.length, 3);
  assert.deepEqual(safe.cells.find(item => item.id === 'chart'), { id: 'chart', x: 0, y: 0, w: 6, h: 8 });
  for (const item of safe.cells) assert.ok(!safe.cells.some(other => other !== item && overlaps(item, other)));
  assert.deepEqual(validateGrid(JSON.parse(JSON.stringify(safe))), safe);
  assert.deepEqual(validateGrid({ cells: [] }), { cells: [] }, 'intentionally empty workspaces stay empty');
});

test('drag and keyboard geometry operations reject collisions without moving other panels and persist valid changes', () => {
  const initial = gridPreset('balanced');
  const colliding = changeGrid(initial, { kind: 'move', id: 'orderBook', dx: -1, dy: 0 });
  assert.match(colliding.error!, /overlap/);
  assert.equal(colliding.layout, initial);
  const minimum = changeGrid(initial, { kind: 'resize', id: 'chart', dx: 0, dy: -1 });
  assert.match(minimum.error!, /minimum/);
  assert.equal(minimum.layout, initial);
  const invalid = changeGrid(initial, { kind: 'move', id: 'chart', dx: NaN, dy: 1 });
  assert.equal(invalid.layout, initial);
  const moved = changeGrid(initial, { kind: 'move', id: 'volumeZ', dx: 0, dy: 4 });
  assert.equal(moved.error, null);
  assert.deepEqual(moved.layout.cells.find(item => item.id === 'volumeZ'), { id: 'volumeZ', x: 4, y: 12, w: 5, h: 3 });
  const resized = changeGrid(moved.layout, { kind: 'resize', id: 'volumeZ', dx: 2, dy: 1 });
  assert.equal(resized.error, null);
  assert.deepEqual(validateGrid(JSON.parse(JSON.stringify(resized.layout))), resized.layout);
  for (const item of initial.cells.filter(item => item.id !== 'volumeZ')) assert.deepEqual(resized.layout.cells.find(other => other.id === item.id), item);
  const removed = changeGrid(resized.layout, { kind: 'remove', id: 'volumeZ' });
  assert.equal(removed.layout.cells.some(item => item.id === 'volumeZ'), false);
  const added = changeGrid(removed.layout, { kind: 'add', id: 'volumeZ' });
  assert.equal(added.error, null);
  assert.equal(added.layout.cells.filter(item => item.id === 'volumeZ').length, 1);
  assert.match(changeGrid(added.layout, { kind: 'add', id: 'volumeZ' }).error!, /already/);
  assert.match(changeGrid({ cells: [{ id: 'chart', x: 0, y: 0, w: 12, h: 48 }] }, { kind: 'add', id: 'cvd' }).error!, /No room/);
});

test('narrow screens use the same spatial reading order, and legacy shell visibility has one migrated owner', () => {
  const layout = migrateShellGrid({ orderBook: false, obi: false, timeSales: true, cvd: false, volumeZ: false, volatility: true, depthOfMarket: true }, 420, 300);
  assert.equal(layout.cells.some(item => item.id === 'orderBook' || item.id === 'obi' || item.id === 'cvd' || item.id === 'volumeZ'), false);
  assert.equal(layout.cells.find(item => item.id === 'chart')!.w, 8);
  for (const id of ['chart', 'timeSales', 'volatility', 'depthOfMarket']) assert.ok(layout.cells.some(item => item.id === id));
  const ordered = gridReadingOrder({ cells: [...layout.cells].reverse() });
  assert.deepEqual(ordered, layout.cells);
  assert.equal(ordered[0]!.id, 'chart');
  assert.ok(ordered.every(item => PANEL_IDS.includes(item.id)));
});

test('anonymous workspace migration retains pane choices and exposes bounded named source-free layouts', () => {
  const old = validatePreferences({ version: 1, source: { mode: 'live', id: 'foreign-live' }, instrument: 'foreign/native', rightWidth: 420,
    candleInterval: '3d', nativeView: 'orderflow', chartSplit: 61, secondary: { mode: 'candles', priceStep: '0.0001' }, priceChart: { source: 'venue_ohlcv', scale: 'log' } });
  const migrated = migrateWorkspaceSlots(validateWorkspaceSlots([old, null, old]));
  assert.deepEqual(migrated.map(item => item.name), ['Workspace 1', 'Workspace 3']);
  assert.equal(migrated[0]!.settings.candleInterval, '3d');
  assert.equal(migrated[0]!.settings.chartSplit, 61);
  assert.equal(migrated[0]!.settings.secondary.priceStep, '0.0001');
  assert.equal(migrated[0]!.settings.grid.cells.find(item => item.id === 'chart')!.w, 8);
  assert.equal('source' in migrated[0]!.settings, false);
  assert.equal('instrument' in migrated[0]!.settings, false);
  assert.ok(!JSON.stringify(migrated).includes('foreign-live'));
  const restored = validatePreferences({ version: 2, layouts: migrated });
  assert.deepEqual(restored.layouts, migrated);
});

test('named layouts save load rename delete without restoring market identity, cohort, source choice or replay clock', () => {
  const old = validatePreferences({ version: 2, candleInterval: '4h', grid: gridPreset('price'), priceChart: { source: 'venue_ohlcv', scale: 'log', positioning: true } });
  const saved = changeWorkspaceLayout(old, { kind: 'save', name: '  Price study  ' });
  assert.equal(saved.error, null);
  assert.equal(saved.preferences.layouts[0]!.name, 'Price study');
  assert.equal('range' in saved.preferences.layouts[0]!.settings, false);
  const range = { start_ns: '9007199254740993123', end_ns: '9007199255740993123' };
  const current = { ...validatePreferences({ ...saved.preferences, source: { mode: 'replay', id: 'active-capture' }, sourceExplicit: true,
    instrument: 'selected/exact', range, follow: false, paused: true, domFrozen: true, tapeFrozen: true,
    secondary: { range, follow: false, paused: true }, priceChart: { source: 'session_trades' } }),
    cohort: ['selected/exact', 'related/exact'], replay_cursor: '9007199255740993123', session_id: 'active-only' };
  const loaded = changeWorkspaceLayout(current, { kind: 'load', name: 'Price study' });
  assert.equal(loaded.error, null);
  assert.deepEqual(loaded.preferences.source, current.source);
  assert.equal(loaded.preferences.sourceExplicit, true);
  assert.equal(loaded.preferences.instrument, current.instrument);
  assert.equal(loaded.preferences.priceChart.source, 'session_trades');
  assert.equal(loaded.preferences.priceChart.scale, 'log');
  assert.equal(loaded.preferences.candleInterval, '4h');
  assert.equal(loaded.preferences.range, current.range);
  assert.equal(loaded.preferences.secondary.range, current.secondary.range);
  assert.equal(loaded.preferences.paused, true);
  assert.equal(loaded.preferences.domFrozen, true);
  assert.equal(loaded.preferences.tapeFrozen, true);
  assert.equal((loaded.preferences as typeof current).cohort, current.cohort);
  assert.equal((loaded.preferences as typeof current).replay_cursor, current.replay_cursor);
  assert.equal((loaded.preferences as typeof current).session_id, current.session_id);
  const contaminated = { ...layoutSettings(old), source: old.source, instrument: 'bad', session_id: 'bad', replay_cursor: 'bad', cohort: ['bad'] };
  assert.equal((applyLayoutSettings(current, contaminated) as typeof current).session_id, current.session_id);
  assert.equal((applyLayoutSettings(current, contaminated) as typeof current).cohort, current.cohort);
  const renamed = changeWorkspaceLayout(loaded.preferences, { kind: 'rename', name: 'Price study', nextName: 'Replay study' });
  assert.equal(renamed.preferences.layouts[0]!.name, 'Replay study');
  const deleted = changeWorkspaceLayout(renamed.preferences, { kind: 'delete', name: 'Replay study' });
  assert.equal(deleted.preferences.layouts.length, 0);
  assert.equal(deleted.preferences.source, current.source);
  assert.equal(deleted.preferences.range, current.range);
});

test('layout names and count are bounded and malformed saved settings never smuggle identity fields', () => {
  for (const name of ['', ' ', 'x'.repeat(41), 'bad\nname', '\u0000', null, 1]) assert.equal(layoutName(name), null);
  let preferences = validatePreferences(null);
  for (let index = 0; index < MAX_LAYOUTS; index++) {
    const result = changeWorkspaceLayout(preferences, { kind: 'save', name: `Study ${index}` });
    assert.equal(result.error, null);
    preferences = result.preferences;
  }
  assert.match(changeWorkspaceLayout(preferences, { kind: 'save', name: 'Extra' }).error!, /At most/);
  assert.match(changeWorkspaceLayout(preferences, { kind: 'save', name: 'study 0' }).error!, /already/);
  assert.match(changeWorkspaceLayout(preferences, { kind: 'rename', name: 'Study 1', nextName: 'STUDY 0' }).error!, /already/);
  assert.match(changeWorkspaceLayout(preferences, { kind: 'load', name: 'Missing' }).error!, /Choose/);
  const reloaded = validatePreferences({ ...preferences, layouts: [
    { name: 'Valid', settings: { source: { mode: 'live', id: 'foreign' }, instrument: 'foreign', session_id: 'foreign', grid: { cells: [] }, candleInterval: '1w' } },
    { name: 'valid', settings: {} }, { name: 'bad\nname', settings: {} }, ...preferences.layouts,
  ] });
  assert.ok(reloaded.layouts.length <= MAX_LAYOUTS);
  assert.equal(reloaded.layouts.filter(item => item.name.toLowerCase() === 'valid').length, 1);
  assert.ok(!JSON.stringify(reloaded.layouts).includes('foreign'));
  assert.equal(reloaded.layouts[0]!.settings.candleInterval, '1w');
  assert.deepEqual(reloaded.layouts[0]!.settings.grid, { cells: [] });
});

test('browser preference loading migrates legacy shell visibility and anonymous slots once, independently of corrupt main JSON', (t) => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  const storage = new Map<string, string>();
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { getItem: (key: string) => storage.get(key) ?? null } });
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, 'localStorage', previous);
    else Reflect.deleteProperty(globalThis, 'localStorage');
  });
  storage.set(preferenceKey, '{corrupt');
  storage.set('limit-orderbook.orderflow.ui-preferences', JSON.stringify({ version: 2, state: { toolProfile: 'volatility',
    panels: { orderBook: false, obi: false, timeSales: true, cvd: false, volumeZ: true, volatility: true, depthOfMarket: false } } }));
  storage.set(workspaceSlotsKey, JSON.stringify([{ version: 1, candleInterval: '1w', source: { mode: 'live', id: 'old-live' }, instrument: 'old/native' }]));
  const migrated = loadPreferences();
  assert.equal(migrated.version, 2);
  assert.equal(migrated.layouts[0]!.name, 'Workspace 1');
  assert.equal(migrated.layouts[0]!.settings.candleInterval, '1w');
  assert.equal(migrated.grid.cells.some(item => item.id === 'orderBook'), false);
  assert.equal(migrated.grid.cells.some(item => item.id === 'volatility'), true);
  assert.equal(migrated.source, null);
  assert.equal(migrated.instrument, '');
  storage.set(preferenceKey, JSON.stringify({ ...migrated, layouts: [], grid: { cells: [] } }));
  const current = loadPreferences();
  assert.deepEqual(current.layouts, [], 'deleted migrated layouts never resurrect from retired storage');
  assert.deepEqual(current.grid, { cells: [] }, 'explicit empty grids never reload old shell visibility');
});
