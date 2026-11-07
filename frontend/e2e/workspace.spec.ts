import { expect, test } from '@playwright/test';
import type { Locator, Page, Request, Response } from '@playwright/test';
import type { Catalog, Costs, Frame, GroupedLadder, ProposalRisk, VisualGrid } from '../src/api';
import { plotArea } from '../src/market-charts-model';

// These scenarios use the actual server's explicitly synthetic demo and replay
// controls. Transport fault tests delay/block requests, never substitute frames
// or implement browser-side economics.
const preferenceKey = 'limit-orderbook.workspace.v1';
const primary = (page: Page) => page.getByTestId('chart-pane-heatmap');
const secondary = (page: Page) => page.getByTestId('chart-pane-flow');
const nextResponse = (page: Page, endpoint: string, matches: (body: Record<string, unknown>) => boolean = () => true, rows = 128) =>
  page.waitForResponse(response => {
    if (!response.url().endsWith(`/api/v1/${endpoint}`) || response.request().method() !== 'POST') return false;
    const request = response.request().postDataJSON() as Record<string, unknown>;
    // The default liquidity pane uses 128 rows; the simultaneous footprint pane
    // uses 32. Never let a secondary response stand in for primary canvas evidence.
    return (endpoint !== 'visualization' || request.rows === rows) && matches(request);
  });

async function body<T>(response: Promise<Response>): Promise<T> {
  const result = await response;
  expect(result.ok(), `${result.status()} ${result.url()}: ${await result.text()}`).toBe(true);
  return await result.json() as T;
}

async function openChartSettings(page: Page, pane = primary(page)) {
  const toggle = pane.locator('.chart-pane-actions').getByRole('button', { name: /settings$/ });
  if (await pane.locator('.m4-chart-inspector').isVisible()) await toggle.click();
  if (!await pane.locator('.chart-pane-settings').isVisible()) await toggle.click();
  await expect(pane.locator('.chart-pane-settings')).toBeVisible();
}

async function openChartInspector(page: Page, pane = primary(page)) {
  if (!await pane.locator('.m4-chart-inspector').isVisible()) {
    await openChartSettings(page, pane);
    await pane.getByRole('button', { name: /Inspector$/ }).click();
  }
  await expect(pane.locator('.m4-chart-inspector')).toBeVisible();
}

async function recenterChart(page: Page, pane = primary(page)) {
  await openChartSettings(page, pane);
  await pane.getByTestId('view-recenter').click();
  await expect(pane.locator('.chart-pane-settings')).toHaveCount(0);
  await expect(pane.locator('.chart-pane-actions').getByRole('button', { name: /settings$/ })).toBeFocused();
}

async function openPane(page: Page, name: 'Watchlist' | 'Costs / analytics') {
  const toggle = page.getByRole('button', { name, exact: true });
  if (await toggle.getAttribute('aria-pressed') !== 'true') await toggle.click();
}

async function openDisclosure(details: Locator) {
  if (await details.getAttribute('open') === null) await details.locator(':scope > summary').click();
}

async function openDemo(page: Page) {
  let latest: Frame | null = null;
  page.on('websocket', socket => {
    if (!socket.url().includes('/api/v1/stream?')) return;
    socket.on('framereceived', event => {
      const message = JSON.parse(event.payload.toString()) as Frame;
      if (message.type === 'frame') latest = message;
    });
  });
  const availableResponse = await page.request.get('/api/v1/instruments');
  expect(availableResponse.ok()).toBe(true);
  const available = await availableResponse.json() as Catalog;
  const demo = available.sources.find(source => source.mode === 'demo' && source.status === 'ready');
  if (!demo) throw new Error('Actual native catalog must expose an explicitly synthetic demo source');
  const created = nextResponse(page, 'sessions');
  const catalogResponse = page.waitForResponse(response => response.url().endsWith('/api/v1/instruments') && response.request().method() === 'GET');
  await page.goto(`/?source=${encodeURIComponent(demo.id)}`);
  const frame = await body<Frame>(created);
  const catalog = await body<Catalog>(catalogResponse);
  expect(frame.source.mode).toBe('demo');
  expect(frame.source.synthetic).toBe(true);
  expect(frame.replay).not.toBeNull();
  await expect(page.locator('.status')).toContainText(/\bConnected$/i);
  await expect(page.locator('.source-banner')).toContainText('SYNTHETIC DEMO');
  // These regression scenarios exercise the retained order-flow tools, not
  // the independent price-action workspace that now opens by default.
  if ((page.viewportSize()?.width ?? 1280) <= 965) {
    await page.getByRole('tablist', { name: 'Market panes', exact: true }).getByRole('tab', { name: 'Liquidity', exact: true }).click();
    await expect(primary(page)).toBeVisible();
  } else {
    await page.getByRole('tablist', { name: 'Chart workspace', exact: true }).getByRole('tab', { name: 'Order flow', exact: true }).click();
    await expect(page.getByTestId('grouped-ladder').locator('tbody tr').first()).toBeVisible();
  }
  return { frame, catalog, latestFrame: () => latest ?? frame };
}

async function evidence<T>(locator: Locator): Promise<T> {
  return JSON.parse(await locator.textContent() ?? 'null') as T;
}
async function ladderEvidence(page: Page): Promise<GroupedLadder> {
  const settings = page.locator('.dom-settings');
  await openDisclosure(settings);
  await openDisclosure(settings.locator('.dom-scope'));
  await openDisclosure(settings.locator('.evidence'));
  const value = await evidence<GroupedLadder>(settings.locator('.evidence pre'));
  await settings.locator(':scope > summary').click();
  return value;
}

async function assertLadder(page: Page, expected: GroupedLadder) {
  await expect.poll(() => ladderEvidence(page)).toEqual(expected);
  await openDisclosure(page.locator('.dom-settings'));
  const displayed = [...expected.rows.filter(row => row.side === 'ask').reverse(), ...expected.rows.filter(row => row.side === 'bid')];
  const rows = page.getByTestId('grouped-ladder').locator('tbody tr[data-side]');
  const quote = await page.getByLabel('DOM quantity units').inputValue() === 'quote';
  const cumulative = await page.getByLabel('Cumulative depth', { exact: true }).isChecked();
  await page.locator('.dom-settings > summary').click();
  await expect(rows).toHaveCount(displayed.length);
  for (let index = 0; index < displayed.length; index++) {
    const level = displayed[index];
    await expect(rows.nth(index)).toHaveAttribute('data-side', level.side);
    expect(await rows.nth(index).locator('.number').evaluateAll(nodes => nodes.map(node => node.getAttribute('title'))))
      .toEqual([level.price, quote ? level.quote_notional : level.base_quantity, ...(cumulative ? [quote ? level.cumulative_quote : level.cumulative_base] : [])]);
  }
}

async function replay(page: Page, action: 'play' | 'pause' | 'step') {
  const response = nextResponse(page, 'replay', request => request.action === action);
  await page.getByTestId(`replay-${action}`).click();
  const frame = await body<Frame>(response);
  if (action !== 'play') await expect(page.locator('.cursor')).toContainText(`${frame.replay?.cursor.delivery_index} /`);
  return frame;
}

async function selectEther(page: Page, catalog: Catalog) {
  const instrument = catalog.instruments.find(item => item.base.ticker === 'ETH' && item.venue === 'binance-global' && item.market_type === 'spot');
  expect(instrument, 'Actual demo catalog must expose the native ETH spot cohort').toBeDefined();
  if (!instrument) throw new Error('Demo ETH spot instrument is unavailable');
  await openPane(page, 'Watchlist');
  await page.getByLabel('Search instruments').fill('ETH');
  const selected = nextResponse(page, 'subscriptions', request => request.instrument_id === instrument.instrument_id);
  await page.locator('.sidebar').getByTitle(instrument.instrument_id, { exact: true }).click();
  const frame = await body<Frame>(selected);
  await expect(page.locator('.sidebar').getByTitle(instrument.instrument_id, { exact: true })).toHaveAttribute('aria-pressed', 'true');
  expect(frame.selection.instrument_id).toBe(instrument.instrument_id);
  return frame;
}

test('latest visibility in a batched notification resumes actual canvas painting', async ({ page }) => {
  // Real Chromium produced this hidden/visible batch during a rapid viewport
  // transition. Control only notification batching, never native market data.
  await page.addInitScript(() => {
    const NativeObserver = window.IntersectionObserver;
    window.IntersectionObserver = class extends NativeObserver {
      constructor(callback: IntersectionObserverCallback, options?: IntersectionObserverInit) {
        super((entries, observer) => {
          const latest = entries.at(-1);
          if (latest?.isIntersecting && latest.target.classList.contains('m4-chart-plot')) {
            const hidden: IntersectionObserverEntry = {
              target: latest.target, time: latest.time, isIntersecting: false, intersectionRatio: 0,
              boundingClientRect: latest.boundingClientRect, intersectionRect: latest.intersectionRect,
              rootBounds: latest.rootBounds,
            };
            callback([hidden, ...entries], observer);
          } else callback(entries, observer);
        }, options);
      }
    };
  });
  const firstResponse = nextResponse(page, 'visualization');
  const { frame, catalog } = await openDemo(page);
  const first = await body<VisualGrid>(firstResponse);
  const chart = primary(page).getByTestId('market-charts');
  const canvas = primary(page).getByTestId('liquidity-canvas');
  await expect(chart).toHaveAttribute('data-painted-view-id', first.view_id);
  const before = await canvas.evaluate(node => (node as HTMLCanvasElement).toDataURL());
  const next = nextResponse(page, 'visualization', request => request.generation !== frame.generation);
  const selected = await selectEther(page, catalog);
  const changed = await body<VisualGrid>(next);
  expect(changed.instrument_id).toBe(selected.selection.instrument_id);
  await expect(chart).toHaveAttribute('data-painted-view-id', changed.view_id);
  expect(await canvas.evaluate(node => (node as HTMLCanvasElement).toDataURL())).not.toBe(before);
});

test('one-sided native liquidity occupies the whole time bucket without false temporal gaps', async ({ page }) => {
  await openDemo(page);
  const response = nextResponse(page, 'visualization', request => request.recenter === true);
  await recenterChart(page);
  const view = await body<VisualGrid>(response);
  await expect(primary(page).getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', view.view_id);
  const canvas = primary(page).getByTestId('liquidity-canvas');
  const bounds = await canvas.boundingBox();
  if (!bounds) throw new Error('Native liquidity canvas is not visible');
  const area = plotArea(Math.floor(bounds.width), Math.floor(bounds.height));
  const samples = await canvas.evaluate((node, { view, area }) => {
    const canvas = node as HTMLCanvasElement;
    const context = canvas.getContext('2d')!;
    const ratio = canvas.width / Math.floor(canvas.getBoundingClientRect().width);
    const pixel = (x: number, y: number) => [...context.getImageData(Math.floor(x * ratio), Math.floor(y * ratio), 1, 1).data];
    const start = BigInt(view.start_ns);
    const span = Number(BigInt(view.end_ns) - start);
    const sides = [0, 1].map(side => {
      const column = view.columns.find(column => column.eligible
        && BigInt(column.time_ns) >= start && BigInt(column.end_ns) <= BigInt(view.end_ns)
        && column.cells.some(cell => cell[side] != null && cell[side] !== '0' && cell[1 - side] === '0'));
      if (!column) throw new Error(`Native fixture has no covered one-sided liquidity for side ${side}`);
      const row = column.cells.findIndex(cell => cell[side] != null && cell[side] !== '0' && cell[1 - side] === '0');
      const left = area.x + Number(BigInt(column.time_ns) - start) / span * area.width;
      const width = Number(BigInt(column.end_ns) - BigInt(column.time_ns)) / span * area.width;
      const y = area.y + area.height * (1 - (row + 0.5) / view.rows);
      return { side, firstHalf: pixel(left + width * 0.25, y), secondHalf: pixel(left + width * 0.75, y) };
    });
    return { background: pixel(0, 0), sides };
  }, { view, area });
  for (const sample of samples.sides) {
    expect(sample.firstHalf, `side ${sample.side} must visibly contain native liquidity`).not.toEqual(samples.background);
    expect(sample.secondHalf, `side ${sample.side} must cover the same observed time bucket`).toEqual(sample.firstHalf);
  }
});

test('exact instrument selection, grouped cumulative depth and pinned time range use server evidence', async ({ page }) => {
  const { catalog, latestFrame } = await openDemo(page);
  const frame = await selectEther(page, catalog);
  await openChartSettings(page);
  await openDisclosure(page.locator('.dom-settings'));
  // Advance the actual replay beyond the requested window so a 30s range
  // cannot accidentally pass as the startup window clamped to the clock origin.
  const speed = nextResponse(page, 'replay', request => request.action === 'speed' && request.speed === '8');
  await page.getByLabel('Replay speed').selectOption('8');
  await body<Frame>(speed);
  await replay(page, 'play');
  await expect.poll(() => BigInt(latestFrame().book?.asof.receive_mono_ns ?? '0') > 35_000_000_000n).toBe(true);
  await replay(page, 'pause');
  const initial = await body<VisualGrid>(nextResponse(page, 'visualization', request => request.generation === frame.generation));
  expect(initial.instrument_id).toBe(frame.selection.instrument_id);
  expect(initial.step_options.length).toBeGreaterThan(0);
  const step = initial.step_options.at(-1)!;
  const regrouped = nextResponse(page, 'visualization', request => request.price_step === step);
  await primary(page).getByLabel('Price aggregation').selectOption(step);
  const grid = await body<VisualGrid>(regrouped);
  const ungrouped = await ladderEvidence(page);
  expect(ungrouped.step).toBe(catalog.instruments.find(item => item.instrument_id === frame.selection.instrument_id)!.tick_size);
  await openDisclosure(page.locator('.dom-settings'));
  const ladderResponse = nextResponse(page, 'ladder', request => request.price_step === step);
  await page.getByLabel('DOM price grouping').selectOption(step);
  await page.getByLabel('Cumulative depth', { exact: true }).check();
  const ladder = await body<GroupedLadder>(ladderResponse);
  expect(grid.price_step).toBe(step);
  expect(grid.instrument_id).toBe(frame.selection.instrument_id);
  expect(ladder.step).toBe(step);
  expect(ladder.instrument_id).toBe(frame.selection.instrument_id);
  expect(ladder.rows.length).toBeGreaterThan(0);
  await assertLadder(page, ladder);

  const bucketResponse = nextResponse(page, 'visualization', request => request.bucket_ns === '5000000000');
  await primary(page).getByLabel('Time bucket').selectOption('5000000000');
  expect((await body<VisualGrid>(bucketResponse)).bucket_ns).toBe('5000000000');
  const rangeResponse = nextResponse(page, 'visualization', request => typeof request.end_ns === 'string' && typeof request.start_ns === 'string'
    && BigInt(request.start_ns) === (BigInt(request.end_ns) > 30_000_000_000n ? BigInt(request.end_ns) - 30_000_000_000n : 0n));
  await primary(page).getByLabel('Chart range').selectOption('30');
  const ranged = await body<VisualGrid>(rangeResponse);
  expect(BigInt(ranged.end_ns) - BigInt(ranged.start_ns)).toBeLessThanOrEqual(30_000_000_000n);
  expect(BigInt(ranged.end_ns)).toBeLessThanOrEqual(BigInt(ranged.now_ns));
  const pinnedResponse = nextResponse(page, 'visualization', request => request.start_ns === ranged.start_ns && request.end_ns === ranged.end_ns);
  await primary(page).getByLabel('Follow time', { exact: true }).uncheck();
  const pinned = await body<VisualGrid>(pinnedResponse);
  const before = await page.locator('.cursor').textContent();
  await replay(page, 'step');
  await expect(page.locator('.cursor')).not.toHaveText(before ?? '');
  const afterStep = await body<VisualGrid>(nextResponse(page, 'visualization', request => request.start_ns === pinned.start_ns && request.end_ns === pinned.end_ns));
  expect(afterStep.start_ns).toBe(pinned.start_ns);
  expect(afterStep.end_ns).toBe(pinned.end_ns);
  const cursorBeforePan = await page.locator('.cursor').textContent();
  const chart = primary(page).getByTestId('market-charts');
  await expect(chart).toHaveAttribute('data-start-ns', afterStep.start_ns);
  await primary(page).getByRole('button', { name: 'Close Liquidity settings', exact: true }).click();
  const plot = primary(page).getByRole('group', { name: 'Resting liquidity interactive plot', exact: true });
  await plot.scrollIntoViewIfNeeded();
  const bounds = await plot.boundingBox();
  expect(bounds).not.toBeNull();
  const pointerPan = nextResponse(page, 'visualization', request => typeof request.start_ns === 'string'
    && typeof request.end_ns === 'string' && BigInt(request.start_ns) < BigInt(afterStep.start_ns)
    && BigInt(request.end_ns) < BigInt(afterStep.end_ns));
  await page.mouse.move(bounds!.x + bounds!.width / 2, bounds!.y + bounds!.height / 2);
  await page.mouse.down();
  await page.mouse.move(bounds!.x + bounds!.width / 2 + 20, bounds!.y + bounds!.height / 2, { steps: 4 });
  await page.mouse.up();
  const dragged = await body<VisualGrid>(pointerPan);
  await expect(chart).toHaveAttribute('data-start-ns', dragged.start_ns);
  await expect(chart).toHaveAttribute('data-end-ns', dragged.end_ns);
  expect(BigInt(dragged.end_ns)).toBeLessThan(BigInt(afterStep.end_ns));
  await openChartSettings(page);
  await expect(primary(page).getByLabel('Follow time', { exact: true })).not.toBeChecked();
  const keyboardPan = nextResponse(page, 'visualization', request => typeof request.end_ns === 'string'
    && BigInt(request.end_ns) < BigInt(dragged.end_ns));
  await plot.press('ArrowLeft');
  const shifted = await body<VisualGrid>(keyboardPan);
  await expect(chart).toHaveAttribute('data-start-ns', shifted.start_ns);
  await expect(chart).toHaveAttribute('data-end-ns', shifted.end_ns);
  expect(BigInt(shifted.end_ns)).toBeLessThan(BigInt(dragged.end_ns));
  const pricePan = nextResponse(page, 'visualization', request => request.price_offset_rows === 4);
  await plot.press('ArrowUp');
  const repriced = await body<VisualGrid>(pricePan);
  expect(repriced.price_min).not.toBe(shifted.price_min);
  expect(repriced.price_step).toBe(shifted.price_step);
  await expect(chart).toHaveAttribute('data-price-min', repriced.price_min);
  await expect(page.locator('.cursor')).toHaveText(cursorBeforePan ?? '');
  await expect(page.getByTestId('replay-pause')).toBeDisabled();
  const centeredResponse = nextResponse(page, 'visualization', request => request.recenter === true && request.price_offset_rows === 0);
  await recenterChart(page);
  const centered = await body<VisualGrid>(centeredResponse);
  expect(centered.start_ns).toBe(repriced.start_ns);
  expect(centered.end_ns).toBe(repriced.end_ns);
  await expect(chart).toHaveAttribute('data-painted-view-id', centered.view_id);
  await openChartSettings(page);
  await expect(primary(page).getByLabel('Follow time', { exact: true })).not.toBeChecked();
  const followingResponse = nextResponse(page, 'visualization', request => typeof request.start_ns === 'string'
    && typeof request.end_ns === 'string' && BigInt(request.end_ns) - BigInt(request.start_ns) === BigInt(centered.end_ns) - BigInt(centered.start_ns));
  await primary(page).getByRole('button', { name: 'Follow latest', exact: true }).click();
  const following = await body<VisualGrid>(followingResponse);
  await expect(chart).toHaveAttribute('data-painted-view-id', following.view_id);
  await plot.press('Escape');
  await openChartInspector(page);
  await openDisclosure(primary(page).locator('.chart-pane-diagnostics'));
  await page.getByTestId('grouped-ladder').locator('tbody tr').first().hover();
  const linked = await primary(page).locator('.linked-readout').textContent();
  const linkedTime = BigInt(/source monotonic ns (\d+)/.exec(linked ?? '')![1]!);
  expect(linkedTime).toBeGreaterThanOrEqual(BigInt(following.start_ns));
  expect(linkedTime).toBeLessThan(BigInt(following.end_ns));
});

test('linked DOM price outside the chart viewport never selects a different price row', async ({ page }) => {
  await openDemo(page);
  await body<VisualGrid>(nextResponse(page, 'visualization'));
  await openChartInspector(page);
  await openDisclosure(primary(page).locator('.m4-chart-guide'));
  const panned = nextResponse(page, 'visualization', request => request.price_offset_rows === 128);
  for (let index = 0; index < 16; index++) await primary(page).getByRole('button', { name: 'Pan prices higher', exact: true }).click();
  const grid = await body<VisualGrid>(panned);
  const bid = page.getByTestId('grouped-ladder').locator('tr.bid').first();
  const price = await bid.locator('td').nth(1).locator('.number').getAttribute('title');
  expect(price).not.toBeNull();
  expect(grid.prices).not.toContain(price);
  await primary(page).locator('.m4-chart-readout summary').first().click();
  await bid.hover();
  await expect(primary(page).getByRole('tooltip')).toContainText(price!);
  await expect(primary(page).getByRole('combobox', { name: 'Exact visible price row', exact: true })).toHaveValue('');
  await expect(primary(page).locator('.m4-chart-readout table').first().locator('tbody tr')).toHaveCount(0);
});

test('independent chart and DOM freezes hold evidence while replay continues', async ({ page }) => {
  const { latestFrame } = await openDemo(page);
  await body<VisualGrid>(nextResponse(page, 'visualization'));
  await primary(page).getByTestId('view-freeze').click();
  await page.getByTestId('dom-freeze').click();
  await expect(primary(page).getByTestId('view-freeze')).toHaveAttribute('aria-pressed', 'true');
  const frozenDOM = await ladderEvidence(page);
  const chart = primary(page).getByTestId('market-charts');
  await expect(chart).toBeVisible();
  const frozenView = await chart.getAttribute('data-view-id');
  expect(frozenView).toBeTruthy();
  await expect(chart).toHaveAttribute('data-painted-view-id', frozenView!);
  const frozenPixels = await primary(page).getByTestId('liquidity-canvas').screenshot();
  const before = await page.locator('.cursor').textContent();
  await replay(page, 'play');
  await expect(page.getByTestId('replay-pause')).toBeEnabled();
  await expect(page.locator('.cursor')).not.toHaveText(before ?? '');
  // Observe native selected-book progress across multiple polling intervals,
  // not merely a heartbeat or an unrelated instrument's next delivery.
  expect(frozenDOM.asof).not.toBeNull();
  await expect.poll(() => BigInt(latestFrame().book?.asof.receive_mono_ns ?? '0')
    > BigInt(frozenDOM.asof!.receive_mono_ns) + 2_000_000_000n).toBe(true);
  const paused = await replay(page, 'pause');
  expect(paused.status).toBe('paused');
  await expect(primary(page).getByTestId('view-freeze')).toHaveAttribute('aria-pressed', 'true');
  expect(await ladderEvidence(page)).toEqual(frozenDOM);
  await expect(chart).toHaveAttribute('data-view-id', frozenView!);
  await expect(chart).toHaveAttribute('data-painted-view-id', frozenView!);
  expect(await primary(page).getByTestId('liquidity-canvas').screenshot()).toEqual(frozenPixels);
  const resumed = nextResponse(page, 'visualization');
  await primary(page).getByTestId('view-freeze').click();
  const refreshed = await body<VisualGrid>(resumed);
  expect(refreshed.generation).toBe(paused.generation);
  await expect(page.getByTestId('replay-play')).toBeEnabled();
  await expect(page.getByTestId('replay-pause')).toBeDisabled();
});

test('DOM-only freeze keeps exact cumulative snapshot while chart receives advancing replay evidence', async ({ page }) => {
  const { latestFrame } = await openDemo(page);
  const initialGrid = await body<VisualGrid>(nextResponse(page, 'visualization'));
  await page.getByTestId('dom-freeze').click();
  await expect(page.getByTestId('dom-freeze')).toHaveAttribute('aria-pressed', 'true');
  const frozen = await ladderEvidence(page);
  const text = await page.getByTestId('grouped-ladder').textContent();
  await replay(page, 'play');
  let advanced = initialGrid;
  await expect.poll(async () => {
    advanced = await body<VisualGrid>(nextResponse(page, 'visualization'));
    return BigInt(advanced.now_ns) > BigInt(initialGrid.now_ns);
  }).toBe(true);
  expect(frozen.asof).not.toBeNull();
  await expect.poll(() => latestFrame().book?.asof.receive_mono_ns).not.toBe(frozen.asof!.receive_mono_ns);
  const paused = await replay(page, 'pause');
  expect(advanced.instrument_id).toBe(frozen.instrument_id);
  expect(await ladderEvidence(page)).toEqual(frozen);
  await expect(page.getByTestId('grouped-ladder')).toHaveText(text ?? '');
  await expect(primary(page).getByTestId('view-freeze')).toHaveAttribute('aria-pressed', 'false');
  const unfrozen = nextResponse(page, 'ladder');
  await page.getByTestId('dom-freeze').click();
  const current = await body<GroupedLadder>(unfrozen);
  expect(current.generation).toBe(paused.generation);
  expect(current.asof).not.toEqual(frozen.asof);
  await assertLadder(page, current);
});

test('independent chart settings and saved resized workspace survive reload without session ownership', async ({ page }) => {
  const { frame: first, catalog } = await openDemo(page);
  const selected = await selectEther(page, catalog);
  const modes = primary(page).getByLabel('Chart mode', { exact: true });
  for (const mode of ['candles', 'footprint', 'profile', 'cvd', 'ofi', 'heatmap']) {
    await modes.selectOption(mode);
    await expect(modes).toHaveValue(mode);
    await expect(primary(page).getByTestId('market-charts')).toHaveAttribute('data-mode', mode);
  }
  await modes.selectOption('footprint');
  await secondary(page).getByLabel('Chart mode', { exact: true }).selectOption('candles');
  await openDisclosure(page.locator('.workspace-configuration'));
  await page.getByLabel('Workspace preset').selectOption('analysis');
  await page.locator('.workspace-configuration > summary').click();
  await openPane(page, 'Watchlist');
  await openPane(page, 'Costs / analytics');
  const separators = [
    ['Watchlist width', 'ArrowRight'], ['DOM width', 'ArrowLeft'],
    ['Research pane height', 'ArrowUp'], ['Chart split', 'ArrowUp'], ['DOM and tape split', 'ArrowUp'],
  ] as const;
  const dimensions: [string, string][] = [];
  for (const [name, key] of separators) {
    const divider = page.getByRole('separator', { name, exact: true });
    const before = await divider.getAttribute('aria-valuenow');
    await divider.press(key);
    await expect(divider).not.toHaveAttribute('aria-valuenow', before!);
    dimensions.push([name, (await divider.getAttribute('aria-valuenow'))!]);
  }
  await openDisclosure(page.locator('.dom-settings'));
  await openChartSettings(page);
  await openChartSettings(page, secondary(page));
  await page.getByLabel('Ladder rows').selectOption('25');
  await primary(page).getByLabel('Time bucket').selectOption('5000000000');
  await primary(page).getByLabel('Chart range').selectOption('60');
  await primary(page).getByLabel('Trades', { exact: true }).uncheck();
  await secondary(page).getByLabel('Time bucket').selectOption('10000000000');
  await secondary(page).getByLabel('Chart range').selectOption('300');
  const expected = {
    mode: 'footprint', preset: 'analysis', leftOpen: true, domRows: 25, bucket: '5000000000',
    rangeSeconds: 60, showTrades: false, instrument: selected.selection.instrument_id,
    secondary: { mode: 'candles', bucket: '10000000000', rangeSeconds: 300 },
  };
  await expect.poll(() => page.evaluate(key => JSON.parse(localStorage.getItem(key) ?? '{}') as Record<string, unknown>, preferenceKey)).toMatchObject(expected);
  await openDisclosure(page.locator('.workspace-configuration'));
  await openDisclosure(page.locator('.workspace-slots'));
  await page.getByLabel('Workspace slot').selectOption('0');
  await page.getByRole('button', { name: 'Save workspace', exact: true }).click();
  await page.getByLabel('Workspace preset').selectOption('ladder');
  await page.locator('.workspace-configuration > summary').click();
  await primary(page).getByLabel('Chart range').selectOption('30');
  await openDisclosure(page.locator('.workspace-configuration'));
  await page.getByRole('button', { name: 'Load workspace', exact: true }).click();
  await expect(page.getByLabel('Workspace preset')).toHaveValue('analysis');
  await page.locator('.workspace-configuration > summary').click();
  await expect(primary(page).getByLabel('Chart range')).toHaveValue('60');
  const saved = await page.evaluate(() => Object.values(localStorage).join('\n'));
  expect(saved).not.toContain(first.session_id);
  expect(saved).not.toContain('session_id');
  const created = nextResponse(page, 'sessions');
  await page.reload();
  const replacement = await body<Frame>(created);
  expect(replacement.session_id).not.toBe(first.session_id);
  expect(replacement.selection).toEqual(selected.selection);
  expect(replacement.source.id).toBe(first.source.id);
  await expect(page.locator('.status')).toContainText(/\bConnected$/i);
  await openDisclosure(page.locator('.workspace-configuration'));
  await openDisclosure(page.locator('.workspace-slots'));
  await page.getByLabel('Workspace preset').selectOption('balanced');
  await page.getByRole('button', { name: 'Load workspace', exact: true }).click();
  await expect(page.getByLabel('Workspace preset')).toHaveValue('analysis');
  await page.locator('.workspace-configuration > summary').click();
  await expect(modes).toHaveValue('footprint');
  await expect(secondary(page).getByLabel('Chart mode', { exact: true })).toHaveValue('candles');
  for (const [name, value] of dimensions) {
    await expect(page.getByRole('separator', { name, exact: true })).toHaveAttribute('aria-valuenow', value);
  }
  await openDisclosure(page.locator('.dom-settings'));
  await openChartSettings(page);
  await openChartSettings(page, secondary(page));
  await expect(page.getByLabel('Ladder rows')).toHaveValue('25');
  await expect(primary(page).getByLabel('Time bucket')).toHaveValue('5000000000');
  await expect(primary(page).getByLabel('Chart range')).toHaveValue('60');
  await expect(primary(page).getByLabel('Trades', { exact: true })).not.toBeChecked();
  await expect(secondary(page).getByLabel('Time bucket')).toHaveValue('10000000000');
  await expect(secondary(page).getByLabel('Chart range')).toHaveValue('300');
});

for (const width of [1440, 1280]) {
  test(`streaming updates do not move workspace panes at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 1440 ? 900 : 800 });
    await openDemo(page);
    await openPane(page, 'Watchlist');
    await openPane(page, 'Costs / analytics');
    const initial = await body<VisualGrid>(nextResponse(page, 'visualization'));
    await expect(primary(page).getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', /.+/);
    const selectors = ['.workspace', '.sidebar', '[data-testid="chart-pane-heatmap"]', '[data-testid="chart-pane-flow"]', '[data-pane="dom"]', '[data-pane="tape"]', '.research-pane'];
    const baseline = await Promise.all(selectors.map(selector => page.locator(selector).boundingBox()));
    expect(baseline.every(box => box !== null && box.width > 0 && box.height > 0)).toBe(true);
    await replay(page, 'play');
    let latest = initial;
    for (let sample = 0; sample < 3; sample++) {
      latest = await body<VisualGrid>(nextResponse(page, 'visualization'));
      await expect(primary(page).getByTestId('market-charts')).toHaveAttribute('data-end-ns', latest.end_ns);
      const current = await Promise.all(selectors.map(selector => page.locator(selector).boundingBox()));
      for (let pane = 0; pane < selectors.length; pane++) {
        expect(current[pane], selectors[pane]).not.toBeNull();
        for (const dimension of ['x', 'y', 'width', 'height'] as const) {
          expect(Math.abs(current[pane]![dimension] - baseline[pane]![dimension]), `${selectors[pane]} ${dimension}`).toBeLessThanOrEqual(1);
        }
      }
    }
    expect(BigInt(latest.now_ns)).toBeGreaterThan(BigInt(initial.now_ns));
    await replay(page, 'pause');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}

async function assertRisk(page: Page, response: ProposalRisk) {
  const panel = page.getByRole('region', { name: 'Proposed trade risk', exact: true });
  const ledger = panel.locator('details').filter({ has: page.getByText('Exact inputs, source identity and server evidence', { exact: true }) });
  await expect.poll(async () => (await evidence<{ response: ProposalRisk }>(ledger.locator('pre'))).response).toEqual(response);
  await expect(panel.locator('.result-heading strong')).toHaveText(response.eligible ? 'Scenario available · non-guaranteed' : 'Scenario unavailable');
  await expect(panel.locator('.estimate-grid .number')).toHaveText([
    response.base_quantity, response.suggested_base_quantity, response.gross_loss, response.fee_cost, response.estimated_loss,
  ].map(value => value ?? 'Unavailable'));
  if (response.reasons.length) await expect(panel.getByLabel('Proposal risk reasons').locator('li')).toHaveText(response.reasons);
  for (const assumption of response.assumptions) await expect(panel.getByText(assumption, { exact: true })).toBeVisible();
}

test('proposal risk preserves exact inputs and actual results, unknown fees and HTTP errors; cost comparison remains usable', async ({ page }) => {
  const { catalog } = await openDemo(page);
  await openPane(page, 'Costs / analytics');
  const frame = await selectEther(page, catalog);
  expect(frame.book?.asks.length).toBeGreaterThan(0);
  expect(frame.book?.bids.length).toBeGreaterThan(0);
  const nativeEntry = frame.book!.asks[0].price;
  const entry = `${nativeEntry}${nativeEntry.includes('.') ? '' : '.'}000000000000000000`;
  const stop = frame.book!.bids[0].price;
  const feeDate = new Date(Number(BigInt(frame.book!.asof.receive_wall_ns) / 1_000_000n)).toISOString().slice(0, 10);
  const panel = page.getByRole('region', { name: 'Proposed trade risk', exact: true });
  await page.getByLabel('Proposal entry price').fill(entry);
  await page.getByLabel('Proposal stop price').fill(stop);
  await page.getByLabel('Proposal base quantity').fill('0.010000000000000000');
  await page.getByLabel('Proposal fee assumption').selectOption('assumed');
  await page.getByLabel('Proposal fee basis points').fill('12.500000000000000001');
  await page.getByLabel('Proposal fee date').fill(feeDate);
  await page.getByLabel('Proposal fee provenance').fill('Browser scenario explicit user assumption');
  const calculation = nextResponse(page, 'proposal-risk');
  await page.getByTestId('proposal-risk-calculate').click();
  const response = await calculation;
  expect(response.request().postDataJSON()).toEqual({
    session_id: frame.session_id, generation: frame.generation, side: 'buy', entry_price: entry, stop_price: stop,
    base_quantity: '0.010000000000000000', fee_bps: '12.500000000000000001', fee_date: feeDate, fee_provenance: 'Browser scenario explicit user assumption',
  });
  const result = await body<ProposalRisk>(Promise.resolve(response));
  expect(result.instrument_id).toBe(frame.selection.instrument_id);
  expect(result.eligible).toBe(true);
  expect(result.estimated_loss).not.toBeNull();
  await assertRisk(page, result);

  await page.getByLabel('Proposal size convention').selectOption('risk');
  await page.getByLabel('Proposal risk budget').fill('10.000000000000000001');
  await page.getByLabel('Proposal fee assumption').selectOption('unknown');
  await expect(panel).toContainText('Inputs changed · stale result');
  const unknownResponse = nextResponse(page, 'proposal-risk');
  await page.getByTestId('proposal-risk-calculate').click();
  const unknownHTTP = await unknownResponse;
  const unknownInput = unknownHTTP.request().postDataJSON() as Record<string, unknown>;
  expect(unknownInput.risk_budget).toBe('10.000000000000000001');
  for (const omitted of ['base_quantity', 'fee_bps', 'fee_date', 'fee_provenance']) expect(unknownInput).not.toHaveProperty(omitted);
  const unknown = await body<ProposalRisk>(Promise.resolve(unknownHTTP));
  expect(unknown.eligible).toBe(false);
  expect(unknown.suggested_base_quantity).toBeNull();
  expect(unknown.estimated_loss).toBeNull();
  expect(unknown.reasons.length).toBeGreaterThan(0);
  await assertRisk(page, unknown);

  await page.getByLabel('Proposal entry price').fill('not-a-decimal');
  const failed = nextResponse(page, 'proposal-risk');
  await page.getByTestId('proposal-risk-calculate').click();
  const error = await failed;
  expect(error.status()).toBe(422);
  const detail = (await error.json() as { detail: unknown }).detail;
  await expect(panel.getByRole('alert')).toContainText(`HTTP ${error.status()}`);
  await expect(panel.getByRole('alert')).toContainText(typeof detail === 'string' ? detail : JSON.stringify(detail));
  await expect(panel).toContainText('Latest calculation failed');
  await expect(page.getByTestId('proposal-risk-calculate')).toBeEnabled();

  await page.getByLabel('Order size', { exact: true }).fill('0.01');
  await page.getByLabel('Fee assumption', { exact: true }).selectOption('assumed');
  await page.getByLabel('Fee basis points', { exact: true }).fill('12.5');
  const costsResponse = nextResponse(page, 'costs');
  await page.getByTestId('estimate').click();
  const costs = await body<Costs>(costsResponse);
  expect(costs.estimate).not.toBeNull();
  expect(costs.matrix.length).toBeGreaterThan(0);
  const costsPanel = page.locator('.costs-panel').filter({ has: page.getByRole('heading', { name: 'Proposed order & comparable venue costs', exact: true }) });
  const executionLedger = costsPanel.locator('details.evidence').filter({ has: page.getByText('Exact execution ledger, fee assumptions, consumed levels & provenance', { exact: true }) });
  await expect.poll(() => evidence(executionLedger.locator('pre'))).toEqual(costs.estimate);
  await expect(costsPanel.locator('.pair-table tbody tr')).toHaveCount(costs.matrix.length);
  for (let index = 0; index < costs.matrix.length; index++) {
    const pair = costs.matrix[index];
    const row = costsPanel.locator('.pair-table tbody tr').nth(index);
    await expect(row.locator('th')).toHaveAttribute('title', `${pair.buy_instrument_id} → ${pair.sell_instrument_id}`);
    expect(await row.locator('.number').evaluateAll(nodes => nodes.map(node => node.getAttribute('title')))).toEqual([
      pair.feasible_base_quantity, pair.gross_reference, pair.depth_slippage, pair.buy_fee, pair.sell_fee,
      pair.fx_cost, pair.rebalance_cost, pair.latency_stress, pair.net, pair.net_bps,
    ].map(value => value ?? '—'));
  }
});

test('corrupt and out-of-bound saved preferences recover without resurrecting session ownership', async ({ page }) => {
  await page.addInitScript(({ key }) => {
    localStorage.setItem(key, JSON.stringify({
      version: 1, session_id: 'must-never-be-reused', source: { mode: 'invalid', id: 'invalid' }, instrument: 42,
      leftWidth: -1000, rightWidth: 1e9, bottomHeight: 'invalid', mode: 'invalid', preset: 'invalid',
      bucket: 'NaN', priceStep: '-1', rangeSeconds: -1, range: { start_ns: '99', end_ns: '1' },
      follow: 'false', paused: 'true', domFrozen: 'true', domRows: 1e9, priceOffset: 'invalid', contrast: 'invalid',
    }));
  }, { key: preferenceKey });
  const { frame } = await openDemo(page);
  expect(frame.session_id).not.toBe('must-never-be-reused');
  await openPane(page, 'Watchlist');
  await openPane(page, 'Costs / analytics');
  await openChartSettings(page);
  await openDisclosure(page.locator('.dom-settings'));
  await expect(page.getByRole('separator', { name: 'Watchlist width' })).toHaveAttribute('aria-valuenow', '180');
  await expect(page.getByRole('separator', { name: 'DOM width' })).toHaveAttribute('aria-valuenow', '500');
  await expect(page.getByLabel('Ladder rows')).toHaveValue('100');
  await expect(primary(page).getByLabel('Price aggregation')).toHaveValue('');
  await expect(primary(page).getByLabel('Time bucket')).toHaveValue('1000000000');
  await expect(primary(page).getByLabel('Follow time', { exact: true })).toBeChecked();
  await expect(primary(page).getByTestId('view-freeze')).toHaveAttribute('aria-pressed', 'false');
  await expect(page.getByTestId('dom-freeze')).toHaveAttribute('aria-pressed', 'false');
  await expect(primary(page).getByLabel('Chart mode', { exact: true })).toHaveValue('heatmap');
  await expect.poll(() => page.evaluate(key => JSON.parse(localStorage.getItem(key) ?? '{}') as Record<string, unknown>, preferenceKey)).toMatchObject({ range: null, priceOffset: 0, contrast: 1 });
  expect(await page.evaluate(key => localStorage.getItem(key), preferenceKey)).not.toContain('must-never-be-reused');
});

test('malformed JSON preferences are replaced by a usable default workspace', async ({ page }) => {
  await page.addInitScript(key => { localStorage.setItem(key, '{"version":1,'); }, preferenceKey);
  await openDemo(page);
  await openPane(page, 'Watchlist');
  await openPane(page, 'Costs / analytics');
  await expect.poll(async () => {
    const stored = await page.evaluate(key => localStorage.getItem(key), preferenceKey);
    try { return JSON.parse(stored ?? 'null') as unknown; }
    catch { return null; }
  }).toMatchObject({ version: 1, preset: 'balanced', mode: 'heatmap', paused: false, domFrozen: false });
  await expect(page.getByTestId('proposal-risk-calculate')).toBeEnabled();
});

test('workspace keyboard shortcuts ignore editable fields and act outside them', async ({ page }) => {
  await openDemo(page);
  await body<VisualGrid>(nextResponse(page, 'visualization'));
  await openPane(page, 'Watchlist');
  await openPane(page, 'Costs / analytics');
  await openChartSettings(page);
  await primary(page).getByLabel('Follow time', { exact: true }).uncheck();
  await primary(page).getByLabel('Chart mode', { exact: true }).selectOption('cvd');
  await primary(page).getByTestId('view-freeze').click();
  await page.getByTestId('dom-freeze').click();
  for (const input of [page.getByLabel('Search instruments'), page.getByLabel('Order size', { exact: true })]) {
    await input.fill('');
    await input.pressSequentially('r f F d / 123456');
    await expect(input).toHaveValue('r f F d / 123456');
    await expect(input).toBeFocused();
    await expect(primary(page).getByTestId('view-freeze')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByTestId('dom-freeze')).toHaveAttribute('aria-pressed', 'true');
    await expect(primary(page).getByLabel('Follow time', { exact: true })).not.toBeChecked();
    await expect(primary(page).getByLabel('Chart mode', { exact: true })).toHaveValue('cvd');
  }
  await primary(page).locator('.m4-chart-plot').focus();
  await page.keyboard.press('/');
  await expect(page.getByLabel('Search instruments')).toBeFocused();
  await page.getByLabel('Search instruments').fill('');
  await primary(page).locator('.m4-chart-plot').focus();
  await page.keyboard.press('Space');
  await expect(primary(page).getByTestId('view-freeze')).toHaveAttribute('aria-pressed', 'false');
  await page.keyboard.press('d');
  await expect(page.getByTestId('dom-freeze')).toHaveAttribute('aria-pressed', 'false');
  await page.keyboard.press('2');
  await expect(primary(page).getByLabel('Chart mode', { exact: true })).toHaveValue('candles');
  await page.keyboard.press('r');
  await expect(primary(page).getByLabel('Follow time', { exact: true })).not.toBeChecked();
  await page.keyboard.press('f');
  await expect(primary(page).getByLabel('Follow time', { exact: true })).toBeChecked();
  await expect(page.getByTestId('replay-play')).toBeEnabled();
  await expect(page.getByTestId('replay-pause')).toBeDisabled();
});

test('compact charts pin quality while overlays preserve plot geometry and footprint detail', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  const { latestFrame } = await openDemo(page);
  for (const pane of [primary(page), secondary(page)]) {
    await expect(pane.getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', /.+/);
    const plot = pane.locator('.m4-chart-plot');
    const initial = await plot.boundingBox();
    expect(initial!.height).toBeGreaterThan(60);
    const assertPinned = async () => {
      await expect(pane.locator('.m4-chart-quality')).toBeInViewport();
      expect(await pane.getByTestId('market-charts').evaluate(element => element.scrollHeight <= element.clientHeight)).toBe(true);
      const current = await plot.boundingBox();
      expect(current!.height).toBe(initial!.height);
    };
    await assertPinned();
    await openChartSettings(page, pane);
    await assertPinned();
    await openChartInspector(page, pane);
    await expect(pane.locator('.m4-chart-inspector')).toBeVisible();
    await openDisclosure(pane.locator('.m4-chart-readout'));
    await expect(pane.getByRole('combobox', { name: 'Exact visible price row', exact: true })).toBeVisible();
    await assertPinned();
    await pane.getByRole('button', { name: 'Close Chart Inspector', exact: true }).click();
  }
  await secondary(page).getByRole('button', { name: /^Maximize / }).click();
  await openChartSettings(page, secondary(page));
  await secondary(page).getByLabel('Time bucket').selectOption('10000000000');
  // Startup source transitions invalidate the first 10s footprint bucket.
  // Deliver a clean native bucket, still within the requested 30s prefix.
  const speed = nextResponse(page, 'replay', request => request.action === 'speed' && request.speed === '4');
  await page.getByLabel('Replay speed').selectOption('4');
  await body<Frame>(speed);
  await replay(page, 'play');
  await expect.poll(() => BigInt(latestFrame().book?.asof.receive_mono_ns ?? '0') >= 11_000_000_000n).toBe(true);
  await replay(page, 'pause');
  const detailedResponse = nextResponse(page, 'visualization', request => request.bucket_ns === '10000000000'
    && typeof request.start_ns === 'string' && typeof request.end_ns === 'string'
    && BigInt(request.start_ns) === (BigInt(request.end_ns) > 30_000_000_000n ? BigInt(request.end_ns) - 30_000_000_000n : 0n), 32);
  await secondary(page).getByLabel('Chart range').selectOption('30');
  await expect(secondary(page).getByLabel('Chart range')).toHaveValue('30');
  const detailed = await body<VisualGrid>(detailedResponse);
  expect(detailed.start_ns).toBe('0');
  expect(BigInt(detailed.end_ns)).toBeLessThan(30_000_000_000n);
  expect(BigInt(detailed.end_ns)).toBeLessThanOrEqual(BigInt(detailed.now_ns));
  expect(detailed.footprints.length).toBeGreaterThan(0);
  await expect(secondary(page).getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', detailed.view_id);
  await secondary(page).getByRole('button', { name: 'Close Order flow settings', exact: true }).click();
  const enlarged = await secondary(page).locator('.m4-chart-plot').boundingBox();
  const area = plotArea(enlarged!.width, enlarged!.height);
  const chart = secondary(page).getByTestId('market-charts');
  const rowFirst = Number(await chart.getAttribute('data-row-first'));
  const rowCount = Number(await chart.getAttribute('data-row-count'));
  expect(rowFirst).toBeGreaterThanOrEqual(0);
  expect(rowCount).toBeGreaterThan(0);
  expect(rowFirst + rowCount).toBeLessThanOrEqual(detailed.rows);
  expect(area.height / rowCount).toBeGreaterThanOrEqual(13);
  expect(Number(BigInt(detailed.bucket_ns)) / Number(BigInt(detailed.end_ns) - BigInt(detailed.start_ns)) * area.width).toBeGreaterThanOrEqual(125);
  await openChartInspector(page, secondary(page));
  await expect(secondary(page).getByRole('combobox', { name: 'Exact visible price row', exact: true })).toBeVisible();
  await openDisclosure(secondary(page).locator('.m4-chart-readout'));
  const visiblePrices = detailed.prices.slice(rowFirst, rowFirst + rowCount);
  const priceSelector = secondary(page).getByRole('combobox', { name: 'Exact visible price row', exact: true });
  await expect(priceSelector.locator('option:not([disabled])')).toHaveText(visiblePrices);
  const footprint = detailed.footprints.find(cell => visiblePrices.includes(cell.price)
    && detailed.columns.some(column => column.time_ns === cell.time_ns));
  expect(footprint, 'Native footprint must have an inspectable sampled time and price').toBeDefined();
  await secondary(page).getByRole('combobox', { name: 'Exact sampled time', exact: true }).selectOption(footprint!.time_ns);
  await priceSelector.selectOption(footprint!.price);
  const exactRow = secondary(page).locator('.m4-chart-readout table').first().getByRole('row')
    .filter({ has: page.getByRole('rowheader', { name: footprint!.price, exact: true }) });
  await expect(exactRow.locator('td')).toHaveText([footprint!.sell_base, footprint!.buy_base, footprint!.unknown_base]);
});

for (const width of [320, 390]) {
  test(`narrow pane selection preserves native views and settings without overflow at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    const { catalog, latestFrame } = await openDemo(page);
    const initial = latestFrame();
    // Both coarse buckets need native source history before pinning or freezing.
    const seek = await page.request.post('/api/v1/replay', {
      data: { session_id: initial.session_id, generation: initial.generation, action: 'seek', delivery_index: '2000' },
    });
    expect(seek.ok()).toBe(true);
    const advanced = await seek.json() as Frame;
    expect(advanced.replay!.cursor.delivery_index).toBe('2000');
    const populatedResponse = page.waitForResponse(async response => {
      if (!response.url().endsWith('/api/v1/visualization') || response.request().method() !== 'POST' || !response.ok()) return false;
      const request = response.request().postDataJSON() as Record<string, unknown>;
      if (request.rows !== 128 || request.generation !== advanced.generation) return false;
      const view = await response.json() as VisualGrid;
      return view.columns.some(column => column.eligible);
    });
    await expect.poll(() => ({
      generation: latestFrame().generation,
      cursor: latestFrame().replay?.cursor.delivery_index,
    })).toEqual({ generation: advanced.generation, cursor: '2000' });
    await expect(page.locator('.replay-controls .cursor')).toContainText('2000 /');
    const populated = await body<VisualGrid>(populatedResponse);
    const tabs = page.getByRole('tablist', { name: 'Market panes', exact: true });
    const chart = primary(page).getByTestId('market-charts');
    await expect(chart).toHaveAttribute('data-painted-view-id', populated.view_id);
    await primary(page).getByLabel('Time bucket', { exact: true }).selectOption('5000000000');
    await openChartSettings(page);
    await primary(page).getByLabel('Chart range', { exact: true }).selectOption('30');
    const pinnedResponse = nextResponse(page, 'visualization', request => request.bucket_ns === '5000000000' && typeof request.start_ns === 'string' && typeof request.end_ns === 'string');
    await primary(page).getByLabel('Follow time', { exact: true }).uncheck();
    const pinned = await body<VisualGrid>(pinnedResponse);
    expect(pinned.generation).toBe(advanced.generation);
    expect(pinned.columns.some(column => column.eligible)).toBe(true);
    await expect(chart).toHaveAttribute('data-painted-view-id', pinned.view_id);
    await primary(page).getByRole('button', { name: 'Close Liquidity settings', exact: true }).click();
    await primary(page).getByTestId('view-freeze').click();
    await expect(primary(page).getByTestId('view-freeze')).toHaveAttribute('aria-pressed', 'true');
    const held = await chart.getAttribute('data-view-id');
    const range = [await chart.getAttribute('data-start-ns'), await chart.getAttribute('data-end-ns')];
    const cursor = await page.locator('.replay-controls .cursor').textContent();
    await primary(page).getByRole('button', { name: 'Link Liquidity cursor', exact: true }).click();

    await tabs.getByRole('tab', { name: 'Flow', exact: true }).click();
    await expect(primary(page)).toBeHidden();
    await expect(secondary(page)).toBeVisible();
    await secondary(page).getByLabel('Chart mode', { exact: true }).selectOption('candles');
    await openChartSettings(page, secondary(page));
    await secondary(page).getByLabel('Chart range', { exact: true }).selectOption('30');
    await secondary(page).getByRole('button', { name: 'Close Order flow settings', exact: true }).click();
    const flowResponse = nextResponse(page, 'visualization', request => request.generation === advanced.generation && request.bucket_ns === '10000000000');
    await secondary(page).getByLabel('Time bucket', { exact: true }).selectOption('10000000000');
    const flow = await body<VisualGrid>(flowResponse);
    expect(flow.columns.some(column => column.eligible)).toBe(true);
    await expect(secondary(page).getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', flow.view_id);
    await tabs.getByRole('tab', { name: 'Book', exact: true }).click();
    await expect(secondary(page)).toBeHidden();
    await expect(page.getByTestId('grouped-ladder').locator('tbody tr').first()).toBeVisible();
    await page.getByTestId('dom-freeze').click();
    await openDisclosure(page.locator('.dom-settings'));
    await page.getByLabel('DOM quantity units', { exact: true }).selectOption('quote');
    await page.getByLabel('Follow midpoint', { exact: true }).uncheck();
    await page.locator('.dom-settings > summary').click();
    await tabs.getByRole('tab', { name: 'Tape', exact: true }).click();
    await expect(page.locator('[data-pane="dom"]')).toBeHidden();
    await expect(page.locator('.tape-panel .tape-scroll')).toBeVisible();
    await page.getByRole('button', { name: 'Freeze tape', exact: true }).click();
    await openDisclosure(page.locator('.tape-settings'));
    await page.getByLabel('Tape side', { exact: true }).selectOption('buy');
    await page.locator('.tape-settings > summary').click();
    await expect(page.locator('.tape-panel .freshness')).toBeVisible();

    await tabs.getByRole('tab', { name: 'Liquidity', exact: true }).click();
    await expect(chart).toHaveAttribute('data-painted-view-id', held!);
    expect([await chart.getAttribute('data-start-ns'), await chart.getAttribute('data-end-ns')]).toEqual(range);
    await expect(primary(page).getByLabel('Time bucket', { exact: true })).toHaveValue('5000000000');
    await expect(primary(page).getByTestId('view-freeze')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('.replay-controls .cursor')).toHaveText(cursor!);
    await expect(primary(page).getByRole('button', { name: 'Link Liquidity cursor', exact: true })).toHaveAttribute('aria-pressed', 'false');
    for (const [id, name] of [['heatmap', 'Liquidity'], ['flow', 'Flow'], ['dom', 'Book'], ['tape', 'Tape']] as const) {
      await tabs.getByRole('tab', { name, exact: true }).click();
      const pane = page.locator(`#market-panel-${id}`);
      await expect(pane).toBeVisible();
      await expect(page.locator('[id^="market-panel-"]:visible')).toHaveCount(1);
      const box = await pane.boundingBox();
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(width);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    }
    await expect(page.getByRole('button', { name: 'Resume tape', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await openDisclosure(page.locator('.tape-settings'));
    await expect(page.getByLabel('Tape side', { exact: true })).toHaveValue('buy');
    await page.locator('.tape-settings > summary').click();
    await tabs.getByRole('tab', { name: 'Book', exact: true }).click();
    await expect(page.getByTestId('dom-freeze')).toHaveAttribute('aria-pressed', 'true');
    await openDisclosure(page.locator('.dom-settings'));
    await expect(page.getByLabel('DOM quantity units', { exact: true })).toHaveValue('quote');
    await expect(page.getByLabel('Follow midpoint', { exact: true })).not.toBeChecked();
    await page.locator('.dom-settings > summary').click();

    await openDisclosure(page.locator('.workspace-configuration'));
    await expect(page.getByLabel('Data source', { exact: true })).toBeVisible();
    await expect(page.getByLabel('Workspace preset', { exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.locator('.workspace-configuration > summary').click();
    await openPane(page, 'Costs / analytics');
    await page.getByRole('tablist', { name: 'Research pane' }).getByRole('tab', { name: 'Core analytics', exact: true }).click();
    await expect(page.locator('.analytics-panel .metrics-table tbody tr').first()).toBeVisible();
    const height = page.getByRole('separator', { name: 'Research pane height', exact: true });
    await height.press('Home');
    await expect(height).toHaveAttribute('aria-valuenow', '170');
    await selectEther(page, catalog);
    await expect(page.locator('.selected-identity strong')).toContainText('ETH');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

    await page.setViewportSize({ width: 1280, height: 900 });
    await expect(tabs).toBeHidden();
    await page.getByRole('tablist', { name: 'Chart workspace', exact: true }).getByRole('tab', { name: 'Order flow', exact: true }).click();
    for (const id of ['heatmap', 'flow', 'dom', 'tape']) await expect(page.locator(`#market-panel-${id}`)).toBeVisible();
    await expect(secondary(page).getByLabel('Chart mode', { exact: true })).toHaveValue('candles');
    await expect(secondary(page).getByLabel('Time bucket', { exact: true })).toHaveValue('10000000000');
    await expect(page.getByRole('separator', { name: 'Chart split', exact: true })).toBeVisible();
  });
}

test('narrow inactive chart and book queries stop while replay continues', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const { latestFrame } = await openDemo(page);
  await expect(primary(page).getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', /.+/);
  const tabs = page.getByRole('tablist', { name: 'Market panes', exact: true });
  await tabs.getByRole('tab', { name: 'Book', exact: true }).click();
  await expect(page.getByTestId('grouped-ladder').locator('tbody tr').first()).toBeVisible();
  await tabs.getByRole('tab', { name: 'Tape', exact: true }).click();
  const queries: string[] = [];
  const observe = (request: Request) => {
    const path = new URL(request.url()).pathname;
    if (path === '/api/v1/visualization' || path === '/api/v1/ladder') queries.push(path);
  };
  page.on('request', observe);
  try {
    await replay(page, 'play');
    const before = BigInt(latestFrame().replay!.cursor.delivery_index);
    await page.waitForTimeout(2300);
    expect(queries).toEqual([]);
    expect(BigInt(latestFrame().replay!.cursor.delivery_index)).toBeGreaterThan(before);
    await replay(page, 'pause');
    const resumed = nextResponse(page, 'visualization');
    await tabs.getByRole('tab', { name: 'Liquidity', exact: true }).click();
    const view = await body<VisualGrid>(resumed);
    await expect(primary(page).getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', view.view_id);
  } finally {
    page.off('request', observe);
  }
});

test('narrow active panes keep loading, empty, stale and disconnected evidence visible', async ({ page, context }) => {
  await page.setViewportSize({ width: 320, height: 844 });
  await openDemo(page);
  await expect(primary(page).getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', /.+/);
  const tabs = page.getByRole('tablist', { name: 'Market panes', exact: true });
  const network = await context.newCDPSession(page);
  await network.send('Network.enable');
  try {
    await network.send('Network.emulateNetworkConditions', { offline: false, latency: 1500, downloadThroughput: -1, uploadThroughput: -1 });
    await tabs.getByRole('tab', { name: 'Flow', exact: true }).click();
    await expect(secondary(page).locator('.m4-chart-empty')).toContainText('Loading');
    await expect(secondary(page).getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', /.+/);
    await network.send('Network.emulateNetworkConditions', { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
    await tabs.getByRole('tab', { name: 'Book', exact: true }).click();
    await expect(page.getByTestId('grouped-ladder').locator('tbody tr').first()).toBeVisible();
    await network.send('Network.setBlockedURLs', { urls: ['*/api/v1/ladder', '*/api/v1/visualization'] });
    await expect(page.locator('.dom-status [role="alert"]')).toContainText('Stale');
    await expect(page.locator('.status')).toContainText(/\bConnected$/i);
    await tabs.getByRole('tab', { name: 'Liquidity', exact: true }).click();
    await primary(page).getByLabel('Time bucket', { exact: true }).selectOption('5000000000');
    await expect(primary(page).getByRole('alert')).toContainText('stale');
    await expect(primary(page).getByRole('alert')).toBeVisible();
    await expect(page.locator('.status')).toContainText(/\bConnected$/i);
    await network.send('Network.setBlockedURLs', { urls: [] });
    const resetting = nextResponse(page, 'replay', request => request.action === 'reset');
    await page.getByTestId('replay-reset').click();
    const empty = await body<Frame>(resetting);
    expect(empty.book).toBeNull();
    for (const [id, name] of [['heatmap', 'Liquidity'], ['flow', 'Flow'], ['dom', 'Book'], ['tape', 'Tape']] as const) {
      await tabs.getByRole('tab', { name, exact: true }).click();
      const pane = page.locator(`#market-panel-${id}`);
      await expect(pane.locator(id === 'heatmap' || id === 'flow' ? '.m4-chart-empty' : '.empty')).toBeVisible();
      await expect(pane.locator('.freshness')).toBeVisible();
    }

    await context.setOffline(true);
    await page.getByRole('button', { name: 'Reconnect terminal', exact: true }).click();
    await expect(page.locator('.status')).not.toContainText(/\bConnected$/i);
    await expect(page.locator('.source-readiness')).toContainText('Unavailable');
    for (const [id, name] of [['heatmap', 'Liquidity'], ['flow', 'Flow'], ['dom', 'Book'], ['tape', 'Tape']] as const) {
      await tabs.getByRole('tab', { name, exact: true }).click();
      const pane = page.locator(`#market-panel-${id}`);
      await expect(pane.locator('.freshness--unavailable')).toBeVisible();
      if (id === 'heatmap' || id === 'flow') await expect(pane.getByRole('alert')).toContainText('No current stream evidence');
      else if (id === 'tape') await expect(pane.locator('.empty')).toBeVisible();
      else await expect(pane.locator('.dom-status [role="alert"]')).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    }
    await expect(page.getByRole('button', { name: 'Reconnect terminal', exact: true })).toBeEnabled();
  } finally {
    await context.setOffline(false);
    await network.send('Network.setBlockedURLs', { urls: [] });
    await network.detach();
  }
});

test('hidden documents and collapsed DOM panes stop queries without pausing replay', async ({ page }) => {
  const { latestFrame } = await openDemo(page);
  await replay(page, 'play');
  const initial = await body<VisualGrid>(nextResponse(page, 'visualization'));
  const queries: string[] = [];
  const observe = (request: Request) => {
    const path = new URL(request.url()).pathname;
    if (path === '/api/v1/visualization' || path === '/api/v1/ladder') queries.push(path);
  };
  page.on('request', observe);
  try {
    await page.getByRole('button', { name: 'DOM / tape', exact: true }).click();
    await expect(page.locator('.book-tape')).toBeHidden();
    queries.length = 0;
    // A bounded negative observation spans three nominal ladder polling periods.
    await page.waitForTimeout(2300);
    expect(queries.filter(path => path.endsWith('/ladder'))).toEqual([]);
    const advancing = await body<VisualGrid>(nextResponse(page, 'visualization'));
    expect(BigInt(advancing.now_ns)).toBeGreaterThan(BigInt(initial.now_ns));
    const beforeHidden = BigInt(latestFrame().replay!.cursor.delivery_index);
    await page.evaluate(() => {
      Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    queries.length = 0;
    await page.waitForTimeout(2300);
    expect(queries).toEqual([]);
    expect(BigInt(latestFrame().replay!.cursor.delivery_index)).toBeGreaterThan(beforeHidden);
    const resumed = nextResponse(page, 'visualization');
    await page.evaluate(() => {
      Reflect.deleteProperty(document, 'visibilityState');
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(BigInt((await body<VisualGrid>(resumed)).now_ns)).toBeGreaterThan(BigInt(advancing.now_ns));
    // Replay progress is proven above; hold one book for the exact row comparison.
    await replay(page, 'pause');
    const reopened = nextResponse(page, 'ladder');
    await page.getByRole('button', { name: 'DOM / tape', exact: true }).click();
    await assertLadder(page, await body<GroupedLadder>(reopened));
  } finally {
    page.off('request', observe);
    await page.evaluate(() => {
      Reflect.deleteProperty(document, 'visibilityState');
      document.dispatchEvent(new Event('visibilitychange'));
    });
  }
});

test('chart aggregation and freeze are independent native queries', async ({ page }) => {
  const { latestFrame } = await openDemo(page);
  await openChartSettings(page);
  await openChartSettings(page, secondary(page));
  const flowResponse = nextResponse(page, 'visualization', request => request.bucket_ns === '5000000000', 32);
  await secondary(page).getByLabel('Time bucket').selectOption('5000000000');
  const flow = await body<VisualGrid>(flowResponse);
  await expect(secondary(page).getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', flow.view_id);
  await expect(primary(page).getByLabel('Time bucket')).toHaveValue('1000000000');
  const primaryResponse = nextResponse(page, 'visualization', request => request.bucket_ns === '10000000000');
  await primary(page).getByLabel('Time bucket').selectOption('10000000000');
  const liquidity = await body<VisualGrid>(primaryResponse);
  expect(liquidity.bucket_ns).toBe('10000000000');
  expect(flow.bucket_ns).toBe('5000000000');
  expect(flow.instrument_id).toBe(liquidity.instrument_id);
  await expect(secondary(page).getByLabel('Time bucket')).toHaveValue('5000000000');
  await primary(page).getByTestId('view-freeze').click();
  const heldView = await primary(page).getByTestId('market-charts').getAttribute('data-view-id');
  const heldDOM = await ladderEvidence(page);
  await replay(page, 'play');
  const advancing = await body<VisualGrid>(nextResponse(page, 'visualization',
    request => request.bucket_ns === '5000000000', 32));
  await expect.poll(() => BigInt(latestFrame().book?.asof.receive_mono_ns ?? '0') > BigInt(liquidity.now_ns)).toBe(true);
  await expect.poll(async () => (await ladderEvidence(page)).asof?.receive_mono_ns)
    .not.toBe(heldDOM.asof?.receive_mono_ns);
  const later = await body<VisualGrid>(nextResponse(page, 'visualization',
    request => request.bucket_ns === '5000000000', 32));
  expect(BigInt(later.now_ns)).toBeGreaterThan(BigInt(advancing.now_ns));
  await expect(secondary(page).getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', later.view_id);
  await expect(primary(page).getByTestId('market-charts')).toHaveAttribute('data-view-id', heldView!);
  await expect(primary(page).getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', heldView!);
  await expect(page.getByTestId('dom-freeze')).toHaveAttribute('aria-pressed', 'false');
  await replay(page, 'pause');
});

test('cursor links propagate exact DOM prices only to opted-in chart panes', async ({ page }) => {
  await openDemo(page);
  await expect(primary(page).getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', /.+/);
  await expect(secondary(page).getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', /.+/);
  // The native startup book has one bid and one ask, not two bid levels.
  const bid = page.getByTestId('grouped-ladder').locator('tr.bid').first();
  const ask = page.getByTestId('grouped-ladder').locator('tr.ask').first();
  const firstPrice = await bid.getAttribute('data-price');
  const nextPrice = await ask.getAttribute('data-price');
  expect(firstPrice).toBeTruthy();
  expect(nextPrice).toBeTruthy();
  expect(nextPrice).not.toBe(firstPrice);
  await bid.hover();
  for (const pane of [primary(page), secondary(page)]) {
    await expect(pane.getByRole('tooltip')).toContainText(firstPrice!);
  }
  await secondary(page).getByRole('button', { name: 'Link Order flow cursor', exact: true }).click();
  await expect(secondary(page).getByRole('button', { name: 'Link Order flow cursor', exact: true })).toHaveAttribute('aria-pressed', 'false');
  await ask.hover();
  await expect(primary(page).getByRole('tooltip')).toContainText(nextPrice!);
  await expect(secondary(page).getByRole('tooltip')).toHaveCount(0);
  await secondary(page).getByRole('button', { name: 'Link Order flow cursor', exact: true }).click();
  await bid.hover();
  await expect(secondary(page).getByRole('tooltip')).toContainText(firstPrice!);
});

test('keyboard maximize and Escape restore panes without replacing native views', async ({ page }) => {
  await openDemo(page);
  const workspace = page.getByTestId('workspace');
  const chart = primary(page).getByTestId('market-charts');
  await expect(chart).toHaveAttribute('data-painted-view-id', /.+/);
  await primary(page).getByTestId('view-freeze').click();
  const before = await chart.getAttribute('data-view-id');
  const original = await primary(page).boundingBox();
  await primary(page).getByRole('button', { name: 'Maximize Liquidity', exact: true }).press('Enter');
  await expect(workspace).toHaveAttribute('data-maximized', 'heatmap');
  await expect(secondary(page)).not.toBeVisible();
  await expect(page.locator('[data-pane="dom"]')).not.toBeVisible();
  const maximized = await primary(page).boundingBox();
  expect(maximized!.height).toBeGreaterThan(original!.height);
  await expect(chart).toHaveAttribute('data-view-id', before!);
  await page.keyboard.press('Escape');
  await expect(workspace).toHaveAttribute('data-maximized', '');
  await expect(secondary(page)).toBeVisible();
  await expect(page.locator('[data-pane="dom"]')).toBeVisible();
  await expect(chart).toHaveAttribute('data-painted-view-id', before!);
  const restored = await primary(page).boundingBox();
  for (const dimension of ['x', 'y', 'width', 'height'] as const) {
    expect(Math.abs(restored![dimension] - original![dimension])).toBeLessThanOrEqual(1);
  }
  await page.getByRole('button', { name: 'Maximize DOM', exact: true }).press('Enter');
  await expect(workspace).toHaveAttribute('data-maximized', 'dom');
  await page.getByRole('button', { name: 'Restore DOM', exact: true }).press('Enter');
  await expect(workspace).toHaveAttribute('data-maximized', '');
  await expect(primary(page)).toBeVisible();
  await expect(secondary(page)).toBeVisible();
});

test('overlapping native chart queries and paint acknowledgments complete for both panes', async ({ page }) => {
  await openDemo(page);
  await expect(primary(page).getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', /.+/);
  await expect(secondary(page).getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', /.+/);
  // Delay the real connection, not responses or economics. Both visualization
  // requests and their actual native paint ACKs remain concurrently in flight.
  const network = await page.context().newCDPSession(page);
  await network.send('Network.enable');
  await network.send('Network.emulateNetworkConditions', {
    offline: false, latency: 350, downloadThroughput: -1, uploadThroughput: -1,
  });
  const acknowledgments: Response[] = [];
  const observe = (response: Response) => {
    if (response.url().endsWith('/api/v1/visualization/ack') && response.request().method() === 'POST') acknowledgments.push(response);
  };
  page.on('response', observe);
  try {
    const liquidityResponse = nextResponse(page, 'visualization', request => request.recenter === true);
    const flowResponse = nextResponse(page, 'visualization', request => request.recenter === true, 32);
    await recenterChart(page);
    await recenterChart(page, secondary(page));
    const [liquidity, flow] = await Promise.all([body<VisualGrid>(liquidityResponse), body<VisualGrid>(flowResponse)]);
    for (const [pane, view] of [[primary(page), liquidity], [secondary(page), flow]] as const) {
      const chart = pane.getByTestId('market-charts');
      await expect(chart).toHaveAttribute('data-painted-view-id', view.view_id);
      await expect(chart).toHaveAttribute('data-receive-to-paint-ms', /\d/);
      await expect.poll(() => acknowledgments.some(response => {
        const request = response.request().postDataJSON() as { view_id: string };
        return request.view_id === view.view_id;
      })).toBe(true);
      const response = acknowledgments.find(item => {
        const request: unknown = item.request().postDataJSON();
        return request !== null && typeof request === 'object' && 'view_id' in request && request.view_id === view.view_id;
      })!;
      const measurement = await body<{ publish_to_ack_upper_bound_ms: number; browser_receive_to_paint_ms: number }>(Promise.resolve(response));
      expect(measurement.publish_to_ack_upper_bound_ms).toBeGreaterThanOrEqual(0);
      expect(measurement.browser_receive_to_paint_ms).toBeGreaterThanOrEqual(0);
    }
  } finally {
    page.off('response', observe);
    await network.detach();
  }
});

test('both frozen chart panes replace native evidence after replay reset and reconnect', async ({ page }) => {
  const { frame: first, catalog } = await openDemo(page);
  const selected = await selectEther(page, catalog);
  await replay(page, 'step');
  await expect(primary(page).getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', /.+/);
  await expect(secondary(page).getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', /.+/);
  await primary(page).getByTestId('view-freeze').click();
  await secondary(page).getByTestId('view-freeze').click();
  await page.getByTestId('dom-freeze').click();
  await expect(page.locator('.tape-panel tbody tr').first()).toBeVisible();
  await page.getByRole('button', { name: 'Freeze tape', exact: true }).click();
  const staleViews = await Promise.all([primary(page), secondary(page)].map(pane => pane.getByTestId('market-charts').getAttribute('data-view-id')));
  const resetResponse = nextResponse(page, 'replay', request => request.action === 'reset');
  const liquidityReset = nextResponse(page, 'visualization', request => request.generation !== selected.generation);
  const flowReset = nextResponse(page, 'visualization', request => request.generation !== selected.generation, 32);
  const domReset = nextResponse(page, 'ladder', request => request.generation !== selected.generation);
  await page.getByTestId('replay-reset').click();
  const reset = await body<Frame>(resetResponse);
  expect(reset.generation).not.toBe(selected.generation);
  expect(reset.book).toBeNull();
  expect(reset.analytics).toBeNull();
  expect(reset.replay?.cursor.delivery_index).toBe('0');
  const resetViews = await Promise.all([body<VisualGrid>(liquidityReset), body<VisualGrid>(flowReset)]);
  for (const [index, pane] of [primary(page), secondary(page)].entries()) {
    const view = resetViews[index];
    expect(view.generation).toBe(reset.generation);
    expect(view.instrument_id).toBe(reset.selection.instrument_id);
    expect(view.view_id).not.toBe(staleViews[index]);
    await expect(pane.getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', view.view_id);
    expect(view.trades).toEqual([]);
    expect(view.profile).toEqual([]);
    expect(view.footprints).toEqual([]);
    await expect(pane.locator('.m4-chart-empty')).toBeVisible();
  }
  const ladder = await body<GroupedLadder>(domReset);
  expect(ladder.generation).toBe(reset.generation);
  await assertLadder(page, ladder);
  await expect(page.getByTestId('grouped-ladder')).toHaveCount(0);
  await expect(page.locator('.tape-panel tbody tr')).toHaveCount(0);
  const replacementResponse = nextResponse(page, 'sessions');
  const liquidityReconnect = nextResponse(page, 'visualization', request => request.session_id !== first.session_id);
  const flowReconnect = nextResponse(page, 'visualization', request => request.session_id !== first.session_id, 32);
  const domReconnect = nextResponse(page, 'ladder', request => request.session_id !== first.session_id);
  await page.getByRole('button', { name: 'Reconnect terminal', exact: true }).click();
  const replacement = await body<Frame>(replacementResponse);
  expect(replacement.session_id).not.toBe(first.session_id);
  expect(replacement.selection).toEqual(reset.selection);
  const replacementViews = await Promise.all([body<VisualGrid>(liquidityReconnect), body<VisualGrid>(flowReconnect)]);
  for (const [index, pane] of [primary(page), secondary(page)].entries()) {
    const view = replacementViews[index];
    expect(view.generation).toBe(replacement.generation);
    expect(view.instrument_id).toBe(replacement.selection.instrument_id);
    expect(view.view_id).not.toBe(resetViews[index].view_id);
    await expect(pane.getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', view.view_id);
  }
  const reconnectedDOM = await body<GroupedLadder>(domReconnect);
  expect(reconnectedDOM.generation).toBe(replacement.generation);
  expect(reconnectedDOM.instrument_id).toBe(replacement.selection.instrument_id);
  await assertLadder(page, reconnectedDOM);
});

test.describe('pane-owned native price origins', () => {
  test('displayable flow without sampled depth stays pinned when its sibling recenters', async ({ page }) => {
    const firstResponse = nextResponse(page, 'visualization', request => request.bucket_ns === '5000000000', 32);
    const { frame } = await openDemo(page);
    await secondary(page).getByLabel('Time bucket', { exact: true }).selectOption('5000000000');
    const flow = await body<VisualGrid>(firstResponse);
    // The actual paused demo has trades before the first five-second depth
    // sample. Require that native case, not a substituted response or source assertion.
    expect(frame.health.ready).toBe(true);
    expect(frame.book?.bids.length).toBeGreaterThan(0);
    expect(frame.book?.asks.length).toBeGreaterThan(0);
    expect(flow.columns.some(column => column.eligible)).toBe(false);
    expect(flow.trades.length).toBeGreaterThan(0);
    expect(flow.footprints.length).toBeGreaterThan(0);
    const chart = secondary(page).getByTestId('market-charts');
    await expect(chart).toHaveAttribute('data-painted-view-id', flow.view_id);
    await expect(secondary(page).locator('.m4-chart-empty')).toHaveCount(0);

    const assertPinned = async (response: Promise<Response>, offset: number) => {
      const received = await response;
      const request = received.request().postDataJSON() as Record<string, unknown>;
      expect(request.price_min).toBe(flow.price_min);
      expect(request.price_step).toBe(flow.price_step);
      expect(request.price_offset_rows).toBe(offset);
      expect(request.recenter).toBeUndefined();
      const view = await body<VisualGrid>(Promise.resolve(received));
      expect(view.price_step).toBe(flow.price_step);
      await expect(chart).toHaveAttribute('data-painted-view-id', view.view_id);
      return view;
    };
    const held = await assertPinned(nextResponse(page, 'visualization', () => true, 32), 0);
    expect(held.price_min).toBe(flow.price_min);
    // Request native-grid navigation explicitly; the default detailed footprint
    // pans its local painted crop before asking the server for another origin.
    await openChartSettings(page, secondary(page));
    await secondary(page).getByLabel('Full native-grid overview', { exact: true }).check();
    await secondary(page).getByRole('button', { name: 'Close Order flow settings', exact: true }).click();

    const pannedResponse = nextResponse(page, 'visualization', request => request.price_offset_rows === 4, 32);
    await secondary(page).locator('.m4-chart-plot').press('ArrowUp');
    const panned = await assertPinned(pannedResponse, 4);
    expect(panned.price_min).not.toBe(flow.price_min);
    const siblingResponse = nextResponse(page, 'visualization', request => request.recenter === true);
    await recenterChart(page);
    await body<VisualGrid>(siblingResponse);
    const afterSibling = await assertPinned(nextResponse(page, 'visualization', () => true, 32), 4);
    expect(afterSibling.price_min).toBe(panned.price_min);
    const repeated = await assertPinned(nextResponse(page, 'visualization', () => true, 32), 4);
    expect(repeated.price_min).toBe(panned.price_min);
  });

  test('unobserved reset geometry is retried until native depth can fit the viewport', async ({ page }) => {
    const { frame } = await openDemo(page);
    const resetResponse = nextResponse(page, 'replay', request => request.action === 'reset');
    const emptyResponse = nextResponse(page, 'visualization', request => request.generation !== frame.generation);
    await page.getByTestId('replay-reset').click();
    const reset = await body<Frame>(resetResponse);
    const empty = await body<VisualGrid>(emptyResponse);
    expect(reset.book).toBeNull();
    expect(reset.analytics).toBeNull();
    expect(empty.columns.some(column => column.eligible)).toBe(false);
    expect(empty.trades).toEqual([]);
    expect(empty.footprints).toEqual([]);

    const retry = await body<VisualGrid>(nextResponse(page, 'visualization', request => request.generation === reset.generation));
    expect(retry.columns.some(column => column.eligible)).toBe(false);
    expect(retry.trades).toEqual([]);

    const fittedResponse = page.waitForResponse(async response => {
      if (!response.url().endsWith('/api/v1/visualization') || response.request().method() !== 'POST' || !response.ok()) return false;
      const request = response.request().postDataJSON() as Record<string, unknown>;
      if (request.rows !== 128 || request.generation !== reset.generation) return false;
      const view = await response.json() as VisualGrid;
      return view.columns.some(column => column.eligible);
    });
    await replay(page, 'play');
    const fitted = await fittedResponse;
    const view = await body<VisualGrid>(Promise.resolve(fitted));
    expect(view.price_min).not.toBe(empty.price_min);
    await replay(page, 'pause');
    const pinned = await body<VisualGrid>(nextResponse(page, 'visualization', request => request.generation === reset.generation));
    expect(pinned.price_min).toBe(view.price_min);
    expect(pinned.price_step).toBe(view.price_step);
  });
});
