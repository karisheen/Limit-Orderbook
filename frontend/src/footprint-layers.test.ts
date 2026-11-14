import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { NativeMarketTrade, VisualGrid } from './api.ts';
import type { NormalizedTrade } from './orderflow/lib/types/market.ts';
import { FOOTPRINT_LAYER_LIMITS, footprintLiquidityWindow, footprintTimeSlices, printInspection, projectFootprintLiquidity, retainedLargePrints, suppressPrintCollisions } from './footprint-layers.ts';

const MONO = 9_007_199_254_740_993_000_000_000n;
const WALL = 1_704_067_200_000_000_000n;
const SECOND = 1_000_000_000n;
function trade(index = 0, quantity = '2.000000000000000001'): NormalizedTrade {
  const native: NativeMarketTrade = {
    instrument_id: 'BTC', display_time_ns: (WALL + BigInt(index) * SECOND).toString(),
    cursor: { delivery: String(index + 1), event: 0 },
    source_identity: { session_id: 'recording', segment_id: 'segment', clock_id: 'clock', connection_epoch: '1' },
    asof: { session_id: 'recording', ingest_ordinal: String(index + 1), event_index: '0', clock_id: 'clock',
      receive_mono_ns: (MONO + BigInt(index) * SECOND).toString(), receive_wall_ns: (WALL + BigInt(index) * SECOND + 123n).toString(), exchange_event_wall_ns: null },
    stream_id: 'trades', base_quantity: quantity, quote_notional: null,
    trade: { trade_id: `native-${index}`, dedup_key: `key-${index}`, price: '100.000000000000000001', quantity,
      quantity_unit: 'BTC', taker_side: 'buy', side_provenance: 'native', is_subscription_history: false },
  };
  return { id: `plot-${index}`, venue: 'native-venue', symbol: 'BTCUSD', timestamp: 0, localTimestamp: 0,
    price: 999, quantity: 999, quoteVolume: null, side: 'buy', marketType: 'spot', native };
}
function grid(): VisualGrid {
  const reference = trade().native!.asof;
  return {
    version: '1', generation: '4', grid_id: 'grid', instrument_id: 'BTC', clock_id: 'clock', view_id: 'view', published_mono_ns: '100',
    quantity_unit: 'BTC', sampling: 'sampled', price_step: '1', price_min: '100', prices: ['100', '101'], rows: 2,
    bucket_ns: SECOND.toString(), start_ns: MONO.toString(), end_ns: (MONO + 3n * SECOND).toString(), now_ns: (MONO + 3n * SECOND).toString(),
    retained_start_ns: MONO.toString(), step_options: [], columns: [0, 1, 2].map<VisualGrid['columns'][number]>(index => ({
      id: String(index), time_ns: (MONO + BigInt(index) * SECOND).toString(), end_ns: (MONO + BigInt(index + 1) * SECOND).toString(),
      segment_id: 'segment', asof: reference, eligible: true, reasons: [], liquidity_hash: String(index), cells: [['2', null], [null, '3']],
      best_bid: '100', best_ask: '101', cvd: null, ofi: null, candle: null,
    })), trades: [], profile: [], footprints: [], reasons: [], truncated: false, complete_window: true, replace: true, next_after: null,
    time_axis: { version: '1', basis: 'receive_mono_highwater', projection: 'piecewise_receive_pair_v1', source_identity: 'recording:source',
      clock_id: 'clock', scope_id: 'scope', available_start_ns: MONO.toString(), available_end_ns: (MONO + 3n * SECOND).toString(),
      offset_change_threshold_ns: '1000000', last_unapplied_pair: null, reasons: [],
      segments: [{ id: 'pair', start_ns: MONO.toString(), reference, cause: 'initial_pair', offset_change_ns: null }] },
  };
}

test('multi-hour footprint requests fit native duration and column limits while disclosing partial history', () => {
  const start = Number(WALL / 1_000_000n);
  const request = footprintLiquidityWindow([{ openTime: start, closeTime: start + 3 * 60 * 60 * 1000 - 1 }]);
  assert.equal(request.rangeSeconds, 1800);
  assert.equal(request.bucket, '5000000000');
  assert.equal(request.limited, true);
  const width = BigInt(request.rangeSeconds) * SECOND;
  assert.ok(width <= 1800n * SECOND);
  const columns = Number((width + BigInt(request.bucket) - 1n) / BigInt(request.bucket)) + 1;
  assert.equal(columns, 361);
  assert.ok(columns <= FOOTPRINT_LAYER_LIMITS.columns);
  for (const seconds of [180, 599, 600, 1800, 1801]) {
    const window = footprintLiquidityWindow([{ openTime: start, closeTime: start + seconds * 1000 - 1 }]);
    assert.equal(window.limited, seconds > 1800);
    assert.equal(window.rangeSeconds, Math.min(seconds, 1800));
    assert.ok(Math.ceil(window.rangeSeconds * 1_000_000_000 / Number(window.bucket)) + 1 <= 600);
  }
});

test('shorter available native coverage clips tiles and discloses partial resting history', () => {
  const view = grid();
  view.time_axis.available_start_ns = (MONO + SECOND).toString();
  view.time_axis.available_end_ns = (MONO + 2500n * 1_000_000n).toString();
  const result = projectFootprintLiquidity(view, [trade()], '4', 'BTC');
  assert.equal(result.limited, true);
  assert.equal(result.tiles.length, 4);
  assert.ok(result.tiles.every(tile => tile.start >= Number(WALL / 1_000_000n) + 1000
    && tile.end <= Number(WALL / 1_000_000n) + 2500));
  const incomplete = grid();
  incomplete.complete_window = false;
  assert.equal(projectFootprintLiquidity(incomplete, [trade()], '4', 'BTC').limited, true);
});

test('historical liquidity uses actual footprint receive projection, not wall labels or present book', () => {
  const view = grid();
  // Visual wall labels can change without changing the footprint's anchored clock.
  view.time_axis.segments[0]!.reference = { ...view.time_axis.segments[0]!.reference, receive_wall_ns: (WALL + 30n * SECOND).toString() };
  const result = projectFootprintLiquidity(view, [trade()], '4', 'BTC');
  assert.equal(result.reason, null);
  assert.equal(result.tiles.length, 6);
  assert.equal(result.tiles[0]!.start, Number(WALL / 1_000_000n));
  assert.equal(result.tiles[0]!.end, Number(WALL / 1_000_000n) + 1000);
  assert.equal(result.tiles[0]!.bid, '2');
});

test('unknown cells, ineligible intervals and future evidence remain blank', () => {
  const view = grid();
  view.columns[0]!.cells = [[null, null], [null, null]];
  view.columns[1]!.eligible = false;
  view.now_ns = (MONO + 2500n * 1_000_000n).toString();
  const result = projectFootprintLiquidity(view, [trade()], '4', 'BTC');
  assert.equal(result.tiles.length, 2);
  assert.equal(result.tiles[0]!.start, Number(WALL / 1_000_000n) + 2000);
  assert.equal(result.tiles[0]!.end, Number(WALL / 1_000_000n) + 2500);
});

test('generation, primary instrument, source clock and projection changes clear historical overlay', () => {
  assert.equal(projectFootprintLiquidity(grid(), [trade()], '5', 'BTC').tiles.length, 0);
  assert.equal(projectFootprintLiquidity(grid(), [trade()], '4', 'ETH').tiles.length, 0);
  assert.equal(projectFootprintLiquidity(grid(), [], '4', 'BTC').tiles.length, 0);
  const otherClock = trade(); otherClock.native!.asof.clock_id = 'other';
  assert.match(projectFootprintLiquidity(grid(), [otherClock], '4', 'BTC').reason!, /clocks differ/);
  const shifted = trade(1); shifted.native!.display_time_ns = (WALL + 2n * SECOND).toString();
  assert.match(projectFootprintLiquidity(grid(), [trade(), shifted], '4', 'BTC').reason!, /projection changed/);
  const missingScope = grid(); missingScope.time_axis.scope_id = null;
  assert.equal(projectFootprintLiquidity(missingScope, [trade()], '4', 'BTC').tiles.length, 0);
  const wrongSession = grid(); wrongSession.columns.forEach(column => { column.asof = { ...column.asof!, session_id: 'different-recording' }; });
  assert.equal(projectFootprintLiquidity(wrongSession, [trade()], '4', 'BTC').tiles.length, 0);
});

test('primary-only overlay does not borrow an aggregate venue receive anchor', () => {
  const other = trade(); other.native!.instrument_id = 'OTHER';
  assert.match(projectFootprintLiquidity(grid(), [other], '4', 'BTC').reason!, /primary trade/);
  assert.equal(projectFootprintLiquidity(grid(), [other, trade()], '4', 'BTC').tiles.length, 6);
});

test('ordinal footprint maps exact in-bucket fractions without stretching through missing buckets', () => {
  const candles = [{ openTime: 1000, closeTime: 1999 }, { openTime: 4000, closeTime: 4999 }];
  assert.deepEqual(footprintTimeSlices(candles, 1500, 4500), [{ index: 0, from: 0.5, to: 1 }, { index: 1, from: 0, to: 0.5 }]);
  assert.deepEqual(footprintTimeSlices(candles, 2000, 4000), []);
  assert.deepEqual(footprintTimeSlices(candles, 4000, 4001), [{ index: 1, from: 0, to: 0.001 }]);
});

test('liquidity data and tile expansion have explicit fixed bounds', () => {
  const view = grid();
  view.prices = Array.from({ length: 128 }, (_, row) => String(100 + row));
  const column = view.columns[0]!;
  view.columns = Array.from({ length: 700 }, (_, index) => ({ ...column, id: String(index), cells: view.prices.map(() => ['2', null] as [string, null]) }));
  const result = projectFootprintLiquidity(view, [trade()], '4', 'BTC');
  assert.equal(result.tiles.length, FOOTPRINT_LAYER_LIMITS.tiles);
  assert.equal(result.limited, true);
});

test('large prints require individual retained native references and exact inspection values', () => {
  const original = trade();
  const aggregateOnly = { ...original, native: undefined };
  const history = trade(1); history.native!.trade.is_subscription_history = true;
  const noBase = trade(2); noBase.native!.base_quantity = null;
  const other = trade(3); other.native!.instrument_id = 'ETH';
  const prints = retainedLargePrints([aggregateOnly, history, noBase, other, original, original], ['BTC'], 1);
  assert.equal(prints.length, 1);
  assert.equal(prints[0]!.quantity, 2); // Native value, not the plotting aggregate 999.
  assert.equal(prints[0]!.price, 100);
  assert.equal(prints[0]!.time, Number(WALL / 1_000_000n));
  const inspection = printInspection(prints[0]!);
  assert.ok(inspection.includes('Venue: native-venue'));
  assert.ok(inspection.includes('Price: 100.000000000000000001'));
  assert.ok(inspection.includes('Base size: 2.000000000000000001'));
  assert.ok(inspection.includes(`Projected receive ns: ${WALL}`));
  assert.ok(inspection.includes(`Receive monotonic ns: ${MONO}`));
  assert.ok(inspection.includes('Exchange event ns: Unavailable'));
  assert.deepEqual(retainedLargePrints([original], ['BTC'], 3), []);
});

test('large print filtering is bounded, largest first, with bubble/venue collision suppression', () => {
  const prints = retainedLargePrints(Array.from({ length: 250 }, (_, index) => trade(index, String(index + 1))), ['BTC'], 0);
  assert.equal(prints.length, FOOTPRINT_LAYER_LIMITS.prints);
  assert.equal(prints[0]!.quantity, 250);
  const badges = suppressPrintCollisions([
    { print: prints[0]!, x: 20, y: 20, radius: 8, width: 40 },
    { print: prints[1]!, x: 45, y: 20, radius: 6, width: 40 },
    { print: prints[2]!, x: 90, y: 20, radius: 6, width: 40 },
  ]);
  assert.deepEqual(badges.map(badge => badge.print.id), [prints[0]!.id, prints[2]!.id]);
});

test('aggregate clock/cohort mismatch clears the primary-only overlay rather than relabeling it', () => {
  const secondary = trade(1); secondary.native!.instrument_id = 'OTHER';
  assert.equal(projectFootprintLiquidity(grid(), [trade(), secondary], '4', 'BTC', ['BTC', 'OTHER']).tiles.length, 6);
  secondary.native!.asof.clock_id = 'disjoint-clock';
  assert.match(projectFootprintLiquidity(grid(), [trade(), secondary], '4', 'BTC', ['BTC', 'OTHER']).reason!, /clocks differ/);
  assert.match(projectFootprintLiquidity(grid(), [trade()], '4', 'BTC', ['OTHER']).reason!, /cohort/);
});
