import { test } from 'node:test';
import assert from 'node:assert/strict';
import { CandleHistoryQuery, candleQueryUsable } from './candle-api.ts';
import type { CandlePageRequest, CandlePageTransport } from './candle-api.ts';
import type { Frame } from './api.ts';
import { MAX_RETAINED_CANDLES, mergeCandleHistory, normalizeCandlePage, validateCandles } from './candle-model.ts';
import type { CandleHistory, CandleRequestIdentity, PriceCandle } from './candle-model.ts';

const origin = 1_704_067_200_000_000_000n;
const minute = 60_000_000_000n;
const timestamp = (index: number) => (origin + BigInt(index) * minute).toString();
const expected: CandleRequestIdentity = { session_id: 'session', generation: '4', instrument_id: 'native',
  instrument_ids: ['native'], interval: '1m', source: 'venue_ohlcv' };
function bar(index: number, close = '101.000000000000000001'): PriceCandle {
  return { start_ns: timestamp(index), end_ns: timestamp(index + 1), open: '100', high: '103', low: '99',
    close, volume: '0.000000000000000001', closed: true };
}
function page(indices: number[], before: string | null = null, overrides: Partial<CandleHistory> = {}): CandleHistory {
  const candles = indices.map(index => bar(index));
  return { ...expected, version: '1', source_identity: 'live:clock', series_key: 'venue:spot:native:USD:1m:utc',
    scope: 'primary', venue_label: 'Native venue', quote_asset_ids: ['asset:USD'], partial: false,
    status: candles.length ? 'ready' : 'empty', venue: 'venue', time_basis: 'exchange_utc', fetched_at_ns: timestamp(100),
    supported_intervals: ['1m', '1h'], candles, reasons: [], volume_unit: 'base',
    coverage_start_ns: candles[0]?.start_ns ?? null, coverage_end_ns: candles.at(-1)?.end_ns ?? null,
    history: { requested_before_ns: before, next_before_ns: candles.length ? timestamp(Math.min(...indices)) : null,
      state: candles.length ? 'more' : 'exhausted', pagination: 'time', max_bars: 300, update_mode: 'poll', poll_interval_ms: 5000 },
    ...overrides };
}
function observedPage(indices: number[], overrides: Partial<CandleHistory> = {}): CandleHistory {
  return page(indices, null, {
    source: 'session_trades', time_basis: 'receive_utc', partial: true, series_key: undefined, history: undefined,
    missing_instrument_ids: [], bucket_alignment: 'utc_epoch_floor', highwater: '10',
    retention: { max_trades: 100, max_bytes: 10000, retained_trades: indices.length, retained_bytes: indices.length * 100, truncated: false },
    ...overrides,
  });
}
function deferredTransport() {
  const requests: { body: CandlePageRequest; signal: AbortSignal; resolve: (value: CandleHistory) => void;
    reject: (error: Error) => void }[] = [];
  const transport: CandlePageTransport = (body, signal) => {
    const { promise, resolve, reject } = Promise.withResolvers<CandleHistory>();
    requests.push({ body, signal, resolve, reject });
    return promise;
  };
  return { requests, query: new CandleHistoryQuery(expected, transport, () => 42_000) };
}

test('out-of-order duplicate pages sort by exact bucket start and preserve exact revised economics', () => {
  const response = page([3, 1, 2, 1]);
  response.candles[3] = bar(1, '101.000000000000000002');
  const normalized = normalizeCandlePage(response);
  assert.deepEqual(normalized.candles.map(candle => candle.start_ns), [1, 2, 3].map(timestamp));
  assert.equal(normalized.candles[0]!.close, '101.000000000000000002');
  assert.equal(normalized.candles[0]!.volume, '0.000000000000000001');
  assert.deepEqual(response.candles.map(candle => candle.start_ns), [3, 1, 2, 1].map(timestamp));
});

test('older-page and newest-poll races coalesce without losing pages or overwriting revisions', async () => {
  const { query, requests } = deferredTransport();
  const first = query.refresh();
  assert.equal(query.refresh(), first);
  requests[0]!.resolve(page([3, 4]));
  await first;
  const older = query.loadOlder();
  assert.equal(query.loadOlder(), older);
  const latest = query.refresh();
  assert.equal(requests.length, 3);
  assert.equal(requests[1]!.body.before_ns, timestamp(3));
  const correction = page([2, 3, 4, 5]);
  correction.candles[0] = bar(2, '102');
  requests[2]!.resolve(correction);
  await latest;
  requests[1]!.resolve(page([0, 1, 2], timestamp(3)));
  await older;
  const state = query.getSnapshot();
  assert.deepEqual(state.data!.candles.map(candle => candle.start_ns), [0, 1, 2, 3, 4, 5].map(timestamp));
  assert.equal(state.data!.candles[2]!.close, '102');
  assert.equal(state.data!.history!.next_before_ns, timestamp(0));
  assert.equal(state.receivedAt, 42_000);
  assert.equal(state.olderLoading, false);
  assert.equal(state.error, null);
});

test('newest completion after older completion retains the loaded left cursor', async () => {
  const { query, requests } = deferredTransport();
  const first = query.refresh(); requests[0]!.resolve(page([3, 4])); await first;
  const latest = query.refresh();
  const older = query.loadOlder();
  requests[2]!.resolve(page([1, 2], timestamp(3))); await older;
  requests[1]!.resolve(page([4, 5])); await latest;
  assert.deepEqual(query.getSnapshot().data!.candles.map(candle => candle.start_ns), [1, 2, 3, 4, 5].map(timestamp));
  assert.equal(query.getSnapshot().data!.history!.next_before_ns, timestamp(1));
});

test('left-page failures remain retryable without labeling current live candles stale', async () => {
  const { query, requests } = deferredTransport();
  const first = query.refresh(); requests[0]!.resolve(page([3, 4])); await first;
  const older = query.loadOlder(); requests[1]!.reject(new Error('Rate limited')); await older;
  assert.equal(query.getSnapshot().error, null);
  assert.equal(query.getSnapshot().olderError, 'Rate limited');
  assert.equal(query.getSnapshot().hasMore, true);
  assert.equal(query.getSnapshot().receivedAt, 42_000);
  const retry = query.loadOlder();
  assert.equal(requests[2]!.body.before_ns, timestamp(3));
  requests[2]!.resolve(page([2], timestamp(3))); await retry;
  assert.equal(query.getSnapshot().olderError, null);
  assert.equal(query.getSnapshot().data!.candles.length, 3);
});

test('failed newest polling retains same-series data and successful retry replaces the forming bar', async () => {
  const { query, requests } = deferredTransport();
  const first = query.refresh(); requests[0]!.resolve(page([1], null, { candles: [bar(1, '100')] })); await first;
  const retry = query.refresh(); requests[1]!.reject(new Error('Disconnected')); await retry;
  assert.equal(query.getSnapshot().data!.candles[0]!.close, '100');
  assert.equal(query.getSnapshot().error, 'Disconnected');
  const recovered = query.refresh(); requests[2]!.resolve(page([1, 2])); await recovered;
  assert.equal(query.getSnapshot().error, null);
  assert.equal(query.getSnapshot().data!.candles[0]!.close, '101.000000000000000001');
});

test('source, series, clock, generation, interval and cohort mismatches drop unverifiable data', async () => {
  for (const mismatch of [
    { source_identity: 'other-clock' }, { series_key: 'other-exact-instrument' }, { time_basis: 'receive_utc' as const },
    { generation: '5' }, { interval: '1h' }, { instrument_ids: ['native', 'peer'], scope: 'aggregate' as const },
  ]) {
    const { query, requests } = deferredTransport();
    const first = query.refresh(); requests[0]!.resolve(page([1, 2])); await first;
    const changed = query.refresh(); requests[1]!.resolve(page([1, 2], null, mismatch)); await changed;
    assert.equal(query.getSnapshot().data, null);
    assert.match(query.getSnapshot().error!, /different source, series/);
    assert.equal(query.getSnapshot().receivedAt, null);
    const retry = query.refresh(); requests[2]!.resolve(page([2, 3])); await retry;
    assert.equal(query.getSnapshot().error, null);
  }
});

test('cancellation and explicit reset fence even transports that resolve after abort', async () => {
  const { query, requests } = deferredTransport();
  const obsolete = query.refresh();
  query.cancel();
  assert.equal(requests[0]!.signal.aborted, true);
  const fresh = query.refresh();
  requests[1]!.resolve(page([8])); await fresh;
  requests[0]!.resolve(page([1])); await obsolete;
  assert.equal(query.getSnapshot().data!.candles[0]!.start_ns, timestamp(8));
  const oldPage = query.loadOlder();
  query.reset();
  assert.equal(requests[2]!.signal.aborted, true);
  requests[2]!.resolve(page([7], timestamp(8))); await oldPage;
  assert.equal(query.getSnapshot().data, null);
  assert.equal(query.getSnapshot().olderLoading, false);
  assert.equal(query.getSnapshot().hasMore, false);
});

test('reconnect retrieves intervening pages before claiming current coverage', async () => {
  const calls: CandlePageRequest[] = [];
  const responses = [page([0, 1]), page([5, 6]), page([3, 4], timestamp(5)), page([1, 2], timestamp(3))];
  const query = new CandleHistoryQuery(expected, async body => { calls.push(body); return responses.shift()!; });
  const states: string[] = [];
  query.subscribe(() => { if (query.getSnapshot().error) states.push(query.getSnapshot().error!); });
  await query.refresh();
  await query.refresh();
  assert.deepEqual(calls.map(body => body.before_ns ?? null), [null, null, timestamp(5), timestamp(3)]);
  assert.deepEqual(query.getSnapshot().data!.candles.map(candle => candle.start_ns), [0, 1, 2, 3, 4, 5, 6].map(timestamp));
  assert.ok(states.some(error => error.includes('Incomplete history')));
  assert.equal(query.getSnapshot().error, null);
  assert.equal(query.getSnapshot().data!.history!.next_before_ns, timestamp(0));
});

test('failed reconnect does not invent missing bars and retry retains the original gap target', async () => {
  const calls: CandlePageRequest[] = [];
  let number = 0;
  const query = new CandleHistoryQuery(expected, async body => {
    calls.push(body);
    number++;
    if (number === 1) return page([0, 1]);
    if (number === 3) throw new Error('Recovery unavailable');
    if (body.before_ns === timestamp(5)) return page([1, 2, 3, 4], timestamp(5));
    return page([5, 6]);
  });
  await query.refresh(); await query.refresh();
  assert.match(query.getSnapshot().error!, /Incomplete history: Recovery unavailable/);
  assert.deepEqual(query.getSnapshot().data!.candles.map(candle => candle.start_ns), [0, 1, 5, 6].map(timestamp));
  await query.refresh();
  assert.equal(calls.at(-1)!.before_ns, timestamp(5));
  assert.equal(query.getSnapshot().error, null);
  assert.equal(query.getSnapshot().data!.candles.length, 7);
});

test('reconnect pages are bounded and recent-only history exposes incomplete coverage', async () => {
  let requests = 0;
  const query = new CandleHistoryQuery(expected, async body => {
    requests++;
    if (requests === 1) return page([0]);
    const index = body.before_ns ? Number((BigInt(body.before_ns) - origin) / minute) - 1 : 100;
    return page([index], body.before_ns ?? null);
  });
  await query.refresh(); await query.refresh();
  assert.equal(requests, 34);
  assert.match(query.getSnapshot().error!, /Incomplete history:.*bounded history window/);
  assert.equal(query.getSnapshot().data!.candles.length, 34);
  let poll = 0;
  const recent = new CandleHistoryQuery(expected, async () => {
    const response = page([poll++ ? 10 : 0]);
    return { ...response, history: { ...response.history!, state: 'limited', pagination: 'recent_only', next_before_ns: null } };
  });
  await recent.refresh(); await recent.refresh();
  assert.equal(recent.getSnapshot().hasMore, false);
  assert.match(recent.getSnapshot().error!, /Incomplete history/);
});

test('empty or short pages obey metadata rather than guessing exhaustion', async () => {
  let calls = 0;
  const query = new CandleHistoryQuery(expected, async body => {
    calls++;
    if (calls === 1) return page([4]);
    if (calls === 2) return page([], body.before_ns, { history: { ...page([]).history!, requested_before_ns: body.before_ns!,
      next_before_ns: timestamp(2), state: 'more' } });
    return page([], body.before_ns);
  });
  await query.refresh();
  assert.equal(query.getSnapshot().hasMore, true);
  await query.loadOlder();
  assert.equal(query.getSnapshot().hasMore, true);
  assert.equal(query.getSnapshot().data!.candles.length, 1);
  await query.loadOlder();
  assert.equal(query.getSnapshot().hasMore, false);
  assert.equal(query.getSnapshot().data!.history!.state, 'exhausted');
  assert.equal(query.getSnapshot().data!.candles.length, 1);
});

test('venue metadata requires bounded decreasing exclusive cursors and explicit series identity', () => {
  const response = page([1, 2]);
  assert.throws(() => validateCandles({ ...response, series_key: undefined }));
  assert.throws(() => validateCandles({ ...response, history: undefined }));
  assert.throws(() => validateCandles({ ...response, history: { ...response.history!, requested_before_ns: timestamp(1), next_before_ns: timestamp(1) } }));
  assert.throws(() => validateCandles({ ...response, history: { ...response.history!, next_before_ns: '9223372036854775808' } }));
  assert.throws(() => normalizeCandlePage(page([1, 2], timestamp(2))));
});

test('10,000-bar retention pauses growth without pruning any visible bucket and still accepts revisions', () => {
  const previous = page(Array.from({ length: MAX_RETAINED_CANDLES }, (_, index) => index));
  const next = page([MAX_RETAINED_CANDLES - 1, MAX_RETAINED_CANDLES]);
  next.candles[0] = bar(MAX_RETAINED_CANDLES - 1, '102');
  const merged = mergeCandleHistory(previous, next);
  assert.equal(merged.historyLimitReached, true);
  assert.equal(merged.data.candles.length, MAX_RETAINED_CANDLES);
  assert.equal(merged.data.candles[0]!.start_ns, timestamp(0));
  assert.equal(merged.data.candles.at(-1)!.start_ns, timestamp(MAX_RETAINED_CANDLES - 1));
  assert.equal(merged.data.candles.at(-1)!.close, '102');
});

test('mounted query exposes its cap, stops older requests, and resets only on explicit intent', async () => {
  const calls: CandlePageRequest[] = [];
  const query = new CandleHistoryQuery(expected, async body => {
    calls.push(body);
    const end = body.before_ns ? Number((BigInt(body.before_ns) - origin) / minute) : 20_000;
    return page(Array.from({ length: body.limit }, (_, index) => end - body.limit + index), body.before_ns ?? null);
  });
  await query.refresh();
  while (query.getSnapshot().hasMore) await query.loadOlder();
  assert.equal(query.getSnapshot().data!.candles.length, MAX_RETAINED_CANDLES);
  assert.equal(query.getSnapshot().historyLimitReached, true);
  assert.equal(calls.at(-1)!.limit, 100);
  const count = calls.length;
  const first = query.getSnapshot().data!.candles[0]!.start_ns;
  await query.loadOlder();
  assert.equal(calls.length, count);
  await query.refresh();
  assert.equal(query.getSnapshot().data!.candles[0]!.start_ns, first);
  query.reset();
  assert.equal(query.getSnapshot().historyLimitReached, false);
  assert.equal(query.getSnapshot().data, null);
  await query.refresh();
  assert.equal(query.getSnapshot().data!.candles.length, 300);
  assert.equal(query.getSnapshot().hasMore, true);
});

test('requested unsupported intervals stay explicit and observed snapshots never accumulate future history', async () => {
  const unsupported = new CandleHistoryQuery({ ...expected, interval: '1M' }, async body => page([], null, {
    interval: body.interval, status: 'unavailable', reasons: ['unsupported_interval'],
    history: { ...page([]).history!, state: 'unsupported' },
  }));
  await unsupported.refresh();
  assert.equal(unsupported.getSnapshot().data!.interval, '1M');
  assert.equal(unsupported.getSnapshot().data!.status, 'unavailable');
  assert.deepEqual(unsupported.getSnapshot().data!.supported_intervals, ['1m', '1h']);
  assert.equal(unsupported.getSnapshot().hasMore, false);
  let calls = 0;
  const observed = new CandleHistoryQuery({ ...expected, source: 'session_trades', instrument_ids: ['native', 'peer'] }, async body => {
    assert.deepEqual(body.instrument_ids, ['native', 'peer']);
    assert.equal(body.before_ns, undefined);
    return page([calls++ ? 1 : 8], null, { source: 'session_trades', time_basis: 'receive_utc', partial: true,
      instrument_ids: ['native', 'peer'], scope: 'aggregate', series_key: undefined, history: undefined,
      missing_instrument_ids: [], bucket_alignment: 'utc_epoch_floor', highwater: '10',
      retention: { max_trades: 100, max_bytes: 10000, retained_trades: 1, retained_bytes: 100, truncated: true } });
  });
  await observed.refresh(); await observed.refresh();
  assert.deepEqual(observed.getSnapshot().data!.candles.map(candle => candle.start_ns), [timestamp(1)]);
  assert.equal(observed.getSnapshot().hasMore, false);
});

test('overflow pauses growth persistently below the hard cap until reset', () => {
  const previous = page(Array.from({ length: 9_900 }, (_, index) => index));
  const overflow = mergeCandleHistory(previous, page(Array.from({ length: 300 }, (_, index) => 9_900 + index)));
  assert.equal(overflow.historyLimitReached, true);
  assert.equal(overflow.data.candles.length, 9_900);
  const smaller = mergeCandleHistory(overflow.data, page([9_899, 9_900]), false, overflow.historyLimitReached);
  assert.equal(smaller.historyLimitReached, true);
  assert.equal(smaller.data.candles.length, 9_900);
  assert.equal(smaller.data.candles.at(-1)!.start_ns, timestamp(9_899));
});

test('a response for the wrong pagination boundary retains prior candles and allows an older retry', async () => {
  const { query, requests } = deferredTransport();
  const initial = query.refresh(); requests[0]!.resolve(page([3, 4])); await initial;
  const older = query.loadOlder(); requests[1]!.resolve(page([1], timestamp(2))); await older;
  assert.match(query.getSnapshot().olderError!, /different history boundary/);
  assert.equal(query.getSnapshot().error, null);
  assert.deepEqual(query.getSnapshot().data!.candles.map(candle => candle.start_ns), [3, 4].map(timestamp));
  assert.equal(query.getSnapshot().hasMore, true);
});

test('venue history survives analytical failure but transport and lifecycle boundaries remain gated', () => {
  const frame: Pick<Frame, 'session_id' | 'generation' | 'selection' | 'status'> = {
    session_id: expected.session_id, generation: expected.generation, status: 'live',
    selection: { instrument_id: expected.instrument_id, metadata_status: null, metadata_version: null },
  };
  for (const status of ['live', 'paused', 'playing', 'ended', 'unavailable', 'error'] as const) {
    const current = { ...frame, status };
    assert.equal(candleQueryUsable(current, true, 'venue_ohlcv'), true, status);
    assert.equal(candleQueryUsable(current, true, 'session_trades'), !['unavailable', 'error'].includes(status), status);
    assert.equal(candleQueryUsable(current, false, 'venue_ohlcv'), false, status);
    assert.equal(candleQueryUsable(current, false, 'session_trades'), false, status);
  }
  for (const source of ['venue_ohlcv', 'session_trades'] as const) {
    for (const status of ['loading', 'seeking'] as const) assert.equal(candleQueryUsable({ ...frame, status }, true, source), false);
    assert.equal(candleQueryUsable(null, true, source), false);
    assert.equal(candleQueryUsable({ ...frame, session_id: '' }, true, source), false);
    assert.equal(candleQueryUsable({ ...frame, generation: '' }, true, source), false);
    assert.equal(candleQueryUsable({ ...frame, selection: { ...frame.selection, instrument_id: null } }, true, source), false);
  }
});

test('same-owner observed unavailability retains exact ready candles as stale until a valid snapshot arrives', async () => {
  const observed: CandleRequestIdentity = { ...expected, source: 'session_trades' };
  let response = observedPage([8]);
  let now = 42_000;
  const query = new CandleHistoryQuery(observed, async () => response, () => now);
  await query.refresh();
  const verified = query.getSnapshot().data;
  now += 5000;
  response = observedPage([], { status: 'unavailable', reasons: ['missing_receive_utc'], highwater: '0', fetched_at_ns: '0' });
  await query.refresh();
  assert.equal(query.getSnapshot().data, verified);
  assert.deepEqual(query.getSnapshot().data!.candles, [bar(8)]);
  assert.equal(query.getSnapshot().receivedAt, 42_000);
  assert.equal(query.getSnapshot().error, 'missing_receive_utc');
  assert.equal(query.getSnapshot().loading, false);
  assert.equal(query.getSnapshot().hasMore, false);
  await query.refresh();
  assert.equal(query.getSnapshot().data, verified);
  assert.equal(query.getSnapshot().receivedAt, 42_000);
  assert.equal(query.getSnapshot().error, 'missing_receive_utc');

  response = observedPage([1]);
  await query.refresh();
  assert.equal(query.getSnapshot().error, null);
  assert.equal(query.getSnapshot().receivedAt, now);
  assert.deepEqual(query.getSnapshot().data!.candles, [bar(1)]);
  response = observedPage([]);
  await query.refresh();
  assert.equal(query.getSnapshot().error, null);
  assert.equal(query.getSnapshot().data!.status, 'empty');
  assert.deepEqual(query.getSnapshot().data!.candles, []);
});

test('initial and new-generation empty or unavailable observed snapshots expose source identity without future candles', async () => {
  const observed: CandleRequestIdentity = { ...expected, source: 'session_trades' };
  const previous = new CandleHistoryQuery(observed, async () => observedPage([8]));
  await previous.refresh();
  for (const generation of [expected.generation, '5']) {
    for (const status of ['empty', 'unavailable'] as const) {
      let response = observedPage([], { generation, status, reasons: ['missing_receive_utc'], highwater: '0', fetched_at_ns: '0' });
      const query = new CandleHistoryQuery({ ...observed, generation }, async () => response);
      assert.equal(query.getSnapshot().data, null);
      await query.refresh();
      assert.equal(query.getSnapshot().error, null);
      assert.equal(query.getSnapshot().data!.status, status);
      assert.equal(query.getSnapshot().data!.generation, generation);
      assert.equal(query.getSnapshot().data!.source_identity, response.source_identity);
      assert.deepEqual(query.getSnapshot().data!.reasons, ['missing_receive_utc']);
      assert.deepEqual(query.getSnapshot().data!.candles, []);
      assert.equal(query.getSnapshot().hasMore, false);
      response = observedPage([1], { generation });
      await query.refresh();
      assert.equal(query.getSnapshot().error, null);
      assert.deepEqual(query.getSnapshot().data!.candles, [bar(1)]);
    }
  }
  assert.deepEqual(previous.getSnapshot().data!.candles, [bar(8)]);
});

test('unavailable observed responses with changed ownership clear rather than retain verified candles', async () => {
  for (const mismatch of [
    { source_identity: 'other-clock' }, { generation: '5' }, { source: 'venue_ohlcv' as const },
    { instrument_ids: ['native', 'peer'], scope: 'aggregate' as const },
  ]) {
    let response = observedPage([8]);
    const query = new CandleHistoryQuery({ ...expected, source: 'session_trades' }, async () => response);
    await query.refresh();
    response = observedPage([], { status: 'unavailable', reasons: ['missing_receive_utc'], ...mismatch });
    await query.refresh();
    assert.equal(query.getSnapshot().data, null);
    assert.equal(query.getSnapshot().receivedAt, null);
    assert.match(query.getSnapshot().error!, /different source, series/);
  }
});

test('venue unavailability keeps verified history stale rather than replacing it with an empty snapshot', async () => {
  const { query, requests } = deferredTransport();
  const initial = query.refresh(); requests[0]!.resolve(page([3, 4])); await initial;
  const unavailable = query.refresh();
  requests[1]!.resolve(page([], null, { status: 'unavailable', reasons: ['provider_unavailable'],
    history: { ...page([]).history!, state: 'unavailable' } }));
  await unavailable;
  assert.match(query.getSnapshot().error!, /provider_unavailable/);
  assert.deepEqual(query.getSnapshot().data!.candles.map(candle => candle.start_ns), [3, 4].map(timestamp));
  assert.equal(query.getSnapshot().hasMore, true);
});
