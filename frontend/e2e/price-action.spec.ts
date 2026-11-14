import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import type { Catalog, Frame } from '../src/api';
import type { CandleHistory } from '../src/candle-model';

const candleIntervals = ['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '8h', '12h', '1d', '3d', '1w', '1M'];

const sessions = new WeakMap<Page, Promise<Frame>[]>();
test.beforeEach(async ({ page }) => {
  const created: Promise<Frame>[] = [];
  sessions.set(page, created);
  page.on('response', response => {
    if (response.url().endsWith('/api/v1/sessions') && response.request().method() === 'POST' && response.ok()) created.push(response.json());
  });
});
test.afterEach(async ({ page, request }) => {
  const created = await Promise.all(sessions.get(page) ?? []);
  await page.close();
  for (const id of new Set(created.map(frame => frame.session_id))) {
    const response = await request.delete(`/api/v1/sessions/${id}`);
    expect(response.ok() || response.status() === 404).toBe(true);
  }
});

const chart = (page: Page) => page.getByTestId('price-chart');
const interval = (page: Page) => page.getByRole('combobox', { name: 'All candle intervals', exact: true });
const candleResponse = (page: Page, value: string) => page.waitForResponse(response => response.url().endsWith('/api/v1/candles')
  && response.request().method() === 'POST' && response.request().postDataJSON().interval === value
  && response.request().postDataJSON().source === 'session_trades' && response.ok());

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
  await page.getByRole('combobox', { name: 'All candle intervals', exact: true }).selectOption('1h');
  await expect(chart(page)).toHaveAttribute('data-presented-generation', frame.generation);
  await expect(chart(page)).toHaveAttribute('data-presented-interval', '1h');
  return { source, frame, latest: () => latest ?? frame };
}

test('hour candles, interval persistence and independent live book during replay', async ({ page }) => {
  const session = await openObservedDemo(page);
  await expect(interval(page)).toHaveValue('1h');
  const minuteResponse = candleResponse(page, '1m');
  await interval(page).selectOption('1m');
  const minuteHTTP = await minuteResponse;
  const minute = await minuteHTTP.json() as CandleHistory;
  expect(minute.source).toBe('session_trades');
  expect(minute.instrument_id).toBe(session.frame.selection.instrument_id);
  expect(minute.session_id).toBe(session.frame.session_id);
  expect(minute.generation).toBe(session.frame.generation);
  expect(minute.instrument_ids).toEqual(minuteHTTP.request().postDataJSON().instrument_ids);
  expect(minute.instrument_ids[0]).toBe(session.frame.selection.instrument_id);
  expect(minute.scope).toBe(minute.instrument_ids.length > 1 ? 'aggregate' : 'primary');
  expect(minute.time_basis).toBe('receive_utc');
  expect(minute.supported_intervals).toEqual(candleIntervals);
  await expect(page.getByRole('combobox', { name: 'All candle intervals', exact: true })).toHaveValue('1m');
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
  await interval(page).selectOption('1h');
  const hour = await (await hourResponse).json() as CandleHistory;
  expect(hour.generation).toBe(session.frame.generation);
  expect(hour.source_identity).toBe(minute.source_identity);
  expect(hour.instrument_ids).toEqual(minute.instrument_ids);
  expect(hour.candles).toHaveLength(1);
  expect(BigInt(hour.candles[0]!.start_ns) % 3_600_000_000_000n).toBe(0n);
  expect(BigInt(hour.candles[0]!.end_ns) - BigInt(hour.candles[0]!.start_ns)).toBe(3_600_000_000_000n);
  expect(hour.coverage_start_ns).not.toBeNull();
  expect(hour.coverage_end_ns).not.toBeNull();
  expect(BigInt(hour.coverage_end_ns!) - BigInt(hour.coverage_start_ns!)).toBeLessThan(3_600_000_000_000n);
  await expect.poll(() => BigInt(session.latest().book?.revision ?? '0')).toBeGreaterThan(initialRevision);
  expect(session.latest().session_id).toBe(session.frame.session_id);
  expect(session.latest().generation).toBe(session.frame.generation);
  expect(session.latest().status).toBe('playing');
  await expect(interval(page)).toHaveCount(1);
  expect(await interval(page).locator('option').evaluateAll(options => options.map(option => (option as HTMLOptionElement).value))).toEqual(candleIntervals);
  await interval(page).selectOption('2h');
  await expect(chart(page)).toHaveAttribute('data-presented-interval', '2h');
  await expect(interval(page)).toHaveValue('2h');
  await interval(page).selectOption('4h');
  await expect(interval(page)).toHaveValue('4h');
  await page.reload();
  await page.getByRole('tablist', { name: 'Chart workspace', exact: true }).getByRole('tab', { name: 'Price action', exact: true }).click();
  await expect(chart(page)).toBeVisible();
  await expect(interval(page)).toHaveValue('4h');
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
    await interval(page).selectOption('1m');
    await requested;
    const current = candleResponse(page, '4h');
    await interval(page).selectOption('4h');
    const response = await (await current).json();
    expect(response.interval).toBe('4h');
    release();
    await delivered;
    await expect(interval(page)).toHaveValue('4h');
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
    await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Injected candle transport unavailable' }) });
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
    await page.screenshot({ path: testInfo.outputPath('disconnected.png') });
  } finally {
    suppress = false;
  }
  await expect(page.locator('.status')).toContainText('Connected');
  await expect(values).toHaveCount(5);
  await expect(chart(page)).not.toHaveAttribute('data-presented-generation', session.frame.generation);
  await expect(chart(page).locator('.price-chart__warning')).toBeHidden();
});

test('exact candle keyboard inspection and replay seek expose only the admitted prefix', async ({ page }) => {
  const session = await openObservedDemo(page);
  const loaded = candleResponse(page, '1m');
  await interval(page).selectOption('1m');
  const history = await (await loaded).json() as CandleHistory;
  expect(history.candles.length).toBeGreaterThanOrEqual(2);
  const plot = page.getByRole('group', { name: 'Price action chart', exact: true });
  const values = chart(page).locator('.price-chart__legend dd');
  await expect(chart(page)).toHaveAttribute('data-presented-interval', '1m');
  await plot.focus();
  await page.keyboard.press('ArrowLeft');
  const previous = history.candles.at(-2)!;
  for (const [index, field] of (['open', 'high', 'low', 'close'] as const).entries()) {
    await expect(values.nth(index)).toHaveText(previous[field]);
  }
  await expect(values.nth(4)).toHaveAttribute('title', new RegExp(`^${previous.volume.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')} `));
  await page.keyboard.press('ArrowRight');
  const latest = history.candles.at(-1)!;
  await expect(values.nth(3)).toHaveText(latest.close);
  await chart(page).getByText('Exact candle data', { exact: true }).click();
  const exactTable = chart(page).getByRole('table', { name: /exact original decimal strings and nanosecond boundaries/ });
  await expect(exactTable).toContainText(latest.start_ns);
  await expect(exactTable).toContainText(latest.close);

  const rewind = page.waitForResponse(response => response.url().endsWith('/api/v1/replay') && response.request().postDataJSON().action === 'seek');
  const prefixReply = page.waitForResponse(response => response.url().endsWith('/api/v1/candles') && response.ok()
    && response.request().postDataJSON().source === 'session_trades'
    && response.request().postDataJSON().interval === '1m'
    && response.request().postDataJSON().generation !== session.frame.generation);
  await page.getByLabel('Seek delivery', { exact: true }).fill('1000');
  await page.getByRole('button', { name: 'Seek', exact: true }).click();
  const frame = await (await rewind).json() as Frame;
  const prefix = await (await prefixReply).json() as CandleHistory;
  expect(prefix.generation).toBe(frame.generation);
  expect(prefix.session_id).toBe(session.frame.session_id);
  expect(BigInt(prefix.highwater!)).toBeLessThanOrEqual(BigInt(frame.replay!.cursor.delivery_index));
  expect(BigInt(prefix.coverage_end_ns!)).toBeLessThan(BigInt(history.coverage_end_ns!));
  expect(prefix.candles.every(bar => BigInt(bar.start_ns) <= BigInt(prefix.coverage_end_ns!))).toBe(true);
  await expect(chart(page)).toHaveAttribute('data-presented-generation', frame.generation);
  await expect(exactTable.getByRole('rowheader', { name: latest.start_ns, exact: true })).toHaveCount(0);
  await expect(values.nth(3)).toHaveText(prefix.candles.at(-1)!.close);
});
