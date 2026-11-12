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
  await expect(page.locator('.status')).toContainText(/\bConnected$/i);
  await page.getByRole('tablist', { name: 'Chart workspace', exact: true }).getByRole('tab', { name: 'Liquidity & flow', exact: true }).click();
  if ((page.viewportSize()?.width ?? 1440) <= 965) {
    await page.getByRole('tablist', { name: 'Market panes', exact: true }).getByRole('tab', { name: 'Liquidity', exact: true }).click();
  }
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

async function tabTo(page: Page, control: Locator, key: 'Tab' | 'Shift+Tab' = 'Tab') {
  // Traverse the real tab sequence without pinning the surrounding layout order.
  const candidates = await page.locator('button, input, select, textarea, a[href], summary, [tabindex]').count();
  for (let index = 0; index < candidates; index++) {
    await page.keyboard.press(key);
    if (await control.evaluate(element => element === document.activeElement)) return;
  }
  await expect(control).toBeFocused();
}

for (const width of [320, 390, 1280, 1920]) {
  for (const [id, title, rows] of [['heatmap', 'Liquidity', 128], ['flow', 'Order flow', 32]] as const) {
    test(`${title} pane actions, Escape focus and Inspector navigation remain usable at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await openDemo(page);
      if (width <= 965) await page.getByRole('tablist', { name: 'Market panes', exact: true }).getByRole('tab', { name: id === 'heatmap' ? 'Liquidity' : 'Flow', exact: true }).click();
      const pane = page.getByTestId(`chart-pane-${id}`);
      const chart = pane.getByTestId('market-charts');
      await pane.locator('.m4-chart-plot').scrollIntoViewIfNeeded();
      await expect(chart).toHaveAttribute('data-painted-view-id', /.+/);
      await expectUnclipped(pane.getByLabel('Chart mode', { exact: true }));
      for (const name of ['Time bucket', 'Price aggregation']) {
        const control = pane.getByLabel(name, { exact: true });
        await expect(control).toHaveCount(1);
        await expectUnclipped(control);
      }
      for (const button of await pane.locator('.chart-pane-actions button').all()) await expectUnclipped(button);

      // Price recenter remains keyboard reachable without resuming frozen time.
      const freeze = pane.getByTestId('view-freeze');
      await freeze.click();
      await expect(freeze).toHaveAttribute('aria-pressed', 'true');
      const frozenStart = await chart.getAttribute('data-start-ns');
      const frozenEnd = await chart.getAttribute('data-end-ns');
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
      const centeredResponse = page.waitForResponse(response => response.url().endsWith('/api/v1/visualization')
        && response.request().method() === 'POST'
        && response.request().postDataJSON().rows === rows
        && response.request().postDataJSON().recenter === true);
      await page.keyboard.press('Enter');
      const centeredHTTP = await centeredResponse;
      expect(centeredHTTP.ok()).toBe(true);
      const centered = await centeredHTTP.json() as VisualGrid;
      expect(centered.start_ns).toBe(frozenStart);
      expect(centered.end_ns).toBe(frozenEnd);
      await pane.locator('.m4-chart-plot').scrollIntoViewIfNeeded();
      await expect(chart).toHaveAttribute('data-painted-view-id', centered.view_id);
      await expect(freeze).toHaveAttribute('aria-pressed', 'true');
      await expect(settingsPanel).toHaveCount(0);
      await expect(settings).toBeFocused();
      // Resume is explicit; subsequent navigation does not rely on price fitting
      // to silently restart automatic chart refresh.
      await tabTo(page, freeze, 'Shift+Tab');
      await page.keyboard.press('Enter');
      await expect(freeze).toHaveAttribute('aria-pressed', 'false');
      const maximize = pane.getByRole('button', { name: `Maximize ${title}`, exact: true });
      await maximize.press('Enter');
      await expect(page.getByTestId('workspace')).toHaveAttribute('data-maximized', id);
      await page.keyboard.press('Enter');
      await expect(maximize).toBeFocused();
      await expect(page.getByTestId('workspace')).toHaveAttribute('data-maximized', '');

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
      await pane.locator('.m4-chart-plot').scrollIntoViewIfNeeded();
      await expect(chart).toHaveAttribute('data-painted-view-id', changed.view_id);
      await expect(pane.locator('.chart-pane-follow')).toHaveText('Pinned');
      await page.keyboard.press('Escape');
      await expect(pane.locator('.m4-chart-guide')).not.toHaveAttribute('open', '');
      await expect(pane.locator('.m4-chart-guide > summary')).toBeFocused();
      await expect(pane.getByRole('complementary', { name: 'Chart Inspector' })).toBeVisible();
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
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    });
  }
}

test('Escape closes focused DOM disclosures before restoring a maximized pane', async ({ page }) => {
  await openDemo(page);
  await page.getByRole('tablist', { name: 'Chart workspace', exact: true }).getByRole('tab', { name: 'Native book & tape', exact: true }).click();
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
  await expect(page.locator('[data-pane="tape"]')).toBeVisible();

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

for (const width of [320, 390]) {
  test(`market tabs support keyboard selection, view changes and desktop resize at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await openDemo(page);
    const tabs = page.getByRole('tablist', { name: 'Market panes', exact: true });
    const charts = page.getByRole('tablist', { name: 'Chart workspace', exact: true });
    const liquidity = tabs.getByRole('tab', { name: 'Liquidity', exact: true });
    await liquidity.focus();
    for (const [key, name, id] of [
      ['ArrowRight', 'Flow', 'flow'], ['End', 'Tape', 'tape'],
      ['ArrowRight', 'Price', 'price'], ['ArrowLeft', 'Tape', 'tape'],
      ['Home', 'Price', 'price'], ['ArrowRight', 'Liquidity', 'heatmap'],
      ['ArrowRight', 'Flow', 'flow'], ['ArrowRight', 'Book', 'dom'],
    ] as const) {
      await page.keyboard.press(key);
      const tab = tabs.getByRole('tab', { name, exact: true });
      await expect(tab).toBeFocused();
      await expect(tab).toHaveAttribute('aria-selected', 'true');
      await expect(tabs.locator('[tabindex="0"]')).toHaveCount(1);
      await expect(page.locator(`#market-panel-${id}`)).toBeVisible();
      await expect(page.locator('[id^="market-panel-"]:visible')).toHaveCount(1);
      expect(await tab.evaluate(element => element.matches(':focus-visible')
        && getComputedStyle(element).outlineStyle !== 'none')).toBe(true);
    }
    await page.getByRole('button', { name: 'Maximize DOM', exact: true }).click();
    await expect(page.getByTestId('workspace')).toHaveAttribute('data-maximized', 'dom');
    await tabs.getByRole('tab', { name: 'Flow', exact: true }).click();
    await expect(page.getByTestId('workspace')).toHaveAttribute('data-maximized', '');
    await expect(page.getByTestId('chart-pane-flow')).toBeVisible();
    await tabs.getByRole('tab', { name: 'Book', exact: true }).click();
    await page.getByRole('button', { name: 'Maximize DOM', exact: true }).click();
    await charts.getByRole('tab', { name: 'Liquidity & flow', exact: true }).click();
    await expect(page.getByTestId('workspace')).toHaveAttribute('data-maximized', '');
    await expect(liquidity).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByTestId('chart-pane-heatmap')).toBeVisible();
    await expect(page.getByTestId('price-chart')).toBeHidden();
    await expect(page.locator('[id^="market-panel-"]:visible')).toHaveCount(1);
    await tabs.getByRole('tab', { name: 'Price', exact: true }).click();
    await tabs.getByRole('tab', { name: 'Book', exact: true }).click();
    await charts.getByRole('tab', { name: 'Price action', exact: true }).click();
    await expect(tabs.getByRole('tab', { name: 'Price', exact: true })).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByTestId('price-chart')).toBeVisible();
    await expect(page.getByTestId('chart-pane-heatmap')).toBeHidden();
    await expect(page.locator('[id^="market-panel-"]:visible')).toHaveCount(1);
    await tabs.getByRole('tab', { name: 'Flow', exact: true }).click();
    await tabs.getByRole('tab', { name: 'Tape', exact: true }).click();
    await expect(charts.getByRole('tab', { name: 'Native book & tape', exact: true })).toHaveAttribute('aria-selected', 'true');
    await expect(page.locator('[data-pane="tape"]')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

    await tabs.getByRole('tab', { name: 'Flow', exact: true }).focus();
    await page.setViewportSize({ width: 966, height: 900 });
    await expect(tabs).toBeHidden();
    expect(await page.evaluate(() => {
      const active = document.activeElement;
      return active instanceof HTMLElement && active !== document.body && active.getClientRects().length > 0;
    })).toBe(true);
    for (const id of ['dom', 'tape']) await expect(page.locator(`#market-panel-${id}`)).toBeVisible();
    for (const id of ['price', 'heatmap', 'flow']) await expect(page.locator(`#market-panel-${id}`)).toBeHidden();
    await page.setViewportSize({ width, height: 900 });
    await expect(tabs.getByRole('tab', { name: 'Tape', exact: true })).toHaveAttribute('aria-selected', 'true');
    await expect(page.locator('[data-pane="tape"]')).toBeVisible();
    await charts.getByRole('tab', { name: 'Liquidity & flow', exact: true }).click();
    await expect(liquidity).toHaveAttribute('aria-selected', 'true');
    await page.setViewportSize({ width: 966, height: 900 });
    for (const id of ['heatmap', 'flow']) await expect(page.locator(`#market-panel-${id}`)).toBeVisible();
    for (const id of ['price', 'dom', 'tape']) await expect(page.locator(`#market-panel-${id}`)).toBeHidden();
    const split = page.getByRole('separator', { name: 'Chart split', exact: true });
    await split.press('Home');
    await expect(split).toHaveAttribute('aria-valuenow', '30');
    await page.setViewportSize({ width, height: 900 });
    await expect(liquidity).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByTestId('chart-pane-heatmap')).toBeVisible();

    const configuration = page.locator('.workspace-configuration > summary').filter({ hasText: 'Source / workspace' });
    await configuration.press('Enter');
    await tabTo(page, page.getByLabel('Data source', { exact: true }));
    await page.keyboard.press('Escape');
    await expect(page.getByLabel('Data source', { exact: true })).toBeHidden();
    await expect(configuration).toBeFocused();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}

test('unified chart tabs support keyboard selection across reference and native views', async ({ page }) => {
  await openDemo(page);
  const tabs = page.getByRole('tablist', { name: 'Chart workspace', exact: true });
  await tabs.getByRole('tab', { name: 'Liquidity & flow', exact: true }).focus();
  for (const [key, name, panel] of [
    ['ArrowRight', 'Native book & tape', 'dom'],
    ['ArrowRight', 'Footprint', null],
    ['ArrowRight', 'Price action', 'price'],
    ['ArrowRight', 'Liquidity & flow', 'heatmap'],
    ['End', 'Native book & tape', 'tape'],
    ['Home', 'Footprint', null],
    ['ArrowLeft', 'Native book & tape', 'dom'],
  ] as const) {
    await page.keyboard.press(key);
    const tab = tabs.getByRole('tab', { name, exact: true });
    await expect(tab).toBeFocused();
    await expect(tab).toHaveAttribute('aria-selected', 'true');
    await expect(tabs.locator('[tabindex="0"]')).toHaveCount(1);
    if (panel) await expect(page.locator(`#market-panel-${panel}`)).toBeVisible();
    if (name === 'Footprint') {
      await expect(page.locator('#native-footprint')).toBeVisible();
      await expect(page.getByTestId('price-chart')).toBeHidden();
      await expect(page.getByTestId('chart-pane-heatmap')).toBeHidden();
      await expect(page.locator('#market-panel-dom')).toBeHidden();
    }
    expect(await tab.evaluate(element => element.matches(':focus-visible')
      && getComputedStyle(element).outlineStyle !== 'none')).toBe(true);
  }
});
