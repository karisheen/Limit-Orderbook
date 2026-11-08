import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import type { Catalog, Frame, NativeMarketData } from '../src/api';
import type { RecordingMutationResponse } from '../src/workflow-api';
import { selectableInstrument } from '../src/instrument-metadata';

async function openSource(page: Page, sourceId: string, instrumentId?: string) {
  const created = page.waitForResponse(response => response.url().endsWith('/api/v1/sessions') && response.request().method() === 'POST');
  await page.goto(`/?source=${encodeURIComponent(sourceId)}`);
  const response = await created;
  expect(response.ok()).toBe(true);
  let frame = await response.json() as Frame;
  await expect(page.locator('.status')).toContainText('Connected');
  if (instrumentId && frame.selection.instrument_id !== instrumentId) {
    await page.locator('summary').filter({ hasText: /^Watchlist$/ }).click();
    await page.getByLabel('Search instruments', { exact: true }).fill(instrumentId);
    const selection = page.waitForResponse(reply => reply.url().endsWith('/api/v1/subscriptions') && reply.request().method() === 'POST');
    await page.getByTitle(instrumentId, { exact: true }).click();
    const selected = await selection;
    expect(selected.ok(), await selected.text()).toBe(true);
    frame = await selected.json() as Frame;
    expect(frame.selection.instrument_id).toBe(instrumentId);
  }
  await page.locator('.native-recording > summary').click();
  return frame;
}

async function capture(page: Page, action: 'start' | 'stop') {
  const pending = page.waitForResponse(response => response.url().endsWith(`/api/v1/recordings/${action}`) && response.request().method() === 'POST');
  await page.getByRole('button', { name: action === 'start' ? 'Record selected instrument' : 'Stop recording', exact: true }).click();
  const response = await pending;
  expect(response.ok(), await response.text()).toBe(true);
  return { body: await response.json() as RecordingMutationResponse, input: response.request().postDataJSON() as { id?: string; session_id: string; generation: string } };
}

async function observedLiveBook(page: Page, frame: Frame, receivedAfter = 0n) {
  await expect.poll(async () => {
    const response = await page.request.post('/api/v1/market-data', {
      data: { session_id: frame.session_id, generation: frame.generation },
    });
    expect(response.ok(), await response.text()).toBe(true);
    const market = await response.json() as NativeMarketData;
    const book = market.books.find(item => item.instrument_id === frame.selection.instrument_id);
    return !!book?.eligibility?.eligible && book.bids.length > 0 && book.asks.length > 0
      && BigInt(book.asof.receive_wall_ns) >= receivedAfter;
  }, { timeout: 30_000 }).toBe(true);
}

test('synthetic replay cannot claim a live recording', async ({ page }) => {
  const response = await page.request.get('/api/v1/instruments');
  expect(response.ok()).toBe(true);
  const catalog = await response.json() as Catalog;
  const demo = catalog.sources.find(source => source.mode === 'demo' && source.status === 'ready');
  if (!demo) throw new Error('Actual backend must provide its ready synthetic source');
  let starts = 0;
  page.on('request', request => {
    if (request.url().endsWith('/api/v1/recordings/start')) starts++;
  });
  await openSource(page, demo.id);
  await expect(page.getByRole('button', { name: 'Record selected instrument', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Stop recording', exact: true })).toBeDisabled();
  await expect(page.locator('.native-recording')).toContainText('Recording is available for public live data only.');
  const listed = page.waitForResponse(reply => reply.url().endsWith('/api/v1/recordings') && reply.request().method() === 'GET');
  await page.getByRole('button', { name: 'Refresh recordings', exact: true }).click();
  expect((await listed).ok()).toBe(true);
  expect(starts).toBe(0);
});

// Opt in only against a real public live collector with at least two instruments.
// The ordinary offline suite does not substitute synthetic books for live capture.
// PLAYWRIGHT_RECORDING_SOURCE_ID is the exact ready source ID from the native catalog.
test('live recording ownership survives a generation-fenced response and replays its original instrument', async ({ page, context, request }) => {
  const sourceId = process.env.PLAYWRIGHT_RECORDING_SOURCE_ID;
  test.skip(!sourceId, 'Set PLAYWRIGHT_RECORDING_SOURCE_ID to exercise real public-live capture; demo is intentionally ineligible');
  test.setTimeout(90_000);
  const catalogResponse = await request.get(`/api/v1/instruments?source_id=${encodeURIComponent(sourceId!)}`);
  expect(catalogResponse.ok()).toBe(true);
  const catalog = await catalogResponse.json() as Catalog;
  expect(catalog.sources.find(source => source.id === sourceId)).toMatchObject({ mode: 'live', status: 'ready' });
  const instrumentId = process.env.PLAYWRIGHT_RECORDING_INSTRUMENT_ID;
  if (instrumentId) expect(catalog.instruments.filter(selectableInstrument).map(item => item.instrument_id)).toContain(instrumentId);
  const second = await context.newPage();
  const { promise: held, resolve: release } = Promise.withResolvers<void>();
  const { promise: started, resolve: captured } = Promise.withResolvers<RecordingMutationResponse>();
  let delay = true;
  await page.route('**/api/v1/recordings/start', async route => {
    if (!delay) { await route.continue(); return; }
    delay = false;
    const response = await route.fetch();
    expect(response.ok()).toBe(true);
    captured(await response.json() as RecordingMutationResponse);
    await held;
    // Selection aborts the obsolete browser response, not the real server capture.
    await route.fulfill({ response }).catch(() => undefined);
  });
  try {
    const first = await openSource(page, sourceId!, instrumentId);
    const other = await openSource(second, sourceId!, instrumentId);
    expect(first.session_id).not.toBe(other.session_id);
    const primary = catalog.instruments.find(instrument => instrument.instrument_id === first.selection.instrument_id);
    const alternatives = catalog.instruments.filter(instrument => selectableInstrument(instrument)
      && instrument.instrument_id !== first.selection.instrument_id);
    const alternative = alternatives.find(instrument => instrument.base.asset_id === primary?.base.asset_id
      && instrument.quote.asset_id === primary?.quote.asset_id) ?? alternatives[0];
    if (!alternative) throw new Error('Live recording generation coverage requires at least two streamable public native instruments');
    await observedLiveBook(page, first);
    const firstCaptureBoundary = BigInt(Date.now()) * 1_000_000n;
    await page.getByRole('button', { name: 'Record selected instrument', exact: true }).click();
    const recording = await started;
    const original = recording.recordings.find(item => item.id === recording.recording_id);
    expect(original).toMatchObject({ session_id: first.session_id, status: 'recording' });
    await expect(second.getByRole('button', { name: 'Stop recording', exact: true })).toBeDisabled();
    await observedLiveBook(page, first, firstCaptureBoundary);

    await page.locator('summary').filter({ hasText: /^Watchlist$/ }).click();
    await page.getByLabel('Search instruments', { exact: true }).fill(alternative.instrument_id);
    const selection = page.waitForResponse(response => response.url().endsWith('/api/v1/subscriptions') && response.ok());
    await page.getByTitle(alternative.instrument_id, { exact: true }).click();
    const selected = await (await selection).json() as Frame;
    expect(selected.session_id).toBe(first.session_id);
    expect(selected.generation).not.toBe(first.generation);
    expect(selected.selection.instrument_id).toBe(alternative.instrument_id);
    await expect(page.getByLabel('Search instruments', { exact: true })).toBeHidden();
    await expect(page.locator('summary').filter({ hasText: /^Watchlist$/ })).toBeFocused();
    await page.locator('.native-recording > summary').click();
    // GET ownership recovery must work even though the start response was fenced.
    await expect(page.getByRole('button', { name: 'Stop recording', exact: true })).toBeEnabled();
    release();
    await expect(page.getByRole('button', { name: 'Record selected instrument', exact: true })).toBeDisabled();

    const secondCaptureBoundary = BigInt(Date.now()) * 1_000_000n;
    const secondCapture = await capture(second, 'start');
    expect(secondCapture.body.recording_id).not.toBe(recording.recording_id);
    expect(secondCapture.body.recordings.find(item => item.id === secondCapture.body.recording_id))
      .toMatchObject({ session_id: other.session_id, status: 'recording' });
    await observedLiveBook(second, other, secondCaptureBoundary);
    const finished = await capture(page, 'stop');
    expect(finished.input).toMatchObject({ id: recording.recording_id, session_id: first.session_id, generation: selected.generation });
    const complete = finished.body.recordings.find(item => item.id === recording.recording_id);
    expect(complete).toMatchObject({ status: 'complete', session_id: null });
    expect(complete?.source_id).toBeTruthy();
    await expect(second.getByRole('button', { name: 'Stop recording', exact: true })).toBeEnabled();
    const secondFinished = await capture(second, 'stop');
    expect(secondFinished.input).toMatchObject({ id: secondCapture.body.recording_id, session_id: other.session_id });

    const row = page.locator('.native-recording-row').filter({ has: page.locator('strong').filter({ hasText: complete!.label }) });
    const replay = row.getByRole('button', { name: 'Replay recording', exact: true });
    await expect(replay).toBeEnabled();
    const replayCreated = page.waitForResponse(response => response.url().endsWith('/api/v1/sessions') && response.request().method() === 'POST');
    await replay.click();
    const replayResponse = await replayCreated;
    expect(replayResponse.ok()).toBe(true);
    const replayFrame = await replayResponse.json() as Frame;
    expect(replayFrame.session_id).not.toBe(first.session_id);
    expect(replayFrame.source).toMatchObject({ id: complete!.source_id, mode: 'replay', recording: true });
    expect(replayFrame.selection.instrument_id).toBe(first.selection.instrument_id);
    await expect(page.locator('.source-banner')).toContainText('RECORDED');
    await expect(page.getByTestId('replay-step')).toBeEnabled();
  } finally {
    release();
    await page.close();
    await second.close();
  }
});
