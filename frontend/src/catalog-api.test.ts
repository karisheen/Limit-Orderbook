import assert from 'node:assert/strict';
import test from 'node:test';
import { ApiError, combineCatalog, request, requestCatalog, REQUEST_TIMEOUT_MS, selectedCatalogPath } from './api';
import type { Catalog, Instrument } from './api';

const ready: Catalog = { version: '1', instruments: [], sources: [{
  id: 'live', mode: 'live', label: 'Public live', status: 'ready', synthetic: false,
  recording: false, duration_ns: null, streams: 0, reasons: [],
}], preferred_source_id: 'live' };

const instrument: Instrument = {
  instrument_id: 'venue:spot:BTCUSD', venue: 'venue', api_family: 'spot', native_symbol: 'BTCUSD',
  base: { asset_id: 'BTC', ticker: 'BTC', native_token_id: null },
  quote: { asset_id: 'USD', ticker: 'USD', native_token_id: null },
  settlement: null, market_type: 'spot', tick_size: '0.01', lot_size: '0.001', quantity_unit: 'base',
  status: 'trading', data_access: 'public', execution_access: 'none', metadata_version: '1', metadata_status: 'verified',
};

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

test('source directory and selected metadata own independent conditional URLs', async context => {
  const selected: Catalog = { version: '1', instruments: [instrument], sources: [], preferred_source_id: 'live' };
  const changed: Catalog = { ...ready, sources: [{ ...ready.sources[0]!, status: 'loading', reasons: ['discovery_refresh'] }] };
  const calls: { url: string; etag: string | null }[] = [];
  const responses = [
    new Response(JSON.stringify(ready), { headers: { ETag: '"directory-1"' } }),
    new Response(JSON.stringify(selected), { headers: { ETag: '"metadata-1"' } }),
    new Response(JSON.stringify(changed), { headers: { ETag: '"directory-2"' } }),
    new Response(null, { status: 304 }),
    new Response(null, { status: 304 }),
  ];
  context.mock.method(globalThis, 'fetch', async (url: string, init?: RequestInit) => {
    calls.push({ url, etag: new Headers(init?.headers).get('If-None-Match') });
    return responses.shift()!;
  });
  const signal = new AbortController().signal;
  const directory = await requestCatalog('/sources', signal);
  const path = selectedCatalogPath('live');
  const metadata = await requestCatalog(path, signal, directory);
  const first = combineCatalog(directory.data, metadata.data);
  const updatedDirectory = await requestCatalog('/sources', signal, directory);
  const confirmedMetadata = await requestCatalog(path, signal, metadata);
  const next = combineCatalog(updatedDirectory.data, confirmedMetadata.data, first);
  assert.strictEqual(confirmedMetadata, metadata);
  assert.strictEqual(next.instruments, first.instruments);
  assert.notStrictEqual(next.sources, first.sources);
  assert.equal(next.sources[0]!.status, 'loading');
  assert.strictEqual(next.instruments[0], first.instruments[0]);
  const confirmedDirectory = await requestCatalog('/sources', signal, updatedDirectory);
  assert.strictEqual(combineCatalog(confirmedDirectory.data, confirmedMetadata.data, next), next);
  assert.deepEqual(calls, [
    { url: '/api/v1/sources', etag: null },
    { url: '/api/v1/instruments?source_id=live&include_sources=false', etag: null },
    { url: '/api/v1/sources', etag: '"directory-1"' },
    { url: '/api/v1/instruments?source_id=live&include_sources=false', etag: '"metadata-1"' },
    { url: '/api/v1/sources', etag: '"directory-2"' },
  ]);
});

test('default full catalog and metadata-only projections never share a validator', async context => {
  const calls: { url: string; etag: string | null }[] = [];
  context.mock.method(globalThis, 'fetch', async (url: string, init?: RequestInit) => {
    calls.push({ url, etag: new Headers(init?.headers).get('If-None-Match') });
    return new Response(null, { status: 304 });
  });
  const signal = new AbortController().signal;
  const full = { path: '/instruments?source_id=live', etag: '"full"', data: ready };
  assert.strictEqual(await requestCatalog(full.path, signal, full), full);
  await assert.rejects(requestCatalog(selectedCatalogPath('live'), signal, full), /no matching catalog representation/);
  await assert.rejects(requestCatalog('/sources', signal, full), /no matching catalog representation/);
  assert.deepEqual(calls.map(call => call.etag), ['"full"', null, null]);
  assert.equal(calls[0]!.url, '/api/v1/instruments?source_id=live');
  assert.equal(selectedCatalogPath('recording/a b'), '/instruments?source_id=recording%2Fa%20b&include_sources=false');
});

test('selected metadata revisions replace metadata while unchanged directory references stay intact', async context => {
  const selected: Catalog = { version: '1', instruments: [instrument], sources: [] };
  const revised: Catalog = { ...selected, instruments: [{ ...instrument, metadata_version: '2', tick_size: '0.1' }] };
  const responses = [
    new Response(JSON.stringify(selected), { headers: { ETag: '"metadata-1"' } }),
    new Response(JSON.stringify(revised), { headers: { ETag: '"metadata-2"' } }),
  ];
  context.mock.method(globalThis, 'fetch', async () => responses.shift()!);
  const signal = new AbortController().signal;
  const path = selectedCatalogPath('live');
  const first = await requestCatalog(path, signal);
  const combined = combineCatalog(ready, first.data);
  const second = await requestCatalog(path, signal, first);
  const changed = combineCatalog(ready, second.data, combined);
  assert.notStrictEqual(changed.instruments, combined.instruments);
  assert.strictEqual(changed.sources, combined.sources);
  assert.equal(changed.instruments[0]!.metadata_version, '2');
  assert.equal(changed.instruments[0]!.tick_size, '0.1');
  assert.equal(combined.instruments[0]!.tick_size, '0.01');
});

test('generic mutation deadline aborts a hung response once without retrying or cancelling caller ownership', async context => {
  const deadline = new AbortController();
  const caller = new AbortController();
  const timeouts: number[] = [];
  let calls = 0;
  context.mock.method(AbortSignal, 'timeout', (milliseconds: number) => {
    timeouts.push(milliseconds);
    return deadline.signal;
  });
  context.mock.method(globalThis, 'fetch', (_url: string, init?: RequestInit) => {
    calls++;
    const { promise, reject } = Promise.withResolvers<Response>();
    init!.signal!.addEventListener('abort', () => reject(init!.signal!.reason), { once: true });
    return promise;
  });
  const pending = request('/journal/put', { rationale: 'owned note' }, caller.signal);
  deadline.abort(new DOMException('Deadline expired', 'TimeoutError'));
  await assert.rejects(pending, error => error instanceof ApiError && error.status === 408 &&
    error.message.includes('after 30 seconds') && error.message.includes('Outcome is ambiguous') &&
    error.message.includes('No automatic retry or session replacement'));
  assert.deepEqual(timeouts, [REQUEST_TIMEOUT_MS]);
  assert.equal(calls, 1);
  assert.equal(caller.signal.aborted, false);
});

test('request deadline covers held response body and does not report body receipt prematurely', async context => {
  const deadline = new AbortController();
  const reading = Promise.withResolvers<void>();
  const response = new Response('{}');
  let received = 0;
  context.mock.method(AbortSignal, 'timeout', () => deadline.signal);
  context.mock.method(globalThis, 'fetch', async (_url: string, init?: RequestInit) => {
    context.mock.method(response, 'text', () => {
      reading.resolve();
      const { promise, reject } = Promise.withResolvers<string>();
      init!.signal!.addEventListener('abort', () => reject(init!.signal!.reason), { once: true });
      return promise;
    });
    return response;
  });
  const pending = request('/recordings/stop', { id: 'owned-recording' }, undefined, undefined, () => { received++; });
  await reading.promise;
  assert.equal(received, 0);
  deadline.abort(new DOMException('Deadline expired', 'TimeoutError'));
  await assert.rejects(pending, /Outcome is ambiguous/);
  assert.equal(received, 0);
});

test('caller cancellation retains its reason and explicitly unbounded creation remains observable', async context => {
  const deadline = new AbortController();
  const caller = new AbortController();
  const reason = new DOMException('Obsolete lifecycle', 'AbortError');
  let timeoutCalls = 0;
  context.mock.method(AbortSignal, 'timeout', () => { timeoutCalls++; return deadline.signal; });
  context.mock.method(globalThis, 'fetch', (_url: string, init?: RequestInit) => {
    const { promise, reject } = Promise.withResolvers<Response>();
    init!.signal!.addEventListener('abort', () => reject(init!.signal!.reason), { once: true });
    return promise;
  });
  const cancelled = request('/journal/put', { rationale: 'owned note' }, caller.signal);
  caller.abort(reason);
  await assert.rejects(cancelled, error => error === reason);
  assert.equal(timeoutCalls, 1);
  const late = Promise.withResolvers<Response>();
  let receivedAt: number | undefined;
  context.mock.method(globalThis, 'fetch', (_url: string, init?: RequestInit) => {
    assert.equal(init?.signal, undefined);
    return late.promise;
  });
  const creation = request<{ session_id: string }>('/sessions', { mode: 'demo' }, undefined, undefined,
    value => { receivedAt = value; }, { timeoutMs: null });
  assert.equal(timeoutCalls, 1);
  assert.equal(receivedAt, undefined);
  late.resolve(new Response('{"session_id":"late-owned-session"}'));
  assert.deepEqual(await creation, { session_id: 'late-owned-session' });
  assert.equal(typeof receivedAt, 'number');
});
