import { test } from 'node:test';
import assert from 'node:assert/strict';
import { terminalDiagnosticReasons, terminalIncidents } from './terminal-incident.ts';
import type { TerminalIncidentInput } from './terminal-incident.ts';
import type { Frame } from './api';

const ready: TerminalIncidentInput = {
  terminalError: null, frameError: null, catalogError: null, storageError: null,
  connection: 'Connected', status: 'playing', ready: true, qualityReasons: [], synthetic: false,
};

const liveDiagnostics: Pick<Frame, 'health' | 'source' | 'book'> = {
  health: { ready: true, reasons: [] },
  source: { id: 'live', mode: 'live', label: 'Public live', status: 'ready', synthetic: false,
    recording: false, duration_ns: null, streams: 34, reasons: ['binance-global:futures:http_status_451', 'bybit:http_status_403'] },
  book: null,
};

test('all-provider availability never becomes selected Coinbase or Hyperliquid evidence', () => {
  for (const instrumentId of ['coinbase:advanced-trade:spot:BTC-USD', 'hyperliquid:info:perpetual:BTC']) {
    const frame: typeof liveDiagnostics = { ...liveDiagnostics, book: {
      instrument_id: instrumentId, bids: [], asks: [], revision: '1',
      asof: { session_id: 'session', ingest_ordinal: '1', event_index: '0', clock_id: 'clock',
        receive_wall_ns: '1', receive_mono_ns: '1', exchange_event_wall_ns: null },
      presentation: { depth_limit: 100, bids_omitted: 0, asks_omitted: 0 },
      quality: { lifecycle: 'live', integrity: 'valid', source_mode: 'live', recorded_source_mode: null,
        reason_codes: ['bounded_depth'], capture_complete: true,
        coverage: { subscribed_depth: '20', retained_bid_levels: '20', retained_ask_levels: '20', full_depth: false } },
    } };
    const scoped = terminalDiagnosticReasons(frame);
    assert.deepEqual(scoped.selectedReasons, ['bounded_depth']);
    assert.deepEqual(scoped.providerReasons, liveDiagnostics.source.reasons);
    assert.deepEqual(terminalIncidents({ ...ready, qualityReasons: scoped.selectedReasons }), []);

    const degraded = terminalDiagnosticReasons({ ...frame,
      health: { ready: false, reasons: ['metadata_unavailable', 'http_status_403', 'sequence_gap'] },
      book: { ...frame.book!, quality: { ...frame.book!.quality, reason_codes: ['sequence_gap', 'capture_incomplete'] } },
    });
    assert.deepEqual(degraded.selectedReasons, ['metadata_unavailable', 'http_status_403', 'sequence_gap', 'capture_incomplete']);
    const incidents = terminalIncidents({ ...ready, ready: false, connection: 'Disconnected',
      frameError: 'selected transport failed', qualityReasons: degraded.selectedReasons });
    assert.deepEqual(incidents.map(item => item.id), ['frame', 'transport', 'readiness']);
    assert.match(incidents[2].message, /metadata_unavailable; http_status_403; sequence_gap; capture_incomplete/);
    assert.doesNotMatch(incidents[2].message, /binance|bybit/);
  }
});

test('non-live source errors remain selected evidence and empty frames stay empty', () => {
  const scoped = terminalDiagnosticReasons({ ...liveDiagnostics,
    source: { ...liveDiagnostics.source, mode: 'replay', reasons: ['capture_incomplete'] },
  });
  assert.deepEqual(scoped, { selectedReasons: ['capture_incomplete'], providerReasons: [] });
  assert.deepEqual(terminalDiagnosticReasons(null), { selectedReasons: [], providerReasons: [] });
});

test('provider diagnostics never remove the same raw reason from selected health', () => {
  const scoped = terminalDiagnosticReasons({ ...liveDiagnostics,
    health: { ready: false, reasons: liveDiagnostics.source.reasons },
  });
  assert.deepEqual(scoped.selectedReasons, liveDiagnostics.source.reasons);
  assert.deepEqual(scoped.providerReasons, liveDiagnostics.source.reasons);
});

test('one primary incident has deterministic precedence without discarding concurrent causes', () => {
  const incidents = terminalIncidents({ ...ready, terminalError: 'socket failed', frameError: 'native failed',
    catalogError: 'catalog failed', storageError: 'quota denied', connection: 'Disconnected',
    status: 'seeking', synthetic: true, qualityReasons: ['analytical_queue_overflow'] });
  assert.deepEqual(incidents.map(item => item.id), ['overflow', 'terminal', 'frame', 'transport', 'seeking', 'catalog', 'storage', 'synthetic']);
  assert.equal(incidents[0].recovery, 'diagnostics');
  assert.equal(incidents.find(item => item.id === 'frame')?.message, 'native failed');
  assert.equal(incidents.find(item => item.id === 'storage')?.message, 'quota denied');
});

test('recovery reveals remaining incident rather than retaining a stale error', () => {
  assert.equal(terminalIncidents({ ...ready, terminalError: 'offline', synthetic: true })[0].id, 'terminal');
  assert.equal(terminalIncidents({ ...ready, synthetic: true })[0].id, 'synthetic');
  assert.deepEqual(terminalIncidents(ready), []);
});

test('connected transport does not make invalid source evidence ready', () => {
  const incidents = terminalIncidents({ ...ready, ready: false, qualityReasons: ['sequence_gap'] });
  assert.equal(incidents[0].id, 'readiness');
  assert.match(incidents[0].message, /sequence_gap/);
  assert.equal(terminalIncidents({ ...ready, status: 'unavailable' })[0].id, 'readiness');
});

test('normal bounded depth stays compact quality rather than an incident', () => {
  assert.deepEqual(terminalIncidents({ ...ready, qualityReasons: ['bounded_depth'] }), []);
  assert.equal(terminalIncidents({ ...ready, qualityReasons: ['analytical_queue_overflow'] })[0].id, 'overflow');
});

test('collector overflow reported only as an error never offers reconnect as its recovery', () => {
  for (const failure of [{ terminalError: 'HTTP 503: analytical_queue_overflow' }, { frameError: 'analytical_queue_overflow' },
    { qualityReasons: ['analytical_queue_overflow: reselect to establish fresh history'] }]) {
    const incidents = terminalIncidents({ ...ready, ...failure });
    assert.equal(incidents[0].id, 'overflow');
    assert.equal(incidents[0].recovery, 'diagnostics');
    assert.match(incidents[0].message, /Operator.*collector capacity/);
    assert.match(incidents[0].message, /analytical history lost/);
    assert.match(incidents[0].message, /cannot recover missing observations/);
    assert.ok(incidents.every(item => item.recovery !== 'reconnect'));
  }
});

test('seeking takes precedence over ancillary notices and clears on reconstruction', () => {
  const input = { ...ready, ready: false, status: 'seeking', catalogError: 'catalog unavailable', storageError: 'storage unavailable' };
  assert.equal(terminalIncidents(input)[0].id, 'seeking');
  assert.equal(terminalIncidents({ ...input, ready: true, status: 'paused' })[0].id, 'catalog');
  assert.equal(terminalIncidents({ ...input, ready: true, status: 'paused', catalogError: null })[0].id, 'storage');
});
