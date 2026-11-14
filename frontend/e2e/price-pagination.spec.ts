import { expect, test } from '@playwright/test';
import type { Locator, Page } from '@playwright/test';
import type { Catalog, Frame } from '../src/api';
import type { CandlePageRequest } from '../src/candle-api';
import type { CandleHistory, PriceCandle } from '../src/candle-model';

// Test-only OHLCV transport fixtures, not market evidence. The app, native demo
// session, subscriptions, source identity and Lightweight Charts renderer are real.
// Venue requests are intercepted before they can contact any external provider.
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

const pageSize = 40;
const minute = 60_000_000_000n;
const origin = 1_735_689_600_000_000_000n;
function fixtureBar(index: number): PriceCandle {
  return {
    start_ns: (origin + BigInt(index) * minute).toString(),
    end_ns: (origin + BigInt(index + 1) * minute).toString(),
    open: '100.01', high: '102.03', low: '99.99',
    close: `100.${String(100 + index).padStart(3, '0')}`,
    volume: index % 2 ? '123.456789' : '124.456789', closed: index < 119,
  };
}
const bars = Array.from({ length: pageSize * 3 }, (_, index) => fixtureBar(index));
// A prepend changes the exact decimal scale. Subtracting these floating plotting
// coordinates used to produce a reciprocal minMove that LWC rejects as a base.
bars[40] = { ...bars[40]!, close: '100.01000000000002', volume: '123.4567890000001' };
bars[0] = { ...bars[0]!, close: '100.01000000000004', volume: '123.4567890000002' };
const fields = ['open', 'high', 'low', 'close', 'volume'] as const;

async function expectReadout(price: Locator, bar: PriceCandle, unit: string) {
  const values = price.locator('.price-chart__legend dd');
  await expect(values).toHaveCount(5);
  for (const [index, field] of fields.entries()) {
    await expect(values.nth(index)).toHaveAttribute('title', field === 'volume' ? `${bar.volume} ${unit}` : bar[field]);
  }
}

async function paint(page: Page) {
  await page.evaluate(() => {
    const { promise, resolve } = Promise.withResolvers<void>();
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
    return promise;
  });
}

interface HoverSample { x: number; y: number; bar: PriceCandle }
async function sampleViewport(page: Page, plot: Locator, price: Locator, available: readonly PriceCandle[]): Promise<HoverSample[]> {
  const box = await plot.boundingBox();
  if (!box) throw new Error('Native renderer plot must have a bounding box');
  const result: HoverSample[] = [];
  // Read the real crosshair at two distinct screen positions; no chart internals
  // or production instrumentation are used to infer its visible time range.
  for (let fraction = 0.15; fraction < 0.98; fraction += 0.05) {
    const x = box.x + (box.width - 80) * fraction;
    const y = box.y + box.height * 0.3;
    await page.mouse.move(x, y);
    await paint(page);
    const exact = await price.locator('.price-chart__legend dd').evaluateAll(nodes => nodes.map(node => node.getAttribute('title')));
    const bar = available.find(item => fields.every((field, index) => field === 'volume'
      ? exact[index]?.startsWith(`${item.volume} `) : exact[index] === item[field]));
    if (bar && bar.start_ns !== available.at(-1)!.start_ns && !result.some(sample => sample.bar.start_ns === bar.start_ns)) result.push({ x, y, bar });
    if (result.length === 2) return result;
  }
  throw new Error('Expected two distinct historical candles under the real mouse crosshair');
}

async function expectViewport(page: Page, price: Locator, samples: HoverSample[], unit: string) {
  for (const sample of samples) {
    await page.mouse.move(sample.x + 1, sample.y);
    await page.mouse.move(sample.x, sample.y);
    await expectReadout(price, sample.bar, unit);
  }
}

async function expectExactTable(price: Locator, expected: readonly PriceCandle[]) {
  const details = price.getByText('Exact candle data', { exact: true }).locator('..');
  if (await details.getAttribute('open') === null) await details.locator('summary').click();
  const newer = details.getByRole('button', { name: 'Newer rows', exact: true });
  while (await newer.isEnabled()) await newer.click();
  const actual: string[][] = [];
  for (;;) {
    const end = expected.length - actual.length;
    const expectedPage = expected.slice(Math.max(0, end - 50), end);
    await expect.poll(() => details.locator('tbody tr').evaluateAll(rows => rows.map(row => Array.from(row.querySelectorAll('th,td'), cell => cell.textContent ?? ''))))
      .toEqual(expectedPage.map(bar => [bar.start_ns, bar.end_ns, bar.open, bar.high, bar.low, bar.close, bar.volume, bar.closed ? 'Closed' : 'Forming']));
    actual.unshift(...await details.locator('tbody tr').evaluateAll(rows => rows.map(row => Array.from(row.querySelectorAll('th,td'), cell => cell.textContent ?? ''))));
    const older = details.getByRole('button', { name: 'Older rows', exact: true });
    if (!await older.isEnabled()) break;
    await older.click();
  }
  expect(actual).toEqual(expected.map(bar => [bar.start_ns, bar.end_ns, bar.open, bar.high, bar.low, bar.close, bar.volume, bar.closed ? 'Closed' : 'Forming']));
  expect(new Set(actual.map(row => row[0])).size).toBe(expected.length);
  await details.locator('summary').click();
}

test('real renderer retains exact candles and detached viewport across two precision-changing prepends and polling', async ({ page }) => {
  test.setTimeout(90_000);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const catalogResponse = await page.request.get('/api/v1/instruments');
  expect(catalogResponse.ok()).toBe(true);
  const catalog = await catalogResponse.json() as Catalog;
  const source = catalog.sources.find(item => item.mode === 'demo' && item.status === 'ready');
  const instrument = catalog.instruments.find(item => item.venue === 'binance-global' && item.market_type === 'spot' && item.base.ticker === 'BTC');
  if (!source || !instrument) throw new Error('Requires the actual native demo and its Binance BTC spot instrument');
  const displayUnit = instrument.quantity_unit === 'base' ? instrument.base.ticker : instrument.quantity_unit;
  const created = page.waitForResponse(response => response.url().endsWith('/api/v1/sessions') && response.request().method() === 'POST' && response.ok());
  await page.goto(`/?source=${encodeURIComponent(source.id)}`);
  let frame = await (await created).json() as Frame;
  await expect(page.locator('.status')).toContainText('Connected');
  if (frame.selection.instrument_id !== instrument.instrument_id) {
    const watchlist = page.locator('.native-controls summary').filter({ hasText: /^Watchlist$/ });
    if (await watchlist.locator('..').getAttribute('open') === null) await watchlist.click();
    await page.getByLabel('Search instruments', { exact: true }).fill('BTC');
    const selected = page.waitForResponse(response => response.url().endsWith('/api/v1/subscriptions') && response.ok()
      && response.request().postDataJSON().instrument_id === instrument.instrument_id);
    await page.getByTitle(instrument.instrument_id, { exact: true }).click();
    frame = await (await selected).json() as Frame;
  }
  await page.getByRole('tablist', { name: 'Chart workspace', exact: true }).getByRole('tab', { name: 'Price action', exact: true }).click();
  const price = page.getByTestId('price-chart');
  const plot = price.getByRole('group', { name: 'Price action chart', exact: true });
  await page.getByRole('combobox', { name: 'All candle intervals', exact: true }).selectOption('1m');
  // Get the session's real immutable source fence without querying an exchange.
  const observedResponse = await page.request.post('/api/v1/candles', { data: {
    session_id: frame.session_id, generation: frame.generation, instrument_ids: [instrument.instrument_id],
    source: 'session_trades', interval: '1m', limit: 300,
  } });
  expect(observedResponse.ok()).toBe(true);
  const observed = await observedResponse.json() as CandleHistory;
  expect(observed.session_id).toBe(frame.session_id);
  expect(observed.generation).toBe(frame.generation);
  expect(observed.instrument_id).toBe(instrument.instrument_id);
  expect(observed.instrument_ids).toEqual([instrument.instrument_id]);
  expect(observed.source_identity).not.toBe('');

  const gates = [Promise.withResolvers<void>(), Promise.withResolvers<void>()];
  const olderRequests: string[] = [];
  let revision = 0;
  let newest = bars.slice(80);
  const envelope = (input: CandlePageRequest, candles: PriceCandle[], more: boolean): CandleHistory => ({
    version: '1', session_id: observed.session_id, generation: observed.generation,
    source_identity: observed.source_identity, instrument_id: observed.instrument_id,
    instrument_ids: [...observed.instrument_ids], scope: 'primary', venue_label: instrument.venue,
    quote_asset_ids: [instrument.quote.asset_id], interval: input.interval, status: 'ready',
    source: input.source, venue: instrument.venue, partial: false, time_basis: 'exchange_utc',
    fetched_at_ns: (origin + 121n * minute + BigInt(revision)).toString(), supported_intervals: ['1m'],
    candles, reasons: ['TEST ONLY: controlled synthetic OHLCV transport fixture; not venue evidence'],
    volume_unit: instrument.quantity_unit, coverage_start_ns: candles[0]!.start_ns, coverage_end_ns: candles.at(-1)!.end_ns,
    series_key: `test-only:${instrument.instrument_id}:1m`,
    history: { requested_before_ns: input.before_ns ?? null, next_before_ns: more ? candles[0]!.start_ns : null,
      state: more ? 'more' : 'exhausted', pagination: 'time', max_bars: 300, update_mode: 'poll', poll_interval_ms: 5000 },
  });
  await page.route('**/api/v1/candles', async route => {
    const input = route.request().postDataJSON() as CandlePageRequest;
    if (input.source !== 'venue_ohlcv') { await route.continue(); return; }
    expect(input.session_id).toBe(observed.session_id);
    expect(input.generation).toBe(observed.generation);
    expect(input.instrument_ids).toEqual([instrument.instrument_id]);
    expect(input.interval).toBe('1m');
    let candles = newest;
    let more = true;
    if (input.before_ns !== undefined) {
      const index = input.before_ns === bars[80]!.start_ns ? 0 : input.before_ns === bars[40]!.start_ns ? 1 : -1;
      expect(index, 'Only the exact exclusive older-page boundaries are accepted').toBeGreaterThanOrEqual(0);
      olderRequests.push(input.before_ns);
      await gates[index]!.promise;
      candles = index === 0 ? bars.slice(40, 80) : bars.slice(0, 40);
      more = index === 0;
      expect(candles.every(bar => BigInt(bar.end_ns) <= BigInt(input.before_ns!))).toBe(true);
    }
    expect(candles.length).toBeLessThanOrEqual(input.limit);
    await route.fulfill({ json: envelope(input, candles, more) });
  });

  try {
    await price.getByRole('button', { name: 'Venue history', exact: true }).click();
    await expect(price).toHaveAttribute('data-presented-source', 'venue_ohlcv');
    await expect(price).toHaveAttribute('data-presented-generation', frame.generation);
    await expect(price.getByText('Current external venue history is not replay evidence.', { exact: false })).toBeVisible();
    await expectReadout(price, bars[119]!, displayUnit);
    await expectExactTable(price, bars.slice(80));

    // Real wheel navigation initiates pagination, while the response gate keeps
    // the mouse crosshair active on the old series until the prepend commits.
    for (let prepend = 0; prepend < 2; prepend++) {
      await plot.scrollIntoViewIfNeeded();
      const box = await plot.boundingBox();
      if (!box) throw new Error('Expected the live chart surface');
      await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.3);
      for (let attempt = 0; olderRequests.length === prepend && attempt < 20; attempt++) {
        await page.mouse.wheel(-120, 0);
        await paint(page);
      }
      await expect.poll(() => olderRequests.length).toBe(prepend + 1);
      await expect(price.getByRole('button', { name: 'Latest', exact: true })).toHaveAttribute('aria-pressed', 'false');
      const retained = bars.slice(prepend === 0 ? 80 : 40);
      const samples = await sampleViewport(page, plot, price, retained);
      await expectReadout(price, samples.at(-1)!.bar, displayUnit);
      gates[prepend]!.resolve();
      await expect(price.locator('.price-chart__table-nav')).toContainText(`${(prepend + 2) * pageSize} loaded bars`);
      await expectReadout(price, samples.at(-1)!.bar, displayUnit);
      await expectViewport(page, price, samples, displayUnit);
      await expect(price.locator('.price-chart__empty')).toHaveCount(0);
      await expect(plot.locator('canvas').first()).toBeVisible();
      expect(errors).toEqual([]);
      await expectExactTable(price, bars.slice(prepend === 0 ? 40 : 0));
    }
    expect(olderRequests).toEqual([bars[80]!.start_ns, bars[40]!.start_ns]);
    await expect(price.getByRole('button', { name: 'Load older history', exact: true })).toHaveCount(0);

    await plot.scrollIntoViewIfNeeded();
    const detached = await sampleViewport(page, plot, price, bars);
    const revised = { ...bars[119]!, close: '100.987', volume: '125.456789' };
    newest = [...bars.slice(80, 119), revised];
    revision = 1;
    const revisedReply = page.waitForResponse(async response => response.url().endsWith('/api/v1/candles') && response.ok()
      && response.request().postDataJSON().source === 'venue_ohlcv' && !response.request().postDataJSON().before_ns
      && (await response.json() as CandleHistory).candles.at(-1)?.close === revised.close);
    await revisedReply;
    await expectViewport(page, price, detached, displayUnit);
    await expect(price.getByRole('button', { name: 'Latest', exact: true })).toHaveAttribute('aria-pressed', 'false');
    await expectExactTable(price, [...bars.slice(0, 119), revised]);

    await plot.scrollIntoViewIfNeeded();
    const beforeRollover = await sampleViewport(page, plot, price, bars);
    const closed = { ...revised, closed: true };
    const rollover = { ...fixtureBar(120), close: '100.988' };
    newest = [...bars.slice(81, 119), closed, rollover];
    revision = 2;
    await expect(price.locator('.price-chart__table-nav')).toContainText('121 loaded bars', { timeout: 15_000 });
    await expectViewport(page, price, beforeRollover, displayUnit);
    await expect(price.getByRole('button', { name: 'Latest', exact: true })).toHaveAttribute('aria-pressed', 'false');
    await expectExactTable(price, [...bars.slice(0, 119), closed, rollover]);
    await expect(page.locator('.status')).toContainText('Connected');
    await expect(price).toHaveAttribute('data-presented-generation', frame.generation);
    await expect(plot.locator('canvas').first()).toBeVisible();
    await expect(price.locator('.price-chart__empty')).toHaveCount(0);
    expect(errors).toEqual([]);
  } finally {
    gates.forEach(gate => gate.resolve());
    if (!page.isClosed()) await page.unroute('**/api/v1/candles');
  }
});
