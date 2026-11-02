import { expect, test } from '@playwright/test';
import type { Frame } from '../src/api';

// Requires the actual backend serving a built terminal with its synthetic demo source.
// No route interception, fixture overlay, fake WebSocket or duplicate economic engine.
test('exact selection, costs, replay and visible API errors', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('.status')).toHaveText('Connected');
  await expect(page.locator('.source-banner')).toContainText('SYNTHETIC DEMO');
  await page.getByLabel('Search instruments').fill('ETH');
  const instrument = page.locator('[data-instrument-id]').first();
  const id = await instrument.getAttribute('data-instrument-id');
  await instrument.click();
  await expect(instrument).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.ladder tbody tr').first()).toBeVisible();
  await page.getByRole('button', { name: 'Costs / analytics', exact: true }).click();
  await page.getByLabel('Order size', { exact: true }).fill('0.123456789012345678');
  const inputRequest = page.waitForRequest(request => request.url().endsWith('/api/v1/costs'));
  await page.getByTestId('estimate').click();
  expect((await inputRequest).postDataJSON().base_quantity).toBe('0.123456789012345678');
  // Lot-invalid exact input is intentionally not rounded in the browser.
  await expect(page.getByTestId('estimate')).toBeEnabled();
  await page.getByLabel('Order size', { exact: true }).fill('0.01');
  await page.getByLabel('Fee assumption', { exact: true }).selectOption('assumed');
  await page.getByLabel('Fee basis points', { exact: true }).fill('12.5');
  await page.getByTestId('estimate').click();
  await expect(page.locator('.result-heading')).toBeVisible();
  await expect(page.locator('.estimate-grid')).toBeVisible();
  await expect(page.locator('.selected-identity')).toContainText('ETH');
  expect(id).toBeTruthy();
  const before = await page.locator('.cursor').textContent();
  await page.getByTestId('replay-step').click();
  await expect(page.locator('.cursor')).not.toHaveText(before ?? '');
  await page.getByLabel('Seek delivery').fill('0');
  await page.getByRole('button', { name: 'Seek', exact: true }).click();
  await expect(page.locator('.cursor')).toContainText('0 /');
  await page.getByLabel('Order size', { exact: true }).fill('not-a-decimal');
  await page.getByTestId('estimate').click();
  await expect(page.locator('#workflow-panel-costs[role="tabpanel"] .costs-panel').filter({ has: page.getByTestId('estimate') }).getByRole('alert')).toContainText('HTTP');
});

test('documents own independent sessions and closing releases one owner', async ({ context, request }) => {
  const first = await context.newPage();
  const second = await context.newPage();
  const firstCreated = first.waitForResponse(response => response.url().endsWith('/api/v1/sessions') && response.request().method() === 'POST');
  await first.goto('/');
  const firstFrame = await (await firstCreated).json() as Frame;
  const secondCreated = second.waitForResponse(response => response.url().endsWith('/api/v1/sessions') && response.request().method() === 'POST');
  await second.goto('/');
  const secondFrame = await (await secondCreated).json() as Frame;
  expect(firstFrame.session_id).not.toBe(secondFrame.session_id);
  await expect(first.locator('.status')).toHaveText('Connected');
  await expect(second.locator('.status')).toHaveText('Connected');
  const secondCursor = await second.locator('.cursor').textContent();
  await first.getByTestId('replay-step').click();
  await expect(second.locator('.cursor')).toHaveText(secondCursor ?? '');
  await first.close();
  await expect.poll(async () => {
    const response = await request.get('/api/v1/sessions');
    const body = await response.json() as { sessions: { id: string }[] };
    return body.sessions.map(session => session.id);
  }).not.toContain(firstFrame.session_id);
  await expect(second.locator('.status')).toHaveText('Connected');
});

test('laptop workspace retains controls and reconnect replaces a session', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/');
  await expect(page.locator('.status')).toHaveText('Connected');
  await page.getByLabel('Search instruments').fill('SOL');
  const selected = page.locator('[data-instrument-id]').first();
  await selected.click();
  await expect(selected).toHaveAttribute('aria-pressed', 'true');
  const created = page.waitForResponse(response => response.url().endsWith('/api/v1/sessions') && response.request().method() === 'POST');
  await page.getByText('Diagnostics', { exact: true }).click();
  const sessionBefore = await page.locator('.telemetry').textContent();
  await page.getByLabel('Reconnect terminal').click();
  const replacement = await (await created).json() as Frame;
  await expect(page.locator('.status')).toHaveText('Connected');
  await expect(selected).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.telemetry')).toContainText(replacement.session_id.slice(0, 8));
  expect(sessionBefore).not.toContain(replacement.session_id.slice(0, 8));
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: 'test-results/terminal-1280.png', fullPage: true });
});
