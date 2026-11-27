import { expect, test, type Locator, type Page } from '@playwright/test';
import type { Catalog, Frame, NativeMarketData } from '../src/api';
import type { WorkspacePreferences } from '../src/workspace';

// Actual native offline sessions only: no routes or injected runtime app state.
// Graphite/Paper keep their existing registered values; a new default must not
// overwrite a saved v3 choice (especially Midnight, the previous default).
// Only the isolated v2 migration case starts with historical browser storageState.
const themes = [
  ['dark', 'Midnight'], ['light', 'Daylight'], ['forest', 'Forest'],
  ['futuristic', 'Neon'], ['dark-dull', 'Graphite'], ['light-dull', 'Paper'],
] as const;
const owners = new WeakMap<Page, { created: Promise<Frame>[]; mutations: string[] }>();

test.beforeEach(async ({ page }) => {
  const observed = { created: [] as Promise<Frame>[], mutations: [] as string[] };
  owners.set(page, observed);
  page.on('request', request => {
    const path = new URL(request.url()).pathname;
    if (request.method() === 'POST' && ['/api/v1/sessions', '/api/v1/subscriptions', '/api/v1/replay'].includes(path)) {
      observed.mutations.push(path);
    }
  });
  page.on('response', response => {
    if (new URL(response.url()).pathname === '/api/v1/sessions' && response.request().method() === 'POST' && response.ok()) {
      observed.created.push(response.json() as Promise<Frame>);
    }
  });
});

test.afterEach(async ({ page, request }) => {
  await page.close();
  const frames = await Promise.all(owners.get(page)?.created ?? []);
  for (const id of new Set(frames.map(frame => frame.session_id))) {
    const response = await request.delete(`/api/v1/sessions/${id}`);
    expect(response.ok() || response.status() === 404).toBe(true);
  }
});

function nextOwner(page: Page) {
  return page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/sessions'
    && response.request().method() === 'POST').then(async response => {
    expect(response.status()).toBe(201);
    return await response.json() as Frame;
  });
}

async function ready(page: Page, frame: Frame) {
  expect(frame.source.mode).toBe('demo');
  expect(frame.status).toBe('paused');
  expect(frame.replay).not.toBeNull();
  await expect(page.getByRole('button', { name: 'Play', exact: true })).toBeEnabled();
  // This public cursor has no semantic role; its text comes from the real response.
  await expect(page.getByText(`${frame.replay!.cursor.delivery_index} / ${frame.replay!.total_deliveries} · ${frame.replay!.speed}×`, { exact: true })).toBeVisible();
}

async function openDemo(page: Page) {
  const response = await page.request.get('/api/v1/instruments');
  expect(response.ok()).toBe(true);
  const catalog = await response.json() as Catalog;
  const source = catalog.sources.find(item => item.mode === 'demo' && item.status === 'ready');
  if (!source) throw new Error('Shell frame coverage requires a ready native offline demo source');
  const created = nextOwner(page);
  await page.goto(`/?source=${encodeURIComponent(source.id)}`);
  const frame = await created;
  expect(frame.source.id).toBe(source.id);
  await ready(page, frame);
  return frame;
}

async function preferences(page: Page) {
  return page.evaluate(() => {
    const ui = JSON.parse(localStorage.getItem('limit-orderbook.orderflow.ui-preferences') ?? 'null') as
      { version: number; state: Record<string, unknown> } | null;
    const workspace = JSON.parse(localStorage.getItem('limit-orderbook.workspace.v1') ?? 'null') as WorkspacePreferences | null;
    return { ui, workspace };
  });
}

function layout(workspace: WorkspacePreferences | null) {
  if (!workspace) throw new Error('Workspace preferences were not persisted');
  const { grid, layouts, preset, nativeView, desktopView, candleInterval, leftOpen, rightOpen, bottomOpen } = workspace;
  return { grid, layouts, preset, nativeView, desktopView, candleInterval, leftOpen, rightOpen, bottomOpen };
}

async function sameOwner(page: Page, before: Frame, mutations: number) {
  const response = await page.request.get(`/api/v1/sessions/${before.session_id}`);
  expect(response.ok()).toBe(true);
  const after = await response.json() as Frame;
  expect(after.session_id).toBe(before.session_id);
  expect(after.source.id).toBe(before.source.id);
  expect(after.source.mode).toBe(before.source.mode);
  expect(after.selection).toEqual(before.selection);
  expect(after.generation).toBe(before.generation);
  expect(after.replay?.cursor).toEqual(before.replay?.cursor);
  await ready(page, after);
  expect(owners.get(page)!.mutations).toHaveLength(mutations);
}

const themeControl = (page: Page) => page.getByRole('combobox', { name: 'Theme', exact: true });
const chartTabs = (page: Page) => page.getByRole('tablist', { name: 'Chart workspace', exact: true });

test('fresh Graphite keeps the existing Order flow layout and Footprint default view', async ({ page }) => {
  const frame = await openDemo(page);
  await expect(themeControl(page)).toHaveValue('dark-dull');
  await expect(themeControl(page).getByRole('option', { name: 'Graphite', exact: true })).toHaveAttribute('value', 'dark-dull');
  await expect(chartTabs(page).getByRole('tab', { name: 'Footprint', exact: true })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('region', { name: 'Footprint', exact: true })).toBeVisible();
  for (const name of ['Chart panel', 'Order book panel', 'Book imbalance panel', 'Time & sales panel', 'Cumulative delta panel', 'Volume Z-score panel']) {
    await expect(page.getByRole('region', { name, exact: true })).toBeVisible();
  }
  await expect.poll(async () => (await preferences(page)).workspace?.preset).toBe('balanced');
  const before = layout((await preferences(page)).workspace);
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark-dull');
  expect(before.nativeView).toBe('footprint');
  const mutations = owners.get(page)!.mutations.length;
  for (const value of ['light-dull', 'dark-dull']) {
    await themeControl(page).selectOption(value);
    await expect(themeControl(page)).toHaveValue(value);
    await expect(page.locator('html')).toHaveAttribute('data-theme', value);
    expect(layout((await preferences(page)).workspace)).toEqual(before);
    await expect(chartTabs(page).getByRole('tab', { name: 'Footprint', exact: true })).toHaveAttribute('aria-selected', 'true');
    await sameOwner(page, frame, mutations);
  }
});

test('all six saved themes restore independently of workspace layout and v3 chart preferences', async ({ page }) => {
  let frame = await openDemo(page);
  for (const [value, label] of themes) {
    await expect(themeControl(page).getByRole('option', { name: label, exact: true })).toHaveAttribute('value', value);
  }
  await page.getByRole('region', { name: 'Footprint', exact: true }).getByRole('radio', { name: 'Delta', exact: true }).click();
  await page.getByRole('button', { name: 'Chart layers', exact: true }).click();
  await page.getByRole('menuitemcheckbox', { name: /^Traded-volume heatmap/ }).click();
  await page.getByRole('button', { name: 'Chart layers', exact: true }).click();
  await page.getByRole('combobox', { name: 'Layout preset', exact: true }).selectOption({ label: 'Liquidity' });
  await expect(page.getByRole('region', { name: 'Market Depth', exact: true })).toBeVisible();
  // Set up once for all six restores. The preset picker is an action, not a
  // selected-preset indicator: retain its grid, a custom width, and a separate view.
  await page.getByRole('button', { name: 'Edit layout', exact: true }).click();
  const chart = page.getByRole('region', { name: 'Chart panel', exact: true });
  const width = await chart.evaluate(node => node.getBoundingClientRect().width);
  await page.getByRole('button', { name: 'Resize Chart', exact: true }).focus();
  await page.keyboard.press('ArrowLeft');
  await expect.poll(() => chart.evaluate(node => node.getBoundingClientRect().width)).toBeLessThan(width);
  await page.getByRole('button', { name: 'Finish editing', exact: true }).click();
  await chartTabs(page).getByRole('tab', { name: 'Price action', exact: true }).click();
  await page.getByRole('combobox', { name: 'All candle intervals', exact: true }).selectOption('5m');
  await expect.poll(async () => (await preferences(page)).workspace?.candleInterval).toBe('5m');
  const before = await preferences(page);
  expect(before.ui).toMatchObject({ version: 3, state: { footprintMode: 'delta', panels: { heatmap: true } } });
  const savedLayout = layout(before.workspace);
  expect(savedLayout.nativeView).toBe('price');

  for (const [value, label] of themes) {
    await test.step(`save and restore ${label}`, async () => {
      const mutations = owners.get(page)!.mutations.length;
      const ownerCount = owners.get(page)!.created.length;
      await themeControl(page).selectOption({ label });
      await expect(themeControl(page)).toHaveValue(value);
      await expect.poll(async () => (await preferences(page)).ui?.state.theme).toBe(value);
      const savedUI = { ...before.ui!.state, theme: value };
      expect((await preferences(page)).ui).toEqual({ version: 3, state: savedUI });
      await expect(page.locator('html')).toHaveAttribute('data-theme', value);
      expect(layout((await preferences(page)).workspace)).toEqual(savedLayout);
      await sameOwner(page, frame, mutations);

      // Reload starts a new owner; theme changes do not. A new owner's cursor
      // may legitimately reset, so compare paused cursors only within an owner.
      const created = nextOwner(page);
      await page.reload();
      const reloaded = await created;
      expect(reloaded.session_id).not.toBe(frame.session_id);
      expect(reloaded.source.id).toBe(frame.source.id);
      expect(reloaded.selection.instrument_id).toBe(frame.selection.instrument_id);
      await ready(page, reloaded);
      await expect(themeControl(page)).toHaveValue(value);
      await expect(page.locator('html')).toHaveAttribute('data-theme', value);
      await expect(chartTabs(page).getByRole('tab', { name: 'Price action', exact: true })).toHaveAttribute('aria-selected', 'true');
      await expect(page.getByRole('combobox', { name: 'All candle intervals', exact: true })).toHaveValue('5m');
      await expect(page.getByRole('region', { name: 'Market Depth', exact: true })).toBeVisible();
      expect(layout((await preferences(page)).workspace)).toEqual(savedLayout);
      expect((await preferences(page)).ui).toEqual({ version: 3, state: savedUI });
      expect(owners.get(page)!.created).toHaveLength(ownerCount + 1);
      await sameOwner(page, reloaded, mutations + 1);
      frame = reloaded;
    });
  }
  await chartTabs(page).getByRole('tab', { name: 'Footprint', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Footprint', exact: true }).getByRole('radio', { name: 'Delta', exact: true })).toHaveAttribute('aria-checked', 'true');
});

test.describe('historical v2 preferences', () => {
  // Legitimate old-browser input, installed before this isolated context/page is
  // created. No workspace snapshot or session evidence is supplied: production
  // startup must migrate the retired panel visibility before v3 drops that field.
  test.use({
    storageState: async ({ baseURL }, use) => {
      if (!baseURL) throw new Error('Historical preference coverage requires the configured native base URL');
      await use({
        cookies: [],
        origins: [{
          origin: new URL(baseURL).origin,
          localStorage: [{
            name: 'limit-orderbook.orderflow.ui-preferences',
            value: JSON.stringify({
              version: 2,
              state: {
                theme: 'dark',
                footprintMode: 'volume',
                panels: { orderBook: false, volumeProfile: false, heatmap: true },
                heatmap: { tradedVolume: true },
                indicators: { vwap: false, sessionHighLow: true },
              },
            }),
          }],
        }],
      });
    },
  });

  test('hydrates saved Midnight and chart settings, migrates legacy visibility, and restores actual v3 on reload', async ({ page }) => {
    const frame = await openDemo(page);
    const assertMigrated = async () => {
      await expect(themeControl(page)).toHaveValue('dark');
      await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
      const footprint = page.getByRole('region', { name: 'Footprint', exact: true });
      await expect(footprint.getByRole('radio', { name: 'Volume', exact: true })).toHaveAttribute('aria-checked', 'true');
      await expect(page.getByRole('region', { name: 'Order book panel', exact: true })).toHaveCount(0);
      await expect(page.getByRole('region', { name: 'Cumulative delta panel', exact: true })).toBeVisible();
      await footprint.getByRole('button', { name: /^Indicators/ }).click();
      await expect(page.getByRole('menuitemcheckbox', { name: /^Session VWAP/ })).toHaveAttribute('aria-checked', 'false');
      await expect(page.getByRole('menuitemcheckbox', { name: 'Session high / low', exact: true })).toHaveAttribute('aria-checked', 'true');
      await footprint.getByRole('button', { name: /^Indicators/ }).click();

      // Read back production's persisted output, not a replacement fixture.
      await expect.poll(async () => (await preferences(page)).ui?.version).toBe(3);
      await expect.poll(async () => (await preferences(page)).workspace?.grid.cells.some(cell => cell.id === 'orderBook')).toBe(false);
      const saved = await preferences(page);
      expect(saved.ui).toMatchObject({
        version: 3,
        state: {
          theme: 'dark', footprintMode: 'volume',
          panels: { volumeProfile: false, heatmap: true },
          heatmap: { tradedVolume: true },
          indicators: { vwap: false, sessionHighLow: true },
        },
      });
      expect(saved.ui!.state.panels).not.toHaveProperty('orderBook');
      return { ui: saved.ui, layout: layout(saved.workspace) };
    };
    const migrated = await assertMigrated();
    await sameOwner(page, frame, 1);
    expect(owners.get(page)!.created).toHaveLength(1);
    const created = nextOwner(page);
    await page.reload();
    const reloaded = await created;
    expect(reloaded.session_id).not.toBe(frame.session_id);
    expect(reloaded.source.id).toBe(frame.source.id);
    await ready(page, reloaded);
    expect(await assertMigrated()).toEqual(migrated);
    await sameOwner(page, reloaded, 2);
    expect(owners.get(page)!.created).toHaveLength(2);
  });
});

// Measured samples, not token equality: visible descendant text (including muted
// picker labels), a discernible control edge against BOTH adjoining surfaces, and
// keyboard outlines against their actual surface (inside for inset outlines).
// Transparent colors are composited through computed ancestor backgrounds.
// Icon-only Layers and decorative select arrows are not text samples; tabs need
// readable text, not four boxed edges. This is not a full-page WCAG audit.
async function contrastSample(control: Locator) {
  return control.evaluate(element => {
    type Color = [number, number, number, number];
    function color(value: string): Color {
      const srgb = value.startsWith('color(srgb ');
      if (!srgb && !/^rgba?\(/.test(value)) throw new Error(`Unsupported computed color: ${value}`);
      const channels = value.replace(/^color\(srgb\s+|^rgba?\(/, '').replace(/\)$/, '').split(/[\s,/]+/).filter(Boolean).map(Number);
      return [channels[0]! / (srgb ? 1 : 255), channels[1]! / (srgb ? 1 : 255), channels[2]! / (srgb ? 1 : 255), channels[3] ?? 1];
    }
    function over(top: Color, bottom: Color): Color {
      return [top[0] * top[3] + bottom[0] * (1 - top[3]), top[1] * top[3] + bottom[1] * (1 - top[3]), top[2] * top[3] + bottom[2] * (1 - top[3]), 1];
    }
    function surface(node: Element | null): Color {
      if (!node) throw new Error('No opaque computed ancestor surface');
      const style = getComputedStyle(node);
      if (Number(style.opacity) !== 1) throw new Error('Opacity requires an additional compositing sample');
      if (style.backgroundImage !== 'none' && !style.backgroundImage.startsWith('url(')) throw new Error('Gradient needs a pixel sample');
      const background = color(style.backgroundColor);
      return background[3] === 1 ? background : over(background, surface(node.parentElement));
    }
    function luminance(rgb: Color) {
      const linear = rgb.slice(0, 3).map(channel => channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4);
      return linear[0]! * 0.2126 + linear[1]! * 0.7152 + linear[2]! * 0.0722;
    }
    function ratio(a: Color, b: Color) {
      const first = luminance(a), second = luminance(b);
      return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
    }
    const style = getComputedStyle(element), inside = surface(element), outside = surface(element.parentElement);
    const text: { label: string; ratio: number }[] = [];
    function sampleText(node: Element, label: string) {
      const background = surface(node);
      text.push({ label, ratio: ratio(over(color(getComputedStyle(node).color), background), background) });
    }
    if (element instanceof HTMLSelectElement) sampleText(element, element.selectedOptions[0]?.text ?? 'select');
    else if (element instanceof HTMLInputElement) sampleText(element, element.value);
    else {
      const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const parent = node.parentElement;
        if (node.textContent?.trim() && parent?.checkVisibility() && !parent.closest('svg')) sampleText(parent, node.textContent.trim());
      }
    }
    const edges = ['Top', 'Right', 'Bottom', 'Left'].map(side => {
      const css = side.toLowerCase();
      const ink = color(style.getPropertyValue(`border-${css}-color`));
      const painted = over(ink, inside);
      return { side, width: parseFloat(style.getPropertyValue(`border-${css}-width`)),
        style: style.getPropertyValue(`border-${css}-style`), ratio: Math.min(ratio(painted, inside), ratio(painted, outside)) };
    });
    const focusWidth = parseFloat(style.outlineWidth), focusOffset = parseFloat(style.outlineOffset);
    const focusSurfaces = focusOffset >= 0 ? [outside] : focusOffset + focusWidth <= 0 ? [inside] : [inside, outside];
    return { text, edges, focus: { visible: element.matches(':focus-visible'), style: style.outlineStyle,
      width: focusWidth, ratio: Math.min(...focusSurfaces.map(background => ratio(over(color(style.outlineColor), background), background))) } };
  });
}

async function keyboardFocus(page: Page, control: Locator) {
  // Actual Tab navigation, not element.focus() or synthesized focus styling.
  for (let step = 0; step < 100; step++) {
    await page.keyboard.press('Tab');
    if (await control.evaluate(node => node === document.activeElement)) return;
  }
  throw new Error('Required shell control is not reachable within one bounded keyboard traversal');
}

for (const [value, label] of themes.filter(([value]) => value === 'dark-dull' || value === 'light-dull')) {
  test(`${label} shell controls have readable text, visible edges and real keyboard focus without idle animation`, async ({ page }) => {
    const frame = await openDemo(page);
    await themeControl(page).selectOption(value);
    await expect(themeControl(page)).toHaveValue(value);
    // Keep the pointer away from samples: measure ordinary, not hover-only colors.
    await page.mouse.move(0, 0);
    await page.keyboard.press('Tab');
    const textControls = [
      themeControl(page), page.getByRole('button', { name: /^Instrument:/ }),
      page.getByRole('button', { name: /^Venue:/ }),
      page.getByRole('combobox', { name: 'All candle intervals', exact: true }),
      page.getByRole('combobox', { name: 'Layout preset', exact: true }),
      page.getByRole('button', { name: 'Edit layout', exact: true }),
      page.getByRole('button', { name: 'Reconnect terminal', exact: true }),
      page.getByRole('button', { name: 'Play', exact: true }), page.getByRole('button', { name: 'Seek', exact: true }),
      page.getByRole('textbox', { name: 'Seek delivery', exact: true }),
    ];
    const controls = [
      ...textControls.map(control => ({ control, textRequired: true })),
      { control: page.getByRole('button', { name: 'Chart layers', exact: true }), textRequired: false },
    ];
    await expect(page.locator('html')).toHaveAttribute('data-theme', value);
    await expect(page.locator('html')).toHaveCSS('color-scheme', value === 'dark-dull' ? 'dark' : 'light');
    for (const { control, textRequired } of controls) {
      await expect(control).toBeVisible();
      await expect(control).toBeEnabled();
      const sample = await contrastSample(control);
      if (textRequired) expect(sample.text.length, control.toString()).toBeGreaterThan(0);
      for (const text of sample.text) expect(text.ratio, `${label}: ${control}: ${text.label}`).toBeGreaterThanOrEqual(4.5);
      expect(sample.edges.some(edge => edge.width > 0 && edge.style !== 'none' && edge.ratio >= 3),
        `${label}: ${control}: a discernible edge against both adjoining surfaces: ${JSON.stringify(sample.edges)}`).toBe(true);
    }
    for (const tab of await chartTabs(page).getByRole('tab').all()) {
      const { text } = await contrastSample(tab);
      expect(text.length, tab.toString()).toBeGreaterThan(0);
      for (const sample of text) expect(sample.ratio, `${label} tab: ${sample.label}`).toBeGreaterThanOrEqual(4.5);
    }
    for (const control of [themeControl(page), page.getByRole('textbox', { name: 'Seek delivery', exact: true }), page.getByRole('button', { name: 'Edit layout', exact: true })]) {
      await keyboardFocus(page, control);
      await expect(control).toBeFocused();
      const { focus } = await contrastSample(control);
      expect(focus.visible).toBe(true);
      expect(focus.style).not.toBe('none');
      expect(focus.width).toBeGreaterThan(0);
      expect(focus.ratio, `${label}: ${control}: keyboard outline`).toBeGreaterThanOrEqual(3);
    }
    // Finite interaction transitions are allowed; persistent CSS/Web Animations
    // are not. Canvas/DPR/repaint and idle RAF coverage lives in canvas-presentation
    // and chart-redesign; no draw hooks or duplicated repaint scenarios here.
    await expect.poll(() => page.evaluate(() => document.getAnimations().filter(animation =>
      animation.playState === 'running' && animation.effect?.getComputedTiming().iterations === Infinity,
    ).map(animation => animation.id || animation.constructor.name))).toEqual([]);
    await sameOwner(page, frame, 1);
  });
}

// Unlike toBeVisible(), this measures the part actually readable now without
// scrolling it into view: clip against the viewport, tile and every scrolling/
// clipping ancestor. Hit-test the central area, not transparent rounded corners.
async function readableAtRest(target: Locator, plot = false) {
  return target.evaluate((element, plot) => {
    const bounds = element.getBoundingClientRect();
    // Shared study drawing reserves 52px for values and 16px for time; excluding
    // 8px above the plot also covers both studies' top padding. Axes alone cannot
    // satisfy the deliberately modest 64×32 CSS-pixel usable plotting minimum.
    const box = { left: bounds.left, right: bounds.right - (plot ? 52 : 0),
      top: bounds.top + (plot ? 8 : 0), bottom: bounds.bottom - (plot ? 16 : 0) };
    const visible = { left: Math.max(0, box.left), right: Math.min(innerWidth, box.right),
      top: Math.max(0, box.top), bottom: Math.min(innerHeight, box.bottom) };
    for (let ancestor = element.parentElement; ancestor; ancestor = ancestor.parentElement) {
      const style = getComputedStyle(ancestor), rect = ancestor.getBoundingClientRect();
      const tile = ancestor.getAttribute('aria-label')?.endsWith(' panel');
      if (tile || /auto|scroll|hidden|clip/.test(style.overflowX) || style.contain.includes('paint')) {
        visible.left = Math.max(visible.left, rect.left + ancestor.clientLeft);
        visible.right = Math.min(visible.right, rect.left + ancestor.clientLeft + ancestor.clientWidth);
      }
      if (tile || /auto|scroll|hidden|clip/.test(style.overflowY) || style.contain.includes('paint')) {
        visible.top = Math.max(visible.top, rect.top + ancestor.clientTop);
        visible.bottom = Math.min(visible.bottom, rect.top + ancestor.clientTop + ancestor.clientHeight);
      }
    }
    const width = Math.max(0, visible.right - visible.left), height = Math.max(0, visible.bottom - visible.top);
    const unobscured = width > 2 && height > 2 && [0.25, 0.5, 0.75].every(fraction => {
      const hit = document.elementFromPoint(visible.left + width * fraction, visible.top + height / 2);
      return hit !== null && (hit === element || element.contains(hit));
    });
    const enough = plot ? width >= 64 && height >= 32
      : width >= box.right - box.left - 1 && height >= box.bottom - box.top - 1;
    return { readable: element.checkVisibility() && unobscured && enough, width, height,
      fullWidth: box.right - box.left, fullHeight: box.bottom - box.top, unobscured };
  }, plot);
}

test('balanced laptop tiles expose both primary touches, the latest print and usable study plots at rest', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  let frame = await openDemo(page);
  const instrumentId = 'coinbase:advanced-trade:spot:BTC-USD';
  if (frame.selection.instrument_id !== instrumentId) {
    await page.locator('summary').filter({ hasText: /^Watchlist$/ }).click();
    await page.getByRole('searchbox', { name: 'Search instruments', exact: true }).fill('BTC');
    const subscribed = page.waitForResponse(response => response.url().endsWith('/api/v1/subscriptions') && response.ok());
    await page.getByTitle(instrumentId, { exact: true }).click();
    frame = await (await subscribed).json() as Frame;
    await ready(page, frame);
  }
  const venue = page.getByRole('button', { name: /^Venue:/ });
  if (await venue.getAttribute('aria-label') !== 'Venue: Coinbase') {
    await venue.click();
    const subscribed = page.waitForResponse(response => response.url().endsWith('/api/v1/subscriptions') && response.ok());
    await page.getByRole('listbox', { name: 'Venue', exact: true }).getByRole('option', { name: /^Coinbase(?:\s|$)/ }).click();
    frame = await (await subscribed).json() as Frame;
    await ready(page, frame);
  }
  expect(frame.selection.instrument_id).toBe(instrumentId);
  await expect(venue).toHaveAttribute('aria-label', 'Venue: Coinbase');
  await expect(page.getByRole('radiogroup', { name: 'Market', exact: true }).getByRole('radio', { name: 'Spot', exact: true })).toHaveAttribute('aria-checked', 'true');
  // Apply the existing default preset once during setup, not a larger/fewer-panel
  // layout to conceal the defect. Neither viewport check changes market or layout.
  await page.getByRole('combobox', { name: 'Layout preset', exact: true }).selectOption('balanced');
  await page.getByRole('combobox', { name: 'All candle intervals', exact: true }).selectOption('1m');
  expect(BigInt(frame.replay!.total_deliveries)).toBeGreaterThanOrEqual(3485n);
  const committed = page.waitForResponse(response => response.url().endsWith('/api/v1/replay')
    && response.request().method() === 'POST' && response.ok()).then(response => response.json() as Promise<Frame>);
  const batches: NativeMarketData[] = [];
  const drained = page.waitForResponse(async response => {
    const request = response.request();
    if (new URL(response.url()).pathname !== '/api/v1/market-data' || request.method() !== 'POST' || !response.ok()) return false;
    const pinned = await committed;
    const payload = request.postDataJSON() as Pick<Frame, 'session_id' | 'generation'>;
    if (payload.session_id !== pinned.session_id || payload.generation !== pinned.generation) return false;
    const batch = await response.json() as NativeMarketData;
    if (batch.session_id !== pinned.session_id || batch.generation !== pinned.generation) return false;
    batches.push(batch);
    return !batch.has_more;
  });
  await page.getByRole('textbox', { name: 'Seek delivery', exact: true }).fill('3485');
  await page.getByRole('button', { name: 'Seek', exact: true }).click();
  frame = await committed;
  await drained;
  await ready(page, frame);
  expect(frame.replay!.cursor.delivery_index).toBe('3485');
  const primary = batches.flatMap(batch => batch.books).filter(book => book.instrument_id === instrumentId).at(-1);
  if (!primary?.bids[0] || !primary.asks[0]) throw new Error('Pinned native demo must provide both primary touch prices');
  const prints = batches.flatMap(batch => batch.trades).filter(trade => trade.instrument_id === instrumentId && trade.base_quantity !== null);
  const latest = prints.reduce<(typeof prints)[number] | undefined>((last, trade) => !last
    || BigInt(trade.cursor.delivery) > BigInt(last.cursor.delivery)
    || trade.cursor.delivery === last.cursor.delivery && trade.cursor.event > last.cursor.event ? trade : last, undefined);
  if (!latest) throw new Error('Pinned native demo must provide an admitted primary print');
  const mutations = owners.get(page)!.mutations.length;
  const book = page.getByRole('region', { name: 'Order Book', exact: true });
  const tape = page.getByRole('region', { name: 'Time & Sales', exact: true });
  // These price/print rows currently have no public row or cell roles.
  const ask = book.locator('.ladder-asks .ladder-price').last();
  const bid = book.locator('.ladder-bids .ladder-price').first();
  const print = tape.locator('.tape-row').first();
  await expect.poll(async () => Number((await ask.textContent())?.replaceAll(',', ''))).toBe(Number(primary.asks[0].price));
  await expect.poll(async () => Number((await bid.textContent())?.replaceAll(',', ''))).toBe(Number(primary.bids[0].price));
  await expect.poll(() => print.getAttribute('title')).toContain(`${Number(latest.base_quantity)} @ ${Number(latest.trade.price)} · `);
  const latestUTC = new Date(Number(BigInt(latest.display_time_ns) / 1_000_000n)).toISOString().slice(11, 23);
  await expect(print.locator('.tape-time')).toHaveText(latestUTC);
  await page.evaluate(() => document.fonts.ready.then(() => undefined));
  await page.mouse.move(0, 0);
  const cvd = page.getByRole('region', { name: 'Cumulative Delta', exact: true });
  const volume = page.getByRole('region', { name: 'Volume Z-Score', exact: true });
  // First measure data and plot space in BOTH viewports before any study focus
  // can scroll a compact action strip or the document to conceal clipping.
  for (const size of [{ width: 1280, height: 800 }, { width: 1440, height: 900 }]) {
    await page.setViewportSize(size);
    for (const target of [ask, bid, print]) {
      await expect.poll(() => readableAtRest(target), { message: `${size.width}×${size.height}: ${target}` }).toMatchObject({ readable: true });
    }
    for (const panel of [cvd, volume]) {
      await expect.poll(() => readableAtRest(panel.locator('canvas'), true),
        { message: `${size.width}×${size.height}: plot area excluding value/time axes in ${panel}` }).toMatchObject({ readable: true });
    }
    await sameOwner(page, frame, mutations);
    expect(owners.get(page)!.created).toHaveLength(1);
  }

  // Actions may occupy a labeled horizontal strip. They need not all fit at
  // once, but every existing control must become fully readable/hit-testable
  // when reached by actual keyboard navigation, without activating a setting.
  const groups = [cvd.getByRole('radiogroup', { name: 'Bar size', exact: true }),
    volume.getByRole('radiogroup', { name: 'View', exact: true }),
    volume.getByRole('radiogroup', { name: 'Bar size', exact: true }),
    volume.getByRole('radiogroup', { name: 'Lookback window', exact: true })];
  const controls = [cvd.getByRole('button', { name: 'Price', exact: true })];
  for (const group of groups) {
    await expect(group).toBeVisible();
    const radios = await group.getByRole('radio').all();
    expect(radios.length, group.toString()).toBeGreaterThan(0);
    controls.push(...radios);
  }
  for (const size of [{ width: 1280, height: 800 }, { width: 1440, height: 900 }]) {
    await page.setViewportSize(size);
    for (const control of controls) {
      await expect(control).toBeEnabled();
      await keyboardFocus(page, control);
      await expect(control).toBeFocused();
      expect(await control.evaluate(element => element.matches(':focus-visible'))).toBe(true);
      await expect.poll(() => readableAtRest(control),
        { message: `${size.width}×${size.height}: keyboard-reached ${control}` }).toMatchObject({ readable: true });
    }
    await sameOwner(page, frame, mutations);
    expect(owners.get(page)!.created).toHaveLength(1);
  }
});
