import { test } from 'node:test';
import assert from 'node:assert/strict';
import { terminalIncidents } from './terminal-incident.ts';
import type { TerminalIncidentInput } from './terminal-incident.ts';

const ready: TerminalIncidentInput = {
  terminalError: null, frameError: null, catalogError: null, storageError: null,
  connection: 'Connected', status: 'playing', ready: true, qualityReasons: [], synthetic: false,
};

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
  for (const failure of [{ terminalError: 'HTTP 503: analytical_queue_overflow' }, { frameError: 'analytical_queue_overflow' }]) {
    const incidents = terminalIncidents({ ...ready, ...failure });
    assert.equal(incidents[0].id, 'overflow');
    assert.equal(incidents[0].recovery, 'diagnostics');
    assert.match(incidents[0].message, /Operator.*collector capacity/);
  }
});

test('seeking takes precedence over ancillary notices and clears on reconstruction', () => {
  const input = { ...ready, ready: false, status: 'seeking', catalogError: 'catalog unavailable', storageError: 'storage unavailable' };
  assert.equal(terminalIncidents(input)[0].id, 'seeking');
  assert.equal(terminalIncidents({ ...input, ready: true, status: 'paused' })[0].id, 'catalog');
  assert.equal(terminalIncidents({ ...input, ready: true, status: 'paused', catalogError: null })[0].id, 'storage');
});
