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

async function tabTo(page: Page, control: Locator) {
  // Traverse the real tab sequence without pinning the surrounding layout order.
  const candidates = await page.locator('button, input, select, textarea, a[href], summary, [tabindex]').count();
  for (let index = 0; index < candidates; index++) {
    await page.keyboard.press('Tab');
    if (await control.evaluate(element => element === document.activeElement)) return;
  }
  await expect(control).toBeFocused();
}

for (const width of [320, 390, 1280, 1920]) {
  test(`pane actions, Escape focus and Inspector navigation remain usable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await openDemo(page);
    for (const [id, title, rows] of [['heatmap', 'Liquidity', 128], ['flow', 'Order flow', 32]] as const) {
      const pane = page.getByTestId(`chart-pane-${id}`);
      const chart = pane.getByTestId('market-charts');
      await expect(chart).toHaveAttribute('data-view-id', /.+/);
      await expectUnclipped(pane.getByLabel('Chart mode', { exact: true }));
      for (const name of ['Time bucket', 'Price aggregation']) {
        const control = pane.getByLabel(name, { exact: true });
        await expect(control).toHaveCount(1);
        await expectUnclipped(control);
      }
      for (const button of await pane.locator('.chart-pane-actions button').all()) await expectUnclipped(button);

      // Recenter is contextual, but remains reachable through real keyboard input.
      const freeze = pane.getByTestId('view-freeze');
      await freeze.click();
      await expect(freeze).toHaveAttribute('aria-pressed', 'true');
      const settings = pane.getByRole('button', { name: `${title} settings`, exact: true });
      const settingsPanel = pane.locator('.chart-pane-settings');
      await tabTo(page, settings);
      await page.keyboard.press('Enter');
      await expect(settingsPanel).toBeVisible();
      await expect(settings).toBeFocused();
      const recenter = pane.getByTestId('view-recenter');
      await tabTo(page, recenter);
      expect(await recenter.evaluate(element => {
        const style = getComputedStyle(element);
        return element.matches(':focus-visible') && style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0;
      })).toBe(true);
      await page.keyboard.press('Enter');
      await expect(freeze).toHaveAttribute('aria-pressed', 'false');
      await expect(settingsPanel).toHaveCount(0);
      await expect(settings).toBeFocused();
      const maximize = pane.getByRole('button', { name: `Maximize ${title}`, exact: true });
      await maximize.press('Enter');
      await expect(page.locator('.workstation-workspace')).toHaveAttribute('data-maximized', id);
      await page.keyboard.press('Enter');
      await expect(maximize).toBeFocused();
      await expect(page.locator('.workstation-workspace')).toHaveAttribute('data-maximized', '');

      await settings.click();
      await expect(settingsPanel).toBeVisible();
      expect(await settingsPanel.evaluate(element => element.getBoundingClientRect().top >= element.parentElement!.querySelector('header')!.getBoundingClientRect().bottom)).toBe(true);
      await tabTo(page, pane.getByLabel('Chart range', { exact: true }));
      await page.keyboard.press('Escape');
      await expect(settingsPanel).toHaveCount(0);
      await expect(settings).toBeFocused();
      await expect(settings).toHaveAttribute('aria-expanded', 'false');
      await page.keyboard.press('Enter');
      await tabTo(page, pane.getByRole('button', { name: `Close ${title} settings`, exact: true }));
      await page.keyboard.press('Enter');
      await expect(settingsPanel).toHaveCount(0);
      await expect(settings).toBeFocused();

      const inspector = pane.getByRole('button', { name: `${title} Inspector`, exact: true });
      await page.keyboard.press('Enter');
      await tabTo(page, inspector);
      await page.keyboard.press('Enter');
      await expect(settingsPanel).toHaveCount(0);
      await expect(settings).toHaveAttribute('aria-expanded', 'true');
      await expect(pane.getByRole('button', { name: 'Close Chart Inspector', exact: true })).toBeFocused();
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
      await expect(settings).toHaveAttribute('aria-expanded', 'false');
      await expect(settings).toBeFocused();
      await page.keyboard.press('Enter');
      await tabTo(page, inspector);
      await page.keyboard.press('Enter');
      await expect(pane.getByRole('button', { name: 'Close Chart Inspector', exact: true })).toBeFocused();
      await page.keyboard.press('Enter');
      await expect(pane.getByRole('complementary', { name: 'Chart Inspector' })).toBeHidden();
      await expect(settings).toBeFocused();
      await expect(settings).toHaveAttribute('aria-expanded', 'false');
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}

test('Escape closes focused DOM disclosures before restoring a maximized pane', async ({ page }) => {
  await openDemo(page);
  const workspace = page.getByTestId('workspace');
  const dom = page.locator('[data-pane="dom"]');
  const settings = dom.locator('.dom-settings');
  const settingsSummary = settings.locator(':scope > summary');
  await dom.getByRole('button', { name: 'Maximize DOM', exact: true }).click();
  await expect(workspace).toHaveAttribute('data-maximized', 'dom');
  await settingsSummary.click();
  await tabTo(page, settings.getByLabel('DOM quantity units', { exact: true }));
  await page.keyboard.press('Escape');
  await expect(settings).not.toHaveAttribute('open', '');
  await expect(settingsSummary).toBeFocused();
  await expect(workspace).toHaveAttribute('data-maximized', 'dom');
  await page.keyboard.press('Escape');
  await expect(workspace).toHaveAttribute('data-maximized', '');
  await expect(page.getByTestId('chart-pane-heatmap')).toBeVisible();

  await settingsSummary.click();
  const scope = settings.locator('.dom-scope');
  const scopeSummary = scope.locator(':scope > summary');
  await scopeSummary.click();
  await expect(scope).toHaveAttribute('open', '');
  await expect(scopeSummary).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(scope).not.toHaveAttribute('open', '');
  await expect(scopeSummary).toBeFocused();
  await expect(settings).toHaveAttribute('open', '');
  await page.keyboard.press('Escape');
  await expect(settings).not.toHaveAttribute('open', '');
  await expect(settingsSummary).toBeFocused();
});
