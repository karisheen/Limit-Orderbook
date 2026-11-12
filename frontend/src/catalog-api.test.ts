import assert from 'node:assert/strict';
import test from 'node:test';
import { ApiError, requestCatalog } from './api';
import type { Catalog } from './api';

const ready: Catalog = { version: '1', instruments: [], sources: [{
  id: 'live', mode: 'live', label: 'Public live', status: 'ready', synthetic: false,
  recording: false, duration_ns: null, streams: 0, reasons: [],
}], preferred_source_id: 'live' };

test('catalog revalidation retains the confirmed representation until changed source evidence arrives', async context => {
  const changed: Catalog = { ...ready, sources: [{ ...ready.sources[0]!, status: 'unavailable', reasons: ['metadata_unavailable'] }] };
  const responses = [
    new Response(JSON.stringify(ready), { headers: { ETag: '"ready"' } }),
    new Response(null, { status: 304, headers: { ETag: '"ready"' } }),
    new Response(JSON.stringify(changed), { headers: { ETag: '"changed"' } }),
  ];
  context.mock.method(globalThis, 'fetch', async () => responses.shift()!);
  const signal = new AbortController().signal;
  const first = await requestCatalog('/instruments?source_id=live', signal);
  const confirmed = await requestCatalog(first.path, signal, first);
  assert.strictEqual(confirmed, first);
  const replaced = await requestCatalog(first.path, signal, confirmed);
  assert.notStrictEqual(replaced.data, first.data);
  assert.equal(replaced.data.sources[0]!.status, 'unavailable');
  assert.deepEqual(replaced.data.sources[0]!.reasons, ['metadata_unavailable']);
  assert.equal(first.data.sources[0]!.status, 'ready');
});

test('304 cannot borrow another source or an unvalidated body', async context => {
  context.mock.method(globalThis, 'fetch', async () => new Response(null, { status: 304 }));
  const signal = new AbortController().signal;
  await assert.rejects(requestCatalog('/instruments?source_id=replay', signal, {
    path: '/instruments?source_id=live', etag: '"live"', data: ready,
  }), /no matching catalog representation/);
  await assert.rejects(requestCatalog('/instruments', signal), /no matching catalog representation/);
  await assert.rejects(requestCatalog('/instruments', signal, {
    path: '/instruments', etag: null, data: ready,
  }), /no matching catalog representation/);
});

test('failed catalog refresh is an error, never cached success; later validation may recover', async context => {
  const responses = [
    new Response(JSON.stringify(ready), { headers: { ETag: '"ready"' } }),
    new Response(JSON.stringify({ detail: 'catalog unavailable' }), { status: 503 }),
    new Response(null, { status: 304 }),
  ];
  context.mock.method(globalThis, 'fetch', async () => responses.shift()!);
  const signal = new AbortController().signal;
  const first = await requestCatalog('/instruments', signal);
  await assert.rejects(requestCatalog(first.path, signal, first), error => error instanceof ApiError && error.status === 503 && error.message.includes('catalog unavailable'));
  assert.strictEqual(await requestCatalog(first.path, signal, first), first);
});
