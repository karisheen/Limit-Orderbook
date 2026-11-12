import { expect, test } from '@playwright/test';
import type { Locator, Page, Request, Response } from '@playwright/test';
import type { Catalog, Frame, VisualGrid } from '../src/api';
import { chartEvidence } from '../src/data-freshness';
import type { WorkspacePreferences } from '../src/workspace';

// All responses are from the actual native demo session; no substituted market data.
const primary = (page: Page) => page.getByTestId('chart-pane-heatmap');
const secondary = (page: Page) => page.getByTestId('chart-pane-flow');
const preferences = (page: Page): Promise<WorkspacePreferences> =>
  page.evaluate(() => JSON.parse(localStorage.getItem('limit-orderbook.workspace.v1')!));

async function openMarketView(page: Page, name: 'Liquidity & flow' | 'Native book & tape') {
  const tab = page.getByRole('tablist', { name: 'Chart workspace', exact: true }).getByRole('tab', { name, exact: true });
  if (await tab.getAttribute('aria-selected') !== 'true') await tab.click();
  await expect(name === 'Liquidity & flow' ? primary(page) : page.locator('[data-pane="dom"]')).toBeVisible();
}

async function painted(pane: Locator, pending: Promise<Response>) {
  const response = await pending;
  const view = await body<VisualGrid>(Promise.resolve(response));
  const chart = pane.getByTestId('market-charts');
  await pane.locator('.m4-chart-plot').scrollIntoViewIfNeeded();
  await expect(chart).toHaveAttribute('data-painted-view-id', view.view_id);
  await expect(chart).toHaveAttribute('data-start-ns', view.start_ns);
  await expect(chart).toHaveAttribute('data-end-ns', view.end_ns);
  return { view, input: response.request().postDataJSON() as Record<string, unknown> };
}

async function settings(pane: Locator) {
  if (!await pane.locator('.chart-pane-settings').isVisible()) {
    await pane.locator('.chart-pane-actions').getByRole('button', { name: /settings$/ }).click();
  }
  await expect(pane.locator('.chart-pane-settings')).toBeVisible();
}

async function closeSettings(pane: Locator) {
  await pane.locator('.chart-pane-settings').getByRole('button', { name: /^Close .* settings$/ }).click();
}
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
  const catalogResponse = await page.request.get('/api/v1/instruments');
  expect(catalogResponse.ok()).toBe(true);
  const catalog = await catalogResponse.json() as Catalog;
  const demo = catalog.sources.find(source => source.mode === 'demo' && source.status === 'ready');
  if (!demo) throw new Error('The native catalog must expose the explicitly synthetic demo');
  const created = page.waitForResponse(response => response.url().endsWith('/api/v1/sessions')
    && response.request().method() === 'POST');
  await page.goto(`/?source=${encodeURIComponent(demo.id)}`);
  await expect(page.locator('.status')).toContainText(/\bConnected$/i);
  await openMarketView(page, 'Liquidity & flow');
  await primary(page).locator('.m4-chart-plot').scrollIntoViewIfNeeded();
  await expect(primary(page).getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', /.+/);
  const initial = await body<Frame>(created);
  if (!initial.replay) throw new Error('The native demo must provide a replay cursor');
  const middle = (BigInt(initial.replay.total_deliveries) / 2n).toString();
  const seek = page.waitForResponse(response => response.url().endsWith('/api/v1/replay')
    && response.request().postDataJSON().action === 'seek'
    && response.request().postDataJSON().delivery_index === middle);
  await page.getByLabel('Seek delivery').fill(middle);
  await page.getByRole('button', { name: 'Seek', exact: true }).click();
  const frame = await body<Frame>(seek);
  await expect(page.locator('.cursor')).toContainText(`${middle} /`);
  expect(frame.status).toBe('paused');
  expect(BigInt(frame.book!.asof.receive_mono_ns)).toBeGreaterThan(45_000_000_000n);
  expect(BigInt(frame.replay!.cursor.delivery_index)).toBeLessThan(BigInt(frame.replay!.total_deliveries));
  await painted(primary(page), visualization(page, input => input.generation === frame.generation));
  return frame;
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
  // Fit time uses native retained sample bounds, not receive-clock label
  // coverage or an invented preset. It is explicit navigation, still frozen.
  expect(zoomed.retained_start_ns).not.toBeNull();
  const retainedStart = BigInt(zoomed.retained_start_ns!);
  const retainedEnd = BigInt(zoomed.now_ns);
  expect(retainedEnd - retainedStart).toBeGreaterThan(30_000_000_000n);
  expect(retainedEnd - retainedStart).toBeLessThan(1_800_000_000_000n);
  await pane.getByRole('button', { name: 'Close Chart Inspector', exact: true }).click();
  await settings(pane);
  const fittedResponse = visualization(page, input => input.start_ns === retainedStart.toString()
    && input.end_ns === retainedEnd.toString());
  await pane.getByRole('button', { name: 'Fit time', exact: true }).click();
  const fitted = await painted(pane, fittedResponse);
  expect(fitted.view.price_min).toBe(zoomed.price_min);
  expect(fitted.view.price_step).toBe(zoomed.price_step);
  expect(fitted.view.bucket_ns).toBe(zoomed.bucket_ns);
  await expect(pane.getByTestId('view-freeze')).toHaveAttribute('aria-pressed', 'true');
});

test('optional time linking aligns to its actor without coupling cursors, price aggregation or freeze', async ({ page }) => {
  await openAdvancedDemo(page);
  const liquidity = primary(page), flow = secondary(page);
  const initial = await painted(liquidity, visualization(page, () => true));
  const groupedResponse = visualization(page, input => input.price_step === initial.view.step_options.at(-1)
    && input.recenter === true);
  await liquidity.getByLabel('Price aggregation', { exact: true }).selectOption(initial.view.step_options.at(-1)!);
  await painted(liquidity, groupedResponse);
  await liquidity.getByTestId('view-freeze').click();
  const heldId = await liquidity.getByTestId('market-charts').getAttribute('data-painted-view-id');
  const bucketResponse = visualization(page, input => input.bucket_ns === '5000000000', 32);
  await flow.getByLabel('Time bucket', { exact: true }).selectOption('5000000000');
  await painted(flow, bucketResponse);
  await settings(flow);
  const rangeResponse = visualization(page, input => typeof input.start_ns === 'string' && typeof input.end_ns === 'string'
    && BigInt(input.end_ns) - BigInt(input.start_ns) === 30_000_000_000n, 32);
  await flow.getByLabel('Chart range').selectOption('30');
  const ranged = await painted(flow, rangeResponse);
  await closeSettings(flow);
  const zoomResponse = visualization(page, input => typeof input.start_ns === 'string' && typeof input.end_ns === 'string'
    && BigInt(input.end_ns) - BigInt(input.start_ns) === 24_000_000_000n, 32);
  await flow.locator('.m4-chart-plot').press('+');
  const actor = await painted(flow, zoomResponse);
  expect(actor.view.start_ns).not.toBe(ranged.view.start_ns);
  await expect(liquidity.getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', heldId!);
  await expect(liquidity.getByRole('button', { name: 'Link Liquidity cursor', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(flow.getByRole('button', { name: 'Link Order flow cursor', exact: true })).toHaveAttribute('aria-pressed', 'true');
  const before = await preferences(page);
  expect(before.rangeLinked).toBe(false);

  // Cursor opt-out neither enables time linking nor edits either native view.
  await flow.getByRole('button', { name: 'Link Order flow cursor', exact: true }).click();
  await settings(flow);
  await expect(flow.getByLabel('Link chart time ranges')).not.toBeChecked();
  const alignResponse = visualization(page, input => input.start_ns === actor.view.start_ns && input.end_ns === actor.view.end_ns);
  await flow.getByLabel('Link chart time ranges').check();
  const aligned = await painted(liquidity, alignResponse);
  expect(aligned.view.bucket_ns).toBe(initial.view.bucket_ns);
  expect(aligned.view.price_step).toBe(initial.view.step_options.at(-1));
  await expect(flow.getByTestId('market-charts')).toHaveAttribute('data-start-ns', actor.view.start_ns);
  await expect(flow.getByTestId('market-charts')).toHaveAttribute('data-end-ns', actor.view.end_ns);
  await expect(flow.getByRole('button', { name: 'Link Order flow cursor', exact: true })).toHaveAttribute('aria-pressed', 'false');
  await closeSettings(flow);

  const nextWidth = 24_000_000_000n * 4n / 5n;
  const matchesZoom = (input: Record<string, unknown>) => typeof input.start_ns === 'string' && typeof input.end_ns === 'string'
    && BigInt(input.end_ns) - BigInt(input.start_ns) === nextWidth;
  const liquidityZoom = visualization(page, matchesZoom);
  const flowZoom = visualization(page, matchesZoom, 32);
  await flow.locator('.m4-chart-plot').press('+');
  const linkedLiquidity = await painted(liquidity, liquidityZoom);
  const linkedFlow = await painted(flow, flowZoom);
  expect(linkedLiquidity.view.start_ns).toBe(linkedFlow.view.start_ns);
  expect(linkedLiquidity.view.end_ns).toBe(linkedFlow.view.end_ns);
  for (const [result, previous] of [[linkedLiquidity, aligned], [linkedFlow, actor]] as const) {
    expect(result.input.price_min).toBe(previous.view.price_min);
    expect(result.input.price_step).toBe(previous.view.price_step);
    expect(result.view.bucket_ns).toBe(previous.view.bucket_ns);
    expect(result.view.price_min).toBe(previous.view.price_min);
    expect(result.input.recenter).toBeUndefined();
  }
  const linked = await preferences(page);
  expect(linked.rangeLinked).toBe(true);
  for (const key of ['bucket', 'priceStep', 'priceOffset', 'mode', 'paused'] as const) {
    expect(linked[key]).toEqual(before[key]);
    expect(linked.secondary[key]).toEqual(before.secondary[key]);
  }
  expect(linked.links).toEqual({ ...before.links, flow: false });
  await expect(liquidity.getByTestId('view-freeze')).toHaveAttribute('aria-pressed', 'true');
  await expect(flow.getByTestId('view-freeze')).toHaveAttribute('aria-pressed', 'false');
  await openMarketView(page, 'Native book & tape');
  const bid = page.getByTestId('grouped-ladder').locator('tr.bid').first();
  const price = await bid.getAttribute('data-price');
  expect(price).toBeTruthy();
  await bid.getByRole('button', { name: `Pin DOM price ${price}`, exact: true }).click();
  await openMarketView(page, 'Liquidity & flow');
  await expect(liquidity.getByRole('tooltip')).toContainText(price!);
  await expect(flow.getByRole('tooltip')).toHaveCount(0);
  for (const pane of [liquidity, flow]) {
    await expect(pane.getByTestId('market-charts')).toHaveAttribute('data-start-ns', linkedFlow.view.start_ns);
    await expect(pane.getByTestId('market-charts')).toHaveAttribute('data-end-ns', linkedFlow.view.end_ns);
  }

  await settings(flow);
  await flow.getByLabel('Link chart time ranges').uncheck();
  await closeSettings(flow);
  for (const [pane, view] of [[liquidity, linkedLiquidity.view], [flow, linkedFlow.view]] as const) {
    await expect(pane.getByTestId('market-charts')).toHaveAttribute('data-start-ns', view.start_ns);
    await expect(pane.getByTestId('market-charts')).toHaveAttribute('data-end-ns', view.end_ns);
  }
  const independentResponse = visualization(page, input => typeof input.start_ns === 'string' && typeof input.end_ns === 'string'
    && BigInt(input.end_ns) - BigInt(input.start_ns) === nextWidth * 4n / 5n, 32);
  await flow.locator('.m4-chart-plot').press('+');
  const independent = await painted(flow, independentResponse);
  expect(independent.view.start_ns).not.toBe(linkedLiquidity.view.start_ns);
  await expect(liquidity.getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', linkedLiquidity.view.view_id);
  // A delivered native replay step updates the unfrozen pane only. Frozen
  // explicit navigation above must not accidentally restart automatic refresh.
  const automatic = visualization(page, input => input.start_ns === independent.view.start_ns && input.end_ns === independent.view.end_ns, 32);
  await replay(page, 'step');
  await painted(flow, automatic);
  await expect(liquidity.getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', linkedLiquidity.view.view_id);
  expect((await preferences(page)).rangeLinked).toBe(false);
});

test('focused secondary chart owns price, follow, fit and freeze shortcuts; DOM and tape cannot leak them', async ({ page }) => {
  await openAdvancedDemo(page);
  const liquidity = primary(page), flow = secondary(page);
  await liquidity.getByTestId('view-freeze').click();
  await expect.poll(async () => (await preferences(page)).paused).toBe(true);
  const primaryId = await liquidity.getByTestId('market-charts').getAttribute('data-painted-view-id');
  const primaryBefore = await preferences(page);
  await settings(flow);
  const rangeResponse = visualization(page, input => typeof input.start_ns === 'string' && typeof input.end_ns === 'string'
    && BigInt(input.end_ns) - BigInt(input.start_ns) === 30_000_000_000n, 32);
  await flow.getByLabel('Chart range').selectOption('30');
  await painted(flow, rangeResponse);
  await flow.getByLabel('Full native-grid overview', { exact: true }).check();
  await closeSettings(flow);
  const plot = flow.locator('.m4-chart-plot');
  await plot.click();
  await expect(plot).toBeFocused();
  await page.keyboard.press('Space');
  await expect(flow.getByTestId('view-freeze')).toHaveAttribute('aria-pressed', 'true');
  const zoomResponse = visualization(page, input => typeof input.start_ns === 'string' && typeof input.end_ns === 'string'
    && BigInt(input.end_ns) - BigInt(input.start_ns) === 24_000_000_000n, 32);
  await page.keyboard.press('+');
  const zoomed = await painted(flow, zoomResponse);
  const priceResponse = visualization(page, input => input.price_offset_rows === 4, 32);
  await page.keyboard.press('ArrowUp');
  const offset = await painted(flow, priceResponse);
  expect(offset.view.price_min).not.toBe(zoomed.view.price_min);
  const centerResponse = visualization(page, input => input.recenter === true && input.price_offset_rows === 0
    && input.start_ns === zoomed.view.start_ns && input.end_ns === zoomed.view.end_ns, 32);
  await page.keyboard.press('r');
  const centered = await painted(flow, centerResponse);
  expect(centered.view.start_ns).toBe(zoomed.view.start_ns);
  expect(centered.view.end_ns).toBe(zoomed.view.end_ns);
  await expect(flow.getByTestId('view-freeze')).toHaveAttribute('aria-pressed', 'true');
  expect((await preferences(page)).secondary.follow).toBe(false);

  const followResponse = visualization(page, input => typeof input.start_ns === 'string' && typeof input.end_ns === 'string'
    && BigInt(input.end_ns) - BigInt(input.start_ns) === 24_000_000_000n, 32);
  await page.keyboard.press('f');
  const following = await painted(flow, followResponse);
  expect(BigInt(following.view.end_ns)).toBeGreaterThan(BigInt(centered.view.end_ns));
  expect(following.view.price_min).toBe(centered.view.price_min);
  expect(following.input.recenter).toBeUndefined();
  await settings(flow);
  await expect(flow.getByLabel('Chart range')).toHaveValue('custom');
  await expect(flow.getByLabel('Follow time', { exact: true })).toBeChecked();
  const followed = await preferences(page);
  expect(followed.secondary.followSpanNs).toBe('24000000000');
  await closeSettings(flow);

  // Compute the fit from this pane's actual returned footprint buckets,
  // intersected with its query and native now. Never use the sibling response
  // or the receive-clock label extent as market history.
  expect(following.view.footprints.length).toBeGreaterThan(0);
  const starts = following.view.footprints.map(cell => BigInt(cell.time_ns));
  const first = starts.reduce((a, b) => a < b ? a : b);
  const last = starts.reduce((a, b) => a > b ? a : b) + BigInt(following.view.bucket_ns);
  const fitStart = first > BigInt(following.view.start_ns) ? first : BigInt(following.view.start_ns);
  const fitEnd = [last, BigInt(following.view.end_ns), BigInt(following.view.now_ns)].reduce((a, b) => a < b ? a : b);
  expect(fitEnd).toBeGreaterThan(fitStart);
  const fitResponse = visualization(page, input => input.start_ns === fitStart.toString() && input.end_ns === fitEnd.toString(), 32);
  await plot.click();
  await page.keyboard.press('Shift+f');
  const fitted = await painted(flow, fitResponse);
  expect(fitted.view.price_min).toBe(following.view.price_min);
  expect(fitted.view.price_step).toBe(following.view.price_step);
  expect(fitted.view.bucket_ns).toBe(following.view.bucket_ns);
  await expect(flow.getByTestId('view-freeze')).toHaveAttribute('aria-pressed', 'true');
  const pinned = await preferences(page);
  expect(pinned.secondary.follow).toBe(false);
  expect(pinned.secondary.followSpanNs).toBeNull();
  expect(pinned.secondary.range).toEqual({ start_ns: fitStart.toString(), end_ns: fitEnd.toString() });
  for (const key of ['follow', 'followSpanNs', 'range', 'priceOffset', 'paused', 'bucket', 'priceStep'] as const) {
    expect(pinned[key]).toEqual(primaryBefore[key]);
  }
  await expect(liquidity.getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', primaryId!);

  // Focus real DOM rows and native tape print buttons, not document.body.
  await openMarketView(page, 'Native book & tape');
  await expect(page.getByTestId('grouped-ladder')).toBeVisible();
  // Workspace persistence is debounced; capture the requested view before testing foreign shortcuts.
  await expect.poll(() => preferences(page)).toMatchObject({ nativeView: 'book' });
  const beforeForeignFocus = await preferences(page);
  const requests: Record<string, unknown>[] = [];
  const observe = (request: Request) => {
    if (request.url().endsWith('/api/v1/visualization') && request.method() === 'POST') requests.push(request.postDataJSON());
  };
  page.on('request', observe);
  try {
    const domRow = page.getByTestId('grouped-ladder').locator('tr[data-side]').first();
    await domRow.focus();
    await expect(domRow).toBeFocused();
    for (const key of ['r', 'f', 'Shift+f', 'Space']) await page.keyboard.press(key);
    const print = page.locator('.tape-panel tbody button').first();
    await print.focus();
    await expect(print).toBeFocused();
    for (const key of ['r', 'f', 'Shift+f', 'Space']) await page.keyboard.press(key);
    await expect(page.getByRole('dialog', { name: 'Trade inspector', exact: true })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(print).toBeFocused();
    await replay(page, 'step');
    expect(await preferences(page)).toEqual(beforeForeignFocus);
    await expect(liquidity).toBeHidden();
    await expect(flow).toBeHidden();
    expect(requests).toEqual([]);
  } finally {
    page.off('request', observe);
  }

  // Only an explicit Space on the focused secondary plot resumes it.
  await openMarketView(page, 'Liquidity & flow');
  await expect(liquidity.getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', primaryId!);
  await expect(flow.getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', fitted.view.view_id);
  const resumedResponse = visualization(page, input => input.start_ns === fitStart.toString() && input.end_ns === fitEnd.toString(), 32);
  await plot.click();
  await page.keyboard.press('Space');
  const resumed = await painted(flow, resumedResponse);
  expect(resumed.view.view_id).not.toBe(fitted.view.view_id);
  await expect(flow.getByTestId('view-freeze')).toHaveAttribute('aria-pressed', 'false');
  await expect(liquidity.getByTestId('view-freeze')).toHaveAttribute('aria-pressed', 'true');
  await expect(liquidity.getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', primaryId!);
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
