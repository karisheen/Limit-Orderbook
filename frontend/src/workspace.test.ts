import { test } from 'node:test';
import assert from 'node:assert/strict';
import { applyWorkspaceSlot, chartFollowSpan, chartSettings, mergeVisualGrid, navigateChartTime, presetLayout, retainedChartRange, validatePreferences, validateWorkspaceSlots, workspaceGeometry } from './workspace.ts';
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
    const applied = applyWorkspaceSlot(validatePreferences({ version: 1 }), slots[0]!);
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
  const preset = validatePreferences({ ...saved, ...presetLayout('analysis') });
  const reloaded = validatePreferences(JSON.parse(JSON.stringify(preset)));
  assert.equal(reloaded.footprintOverview, true);
  assert.equal(reloaded.secondary.footprintOverview, false);
  assert.equal(reloaded.secondary.rangeSeconds, 6);
  assert.equal(reloaded.secondary.showQuotes, true);
  const slots = validateWorkspaceSlots([reloaded]);
  const restored = applyWorkspaceSlot(validatePreferences(null), slots[0]!);
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
  const saved = validatePreferences({ version: 1, ...presetLayout('ladder'), chartSplit: 64, tapeSplit: 70, domRows: 40,
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
  const restored = applyWorkspaceSlot(current, slots[0]!);
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

test('balanced auto geometry gives charts the remainder at desktop evidence sizes', () => {
  const settings = validatePreferences({ version: 1, ...presetLayout('balanced') });
  for (const [viewport, width] of [[1280, 336], [1440, 360], [1920, 420]]) {
    const geometry = workspaceGeometry(settings, viewport);
    assert.equal(geometry.rightWidth, width);
    assert.equal(geometry.min, 320);
    assert.equal(geometry.stacked, false);
    assert.ok(viewport - width - 6 >= 640);
  }
  assert.equal(settings.chartSplit, 52);
  assert.equal(settings.tapeSplit, 60);
  assert.equal(workspaceGeometry(settings, 966).rightWidth, 320);
});

test('legacy numeric widths conservatively migrate to manual, including old default widths', () => {
  for (const rightWidth of [250, 300, 314, 420, 487]) {
    const migrated = validatePreferences({ version: 1, rightWidth });
    assert.equal(migrated.rightWidthMode, 'manual');
    assert.equal(migrated.rightWidth, rightWidth);
    assert.equal(workspaceGeometry(migrated, 1440).rightWidth, rightWidth);
    const reloaded = validatePreferences(JSON.parse(JSON.stringify(migrated)));
    assert.equal(reloaded.rightWidthMode, 'manual');
    assert.equal(reloaded.rightWidth, rightWidth);
    assert.equal(workspaceGeometry(reloaded, 1440).rightWidth, rightWidth);
  }
  for (const rightWidth of [undefined, null, Infinity, '420']) {
    assert.equal(validatePreferences({ version: 1, rightWidth }).rightWidthMode, 'auto');
  }
  const manual = validatePreferences({ version: 1, rightWidth: 487 });
  const reset = validatePreferences({ ...manual, ...presetLayout('balanced') });
  assert.equal(reset.rightWidthMode, 'auto');
  assert.equal(validatePreferences(JSON.parse(JSON.stringify(reset))).rightWidthMode, 'auto');
});

test('analysis retains its manual 300px pane through reload and workspace slots', () => {
  const settings = validatePreferences({ version: 1, ...presetLayout('analysis') });
  const reloaded = validatePreferences(JSON.parse(JSON.stringify(settings)));
  const slots = validateWorkspaceSlots([reloaded]);
  const restored = applyWorkspaceSlot(validatePreferences(null), slots[0]!);
  for (const preferences of [settings, reloaded, restored]) {
    assert.equal(preferences.rightWidthMode, 'manual');
    assert.equal(preferences.rightWidth, 300);
    assert.equal(workspaceGeometry(preferences, 1440).rightWidth, 300);
  }
});

test('manual resize bounds allow narrowing below the automatic minimum down to 250px', () => {
  const settings = validatePreferences({ version: 1, rightWidth: 300, rightWidthMode: 'manual' });
  for (const rightWidth of [300, 290, 260, 250, 500]) {
    const geometry = workspaceGeometry({ ...settings, rightWidth }, 1440);
    assert.equal(geometry.rightWidth, rightWidth);
    assert.equal(geometry.min, 250);
    assert.equal(geometry.max, 500);
  }
  const minimum = workspaceGeometry({ ...settings, rightWidth: 250 }, 1440);
  assert.equal(minimum.rightWidth, minimum.min);
  const maximum = workspaceGeometry({ ...settings, rightWidth: 500 }, 1440);
  assert.equal(maximum.rightWidth, maximum.max);
});

test('a viewport clamp below 320px preserves the hidden manual request for later restoration', () => {
  for (const rightWidth of [300, 500]) {
    const settings = Object.freeze(validatePreferences({ version: 1, rightWidth, leftOpen: true, leftWidth: 222 }));
    const saved = JSON.stringify(settings);
    const clamped = workspaceGeometry(settings, 1150);
    assert.equal(clamped.min, 250);
    assert.equal(clamped.max, 276);
    assert.equal(clamped.rightWidth, 276);
    assert.equal(settings.rightWidth, rightWidth);
    assert.equal(JSON.stringify(settings), saved);
    const reloaded = validatePreferences(JSON.parse(saved));
    assert.equal(workspaceGeometry(reloaded, 1440).rightWidth, rightWidth);
  }
});

test('viewport and watchlist constraints never rewrite requested manual geometry', () => {
  const settings = validatePreferences({ version: 1, rightWidth: 487, leftOpen: true, leftWidth: 222 });
  const saved = JSON.stringify(settings);
  const narrow = workspaceGeometry(settings, 1280);
  assert.equal(narrow.rightWidth, 406);
  assert.ok(1280 - settings.leftWidth - 12 - narrow.rightWidth >= 640);
  assert.equal(workspaceGeometry(settings, 1920).rightWidth, 487);
  assert.equal(workspaceGeometry(settings, 966).rightWidth, 320);
  assert.equal(workspaceGeometry(settings, 965).stacked, true);
  assert.equal(JSON.stringify(settings), saved);
  const slots = validateWorkspaceSlots([settings]);
  const restored = applyWorkspaceSlot(validatePreferences(null), slots[0]!);
  assert.equal(restored.rightWidthMode, 'manual');
  assert.equal(restored.rightWidth, 487);
  assert.equal(workspaceGeometry(restored, 1920).rightWidth, 487);
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
  const reloaded = validatePreferences(JSON.parse(JSON.stringify({ ...saved, ...presetLayout('ladder') })));
  assert.equal(reloaded.followSpanNs, '12345678901');
  assert.equal(reloaded.secondary.followSpanNs, '9876543210');
  assert.equal(reloaded.rangeLinked, true);
  assert.deepEqual(reloaded.links, saved.links);
  const current = validatePreferences({ version: 1, source: { mode: 'live', id: 'different-native-clock' } });
  const restored = applyWorkspaceSlot(current, validateWorkspaceSlots([reloaded])[0]!);
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
