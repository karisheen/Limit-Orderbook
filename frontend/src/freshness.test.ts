import { test } from 'node:test';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import type { Frame, PlotTrade, Reference, VisualColumn, VisualGrid } from './api.ts';
import { request } from './api.ts';

// Static imports run before this hook; load freshness after installing the
// bundler-resolution boundary required by Node's strip-types test runner.
registerHooks({
  resolve(specifier, context, nextResolve) {
    return nextResolve(specifier === './market-charts-model' ? './market-charts-model.ts' : specifier, context);
  },
});
const { browserResidencyMs, chartEvidence, chartLag, evidenceIdentity, referenceLag, sourceClockLag, sourceFrontier } = await import('./data-freshness.ts');

const reference = (mono = '1000000000', clock = 'recorded-clock'): Reference => ({
  session_id: 'source-session', ingest_ordinal: '1', event_index: '0', clock_id: clock,
  receive_mono_ns: mono, receive_wall_ns: '946684800000000000', exchange_event_wall_ns: null,
});

function frame(asof = reference(), mode: Frame['source']['mode'] = 'replay'): Frame {
  return {
    version: '1', type: 'frame', frame_id: '1', session_id: 'browser-session', generation: '3', status: 'playing',
    selection: { instrument_id: 'BTC' }, source: { id: mode, mode, label: mode, status: 'ready', synthetic: false,
      recording: false, duration_ns: null, streams: 1, reasons: [] },
    book: { instrument_id: 'BTC', bids: [], asks: [], asof, revision: '1', presentation: { depth_limit: 10, bids_omitted: 0, asks_omitted: 0 },
      quality: { lifecycle: 'live', integrity: 'valid', source_mode: mode, recorded_source_mode: null, reason_codes: [],
        capture_complete: true, coverage: { subscribed_depth: null, retained_bid_levels: '0', retained_ask_levels: '0', full_depth: false } } },
    analytics: null, replay: null, health: { ready: true, reasons: [] }, error: null,
    telemetry: { published_mono_ns: '999999999999999999', queue_depth: 0, queue_highwater: 0, coalesced: 0, overflows: 0,
      ack_expired: 0, ingestion: [], observer_duration_ms: null, queue_wait_ms: null,
      publish_to_ack_upper_bound_ms: null, browser_receive_to_paint_ms: null },
  };
}

function column(asof = reference(), time = '1000000000'): VisualColumn {
  return { id: time, time_ns: time, end_ns: (BigInt(time) + 1_000_000_000n).toString(), segment_id: 'segment',
    asof, eligible: true, reasons: [], liquidity_hash: 'hash', cells: [], best_bid: null, best_ask: null,
    cvd: null, ofi: null, candle: null };
}

function grid(columns = [column()]): VisualGrid {
  return { version: '1', generation: '3', grid_id: 'grid', instrument_id: 'BTC', clock_id: 'recorded-clock',
    view_id: 'view-1', published_mono_ns: '88888888888888888', quantity_unit: 'base', sampling: 'sampled',
    time_axis: { version: '1', basis: 'receive_mono_highwater', projection: 'piecewise_receive_pair_v1',
      source_identity: 'source-session', clock_id: 'recorded-clock', scope_id: null,
      available_start_ns: null, available_end_ns: null, offset_change_threshold_ns: '1000000',
      segments: [], last_unapplied_pair: null, reasons: ['receive_clock_unavailable'] },
    price_step: '1', price_min: '0', prices: [], rows: 0, bucket_ns: '1000000000', start_ns: '0',
    end_ns: '3000000000', now_ns: '3000000000', retained_start_ns: '0', step_options: [], columns,
    trades: [], profile: [], footprints: [], reasons: [], truncated: false, complete_window: true, replace: true, next_after: null };
}

test('native source lag preserves large exact clock boundaries and submillisecond precision', () => {
  const result = sourceClockLag({ clock_id: 'a', mono_ns: '9007199254740993000000001' }, [
    { clock_id: 'a', mono_ns: '9007199254740993001500001' },
    { clock_id: 'unrelated', mono_ns: '9999999999999999999999999' },
  ]);
  assert.equal(result.ms, 1.5);
  assert.equal(result.highwaterNs, '9007199254740993001500001');
});

test('mismatched, missing, invalid and regressing clocks are unavailable, not fresh zero', () => {
  const point = { clock_id: 'a', mono_ns: '100' };
  assert.match(sourceClockLag(point, [{ clock_id: 'b', mono_ns: '200' }]).reason!, /mismatch/);
  assert.equal(sourceClockLag(point, []).ms, null);
  assert.match(sourceClockLag(point, [{ clock_id: 'a', mono_ns: '99' }]).reason!, /skew/);
  assert.equal(sourceClockLag({ ...point, clock_id: '' }, [point]).ms, null);
  assert.equal(sourceClockLag({ ...point, mono_ns: '-1' }, [point]).ms, null);
  assert.equal(sourceClockLag(point, [{ ...point, mono_ns: 'NaN' }]).ms, null);
  assert.equal(sourceClockLag(point, [point]).ms, 0);
});

test('replay age uses original source clock, not old recording wall date or current publication time', () => {
  const original = reference();
  const next = frame(reference('4000000000'));
  assert.notEqual(original.session_id, next.session_id);
  assert.equal(referenceLag(next, original, true).ms, 3000);
  next.telemetry.published_mono_ns = '1';
  original.receive_wall_ns = '0';
  assert.equal(referenceLag(next, original, true).ms, 3000);
});

test('frozen chart evidence falls behind advancing frames despite unchanged displayed response', () => {
  const frozen = grid();
  assert.equal(chartLag(frame(reference('3000000000')), frozen, true).ms, 2000);
  assert.equal(chartLag(frame(reference('9000000000')), frozen, true).ms, 8000);
  assert.equal(frozen.view_id, 'view-1');
  assert.equal(browserResidencyMs(9000, 1000), 8000);
});

test('new frames and new query IDs do not rejuvenate retained evidence identity', () => {
  const first = frame();
  const next = { ...first, frame_id: '99', book: { ...first.book!, asof: { ...first.book!.asof } } };
  const before = grid(), after = { ...grid(), view_id: 'view-99' };
  assert.equal(evidenceIdentity(first.book!.asof), evidenceIdentity(next.book.asof));
  assert.equal(evidenceIdentity(chartEvidence(before).reference), evidenceIdentity(chartEvidence(after).reference));
  assert.equal(browserResidencyMs(3000, 500), 2500);
  assert.equal(browserResidencyMs(6000, 500), 5500);
  assert.notEqual(evidenceIdentity(reference()), evidenceIdentity({ ...reference(), ingest_ordinal: '2' }));
});

test('browser residence is independently unavailable for missing or skewed local observations', () => {
  assert.equal(browserResidencyMs(100, null), null);
  assert.equal(browserResidencyMs(100, 101), null);
  assert.equal(browserResidencyMs(Number.NaN, 0), null);
  assert.equal(browserResidencyMs(0, -1), null);
  assert.equal(browserResidencyMs(100, 100), 0);
});

test('latest eligible visible column, not newest response or an outside-window column, supplies chart evidence', () => {
  const early = column(reference(), '1000000000');
  const latest = column(reference('2000000000'), '2000000000');
  const outside = column(reference('3000000000'), '3000000000');
  const view = grid([outside, early, latest]);
  assert.equal(chartEvidence(view).reference?.receive_mono_ns, '2000000000');
  latest.eligible = false;
  assert.equal(chartEvidence(view).reference?.receive_mono_ns, '1000000000');
  early.eligible = false;
  assert.equal(chartEvidence(view).reference, null);
});

test('chart clocks, source bounds and active identity must match exactly', () => {
  const view = grid();
  assert.match(chartEvidence({ ...view, clock_id: 'different' }).reason!, /mismatch/);
  assert.equal(chartEvidence({ ...view, columns: [column(reference('4000000000'))] }).reference, null);
  assert.equal(chartEvidence({ ...view, start_ns: view.end_ns }).reference, null);
  assert.equal(chartLag(frame(), { ...view, generation: 'old' }, true).ms, null);
  assert.equal(chartLag(frame(), { ...view, instrument_id: 'ETH' }, true).ms, null);
  assert.match(chartLag(frame(reference('9000000000', 'new-clock')), view, true).reason!, /mismatch/);
});

test('disconnection and unavailable sources never advertise a zero lag', () => {
  assert.match(referenceLag(frame(), reference(), false).reason!, /disconnected/);
  assert.equal(chartLag(frame(), grid(), false).ms, null);
  assert.equal(referenceLag({ ...frame(), status: 'unavailable' }, reference(), true).ms, null);
  assert.equal(referenceLag(frame(), null, true).ms, null);
});

test('same-clock analytics evaluation advances stale live evidence; unnamed frame publication does not', () => {
  const live = frame(reference(), 'live');
  live.analytics = { instrument_id: 'BTC', tape: [], profile: [], display_evidence: [], reasons: [],
    presentation: { profile_limit: 0, profile_omitted: 0, evidence_limit: 0, evidence_omitted: 0 },
    metrics: [{ name: 'metric', value: null, unit: '', instrument_id: 'BTC', venue: '', asof: reference(),
      eligibility: { eligible: false, reasons: [] }, formula_version: '', config_hash: '', quality: null, references: [],
      window: { clock_id: 'recorded-clock', end_mono_ns: '8000000000' } }] };
  assert.equal(referenceLag(live, reference(), true).ms, 7000);
  live.analytics.metrics[0]!.window.clock_id = 'other-clock';
  assert.equal(referenceLag(live, reference(), true).ms, 0);
});

test('paused replay source lag stays in recording time while local frozen residence advances', () => {
  const paused = { ...frame(reference('3000000000')), status: 'paused' as const };
  const view = grid();
  assert.equal(chartLag(paused, view, true).ms, 2000);
  assert.equal(browserResidencyMs(20_000, 1000), 19_000);
  assert.equal(chartLag({ ...paused, frame_id: '100' }, view, true).ms, 2000);
  assert.equal(browserResidencyMs(30_000, 1000), 29_000);
});

test('live processing highwater is clock-qualified and never borrowed by replay', () => {
  const live = frame(reference(), 'live');
  live.telemetry.ingestion = [{
    key: { venue: 'venue', api_family: 'spot', instrument_id: 'BTC', channel: 'depth', depth: null, aggregation: [] },
    stream_id: 'stream', reference_count: '1', lifecycle: 'live', connection_epoch: '1',
    queue_depth: '0', queue_bytes: '0', reason: '', capture_complete: true, processed_frames: '1',
    queue_highwater_count: '0', queue_highwater_bytes: '0',
    last_processing: { clock_id: 'recorded-clock', ingest_ordinal: '2', receive_mono_ns: '2000000000',
      queue_exit_mono_ns: '2000000001', decode_start_mono_ns: '2000000002', decode_end_mono_ns: '2000000003',
      validation_end_mono_ns: '2000000004', normalized_mono_ns: '2000000005', publish_mono_ns: '3000000000' },
  }];
  assert.equal(referenceLag(live, reference(), true).ms, 2000);
  const replay = { ...live, source: { ...live.source, mode: 'replay' as const } };
  assert.equal(referenceLag(replay, reference(), true).ms, 0);
  live.telemetry.ingestion[0]!.last_processing!.clock_id = 'other-clock';
  assert.equal(referenceLag(live, reference(), true).ms, 0);
  live.telemetry.ingestion[0]!.last_processing!.clock_id = 'recorded-clock';
  live.telemetry.ingestion[0]!.key.instrument_id = 'ETH';
  assert.equal(referenceLag(live, reference(), true).ms, 0);
});

test('follow frontier includes quiet same-clock evaluation intervals and rejects other clocks and instruments', () => {
  const current = frame();
  current.analytics = { instrument_id: 'BTC', tape: [], profile: [], display_evidence: [], reasons: [],
    presentation: { profile_limit: 0, profile_omitted: 0, evidence_limit: 0, evidence_omitted: 0 },
    metrics: [{ name: 'metric', value: null, unit: '', instrument_id: 'BTC', venue: '', asof: reference(),
      eligibility: { eligible: false, reasons: [] }, formula_version: '', config_hash: '', quality: null, references: [],
      window: { clock_id: 'recorded-clock', end_mono_ns: '8000000000' } }] };
  assert.equal(sourceFrontier(current, 'recorded-clock'), 8_000_000_000n);
  assert.equal(sourceFrontier(current), 8_000_000_000n);
  current.analytics.metrics.push({ ...current.analytics.metrics[0]!, instrument_id: 'ETH',
    window: { clock_id: 'recorded-clock', end_mono_ns: '9000000000000' } });
  assert.equal(sourceFrontier(current, 'recorded-clock'), 8_000_000_000n);
  current.analytics.metrics[0]!.window.clock_id = 'unrelated';
  assert.equal(sourceFrontier(current, 'recorded-clock'), 1_000_000_000n);
  assert.equal(sourceFrontier(current, 'missing-clock'), null);
});

function trade(mono = '1500000000', price = '10', changes: Partial<PlotTrade> = {}): PlotTrade {
  return { id: mono, time_ns: mono, price, base_quantity: '1', taker_side: 'buy', asof: reference(mono),
    segment_id: 'segment', is_subscription_history: false, ...changes };
}

function flowGrid(): VisualGrid {
  const view = grid([{ ...column(), eligible: false, candle: {
    open: '10', high: '11', low: '10', close: '11', volume: '2', eligible: true, reasons: [],
  } }]);
  return { ...view, price_min: '10', prices: ['10', '11'], rows: 2,
    trades: [trade(), trade('1800000000', '11')],
    footprints: [{ time_ns: '1000000000', price: '10', buy_base: '1', sell_base: '0', unknown_base: '0' },
      { time_ns: '1000000000', price: '11', buy_base: '1', sell_base: '0', unknown_base: '0' }],
    profile: [{ price: '10', buy_base: '1', sell_base: '0', unknown_base: '0' },
      { price: '11', buy_base: '1', sell_base: '0', unknown_base: '0' }] };
}

test('trade-derived modes retain actual painted evidence and residence identity without eligible depth', () => {
  const view = flowGrid();
  for (const mode of ['footprint', 'profile', 'candles'] as const) {
    assert.equal(chartEvidence(view, { mode }).reference?.receive_mono_ns, '1800000000');
    assert.equal(chartLag(frame(reference('3000000000')), view, true, { mode }).ms, 1200);
    assert.equal(evidenceIdentity(chartEvidence(view, { mode }).reference),
      evidenceIdentity(chartEvidence({ ...view, view_id: 'new-response' }, { mode }).reference));
  }
  assert.equal(chartEvidence(view).reference, null);
});

test('flow evidence excludes unpainted aggregates, history, unknown size, wrong clocks and outside bounds', () => {
  for (const mode of ['footprint', 'profile', 'candles'] as const) {
    const view = flowGrid();
    view.trades = [trade(), trade('1900000000', '10', { is_subscription_history: true }),
      trade('2000000000', '10', { base_quantity: null }), trade('2100000000', '10', { asof: reference('2100000000', 'foreign') }),
      trade('3000000000'), trade('2200000000', '30')];
    assert.equal(chartEvidence(view, { mode }).reference?.receive_mono_ns, '1500000000');
    view.trades = [trade('1500000000', '10', { asof: reference('4000000000') })];
    assert.equal(chartEvidence(view, { mode }).reference, null);
    view.trades = [trade()];
    view.footprints = [];
    view.profile = [];
    view.columns[0]!.candle!.eligible = false;
    assert.equal(chartEvidence(view, { mode }).reference, null);
  }
});

test('footprint and candle evidence must belong to the rendered bucket and segment', () => {
  const view = flowGrid();
  view.trades.push(trade('2500000000'));
  assert.equal(chartEvidence(view, { mode: 'footprint' }).reference?.receive_mono_ns, '1800000000');
  assert.equal(chartEvidence(view, { mode: 'candles' }).reference?.receive_mono_ns, '1800000000');
  view.trades = [trade('1500000000', '10', { segment_id: 'unpainted' })];
  assert.equal(chartEvidence(view, { mode: 'candles' }).reference, null);
});

test('newer quote and trade overlays never rejuvenate primary-mode evidence', () => {
  const view = flowGrid();
  view.columns[0]!.eligible = true;
  view.columns[0]!.best_bid = '10';
  assert.equal(chartEvidence(view, { mode: 'heatmap' }).reference?.receive_mono_ns, '1000000000');
  assert.equal(chartLag(frame(reference('3000000000')), view, true, { mode: 'heatmap' }).ms, 2000);
  view.columns[0]!.eligible = false;
  assert.equal(chartEvidence(view, { mode: 'heatmap' }).reference, null);
  assert.equal(chartLag(frame(reference('3000000000')), view, true, { mode: 'heatmap' }).ms, null);
  view.columns[0]!.eligible = true;
  view.columns[0]!.asof = reference('1900000000');
  view.trades.push(trade('2000000000', '10', { is_subscription_history: true }));
  for (const mode of ['footprint', 'profile', 'candles'] as const) {
    assert.equal(chartEvidence(view, { mode }).reference?.receive_mono_ns, '1800000000');
  }
  view.footprints = [];
  view.profile = [];
  view.columns[0]!.candle!.eligible = false;
  for (const mode of ['footprint', 'profile', 'candles'] as const) {
    assert.equal(chartEvidence(view, { mode }).reference, null);
  }
});

test('metric charts use their eligible metric references instead of an unrelated book sample', () => {
  const view = flowGrid();
  view.columns[0]!.cvd = { name: 'cvd', value: '2', unit: 'base', instrument_id: 'BTC', venue: '',
    asof: reference('1800000000'), eligibility: { eligible: true, reasons: [] }, formula_version: '',
    config_hash: '', window: {}, quality: null, references: [reference('1500000000')] };
  assert.equal(chartEvidence(view, { mode: 'cvd' }).reference?.receive_mono_ns, '1800000000');
  view.columns[0]!.cvd!.eligibility.eligible = false;
  assert.equal(chartEvidence(view, { mode: 'cvd' }).reference, null);
});

test('API receipt occurs after the complete body and before JSON decoding', async context => {
  const { promise: started, resolve: bodyStarted } = Promise.withResolvers<void>();
  const { promise: text, resolve: releaseBody } = Promise.withResolvers<string>();
  context.mock.method(globalThis, 'fetch', async () => ({
    ok: true, status: 200, text: () => { bodyStarted(); return text; },
  } as Response));
  let receivedAt: number | null = null;
  let decoded = false;
  context.mock.method(JSON, 'parse', () => {
    assert.notEqual(receivedAt, null, 'receipt must precede JSON parsing');
    decoded = true;
    return { complete: true };
  });
  const pending = request('/visualization', undefined, undefined, undefined, time => { receivedAt = time; });
  await started;
  assert.equal(receivedAt, null, 'headers alone are not complete response receipt');
  assert.equal(decoded, false);
  const completedAfter = performance.now();
  releaseBody('{"complete":true}');
  assert.deepEqual(await pending, { complete: true });
  assert.ok(receivedAt !== null && receivedAt >= completedAfter);
  assert.equal(decoded, true);
});

test('a failed body transfer never reports a complete response receipt', async context => {
  context.mock.method(globalThis, 'fetch', async () => ({
    ok: true, status: 200, text: async () => { throw new Error('body transfer interrupted'); },
  } as Response));
  let received = false;
  await assert.rejects(request('/visualization', undefined, undefined, undefined, () => { received = true; }), /body transfer interrupted/);
  assert.equal(received, false);
});

test('render caps and exact native price rows exclude references that were not painted', () => {
  const view = flowGrid();
  view.price_min = '9007199254740993';
  view.prices = ['9007199254740993', '9007199254740994'];
  view.profile = [{ price: view.prices[0]!, buy_base: '1', sell_base: '0', unknown_base: '0' }];
  view.trades = [trade('1500000000', view.prices[0]!), trade('1800000000', '9007199254740992')];
  assert.equal(chartEvidence(view, { mode: 'profile' }).reference?.receive_mono_ns, '1500000000');
  view.trades = [trade('1500000000', view.prices[0]!), ...Array.from({ length: 2000 }, (_, index) =>
    trade('1800000000', '9007199254740000', { id: `offscreen-${index}`, is_subscription_history: true }))];
  assert.equal(chartEvidence(view, { mode: 'heatmap' }).reference, null);
  assert.equal(chartEvidence(view, { mode: 'profile' }).reference?.receive_mono_ns, '1500000000');
  view.footprints = [{ time_ns: '1000000000', price: view.prices[0]!, buy_base: '1', sell_base: '0', unknown_base: '0' },
    ...Array.from({ length: 12000 }, () => ({ time_ns: '2000000000', price: view.prices[0]!, buy_base: '1', sell_base: '0', unknown_base: '0' }))];
  assert.equal(chartEvidence(view, { mode: 'footprint' }).reference, null);
});

test('an existing chart clock can follow evaluation windows even when current references are unavailable', () => {
  const current = frame();
  current.book = null;
  current.analytics = { instrument_id: 'BTC', tape: [], profile: [], display_evidence: [], reasons: [],
    presentation: { profile_limit: 0, profile_omitted: 0, evidence_limit: 0, evidence_omitted: 0 },
    metrics: [{ name: 'metric', value: null, unit: '', instrument_id: 'BTC', venue: '', asof: null,
      eligibility: { eligible: false, reasons: [] }, formula_version: '', config_hash: '', quality: null, references: [],
      window: { clock_id: 'recorded-clock', end_mono_ns: '8000000000' } }] };
  assert.equal(sourceFrontier(current, 'recorded-clock'), 8_000_000_000n);
  assert.equal(sourceFrontier(current, 'unrelated'), null);
  assert.equal(sourceFrontier(current), null);
});
