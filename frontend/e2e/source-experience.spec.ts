import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import type { Catalog, Frame } from '../src/api';

// Real server sessions only: these checks do not substitute live frames or trades.
const preferenceKey = 'limit-orderbook.workspace.v1';
const creation = (page: Page) => page.waitForResponse(response => response.url().endsWith('/api/v1/sessions') && response.request().method() === 'POST');
async function demoCatalog(page: Page) {
  const response = await page.request.get('/api/v1/instruments');
  expect(response.ok()).toBe(true);
  const catalog = await response.json() as Catalog;
  const demo = catalog.sources.find(source => source.mode === 'demo');
  expect(demo).toBeDefined();
  return { catalog, demo: demo! };
}

test('paused fixture supports scale recovery and independent replay controls', async ({ page, context }) => {
  const { demo } = await demoCatalog(page);
  let plays = 0;
  page.on('request', request => {
    if (request.url().endsWith('/api/v1/replay') && request.method() === 'POST' && request.postDataJSON().action === 'play') plays++;
  });
  const created = creation(page);
  await page.goto(`/?source=${encodeURIComponent(demo.id)}`);
  const frame = await (await created).json() as Frame;
  await expect(page.locator('.status')).toHaveText('Connected');
  const primary = page.getByTestId('chart-pane-heatmap');
  await expect(primary).toBeVisible();
  const secondary = page.getByTestId('chart-pane-flow');
  await expect(secondary).toBeVisible();
  // Choose distinct native buckets so responses remain attributable when the
  // primary changes to footprint; native requests have no presentation pane ID.
  await primary.getByLabel('Time bucket', { exact: true }).selectOption('1000000000');
  const secondaryReady = page.waitForResponse(response => response.url().endsWith('/api/v1/visualization')
    && response.request().method() === 'POST' && response.request().postDataJSON().rows === 32
    && response.request().postDataJSON().bucket_ns === '5000000000');
  await secondary.getByLabel('Time bucket', { exact: true }).selectOption('5000000000');
  expect((await secondaryReady).ok()).toBe(true);
  expect(frame.status).toBe('paused');
  await expect(page.getByTestId('synthetic-guidance')).toBeVisible();
  await expect(page.getByTestId('grouped-ladder').locator('tr.bid').first()).toBeInViewport();
  await expect(page.getByTestId('grouped-ladder').locator('tr.ask').last()).toBeInViewport();
  expect(plays).toBe(0);
  const aggregation = primary.getByLabel('Price aggregation');
  await expect(aggregation.locator('option').nth(1)).toBeAttached();
  const explicitStep = await aggregation.locator('option').nth(1).getAttribute('value') ?? await aggregation.locator('option').nth(1).textContent();
  const explicit = page.waitForResponse(response => response.url().endsWith('/api/v1/visualization')
    && response.request().method() === 'POST' && response.request().postDataJSON().rows === 128
    && response.request().postDataJSON().bucket_ns === '1000000000'
    && response.request().postDataJSON().price_step === explicitStep);
  await aggregation.selectOption(explicitStep!);
  const narrow = await (await explicit).json();
  expect(Number(narrow.price_min) + narrow.rows * Number(narrow.price_step)).toBeLessThan(Number(frame.book!.asks[0].price));
  const footprintResponse = page.waitForResponse(response => response.url().endsWith('/api/v1/visualization')
    && response.request().method() === 'POST' && response.request().postDataJSON().rows === 32
    && response.request().postDataJSON().bucket_ns === '1000000000'
    && response.request().postDataJSON().price_step === explicitStep);
  await primary.getByLabel('Chart mode', { exact: true }).selectOption('footprint');
  const footprint = await (await footprintResponse).json();
  expect(footprint.price_step).toBe(narrow.price_step);
  expect(footprint.trades).toEqual(narrow.trades);
  const automatic = page.waitForResponse(response => response.url().endsWith('/api/v1/visualization')
    && response.request().method() === 'POST' && response.request().postDataJSON().rows === 32
    && response.request().postDataJSON().bucket_ns === '1000000000'
    && response.request().postDataJSON().recenter === true && response.request().postDataJSON().price_step === undefined);
  await aggregation.selectOption('');
  const refit = await (await automatic).json();
  expect(Number(refit.price_min)).toBeLessThanOrEqual(Number(frame.book!.bids[0].price));
  expect(Number(refit.price_min) + refit.rows * Number(refit.price_step)).toBeGreaterThan(Number(frame.book!.asks[0].price));
  expect((await (await page.request.get(`/api/v1/sessions/${frame.session_id}`)).json() as Frame).status).toBe('paused');

  const second = await context.newPage();
  const secondCreated = creation(second);
  await second.goto(`/?source=${encodeURIComponent(demo.id)}`);
  const other = await (await secondCreated).json() as Frame;
  expect(other.session_id).not.toBe(frame.session_id);
  const play = page.waitForResponse(response => response.url().endsWith('/api/v1/replay') && response.request().postDataJSON().action === 'play');
  await page.getByRole('button', { name: 'Play synthetic demo', exact: true }).click();
  expect((await (await play).json() as Frame).status).toBe('playing');
  expect((await (await page.request.get(`/api/v1/sessions/${other.session_id}`)).json() as Frame).status).toBe('paused');
  await expect(second.locator('.cursor')).toContainText(`${other.replay!.cursor.delivery_index} /`);
  expect(plays).toBe(1);
  await second.close();
});

test('missing URL source never opens demo and explicit recovery persists the source choice', async ({ page }) => {
  const { demo } = await demoCatalog(page);
  let sessions = 0;
  page.on('request', request => { if (request.url().endsWith('/api/v1/sessions') && request.method() === 'POST') sessions++; });
  await page.goto('/?source=missing-source-regression');
  await expect(page.locator('.error-banner[role="alert"]')).toBeVisible();
  expect(sessions).toBe(0);
  const created = creation(page);
  await page.getByLabel('Data source').selectOption(demo.id);
  expect((await (await created).json() as Frame).source.id).toBe(demo.id);
  await expect.poll(() => page.evaluate(key => JSON.parse(localStorage.getItem(key) ?? '{}'), preferenceKey)).toMatchObject({ source: { id: demo.id, mode: 'demo' }, sourceExplicit: true });
  expect(new URL(page.url()).searchParams.get('source')).toBe(demo.id);
  const reloaded = creation(page);
  await page.reload();
  expect((await (await reloaded).json() as Frame).source.id).toBe(demo.id);
});

test('explicit saved paused demo survives a server live preference and URL override wins saved source', async ({ page }) => {
  const { demo } = await demoCatalog(page);
  const savedView = { paused: true, range: { start_ns: '0', end_ns: '1000000000' }, follow: false, priceStep: '100', priceOffset: 7 };
  await page.addInitScript(({ key, id, view }) => localStorage.setItem(key, JSON.stringify({
    version: 1, source: { id, mode: 'demo' }, sourceExplicit: true,
    ...view, secondary: view, domPriceStep: '100', domFrozen: true, tapeFrozen: true,
  })), { key: preferenceKey, id: demo.id, view: savedView });
  const created = creation(page);
  await page.goto('/');
  const frame = await (await created).json() as Frame;
  expect(frame.source.id).toBe(demo.id);
  expect(frame.status).toBe('paused');
  await expect(page.locator('.status')).toHaveText('Connected');
  await expect(page.getByTestId('chart-pane-heatmap').getByTestId('view-freeze')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByTestId('chart-pane-flow').getByTestId('view-freeze')).toHaveAttribute('aria-pressed', 'true');
  await expect.poll(() => page.evaluate(key => JSON.parse(localStorage.getItem(key) ?? '{}'), preferenceKey))
    .toMatchObject({ ...savedView, secondary: savedView, domPriceStep: '100', domFrozen: true, tapeFrozen: true });
  const reloaded = creation(page);
  await page.reload();
  const restored = await (await reloaded).json() as Frame;
  expect(restored.source.id).toBe(demo.id);
  await expect(page.locator('.status')).toHaveText('Connected');
  await expect.poll(() => page.evaluate(key => JSON.parse(localStorage.getItem(key) ?? '{}'), preferenceKey))
    .toMatchObject({ ...savedView, secondary: savedView, domPriceStep: '100', domFrozen: true, tapeFrozen: true });
  // A replacement in this document must not inherit the old evidence fence.
  const reconnected = creation(page);
  await page.getByLabel('Reconnect terminal').click();
  const replacement = await (await reconnected).json() as Frame;
  expect(replacement.session_id).not.toBe(restored.session_id);
  await expect(page.locator('.status')).toHaveText('Connected');
  await expect(page.getByTestId('chart-pane-heatmap').getByTestId('view-freeze')).toHaveAttribute('aria-pressed', 'false');
  await expect(page.getByTestId('chart-pane-flow').getByTestId('view-freeze')).toHaveAttribute('aria-pressed', 'false');
  await expect.poll(() => page.evaluate(key => JSON.parse(localStorage.getItem(key) ?? '{}'), preferenceKey))
    .toMatchObject({ paused: false, range: null, follow: true, domFrozen: false, tapeFrozen: false,
      secondary: { paused: false, range: null, follow: true } });
  await page.goto('/?source=missing-explicit-override');
  await expect(page.locator('.error-banner[role="alert"]')).toBeVisible();
  await expect(page.getByTestId('synthetic-guidance')).toHaveCount(0);
});

for (const resolution of ['URL override', 'server preference'] as const) {
  test(`${resolution} clears another source's saved ranges, scales and freezes on first attachment`, async ({ page }) => {
    const { catalog, demo } = await demoCatalog(page);
    const expectedSource = resolution === 'URL override' ? demo.id : catalog.preferred_source_id ?? demo.id;
    const savedView = { paused: true, range: { start_ns: '0', end_ns: '1000000000' }, follow: false, priceStep: '100', priceOffset: 7 };
    await page.addInitScript(({ key, view, explicit }) => localStorage.setItem(key, JSON.stringify({
      version: 1, source: { id: 'retired-recording-regression', mode: 'replay' }, sourceExplicit: explicit,
      ...view, secondary: { ...view, priceOffset: -7 }, domPriceStep: '100', domFrozen: true, tapeFrozen: true,
    })), { key: preferenceKey, view: savedView, explicit: resolution === 'URL override' });
    const created = creation(page);
    await page.goto(resolution === 'URL override' ? `/?source=${encodeURIComponent(demo.id)}` : '/');
    expect((await (await created).json() as Frame).source.id).toBe(expectedSource);
    await expect(page.locator('.status')).toHaveText('Connected');
    await expect(page.getByTestId('chart-pane-heatmap').getByTestId('view-freeze')).toHaveAttribute('aria-pressed', 'false');
    await expect(page.getByTestId('chart-pane-flow').getByTestId('view-freeze')).toHaveAttribute('aria-pressed', 'false');
    await expect(page.getByRole('button', { name: 'Freeze tape', exact: true })).toHaveAttribute('aria-pressed', 'false');
    await expect.poll(() => page.evaluate(key => JSON.parse(localStorage.getItem(key) ?? '{}'), preferenceKey))
      .toMatchObject({
        source: { id: expectedSource }, paused: false, range: null, follow: true, priceStep: '', priceOffset: 0,
        secondary: { paused: false, range: null, follow: true, priceStep: '', priceOffset: 0 },
        domPriceStep: '', domFrozen: false, tapeFrozen: false,
      });
  });
}

test('persisted tape freeze captures first eligible trades and holds them until resumed', async ({ page }) => {
  const { demo } = await demoCatalog(page);
  await page.addInitScript(({ key, id }) => localStorage.setItem(key, JSON.stringify({
    version: 1, source: { id, mode: 'demo' }, sourceExplicit: true, tapeFrozen: true,
  })), { key: preferenceKey, id: demo.id });
  const created = creation(page);
  await page.goto(`/?source=${encodeURIComponent(demo.id)}`);
  const initial = await (await created).json() as Frame;
  expect(initial.analytics!.tape.length).toBeGreaterThan(0);
  await expect(page.locator('.status')).toHaveText('Connected');
  const rows = page.locator('.tape-panel tbody tr');
  const prices = page.locator('.tape-panel .tape-price');
  await expect(rows).toHaveCount(initial.analytics!.tape.slice(-50).length);
  await expect.poll(() => prices.evaluateAll(nodes => nodes.map(node => node.getAttribute('aria-label'))))
    .toEqual(initial.analytics!.tape.slice(-50).reverse().map(item => `Link trade price ${item.trade.price}`));
  await expect(page.getByRole('button', { name: 'Resume tape', exact: true })).toHaveAttribute('aria-pressed', 'true');
  const frozenRows = await rows.allTextContents();

  // Advance real source deliveries far enough to include new selected-instrument
  // trades. Unlike seek/reset, stepping retains the evidence generation.
  const response = await page.request.post('/api/v1/replay', {
    data: { session_id: initial.session_id, generation: initial.generation, action: 'step', count: 100 },
  });
  expect(response.ok()).toBe(true);
  const advanced = await response.json() as Frame;
  expect(advanced.generation).toBe(initial.generation);
  expect(advanced.analytics!.tape.length).toBeGreaterThan(initial.analytics!.tape.length);
  await expect(page.locator('.cursor')).toContainText(`${advanced.replay!.cursor.delivery_index} /`);
  await expect(rows).toHaveText(frozenRows);
  await expect(page.getByRole('button', { name: 'Resume tape', exact: true })).toHaveAttribute('aria-pressed', 'true');

  await page.getByRole('button', { name: 'Resume tape', exact: true }).click();
  await expect(rows).toHaveCount(advanced.analytics!.tape.slice(-50).length);
  await expect.poll(() => prices.evaluateAll(nodes => nodes.map(node => node.getAttribute('aria-label'))))
    .toEqual(advanced.analytics!.tape.slice(-50).reverse().map(item => `Link trade price ${item.trade.price}`));
  await expect(page.getByRole('button', { name: 'Freeze tape', exact: true })).toHaveAttribute('aria-pressed', 'false');
});

test('network launch migrates automatic legacy demo to actual public live with an inspectable notice', async ({ page }) => {
  test.setTimeout(90_000);
  const { catalog, demo } = await demoCatalog(page);
  test.skip(catalog.preferred_source_id !== 'live', 'Requires operator-enabled public live server; offline mode is not promoted to network access.');
  await page.addInitScript(({ key, id }) => localStorage.setItem(key, JSON.stringify({ version: 1, source: { id, mode: 'demo' } })), { key: preferenceKey, id: demo.id });
  const created = creation(page);
  await page.goto('/');
  const frame = await (await created).json() as Frame;
  expect(frame.source.mode).toBe('live');
  expect(frame.source.synthetic).toBe(false);
  await page.getByText('Source & instrument details', { exact: true }).click();
  await expect(page.getByTestId('source-preference-notice')).toBeVisible();
  await page.getByText('Source & instrument details', { exact: true }).click();
  await expect(page.getByTestId('synthetic-guidance')).toHaveCount(0);
  await expect(page.getByTestId('grouped-ladder').locator('tr.bid').first()).toBeInViewport();
  await expect(page.getByTestId('grouped-ladder').locator('tr.ask').last()).toBeInViewport();
  await page.getByLabel('Workspace preset').selectOption('ladder');
  await expect(page.getByTestId('grouped-ladder').locator('tr.bid').first()).toBeInViewport();
  await expect(page.getByTestId('grouped-ladder').locator('tr.ask').last()).toBeInViewport();
  await page.locator('.dom-settings > summary').click();
  const scroll = page.locator('.ladder-scroll');
  await scroll.evaluate(node => { node.scrollTop = 50; });
  await page.waitForResponse(response => response.url().endsWith('/api/v1/ladder') && response.request().method() === 'POST');
  await page.waitForResponse(response => response.url().endsWith('/api/v1/ladder') && response.request().method() === 'POST');
  expect(await scroll.evaluate(node => node.scrollTop)).toBe(50);
  await expect(page.getByLabel('Follow midpoint', { exact: true })).not.toBeChecked();
  await scroll.evaluate(node => { node.scrollTop = 0; });
  await page.waitForResponse(response => response.url().endsWith('/api/v1/ladder') && response.request().method() === 'POST');
  await page.waitForResponse(response => response.url().endsWith('/api/v1/ladder') && response.request().method() === 'POST');
  expect(await scroll.evaluate(node => node.scrollTop)).toBe(0);
  const regrouped = page.waitForResponse(response => response.url().endsWith('/api/v1/ladder') && response.request().method() === 'POST' && response.request().postDataJSON().rows === 50);
  await page.getByLabel('Ladder rows').selectOption('50');
  expect((await regrouped).ok()).toBe(true);
  expect(await scroll.evaluate(node => node.scrollTop)).toBe(0);
  await page.getByRole('button', { name: 'Recenter DOM', exact: true }).click();
  await expect(page.getByLabel('Follow midpoint', { exact: true })).toBeChecked();
  await expect(page.getByTestId('grouped-ladder').locator('tr.bid').first()).toBeInViewport();
  await expect(page.getByTestId('grouped-ladder').locator('tr.ask').last()).toBeInViewport();
  const watchlist = page.getByRole('button', { name: 'Watchlist', exact: true });
  if (await watchlist.getAttribute('aria-pressed') !== 'true') await watchlist.click();
  const instruments = page.locator('[data-instrument-id]');
  const firstPage = await instruments.evaluateAll(nodes => nodes.map(node => node.getAttribute('data-instrument-id')));
  expect(firstPage.length).toBeLessThanOrEqual(100);
  await page.getByRole('button', { name: 'Next instrument page', exact: true }).click();
  const secondPageId = await instruments.first().getAttribute('data-instrument-id');
  expect(firstPage).not.toContain(secondPageId);
  await page.getByLabel('Search instruments').fill(secondPageId!);
  await expect(instruments).toHaveCount(1);
  await expect(instruments).toHaveAttribute('data-instrument-id', secondPageId!);
  await expect(page.getByRole('navigation', { name: 'Instrument pages' })).toHaveCount(0);
  await page.getByLabel('Search instruments').fill('');
  expect(await instruments.evaluateAll(nodes => nodes.map(node => node.getAttribute('data-instrument-id')))).toEqual(firstPage);
  expect((await (await page.request.get(`/api/v1/sessions/${frame.session_id}`)).json() as Frame).selection).toEqual(frame.selection);
  await page.getByLabel('Search instruments').fill('BTC');
  await page.getByLabel('Filter venue').selectOption('hyperliquid');
  const subscribed = page.waitForResponse(response => response.url().endsWith('/api/v1/subscriptions')
    && response.request().method() === 'POST' && response.request().postDataJSON().instrument_id === 'hyperliquid:info:perpetual:BTC');
  await page.locator('[data-instrument-id="hyperliquid:info:perpetual:BTC"]').click();
  expect(((await (await subscribed).json()) as Frame).selection.instrument_id).toBe('hyperliquid:info:perpetual:BTC');
  if (await page.locator('.dom-settings').getAttribute('open') === null) {
    await page.locator('.dom-settings > summary').click();
  }
  await page.getByLabel('DOM price grouping').selectOption('100');
  await expect(page.getByTestId('grouped-ladder').locator('tr.bid').first()).toBeVisible();
  await expect(page.getByTestId('grouped-ladder').locator('tr.ask').last()).toBeVisible();
  await expect.poll(() => scroll.evaluate(node => node.scrollHeight <= node.clientHeight)).toBe(true);
  await page.getByLabel('DOM price grouping').selectOption('');
  await expect.poll(() => scroll.evaluate(node => node.scrollHeight > node.clientHeight)).toBe(true);
  await expect(page.getByLabel('Follow midpoint', { exact: true })).toBeChecked();
  await expect(page.getByTestId('grouped-ladder').locator('tr.bid').first()).toBeInViewport();
  await expect(page.getByTestId('grouped-ladder').locator('tr.ask').last()).toBeInViewport();
});
