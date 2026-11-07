import { expect, test } from '@playwright/test';
import type { Catalog, Frame, VisualGrid } from '../src/api';

// Fault injection surrounds the actual backend. Market frames/results are never
// replaced with fixtures: tests drop transport traffic, delay real responses,
// fail HTTP requests, or temporarily mark the real catalog not ready.
test('a silent open socket loses current status and recovers with a new session', async ({ page }) => {
  let suppress = false;
  let socketCount = 0;
  await page.routeWebSocket('**/api/v1/stream?*', client => {
    socketCount++;
    const server = client.connectToServer();
    server.onMessage(message => { if (!suppress) client.send(message); });
  });
  const initialCreated = page.waitForResponse(response => response.url().endsWith('/api/v1/sessions') && response.request().method() === 'POST');
  await page.goto('/');
  const initial = await (await initialCreated).json() as Frame;
  await expect(page.locator('.status')).toContainText('Connected');
  await page.getByRole('tablist', { name: 'Chart workspace', exact: true }).getByRole('tab', { name: 'Order flow', exact: true }).click();
  await expect(page.locator('.ladder tbody tr').first()).toBeVisible();
  const chart = page.getByTestId('chart-pane-heatmap').getByTestId('market-charts');
  await expect(chart).toHaveAttribute('data-painted-view-id', /.+/);
  const initialViewId = await chart.getAttribute('data-painted-view-id');
  await page.getByRole('button', { name: 'Costs / analytics', exact: true }).click();
  const estimate = page.getByTestId('estimate');
  await expect(estimate).toBeEnabled();
  const incident = page.getByTestId('terminal-incident');
  await incident.getByRole('button', { name: /^Diagnostics/ }).click();
  const diagnostics = page.locator('.diagnostics');
  await diagnostics.getByText('All terminal incidents and exact causes', { exact: true }).click();
  const replacementCreated = page.waitForResponse(response => response.url().endsWith('/api/v1/sessions') && response.request().method() === 'POST');
  const replacementView = page.waitForResponse(async response => {
    if (!response.url().endsWith('/api/v1/visualization') || response.request().method() !== 'POST') return false;
    const query = response.request().postDataJSON() as { session_id: string; rows: number };
    if (query.rows !== 128) return false;
    const replacement = await (await replacementCreated).json() as Frame;
    return query.session_id === replacement.session_id;
  });
  suppress = true;
  // Invalidation persists through retry; the one-second disconnected label does not.
  await expect(estimate).toBeDisabled({ timeout: 8000 });
  await expect(page.locator('.ladder tbody tr')).toHaveCount(0);
  await expect(chart).toHaveAttribute('data-painted-view-id', '');
  await expect(page.locator('.source-readiness')).toHaveText('Unavailable');
  await expect(incident).toHaveCount(1);
  await expect(incident).toHaveAttribute('data-incident', /^(terminal|transport)$/);
  await expect(incident.getByRole('button', { name: 'Reconnect', exact: true })).toBeEnabled();
  suppress = false;
  // Resume only transport delivery; the existing retry loop must create the
  // replacement session without a manual reconnect.
  const replacement = await (await replacementCreated).json() as Frame;
  expect(replacement.session_id).not.toBe(initial.session_id);
  expect(replacement.source.id).toBe(initial.source.id);
  expect(replacement.selection.instrument_id).toBe(initial.selection.instrument_id);
  await expect(page.locator('.status')).toContainText('Connected');
  expect(socketCount).toBeGreaterThan(1);
  await expect(page.locator('.ladder tbody tr').first()).toBeVisible();
  await expect(estimate).toBeEnabled();
  const visualization = await replacementView;
  expect(visualization.ok()).toBe(true);
  const recovered = await visualization.json() as VisualGrid;
  expect(recovered.generation).toBe(replacement.generation);
  expect(recovered.instrument_id).toBe(replacement.selection.instrument_id);
  expect(recovered.view_id).not.toBe(initialViewId);
  await expect(chart).toHaveAttribute('data-painted-view-id', recovered.view_id);
  await expect(page.getByTestId('chart-pane-heatmap').getByTestId('chart-freshness')).toHaveAttribute('data-view-id', recovered.view_id);
  await expect(diagnostics.locator('.telemetry')).toContainText(replacement.session_id.slice(0, 8));
  await expect(incident).not.toHaveAttribute('data-incident', /^(terminal|transport)$/);
});

test('setup retries survive repeated HTTP failure without a manual reconnect', async ({ page }) => {
  let failures = 0;
  await page.route(/\/api\/v1\/instruments(?:\?.*)?$/, async route => {
    if (failures < 3) { failures++; await route.abort('connectionfailed'); }
    else await route.continue();
  });
  await page.goto('/');
  await expect(page.locator('.status')).toContainText('Connected', { timeout: 20_000 });
  expect(failures).toBe(3);
  await expect(page.locator('.ladder tbody tr').first()).toBeVisible();
});

test('a replaced session does not inherit an obsolete mutation lock', async ({ page }) => {
  const { promise: held, resolve: release } = Promise.withResolvers<void>();
  const { promise: responseCaptured, resolve: captured } = Promise.withResolvers<void>();
  let delayNext = true;
  await page.route('**/api/v1/replay', async route => {
    if (!delayNext) { await route.continue(); return; }
    delayNext = false;
    const response = await route.fetch();
    captured();
    await held;
    // The old request is intentionally aborted when the document replaces its session.
    await route.fulfill({ response }).catch(() => undefined);
  });
  await page.goto('/');
  await expect(page.locator('.status')).toContainText('Connected');
  await page.getByTestId('replay-step').click();
  await responseCaptured;
  await expect(page.getByTestId('replay-step')).toBeDisabled();
  const created = page.waitForResponse(response => response.url().endsWith('/api/v1/sessions') && response.request().method() === 'POST');
  await page.getByLabel('Reconnect terminal').click();
  const replacement = await (await created).json() as Frame;
  await expect(page.locator('.status')).toContainText('Connected');
  await expect(page.getByTestId('replay-step')).toBeEnabled();
  const before = await page.locator('.cursor').textContent();
  await page.getByTestId('replay-step').click();
  await expect(page.locator('.cursor')).not.toHaveText(before ?? '');
  release();
  await page.locator('.diagnostics > summary').click();
  await expect(page.locator('.telemetry')).toContainText(replacement.session_id.slice(0, 8));
  await expect(page.getByTestId('replay-step')).toBeEnabled();
});

test('catalog readiness refreshes without replacing the active session', async ({ page }) => {
  let loading = true;
  await page.route(/\/api\/v1\/instruments(?:\?.*)?$/, async route => {
    const response = await route.fetch();
    const catalog = await response.json() as Catalog;
    if (loading && new URL(route.request().url()).searchParams.has('source_id')) catalog.sources = catalog.sources.map(source => ({ ...source, status: 'loading', reasons: ['fault_injected_catalog_preparation'] }));
    await route.fulfill({ response, json: catalog });
  });
  const created = page.waitForResponse(response => response.url().endsWith('/api/v1/sessions') && response.request().method() === 'POST');
  await page.goto('/');
  const frame = await (await created).json() as Frame;
  await expect(page.locator('.status')).toContainText('Connected');
  const source = page.getByLabel('Data source').locator(`option[value="${frame.source.id}"]`);
  await expect(source).toHaveJSProperty('disabled', true);
  loading = false;
  await expect(source).toHaveJSProperty('disabled', false, { timeout: 10_000 });
  await page.locator('.diagnostics > summary').click();
  await expect(page.locator('.telemetry')).toContainText(frame.session_id.slice(0, 8));
});

test('receive-to-paint ACK includes frame decode time', async ({ page }) => {
  await page.addInitScript(() => {
    const parse = JSON.parse;
    JSON.parse = (text, reviver) => {
      if (text.includes('"frame_id"') && text.includes('"type"')) {
        const started = performance.now();
        while (performance.now() - started < 80) { /* Deliberate decode fault injection. */ }
      }
      return parse(text, reviver);
    };
  });
  let measured: number | null = null;
  await page.routeWebSocket('**/api/v1/stream?*', client => {
    const server = client.connectToServer();
    client.onMessage(message => {
      const acknowledgement = JSON.parse(message.toString()) as { type: string; browser_receive_to_paint_ms?: number };
      if (acknowledgement.type === 'ack' && measured === null) measured = acknowledgement.browser_receive_to_paint_ms ?? null;
      server.send(message);
    });
  });
  await page.goto('/');
  await expect.poll(() => measured).not.toBeNull();
  expect(measured).toBeGreaterThanOrEqual(80);
});

test('a real canvas render failure clears displayed freshness and cannot acknowledge the failed view', async ({ page }) => {
  await page.addInitScript(() => {
    const fillRect = CanvasRenderingContext2D.prototype.fillRect;
    CanvasRenderingContext2D.prototype.fillRect = function (this: CanvasRenderingContext2D, ...args: Parameters<CanvasRenderingContext2D['fillRect']>) {
      const pane = this.canvas.closest<HTMLElement>('[data-pane="heatmap"]');
      if (pane?.dataset.failCanvasPaint === 'true') {
        this.canvas.dataset.failedViewId = pane.querySelector<HTMLElement>('[data-testid="market-charts"]')?.dataset.viewId ?? '';
        throw new Error('Injected Canvas2D paint failure');
      }
      return fillRect.apply(this, args);
    };
  });
  const acknowledgments: string[] = [];
  page.on('request', request => {
    if (request.url().endsWith('/api/v1/visualization/ack') && request.method() === 'POST') {
      const acknowledgment: unknown = request.postDataJSON();
      if (acknowledgment && typeof acknowledgment === 'object' && 'view_id' in acknowledgment && typeof acknowledgment.view_id === 'string') acknowledgments.push(acknowledgment.view_id);
    }
  });
  const catalogResponse = await page.request.get('/api/v1/instruments');
  expect(catalogResponse.ok()).toBe(true);
  const catalog = await catalogResponse.json() as Catalog;
  const demo = catalog.sources.find(source => source.mode === 'demo' && source.status === 'ready');
  if (!demo) throw new Error('Actual native catalog must expose a ready synthetic demo source');
  await page.goto(`/?source=${encodeURIComponent(demo.id)}`);
  await expect(page.locator('.status')).toContainText('Connected');
  await page.getByRole('tablist', { name: 'Chart workspace', exact: true }).getByRole('tab', { name: 'Order flow', exact: true }).click();
  const pane = page.getByTestId('chart-pane-heatmap');
  const chart = pane.getByTestId('market-charts');
  const freshness = pane.getByTestId('chart-freshness');
  const canvas = pane.getByTestId('liquidity-canvas');
  await expect(chart).toHaveAttribute('data-painted-view-id', /.+/);
  const initialId = await chart.getAttribute('data-painted-view-id');
  await expect(freshness).toHaveAttribute('data-view-id', initialId!);
  await expect.poll(() => acknowledgments.includes(initialId!)).toBe(true);

  const nextView = (bucket: string) => page.waitForResponse(response => {
    if (!response.url().endsWith('/api/v1/visualization') || response.request().method() !== 'POST') return false;
    const request = response.request().postDataJSON() as { rows: number; bucket_ns: string };
    return request.rows === 128 && request.bucket_ns === bucket;
  });
  await pane.evaluate(node => { node.setAttribute('data-fail-canvas-paint', 'true'); });
  const failedResponse = nextView('5000000000');
  await pane.getByLabel('Time bucket').selectOption('5000000000');
  const failedResult = await failedResponse;
  expect(failedResult.ok()).toBe(true);
  const failed = await failedResult.json() as VisualGrid;
  await expect(canvas).toHaveAttribute('data-failed-view-id', failed.view_id);
  await expect(pane.getByRole('alert')).toContainText('Injected Canvas2D paint failure');
  await expect(chart).toHaveAttribute('data-painted-view-id', '');
  await expect(freshness).not.toHaveAttribute('data-view-id');
  await expect(freshness).toHaveAttribute('data-unavailable-reason', 'chart identity mismatch / unavailable');
  await expect(freshness).not.toHaveAttribute('data-evidence-ns');
  await expect(freshness).not.toHaveAttribute('data-response-resident-ms');
  expect(await canvas.evaluate(node => {
    const surface = node as HTMLCanvasElement;
    const pixels = surface.getContext('2d')!.getImageData(0, 0, surface.width, surface.height).data;
    return pixels.every((value, index) => index % 4 === 3 || value === 0);
  })).toBe(true);
  // Allow both paint opportunities to pass: a failed draw must never emit an ACK.
  await page.evaluate(() => {
    const { promise, resolve } = Promise.withResolvers<void>();
    requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    return promise;
  });
  expect(acknowledgments).not.toContain(failed.view_id);

  await pane.evaluate(node => { node.removeAttribute('data-fail-canvas-paint'); });
  const recoveryResponse = nextView('10000000000');
  await pane.getByLabel('Time bucket').selectOption('10000000000');
  const recoveryResult = await recoveryResponse;
  expect(recoveryResult.ok()).toBe(true);
  const recovered = await recoveryResult.json() as VisualGrid;
  expect(recovered.view_id).not.toBe(failed.view_id);
  await expect(chart).toHaveAttribute('data-painted-view-id', recovered.view_id);
  await expect(freshness).toHaveAttribute('data-view-id', recovered.view_id);
  await expect(pane.getByRole('alert')).toHaveCount(0);
  await expect.poll(() => acknowledgments.includes(recovered.view_id)).toBe(true);
  expect(acknowledgments).not.toContain(failed.view_id);
  expect(acknowledgments.filter(id => id === initialId)).toHaveLength(1);
});
