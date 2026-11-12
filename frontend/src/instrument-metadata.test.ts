import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Frame, Instrument } from './api.ts';
import { initialLiveInstrument, metadataNotice, metadataVerified, nativeReadinessEvidence, selectableInstrument } from './instrument-metadata.ts';
import { retainConfirmedCatalog } from './integration/nativeWorkspace.ts';

function candidate(venue = 'bybit', patch: Partial<Instrument> = {}): Instrument {
  return {
    instrument_id: `${venue}:spot:BTCUSDT`, venue, api_family: 'spot', native_symbol: 'BTCUSDT',
    base: { asset_id: 'BTC', ticker: 'BTC', native_token_id: null },
    quote: { asset_id: 'USDT', ticker: 'USDT', native_token_id: null }, settlement: null,
    market_type: 'spot', tick_size: null, lot_size: null, quantity_unit: 'base',
    status: 'unknown', data_access: 'unknown', execution_access: 'unknown',
    metadata_status: 'streaming_only', metadata_version: 'candidate-1', ...patch,
  };
}
function scope(instrument: Instrument): Pick<Frame, 'session_id' | 'generation' | 'source' | 'selection'> {
  return { session_id: 'session', generation: '1',
    source: { id: 'live', mode: 'live', label: 'Live', status: 'ready', synthetic: false, recording: false, duration_ns: null, streams: 2, reasons: [] },
    selection: { instrument_id: instrument.instrument_id, metadata_status: instrument.metadata_status, metadata_version: instrument.metadata_version } };
}

test('live startup accepts unknown protocol candidates without inventing native precision', () => {
  const bybit = candidate();
  const binance = candidate('binance-global');
  assert.equal(initialLiveInstrument([bybit]), bybit);
  assert.equal(initialLiveInstrument([bybit, binance]), binance);
  for (const instrument of [bybit, binance]) {
    assert.equal(selectableInstrument(instrument), true);
    assert.equal(metadataVerified(scope(instrument)), false);
    assert.equal(instrument.status, 'unknown');
    assert.equal(instrument.tick_size, null);
    assert.equal(instrument.lot_size, null);
  }
});

test('startup excludes forbidden states and blocked candidates while retaining accessible verified preference', () => {
  const unverified = candidate();
  const verified = candidate('hyperliquid', { native_symbol: 'BTC', market_type: 'perpetual', status: 'active', data_access: 'available', metadata_status: 'verified', metadata_version: 'verified-1', tick_size: '1', lot_size: '0.001' });
  assert.equal(initialLiveInstrument([candidate('binance-global'), verified]), verified);
  assert.equal(metadataVerified(scope(verified)), true);
  for (const status of ['suspended', 'delisted', 'unavailable']) {
    assert.equal(selectableInstrument({ ...unverified, status }), false);
    assert.equal(initialLiveInstrument([{ ...unverified, status }]), undefined);
  }
  assert.equal(initialLiveInstrument([{ ...unverified, data_access: 'blocked' }]), undefined);
  assert.equal(selectableInstrument({ ...unverified, metadata_status: 'verified' }), false);
  assert.equal(initialLiveInstrument([]), undefined);
});

test('catalog enrichment cannot enable financial actions or change the session-pinned tick and lot', () => {
  const streaming = candidate();
  const frame = scope(streaming);
  const confirmed = retainConfirmedCatalog(null, frame, [streaming], null);
  const verified = { ...streaming, metadata_status: 'verified' as const, metadata_version: 'rest-2', status: 'active', tick_size: '0.01', lot_size: '0.001' };
  const enriched = retainConfirmedCatalog(confirmed, frame, [verified], null);
  assert.equal(enriched?.instruments[0], verified);
  assert.equal(enriched?.selected, streaming);
  assert.equal(enriched?.selected?.lot_size, null);
  assert.equal(metadataVerified(frame), false);
  assert.match(metadataNotice(frame, [verified])!, /Streaming-only metadata/);
  assert.match(metadataNotice(frame, [verified])!, /Close other sessions and stop recordings/);
  // Reconnecting may join another owner's still-pinned shared stream.
  const joined = { ...frame, session_id: 'another-session' };
  assert.equal(metadataVerified(joined), false);
  assert.equal(retainConfirmedCatalog(enriched, joined, [verified], null)?.selected, undefined);
  const adopted = { ...joined, selection: { ...joined.selection, metadata_status: 'verified' as const, metadata_version: verified.metadata_version } };
  assert.equal(retainConfirmedCatalog(enriched, adopted, [verified], null)?.selected, verified);
  assert.equal(metadataVerified(adopted), true);
  assert.equal(metadataNotice(adopted, [verified]), null);
});

test('missing pinned metadata never borrows catalogue verification and version changes invalidate retention', () => {
  const instrument = candidate();
  const frame = scope(instrument);
  const confirmed = retainConfirmedCatalog(null, frame, [instrument], null);
  const missing = { ...frame, selection: { ...frame.selection, metadata_status: null, metadata_version: null } };
  assert.equal(metadataVerified(null), false);
  assert.equal(metadataVerified(missing), false);
  assert.equal(retainConfirmedCatalog(confirmed, missing, [instrument], 'refresh failed'), null);
  assert.equal(retainConfirmedCatalog(confirmed, { ...frame, selection: { ...frame.selection, metadata_version: 'candidate-2' } }, [], 'refresh failed'), null);
  assert.equal(metadataVerified({ selection: { instrument_id: null, metadata_status: 'verified', metadata_version: '1' } }), false);
});

test('native readiness separates catalog candidates, selected books, prints and pinned verification', () => {
  const instrument = candidate();
  const frame: Pick<Frame, 'selection' | 'book' | 'analytics'> = {
    selection: scope(instrument).selection, book: null, analytics: null,
  };
  const none = { bookObserved: false, tradesObserved: false, metadataVerified: false };
  assert.deepEqual(nativeReadinessEvidence(null), none);
  assert.deepEqual(nativeReadinessEvidence(frame), none);
  const asof = { session_id: 'capture', ingest_ordinal: '1', event_index: '0', clock_id: 'clock',
    receive_wall_ns: '1000000000', receive_mono_ns: '1', exchange_event_wall_ns: null };
  const book: NonNullable<Frame['book']> = {
    instrument_id: instrument.instrument_id, bids: [], asks: [], asof, revision: '1',
    quality: { lifecycle: 'live', integrity: 'valid', source_mode: 'live', recorded_source_mode: null,
      reason_codes: [], capture_complete: true,
      coverage: { subscribed_depth: '20', retained_bid_levels: '0', retained_ask_levels: '0', full_depth: false } },
    presentation: { depth_limit: 20, bids_omitted: 0, asks_omitted: 0 },
  };
  assert.deepEqual(nativeReadinessEvidence({ ...frame, book }), { ...none, bookObserved: true });
  const analytics: NonNullable<Frame['analytics']> = {
    instrument_id: instrument.instrument_id, metrics: [], profile: [], display_evidence: [], reasons: [],
    presentation: { profile_limit: 20, profile_omitted: 0, evidence_limit: 20, evidence_omitted: 0 },
    tape: [{ asof, stream_id: 'trades', base_quantity: '1', quote_notional: '10',
      trade: { trade_id: '1', dedup_key: '1', price: '10', quantity: '1', quantity_unit: 'base',
        taker_side: 'unknown', side_provenance: 'unknown', is_subscription_history: false } }],
  };
  assert.deepEqual(nativeReadinessEvidence({ ...frame, analytics }), { ...none, tradesObserved: true });
  assert.deepEqual(nativeReadinessEvidence({ ...frame,
    book: { ...book, instrument_id: 'another-primary' },
    analytics: { ...analytics, instrument_id: 'another-primary' } }), none);
  assert.deepEqual(nativeReadinessEvidence({ ...frame, analytics: { ...analytics,
    tape: analytics.tape.map(print => ({ ...print, trade: { ...print.trade, is_subscription_history: true } })) } }), none);
  const verified = { ...frame, selection: { ...frame.selection, metadata_status: 'verified' as const } };
  assert.deepEqual(nativeReadinessEvidence(verified), { ...none, metadataVerified: true });
  assert.deepEqual(nativeReadinessEvidence({ ...verified,
    selection: { ...verified.selection, metadata_version: null } }), none);
});

test('denied catalog access remains unavailable and cannot verify a streaming-only session', () => {
  const pinned = candidate();
  const denied = { ...pinned, data_access: 'blocked', metadata_status: 'verified' as const, metadata_version: 'newer' };
  assert.equal(selectableInstrument(denied), false);
  assert.equal(initialLiveInstrument([denied]), undefined);
  assert.equal(metadataVerified(scope(pinned)), false);
  assert.match(metadataNotice(scope(pinned), [denied])!, /Subscription candidacy does not establish data access/);
  assert.match(metadataNotice(scope(pinned), [denied])!, /read-only studies are not financial execution inputs/);
});
