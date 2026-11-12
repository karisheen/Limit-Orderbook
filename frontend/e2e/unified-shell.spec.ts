import { expect, test } from '@playwright/test';
import type { Locator, Page } from '@playwright/test';
import type { Catalog, Frame } from '../src/api';
import type { CandleHistory } from '../src/candle-model';

// Real catalog, replay and native presentation responses only. These scenarios
// assert that reference studies are consumers of the document's native session,
// not an independently connected exchange terminal.
async function openDemo(page: Page) {
  const response = await page.request.get('/api/v1/instruments');
  expect(response.ok()).toBe(true);
  const catalog = await response.json() as Catalog;
  const source = catalog.sources.find(item => item.mode === 'demo' && item.status === 'ready');
  if (!source) throw new Error('Unified terminal coverage requires the real ready synthetic demo source');
  const created = page.waitForResponse(reply => reply.url().endsWith('/api/v1/sessions') && reply.request().method() === 'POST');
  await page.goto(`/?source=${encodeURIComponent(source.id)}`);
  const result = await created;
  expect(result.ok()).toBe(true);
  const frame = await result.json() as Frame;
  await expect(page.locator('.status')).toContainText('Connected');
  await page.getByRole('tablist', { name: 'Chart workspace', exact: true }).getByRole('tab', { name: 'Footprint', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Footprint', exact: true })).toBeVisible();
  return { frame, catalog };
}

async function choose(group: Locator, name: string) {
  const option = group.getByRole('radio', { name, exact: true });
  await option.click();
  await expect(option).toHaveAttribute('aria-checked', 'true');
}

test('reference study modes and native charts share one session without exchange sockets', async ({ page }) => {
  const sessions: string[] = [];
  const sockets: string[] = [];
  const consumers: { path: string; session_id: string; generation: string }[] = [];
  page.on('websocket', socket => sockets.push(socket.url()));
  page.on('request', request => {
    const path = new URL(request.url()).pathname;
    if (request.method() !== 'POST') return;
    if (path === '/api/v1/sessions') sessions.push(request.url());
    if (['/api/v1/market-data', '/api/v1/candles', '/api/v1/visualization', '/api/v1/costs'].includes(path)) {
      const input = request.postDataJSON() as { session_id: string; generation: string };
      consumers.push({ path, session_id: input.session_id, generation: input.generation });
    }
  });
  const presented = page.waitForResponse(response => response.url().endsWith('/api/v1/market-data') && response.ok());
  const { frame } = await openDemo(page);
  const market = await (await presented).json() as { session_id: string; generation: string; books: unknown[] };
  expect(market.session_id).toBe(frame.session_id);
  expect(market.generation).toBe(frame.generation);
  expect(market.books.length).toBeGreaterThan(0);
  await expect(page.getByRole('button', { name: /Workbench|Contribute/ })).toHaveCount(0);
  await expect(page.getByRole('radiogroup', { name: 'Market', exact: true }).getByRole('radio')).toHaveText(['Perp', 'Spot']);

  const footprint = page.getByRole('region', { name: 'Footprint', exact: true });
  for (const mode of ['Delta', 'Volume', 'Bid × Ask']) {
    await choose(footprint.getByRole('radiogroup', { name: 'Footprint cell mode', exact: true }), mode);
  }
  await choose(page.getByRole('radiogroup', { name: 'Candle interval', exact: true }), '5m');
  await expect(footprint.locator('.panel-subtitle')).toContainText('· 5M ·');
  await footprint.getByRole('button', { name: /^Indicators/ }).click();
  const vwap = page.getByRole('menuitemcheckbox', { name: /^Session VWAP/ });
  const before = await vwap.getAttribute('aria-checked');
  await vwap.click();
  await expect(vwap).toHaveAttribute('aria-checked', before === 'true' ? 'false' : 'true');
  await footprint.getByRole('button', { name: /^Indicators/ }).click();

  const cvd = page.getByRole('region', { name: 'Cumulative Delta', exact: true });
  await choose(cvd.getByRole('radiogroup', { name: 'Bar size', exact: true }), '1m');
  const price = cvd.getByRole('button', { name: 'Price', exact: true });
  const priceEnabled = (await price.getAttribute('class'))?.includes('is-active');
  await price.click();
  if (priceEnabled) await expect(price).not.toHaveClass(/is-active/);
  else await expect(price).toHaveClass(/is-active/);

  const volume = page.getByRole('region', { name: 'Volume Z-Score', exact: true });
  await choose(volume.getByRole('radiogroup', { name: 'View', exact: true }), 'Bars');
  await choose(volume.getByRole('radiogroup', { name: 'Lookback window', exact: true }), '100');
  await expect(volume.locator('.panel-subtitle')).toContainText('prior 100');
  await choose(volume.getByRole('radiogroup', { name: 'View', exact: true }), 'Z');

  const workspace = page.getByRole('radiogroup', { name: 'Workspace', exact: true });
  await choose(workspace, 'Liquidity');
  await expect(page.getByRole('region', { name: 'Market Depth', exact: true })).toBeVisible();
  await expect(cvd).toHaveCount(0);
  await choose(workspace, 'Volatility');
  const volatility = page.getByRole('region', { name: 'Realized Volatility', exact: true });
  await choose(volatility.getByRole('radiogroup', { name: 'Return interval', exact: true }), '1m');
  await choose(volatility.getByRole('radiogroup', { name: 'Lookback window', exact: true }), '120');
  await expect(volatility.locator('.panel-subtitle')).toContainText('120 × 1m');
  await choose(workspace, 'Order Flow');
  await expect(cvd).toBeVisible();
  await expect(volatility).toHaveCount(0);

  const charts = page.getByRole('tablist', { name: 'Chart workspace', exact: true });
  const candles = page.waitForResponse(response => response.url().endsWith('/api/v1/candles') && response.ok()
    && response.request().postDataJSON().interval === '5m' && response.request().postDataJSON().source === 'session_trades');
  await charts.getByRole('tab', { name: 'Price action', exact: true }).click();
  const candleHTTP = await candles;
  const candleData = await candleHTTP.json() as CandleHistory;
  expect(candleData.session_id).toBe(frame.session_id);
  expect(candleData.generation).toBe(frame.generation);
  expect(candleData.instrument_ids).toEqual(candleHTTP.request().postDataJSON().instrument_ids);
  expect(candleData.instrument_ids[0]).toBe(frame.selection.instrument_id);
  expect(candleData.time_basis).toBe('receive_utc');
  await expect(page.getByTestId('price-chart')).toBeVisible();
  await expect(page.getByTestId('price-chart')).toHaveAttribute('data-presented-interval', '5m');
  await expect(page.getByTestId('price-chart')).toHaveAttribute('data-presented-source', 'session_trades');
  await page.getByRole('button', { name: 'Candle timeframe', exact: true }).click();
  await page.keyboard.press('End');
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('price-chart')).toHaveAttribute('data-presented-interval', '1M');
  await expect(page.getByRole('combobox', { name: 'All candle intervals', exact: true })).toHaveValue('1M');
  const visualization = page.waitForResponse(response => response.url().endsWith('/api/v1/visualization') && response.ok());
  await charts.getByRole('tab', { name: 'Liquidity & flow', exact: true }).click();
  expect((await visualization).request().postDataJSON().session_id).toBe(frame.session_id);
  await page.getByTestId('chart-pane-heatmap').locator('.m4-chart-plot').scrollIntoViewIfNeeded();
  await expect(page.getByTestId('chart-pane-heatmap').getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', /.+/);
  await charts.getByRole('tab', { name: 'Footprint', exact: true }).click();
  await expect(footprint).toBeVisible();
  await expect(footprint.locator('.panel-subtitle')).toContainText('· 1M ·');
  await expect(footprint.getByRole('radio', { name: 'Bid × Ask', exact: true })).toHaveAttribute('aria-checked', 'true');
  await page.getByRole('tablist', { name: 'Research pane', exact: true }).getByRole('tab', { name: 'Costs & proposed risk', exact: true }).click();
  await page.getByLabel('Order size', { exact: true }).fill('0.01');
  const costs = page.waitForResponse(response => response.url().endsWith('/api/v1/costs') && response.request().method() === 'POST');
  await page.getByTestId('estimate').click();
  const estimate = await costs;
  expect(estimate.ok()).toBe(true);
  expect(estimate.request().postDataJSON().session_id).toBe(frame.session_id);
  await page.getByRole('button', { name: 'Collapse workflow drawer', exact: true }).click();

  const active = await page.request.get(`/api/v1/sessions/${frame.session_id}`);
  expect(active.ok()).toBe(true);
  const unchanged = await active.json() as Frame;
  expect(unchanged.generation).toBe(frame.generation);
  expect(unchanged.selection.instrument_id).toBe(frame.selection.instrument_id);
  expect(unchanged.replay?.cursor).toEqual(frame.replay?.cursor);
  expect(sessions).toHaveLength(1);
  expect(consumers.some(item => item.path === '/api/v1/market-data')).toBe(true);
  expect(consumers.some(item => item.path === '/api/v1/candles')).toBe(true);
  expect(consumers.some(item => item.path === '/api/v1/visualization')).toBe(true);
  expect(consumers.some(item => item.path === '/api/v1/costs')).toBe(true);
  expect(consumers.every(item => item.session_id === frame.session_id && item.generation === frame.generation)).toBe(true);
  expect(sockets).toHaveLength(1);
  const socket = new URL(sockets[0]!);
  expect(socket.host).toBe(new URL(page.url()).host);
  expect(socket.pathname).toBe('/api/v1/stream');
  expect(socket.searchParams.get('session_id')).toBe(frame.session_id);
});

for (const width of [1440, 390]) {
  test(`reference picker remains keyboard usable at ${width}px without a second session`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    let creations = 0;
    page.on('request', request => {
      if (request.url().endsWith('/api/v1/sessions') && request.method() === 'POST') creations++;
    });
    const { frame, catalog } = await openDemo(page);
    const current = catalog.instruments.find(item => item.instrument_id === frame.selection.instrument_id);
    if (!current) throw new Error('The native selection must exist in its source catalog');
    const target = catalog.instruments.find(item => item.market_type === current.market_type && item.base.asset_id !== current.base.asset_id);
    if (!target) throw new Error('Synthetic source must expose another crypto base for keyboard selection');
    const picker = page.getByRole('button', { name: /^Instrument:/ });
    await picker.focus();
    await page.keyboard.press('Enter');
    const search = page.getByRole('combobox', { name: 'Search instrument', exact: true });
    await expect(search).toBeFocused();
    await search.fill(target.base.ticker);
    const selected = page.waitForResponse(response => response.url().endsWith('/api/v1/subscriptions') && response.ok());
    await page.keyboard.press('Enter');
    const next = await (await selected).json() as Frame;
    expect(next.session_id).toBe(frame.session_id);
    const identity = catalog.instruments.find(item => item.instrument_id === next.selection.instrument_id);
    expect(identity?.base.asset_id).toBe(target.base.asset_id);
    await expect(picker).toContainText(target.base.ticker);
    await expect(page.getByRole('dialog', { name: 'Instrument', exact: true })).toHaveCount(0);
    await expect(picker).toBeFocused();
    await picker.click();
    await page.keyboard.press('Escape');
    await expect(picker).toHaveAttribute('aria-expanded', 'false');
    await expect(picker).toBeFocused();
    const venue = page.getByRole('button', { name: /^Venue:/ });
    await venue.click();
    const aggregate = page.getByRole('listbox', { name: 'Venue', exact: true }).getByRole('option', { name: /^All (spot|perps)/ });
    await expect(aggregate).toContainText('Indicative aggregate');
    await aggregate.click();
    await expect(venue).toContainText(/All (spot|perps)/);
    const native = await page.request.get(`/api/v1/sessions/${frame.session_id}`);
    expect(native.ok()).toBe(true);
    expect((await native.json() as Frame).selection.instrument_id).toBe(next.selection.instrument_id);
    expect(creations).toBe(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    if (width === 1440) {
      const separator = page.getByRole('separator', { name: 'Resize sidebar width', exact: true });
      const sidebar = page.locator('.terminal > .workspace > .sidebar');
      const before = await sidebar.evaluate(node => node.getBoundingClientRect().width);
      await separator.focus();
      await page.keyboard.press('ArrowLeft');
      await expect(separator).toBeFocused();
      await expect.poll(() => sidebar.evaluate(node => node.getBoundingClientRect().width)).toBe(before + 10);
    }
  });
}
