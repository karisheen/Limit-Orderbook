import { expect, test } from '@playwright/test';
import type { Locator, Page, Request, Response } from '@playwright/test';
import type { Catalog, Costs, Frame, GroupedLadder, ProposalRisk, VisualGrid } from '../src/api';

// These scenarios use the actual server's explicitly synthetic demo and replay
// controls. No intercepted routes, substituted frames, or browser-side economics.
const preferenceKey = 'limit-orderbook.workspace.v1';
const nextResponse = (page: Page, endpoint: string, matches: (body: Record<string, unknown>) => boolean = () => true) =>
  page.waitForResponse(response => response.url().endsWith(`/api/v1/${endpoint}`) && response.request().method() === 'POST'
    && matches(response.request().postDataJSON() as Record<string, unknown>));

async function body<T>(response: Promise<Response>): Promise<T> {
  const result = await response;
  expect(result.ok(), `${result.status()} ${result.url()}: ${await result.text()}`).toBe(true);
  return await result.json() as T;
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
  const created = nextResponse(page, 'sessions');
  const catalogResponse = page.waitForResponse(response => response.url().endsWith('/api/v1/instruments') && response.request().method() === 'GET');
  await page.goto('/');
  const frame = await body<Frame>(created);
  const catalog = await body<Catalog>(catalogResponse);
  expect(frame.source.mode).toBe('demo');
  expect(frame.source.synthetic).toBe(true);
  expect(frame.replay).not.toBeNull();
  await expect(page.locator('.status')).toHaveText('Connected');
  await expect(page.locator('.source-banner')).toContainText('SYNTHETIC DEMO');
  await expect(page.getByTestId('grouped-ladder').locator('tbody tr').first()).toBeVisible();
  return { frame, catalog, latestFrame: () => latest ?? frame };
}

async function evidence<T>(locator: Locator): Promise<T> {
  return JSON.parse(await locator.textContent() ?? 'null') as T;
}
const ladderEvidence = (page: Page) => page.locator('.ladder-panel .evidence pre');

async function assertLadder(page: Page, expected: GroupedLadder) {
  await expect.poll(() => evidence<GroupedLadder>(ladderEvidence(page))).toEqual(expected);
  const displayed = [...expected.rows.filter(row => row.side === 'ask').reverse(), ...expected.rows.filter(row => row.side === 'bid')];
  const rows = page.getByTestId('grouped-ladder').locator('tbody tr');
  await expect(rows).toHaveCount(displayed.length);
  for (let index = 0; index < displayed.length; index++) {
    const level = displayed[index];
    await expect(rows.nth(index).locator('td').first()).toHaveText(level.side);
    expect(await rows.nth(index).locator('.number').evaluateAll(nodes => nodes.map(node => node.getAttribute('title'))))
      .toEqual([level.price, level.base_quantity, level.cumulative_base]);
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
  await page.getByLabel('Search instruments').fill('ETH');
  const selected = nextResponse(page, 'subscriptions', request => request.instrument_id === instrument.instrument_id);
  await page.getByTitle(instrument.instrument_id, { exact: true }).click();
  const frame = await body<Frame>(selected);
  await expect(page.getByTitle(instrument.instrument_id, { exact: true })).toHaveAttribute('aria-pressed', 'true');
  expect(frame.selection.instrument_id).toBe(instrument.instrument_id);
  return frame;
}

test('exact instrument selection, grouped cumulative depth and pinned time range use server evidence', async ({ page }) => {
  const { catalog, latestFrame } = await openDemo(page);
  const frame = await selectEther(page, catalog);
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
  const ladderResponse = nextResponse(page, 'ladder', request => request.price_step === step);
  await page.getByLabel('Price aggregation').selectOption(step);
  const grid = await body<VisualGrid>(regrouped);
  const ladder = await body<GroupedLadder>(ladderResponse);
  expect(grid.price_step).toBe(step);
  expect(grid.instrument_id).toBe(frame.selection.instrument_id);
  expect(ladder.step).toBe(step);
  expect(ladder.instrument_id).toBe(frame.selection.instrument_id);
  expect(ladder.rows.length).toBeGreaterThan(0);
  await assertLadder(page, ladder);

  const bucketResponse = nextResponse(page, 'visualization', request => request.bucket_ns === '5000000000');
  await page.getByLabel('Time bucket').selectOption('5000000000');
  expect((await body<VisualGrid>(bucketResponse)).bucket_ns).toBe('5000000000');
  const rangeResponse = nextResponse(page, 'visualization', request => typeof request.end_ns === 'string' && typeof request.start_ns === 'string'
    && BigInt(request.start_ns) === (BigInt(request.end_ns) > 30_000_000_000n ? BigInt(request.end_ns) - 30_000_000_000n : 0n));
  await page.getByLabel('Chart range').selectOption('30');
  const ranged = await body<VisualGrid>(rangeResponse);
  expect(BigInt(ranged.end_ns) - BigInt(ranged.start_ns)).toBeLessThanOrEqual(30_000_000_000n);
  expect(BigInt(ranged.end_ns)).toBeLessThanOrEqual(BigInt(ranged.now_ns));
  const pinnedResponse = nextResponse(page, 'visualization', request => request.start_ns === ranged.start_ns && request.end_ns === ranged.end_ns);
  await page.getByLabel('Follow time', { exact: true }).uncheck();
  const pinned = await body<VisualGrid>(pinnedResponse);
  await expect(page.locator('.chart-status')).toContainText('Pinned time window');
  const before = await page.locator('.cursor').textContent();
  await replay(page, 'step');
  await expect(page.locator('.cursor')).not.toHaveText(before ?? '');
  const afterStep = await body<VisualGrid>(nextResponse(page, 'visualization', request => request.start_ns === pinned.start_ns && request.end_ns === pinned.end_ns));
  expect(afterStep.start_ns).toBe(pinned.start_ns);
  expect(afterStep.end_ns).toBe(pinned.end_ns);
  const cursorBeforePan = await page.locator('.cursor').textContent();
  const chart = page.getByTestId('market-charts');
  await expect(chart).toHaveAttribute('data-start-ns', afterStep.start_ns);
  const plot = page.getByRole('group', { name: 'Resting liquidity interactive plot', exact: true });
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
  await expect(page.getByLabel('Follow time', { exact: true })).not.toBeChecked();
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
  await page.getByTestId('view-recenter').click();
  const centered = await body<VisualGrid>(centeredResponse);
  await expect(page.getByLabel('Follow time', { exact: true })).toBeChecked();
  await plot.press('Escape');
  await page.getByTestId('grouped-ladder').locator('tbody tr').first().hover();
  const linked = await page.locator('.linked-readout').textContent();
  const linkedTime = BigInt(/source monotonic ns (\d+)/.exec(linked ?? '')![1]!);
  expect(linkedTime).toBeGreaterThanOrEqual(BigInt(centered.start_ns));
  expect(linkedTime).toBeLessThan(BigInt(centered.end_ns));
});

test('view pause holds chart and DOM evidence while replay continues independently', async ({ page }) => {
  const { latestFrame } = await openDemo(page);
  await body<VisualGrid>(nextResponse(page, 'visualization'));
  await page.getByTestId('view-freeze').click();
  await expect(page.getByTestId('view-freeze')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.chart-status')).toContainText('replay / recording unaffected');
  const frozenDOM = await evidence<GroupedLadder>(ladderEvidence(page));
  const chart = page.getByTestId('market-charts');
  await expect(chart).toBeVisible();
  const frozenView = await chart.getAttribute('data-view-id');
  expect(frozenView).toBeTruthy();
  await expect(chart).toHaveAttribute('data-painted-view-id', frozenView!);
  const frozenPixels = await page.getByTestId('liquidity-canvas').screenshot();
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
  await expect(page.getByTestId('view-freeze')).toHaveAttribute('aria-pressed', 'true');
  expect(await evidence<GroupedLadder>(ladderEvidence(page))).toEqual(frozenDOM);
  await expect(chart).toHaveAttribute('data-view-id', frozenView!);
  await expect(chart).toHaveAttribute('data-painted-view-id', frozenView!);
  expect(await page.getByTestId('liquidity-canvas').screenshot()).toEqual(frozenPixels);
  const resumed = nextResponse(page, 'visualization');
  await page.getByTestId('view-freeze').click();
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
  const frozen = await evidence<GroupedLadder>(ladderEvidence(page));
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
  expect(await evidence<GroupedLadder>(ladderEvidence(page))).toEqual(frozen);
  await expect(page.getByTestId('grouped-ladder')).toHaveText(text ?? '');
  await expect(page.getByTestId('view-freeze')).toHaveAttribute('aria-pressed', 'false');
  const unfrozen = nextResponse(page, 'ladder');
  await page.getByTestId('dom-freeze').click();
  const current = await body<GroupedLadder>(unfrozen);
  expect(current.generation).toBe(paused.generation);
  expect(current.asof).not.toEqual(frozen.asof);
  await assertLadder(page, current);
});

test('chart mode, selection and resized layout survive reload but session identity does not', async ({ page }) => {
  const { frame: first, catalog } = await openDemo(page);
  const selected = await selectEther(page, catalog);
  const modes = page.getByRole('tablist', { name: 'Chart view' });
  for (const mode of ['candles', 'footprint', 'profile', 'cvd', 'ofi', 'heatmap']) {
    await modes.getByRole('tab', { name: mode, exact: true }).click();
    await expect(modes.getByRole('tab', { name: mode, exact: true })).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByTestId('market-charts')).toHaveAttribute('data-mode', mode);
  }
  await modes.getByRole('tab', { name: 'footprint', exact: true }).click();
  await page.getByLabel('Workspace preset').selectOption('analysis');
  await page.getByRole('button', { name: 'Watchlist', exact: true }).click();
  await page.getByRole('separator', { name: 'Watchlist width' }).press('ArrowRight');
  await page.getByRole('separator', { name: 'DOM width' }).press('ArrowLeft');
  await page.getByRole('separator', { name: 'Research pane height' }).press('ArrowUp');
  await page.getByLabel('Ladder rows').selectOption('25');
  await page.getByLabel('Time bucket').selectOption('5000000000');
  await page.getByLabel('Chart range').selectOption('60');
  await page.getByLabel('Trades', { exact: true }).uncheck();
  const expected = { mode: 'footprint', preset: 'analysis', leftWidth: 210, rightWidth: 290, bottomHeight: 330, leftOpen: true, domRows: 25, bucket: '5000000000', rangeSeconds: 60, showTrades: false, instrument: selected.selection.instrument_id };
  await expect.poll(() => page.evaluate(key => JSON.parse(localStorage.getItem(key) ?? '{}') as Record<string, unknown>, preferenceKey)).toMatchObject(expected);
  const saved = await page.evaluate(key => localStorage.getItem(key), preferenceKey);
  expect(saved).not.toContain(first.session_id);
  expect(saved).not.toContain('session_id');
  const created = nextResponse(page, 'sessions');
  await page.reload();
  const replacement = await body<Frame>(created);
  expect(replacement.session_id).not.toBe(first.session_id);
  expect(replacement.selection).toEqual(selected.selection);
  expect(replacement.source.id).toBe(first.source.id);
  await expect(page.locator('.status')).toHaveText('Connected');
  await expect(modes.getByRole('tab', { name: 'footprint', exact: true })).toHaveAttribute('aria-selected', 'true');
  for (const [name, value] of [['Watchlist width', '210'], ['DOM width', '290'], ['Research pane height', '330']]) {
    await expect(page.getByRole('separator', { name })).toHaveAttribute('aria-valuenow', value);
  }
  await expect(page.getByLabel('Ladder rows')).toHaveValue('25');
  await expect(page.getByLabel('Time bucket')).toHaveValue('5000000000');
  await expect(page.getByLabel('Chart range')).toHaveValue('60');
  await expect(page.getByLabel('Trades', { exact: true })).not.toBeChecked();
});

for (const width of [1440, 1280]) {
  test(`streaming updates do not move workspace panes at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 1440 ? 900 : 800 });
    await openDemo(page);
    const initial = await body<VisualGrid>(nextResponse(page, 'visualization'));
    await expect(page.getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', /.+/);
    const selectors = ['.workspace', '.sidebar', '.chart-panel', '.book-tape', '.research-pane', '.telemetry'];
    const baseline = await Promise.all(selectors.map(selector => page.locator(selector).boundingBox()));
    expect(baseline.every(box => box !== null && box.width > 0 && box.height > 0)).toBe(true);
    await replay(page, 'play');
    let latest = initial;
    for (let sample = 0; sample < 3; sample++) {
      latest = await body<VisualGrid>(nextResponse(page, 'visualization'));
      await expect(page.getByTestId('market-charts')).toHaveAttribute('data-end-ns', latest.end_ns);
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
  await expect(page.getByRole('separator', { name: 'Watchlist width' })).toHaveAttribute('aria-valuenow', '180');
  await expect(page.getByRole('separator', { name: 'DOM width' })).toHaveAttribute('aria-valuenow', '500');
  await expect(page.getByRole('separator', { name: 'Research pane height' })).toHaveAttribute('aria-valuenow', '248');
  await expect(page.getByLabel('Ladder rows')).toHaveValue('100');
  await expect(page.getByLabel('Price aggregation')).toHaveValue('');
  await expect(page.getByLabel('Time bucket')).toHaveValue('1000000000');
  await expect(page.getByLabel('Chart range')).toHaveValue('180');
  await expect(page.getByLabel('Follow time', { exact: true })).toBeChecked();
  await expect(page.getByTestId('view-freeze')).toHaveAttribute('aria-pressed', 'false');
  await expect(page.getByTestId('dom-freeze')).toHaveAttribute('aria-pressed', 'false');
  await expect(page.getByRole('tablist', { name: 'Chart view' }).getByRole('tab', { name: 'heatmap', exact: true })).toHaveAttribute('aria-selected', 'true');
  await expect.poll(() => page.evaluate(key => JSON.parse(localStorage.getItem(key) ?? '{}') as Record<string, unknown>, preferenceKey)).toMatchObject({ range: null, priceOffset: 0, contrast: 1 });
  expect(await page.evaluate(key => localStorage.getItem(key), preferenceKey)).not.toContain('must-never-be-reused');
});

test('malformed JSON preferences are replaced by a usable default workspace', async ({ page }) => {
  await page.addInitScript(key => { localStorage.setItem(key, '{"version":1,'); }, preferenceKey);
  await openDemo(page);
  await expect(page.getByLabel('Workspace preset')).toHaveValue('balanced');
  await expect(page.getByRole('separator', { name: 'Watchlist width' })).toHaveAttribute('aria-valuenow', '222');
  await expect(page.getByRole('separator', { name: 'DOM width' })).toHaveAttribute('aria-valuenow', '314');
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
  await page.getByLabel('Follow time', { exact: true }).uncheck();
  await page.getByRole('tablist', { name: 'Chart view' }).getByRole('tab', { name: 'cvd', exact: true }).click();
  await page.getByTestId('view-freeze').click();
  await page.getByTestId('dom-freeze').click();
  for (const input of [page.getByLabel('Search instruments'), page.getByLabel('Order size', { exact: true })]) {
    await input.fill('');
    await input.pressSequentially('r d / 123456');
    await expect(input).toHaveValue('r d / 123456');
    await expect(input).toBeFocused();
    await expect(page.getByTestId('view-freeze')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByTestId('dom-freeze')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByLabel('Follow time', { exact: true })).not.toBeChecked();
    await expect(page.getByRole('tablist', { name: 'Chart view' }).getByRole('tab', { name: 'cvd', exact: true })).toHaveAttribute('aria-selected', 'true');
  }
  await page.locator('.brand').click();
  await page.keyboard.press('/');
  await expect(page.getByLabel('Search instruments')).toBeFocused();
  await page.getByLabel('Search instruments').fill('');
  await page.locator('.brand').click();
  await page.keyboard.press('Space');
  await expect(page.getByTestId('view-freeze')).toHaveAttribute('aria-pressed', 'false');
  await page.keyboard.press('d');
  await expect(page.getByTestId('dom-freeze')).toHaveAttribute('aria-pressed', 'false');
  await page.keyboard.press('2');
  await expect(page.getByRole('tablist', { name: 'Chart view' }).getByRole('tab', { name: 'candles', exact: true })).toHaveAttribute('aria-selected', 'true');
  await page.keyboard.press('r');
  await expect(page.getByLabel('Follow time', { exact: true })).toBeChecked();
  await expect(page.getByTestId('replay-play')).toBeEnabled();
  await expect(page.getByTestId('replay-pause')).toBeDisabled();
});

test('narrow workspace exposes real chart, DOM, research and watchlist without document overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openDemo(page);
  await body<VisualGrid>(nextResponse(page, 'visualization'));
  await expect(page.getByTestId('market-charts')).toHaveAttribute('data-painted-view-id', /.+/);
  for (const selector of ['.chart-panel', '.book-tape', '.research-pane', '.sidebar']) {
    const pane = page.locator(selector);
    await pane.scrollIntoViewIfNeeded();
    await expect(pane).toBeInViewport();
    const box = await pane.boundingBox();
    expect(box, selector).not.toBeNull();
    expect(box!.width, selector).toBeGreaterThan(100);
    expect(box!.height, selector).toBeGreaterThan(100);
    expect(box!.x, selector).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width, selector).toBeLessThanOrEqual(390);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  }
  await page.getByTestId('dom-freeze').click();
  await expect(page.getByTestId('dom-freeze')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByTestId('grouped-ladder').locator('tbody tr').first()).toBeVisible();
  await page.getByRole('tablist', { name: 'Research pane' }).getByRole('tab', { name: 'Core analytics', exact: true }).click();
  await expect(page.locator('.analytics-panel .metrics-table tbody tr').first()).toBeVisible();
  await page.getByRole('tablist', { name: 'Research pane' }).getByRole('tab', { name: 'Costs & proposed risk', exact: true }).click();
  await page.getByLabel('Proposal entry price').fill('1');
  await expect(page.getByLabel('Proposal entry price')).toHaveValue('1');
  await page.getByLabel('Search instruments').fill('ETH');
  await expect(page.locator('[data-instrument-id]').first()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
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
    await expect(page.locator('.book-tape')).toHaveCount(0);
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
