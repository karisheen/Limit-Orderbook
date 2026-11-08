import assert from 'node:assert/strict';
import test from 'node:test';
import type { Instrument, NativeMarketBook, NativeMarketBoundary, NativeMarketData, NativeMarketTrade } from './api';
import { createTerminalStore } from './orderflow/lib/store/terminalStore';
import { compareCursor, drainNativeMarketData, NativeMarketBridge } from './orderflow/lib/session/NativeMarketBridge';
import { displayCohort, displayQuoteEligible } from './orderflow/lib/config/instruments';

const primary: Instrument = {
  instrument_id: 'native:one:BTC-USD', venue: 'one', api_family: 'spot', native_symbol: 'BTC-USD',
  base: { asset_id: 'btc', ticker: 'BTC', native_token_id: null },
  quote: { asset_id: 'USD', ticker: 'USD', native_token_id: null }, settlement: null,
  market_type: 'spot', tick_size: '0.01', lot_size: '0.001', quantity_unit: 'base',
  status: 'active', data_access: 'public', execution_access: 'none', metadata_version: '1', metadata_status: 'verified',
};
function trade(event: number, patch: Partial<NativeMarketTrade> = {}): NativeMarketTrade {
  return {
    instrument_id: primary.instrument_id, cursor: { delivery: '9007199254740993', event },
    source_identity: { session_id: 'capture', segment_id: 'segment', clock_id: 'clock', connection_epoch: '1' },
    trade: { trade_id: String(event), dedup_key: String(event), price: '100.00000000000000000001', quantity: '1',
      quantity_unit: 'base', taker_side: 'buy', side_provenance: 'venue', is_subscription_history: false },
    asof: { session_id: 's', ingest_ordinal: '1', event_index: String(event), clock_id: 'clock',
      receive_wall_ns: '1700000000000000000', receive_mono_ns: String(1_000_000 + event * 1000), exchange_event_wall_ns: null },
    stream_id: 'stream', base_quantity: '1', quote_notional: '100.00000000000000000001', ...patch,
  };
}
function page(trades: NativeMarketTrade[], patch: Partial<NativeMarketData> = {}): NativeMarketData {
  return { version: '1', session_id: 's', generation: '1', cursor: trades.at(-1)?.cursor ?? { delivery: '0', event: -1 },
    highwater: '9007199254740993', gap: false, has_more: false, books: [], instruments: [primary], trades, boundaries: [],
    now: { clock_id: 'clock', mono_ns: '1000000', wall_ns: '1700000000000000000' }, ...patch };
}
function book(instrument: Instrument, base = '2'): NativeMarketBook {
  return { instrument_id: instrument.instrument_id, bids: [{ price: '99', quantity: '2000', base_quantity: base, quote_notional: '198' }],
    asks: [{ price: '101', quantity: '3000', base_quantity: '3', quote_notional: '303' }],
    quality: { lifecycle: 'live', integrity: 'valid', source_mode: 'replay', recorded_source_mode: 'live', reason_codes: [],
      capture_complete: true, coverage: { subscribed_depth: '10', retained_bid_levels: '1', retained_ask_levels: '1', full_depth: false } },
    asof: trade(0).asof, revision: '1', presentation: { depth_limit: 100, bids_omitted: 0, asks_omitted: 0 },
    eligibility: { eligible: true, reasons: [] } };
}
function setup(selected: Instrument = primary) {
  const store = createTerminalStore();
  const bridge = new NativeMarketBridge(store);
  bridge.reset({ sessionId: 's', generation: '1', sourceId: 'replay', sourceMode: 'replay', primary: selected });
  bridge.configure();
  return { store, bridge };
}

test('cursor drain preserves burst trades across partial delivery pages and deduplicates retries', async () => {
  const { store, bridge } = setup();
  const burst = Array.from({ length: 6001 }, (_, index) => trade(index));
  const pages = [page([], { cursor: { delivery: '9007199254740992', event: 8 }, has_more: true }),
    page(burst.slice(0, 2000), { has_more: true }), page(burst.slice(2000, 4000), { has_more: true }), page(burst.slice(4000))];
  const requested: string[] = [];
  await drainNativeMarketData(bridge, async cursor => {
    requested.push(`${cursor.delivery}:${cursor.event}`);
    return pages.shift()!;
  }, new AbortController().signal);
  assert.deepEqual(requested, ['0:-1', '9007199254740992:8', '9007199254740993:1999', '9007199254740993:3999']);
  assert.equal(store.getState().session?.trades, 6001);
  assert.equal(store.getState().currentCandle?.volume, 6001);
  assert.equal(store.getState().recentTrades.length, 500);
  bridge.accept(page(burst.slice(4000)));
  assert.equal(store.getState().session?.trades, 6001);
  assert.equal(store.getState().recentTrades[0]?.native?.trade.price, '100.00000000000000000001');
  assert.ok(compareCursor({ delivery: '9007199254740993', event: 0 }, { delivery: '9007199254740992', event: 999 }) > 0);
});

test('unknown side is volume without sell delta; history retains native provenance only on tape', () => {
  const { store, bridge } = setup();
  const unknown = trade(1); unknown.trade = { ...unknown.trade, taker_side: 'unknown', side_provenance: 'unavailable' };
  const history = trade(2); history.trade = { ...history.trade, taker_side: 'sell', is_subscription_history: true };
  bridge.accept(page([trade(0), unknown, history]));
  const state = store.getState();
  assert.equal(state.currentCandle?.volume, 2);
  assert.equal(state.currentCandle?.delta, 1);
  assert.equal(state.liveBar?.cvd, 1);
  assert.equal(state.session?.sellVolume, 0);
  assert.equal(state.volumeProfile?.totalVolume, 2);
  assert.equal(state.recentTrades.length, 3);
  assert.equal(state.recentTrades[0]?.native, history);
  assert.equal(state.recentTrades[1]?.side, 'unknown');
  assert.equal(state.displayStatus.unknownSideTrades, 1);
  assert.equal(state.displayStatus.historyTrades, 1);
  assert.ok(state.displayStatus.reasons.some(reason => reason.includes('tape-only')));
});

test('display controls recompute retained inputs and selection fences clear every old cache', () => {
  const { store, bridge } = setup();
  bridge.accept(page([trade(0), trade(1)], { books: [book(primary)] }));
  store.getState().setTimeframe('1m'); store.getState().setTickSize(1); bridge.configure();
  assert.equal(store.getState().session?.trades, 2);
  assert.equal(store.getState().recentTrades.length, 2);
  assert.equal(store.getState().volumeProfile?.totalVolume, 2);
  const next = { ...primary, instrument_id: 'native:one:ETH-USD', base: { ...primary.base, asset_id: 'eth', ticker: 'ETH' } };
  bridge.reset({ sessionId: 's', generation: '2', sourceId: 'replay', primary: next });
  assert.equal(store.getState().session, null);
  assert.equal(store.getState().recentTrades.length, 0);
  assert.equal(store.getState().orderBook, null);
  assert.equal(store.getState().flowBars.length, 0);
  assert.equal(store.getState().obiHistory.length, 0);
  assert.equal(bridge.accept(page([trade(2)])), false);
  bridge.accept(page([trade(3)], { generation: '2', instruments: [primary, next] }));
  assert.equal(store.getState().recentTrades.length, 0);
  assert.equal(store.getState().displayStatus.primaryInstrumentId, next.instrument_id);
  bridge.accept(page([trade(4, { instrument_id: next.instrument_id })], { generation: '2', instruments: [next] }));
  assert.equal(store.getState().session?.trades, 1);
  assert.equal(store.getState().recentTrades[0]?.native?.instrument_id, next.instrument_id);
});

test('native cohort aggregates base projections, not contract counts, and never changes primary', () => {
  const { store, bridge } = setup();
  const alternate: Instrument = { ...primary, instrument_id: 'native:two:BTC-USDT', venue: 'two',
    quote: { asset_id: 'USDT', ticker: 'USDT', native_token_id: null } };
  const otherMarket = { ...primary, instrument_id: 'native:one:BTC-PERP', market_type: 'perpetual' };
  const otherBase = { ...primary, instrument_id: 'native:one:ETH-USD', base: { ...primary.base, asset_id: 'eth' } };
  store.getState().setVenue('all-spot'); store.getState().setSelectedVenues([]); bridge.configure();
  bridge.accept(page([trade(0), trade(1, { instrument_id: alternate.instrument_id }), trade(2, { instrument_id: otherMarket.instrument_id }),
    trade(3, { instrument_id: otherBase.instrument_id })], {
    instruments: [primary, alternate, otherMarket, otherBase], books: [book(primary), book(alternate, '4'), book(otherMarket), book(otherBase)],
  }));
  assert.equal(store.getState().aggregatedBook?.bids[0]?.size, 6);
  assert.equal(store.getState().orderBook?.bids[0]?.size, 2);
  assert.deepEqual(store.getState().aggregatedBook?.venues, ['one', 'two']);
  assert.equal(store.getState().session?.trades, 2);
  assert.equal(store.getState().displayStatus.primaryInstrumentId, primary.instrument_id);
  assert.ok(store.getState().displayStatus.reasons.some(reason => reason.includes('cross-quote')));
  assert.ok(store.getState().displayStatus.reasons.some(reason => reason.includes('Partial native depth')));
  store.getState().setSelectedVenues(['two']); bridge.configure();
  assert.equal(store.getState().session?.trades, 1);
  assert.equal(store.getState().orderBook, null);
  assert.equal(store.getState().aggregatedBook?.bids[0]?.size, 4);
  assert.equal(store.getState().displayStatus.primaryInstrumentId, primary.instrument_id);
});

test('ineligible and unvalued books are unavailable and disappeared cohort books are removed', () => {
  const { store, bridge } = setup();
  const initial = page([], { books: [book(primary)] });
  bridge.accept(initial);
  assert.equal(store.getState().orderBook?.bids[0]?.size, 2);
  const unavailable = book(primary); unavailable.bids[0]!.base_quantity = null;
  bridge.accept(page([], { books: [unavailable] }));
  assert.equal(store.getState().orderBook, null);
  assert.ok(store.getState().displayStatus.reasons.some(reason => reason.includes('quantity unavailable')));
  const stale = book(primary); stale.eligibility = { eligible: false, reasons: ['stale'] };
  bridge.accept(page([], { books: [stale] }));
  assert.equal(store.getState().aggregatedBook, null);
  assert.ok(store.getState().displayStatus.reasons.includes('stale'));
  bridge.accept(initial);
  bridge.accept(page([]));
  assert.equal(store.getState().orderBook, null);
  assert.equal(store.getState().aggregatedBook, null);
});

test('cursor gaps reset study continuity, surface loss, and do not duplicate a retried gap page', () => {
  const { store, bridge } = setup();
  bridge.accept(page([trade(0)], { books: [book(primary, '2')] }));
  bridge.accept(page([trade(1)], { books: [book(primary, '4')] }));
  const depth = store.getState().obiHistory;
  const gap = page([trade(10), trade(11)], { gap: true, books: [book(primary, '4')] });
  bridge.accept(gap);
  assert.equal(store.getState().session?.trades, 2);
  assert.equal(store.getState().displayStatus.gap, true);
  assert.deepEqual(store.getState().obiHistory, depth);
  bridge.accept(gap);
  assert.equal(store.getState().session?.trades, 2);
  bridge.accept(page([trade(20)], { gap: true }));
  assert.equal(store.getState().session?.trades, 1);
  assert.ok(store.getState().displayStatus.reasons.some(reason => reason.includes('cursor gap')));
});

test('aborted page drains cannot repopulate reset stores; stalled pages fail visibly', async () => {
  const { store, bridge } = setup();
  const controller = new AbortController();
  await drainNativeMarketData(bridge, async () => {
    controller.abort(); bridge.reset(null); return page([trade(0)]);
  }, controller.signal);
  assert.equal(store.getState().recentTrades.length, 0);
  bridge.reset({ sessionId: 's', generation: '1', sourceId: 'replay', primary });
  await assert.rejects(drainNativeMarketData(bridge, async () => page([], { has_more: true }), new AbortController().signal), /did not advance/);
});

function boundary(event: number, patch: Partial<NativeMarketBoundary> = {}): NativeMarketBoundary {
  return { instrument_id: primary.instrument_id, stream_id: 'stream', cursor: trade(event).cursor,
    asof: trade(event).asof, kind: 'gap', reason: 'missing trade sequence', ...patch };
}

test('timeframe, tick and indicator rebuilds retain independent depth history', () => {
  const { store, bridge } = setup();
  for (let event = 0; event < 3; event++) {
    bridge.accept(page([trade(event)], { books: [book(primary, String(event + 2))] }));
  }
  const depth = store.getState().obiHistory;
  assert.equal(depth.length, 3);
  for (const change of [
    () => store.getState().setTimeframe('1m'),
    () => store.getState().setTickSize(2),
    () => store.getState().setImbalanceThreshold(4),
    () => store.getState().setValueAreaPct(0.8),
  ]) {
    change(); bridge.configure();
    assert.deepEqual(store.getState().obiHistory, depth);
    assert.equal(store.getState().session?.trades, 3);
    assert.equal(store.getState().currentCandle?.delta, 3);
  }
});

test('retained trade window rebuilds never collapse OBI history', () => {
  const { store, bridge } = setup();
  bridge.accept(page([trade(0)], { books: [book(primary, '2')] }));
  bridge.accept(page([trade(1)], { books: [book(primary, '4')] }));
  const depth = store.getState().obiHistory;
  bridge.accept(page(Array.from({ length: 100_000 }, (_, index) => trade(index + 2)), { books: [book(primary, '4')] }));
  assert.equal(store.getState().session?.trades, 100_000);
  assert.deepEqual(store.getState().obiHistory, depth);
  bridge.accept(page([trade(100_002)], { books: [book(primary, '4')] }));
  assert.equal(store.getState().session?.trades, 100_000);
  assert.deepEqual(store.getState().obiHistory, depth);
});

test('ordered trade boundaries restart footprints and CVD within a page without resetting books', () => {
  const { store, bridge } = setup();
  bridge.accept(page([trade(0)], { books: [book(primary, '2')] }));
  bridge.accept(page([trade(1)], { books: [book(primary, '4')] }));
  const depth = store.getState().obiHistory;
  const interrupted = page([trade(2), trade(4)], {
    boundaries: [boundary(3)], books: [book(primary, '4')],
  });
  bridge.accept(interrupted);
  assert.equal(store.getState().session?.trades, 1);
  assert.equal(store.getState().currentCandle?.volume, 1);
  assert.equal(store.getState().currentCandle?.cumulativeDelta, 1);
  assert.equal(store.getState().liveBar?.cvd, 1);
  assert.equal(store.getState().displayStatus.gap, true);
  assert.deepEqual(store.getState().obiHistory, depth);
  bridge.accept(interrupted);
  store.getState().setTimeframe('1m'); bridge.configure();
  assert.equal(store.getState().session?.trades, 1);
  assert.deepEqual(store.getState().obiHistory, depth);
});

test('boundary-only pages clear affected studies, never unrelated cohort streams', () => {
  const { store, bridge } = setup();
  const alternate = { ...primary, instrument_id: 'native:two:BTC-USD', venue: 'two' };
  store.getState().setVenue('all-spot'); store.getState().setSelectedVenues([]); bridge.configure();
  const books = [book(primary), book(alternate)];
  const instruments = [primary, alternate];
  bridge.accept(page([trade(0), trade(1, { instrument_id: alternate.instrument_id, stream_id: 'other' })], { books, instruments }));
  const depth = store.getState().obiHistory;
  bridge.accept(page([], { books, instruments, boundaries: [boundary(2)], cursor: trade(2).cursor }));
  assert.equal(store.getState().session?.trades, 1);
  assert.equal(store.getState().recentTrades[0]?.native?.stream_id, 'other');
  assert.deepEqual(store.getState().obiHistory, depth);
  bridge.accept(page([], { books, instruments, boundaries: [boundary(3, { instrument_id: alternate.instrument_id, stream_id: 'other', kind: 'disconnect' })],
    cursor: trade(3).cursor }));
  assert.equal(store.getState().session, null);
  assert.equal(store.getState().currentCandle, null);
  assert.equal(store.getState().liveBar, null);
  assert.deepEqual(store.getState().obiHistory, depth);
});

test('native source identity changes across pages restart only that trade stream', () => {
  for (const field of ['session_id', 'segment_id', 'clock_id', 'connection_epoch'] as const) {
    const { store, bridge } = setup();
    bridge.accept(page([trade(0)], { books: [book(primary, '2')] }));
    bridge.accept(page([trade(1)], { books: [book(primary, '4')] }));
    const depth = store.getState().obiHistory;
    const changed = trade(2);
    changed.source_identity = { ...changed.source_identity, [field]: 'next' };
    bridge.accept(page([changed], { books: [book(primary, '4')] }));
    assert.equal(store.getState().session?.trades, 1, field);
    assert.equal(store.getState().liveBar?.cvd, 1, field);
    assert.ok(store.getState().displayStatus.reasons.some(reason => reason.includes('source identity changed')));
    assert.deepEqual(store.getState().obiHistory, depth);
  }
});

test('staggered stream clock transitions rebase candles and CVD after old-clock evidence disappears', () => {
  for (const transition of ['boundary', 'source identity'] as const) {
    const { store, bridge } = setup();
    const alternate = { ...primary, instrument_id: 'native:two:BTC-USD', venue: 'two' };
    store.getState().setVenue('all-spot'); store.getState().setSelectedVenues([]); bridge.configure();
    const books = [book(primary), book(alternate)];
    const instruments = [primary, alternate];
    const oldA = trade(0);
    const oldB = trade(1, { instrument_id: alternate.instrument_id, stream_id: 'other' });
    bridge.accept(page([oldA, oldB], { books, instruments }));
    const depth = store.getState().obiHistory;
    const newA = trade(3, {
      source_identity: { ...oldA.source_identity, clock_id: 'next-clock' },
      asof: { ...trade(3).asof, clock_id: 'next-clock', receive_mono_ns: '10', receive_wall_ns: '1700000060000000000' },
      trade: { ...trade(3).trade, taker_side: 'sell', quantity: '2' }, base_quantity: '2', quote_notional: '200',
    });
    const newB = trade(5, {
      instrument_id: alternate.instrument_id, stream_id: 'other',
      source_identity: { ...oldB.source_identity, clock_id: 'next-clock' },
      asof: { ...trade(5).asof, clock_id: 'next-clock', receive_mono_ns: '1000010', receive_wall_ns: '1700000060001000000' },
      trade: { ...trade(5).trade, quantity: '3' }, base_quantity: '3', quote_notional: '300',
    });
    bridge.accept(page([newA], {
      books, instruments, boundaries: transition === 'boundary' ? [boundary(3, { asof: newA.asof })] : [],
    }));
    // Keep B's old-clock evidence, but never project A using B's monotonic clock.
    assert.equal(store.getState().session?.trades, 1, transition);
    assert.equal(store.getState().liveBar?.cvd, 1, transition);
    assert.deepEqual(store.getState().recentTrades.map(value => value.native), [oldB], transition);
    assert.ok(store.getState().displayStatus.reasons.some(reason => reason.includes('Multiple native receive clocks')));
    if (transition === 'boundary') {
      bridge.accept(page([], {
        books, instruments, cursor: trade(4).cursor,
        boundaries: [boundary(4, { instrument_id: alternate.instrument_id, stream_id: 'other', asof: newB.asof })],
      }));
      assert.equal(store.getState().currentCandle?.volume, 2);
      assert.equal(store.getState().liveBar?.cvd, -2);
      assert.deepEqual(store.getState().recentTrades.map(value => value.native), [newA]);
    }
    bridge.accept(page([newB], { books, instruments }));
    const assertRecovered = () => {
      const state = store.getState();
      assert.equal(state.session?.trades, 2, transition);
      assert.equal(state.currentCandle?.volume, 5, transition);
      assert.equal(state.currentCandle?.delta, 1, transition);
      assert.equal(state.currentCandle?.cumulativeDelta, 1, transition);
      assert.equal(state.liveBar?.cvd, 1, transition);
      assert.deepEqual(state.recentTrades.map(value => value.native).sort((a, b) => a!.cursor.event - b!.cursor.event), [newA, newB]);
      assert.deepEqual(state.recentTrades.map(value => value.timestamp).sort(), [1700000060000, 1700000060001]);
      assert.equal(state.displayStatus.gap, true);
      assert.ok(state.displayStatus.reasons.some(reason => reason.includes(
        transition === 'boundary' ? 'Native trade gap' : 'source identity changed',
      )));
      assert.deepEqual(state.obiHistory, depth);
    };
    assertRecovered();
    bridge.accept(page([newB], { books, instruments }));
    store.getState().setTimeframe('1m'); bridge.configure();
    assertRecovered();
  }
});

test('aggregate venues refresh on native primary/base/market scope changes', () => {
  const { store, bridge } = setup();
  store.getState().setVenue('all-spot'); store.getState().setSelectedVenues(['one']); bridge.configure();
  const next: Instrument = { ...primary, instrument_id: 'native:three:ETH-PERP', venue: 'three', market_type: 'perpetual',
    base: { ...primary.base, asset_id: 'eth', ticker: 'ETH' } };
  const other = { ...next, instrument_id: 'native:four:ETH-PERP', venue: 'four' };
  bridge.reset({ sessionId: 's', generation: '2', sourceId: 'replay', primary: next });
  bridge.accept(page([trade(0, { instrument_id: next.instrument_id }), trade(1, { instrument_id: other.instrument_id, stream_id: 'other' })],
    { generation: '2', instruments: [next, other, primary], books: [book(next), book(other)] }));
  assert.equal(store.getState().venue, 'all-perps');
  assert.deepEqual(store.getState().selectedVenues, ['three', 'four']);
  assert.equal(store.getState().session?.trades, 2);
  assert.deepEqual(store.getState().aggregatedBook?.venues, ['three', 'four']);
  store.getState().setSelectedVenues(['four']); bridge.configure();
  assert.equal(store.getState().obiHistory.length, 1);
  assert.deepEqual(store.getState().aggregatedBook?.venues, ['four']);
});

test('busy polling cancellation and same-scope metadata refresh preserve caches and cursor', async () => {
  const { store, bridge } = setup();
  bridge.accept(page([trade(0)], { books: [book(primary)] }));
  bridge.configure(); // Settle the first-print automatic tick-size change.
  const state = store.getState();
  const cursor = bridge.cursor;
  const controller = new AbortController();
  await drainNativeMarketData(bridge, async () => {
    controller.abort(); // The hook aborts its request when a mutation becomes busy.
    return page([trade(1)]);
  }, controller.signal);
  bridge.reset({ sessionId: 's', generation: '1', sourceId: 'replay', primary: { ...primary } });
  bridge.configure();
  assert.equal(bridge.cursor, cursor);
  assert.equal(store.getState().session, state.session);
  assert.equal(store.getState().currentCandle, state.currentCandle);
  assert.equal(store.getState().recentTrades, state.recentTrades);
  assert.equal(store.getState().obiHistory, state.obiHistory);
  await drainNativeMarketData(bridge, async after => {
    assert.deepEqual(after, cursor);
    return page([trade(1)], { books: [book(primary)] });
  }, new AbortController().signal);
  assert.equal(store.getState().session?.trades, 2);
});

test('native lag subtracts exact timestamps; missing and negative evidence are unavailable', () => {
  const { store, bridge } = setup();
  const observed = trade(0);
  observed.asof = { ...observed.asof, receive_wall_ns: '1700000000000000001', exchange_event_wall_ns: '1699999999876543211' };
  bridge.accept(page([observed]));
  assert.equal(store.getState().session?.lastTradeLagMs, 123.45679);
  assert.equal(store.getState().displayStatus.lagObservation, 'recorded');
  bridge.accept(page([trade(1)]));
  assert.equal(store.getState().session?.lastTradeLagMs, null);
  const skewed = trade(2);
  skewed.asof = { ...skewed.asof, exchange_event_wall_ns: '1700000000000000001' };
  bridge.accept(page([skewed]));
  assert.equal(store.getState().session?.lastTradeLagMs, null);
  const zero = trade(3);
  zero.asof = { ...zero.asof, exchange_event_wall_ns: zero.asof.receive_wall_ns };
  bridge.accept(page([zero]));
  assert.equal(store.getState().session?.lastTradeLagMs, 0);
});

test('same-cursor boundaries precede trades and each native invalidation resets signed studies', () => {
  for (const kind of ['gap', 'reset', 'disconnect', 'source_change', 'rejection'] as const) {
    const { store, bridge } = setup();
    bridge.accept(page([trade(0)]));
    const next = page([trade(2)], { boundaries: [boundary(2, { kind })] });
    bridge.accept(next);
    assert.equal(store.getState().session?.trades, 1, kind);
    assert.equal(store.getState().liveBar?.cvd, 1, kind);
    bridge.accept(next);
    assert.equal(store.getState().session?.trades, 1, kind);
  }
});

function quotedInstrument(venue: string, quote: string, suffix = quote): Instrument {
  return { ...primary, instrument_id: `native:${venue}:BTC-${suffix}`, venue, native_symbol: `BTC-${suffix}`,
    quote: { asset_id: quote, ticker: quote, native_token_id: null } };
}

test('display quote eligibility uses exact native identity, never ticker spelling or case folding', () => {
  for (const quote of ['USD', 'USDT', 'USDC']) {
    assert.equal(displayQuoteEligible(primary, quotedInstrument('peer', quote)), true, quote);
  }
  for (const quote of ['usd', 'usdt', 'USD1', 'venue:USD', 'BRL', 'EUR', 'BTC']) {
    const peer = quotedInstrument('peer', quote);
    peer.quote.ticker = 'USD';
    assert.equal(displayQuoteEligible(primary, peer), false, quote);
    assert.equal(displayQuoteEligible(peer, { ...peer, venue: 'other' }), true, quote);
  }
});

test('old multi-currency recordings keep USD 100 and exclude BRL 500 from every display study', () => {
  const excluded = quotedInstrument('foreign', 'venue:BRL');
  const control = setup(), mixed = setup();
  for (const { store, bridge } of [control, mixed]) {
    store.getState().setVenue('all-spot'); store.getState().setSelectedVenues([]); bridge.configure();
  }
  const valid = trade(1, { trade: { ...trade(1).trade, price: '100' }, quote_notional: '100' });
  const foreign = trade(0, {
    instrument_id: excluded.instrument_id,
    trade: { ...trade(0).trade, price: '500', taker_side: 'unknown', is_subscription_history: true },
    asof: { ...trade(0).asof, clock_id: 'foreign-clock', receive_wall_ns: '1800000000000000000' },
    source_identity: { ...trade(0).source_identity, clock_id: 'foreign-clock' },
  });
  const changedForeign = { ...foreign, cursor: trade(3).cursor,
    trade: { ...foreign.trade, is_subscription_history: false },
    source_identity: { ...foreign.source_identity, connection_epoch: '2' } };
  const foreignBook = book(excluded, '500');
  foreignBook.quality.reason_codes = ['foreign-book-only'];
  control.bridge.accept(page([valid], { books: [book(primary)] }));
  mixed.bridge.accept(page([foreign, valid, changedForeign], {
    instruments: [excluded, primary], books: [foreignBook, book(primary)],
    boundaries: [boundary(2, { instrument_id: excluded.instrument_id })],
  }));
  const state = mixed.store.getState(), expected = control.store.getState();
  assert.deepEqual(state.displayStatus.instrumentIds, [primary.instrument_id]);
  assert.equal(state.ticker?.high, 100);
  assert.equal(state.session?.trades, 1);
  assert.equal(state.recentTrades[0]?.timestamp, 1700000000000);
  for (const field of ['session', 'ticker', 'currentCandle', 'candles', 'volumeProfile', 'liveBar', 'flowBars',
    'recentTrades', 'orderBook', 'aggregatedBook', 'obiHistory', 'connections', 'displayStatus'] as const) {
    assert.deepEqual(state[field], expected[field], field);
  }
  mixed.store.getState().setTimeframe('1m'); mixed.bridge.configure();
  assert.equal(mixed.store.getState().session?.trades, 1);
  assert.equal(mixed.store.getState().ticker?.high, 100);
  assert.equal(mixed.store.getState().displayStatus.gap, false);
});

test('USD-family aggregates retain legitimate USDT and USDC native prices as indicative only', () => {
  const { store, bridge } = setup();
  const usdt = quotedInstrument('tether', 'USDT'), usdc = quotedInstrument('circle', 'USDC');
  store.getState().setVenue('all-spot'); store.getState().setSelectedVenues([]); bridge.configure();
  bridge.accept(page([trade(0), trade(1, { instrument_id: usdt.instrument_id, stream_id: 'tether',
    trade: { ...trade(1).trade, price: '101' } }), trade(2, { instrument_id: usdc.instrument_id, stream_id: 'circle',
    trade: { ...trade(2).trade, price: '99' } })], {
    instruments: [primary, usdt, usdc], books: [book(primary), book(usdt), book(usdc)],
  }));
  const state = store.getState();
  assert.deepEqual(state.displayStatus.instrumentIds, [primary.instrument_id, usdt.instrument_id, usdc.instrument_id]);
  assert.equal(state.session?.trades, 3);
  assert.equal(state.ticker?.high, 101);
  assert.equal(state.ticker?.low, 99);
  assert.equal(state.liveBar?.cvd, 3);
  assert.equal(state.volumeProfile?.totalVolume, 3);
  assert.equal(state.aggregatedBook?.bids[0]?.size, 6);
  assert.ok(state.displayStatus.reasons.includes('Indicative cross-quote aggregation; native quotes are not converted'));
});

test('non-USD aggregates require exact scoped quote identity and preserve explicit native primary', () => {
  const euro = quotedInstrument('one', 'venue:EUR');
  euro.quote.ticker = 'EUR';
  const exact = quotedInstrument('two', 'venue:EUR'), differentScope = quotedInstrument('three', 'other:EUR');
  differentScope.quote.ticker = 'EUR';
  const dollar = quotedInstrument('four', 'USD');
  const { store, bridge } = setup(euro);
  store.getState().setVenue('all-spot'); store.getState().setSelectedVenues([]); bridge.configure();
  const instruments = [euro, exact, differentScope, dollar];
  bridge.accept(page(instruments.map((instrument, event) => trade(event, {
    instrument_id: instrument.instrument_id, stream_id: instrument.instrument_id,
  })), { instruments, books: instruments.map(instrument => book(instrument)) }));
  assert.deepEqual(store.getState().displayStatus.instrumentIds, [euro.instrument_id, exact.instrument_id]);
  assert.equal(store.getState().session?.trades, 2);
  assert.deepEqual(Object.keys(store.getState().connections), ['one', 'two']);
  assert.ok(!store.getState().displayStatus.reasons.some(reason => reason.includes('cross-quote')));
  store.getState().setVenue('one'); bridge.configure();
  assert.deepEqual(store.getState().displayStatus.instrumentIds, [euro.instrument_id]);
  assert.equal(store.getState().session?.trades, 1);
  assert.equal(store.getState().orderBook?.native?.instrument_id, euro.instrument_id);
});

test('duplicate venue quotes choose exact primary, then exact quote, stablecoin rank and native ID', () => {
  const primaryDuplicate = quotedInstrument('one', 'USD', 'AAA');
  const exact = quotedInstrument('two', 'USD'), exactDuplicate = quotedInstrument('two', 'USD', 'ZZZ');
  const secondUsdt = quotedInstrument('two', 'USDT');
  const thirdUsdt = quotedInstrument('three', 'USDT'), thirdUsdc = quotedInstrument('three', 'USDC');
  const thirdDuplicate = quotedInstrument('three', 'USDT', 'ZZZ');
  const instruments = [primary, primaryDuplicate, secondUsdt, exactDuplicate, exact, thirdUsdc, thirdDuplicate, thirdUsdt];
  const expectedIds = [primary.instrument_id, exact.instrument_id, thirdUsdt.instrument_id];
  assert.deepEqual(displayCohort(instruments, primary).map(item => item.instrument_id), expectedIds);
  assert.deepEqual(displayCohort([...instruments].reverse(), primary).map(item => item.instrument_id).sort(), [...expectedIds].sort());
  const { store, bridge } = setup();
  store.getState().setVenue('all-spot'); store.getState().setSelectedVenues([]); bridge.configure();
  bridge.accept(page(instruments.map((instrument, event) => trade(event, {
    instrument_id: instrument.instrument_id, stream_id: instrument.instrument_id,
    trade: { ...trade(event).trade, price: expectedIds.includes(instrument.instrument_id) ? '100' : '500' },
  })), { instruments, books: instruments.map(instrument => book(instrument)) }));
  assert.deepEqual(store.getState().displayStatus.instrumentIds, expectedIds);
  assert.equal(store.getState().ticker?.high, 100);
  assert.equal(store.getState().session?.trades, 3);
  assert.equal(store.getState().liveBar?.cvd, 3);
  assert.equal(store.getState().volumeProfile?.totalVolume, 3);
  assert.equal(store.getState().aggregatedBook?.bids[0]?.size, 6);
  store.getState().setVenue('one'); bridge.configure();
  assert.deepEqual(store.getState().displayStatus.instrumentIds, [primary.instrument_id]);
  assert.equal(store.getState().session?.trades, 1);
  store.getState().setVenue('all-spot'); store.getState().setSelectedVenues([]); bridge.configure();
  assert.equal(store.getState().session?.trades, 3);
});

test('a changed winning native instrument rebuilds every aggregate study from the chosen IDs', () => {
  const fallback = quotedInstrument('two', 'USDT'), exact = quotedInstrument('two', 'USD');
  const { store, bridge } = setup();
  store.getState().setVenue('all-spot'); store.getState().setSelectedVenues([]); bridge.configure();
  bridge.accept(page([trade(0), trade(1, { instrument_id: fallback.instrument_id, stream_id: 'fallback',
    trade: { ...trade(1).trade, price: '110' } })], {
    instruments: [primary, fallback], books: [book(primary), book(fallback)],
  }));
  assert.equal(store.getState().ticker?.high, 110);
  bridge.accept(page([trade(2, { instrument_id: exact.instrument_id, stream_id: 'exact',
    trade: { ...trade(2).trade, price: '101' } })], {
    instruments: [primary, fallback, exact], books: [book(primary), book(fallback), book(exact)],
  }));
  assert.deepEqual(store.getState().displayStatus.instrumentIds, [primary.instrument_id, exact.instrument_id]);
  assert.equal(store.getState().ticker?.high, 101);
  assert.equal(store.getState().session?.trades, 2);
  assert.equal(store.getState().liveBar?.cvd, 2);
  assert.equal(store.getState().volumeProfile?.totalVolume, 2);
  assert.equal(store.getState().aggregatedBook?.bids[0]?.size, 4);
  assert.equal(store.getState().obiHistory.length, 1);
});

test('single-native view ignores peer continuity and clocks while preserving its own source resets', () => {
  const alternate = quotedInstrument('two', 'USDT');
  const { store, bridge } = setup();
  const instruments = [primary, alternate], books = [book(primary), book(alternate)];
  bridge.accept(page([trade(0), trade(1, { instrument_id: alternate.instrument_id, stream_id: 'other' })], { instruments, books }));
  const depth = store.getState().obiHistory;
  bridge.accept(page([], { instruments, books, cursor: trade(2).cursor,
    boundaries: [boundary(2, { instrument_id: alternate.instrument_id, stream_id: 'other' })] }));
  assert.equal(store.getState().displayStatus.gap, false);
  assert.equal(store.getState().session?.trades, 1);
  bridge.accept(page([trade(3, { instrument_id: alternate.instrument_id, stream_id: 'other' })], { instruments, books }));
  const next = trade(4, {
    source_identity: { ...trade(4).source_identity, clock_id: 'next-clock' },
    asof: { ...trade(4).asof, clock_id: 'next-clock', receive_mono_ns: '10', receive_wall_ns: '1700000060000000000' },
  });
  bridge.accept(page([next], { instruments, books }));
  assert.deepEqual(store.getState().displayStatus.instrumentIds, [primary.instrument_id]);
  assert.equal(store.getState().session?.trades, 1);
  assert.deepEqual(store.getState().recentTrades.map(value => value.native), [next]);
  assert.equal(store.getState().recentTrades[0]?.timestamp, 1700000060000);
  assert.equal(store.getState().displayStatus.gap, true);
  assert.deepEqual(store.getState().obiHistory, depth);
});

test('aggregate scope refresh advertises only quote-eligible venues from old catalogs', () => {
  const { store, bridge } = setup();
  const foreign = quotedInstrument('foreign', 'BRL'), stable = quotedInstrument('stable', 'USDC');
  store.getState().setVenue('all-spot');
  bridge.reset({ sessionId: 's', generation: '2', sourceId: 'replay', sourceMode: 'replay', primary });
  bridge.accept(page([], { generation: '2', instruments: [foreign, stable, primary],
    books: [book(foreign), book(stable), book(primary)] }));
  assert.deepEqual(store.getState().selectedVenues, ['one', 'stable']);
  assert.deepEqual(store.getState().displayStatus.instrumentIds, [primary.instrument_id, stable.instrument_id]);
  assert.deepEqual(Object.keys(store.getState().connections).sort(), ['one', 'stable']);
});
