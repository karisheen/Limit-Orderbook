import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Instrument, Source } from './api.ts';
import type { ManagedRecording } from './workflow-api.ts';
import { financialInstrumentsForSelection, nativeInstrumentEvidence, nativeMetadataScope, ownedSessionRecording, paneForView, retainConfirmedCatalog, settledRecordingStop, viewForPane } from './integration/nativeWorkspace.ts';

const source: Source = { id: 'live', mode: 'live', label: 'Live', status: 'ready', synthetic: false, recording: false, duration_ns: null, streams: 2, reasons: [] };
const frame = { session_id: 'session-1', generation: '1', source, selection: { instrument_id: 'venue/BTC-USDT', metadata_status: 'verified' as const, metadata_version: '1' } };
const instrument: Instrument = {
  instrument_id: frame.selection.instrument_id, venue: 'venue', api_family: 'spot', native_symbol: 'BTCUSDT',
  base: { asset_id: 'BTC', ticker: 'BTC', native_token_id: null }, quote: { asset_id: 'USDT', ticker: 'USDT', native_token_id: null },
  settlement: null, market_type: 'spot', tick_size: '0.01', lot_size: '0.00001', quantity_unit: 'base', status: 'active',
  data_access: 'available', execution_access: 'blocked', metadata_version: '1', metadata_status: 'verified',
};

test('catalog errors retain confirmed exact instrument and cohort without changing metadata identity', () => {
  const instruments = [instrument, { ...instrument, instrument_id: 'other/BTC-USDT', venue: 'other' }];
  const confirmed = retainConfirmedCatalog(null, frame, instruments, null);
  assert.equal(confirmed?.instruments, instruments);
  assert.equal(retainConfirmedCatalog(confirmed, frame, [], 'catalog unavailable'), confirmed);
  assert.equal(retainConfirmedCatalog(confirmed, { ...frame }, [], 'catalog still unavailable'), confirmed);
  assert.equal(retainConfirmedCatalog(confirmed, frame, instruments, null), confirmed);
});

test('telemetry and source status refreshes retain selected metadata without rescanning stable discovery', () => {
  let searches = 0;
  const instruments = new Proxy([instrument], {
    get(target, property, receiver) {
      if (property === 'find' || property === 'some') searches += 1;
      return Reflect.get(target, property, receiver);
    },
  });
  const confirmed = retainConfirmedCatalog(null, frame, instruments, null);
  assert.equal(confirmed?.selected, instrument);
  assert.equal(searches, 1);
  for (let index = 0; index < 200; index += 1) {
    const refreshed = { ...frame, frame_id: `${index}`, telemetry: { queue_depth: index },
      source: { ...source, status: index % 2 ? 'ready' : 'degraded', reasons: [`status-${index}`] } };
    assert.equal(nativeMetadataScope(refreshed), nativeMetadataScope(frame));
    assert.equal(retainConfirmedCatalog(confirmed, refreshed, instruments, null), confirmed);
  }
  assert.equal(searches, 1);
});

test('equivalent catalog200 preserves drain identity while authoritative selected metadata changes fence it', () => {
  const confirmed = retainConfirmedCatalog(null, frame, [instrument], null);
  // JSON object property order is not native evidence identity.
  const { base, quote, ...rest } = structuredClone(instrument);
  const replacement = { quote, ...rest, base };
  const refreshed = retainConfirmedCatalog(confirmed, { ...frame, source: { ...source, streams: 50 } }, [replacement], null);
  assert.equal(refreshed?.selected, replacement);
  assert.equal(nativeInstrumentEvidence(refreshed?.selected), nativeInstrumentEvidence(confirmed?.selected));
  const changed = { ...replacement, tick_size: '0.1', status: 'halted' };
  const updated = retainConfirmedCatalog(refreshed, frame, [changed], null);
  assert.equal(updated?.selected, changed);
  assert.notEqual(nativeInstrumentEvidence(updated?.selected), nativeInstrumentEvidence(refreshed?.selected));
  assert.equal(nativeInstrumentEvidence(retainConfirmedCatalog(updated, frame, [], null)?.selected), null);
});

test('native drain evidence fences every selected metadata domain without relying on object equality', () => {
  const original = nativeInstrumentEvidence(instrument);
  const changes: Partial<Instrument>[] = [
    { instrument_id: 'other' }, { venue: 'other' }, { api_family: 'other' }, { native_symbol: 'other' },
    { base: { ...instrument.base, asset_id: 'other' } }, { base: { ...instrument.base, ticker: 'other' } },
    { base: { ...instrument.base, native_token_id: 'other' } },
    { quote: { ...instrument.quote, asset_id: 'other' } }, { quote: { ...instrument.quote, ticker: 'other' } },
    { quote: { ...instrument.quote, native_token_id: 'other' } }, { settlement: instrument.quote },
    { market_type: 'perpetual' }, { tick_size: '0.1' }, { lot_size: '1' }, { quantity_unit: 'contracts' },
    { status: 'halted' }, { data_access: 'blocked' }, { execution_access: 'available' },
    { metadata_version: '2' }, { metadata_status: 'streaming_only' },
    { contract_multiplier: '10' }, { contract_value_currency: 'USD' }, { inverse: true },
  ];
  for (const patch of changes) {
    assert.notEqual(nativeInstrumentEvidence({ ...instrument, ...patch }), original, JSON.stringify(patch));
  }
  assert.equal(nativeInstrumentEvidence(undefined), null);
});

test('metadata scope keys fence native ownership and selection but not volatile presentation', () => {
  const original = nativeMetadataScope(frame);
  for (const changed of [
    null,
    { ...frame, session_id: 'session-2' },
    { ...frame, generation: '2' },
    { ...frame, source: { ...source, id: 'other' } },
    { ...frame, source: { ...source, mode: 'replay' as const } },
    { ...frame, selection: { ...frame.selection, instrument_id: 'other' } },
    { ...frame, selection: { ...frame.selection, metadata_version: '2' } },
    { ...frame, selection: { ...frame.selection, metadata_status: 'streaming_only' as const } },
    { ...frame, selection: { instrument_id: null, metadata_version: null, metadata_status: null } },
  ]) assert.notEqual(nativeMetadataScope(changed), original);
});

test('stable discovery re-resolves metadata version and status when the native selection adopts them', () => {
  const verified = { ...instrument, metadata_version: '2', tick_size: '0.1' };
  const streaming = { ...verified, metadata_status: 'streaming_only' as const, tick_size: null };
  const instruments = [instrument, verified, streaming];
  const confirmed = retainConfirmedCatalog(null, frame, instruments, null);
  const nextFrame = { ...frame, selection: { ...frame.selection, metadata_version: '2' } };
  const next = retainConfirmedCatalog(confirmed, nextFrame, instruments, null);
  assert.equal(next?.selected, verified);
  const unverified = retainConfirmedCatalog(next,
    { ...nextFrame, selection: { ...nextFrame.selection, metadata_status: 'streaming_only' } }, instruments, null);
  assert.equal(unverified?.selected, streaming);
  assert.equal(unverified?.selected?.tick_size, null);
});

test('catalog failures never import metadata across session, generation, source or selection boundaries', () => {
  const confirmed = retainConfirmedCatalog(null, frame, [instrument], null);
  for (const changed of [
    null,
    { ...frame, session_id: 'session-2' },
    { ...frame, generation: '2' },
    { ...frame, source: { ...source, id: 'another-source' } },
    { ...frame, source: { ...source, mode: 'replay' as const } },
    { ...frame, selection: { ...frame.selection, instrument_id: 'other/BTC-USDT' } },
  ]) {
    // Even a nonempty stale catalog must not bypass the failed-refresh fence.
    assert.equal(retainConfirmedCatalog(confirmed, changed, [instrument], 'catalog unavailable'), null);
  }
  assert.equal(retainConfirmedCatalog(null, frame, [instrument], 'initial catalog unavailable'), null);
});

test('successful catalog refresh replaces old metadata and authoritative removal does not resurrect it', () => {
  const confirmed = retainConfirmedCatalog(null, frame, [instrument], null);
  const updated = [{ ...instrument, metadata_version: '2', tick_size: '0.1' }];
  assert.equal(retainConfirmedCatalog(confirmed, frame, updated, null)?.instruments, updated);
  assert.equal(retainConfirmedCatalog(confirmed, frame, updated, null)?.selected, instrument);
  const removed = retainConfirmedCatalog(confirmed, frame, [], null);
  assert.deepEqual(removed?.instruments, []);
  assert.equal(removed?.selected, undefined);
  assert.equal(retainConfirmedCatalog(removed, frame, [instrument], 'catalog unavailable'), removed);
});

test('financial selection retains pinned precision while discovery advances independently', () => {
  const pinned = { ...instrument };
  const newer = { ...instrument, metadata_version: '2', tick_size: '0.1', lot_size: '0.001' };
  const before = { ...instrument, instrument_id: 'before/BTC-USDT', venue: 'before' };
  const after = { ...instrument, instrument_id: 'after/BTC-USDT', venue: 'after' };
  const discovery = [before, newer, after];
  const original = structuredClone(discovery);

  const financial = financialInstrumentsForSelection(discovery, pinned.instrument_id, pinned);
  assert.deepEqual(financial, [before, pinned, after]);
  assert.equal(financial[0], before);
  assert.equal(financial[1], pinned);
  assert.equal(financial[2], after);
  assert.equal(financial[1].metadata_version, '1');
  assert.equal(financial[1].tick_size, '0.01');
  assert.equal(financial[1].lot_size, '0.00001');
  assert.equal(discovery[1], newer);
  assert.deepEqual(discovery, original);
});

test('financial selection fails closed without a matching pin and preserves other choices', () => {
  const newer = { ...instrument, metadata_version: '2', tick_size: '0.1' };
  const other = { ...instrument, instrument_id: 'other/BTC-USDT', venue: 'other' };
  const discovery = [newer, other];
  const original = structuredClone(discovery);
  for (const pin of [undefined, other]) {
    const financial = financialInstrumentsForSelection(discovery, instrument.instrument_id, pin);
    assert.deepEqual(financial, [other]);
    assert.equal(financial[0], other);
    assert.deepEqual(discovery, original);
  }
});

test('financial selection appends an absent pinned descriptor without changing discovery', () => {
  const other = { ...instrument, instrument_id: 'other/BTC-USDT', venue: 'other' };
  const discovery = [other];
  const original = structuredClone(discovery);
  const financial = financialInstrumentsForSelection(discovery, instrument.instrument_id, instrument);
  assert.deepEqual(financial, [other, instrument]);
  assert.equal(financial[0], other);
  assert.equal(financial[1], instrument);
  assert.deepEqual(discovery, original);
  assert.deepEqual(financialInstrumentsForSelection([], instrument.instrument_id, instrument), [instrument]);
  for (const selectedId of [null, undefined]) {
    assert.equal(financialInstrumentsForSelection(discovery, selectedId, instrument), discovery);
  }
});

test('every mobile pane selects its owning chart mode and preserves Flow and Tape selection', () => {
  for (const pane of ['price', 'heatmap', 'flow', 'dom', 'tape'] as const) {
    assert.equal(paneForView(viewForPane(pane), pane), pane);
  }
  assert.equal(paneForView('price', 'tape'), 'price');
  assert.equal(paneForView('orderflow', 'dom'), 'heatmap');
  assert.equal(paneForView('book', 'price'), 'dom');
});

const capture: ManagedRecording = {
  id: 'capture-1', session_id: frame.session_id, label: 'Selected instrument', status: 'recording',
  source_id: null, reasons: [], scope: 'instrument', instruments: [instrument.instrument_id],
  channels: [{ venue: instrument.venue, instrument_id: instrument.instrument_id, stream: 'book' }],
};

test('capture ownership survives incomplete and error transitions until explicit stop releases the session', () => {
  let recordings = [capture];
  assert.equal(ownedSessionRecording(recordings, frame.session_id), capture);
  for (const status of ['incomplete', 'error'] as const) {
    recordings = [{ ...recordings[0], status, reasons: ['Channel unavailable'] }];
    assert.equal(ownedSessionRecording(recordings, frame.session_id), recordings[0]);
    assert.equal(ownedSessionRecording(recordings, frame.session_id)?.id, capture.id);
  }
  recordings = [{ ...recordings[0], session_id: null }];
  assert.equal(ownedSessionRecording(recordings, frame.session_id), undefined);
});

test('archived captures never claim ownership regardless of their final status', () => {
  for (const status of ['recording', 'complete', 'incomplete', 'error'] as const) {
    const archived = { ...capture, session_id: null, status };
    assert.equal(ownedSessionRecording([archived], frame.session_id), undefined);
    assert.equal(ownedSessionRecording([archived], null), undefined);
    assert.equal(ownedSessionRecording([archived], undefined), undefined);
  }
});

test('ambiguous stop resolves only from its own released terminal recording', () => {
  assert.equal(settledRecordingStop([], capture.id), undefined);
  assert.equal(settledRecordingStop([capture], capture.id), undefined);
  assert.equal(settledRecordingStop([{ ...capture, session_id: null }], capture.id), undefined);
  assert.equal(settledRecordingStop([{ ...capture, id: 'another', status: 'complete', session_id: null }], capture.id), undefined);
  for (const status of ['complete', 'incomplete', 'error'] as const) {
    const retained = { ...capture, status, reasons: status === 'complete' ? [] : ['native verification failed'] };
    assert.equal(settledRecordingStop([retained], capture.id), undefined);
    const finished = { ...retained, session_id: null };
    assert.equal(settledRecordingStop([finished], capture.id), finished);
    assert.deepEqual(settledRecordingStop([finished], capture.id)?.reasons, retained.reasons);
  }
});

test('session changes cannot stop another session capture or prevent starting their own', () => {
  const other = { ...capture, id: 'capture-2', session_id: 'session-2', scope: 'cohort' as const };
  assert.equal(ownedSessionRecording([capture], other.session_id), undefined);
  assert.equal(ownedSessionRecording([other, capture], frame.session_id), capture);
  assert.equal(ownedSessionRecording([capture, other], other.session_id), other);
  assert.equal(ownedSessionRecording([capture, other], null), undefined);
  assert.equal(ownedSessionRecording([capture, other], undefined), undefined);
  assert.equal(ownedSessionRecording([], frame.session_id), undefined);
});
