import { expect, test } from '@playwright/test';
import type { Page, Request } from '@playwright/test';
import type { Catalog, Frame, NativeMarketData } from '../src/api';
import type { CandleHistory } from '../src/candle-model';
import type { PositioningHistory } from '../src/positioning-model';

// Responses come from the native demo; transport failures/delays are explicit.
// The readout stress test only adds trailing decimal zeroes to native values.
// No replacement candles, positioning observations, trades or depth are generated.
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

async function openDemo(page: Page) {
  const response = await page.request.get('/api/v1/instruments');
  expect(response.ok()).toBe(true);
  const catalog = await response.json() as Catalog;
  const source = catalog.sources.find(item => item.mode === 'demo' && item.status === 'ready');
  if (!source) throw new Error('Chart redesign regressions require the real native synthetic demo');
  const created = page.waitForResponse(reply => reply.url().endsWith('/api/v1/sessions') && reply.request().method() === 'POST' && reply.ok());
  await page.goto(`/?source=${encodeURIComponent(source.id)}`);
  const frame = await (await created).json() as Frame;
  await expect(page.locator('.status')).toContainText('Connected');
  await page.getByRole('tablist', { name: 'Chart workspace', exact: true }).getByRole('tab', { name: 'Price action', exact: true }).click();
  await expect(page.getByTestId('price-chart')).toBeVisible();
  await page.getByLabel('Seek delivery', { exact: true }).fill('5000');
  const sought = page.waitForResponse(reply => reply.url().endsWith('/api/v1/replay') && reply.request().postDataJSON().action === 'seek' && reply.ok());
  await page.getByRole('button', { name: 'Seek', exact: true }).click();
  const advanced = await (await sought).json() as Frame;
  expect(advanced.session_id).toBe(frame.session_id);
  // Source controls can leave the stacked mobile plot entirely offscreen.
  await page.getByTestId('price-chart').getByRole('group', { name: 'Price action chart', exact: true }).scrollIntoViewIfNeeded();
  await expect(page.getByTestId('price-chart')).toHaveAttribute('data-presented-generation', advanced.generation);
  return { frame: advanced, catalog, source };
}

async function selectInstrument(page: Page, instrumentId: string, ticker: string) {
  const watchlist = page.locator('.native-controls summary').filter({ hasText: /^Watchlist$/ });
  if (await watchlist.locator('..').getAttribute('open') === null) await watchlist.click();
  await page.getByLabel('Search instruments', { exact: true }).fill(ticker);
  const subscribed = page.waitForResponse(response => response.url().endsWith('/api/v1/subscriptions') && response.ok()
    && response.request().postDataJSON().instrument_id === instrumentId);
  await page.getByTitle(instrumentId, { exact: true }).click();
  return await (await subscribed).json() as Frame;
}

async function observePricePaints(page: Page) {
  await page.addInitScript(() => {
    let paints = 0;
    Object.defineProperty(window, 'pricePaintCount', { get: () => paints });
    const fillRect = CanvasRenderingContext2D.prototype.fillRect;
    CanvasRenderingContext2D.prototype.fillRect = function (x, y, width, height) {
      if (this.canvas.closest('.price-chart__plot')) paints++;
      return fillRect.call(this, x, y, width, height);
    };
  });
}

const pricePaints = (page: Page) => page.evaluate(() => Reflect.get(window, 'pricePaintCount') as number);
const settlePriceLayout = (page: Page) => page.evaluate(() => {
  const { promise, resolve } = Promise.withResolvers<void>();
  requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
  return promise;
});

test('explicit venue history is primary-only in an aggregate replay and never shows old observed candles', async ({ page }) => {
  const { frame } = await openDemo(page);
  const price = page.getByTestId('price-chart');
  const venue = page.getByRole('button', { name: /^Venue:/ });
  await venue.click();
  const selectedReply = page.waitForResponse(response => response.url().endsWith('/api/v1/subscriptions') && response.ok());
  await page.getByRole('listbox', { name: 'Venue', exact: true }).getByRole('option', { name: /^All (spot|perps)/ }).click();
  const selected = await (await selectedReply).json() as Frame;
  expect(selected.session_id).toBe(frame.session_id);
  expect(selected.generation).not.toBe(frame.generation);
  const observedReply = page.waitForResponse(response => response.url().endsWith('/api/v1/candles') && response.ok()
    && response.request().postDataJSON().interval === '1m' && response.request().postDataJSON().source === 'session_trades'
    && response.request().postDataJSON().session_id === selected.session_id
    && response.request().postDataJSON().generation === selected.generation
    && response.request().postDataJSON().instrument_ids.length > 1);
  await page.getByRole('combobox', { name: 'All candle intervals', exact: true }).selectOption('1m');
  const observed = await (await observedReply).json() as CandleHistory;
  expect(observed.session_id).toBe(selected.session_id);
  expect(observed.generation).toBe(selected.generation);
  expect(observed.source).toBe('session_trades');
  expect(observed.instrument_ids.length).toBeGreaterThan(1);
  expect(observed.scope).toBe('aggregate');
  expect(observed.candles.length).toBeGreaterThan(0);
  await expect(price).toHaveAttribute('data-presented-scope', 'aggregate');
  await expect(price).toHaveAttribute('data-presented-generation', selected.generation);
  await expect(price.locator('.price-chart__legend dd').nth(3)).toHaveText(observed.candles.at(-1)!.close);

  // Fail before the server could contact an external venue: this test must remain offline.
  const { promise: venueRequest, resolve: intercepted } = Promise.withResolvers<Record<string, unknown>>();
  await page.route('**/api/v1/candles', async route => {
    const input = route.request().postDataJSON() as Record<string, unknown>;
    if (input.source !== 'venue_ohlcv') { await route.continue(); return; }
    intercepted(input);
    await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Injected venue transport unavailable' }) });
  });
  await price.getByRole('button', { name: 'Venue history', exact: true }).click();
  const input = await venueRequest;
  expect(input.instrument_ids).toEqual([selected.selection.instrument_id]);
  expect(input.session_id).toBe(selected.session_id);
  expect(input.generation).toBe(selected.generation);
  await expect(price).toHaveAttribute('data-source', 'venue_ohlcv');
  await expect(price).toHaveAttribute('data-presented-source', '');
  await expect(price.locator('.price-chart__legend dd')).toHaveCount(0);
  await expect(price.getByText(/Current external venue history is not replay evidence\./)).toBeVisible();
  await expect(venue).toContainText(/All (spot|perps)/);
  await expect(price.getByRole('button', { name: 'OI & funding', exact: true })).toBeDisabled();

  await price.getByRole('button', { name: 'Session trades', exact: true }).click();
  await expect(price).toHaveAttribute('data-presented-source', 'session_trades');
  await expect(price).toHaveAttribute('data-presented-scope', 'aggregate');
  await expect(price).toHaveAttribute('data-presented-generation', selected.generation);
  await expect(price.locator('.price-chart__legend dd').nth(3)).toHaveText(observed.candles.at(-1)!.close);
  const active = await (await page.request.get(`/api/v1/sessions/${frame.session_id}`)).json() as Frame;
  expect(active.generation).toBe(selected.generation);
  expect(active.selection).toEqual(selected.selection);
  expect(active.replay?.cursor).toEqual(selected.replay?.cursor);
});

test('a delayed old ticker response cannot leak into the newly selected native instrument', async ({ page }) => {
  const { frame, catalog } = await openDemo(page);
  const initial = catalog.instruments.find(item => item.instrument_id === frame.selection.instrument_id)!;
  const target = catalog.instruments.find(item => item.market_type === initial.market_type && item.venue === initial.venue && item.base.asset_id !== initial.base.asset_id);
  if (!target) throw new Error('The native demo must offer another instrument on the selected venue');
  const { promise: held, resolve: intercepted } = Promise.withResolvers<void>();
  const { promise: gate, resolve: release } = Promise.withResolvers<void>();
  const { promise: delivered, resolve: completed } = Promise.withResolvers<void>();
  await page.route('**/api/v1/candles', async route => {
    const input = route.request().postDataJSON();
    if (input.interval !== '3m' || input.instrument_ids[0] !== initial.instrument_id) { await route.continue(); return; }
    const response = await route.fetch();
    intercepted();
    await gate;
    await route.fulfill({ response }).catch(() => { /* Superseded native fetch may be canceled. */ });
    completed();
  });
  try {
    await page.getByRole('combobox', { name: 'All candle intervals', exact: true }).selectOption('3m');
    await held;
    const candles = page.waitForResponse(response => response.url().endsWith('/api/v1/candles') && response.ok()
      && response.request().postDataJSON().instrument_ids[0] === target.instrument_id && response.request().postDataJSON().interval === '3m');
    const selected = await selectInstrument(page, target.instrument_id, target.base.ticker);
    const current = await (await candles).json() as CandleHistory;
    expect(current.instrument_id).toBe(target.instrument_id);
    expect(current.session_id).toBe(frame.session_id);
    expect(current.generation).toBe(selected.generation);
    expect(current.candles.length).toBeGreaterThan(0);
    release();
    await delivered;
    const price = page.getByTestId('price-chart');
    await expect(price).toHaveAttribute('data-presented-generation', selected.generation);
    await expect(price.locator('.price-chart__legend dd').nth(3)).toHaveText(current.candles.at(-1)!.close);
    await price.getByText('Chart help & provenance', { exact: true }).click();
    const provenance = price.locator('details').filter({ has: page.getByText('Chart help & provenance', { exact: true }) });
    await expect(provenance).toContainText(target.instrument_id);
    await expect(provenance).not.toContainText(initial.instrument_id);
  } finally {
    release();
    await page.unroute('**/api/v1/candles');
  }
});

test('positioning is opt-in and missing native observations never create empty chart panes', async ({ page }) => {
  let positioningRequests = 0;
  page.on('request', request => { if (request.url().endsWith('/api/v1/positioning')) positioningRequests++; });
  const { catalog } = await openDemo(page);
  const instrument = catalog.instruments.find(item => item.venue === 'hyperliquid' && item.market_type === 'perpetual' && item.base.ticker === 'BTC');
  if (!instrument) throw new Error('The native demo must provide its illustrative Hyperliquid BTC positioning stream');
  const selected = await selectInstrument(page, instrument.instrument_id, instrument.base.ticker);
  const price = page.getByTestId('price-chart');
  const toggle = price.getByRole('button', { name: 'OI & funding', exact: true });
  await expect(toggle).toHaveAttribute('aria-pressed', 'false');
  await expect(price).toHaveAttribute('data-positioning', 'off');
  expect(positioningRequests).toBe(0);
  const plot = price.getByRole('group', { name: 'Price action chart', exact: true });
  await expect(plot).toHaveAttribute('data-positioning-pane-count', '0');
  const positioningReply = page.waitForResponse(response => response.url().endsWith('/api/v1/positioning') && response.ok());
  await toggle.click();
  const positioning = await (await positioningReply).json() as PositioningHistory;
  expect(positioning.session_id).toBe(selected.session_id);
  expect(positioning.generation).toBe(selected.generation);
  expect(positioning.instrument_id).toBe(instrument.instrument_id);
  const series = positioning.series.find(item => item.instrument_id === instrument.instrument_id && item.relation === 'selected');
  expect(positioning.status).toBe('ready');
  expect(series?.bars.length).toBeGreaterThan(0);
  await expect(price).toHaveAttribute('data-positioning', 'ready');
  await expect(price).toHaveAttribute('data-positioning-instrument', instrument.instrument_id);
  await expect(plot).toHaveAttribute('data-positioning-pane-count', '2');
  const latest = series!.bars.at(-1)!;
  const readout = price.getByLabel('Open interest and funding at the inspected time', { exact: true });
  await expect(readout).toContainText(latest.open_interest!.close);
  await expect(readout).toContainText(latest.funding_rate!);

  const emptyReply = page.waitForResponse(response => response.url().endsWith('/api/v1/positioning') && response.ok()
    && response.request().postDataJSON().session_id === selected.session_id
    && response.request().postDataJSON().generation !== positioning.generation);
  const emptyCandlesReply = page.waitForResponse(response => response.url().endsWith('/api/v1/candles') && response.ok()
    && response.request().postDataJSON().source === 'session_trades'
    && response.request().postDataJSON().session_id === selected.session_id
    && response.request().postDataJSON().generation !== positioning.generation);
  const resetReply = page.waitForResponse(response => response.url().endsWith('/api/v1/replay') && response.ok()
    && response.request().postDataJSON().action === 'seek');
  await page.getByLabel('Seek delivery', { exact: true }).fill('0');
  await page.getByRole('button', { name: 'Seek', exact: true }).click();
  const reset = await (await resetReply).json() as Frame;
  expect(reset.session_id).toBe(selected.session_id);
  expect(reset.generation).not.toBe(selected.generation);
  expect(reset.replay?.cursor.delivery_index).toBe('0');
  const emptyCandles = await (await emptyCandlesReply).json() as CandleHistory;
  expect(emptyCandles.session_id).toBe(reset.session_id);
  expect(emptyCandles.generation).toBe(reset.generation);
  expect(emptyCandles.instrument_id).toBe(instrument.instrument_id);
  expect(emptyCandles.status).toBe('unavailable');
  expect(emptyCandles.candles).toEqual([]);
  expect(emptyCandles.source_identity).not.toBe('');
  const empty = await (await emptyReply).json() as PositioningHistory;
  expect(empty.session_id).toBe(reset.session_id);
  expect(empty.generation).toBe(reset.generation);
  expect(empty.instrument_id).toBe(instrument.instrument_id);
  expect(empty.source_identity).toBe(emptyCandles.source_identity);
  expect(empty.interval).toBe(emptyCandles.interval);
  expect(empty.status).toBe('unavailable');
  expect(empty.series.every(item => item.bars.length === 0)).toBe(true);
  await expect(price).toHaveAttribute('data-positioning', empty.status);
  await expect(price).toHaveAttribute('data-presented-generation', reset.generation);
  await expect(price.locator('.price-chart__legend dd')).toHaveCount(0);
  await expect(plot).toHaveAttribute('data-positioning-pane-count', '0');
  await expect(plot).toHaveAttribute('data-chart-pane-count', '2');
  await expect(price).toContainText('No empty panes reserved.');
  await expect(readout).not.toContainText(latest.open_interest!.close);
  await toggle.click();
  await expect(price).toHaveAttribute('data-positioning', 'off');
  await expect(toggle).toHaveAttribute('aria-pressed', 'false');
  const disabledRequests = positioningRequests;
  const intervalReply = page.waitForResponse(response => response.url().endsWith('/api/v1/candles') && response.ok()
    && response.request().postDataJSON().interval === '4h');
  await page.getByRole('combobox', { name: 'All candle intervals', exact: true }).selectOption('4h');
  await intervalReply;
  await page.waitForResponse(response => response.url().endsWith('/api/v1/candles') && response.ok()
    && response.request().postDataJSON().interval === '4h');
  expect(positioningRequests).toBe(disabledRequests);
});

test('named grid editing persists geometry while loading layouts preserves the active source and session', async ({ page }) => {
  const { frame, catalog } = await openDemo(page);
  const toolbar = page.getByRole('navigation', { name: 'Workspace layout', exact: true });
  await toolbar.getByRole('combobox', { name: 'Layout preset', exact: true }).selectOption('price');
  await toolbar.getByRole('button', { name: 'Edit layout', exact: true }).click();
  const chartTile = page.locator('[data-grid-panel="chart"]');
  const before = await chartTile.boundingBox();
  await chartTile.getByRole('button', { name: 'Resize Chart', exact: true }).focus();
  await page.keyboard.press('ArrowLeft');
  await expect.poll(async () => (await chartTile.boundingBox())!.width).toBeLessThan(before!.width);
  const resized = await chartTile.boundingBox();
  await chartTile.getByRole('button', { name: 'Move Chart', exact: true }).focus();
  await page.keyboard.press('ArrowRight');
  await expect.poll(async () => (await chartTile.boundingBox())!.x).toBeGreaterThan(resized!.x);
  const moved = await chartTile.boundingBox();
  const handle = await chartTile.getByRole('button', { name: 'Move Chart', exact: true }).boundingBox();
  await page.mouse.move(handle!.x + handle!.width / 2, handle!.y + handle!.height / 2);
  await page.mouse.down();
  await page.mouse.move(handle!.x + handle!.width / 2 - before!.width / 12, handle!.y + handle!.height / 2, { steps: 5 });
  await page.mouse.up();
  await expect.poll(async () => (await chartTile.boundingBox())!.x).toBeLessThan(moved!.x);
  await toolbar.getByRole('combobox', { name: 'Add panel', exact: true }).selectOption('cvd');
  await toolbar.getByRole('button', { name: 'Add panel', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Cumulative Delta', exact: true })).toBeVisible();
  const cvdTile = page.locator('[data-grid-panel="cvd"]');
  const placed = await chartTile.boundingBox();
  await chartTile.getByRole('button', { name: 'Move Chart', exact: true }).press('ArrowDown');
  await expect(toolbar.getByRole('status')).toContainText('Panels cannot overlap');
  expect(await chartTile.boundingBox()).toEqual(placed);
  const cvdBefore = await cvdTile.boundingBox();
  await cvdTile.getByRole('button', { name: 'Resize Cumulative delta', exact: true }).focus();
  await page.keyboard.press('ArrowDown');
  await expect.poll(async () => (await cvdTile.boundingBox())!.height).toBeGreaterThan(cvdBefore!.height);
  await toolbar.getByText('Saved layouts', { exact: true }).click();
  await toolbar.getByRole('textbox', { name: 'Layout name', exact: true }).fill('Exact chart review');
  await toolbar.getByRole('button', { name: 'Save layout', exact: true }).click();
  await expect(toolbar.getByRole('combobox', { name: 'Saved layout', exact: true })).toHaveValue('Exact chart review');
  await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('limit-orderbook.workspace.v1')!).layouts
    .map((layout: { name: string }) => layout.name))).toContain('Exact chart review');
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('limit-orderbook.workspace.v1')!));
  expect(saved.layouts.find((layout: { name: string }) => layout.name === 'Exact chart review').settings.grid).toEqual(saved.grid);
  await cvdTile.getByRole('button', { name: 'Remove Cumulative delta', exact: true }).click();
  await expect(cvdTile).toHaveCount(0);
  await toolbar.getByRole('button', { name: 'Finish editing', exact: true }).click();
  await expect(chartTile.getByRole('button', { name: 'Move Chart', exact: true })).toHaveCount(0);
  await expect(toolbar.getByText('Saved layouts', { exact: true }).locator('..')).not.toHaveAttribute('open', '');

  // A saved layout must not act as a hidden instrument/source/session bookmark.
  const initial = catalog.instruments.find(item => item.instrument_id === frame.selection.instrument_id)!;
  const target = catalog.instruments.find(item => item.market_type === initial.market_type && item.base.asset_id !== initial.base.asset_id);
  if (!target) throw new Error('The native demo must offer another base instrument');
  const selected = await selectInstrument(page, target.instrument_id, target.base.ticker);
  await page.getByTestId('price-chart').getByRole('button', { name: 'Session trades', exact: true }).click();
  await toolbar.getByText('Saved layouts', { exact: true }).click();
  await toolbar.getByRole('button', { name: 'Load layout', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Cumulative Delta', exact: true })).toBeVisible();
  const activeResponse = await page.request.get(`/api/v1/sessions/${frame.session_id}`);
  expect(activeResponse.ok()).toBe(true);
  const active = await activeResponse.json() as Frame;
  expect(active.session_id).toBe(selected.session_id);
  expect(active.source).toEqual(selected.source);
  expect(active.selection).toEqual(selected.selection);
  expect(active.generation).toBe(selected.generation);
  expect(active.replay?.cursor).toEqual(selected.replay?.cursor);
  await expect(page.getByRole('button', { name: /^Instrument:/ })).toContainText(target.base.ticker);
  await expect(page.getByTestId('price-chart')).toHaveAttribute('data-source', 'session_trades');
  await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('limit-orderbook.workspace.v1')!).grid)).toEqual(saved.grid);
  await toolbar.getByText('Saved layouts', { exact: true }).click();
  await toolbar.getByRole('textbox', { name: 'Layout name', exact: true }).fill('Retained review');
  await toolbar.getByRole('button', { name: 'Rename layout', exact: true }).click();
  await expect(toolbar.getByRole('combobox', { name: 'Saved layout', exact: true })).toHaveValue('Retained review');

  await page.reload();
  await expect(page.locator('.status')).toContainText('Connected');
  await toolbar.getByText('Saved layouts', { exact: true }).click();
  await expect(toolbar.getByRole('combobox', { name: 'Saved layout', exact: true }).locator('option', { hasText: 'Retained review' })).toHaveCount(1);
  await expect(page.locator('[data-grid-panel="cvd"]')).toBeVisible();
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('limit-orderbook.workspace.v1')!).grid)).toEqual(saved.grid);
  await toolbar.getByRole('combobox', { name: 'Saved layout', exact: true }).selectOption('Retained review');
  await toolbar.getByRole('button', { name: 'Delete layout', exact: true }).click();
  await expect(toolbar.getByRole('combobox', { name: 'Saved layout', exact: true }).locator('option', { hasText: 'Retained review' })).toHaveCount(0);
  await expect(page.locator('[data-grid-panel="cvd"]')).toBeVisible();
});

for (const width of [1440, 1280, 390]) {
  test(`all six themes repaint native liquidity without changing source or viewport at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 1280 ? 800 : 900 });
    const { frame } = await openDemo(page);
    const intervals = page.getByRole('combobox', { name: 'All candle intervals', exact: true });
    await expect(intervals).toHaveCount(1);
    await expect(intervals).toBeVisible();
    await intervals.selectOption('1M');
    await expect(intervals).toHaveValue('1M');
    await expect(page.getByTestId('price-chart')).toHaveAttribute('data-presented-interval', '1M');
    await expect(page.getByRole('button', { name: 'Candle timeframe', exact: true })).toHaveCount(0);
    await page.getByRole('tablist', { name: 'Chart workspace', exact: true }).getByRole('tab', { name: 'Liquidity & flow', exact: true }).click();
    const pane = page.getByTestId('chart-pane-heatmap');
    const chart = pane.getByTestId('market-charts');
    const canvas = pane.getByTestId('liquidity-canvas');
    await canvas.scrollIntoViewIfNeeded();
    await expect(chart).toHaveAttribute('data-painted-view-id', /.+/);
    await pane.getByTestId('view-freeze').click();
    await expect(pane.getByTestId('view-freeze')).toHaveAttribute('aria-pressed', 'true');
    const identity = await chart.getAttribute('data-painted-view-id');
    const range = [await chart.getAttribute('data-start-ns'), await chart.getAttribute('data-end-ns')];
    for (const theme of ['dark', 'light', 'forest', 'futuristic', 'dark-dull', 'light-dull']) {
      await page.getByRole('combobox', { name: 'Theme', exact: true }).selectOption(theme);
      await canvas.scrollIntoViewIfNeeded();
      await expect.poll(() => canvas.evaluate(node => {
        const canvas = node as HTMLCanvasElement;
        const token = getComputedStyle(canvas).getPropertyValue('--bg-chart').trim();
        const reference = document.createElement('canvas');
        reference.width = reference.height = 1;
        const context = reference.getContext('2d')!;
        context.fillStyle = token;
        context.fillRect(0, 0, 1, 1);
        return {
          actual: [...canvas.getContext('2d')!.getImageData(0, 0, 1, 1).data],
          expected: [...context.getImageData(0, 0, 1, 1).data],
        };
      }).then(pixel => pixel.actual.join(',') === pixel.expected.join(','))).toBe(true);
      await expect(chart).toHaveAttribute('data-painted-view-id', identity!);
      expect([await chart.getAttribute('data-start-ns'), await chart.getAttribute('data-end-ns')]).toEqual(range);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }
    const active = await (await page.request.get(`/api/v1/sessions/${frame.session_id}`)).json() as Frame;
    expect(active.generation).toBe(frame.generation);
    expect(active.selection).toEqual(frame.selection);
    expect(active.replay?.cursor).toEqual(frame.replay?.cursor);
    if (width === 390) {
      const cells = await page.locator('[data-grid-panel]').evaluateAll(nodes => nodes.map(node => {
        const box = node.getBoundingClientRect();
        return { x: box.x, y: box.y, right: box.right, bottom: box.bottom };
      }));
      expect(cells.length).toBeGreaterThan(1);
      for (let index = 0; index < cells.length; index++) {
        expect(cells[index]!.x).toBeGreaterThanOrEqual(0);
        expect(cells[index]!.right).toBeLessThanOrEqual(width);
        if (index) expect(cells[index]!.y).toBeGreaterThanOrEqual(cells[index - 1]!.bottom);
      }
    }
  });
}

test('footprint layers inspect retained native prints and historical resting liquidity without inventing missing evidence', async ({ page }) => {
  const marketReads: Promise<void>[] = [];
  const replies: NativeMarketData[] = [];
  const marketErrors: unknown[] = [];
  const collectMarket = (request: Request) => {
    if (!request.url().endsWith('/api/v1/market-data')) return;
    // Headers alone are not evidence: generation changes can abort the body.
    marketReads.push((async () => {
      const response = await request.response();
      if (response?.ok()) replies.push(await response.json() as NativeMarketData);
    })().catch(error => { marketErrors.push(error); }));
  };
  const failedMarket = (request: Request) => {
    if (!request.url().endsWith('/api/v1/market-data')) return;
    const failure = request.failure()?.errorText;
    // Native collection cancels obsolete generations; other failures stay fatal.
    if (failure !== 'net::ERR_ABORTED') marketErrors.push(new Error(`Native market request failed: ${failure}`));
  };
  page.on('requestfinished', collectMarket);
  page.on('requestfailed', failedMarket);
  const { frame } = await openDemo(page);
  await page.getByRole('tablist', { name: 'Chart workspace', exact: true }).getByRole('tab', { name: 'Footprint', exact: true }).click();
  const footprint = page.getByRole('region', { name: 'Footprint', exact: true });
  await footprint.scrollIntoViewIfNeeded();
  const indicators = footprint.getByRole('button', { name: /^Indicators/ });
  await indicators.click();
  const resting = page.getByRole('menuitemcheckbox', { name: /^Resting liquidity/ });
  const prints = page.getByRole('menuitemcheckbox', { name: /^Large prints/ });
  await expect(resting).toHaveAttribute('aria-checked', 'false');
  await expect(prints).toHaveAttribute('aria-checked', 'false');
  await page.getByLabel('Resting quantity ceiling', { exact: true }).fill('2');
  await page.getByLabel('Resting liquidity opacity', { exact: true }).press('Home');
  for (let step = 0; step < 9; step++) await page.getByLabel('Resting liquidity opacity', { exact: true }).press('ArrowRight');
  await page.getByLabel('Large print minimum size', { exact: true }).fill('0');
  await resting.click();
  await prints.click();
  await indicators.click();
  const liquidity = page.getByTestId('footprint-liquidity-status');
  await expect(liquidity).toContainText('Unknown spans blank');
  await expect(liquidity).toContainText('0–2 base');
  await expect(liquidity).toContainText('50% opacity');
  await expect(liquidity).toContainText(/[1-9]\d* historical tiles/);
  await expect(page.getByTestId('footprint-print-status')).toContainText('no liquidation feed');
  await footprint.getByText('Inspect retained large prints', { exact: true }).click();
  const retained = footprint.locator('details').filter({ has: page.getByText('Inspect retained large prints', { exact: true }) });
  const firstPrint = retained.locator('li').first();
  await expect(firstPrint).toBeVisible();
  const inspected = await firstPrint.innerText();
  page.off('requestfinished', collectMarket);
  page.off('requestfailed', failedMarket);
  await Promise.all(marketReads);
  expect(marketErrors, 'Completed native responses must contain valid JSON; only generation cancellation is expected').toEqual([]);
  const native = replies.filter(reply => reply.session_id === frame.session_id && reply.generation === frame.generation)
    .flatMap(reply => reply.trades)
    .find(trade => inspected.includes(`Instrument: ${trade.instrument_id}\n`) && inspected.includes(`Projected receive ns: ${trade.display_time_ns}\n`)
      && inspected.includes(`Trade: ${trade.trade.trade_id ?? trade.trade.dedup_key}`));
  expect(native, 'Displayed print must identify an individually retained native response trade').toBeDefined();
  await expect(firstPrint).toContainText(`Price: ${native!.trade.price}`);
  await expect(firstPrint).toContainText(`Base size: ${native!.base_quantity}`);
  await expect(firstPrint).toContainText(`Native size: ${native!.trade.quantity} ${native!.trade.quantity_unit}`);
  await expect(firstPrint).toContainText(`Original receive wall ns: ${native!.asof.receive_wall_ns}`);
  await expect(firstPrint).toContainText(`Receive monotonic ns: ${native!.asof.receive_mono_ns}`);
  await expect(firstPrint).toContainText(`Clock: ${native!.asof.clock_id}`);
  await expect(firstPrint).toContainText(`Venue: ${native!.instrument_id.split(':')[0]}`);

  // The threshold must filter actual print evidence, not just recolor badges.
  await indicators.click();
  await page.getByLabel('Large print minimum size', { exact: true }).fill('1000000000');
  await indicators.click();
  await expect(page.getByTestId('footprint-print-status')).toContainText('No matching retained native trades');
  await expect(retained).toHaveCount(0);
  await indicators.click();
  await page.getByLabel('Large print minimum size', { exact: true }).fill('0');
  await indicators.click();
  await expect(footprint.getByText('Inspect retained large prints', { exact: true })).toBeVisible();
  const resetReply = page.waitForResponse(response => response.url().endsWith('/api/v1/replay') && response.ok()
    && response.request().postDataJSON().action === 'seek');
  await page.getByLabel('Seek delivery', { exact: true }).fill('0');
  await page.getByRole('button', { name: 'Seek', exact: true }).click();
  await resetReply;
  await expect(page.getByTestId('footprint-print-status')).toContainText('No matching retained native trades');
  await expect(liquidity).not.toContainText(/\d+ historical tiles/);
  await expect(liquidity).toContainText(/No historical|Waiting for|No eligible/);
  await expect(liquidity).toContainText('Unknown spans blank');
  await expect(retained).toHaveCount(0);
  await indicators.click();
  await resting.click();
  await prints.click();
  await indicators.click();
  await expect(liquidity).toHaveCount(0);
  await expect(page.getByTestId('footprint-print-status')).toHaveCount(0);
});

test('offscreen price presentation stops painting and history requests while native collection stays owned', async ({ page }) => {
  await observePricePaints(page);
  let historyRequests = 0;
  let nativeRequests = 0;
  page.on('request', request => {
    if (/\/api\/v1\/(candles|positioning)$/.test(request.url())) historyRequests++;
    if (request.url().includes('/market-data')) nativeRequests++;
  });
  await page.setViewportSize({ width: 390, height: 900 });
  const { frame } = await openDemo(page);
  await page.getByRole('combobox', { name: 'Layout preset', exact: true }).selectOption('volatility');
  await page.getByRole('combobox', { name: 'All candle intervals', exact: true }).selectOption('1m');
  const price = page.getByTestId('price-chart');
  const plot = price.getByRole('group', { name: 'Price action chart', exact: true });
  await plot.scrollIntoViewIfNeeded();
  await expect(price).toHaveAttribute('data-presentation-active', 'true');
  await expect(price).toHaveAttribute('data-presented-interval', '1m');
  const contextReply = page.waitForResponse(response => response.url().endsWith('/api/v1/positioning') && response.ok());
  await price.getByRole('button', { name: 'OI & funding', exact: true }).click();
  await contextReply;
  await plot.scrollIntoViewIfNeeded();
  await page.mouse.move(0, 0);
  await plot.press('Home');
  await plot.press('ArrowLeft');
  await settlePriceLayout(page);
  const readout = await price.locator('.price-chart__legend').textContent();
  const beforePixels = await plot.screenshot();

  // Scroll the real stacked workspace, rather than faking IntersectionObserver.
  await page.locator('[data-grid-panel="volatility"]').scrollIntoViewIfNeeded();
  await expect(price).toHaveAttribute('data-presentation-active', 'false');
  await expect(plot.locator('canvas')).toHaveCount(0);
  const historyBefore = historyRequests;
  const nativeBefore = nativeRequests;
  const paintBefore = await pricePaints(page);
  // Observe longer than the native-history five-second polling interval.
  await page.waitForTimeout(5500);
  expect(historyRequests).toBe(historyBefore);
  expect(await pricePaints(page)).toBe(paintBefore);
  expect(nativeRequests).toBeGreaterThan(nativeBefore);
  await expect(price.locator('.price-chart__legend')).toHaveText(readout!);
  const retained = await (await page.request.get(`/api/v1/sessions/${frame.session_id}`)).json() as Frame;
  expect(retained.generation).toBe(frame.generation);
  expect(retained.replay?.cursor).toEqual(frame.replay?.cursor);

  const resumed = page.waitForResponse(response => response.url().endsWith('/api/v1/candles') && response.ok());
  await plot.scrollIntoViewIfNeeded();
  await resumed;
  await expect(price).toHaveAttribute('data-presentation-active', 'true');
  await expect(price.locator('.price-chart__legend')).toHaveText(readout!);
  await expect(price.getByRole('button', { name: 'Latest', exact: true })).toHaveAttribute('aria-pressed', 'false');
  await settlePriceLayout(page);
  expect(await plot.screenshot()).toEqual(beforePixels);
});

test('static price pixels repaint on DPR and theme changes and suspend on document visibility changes', async ({ page }) => {
  await observePricePaints(page);
  await openDemo(page);
  await page.getByRole('combobox', { name: 'Layout preset', exact: true }).selectOption('price');
  await page.getByRole('combobox', { name: 'All candle intervals', exact: true }).selectOption('1m');
  const price = page.getByTestId('price-chart');
  const plot = price.getByRole('group', { name: 'Price action chart', exact: true });
  await expect(price).toHaveAttribute('data-presented-interval', '1m');
  await page.mouse.move(0, 0);
  await plot.press('Home');
  await plot.press('ArrowLeft');
  await settlePriceLayout(page);
  const exact = await price.locator('.price-chart__legend').textContent();
  await expect(price.locator('.price-chart__legend')).toHaveAttribute('aria-live', 'polite');
  const paints = await pricePaints(page);
  expect(paints).toBeGreaterThan(0);
  // A paused source must not create a self-sustaining canvas/layout RAF loop.
  await page.waitForTimeout(350);
  expect(await pricePaints(page)).toBe(paints);

  const browser = await page.context().newCDPSession(page);
  await browser.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 2, mobile: false });
  await expect.poll(() => page.evaluate(() => window.devicePixelRatio)).toBe(2);
  // LWC uses the browser's physical pixel box. Chromium CDP can change the JS
  // ratio without changing that box; every visible canvas must match the box,
  // not an emulated ratio that contradicts the browser's own resize evidence.
  await expect.poll(() => plot.locator('canvas').evaluateAll(nodes => {
    if (!nodes.length) return false;
    const { promise, resolve } = Promise.withResolvers<boolean>();
    const results = new Map<Element, boolean>();
    const observer = new ResizeObserver(entries => {
      for (const entry of entries) {
        const canvas = entry.target as HTMLCanvasElement;
        const box = entry.devicePixelContentBoxSize?.[0];
        const width = box?.inlineSize ?? Math.round(entry.contentRect.width * devicePixelRatio);
        const height = box?.blockSize ?? Math.round(entry.contentRect.height * devicePixelRatio);
        results.set(canvas, width > 0 && height > 0 && Math.abs(canvas.width - width) <= 1 && Math.abs(canvas.height - height) <= 1);
      }
      if (results.size === nodes.length) {
        observer.disconnect();
        resolve([...results.values()].every(Boolean));
      }
    });
    nodes.forEach(node => observer.observe(node, { box: 'device-pixel-content-box' }));
    return promise;
  })).toBe(true);
  await expect(price.locator('.price-chart__legend')).toHaveText(exact!);
  await expect.poll(() => pricePaints(page)).toBeGreaterThan(paints);
  await settlePriceLayout(page);
  const darkPixels = await plot.screenshot();
  const fullWidth = (await plot.boundingBox())!.width;
  await browser.send('Emulation.setDeviceMetricsOverride', { width: 1200, height: 900, deviceScaleFactor: 2, mobile: false });
  await expect.poll(async () => (await plot.boundingBox())!.width).toBeLessThan(fullWidth);
  await settlePriceLayout(page);
  await expect(price.locator('.price-chart__legend')).toHaveText(exact!);
  await expect(price.locator('.price-chart__legend')).toHaveAttribute('aria-live', 'polite');
  await browser.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 2, mobile: false });
  await expect.poll(async () => (await plot.boundingBox())!.width).toBe(fullWidth);
  await settlePriceLayout(page);
  expect(await plot.screenshot()).toEqual(darkPixels);
  await page.getByRole('combobox', { name: 'Theme', exact: true }).selectOption('light');
  await settlePriceLayout(page);
  expect(await plot.screenshot()).not.toEqual(darkPixels);
  await expect(price.locator('.price-chart__legend')).toHaveText(exact!);
  await expect(price.getByRole('button', { name: 'Latest', exact: true })).toHaveAttribute('aria-pressed', 'false');
  // Exercise the browser visibility event contract deterministically; the
  // offscreen regression above separately uses real viewport intersection.
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect(price).toHaveAttribute('data-presentation-active', 'false');
  await expect(plot.locator('canvas')).toHaveCount(0);
  const hiddenPaints = await pricePaints(page);
  await page.waitForTimeout(350);
  expect(await pricePaints(page)).toBe(hiddenPaints);
  await page.evaluate(() => {
    Reflect.deleteProperty(document, 'visibilityState');
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect(price).toHaveAttribute('data-presentation-active', 'true');
  await expect(price.locator('.price-chart__legend')).toHaveText(exact!);
  await expect.poll(() => pricePaints(page)).toBeGreaterThan(hiddenPaints);
  await browser.send('Emulation.clearDeviceMetricsOverride');
  await browser.detach();
  // Synthetic SDK reprojection cannot take ownership, but actual pointer input can.
  const bounds = (await plot.boundingBox())!;
  // Traverse the plot, including its real bars. The sparse demo leaves blank
  // space at the midpoint; hovering blank space has no candle to inspect.
  await page.mouse.move(bounds.x + 8, bounds.y + bounds.height / 3);
  await page.mouse.move(bounds.x + bounds.width - 100, bounds.y + bounds.height / 3, { steps: 100 });
  await expect(price.locator('.price-chart__legend')).toHaveAttribute('aria-live', 'off');
  await plot.press('ArrowLeft');
  await expect(price.locator('.price-chart__legend')).toHaveAttribute('aria-live', 'polite');
});

test('long exact OHLC and OI readouts scroll without changing narrow plot geometry during inspection', async ({ page }) => {
  // Precision representation stress: trailing zeroes preserve every native value,
  // timestamp and observation. They are not invented market/accounting evidence.
  const padded = (value: string, index: number) => `${value}${value.includes('.') ? '' : '.'}${'0'.repeat(index % 2 ? 32 : 24)}`;
  await page.route('**/api/v1/candles', async route => {
    const response = await route.fetch();
    if (!response.ok()) { await route.fulfill({ response }); return; }
    const data = await response.json() as CandleHistory;
    data.candles.forEach((bar, index) => {
      for (const field of ['open', 'high', 'low', 'close', 'volume'] as const) bar[field] = padded(bar[field], index);
    });
    await route.fulfill({ response, json: data });
  });
  await page.route('**/api/v1/positioning', async route => {
    const response = await route.fetch();
    if (!response.ok()) { await route.fulfill({ response }); return; }
    const data = await response.json() as PositioningHistory;
    data.series.forEach(series => series.bars.forEach((bar, index) => {
      if (bar.open_interest) for (const field of ['open', 'high', 'low', 'close', 'change'] as const) bar.open_interest[field] = padded(bar.open_interest[field], index);
      if (bar.funding_rate !== null) bar.funding_rate = padded(bar.funding_rate, index);
    }));
    await route.fulfill({ response, json: data });
  });
  await page.setViewportSize({ width: 390, height: 900 });
  const { catalog } = await openDemo(page);
  const instrument = catalog.instruments.find(item => item.venue === 'hyperliquid' && item.market_type === 'perpetual' && item.base.ticker === 'BTC');
  if (!instrument) throw new Error('Native demo must supply observed Hyperliquid positioning');
  await selectInstrument(page, instrument.instrument_id, instrument.base.ticker);
  await page.getByRole('combobox', { name: 'All candle intervals', exact: true }).selectOption('1m');
  const price = page.getByTestId('price-chart');
  const plot = price.getByRole('group', { name: 'Price action chart', exact: true });
  await plot.scrollIntoViewIfNeeded();
  await expect(price).toHaveAttribute('data-presented-interval', '1m');
  await price.getByRole('button', { name: 'OI & funding', exact: true }).click();
  await expect(price).toHaveAttribute('data-positioning', 'ready');
  const legend = price.locator('.price-chart__legend');
  const interest = price.getByLabel('Open interest and funding at the inspected time', { exact: true });
  await expect(legend.locator('dd').nth(3)).toHaveText(/0{24}/);
  await expect(interest).toContainText(/0{24}/);
  await plot.scrollIntoViewIfNeeded();
  await page.mouse.move(0, 0);
  await plot.focus();
  await settlePriceLayout(page);
  const geometry = () => price.evaluate(node => ['.price-chart__legend', '.price-chart__positioning', '.price-chart__plot'].map(selector => {
    const bounds = node.querySelector(selector)!.getBoundingClientRect();
    return { height: bounds.height, top: bounds.top };
  }));
  const before = await geometry();
  const readings = new Set<string>();
  for (const key of ['ArrowLeft', 'ArrowRight', 'ArrowLeft', 'ArrowRight']) {
    await plot.press(key);
    await settlePriceLayout(page);
    readings.add((await legend.textContent())!);
    expect(await geometry()).toEqual(before);
  }
  expect(readings.size).toBe(2);
  for (const readout of [legend, interest]) {
    expect(await readout.evaluate(node => node.scrollWidth > node.clientWidth)).toBe(true);
    await readout.evaluate(node => { node.scrollLeft = node.scrollWidth; });
    expect(await readout.evaluate(node => node.scrollLeft)).toBeGreaterThan(0);
  }
  expect(await geometry()).toEqual(before);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('grid touch gestures ignore foreign contacts and cancellation releases the owning pointer', async ({ page }) => {
  await openDemo(page);
  const toolbar = page.getByRole('navigation', { name: 'Workspace layout', exact: true });
  await toolbar.getByRole('combobox', { name: 'Layout preset', exact: true }).selectOption('price');
  await toolbar.getByRole('button', { name: 'Edit layout', exact: true }).click();
  const tile = page.locator('[data-grid-panel="chart"]');
  await tile.getByRole('button', { name: 'Resize Chart', exact: true }).press('ArrowLeft');
  const move = tile.getByRole('button', { name: 'Move Chart', exact: true });
  await move.press('ArrowRight');
  const before = await tile.getAttribute('style');
  const handle = (await move.boundingBox())!;
  const x = handle.x + 15, y = handle.y + handle.height / 2;
  const column = (await page.locator('.grid-surface').boundingBox())!.width / 12;
  await move.evaluate(() => {
    document.addEventListener('pointerdown', event => {
      Reflect.set(window, event.isPrimary ? 'gridOwnerPointer' : 'gridForeignPointer', event.pointerId);
    }, true);
    document.addEventListener('pointerup', event => Reflect.set(window, 'gridEndedPointer', event.pointerId), true);
    document.addEventListener('pointercancel', event => Reflect.set(window, 'gridCancelledPointer', event.pointerId), true);
  });
  const input = await page.context().newCDPSession(page);
  const owner = { x, y, id: 11 };
  await input.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [owner] });
  await expect(tile.locator('.grid-drag-preview')).toHaveCount(1);
  await input.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [owner, { x: x + 10, y, id: 22 }] });
  const foreign = { x: x + 10 + column, y, id: 22 };
  await input.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [owner, foreign] });
  await expect(tile.locator('.grid-drag-preview')).toHaveCSS('transform', 'matrix(1, 0, 0, 1, 0, 0)');
  // Chromium's nonempty touchEnd list identifies the contacts being released,
  // not the remaining contacts. Sending [owner] actually ends the owning gesture.
  await input.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [foreign] });
  expect(await page.evaluate(() => Reflect.get(window, 'gridEndedPointer'))).toBe(await page.evaluate(() => Reflect.get(window, 'gridForeignPointer')));
  expect(await page.evaluate(() => Reflect.get(window, 'gridEndedPointer'))).not.toBe(await page.evaluate(() => Reflect.get(window, 'gridOwnerPointer')));
  expect(await move.evaluate(node => node.hasPointerCapture(Reflect.get(window, 'gridOwnerPointer') as number))).toBe(true);
  await expect(tile).toHaveAttribute('style', before!);
  await expect(tile.locator('.grid-drag-preview')).toHaveCount(1);
  await input.send('Input.dispatchTouchEvent', { type: 'touchCancel', touchPoints: [] });
  await expect(tile.locator('.grid-drag-preview')).toHaveCount(0);
  expect(await page.evaluate(() => Reflect.get(window, 'gridCancelledPointer'))).toBe(await page.evaluate(() => Reflect.get(window, 'gridOwnerPointer')));
  expect(await move.evaluate(node => node.hasPointerCapture(Reflect.get(window, 'gridOwnerPointer') as number))).toBe(false);
  await expect(tile).toHaveAttribute('style', before!);

  // A new real gesture remains usable after cancel; it matches the arrow-key operation.
  await input.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y, id: 33 }] });
  await input.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: x - column, y, id: 33 }] });
  await input.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await expect(tile).not.toHaveAttribute('style', before!);
  await move.press('ArrowRight');
  await expect(tile).toHaveAttribute('style', before!);
  await input.detach();
});

test('narrow grid editing explains stacking and provides tap controls with keyboard parity', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  await openDemo(page);
  const toolbar = page.getByRole('navigation', { name: 'Workspace layout', exact: true });
  await toolbar.getByRole('combobox', { name: 'Layout preset', exact: true }).selectOption('price');
  await toolbar.getByRole('button', { name: 'Edit layout', exact: true }).click();
  await expect(toolbar).toContainText('dragging is disabled');
  await expect(toolbar).toContainText('sizes apply on wider screens');
  const tile = page.locator('[data-grid-panel="chart"]');
  const resize = tile.getByRole('button', { name: 'Resize Chart', exact: true });
  await resize.click();
  await expect(resize).toHaveAttribute('aria-pressed', 'true');
  const before = await tile.getAttribute('style');
  await tile.getByRole('button', { name: 'Resize Chart left', exact: true }).click();
  await expect(tile).not.toHaveAttribute('style', before!);
  const resized = await tile.getAttribute('style');
  await resize.press('ArrowRight');
  await expect(tile).toHaveAttribute('style', before!);
  await resize.press('ArrowLeft');
  await expect(tile).toHaveAttribute('style', resized!);
  await tile.getByRole('button', { name: 'Move Chart', exact: true }).click();
  await tile.getByRole('button', { name: 'Move Chart right', exact: true }).click();
  await expect(tile).not.toHaveAttribute('style', resized!);
  await tile.getByRole('button', { name: 'Move Chart', exact: true }).press('ArrowLeft');
  await expect(tile).toHaveAttribute('style', resized!);
  const buttons = await tile.locator('.grid-direction-controls button').evaluateAll(nodes => nodes.map(node => {
    const rect = node.getBoundingClientRect();
    return { width: rect.width, height: rect.height, left: rect.left, right: rect.right, touchAction: getComputedStyle(node).touchAction };
  }));
  for (const button of buttons) {
    expect(button.width).toBeGreaterThanOrEqual(44);
    expect(button.height).toBeGreaterThanOrEqual(44);
    expect(button.left).toBeGreaterThanOrEqual(0);
    expect(button.right).toBeLessThanOrEqual(390);
    expect(button.touchAction).toBe('pan-y');
  }
});
