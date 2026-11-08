import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Instrument, Source } from './api.ts';
import type { ManagedRecording } from './workflow-api.ts';
import { ownedSessionRecording, paneForView, retainConfirmedCatalog, toggleBookView, viewForPane } from './integration/nativeWorkspace.ts';

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

test('every mobile pane selects its owning chart mode and preserves Flow and Tape selection', () => {
  for (const pane of ['price', 'heatmap', 'flow', 'dom', 'tape'] as const) {
    assert.equal(paneForView(viewForPane(pane), pane), pane);
  }
  assert.equal(paneForView('price', 'tape'), 'price');
  assert.equal(paneForView('orderflow', 'dom'), 'heatmap');
  assert.equal(paneForView('book', 'price'), 'dom');
});

test('DOM and tape toggle exits Book coherently rather than hiding content under a selected Book tab', () => {
  for (const narrow of [false, true]) {
    for (const desktopView of ['price', 'orderflow'] as const) {
      assert.deepEqual(toggleBookView('book', true, narrow, desktopView), { nativeView: desktopView, rightOpen: false });
    }
  }
  assert.deepEqual(toggleBookView('price', false, false, 'price'), { nativeView: 'price', rightOpen: true });
  assert.deepEqual(toggleBookView('orderflow', true, false, 'orderflow'), { nativeView: 'orderflow', rightOpen: false });
  for (const rightOpen of [false, true]) {
    assert.deepEqual(toggleBookView('price', rightOpen, true, 'price'), { nativeView: 'book', rightOpen: true });
    assert.deepEqual(toggleBookView('orderflow', rightOpen, true, 'orderflow'), { nativeView: 'book', rightOpen: true });
  }
});

test('closing Book or Tape selects a visible primary pane in the remembered chart workspace', () => {
  for (const previousPane of ['dom', 'tape'] as const) {
    for (const narrow of [false, true]) {
      for (const desktopView of ['price', 'orderflow'] as const) {
        const next = toggleBookView(viewForPane(previousPane), true, narrow, desktopView);
        const selected = paneForView(next.nativeView!, previousPane);
        assert.equal(selected, desktopView === 'price' ? 'price' : 'heatmap');
        assert.equal(viewForPane(selected), next.nativeView);
        assert.equal(next.rightOpen, false);
      }
    }
  }
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

test('session changes cannot stop another session capture or prevent starting their own', () => {
  const other = { ...capture, id: 'capture-2', session_id: 'session-2', scope: 'cohort' as const };
  assert.equal(ownedSessionRecording([capture], other.session_id), undefined);
  assert.equal(ownedSessionRecording([other, capture], frame.session_id), capture);
  assert.equal(ownedSessionRecording([capture, other], other.session_id), other);
  assert.equal(ownedSessionRecording([capture, other], null), undefined);
  assert.equal(ownedSessionRecording([capture, other], undefined), undefined);
  assert.equal(ownedSessionRecording([], frame.session_id), undefined);
});
