import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import type { Catalog, Frame } from '../src/api';

const chart = (page: Page) => page.getByTestId('price-chart');
const interval = (page: Page, value: string) => page.getByRole('button', { name: `Candle interval ${value}`, exact: true });
const candleResponse = (page: Page, value: string) => page.waitForResponse(response => response.url().endsWith('/api/v1/candles')
  && response.request().method() === 'POST' && response.request().postDataJSON().interval === value && response.ok());

async function openObservedDemo(page: Page) {
  let latest: Frame | null = null;
  page.on('websocket', socket => {
    if (!socket.url().includes('/api/v1/stream?')) return;
    socket.on('framereceived', event => {
      const frame = JSON.parse(event.payload.toString()) as Frame;
      if (frame.type === 'frame') latest = frame;
    });
  });
  const catalog = await (await page.request.get('/api/v1/instruments')).json() as Catalog;
  const source = catalog.sources.find(item => item.mode === 'demo' && item.status === 'ready');
  if (!source) throw new Error('The actual backend must provide its offline synthetic source');
  await page.goto(`/?source=${encodeURIComponent(source.id)}`);
  await page.getByRole('tablist', { name: 'Chart workspace', exact: true }).getByRole('tab', { name: 'Price action', exact: true }).click();
  await expect(chart(page)).toBeVisible();
  await expect(page.getByLabel('Seek delivery')).toBeEnabled();
  await page.getByLabel('Seek delivery').fill('5000');
  const sought = page.waitForResponse(response => response.url().endsWith('/api/v1/replay') && response.request().postDataJSON().action === 'seek');
  await page.getByRole('button', { name: 'Seek', exact: true }).click();
  const response = await sought;
  expect(response.ok(), await response.text()).toBe(true);
  const frame = await response.json() as Frame;
  await expect.poll(() => latest?.generation).toBe(frame.generation);
  return { source, frame, latest: () => latest ?? frame };
}

test('hour candles, interval persistence and independent live book during replay', async ({ page }) => {
  const session = await openObservedDemo(page);
  await expect(interval(page, '1h')).toHaveAttribute('aria-pressed', 'true');
  const minuteResponse = candleResponse(page, '1m');
  await interval(page, '1m').click();
  const minute = await (await minuteResponse).json();
  expect(minute.source).toBe('session_trades');
  expect(minute.instrument_id).toBe(session.frame.selection.instrument_id);
  expect(minute.candles.length).toBeGreaterThanOrEqual(2);
  for (const bar of minute.candles) {
    expect(BigInt(bar.start_ns) % 60_000_000_000n).toBe(0n);
    expect(BigInt(bar.end_ns) - BigInt(bar.start_ns)).toBe(60_000_000_000n);
  }
  const played = page.waitForResponse(response => response.url().endsWith('/api/v1/replay') && response.request().postDataJSON().action === 'play');
  await page.getByTestId('replay-play').click();
  expect((await played).ok()).toBe(true);
  const initialRevision = BigInt(session.latest().book?.revision ?? '0');
  const hourResponse = candleResponse(page, '1h');
  await interval(page, '1h').click();
  const hour = await (await hourResponse).json();
  expect(hour.generation).toBe(session.frame.generation);
  expect(hour.candles).toHaveLength(1);
  expect(BigInt(hour.candles[0].start_ns) % 3_600_000_000_000n).toBe(0n);
  expect(BigInt(hour.candles[0].end_ns) - BigInt(hour.candles[0].start_ns)).toBe(3_600_000_000_000n);
  expect(BigInt(hour.coverage_end_ns) - BigInt(hour.coverage_start_ns)).toBeLessThan(3_600_000_000_000n);
  await expect.poll(() => BigInt(session.latest().book?.revision ?? '0')).toBeGreaterThan(initialRevision);
  expect(session.latest().session_id).toBe(session.frame.session_id);
  expect(session.latest().generation).toBe(session.frame.generation);
  expect(session.latest().status).toBe('playing');
  await page.getByRole('button', { name: 'Candle timeframe', exact: true }).click();
  await expect(page.getByRole('menuitemradio', { name: '1h •', exact: true })).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Enter');
  await expect(chart(page)).toHaveAttribute('data-presented-interval', '2h');
  await expect(page.getByRole('button', { name: 'Candle timeframe', exact: true })).toContainText('2h');
  await interval(page, '4h').click();
  await expect(interval(page, '4h')).toHaveAttribute('aria-pressed', 'true');
  await page.reload();
  await page.getByRole('tablist', { name: 'Chart workspace', exact: true }).getByRole('tab', { name: 'Price action', exact: true }).click();
  await expect(chart(page)).toBeVisible();
  await expect(interval(page, '4h')).toHaveAttribute('aria-pressed', 'true');
});

test('a delayed obsolete interval response cannot replace the newly selected candles', async ({ page }) => {
  await openObservedDemo(page);
  const { promise: gate, resolve: release } = Promise.withResolvers<void>();
  const { promise: requested, resolve: intercepted } = Promise.withResolvers<void>();
  const { promise: delivered, resolve: deliveryDone } = Promise.withResolvers<void>();
  await page.route('**/api/v1/candles', async route => {
    if (route.request().postDataJSON().interval !== '1m') { await route.continue(); return; }
    const response = await route.fetch();
    intercepted();
    await gate;
    await route.fulfill({ response }).catch(() => { /* Superseded fetches may already be aborted. */ });
    deliveryDone();
  });
  try {
    await interval(page, '1m').click();
    await requested;
    const current = candleResponse(page, '4h');
    await interval(page, '4h').click();
    const response = await (await current).json();
    expect(response.interval).toBe('4h');
    release();
    await delivered;
    await expect(interval(page, '4h')).toHaveAttribute('aria-pressed', 'true');
    await expect(chart(page)).toHaveAttribute('data-interval', '4h');
    // Keyboard inspection remains within the current chart, not the late minute series.
    await page.getByLabel('Price action chart', { exact: true }).focus();
    await page.keyboard.press('ArrowLeft');
    await expect(chart(page)).toHaveAttribute('data-interval', '4h');
    await expect(chart(page)).toHaveAttribute('data-presented-interval', '4h');
    await expect(chart(page).getByRole('alert')).toHaveCount(0);
  } finally {
    release();
    await page.unroute('**/api/v1/candles');
  }
});

test('an unavailable refresh retains known candles as stale and explicit retry recovers', async ({ page }) => {
  let unavailable = false;
  await page.route('**/api/v1/candles', async route => {
    const response = await route.fetch();
    if (!unavailable || !response.ok()) { await route.fulfill({ response }); return; }
    const actual = await response.json();
    await route.fulfill({ response, json: { ...actual, status: 'unavailable', candles: [], reasons: ['transport_unavailable'] } });
  });
  const session = await openObservedDemo(page);
  await expect(chart(page)).toHaveAttribute('data-presented-generation', session.frame.generation);
  const values = chart(page).locator('.price-chart__legend dd');
  await expect(values).toHaveCount(5);
  const observed = await values.allTextContents();
  unavailable = true;
  await expect(chart(page).locator('.price-chart__warning')).toBeVisible();
  await expect(chart(page).locator('.price-chart__status')).toContainText('Stale');
  await expect(values).toHaveText(observed);
  await expect(chart(page)).toHaveAttribute('data-presented-generation', session.frame.generation);
  unavailable = false;
  await chart(page).getByRole('button', { name: 'Retry candles', exact: true }).click();
  await expect(chart(page).locator('.price-chart__warning')).toBeHidden();
  await expect(values).toHaveText(observed);
});

test('lost stream evidence marks held candles stale before replacing the session', async ({ page }, testInfo) => {
  let suppress = false;
  await page.routeWebSocket('**/api/v1/stream?*', client => {
    const server = client.connectToServer();
    server.onMessage(message => { if (!suppress) client.send(message); });
  });
  const session = await openObservedDemo(page);
  const values = chart(page).locator('.price-chart__legend dd');
  await expect(chart(page)).toHaveAttribute('data-presented-generation', session.frame.generation);
  await expect(values).toHaveCount(5);
  const observed = await values.allTextContents();
  suppress = true;
  try {
    await expect(chart(page).locator('.price-chart__status')).toHaveText('Stale snapshot', { timeout: 8000 });
    await expect(values).toHaveText(observed);
    await expect(page.locator('.source-readiness')).toHaveText('Unavailable');
    await page.screenshot({ path: testInfo.outputPath('disconnected.png') });
  } finally {
    suppress = false;
  }
  await expect(page.locator('.status')).toContainText('Connected');
  await expect(values).toHaveCount(5);
  await expect(chart(page)).not.toHaveAttribute('data-presented-generation', session.frame.generation);
  await expect(chart(page).locator('.price-chart__warning')).toBeHidden();
});
