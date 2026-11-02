import { expect, test } from '@playwright/test';
import type { Locator, Page } from '@playwright/test';
import type { Catalog, Frame, VisualGrid } from '../src/api';

// Exercise real native demo sessions and browser hit testing; no substituted data.
async function openDemo(page: Page) {
  const response = await page.request.get('/api/v1/instruments');
  expect(response.ok()).toBe(true);
  const catalog = await response.json() as Catalog;
  const demo = catalog.sources.find(source => source.mode === 'demo' && source.status === 'ready');
  if (!demo) throw new Error('A ready native synthetic demo source is required');
  const created = page.waitForResponse(response => response.url().endsWith('/api/v1/sessions') && response.status() === 201);
  await page.goto(`/?source=${encodeURIComponent(demo.id)}`);
  await expect(page.locator('.status')).toHaveText('Connected');
  const frame = await (await created).json() as Frame;
  if (!frame.replay) throw new Error('The native demo must provide a replay cursor');
  const middle = (BigInt(frame.replay.total_deliveries) / 2n).toString();
  await page.getByLabel('Seek delivery').fill(middle);
  await page.getByRole('button', { name: 'Seek', exact: true }).click();
  await expect(page.locator('.cursor')).toContainText(`${middle} /`);
}

async function expectUnclipped(control: Locator) {
  await control.scrollIntoViewIfNeeded();
  expect(await control.evaluate(element => {
    const bounds = element.getBoundingClientRect();
    const pane = element.closest('.chart-pane')!.getBoundingClientRect();
    const center = document.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
    return bounds.left >= pane.left && bounds.right <= pane.right
      && bounds.left >= 0 && bounds.right <= window.innerWidth
      && bounds.top >= pane.top && bounds.bottom <= pane.bottom
      && (center === element || !!center && element.contains(center));
  })).toBe(true);
}

for (const width of [320, 390, 1280, 1920]) {
  test(`pane actions, Escape focus and Inspector navigation remain usable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await openDemo(page);
    for (const [id, title, rows] of [['heatmap', 'Liquidity', 128], ['flow', 'Order flow', 32]] as const) {
      const pane = page.getByTestId(`chart-pane-${id}`);
      const chart = pane.getByTestId('market-charts');
      await expect(chart).toHaveAttribute('data-view-id', /.+/);
      await expectUnclipped(pane.locator('.chart-pane-header > strong'));
      await expectUnclipped(pane.getByLabel('Chart mode', { exact: true }));
      for (const button of await pane.locator('.chart-pane-actions button').all()) await expectUnclipped(button);

      // Tab, not programmatic focus, must reach the trailing actions.
      const freeze = pane.getByTestId('view-freeze');
      await freeze.click();
      await expect(freeze).toHaveAttribute('aria-pressed', 'true');
      await page.keyboard.press('Tab');
      const recenter = pane.getByTestId('view-recenter');
      await expect(recenter).toBeFocused();
      expect(await recenter.evaluate(element => {
        const style = getComputedStyle(element);
        return element.matches(':focus-visible') && style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0;
      })).toBe(true);
      await page.keyboard.press('Enter');
      await expect(freeze).toHaveAttribute('aria-pressed', 'false');
      await page.keyboard.press('Tab');
      const maximize = pane.getByRole('button', { name: `Maximize ${title}`, exact: true });
      await expect(maximize).toBeFocused();
      await page.keyboard.press('Enter');
      await expect(page.locator('.workstation-workspace')).toHaveAttribute('data-maximized', id);
      await page.keyboard.press('Enter');
      await expect(maximize).toBeFocused();
      await expect(page.locator('.workstation-workspace')).toHaveAttribute('data-maximized', '');

      const settings = pane.getByRole('button', { name: `${title} settings`, exact: true });
      await settings.click();
      const settingsPanel = pane.locator('.chart-pane-settings');
      await expect(settingsPanel).toBeVisible();
      expect(await settingsPanel.evaluate(element => element.getBoundingClientRect().top >= element.parentElement!.querySelector('header')!.getBoundingClientRect().bottom)).toBe(true);
      await pane.getByLabel('Time bucket', { exact: true }).focus();
      await page.keyboard.press('Escape');
      await expect(settingsPanel).toHaveCount(0);
      await expect(settings).toBeFocused();
      await expect(settings).toHaveAttribute('aria-expanded', 'false');

      const inspector = pane.getByRole('button', { name: `${title} Inspector`, exact: true });
      await inspector.click();
      // Navigation belongs to the explicit Legend & navigation disclosure.
      // A normal click verifies actual browser hit testing, not DOM dispatch.
      await pane.locator('.m4-chart-guide > summary').click();
      await expect(pane.locator('.m4-chart-guide')).toHaveAttribute('open', '');
      const beforeStart = BigInt((await chart.getAttribute('data-start-ns'))!);
      const beforeEnd = BigInt((await chart.getAttribute('data-end-ns'))!);
      const zoomResponse = page.waitForResponse(response => {
        if (!response.url().endsWith('/api/v1/visualization') || response.request().method() !== 'POST') return false;
        const input = response.request().postDataJSON() as { rows: number; start_ns?: string; end_ns?: string };
        return input.rows === rows && input.start_ns != null && input.end_ns != null
          && BigInt(input.end_ns) - BigInt(input.start_ns) < beforeEnd - beforeStart;
      });
      const zoom = pane.getByRole('button', { name: 'Zoom chart in', exact: true });
      await expectUnclipped(zoom);
      await zoom.click();
      const response = await zoomResponse;
      expect(response.ok()).toBe(true);
      const changed = await response.json() as VisualGrid;
      expect(BigInt(changed.end_ns) - BigInt(changed.start_ns)).toBeLessThan(beforeEnd - beforeStart);
      await expect(chart).toHaveAttribute('data-view-id', changed.view_id);
      await expect(pane.locator('.chart-pane-follow')).toHaveText('Pinned');
      await page.keyboard.press('Escape');
      await expect(pane.getByRole('complementary', { name: 'Chart Inspector' })).toBeHidden();
      await expect(inspector).toHaveAttribute('aria-expanded', 'false');
      await expect(inspector).toBeFocused();
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}
