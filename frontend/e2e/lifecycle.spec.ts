import { expect, test } from '@playwright/test';
import type { Catalog, Frame } from '../src/api';

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
  await page.goto('/');
  await expect(page.locator('.status')).toHaveText('Connected');
  await expect(page.locator('.ladder tbody tr').first()).toBeVisible();
  suppress = true;
  await expect(page.locator('.status')).toHaveText('Disconnected · reconnecting', { timeout: 8000 });
  await expect(page.getByTestId('estimate')).toBeDisabled();
  await expect(page.locator('.ladder tbody tr')).toHaveCount(0);
  await expect(page.getByRole('alert')).toContainText('No stream frame received');
  suppress = false;
  await expect(page.locator('.status')).toHaveText('Connected');
  expect(socketCount).toBeGreaterThan(1);
  await expect(page.locator('.ladder tbody tr').first()).toBeVisible();
});

test('setup retries survive repeated HTTP failure without a manual reconnect', async ({ page }) => {
  let failures = 0;
  await page.route(/\/api\/v1\/instruments(?:\?.*)?$/, async route => {
    if (failures < 3) { failures++; await route.abort('connectionfailed'); }
    else await route.continue();
  });
  await page.goto('/');
  await expect(page.locator('.status')).toHaveText('Connected', { timeout: 20_000 });
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
  await expect(page.locator('.status')).toHaveText('Connected');
  await page.getByTestId('replay-step').click();
  await responseCaptured;
  await expect(page.getByTestId('replay-step')).toBeDisabled();
  const created = page.waitForResponse(response => response.url().endsWith('/api/v1/sessions') && response.request().method() === 'POST');
  await page.getByLabel('Reconnect terminal').click();
  const replacement = await (await created).json() as Frame;
  await expect(page.locator('.status')).toHaveText('Connected');
  await expect(page.getByTestId('replay-step')).toBeEnabled();
  const before = await page.locator('.cursor').textContent();
  await page.getByTestId('replay-step').click();
  await expect(page.locator('.cursor')).not.toHaveText(before ?? '');
  release();
  await expect(page.locator('.telemetry')).toContainText(replacement.session_id.slice(0, 8));
  await expect(page.getByTestId('replay-step')).toBeEnabled();
});

test('catalog readiness refreshes without replacing the active session', async ({ page }) => {
  let loading = true;
  await page.route(/\/api\/v1\/instruments(?:\?.*)?$/, async route => {
    const response = await route.fetch();
    const catalog = await response.json() as Catalog;
    if (loading) catalog.sources = catalog.sources.map(source => ({ ...source, status: 'loading', reasons: ['fault_injected_catalog_preparation'] }));
    await route.fulfill({ response, json: catalog });
  });
  const created = page.waitForResponse(response => response.url().endsWith('/api/v1/sessions') && response.request().method() === 'POST');
  await page.goto('/');
  const frame = await (await created).json() as Frame;
  await expect(page.locator('.status')).toHaveText('Connected');
  const source = page.getByLabel('Data source').locator(`option[value="${frame.source.id}"]`);
  await expect(source).toHaveJSProperty('disabled', true);
  await expect(source).toContainText('loading');
  await expect(source).toContainText('fault_injected_catalog_preparation');
  loading = false;
  await expect(source).toHaveJSProperty('disabled', false, { timeout: 10_000 });
  await expect(source).toContainText('ready');
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
