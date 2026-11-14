import { expect, test, type Locator, type Page } from '@playwright/test';
import type { Catalog, Frame, NativeMarketData } from '../src/api';

// Real demo sessions and canvas pixels. Only the drawing operation is fault
// injected; backend market evidence and successful draws are never substituted.
async function settlePresentation(page: Page) {
  await page.evaluate(async () => {
    for (let index = 0; index < 4; index++) {
      const { promise, resolve } = Promise.withResolvers<void>();
      requestAnimationFrame(() => resolve());
      await promise;
    }
  });
}

async function seek(page: Page, delivery: string) {
  const committed = page.waitForResponse(response => response.url().endsWith('/api/v1/replay') && response.request().method() === 'POST' && response.ok())
    .then(response => response.json() as Promise<Frame>);
  const drained = page.waitForResponse(async response => {
    if (!/\/market-data(?:\?|$)/.test(response.url()) || !response.ok()) return false;
    const frame = await committed;
    const batch = await response.json() as NativeMarketData;
    return batch.session_id === frame.session_id && batch.generation === frame.generation && !batch.has_more;
  });
  await page.getByLabel('Seek delivery').fill(delivery);
  await page.getByRole('button', { name: 'Seek', exact: true }).click();
  await expect(page.locator('.cursor')).toContainText(`${delivery} /`);
  await drained;
  await settlePresentation(page);
  return committed;
}

async function openDemo(page: Page) {
  await page.setViewportSize({ width: 1600, height: 1200 });
  await page.addInitScript(() => {
    const clear = CanvasRenderingContext2D.prototype.clearRect;
    CanvasRenderingContext2D.prototype.clearRect = function (...args) {
      if (this.canvas.closest('.canvas-host')) {
        this.canvas.dataset.testPaints = String(Number(this.canvas.dataset.testPaints ?? '0') + 1);
      }
      return clear.apply(this, args);
    };
    const fill = CanvasRenderingContext2D.prototype.fillRect;
    CanvasRenderingContext2D.prototype.fillRect = function (...args) {
      if (this.canvas.dataset.testFailDraw === 'true') throw new Error('Injected shared panel draw failure');
      return fill.apply(this, args);
    };
  });
  const response = await page.request.get('/api/v1/sources');
  expect(response.ok()).toBe(true);
  const catalog = await response.json() as Catalog;
  const demo = catalog.sources.find(source => source.mode === 'demo' && source.status === 'ready');
  if (!demo) throw new Error('A ready native synthetic demo source is required');
  const created = page.waitForResponse(response => response.url().endsWith('/api/v1/sessions') && response.status() === 201);
  await page.goto(`/?source=${encodeURIComponent(demo.id)}`);
  const initial = await (await created).json() as Frame;
  await expect(page.locator('.status')).toContainText(/\bConnected$/i);
  await page.getByRole('combobox', { name: 'Layout preset', exact: true }).selectOption('execution');
  if (!initial.replay) throw new Error('The native demo must provide a replay cursor');
  const frame = await seek(page, (BigInt(initial.replay.total_deliveries) / 2n).toString());
  const panel = page.getByRole('region', { name: 'Book Imbalance', exact: true });
  const canvas = panel.locator('canvas');
  await canvas.scrollIntoViewIfNeeded();
  await expect(canvas).toHaveAttribute('data-test-paints', /^[1-9]\d*$/);
  await settlePresentation(page);
  return { frame, panel, canvas };
}

async function expectThemePixel(canvas: Locator) {
  await expect.poll(() => canvas.evaluate(node => {
    const canvas = node as HTMLCanvasElement;
    const reference = document.createElement('canvas');
    reference.width = reference.height = 1;
    const context = reference.getContext('2d')!;
    context.fillStyle = getComputedStyle(canvas).getPropertyValue('--bg-panel').trim();
    context.fillRect(0, 0, 1, 1);
    return [...canvas.getContext('2d')!.getImageData(0, 0, 1, 1).data].join(',')
      === [...context.getImageData(0, 0, 1, 1).data].join(',');
  })).toBe(true);
}

test('a shared panel draw failure is accessible, clears pixels, isolates siblings and retries explicitly', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const { panel, canvas } = await openDemo(page);
  const sibling = page.locator('[data-grid-panel="cvd"] canvas');
  await sibling.scrollIntoViewIfNeeded();
  await expect(sibling).toHaveAttribute('data-test-paints', /^[1-9]\d*$/);
  const siblingBefore = Number(await sibling.getAttribute('data-test-paints'));
  await canvas.evaluate(node => { (node as HTMLCanvasElement).dataset.testFailDraw = 'true'; });
  await page.getByRole('combobox', { name: 'Theme', exact: true }).selectOption('light');
  const alert = panel.getByRole('alert');
  await expect(alert).toContainText('Book Imbalance could not be drawn');
  await expect(alert).toContainText('Injected shared panel draw failure');
  expect(await canvas.evaluate(node => (node as HTMLCanvasElement).width)).toBe(0);
  await expect.poll(async () => Number(await sibling.getAttribute('data-test-paints'))).toBeGreaterThan(siblingBefore);
  const failedPaints = await canvas.getAttribute('data-test-paints');
  await page.getByRole('combobox', { name: 'Theme', exact: true }).selectOption('forest');
  await settlePresentation(page);
  expect(await canvas.getAttribute('data-test-paints')).toBe(failedPaints);
  await canvas.evaluate(node => { delete (node as HTMLCanvasElement).dataset.testFailDraw; });
  await alert.getByRole('button', { name: 'Retry drawing', exact: true }).click();
  await expect(alert).toHaveCount(0);
  await expect(canvas).toBeVisible();
  await expectThemePixel(canvas);
  expect(errors).toEqual([]);
});

test('paused shared panels repaint all themes and atomically follow DPR without an idle RAF loop', async ({ page, context }) => {
  const { frame, canvas } = await openDemo(page);
  for (const theme of ['light', 'forest', 'futuristic', 'dark-dull', 'light-dull', 'dark']) {
    const before = Number(await canvas.getAttribute('data-test-paints'));
    await page.getByRole('combobox', { name: 'Theme', exact: true }).selectOption(theme);
    await canvas.scrollIntoViewIfNeeded();
    await expect.poll(async () => Number(await canvas.getAttribute('data-test-paints'))).toBeGreaterThan(before);
    await expectThemePixel(canvas);
  }
  const cdp = await context.newCDPSession(page);
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1200, deviceScaleFactor: 2, mobile: false });
  await expect.poll(() => canvas.evaluate(node => {
    const canvas = node as HTMLCanvasElement;
    return { width: canvas.width, expectedWidth: canvas.parentElement!.clientWidth * devicePixelRatio,
      height: canvas.height, expectedHeight: canvas.parentElement!.clientHeight * devicePixelRatio,
      ratio: devicePixelRatio, transform: canvas.getContext('2d')!.getTransform().a };
  }).then(surface => surface.ratio === 2 && surface.width === surface.expectedWidth && surface.height === surface.expectedHeight && surface.transform === 2)).toBe(true);
  await settlePresentation(page);
  const paints = await canvas.getAttribute('data-test-paints');
  await settlePresentation(page);
  expect(await canvas.getAttribute('data-test-paints')).toBe(paints);
  const active = await (await page.request.get(`/api/v1/sessions/${frame.session_id}`)).json() as Frame;
  expect(active.replay?.cursor).toEqual(frame.replay?.cursor);
  expect(active.generation).toBe(frame.generation);
  await cdp.send('Emulation.clearDeviceMetricsOverride');
  await cdp.detach();
});

test('offscreen shared panels stop drawing while native data drains and return with current retained evidence', async ({ page }) => {
  const { frame, canvas } = await openDemo(page);
  const host = canvas.locator('..');
  await host.evaluate(node => { (node as HTMLElement).style.transform = 'translateX(-20000px)'; });
  await settlePresentation(page);
  const before = await canvas.getAttribute('data-test-paints');
  if (!frame.replay) throw new Error('The native demo must provide a replay cursor');
  const next = await seek(page, (BigInt(frame.replay.total_deliveries) * 3n / 4n).toString());
  await page.getByRole('combobox', { name: 'Theme', exact: true }).selectOption('light');
  await settlePresentation(page);
  expect(await canvas.getAttribute('data-test-paints')).toBe(before);
  expect(next.replay?.cursor).not.toEqual(frame.replay.cursor);
  await host.evaluate(node => { (node as HTMLElement).style.transform = ''; });
  await canvas.scrollIntoViewIfNeeded();
  await expect.poll(async () => Number(await canvas.getAttribute('data-test-paints'))).toBeGreaterThan(Number(before));
  await expectThemePixel(canvas);
  const returned = await canvas.evaluate(node => (node as HTMLCanvasElement).toDataURL());
  // A fresh panel at the same native cursor must agree with re-entry pixels.
  await page.getByRole('combobox', { name: 'Layout preset', exact: true }).selectOption('price');
  await page.getByRole('combobox', { name: 'Layout preset', exact: true }).selectOption('execution');
  const fresh = page.getByRole('region', { name: 'Book Imbalance', exact: true }).locator('canvas');
  await fresh.scrollIntoViewIfNeeded();
  await expect(fresh).toHaveAttribute('data-test-paints', /^[1-9]\d*$/);
  await expect.poll(() => fresh.evaluate(node => (node as HTMLCanvasElement).toDataURL())).toBe(returned);
});
