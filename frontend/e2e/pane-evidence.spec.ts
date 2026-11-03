import { expect, test } from '@playwright/test';
import type { Page, Response } from '@playwright/test';
import type { Catalog, Frame, VisualGrid } from '../src/api';
import { chartEvidence } from '../src/data-freshness';

// All responses are from the actual native demo session; no substituted market data.
const primary = (page: Page) => page.getByTestId('chart-pane-heatmap');
const visualization = (page: Page, matches: (input: Record<string, unknown>) => boolean, rows = 128) =>
  page.waitForResponse(response => response.url().endsWith('/api/v1/visualization')
    && response.request().method() === 'POST'
    && response.request().postDataJSON().rows === rows
    && matches(response.request().postDataJSON() as Record<string, unknown>));

async function body<T>(pending: Promise<Response>): Promise<T> {
  const response = await pending;
  expect(response.ok(), `${response.status()} ${await response.text()}`).toBe(true);
  return await response.json() as T;
}

async function replay(page: Page, action: 'play' | 'pause' | 'step') {
  const response = page.waitForResponse(response => response.url().endsWith('/api/v1/replay')
    && response.request().postDataJSON().action === action);
  await page.getByTestId(`replay-${action}`).click();
  return body<Frame>(response);
}

async function openAdvancedDemo(page: Page) {
  let latest: Frame | null = null;
  page.on('websocket', socket => socket.on('framereceived', event => {
    const message = JSON.parse(event.payload.toString()) as Frame;
    if (message.type === 'frame') latest = message;
  }));
  const catalogResponse = await page.request.get('/api/v1/instruments');
  expect(catalogResponse.ok()).toBe(true);
  const catalog = await catalogResponse.json() as Catalog;
  const demo = catalog.sources.find(source => source.mode === 'demo' && source.status === 'ready');
  if (!demo) throw new Error('The native catalog must expose the explicitly synthetic demo');
  await page.goto(`/?source=${encodeURIComponent(demo.id)}`);
  await expect(page.locator('.status')).toHaveText('Connected');
  await expect(primary(page).getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', /.+/);
  const speed = page.waitForResponse(response => response.url().endsWith('/api/v1/replay')
    && response.request().postDataJSON().action === 'speed');
  await page.getByLabel('Replay speed').selectOption('8');
  await body<Frame>(speed);
  await replay(page, 'play');
  await expect.poll(() => BigInt(latest?.book?.asof.receive_mono_ns ?? '0') > 45_000_000_000n).toBe(true);
  return replay(page, 'pause');
}

test('nondefault follow uses the evaluation frontier and frozen pan and zoom keep their requested bounds', async ({ page }) => {
  const frame = await openAdvancedDemo(page);
  const pane = primary(page);
  await pane.getByRole('button', { name: /settings$/ }).click();
  const clock = frame.book!.asof.clock_id;
  const points = [frame.book!.asof.receive_mono_ns,
    ...(frame.analytics?.tape.filter(print => print.asof.clock_id === clock).map(print => print.asof.receive_mono_ns) ?? []),
    ...(frame.analytics?.metrics.filter(metric => metric.instrument_id === frame.selection.instrument_id).flatMap(metric => [
      ...(metric.asof?.clock_id === clock ? [metric.asof.receive_mono_ns] : []),
      ...metric.references.filter(reference => reference.clock_id === clock).map(reference => reference.receive_mono_ns),
      ...(metric.window.clock_id === clock && typeof metric.window.end_mono_ns === 'string' ? [metric.window.end_mono_ns] : []),
    ]) ?? [])];
  const frontier = points.reduce((latest, time) => BigInt(time) > latest ? BigInt(time) : latest, 0n);
  const followResponse = visualization(page, input => input.end_ns === frontier.toString()
    && input.start_ns === (frontier - 30_000_000_000n).toString());
  await pane.getByLabel('Chart range').selectOption('30');
  const following = await body<VisualGrid>(followResponse);
  await expect(pane.getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', following.view_id);
  await pane.getByTestId('view-freeze').click();
  await expect(pane.getByTestId('view-freeze')).toHaveAttribute('aria-pressed', 'true');
  await pane.getByRole('button', { name: /Inspector$/ }).click();
  await expect(pane.locator('.chart-pane-settings')).toHaveCount(0);
  await expect(pane.locator('.chart-pane-actions').getByRole('button', { name: /settings$/ })).toHaveAttribute('aria-expanded', 'true');
  await pane.locator('.m4-chart-guide > summary').click();
  const chart = pane.getByTestId('market-charts');
  const start = BigInt((await chart.getAttribute('data-start-ns'))!);
  const end = BigInt((await chart.getAttribute('data-end-ns'))!);
  const shift = (end - start) / 4n;
  const panResponse = visualization(page, input => input.start_ns === (start - shift).toString() && input.end_ns === (end - shift).toString());
  await pane.getByRole('button', { name: 'Pan chart earlier', exact: true }).click();
  const panned = await body<VisualGrid>(panResponse);
  await expect(chart).toHaveAttribute('data-painted-view-id', panned.view_id);
  await expect(chart).toHaveAttribute('data-start-ns', (start - shift).toString());
  await expect(chart).toHaveAttribute('data-end-ns', (end - shift).toString());
  await expect(pane.getByTestId('view-freeze')).toHaveAttribute('aria-pressed', 'true');
  const zoomResponse = visualization(page, input => typeof input.start_ns === 'string' && typeof input.end_ns === 'string'
    && BigInt(input.end_ns) - BigInt(input.start_ns) === (end - start) * 4n / 5n);
  await pane.getByRole('button', { name: 'Zoom chart in', exact: true }).click();
  const zoomed = await body<VisualGrid>(zoomResponse);
  await expect(chart).toHaveAttribute('data-painted-view-id', zoomed.view_id);
  await expect(pane.getByTestId('chart-freshness')).toHaveAttribute('data-frozen', 'true');
  const otherPanePoll = visualization(page, () => true, 32);
  await replay(page, 'step');
  await body<VisualGrid>(otherPanePoll);
  await expect(chart).toHaveAttribute('data-painted-view-id', zoomed.view_id);
  await expect(chart).toHaveAttribute('data-start-ns', zoomed.start_ns);
  await expect(chart).toHaveAttribute('data-end-ns', zoomed.end_ns);
});

test('flow freshness tracks primary evidence regardless of quote and trade overlay toggles', async ({ page }) => {
  await openAdvancedDemo(page);
  const pane = page.getByTestId('chart-pane-flow');
  await pane.getByRole('button', { name: /settings$/ }).click();
  await pane.getByLabel('Trades', { exact: true }).uncheck();
  await pane.getByLabel('Quotes', { exact: true }).uncheck();
  const pending = visualization(page, () => true, 32);
  const view = await body<VisualGrid>(pending);
  const chart = pane.getByTestId('market-charts');
  await expect(chart).toHaveAttribute('data-painted-view-id', view.view_id);
  const rowViewport = {
    first: Number(await chart.getAttribute('data-row-first')),
    count: Number(await chart.getAttribute('data-row-count')),
  };
  expect(rowViewport.first).toBeGreaterThanOrEqual(0);
  expect(rowViewport.count).toBeGreaterThan(0);
  expect(rowViewport.first + rowViewport.count).toBeLessThanOrEqual(view.rows);
  const evidence = chartEvidence(view, { mode: 'footprint', rowViewport });
  expect(evidence.reference, 'Native footprint fixture must contain displayed trade evidence').not.toBeNull();
  await expect(pane.getByTestId('chart-freshness')).toHaveAttribute('data-evidence-ns', evidence.reference!.receive_mono_ns);
  await expect(pane.getByTestId('chart-freshness')).toHaveAttribute('data-clock-id', view.clock_id);
  await pane.getByTestId('view-freeze').click();
  const identity = await pane.getByTestId('chart-freshness').getAttribute('data-evidence-ns');
  const before = Number(await pane.getByTestId('chart-freshness').getAttribute('data-evidence-resident-ms'));
  await pane.getByLabel('Trades', { exact: true }).check();
  await pane.getByLabel('Quotes', { exact: true }).check();
  await expect(pane.getByTestId('chart-freshness')).toHaveAttribute('data-evidence-ns', identity!);
  await expect(pane.getByTestId('chart-freshness')).toHaveAttribute('aria-label', /primary layer/);
  await replay(page, 'step');
  await expect.poll(async () => Number(await pane.getByTestId('chart-freshness').getAttribute('data-evidence-resident-ms'))).toBeGreaterThan(before);
  await expect(pane.getByTestId('chart-freshness')).toHaveAttribute('data-evidence-ns', identity!);
});
