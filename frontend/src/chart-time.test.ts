import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { ReceiveClockSegment, VisualGrid } from './api.ts';
import { chartTimeTicks, chartTimeZoneLabel, formatChartDateContext, formatChartTime, projectReceiveTime } from './chart-time.ts';

const SECOND = 1_000_000_000n;
const MONO = 9_007_199_254_740_993_000_000_001n;
const WALL = 1_704_067_200_000_000_000n; // 2024-01-01T00:00:00Z

function grid(wall = WALL, duration = 20n * SECOND): VisualGrid {
  return {
    version: '1', generation: '1', grid_id: 'grid', instrument_id: 'BTC', clock_id: 'clock',
    view_id: 'view', published_mono_ns: '999', quantity_unit: 'base', sampling: 'sampled',
    price_step: '1', price_min: '0', prices: [], rows: 0, bucket_ns: SECOND.toString(),
    start_ns: MONO.toString(), end_ns: (MONO + duration).toString(), now_ns: (MONO + duration).toString(),
    retained_start_ns: MONO.toString(), step_options: [], columns: [], trades: [], profile: [], footprints: [],
    reasons: [], truncated: false, complete_window: true, replace: true, next_after: null,
    time_axis: {
      version: '1', basis: 'receive_mono_highwater', projection: 'piecewise_receive_pair_v1',
      source_identity: 'recording:session:hash', clock_id: 'clock', scope_id: 'opaque-epoch-1',
      available_start_ns: MONO.toString(), available_end_ns: (MONO + duration).toString(),
      offset_change_threshold_ns: '1000000', last_unapplied_pair: null, reasons: [],
      segments: [{ id: 'pair-1', start_ns: MONO.toString(), cause: 'initial_pair', offset_change_ns: null,
        reference: { session_id: 'session', ingest_ordinal: '1', event_index: '0', clock_id: 'clock',
          receive_wall_ns: wall.toString(), receive_mono_ns: MONO.toString(), exchange_event_wall_ns: null } }],
    },
  };
}

function step(view: VisualGrid, admission: bigint, receive: bigint, wallShift: bigint): ReceiveClockSegment {
  const initial = view.time_axis.segments[0]!.reference;
  const previous = view.time_axis.segments[view.time_axis.segments.length - 1]!.reference;
  const wall = BigInt(initial.receive_wall_ns) + receive - MONO + wallShift;
  const change = wall - receive - (BigInt(previous.receive_wall_ns) - BigInt(previous.receive_mono_ns));
  return {
    id: `step-${admission}`, start_ns: admission.toString(), cause: 'wall_offset_change', offset_change_ns: change.toString(),
    reference: { ...initial, ingest_ordinal: '2', receive_mono_ns: receive.toString(), receive_wall_ns: wall.toString() },
  };
}

test('projection preserves nanoseconds above Number precision and does not depend on viewport', () => {
  const view = grid(WALL + 123n), coordinate = (MONO + SECOND + 7n).toString();
  const expected = (WALL + SECOND + 130n).toString();
  assert.equal(projectReceiveTime(view, coordinate), expected);
  assert.equal(projectReceiveTime({ ...view, start_ns: (MONO + SECOND).toString(), end_ns: (MONO + 2n * SECOND).toString() }, coordinate), expected);
  assert.match(formatChartTime(view, coordinate, 'UTC', true), /00:00:01\.000000130 UTC$/);
});

test('late admission selects the segment without replacing its original receive anchor', () => {
  const view = grid();
  const admission = MONO + 10n * SECOND;
  view.time_axis.segments.push(step(view, admission, MONO + 4n * SECOND, 2n * SECOND));
  assert.equal(projectReceiveTime(view, (admission - 1n).toString()), (WALL + 10n * SECOND - 1n).toString());
  assert.equal(projectReceiveTime(view, admission.toString()), (WALL + 12n * SECOND).toString());
  assert.equal(projectReceiveTime(view, (admission + 1n).toString()), (WALL + 12n * SECOND + 1n).toString());
});

test('coverage, scope and matching clocks are required; publication time cannot supply a map', () => {
  for (const alter of [
    (view: VisualGrid) => { view.time_axis.scope_id = null; },
    (view: VisualGrid) => { view.time_axis.source_identity = ''; },
    (view: VisualGrid) => { view.time_axis.clock_id = 'other'; },
    (view: VisualGrid) => { view.time_axis.segments[0]!.reference.clock_id = 'other'; },
    (view: VisualGrid) => { view.time_axis.available_start_ns = null; },
    (view: VisualGrid) => { view.time_axis.available_end_ns = null; },
    (view: VisualGrid) => { view.time_axis.segments = []; },
    (view: VisualGrid) => { view.time_axis.segments[0]!.reference.receive_wall_ns = 'NaN'; },
    (view: VisualGrid) => { view.time_axis.segments[0]!.reference.receive_mono_ns = (MONO + 1n).toString(); },
    (view: VisualGrid) => { view.time_axis.segments[0]!.start_ns = (MONO + 1n).toString(); },
    (view: VisualGrid) => { view.time_axis.segments.push({ ...view.time_axis.segments[0]!, id: 'duplicate-admission' }); },
  ]) {
    const view = grid();
    alter(view);
    assert.equal(projectReceiveTime(view, MONO.toString()), null);
    assert.equal(formatChartTime(view, MONO.toString()), 'Clock time unavailable');
    assert.deepEqual(chartTimeTicks(view, 800), []);
  }
  const view = grid();
  assert.equal(projectReceiveTime(view, (MONO - 1n).toString()), null);
  assert.equal(projectReceiveTime(view, (BigInt(view.now_ns) + 1n).toString()), null);
  assert.equal(projectReceiveTime(view, '1.5'), null);
  assert.equal(projectReceiveTime(view, '-1'), null);
});

test('clipped coverage keeps a predecessor pair and never extrapolates through the future', () => {
  const view = grid();
  view.time_axis.available_start_ns = (MONO + 3n * SECOND).toString();
  view.time_axis.available_end_ns = (MONO + 12n * SECOND).toString();
  view.end_ns = (MONO + 60n * SECOND).toString();
  view.time_axis.segments.push(step(view, MONO + 15n * SECOND, MONO + 15n * SECOND, SECOND));
  view.time_axis.last_unapplied_pair = {
    admission_ns: (MONO + 5n * SECOND).toString(), reason: 'nonadvancing_receive_pair', offset_change_ns: (99n * SECOND).toString(),
    reference: { ...view.time_axis.segments[0]!.reference, receive_wall_ns: (WALL + 99n * SECOND).toString() },
  };
  assert.equal(projectReceiveTime(view, (MONO + 2n * SECOND).toString()), null);
  assert.equal(projectReceiveTime(view, (MONO + 5n * SECOND).toString()), (WALL + 5n * SECOND).toString());
  assert.equal(projectReceiveTime(view, (MONO + 12n * SECOND).toString()), (WALL + 12n * SECOND).toString());
  assert.equal(projectReceiveTime(view, (MONO + 12n * SECOND + 1n).toString()), null);
  const ticks = chartTimeTicks(view, 1200);
  assert.ok(ticks.length > 0);
  assert.ok(ticks.every(tick => BigInt(tick.time_ns) >= MONO + 3n * SECOND && BigInt(tick.time_ns) <= MONO + 12n * SECOND));
  assert.ok(ticks.every(tick => tick.kind !== 'discontinuity'));
  view.now_ns = (MONO + 6n * SECOND).toString();
  assert.equal(projectReceiveTime(view, (MONO + 7n * SECOND).toString()), null);
});

test('Date overflow is explicit while exact wall projection remains available', () => {
  const view = grid(8_640_000_000_000_001n * 1_000_000n);
  assert.equal(projectReceiveTime(view, MONO.toString()), view.time_axis.segments[0]!.reference.receive_wall_ns);
  assert.equal(formatChartTime(view, MONO.toString(), 'UTC', true), 'Clock time unavailable');
  assert.deepEqual(chartTimeTicks(view, 800), []);
  const beforeEpoch = grid(-1n);
  assert.match(formatChartTime(beforeEpoch, MONO.toString(), 'UTC', true), /^1969-12-31 23:59:59\.999999999 UTC$/);
  const enormousViewport = grid();
  enormousViewport.end_ns = (10n ** 400n).toString();
  assert.doesNotThrow(() => chartTimeTicks(enormousViewport, 800));
});

test('UTC labels use seconds for short windows, minutes for wide windows and exact dates at midnight', () => {
  const short = grid(WALL - 10n * SECOND);
  assert.equal(formatChartTime(short, MONO.toString()), '23:59:50');
  assert.equal(chartTimeZoneLabel(), 'UTC');
  const midnight = chartTimeTicks(short, 1000).find(tick => tick.kind === 'date');
  assert.equal(midnight?.time_ns, (MONO + 10n * SECOND).toString());
  assert.equal(midnight?.label, '2024-01-01');
  const wide = grid(WALL + 3600n * SECOND, 3600n * SECOND);
  assert.equal(formatChartTime(wide, MONO.toString()), '01:00');
  assert.ok(chartTimeTicks(wide, 1000).filter(tick => tick.kind === 'time').every(tick => /^\d{2}:\d{2}$/.test(tick.label)));
  const minutes = chartTimeTicks(grid(WALL + 3600n * SECOND, 300n * SECOND), 1600).filter(tick => tick.kind === 'time');
  assert.ok(minutes.length > 1);
  assert.equal(new Set(minutes.map(tick => tick.label)).size, minutes.length);
});

test('permanent clock context retains only the mapped calendar date and explicit zone', () => {
  const view = grid(WALL - 1n);
  assert.equal(formatChartDateContext(view, MONO.toString()), '2023-12-31 UTC');
  assert.equal(formatChartDateContext(view, (MONO + 1n).toString()), '2024-01-01 UTC');
  assert.equal(formatChartDateContext(view, (MONO - 1n).toString()), 'Clock time unavailable · UTC');
  assert.equal(formatChartDateContext(view, (MONO + 21n * SECOND).toString()), 'Clock time unavailable · UTC');
  assert.equal(formatChartDateContext(grid(8_640_000_000_000_001n * 1_000_000n), MONO.toString()), 'Clock time unavailable · UTC');
});

test('clock-aligned ticks remain stable under a small pan and labels never overlap', () => {
  const view = grid(WALL + SECOND / 3n, 60n * SECOND);
  const ticks = chartTimeTicks(view, 800);
  assert.ok(ticks.length >= 3);
  for (const tick of ticks) {
    assert.equal(BigInt(projectReceiveTime(view, tick.time_ns)!) % SECOND, 0n);
  }
  const panned = { ...view, start_ns: (MONO + SECOND).toString(), end_ns: (MONO + 61n * SECOND).toString() };
  const after = chartTimeTicks(panned, 800);
  assert.ok(ticks.slice(1, -1).every(tick => after.some(next => next.time_ns === tick.time_ns)));
  for (const width of [40, 120, 320, 800, 1600]) {
    const selected = chartTimeTicks(view, width).filter(tick => tick.label);
    const bounds = selected.map(tick => {
      const half = tick.label.length * 7 / 2;
      const x = Number((BigInt(tick.time_ns) - MONO) * 1_000_000n / (60n * SECOND)) / 1_000_000 * width;
      const center = Math.max(half, Math.min(width - half, x));
      return { left: center - half, right: center + half };
    });
    assert.ok(bounds.every(bound => bound.left >= 0 && bound.right <= width));
    for (let index = 1; index < bounds.length; index++) assert.ok(bounds[index]!.left >= bounds[index - 1]!.right + 12);
  }
});

test('forward and backward wall steps keep monotonic positions and explicit discontinuities', () => {
  const view = grid();
  view.time_axis.segments.push(step(view, MONO + 7n * SECOND, MONO + 7n * SECOND, 5n * SECOND));
  view.time_axis.segments.push(step(view, MONO + 14n * SECOND, MONO + 14n * SECOND, -4n * SECOND));
  const ticks = chartTimeTicks(view, 1000);
  const markers = ticks.filter(tick => tick.kind === 'discontinuity');
  assert.deepEqual(markers.map(tick => tick.time_ns), [(MONO + 7n * SECOND).toString(), (MONO + 14n * SECOND).toString()]);
  assert.match(markers[0]!.label, /\+5s/);
  assert.match(markers[1]!.label, /-9s/);
  assert.equal(projectReceiveTime(view, (MONO + 14n * SECOND).toString()), (WALL + 10n * SECOND).toString());
  const narrow = chartTimeTicks(view, 40);
  assert.equal(narrow.filter(tick => tick.kind === 'discontinuity').length, 2);
});

test('local repeated DST hours have distinct detailed offsets and calendar days respect DST', () => {
  const previous = process.env.TZ;
  process.env.TZ = 'America/New_York';
  try {
    const repeated = grid(BigInt(Date.parse('2024-11-03T05:30:00Z')) * 1_000_000n, 2n * 3600n * SECOND);
    const first = formatChartTime(repeated, MONO.toString(), 'local', true);
    const second = formatChartTime(repeated, (MONO + 3600n * SECOND).toString(), 'local', true);
    assert.match(first, /2024-11-03 01:30:00\.000000000 UTC-04:00$/);
    assert.match(second, /2024-11-03 01:30:00\.000000000 UTC-05:00$/);
    assert.equal(formatChartDateContext(repeated, MONO.toString(), 'local'), '2024-11-03 UTC-04:00');
    assert.equal(formatChartDateContext(repeated, (MONO + 3600n * SECOND).toString(), 'local'), '2024-11-03 UTC-05:00');
    const days = grid(BigInt(Date.parse('2024-03-10T05:00:00Z')) * 1_000_000n, 48n * 3600n * SECOND);
    const dates = chartTimeTicks(days, 800, 'local').filter(tick => tick.kind === 'date');
    assert.ok(dates.length >= 2);
    assert.equal(BigInt(dates[1]!.time_ns) - BigInt(dates[0]!.time_ns), 23n * 3600n * SECOND);
    assert.equal(dates[0]!.label, '2024-03-10');
    assert.equal(dates[1]!.label, '2024-03-11');
    process.env.TZ = 'Asia/Kolkata';
    const fractionalZone = grid(WALL, 6n * 3600n * SECOND);
    const hours = chartTimeTicks(fractionalZone, 800, 'local').filter(tick => tick.kind === 'time');
    assert.ok(hours.length > 0);
    assert.ok(hours.every(tick => tick.label.endsWith(':00')));
  } finally {
    if (previous === undefined) delete process.env.TZ;
    else process.env.TZ = previous;
  }
});
