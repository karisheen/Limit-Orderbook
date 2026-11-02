import { test } from 'node:test';
import assert from 'node:assert/strict';
import { startupSource, validatePreferences } from './workspace.ts';
import type { Catalog, Mode, Source } from './api.ts';

const source = (id: string, mode: Mode): Source => ({ id, mode, label: id, status: 'ready', synthetic: mode === 'demo', recording: false, duration_ns: null, streams: 0, reasons: [] });
const catalog: Catalog = { version: '1', instruments: [], sources: [source('fixture', 'demo'), source('live', 'live'), source('capture', 'replay')], preferred_source_id: 'live' };
const saved = (mode: Mode, id: string, explicit?: boolean) => validatePreferences({ version: 1, source: { mode, id }, ...(explicit === undefined ? {} : { sourceExplicit: explicit }), instrument: 'exact/native', paused: true });

test('network server preference replaces legacy automatic demo without carrying a foreign instrument', () => {
  const preferences = saved('demo', 'fixture');
  assert.equal(preferences.sourceExplicit, false);
  const resolved = startupSource(catalog, preferences, '');
  assert.deepEqual(resolved.options, { mode: 'live', source_id: 'live' });
  assert.equal(preferences.paused, true);
});

test('explicit saved demo and legacy non-demo choices outrank server defaults', () => {
  for (const preferences of [saved('demo', 'fixture', true), saved('replay', 'capture'), saved('live', 'live')]) {
    assert.equal(preferences.sourceExplicit, true);
    const resolved = startupSource(catalog, preferences, '');
    assert.deepEqual(resolved.options, { mode: preferences.source!.mode, source_id: preferences.source!.id, instrument_id: 'exact/native' });
    assert.equal(resolved.notice, null);
    assert.equal(preferences.paused, true);
  }
});

test('URL source wins over saved explicit demo without carrying foreign instrument or session', () => {
  const resolved = startupSource(catalog, saved('demo', 'fixture', true), '?source=live');
  assert.deepEqual(resolved.options, { mode: 'live', source_id: 'live' });
});

test('offline default remains demo and explicit live or missing sources cannot fall back', () => {
  const offline: Catalog = { ...catalog, sources: [source('fixture', 'demo')], preferred_source_id: 'fixture' };
  assert.deepEqual(startupSource(offline, validatePreferences(null), '').options, { mode: 'demo', source_id: 'fixture' });
  for (const query of ['?source=live', '?source=missing', '?source=']) {
    assert.throws(() => startupSource(offline, saved('demo', 'fixture'), query), Error);
  }
  assert.throws(() => startupSource(offline, saved('live', 'live'), ''), Error);
});

test('unavailable live is retained for readiness error, never changed to ready demo', () => {
  const unavailable: Catalog = { ...catalog, sources: [source('fixture', 'demo'), { ...source('live', 'live'), status: 'unavailable', reasons: ['discovery failed'] }] };
  assert.deepEqual(startupSource(unavailable, saved('demo', 'fixture'), '').options, { mode: 'live', source_id: 'live' });
});
