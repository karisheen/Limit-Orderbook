#!/usr/bin/env node
/**
 * Actual offline native-backend evidence, not a fixture or a browser test.
 * PLAYWRIGHT_BASE_URL=http://127.0.0.1:8000 node tools/capture_shell_frame.mjs before artifacts/shell-before
 * Repeat with `after` and another output directory against the PR build.
 * Relative output paths resolve from this checkout, never the invoking cwd.
 * Use an already-running, disposable, offline synthetic-demo backend. This
 * collector never starts a server, contacts exchanges, or deletes its sessions.
 * Graphite/Paper cover every viewport; four other themes cover 1440x900 after.
 * Matrix captures use DPR 1, plus one fresh 1440x900 Graphite DPR 2 sample.
 * Two after images show native Liquidity and display Footprint. Price action
 * is chosen deliberately, not made the default. Images use the real viewport;
 * narrow captures keep the painted chart in view rather than hiding it offscreen.
 */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, readFile, realpath, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, isAbsolute, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = await realpath(resolve(dirname(fileURLToPath(import.meta.url)), '..'));
const require = createRequire(join(root, 'frontend/package.json'));
const { chromium } = require('./node_modules/playwright');
const playwrightVersion = require('./node_modules/playwright/package.json').version;
const [phase, outputArgument, ...extra] = process.argv.slice(2);
assert(['before', 'after'].includes(phase) && outputArgument && !extra.length,
  'Usage: PLAYWRIGHT_BASE_URL=http://127.0.0.1:8000 node tools/capture_shell_frame.mjs before|after OUTPUT_DIRECTORY');
assert(process.env.PLAYWRIGHT_BASE_URL, 'PLAYWRIGHT_BASE_URL is required');
const base = new URL(process.env.PLAYWRIGHT_BASE_URL);
assert(base.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(base.hostname)
  && !base.username && !base.password && base.pathname === '/' && !base.search && !base.hash,
'PLAYWRIGHT_BASE_URL must be a loopback HTTP origin without credentials, path, query or fragment');
const within = (parent, child) => {
  const rel = relative(parent, child);
  return !rel || (!isAbsolute(rel) && rel !== '..' && !rel.startsWith('../'));
};
const output = resolve(root, outputArgument);
// Resolve existing ancestors before mkdir, so a symlink cannot redirect output
// into another checkout. Only this checkout and system temporary roots are valid.
let ancestor = output;
while (true) {
  try { ancestor = await realpath(ancestor); break; }
  catch (error) { if (error.code !== 'ENOENT') throw error; ancestor = dirname(ancestor); }
}
const temporaryRoots = await Promise.all([tmpdir(), '/tmp'].map(directory => realpath(directory)));
assert([root, ...temporaryRoots].some(directory => within(directory, ancestor)),
  'Output must be inside this collector checkout or a system temporary directory; other checkouts are read-only');
await mkdir(output, { recursive: true });
const manifestPath = join(output, `${phase}-manifest.json`);
await writeFile(manifestPath, '{}\n', { flag: 'wx' });

const SOURCE = 'synthetic-demo-v3-180s';
const INSTRUMENT = 'coinbase:advanced-trade:spot:BTC-USD';
const DELIVERY = '3485';
const viewports = [[1280, 800], [1440, 900], [1920, 1080], [2560, 1440], [3840, 2160], [390, 844]];
const contexts = [...viewports.map(([width, height]) => ({ width, height, dpr: 1 })), { width: 1440, height: 900, dpr: 2 }];
const themes = ['dark', 'light', 'forest', 'futuristic', 'dark-dull', 'light-dull'];
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const sleep = ms => new Promise(done => setTimeout(done, ms));
const report = {
  schema: 1, phase, status: 'running', startedAt: new Date().toISOString(), baseURL: base.origin,
  playwrightVersion, collectorSHA256: sha(await readFile(fileURLToPath(import.meta.url))),
  selection: { sourceId: SOURCE, instrumentId: INSTRUMENT, delivery: DELIVERY, interval: '1m',
    layout: 'balanced', scope: 'primary', candleSource: 'session_trades', view: 'Price action' },
  matrix: { contexts, matchedThemes: ['dark-dull', 'light-dull'], additionalAfterThemesAt: '1440x900/dpr1',
    additionalAfterViewsAt: '1440x900/dpr1/dark-dull', scaledSample: '1440x900/dpr2/dark-dull', fullPage: false,
    rationale: 'Pairwise theme/viewport coverage, not the full six-by-six cross product. Viewport screenshots keep the sampled paint in view; narrow images intentionally need not include the topbar.' },
  constraints: ['Fresh browser context for each viewport; no preferences seeded',
    'Public visible controls only; no direct API mutations, response interception, or prototype import',
    'Only loopback discovery/session GETs outside application-owned traffic',
    'Backend must already be offline; sessions are owned by its disposable run',
    'Contrast samples are diagnostics, not a baseline gate or a complete accessibility audit'],
  runs: [], captures: [], failures: [],
};
const persist = () => writeFile(manifestPath, `${JSON.stringify(report, null, 2)}\n`);
async function until(label, predicate) {
  const end = Date.now() + 25000;
  while (Date.now() < end) {
    const value = await predicate();
    if (value) return value;
    await sleep(100);
  }
  throw new Error(`Timed out: ${label}; no fallback or substituted data`);
}
const frameSummary = frame => ({ session_id: frame.session_id, generation: frame.generation,
  frame_id: frame.frame_id, status: frame.status, source: frame.source, selection: frame.selection, replay: frame.replay });
async function getJSON(context, pathname) {
  const response = await context.request.get(`${base.origin}${pathname}`, { maxRedirects: 0, timeout: 25000 });
  assert(response.ok(), `${pathname}: HTTP ${response.status()}`);
  return response.json();
}
async function reveal(locator) {
  const ancestors = locator.locator('xpath=ancestor::details');
  for (let i = 0; i < await ancestors.count(); i++) {
    const details = ancestors.nth(i);
    if (await details.getAttribute('open') === null) await details.locator(':scope > summary').click();
  }
}
async function ready(page, observed) {
  const frame = await until('settled native frame', () => observed.frame
    && !['loading', 'seeking', 'playing'].includes(observed.frame.status) && observed.frame);
  await page.waitForFunction(expected => /\bconnected$/i.test(document.querySelector('.status')?.textContent || '')
    && (document.querySelector('.telemetry')?.textContent || '').includes(`Session ${expected.session.slice(0, 8)} · generation ${expected.generation}`)
    && !document.querySelector('[data-testid=replay-step]')?.disabled,
  { session: frame.session_id, generation: frame.generation });
  return frame;
}
async function action(page, observed, endpoint, invoke) {
  await ready(page, observed);
  const previous = observed.frame;
  const [response] = await Promise.all([
    page.waitForResponse(reply => new URL(reply.url()).pathname === `/api/v1/${endpoint}` && reply.request().method() === 'POST'),
    invoke(),
  ]);
  assert(response.ok(), `${endpoint}: HTTP ${response.status()}`);
  const frame = await response.json();
  assert.equal(frame.session_id, previous.session_id, 'Public action replaced the native session');
  assert(BigInt(frame.generation) > BigInt(previous.generation), 'Native action did not advance generation');
  observed.frame = frame;
  await ready(page, observed);
  return frame;
}
function assertPinned(frame, pin) {
  assert.equal(frame.session_id, pin.session_id);
  assert.equal(frame.generation, pin.generation);
  assert.equal(frame.source.id, SOURCE);
  assert.equal(frame.source.mode, 'demo');
  assert.equal(frame.source.synthetic, true);
  assert.equal(frame.selection.instrument_id, INSTRUMENT);
  assert.deepEqual(frame.selection, pin.selection);
  assert.deepEqual(frame.replay.cursor, pin.replay.cursor, 'Admitted replay cursor changed');
  assert.equal(frame.replay.cursor.delivery_index, DELIVERY);
  assert(['paused', 'ended'].includes(frame.status), `Unexpected native status ${frame.status}`);
  assert(['paused', 'ended'].includes(frame.replay.status), `Unexpected replay status ${frame.replay.status}`);
}

// Read-only DOM/canvas sampling: no paint instrumentation, state injection,
// hidden-control manipulation, forced actions, or preference writes.
async function paintSample(locator) {
  return locator.evaluate(node => {
    const box = element => {
      const r = element.getBoundingClientRect();
      return { x: r.x, y: r.y, width: r.width, height: r.height };
    };
    const canvases = [...node.querySelectorAll('canvas')].filter(canvas => {
      const r = canvas.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && getComputedStyle(canvas).visibility !== 'hidden';
    }).map(canvas => {
      // Downsample an existing raster off-DOM, then read it once: no synchronous
      // GPU readback per pixel, and no mutation of the application canvas.
      const sample = new OffscreenCanvas(24, 24);
      const context = sample.getContext('2d', { willReadFrequently: true });
      const colors = new Set();
      let hash = 2166136261, opaqueSamples = 0;
      if (canvas.width && canvas.height) {
        context.drawImage(canvas, 0, 0, 24, 24);
        const pixels = context.getImageData(0, 0, 24, 24).data;
        for (let i = 0; i < pixels.length; i += 4) {
          if (pixels[i + 3]) { opaqueSamples++; colors.add(`${pixels[i]},${pixels[i + 1]},${pixels[i + 2]},${pixels[i + 3]}`); }
          for (let channel = 0; channel < 4; channel++) hash = Math.imul(hash ^ pixels[i + channel], 16777619) >>> 0;
        }
      }
      const r = canvas.getBoundingClientRect();
      const viewportArea = Math.max(0, Math.min(innerWidth, r.right) - Math.max(0, r.left))
        * Math.max(0, Math.min(innerHeight, r.bottom) - Math.max(0, r.top));
      return { css: box(canvas), width: canvas.width, height: canvas.height, viewportArea,
        sampledPixels: 576, opaqueSamples, distinctColors: colors.size, pixelFingerprint: hash.toString(16) };
    });
    return { attributes: Object.fromEntries([...node.attributes].filter(item => item.name.startsWith('data-')).map(item => [item.name, item.value])),
      canvases, empty: Boolean(node.querySelector('.price-chart__empty, .m4-chart-empty, .m4-chart-error, .canvas-overlay')) };
  });
}
async function ensurePaintVisible(locator) {
  await locator.evaluate(node => {
    const intersects = [...node.querySelectorAll('canvas')].some(canvas => {
      const r = canvas.getBoundingClientRect();
      return Math.max(0, Math.min(innerWidth, r.right) - Math.max(0, r.left))
        * Math.max(0, Math.min(innerHeight, r.bottom) - Math.max(0, r.top)) > 10000;
    });
    // Preserve the desktop shell and toolbar when paint already intersects.
    // If offscreen, scroll to the panel's beginning, not its inner plot center.
    if (!intersects) node.scrollIntoView({ block: 'start', inline: 'nearest', behavior: 'instant' });
  });
}
async function settledPaint(page, locator) {
  await ensurePaintVisible(locator);
  await page.mouse.move(0, 0);
  let last = '', repeats = 0;
  return until('meaningful canvas pixels settled across three samples', async () => {
    await page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));
    const sample = await paintSample(locator);
    const key = JSON.stringify(sample);
    repeats = key === last ? repeats + 1 : 1;
    last = key;
    const meaningful = !sample.empty && sample.canvases.some(canvas =>
      canvas.viewportArea > 10000 && canvas.opaqueSamples > 8 && canvas.distinctColors > 2);
    return meaningful && repeats >= 3 ? { ...sample, stableSamples: repeats, sampleSpacingMs: 100, meaningful } : null;
  });
}
async function priceReady(page, observed, pin) {
  const chart = page.getByTestId('price-chart');
  await ensurePaintVisible(chart);
  await page.waitForFunction(generation => {
    const node = document.querySelector('[data-testid=price-chart]');
    return node?.getAttribute('data-presented-source') === 'session_trades'
      && node.getAttribute('data-presented-scope') === 'primary'
      && node.getAttribute('data-presented-interval') === '1m'
      && node.getAttribute('data-positioning') === 'off'
      && node.getAttribute('data-presented-generation') === generation;
  }, pin.generation);
  const candles = await until('native primary session-trade candle DTO', () => [...observed.candles].reverse().find(body =>
    body.session_id === pin.session_id && body.generation === pin.generation && body.instrument_id === INSTRUMENT
    && body.instrument_ids?.length === 1 && body.instrument_ids[0] === INSTRUMENT
    && body.scope === 'primary' && body.source === 'session_trades' && body.interval === '1m'
    && body.status === 'ready' && body.candles?.length));
  return { kind: 'native-price', session_id: candles.session_id, generation: candles.generation,
    instrument_id: candles.instrument_id, scope: candles.scope, source: candles.source, interval: candles.interval,
    candleCount: candles.candles.length, first: candles.candles[0], last: candles.candles.at(-1),
    paint: await settledPaint(page, chart) };
}
async function liquidityReady(page, observed, pin) {
  const pane = page.getByTestId('chart-pane-heatmap');
  await pane.getByRole('combobox', { name: 'Chart mode', exact: true }).selectOption('heatmap');
  const chart = pane.getByTestId('market-charts');
  await ensurePaintVisible(chart);
  const evidence = await until('painted native resting-liquidity view and matching DTO', async () => {
    const dom = await chart.evaluate(node => {
      const freshness = node.querySelector('[data-testid=chart-freshness]');
      return { painted: node.getAttribute('data-painted-view-id'), drawn: node.getAttribute('data-drawn-view-id'),
        mode: node.getAttribute('data-drawn-mode'), tiles: Number(node.getAttribute('data-tile-count')),
        generation: freshness?.getAttribute('data-generation'), evidenceNs: freshness?.getAttribute('data-evidence-ns'),
        evidenceView: freshness?.getAttribute('data-view-id'), freshness: freshness?.textContent };
    });
    if (!dom.painted || dom.painted !== dom.drawn || dom.mode !== 'heatmap' || !dom.tiles
      || dom.generation !== pin.generation || !dom.evidenceNs || dom.evidenceView !== dom.painted) return null;
    const matched = [...observed.grids].reverse().find(({ request, body }) => request.session_id === pin.session_id
      && request.generation === pin.generation && body.generation === pin.generation
      && body.instrument_id === INSTRUMENT && body.view_id === dom.painted
      && body.columns?.some(column => column.eligible && column.cells.some(cell => cell.some(value => value !== null && Number(value) > 0))));
    if (!matched) return null;
    const { body: grid, request, status, receivedAt } = matched;
    return { kind: 'native-liquidity', presentation: dom,
      observedRequest: { path: '/api/v1/visualization', method: 'POST', payload: request },
      response: { status, receivedAt }, generation: grid.generation,
      instrument_id: grid.instrument_id, view_id: grid.view_id,
      start_ns: grid.start_ns, end_ns: grid.end_ns, eligibleColumns: grid.columns.filter(column => column.eligible).length };
  });
  const paint = await settledPaint(page, chart);
  assert.equal(paint.attributes['data-painted-view-id'], evidence.view_id);
  assert.equal(paint.attributes['data-drawn-view-id'], evidence.view_id);
  return { ...evidence, paint };
}
async function footprintReady(page, observed, pin) {
  const chart = page.getByRole('region', { name: 'Footprint', exact: true });
  await ensurePaintVisible(chart);
  const data = await until('retained native prints for display Footprint', () => [...observed.market].reverse().find(body =>
    body.session_id === pin.session_id && body.generation === pin.generation
    && body.trades?.some(trade => trade.instrument_id === INSTRUMENT)));
  const trades = data.trades.filter(trade => trade.instrument_id === INSTRUMENT);
  const visibleHeading = await chart.locator('header').innerText();
  assert(/BTC/.test(visibleHeading) && /Coinbase/.test(visibleHeading), 'Display Footprint does not identify the selected native market');
  return { kind: 'display-footprint', note: 'Display projection of retained native prints, not a second native visualization or an exchange candle feed',
    session_id: data.session_id, generation: data.generation, instrument_id: INSTRUMENT,
    retainedTrades: trades.length, firstNativeTrade: trades[0], lastNativeTrade: trades.at(-1),
    visibleHeading, paint: await settledPaint(page, chart) };
}

async function geometry(page) {
  return page.evaluate(() => {
    const doc = document.documentElement;
    return { viewport: { width: innerWidth, height: innerHeight, dpr: devicePixelRatio },
      document: { width: Math.max(doc.scrollWidth, document.body.scrollWidth), height: Math.max(doc.scrollHeight, document.body.scrollHeight) },
      horizontalOverflow: Math.max(0, doc.scrollWidth - innerWidth, document.body.scrollWidth - innerWidth),
      scroll: { x: scrollX, y: scrollY }, theme: doc.getAttribute('data-theme'),
      controls: [...document.querySelectorAll('select')].map(node => ({ name: node.getAttribute('aria-label'), value: node.value,
        selected: node.selectedOptions[0]?.textContent })),
      activeViews: [...document.querySelectorAll('[role=tab][aria-selected=true]')].map(node => node.textContent),
      canvases: [...document.querySelectorAll('canvas')].map(canvas => {
        const r = canvas.getBoundingClientRect();
        return { width: canvas.width, height: canvas.height, css: { x: r.x, y: r.y, width: r.width, height: r.height },
          backingScaleX: r.width ? canvas.width / r.width : null, backingScaleY: r.height ? canvas.height / r.height : null };
      }) };
  });
}
async function accessibilitySamples(page) {
  // Actual Tab traversal, with no activation; restore focus through the same
  // public Theme control afterwards. Samples deliberately do not gate contrast.
  await page.getByRole('combobox', { name: 'Theme', exact: true }).focus();
  const focus = [];
  for (let i = 0; i < 6; i++) {
    await page.keyboard.press('Tab');
    focus.push(await page.evaluate(() => {
      const node = document.activeElement, style = getComputedStyle(node), r = node.getBoundingClientRect();
      return { tag: node.tagName, role: node.getAttribute('role'), name: node.getAttribute('aria-label') || node.textContent?.trim().slice(0, 100),
        focusVisible: node.matches(':focus-visible'), outlineStyle: style.outlineStyle, outlineWidth: style.outlineWidth,
        outlineColor: style.outlineColor, boxShadow: style.boxShadow,
        rect: { x: r.x, y: r.y, width: r.width, height: r.height },
        withinViewport: r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight };
    }));
  }
  const contrast = await page.evaluate(() => {
    const parse = color => {
      const values = color.match(/[\d.]+/g)?.map(Number);
      return values?.length >= 3 ? [...values.slice(0, 3), values[3] ?? 1] : null;
    };
    const over = (fg, bg) => [0, 1, 2].map(i => fg[i] * fg[3] + bg[i] * (1 - fg[3])).concat(1);
    const luminance = color => color.slice(0, 3).map(value => {
      const c = value / 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    }).reduce((sum, value, i) => sum + value * [0.2126, 0.7152, 0.0722][i], 0);
    const ratio = (a, b) => {
      const aa = luminance(a), bb = luminance(b);
      return Number(((Math.max(aa, bb) + 0.05) / (Math.min(aa, bb) + 0.05)).toFixed(3));
    };
    const background = node => {
      const ancestors = [];
      for (let n = node; n; n = n.parentElement) ancestors.unshift(n);
      let color = [255, 255, 255, 1], unsupported = false;
      for (const n of ancestors) {
        const style = getComputedStyle(n), layer = parse(style.backgroundColor);
        if (!layer || style.backgroundImage !== 'none' || Number(style.opacity) !== 1) unsupported = true;
        if (layer) color = over(layer, color);
      }
      return { color, unsupported };
    };
    return ['.status', '.telemetry', '.price-chart__legend', 'select[aria-label="Theme"]',
      'select[aria-label="Layout preset"]', 'select[aria-label="All candle intervals"]', '[role=tab][aria-selected=true]'].map(selector => {
      const node = document.querySelector(selector);
      if (!node) return { selector, unavailable: 'not present' };
      const style = getComputedStyle(node), bg = background(node), outside = background(node.parentElement);
      const fg = parse(style.color), border = parse(style.borderTopColor), r = node.getBoundingClientRect();
      if (!fg || bg.unsupported || outside.unsupported) return { selector, unavailable: 'gradient, group opacity or unsupported color; no fabricated ratio' };
      const textRatio = ratio(over(fg, bg.color), bg.color);
      const largeText = parseFloat(style.fontSize) >= 24 || (parseFloat(style.fontSize) >= 18.66 && Number(style.fontWeight) >= 700);
      const borderRatio = border && style.borderTopStyle !== 'none' && parseFloat(style.borderTopWidth) > 0
        ? ratio(over(border, outside.color), outside.color) : null;
      return { selector, text: node.textContent?.trim().slice(0, 100), foreground: style.color, background: bg.color,
        fontSize: style.fontSize, fontWeight: style.fontWeight, textRatio, textThreshold: largeText ? 3 : 4.5,
        textPass: textRatio >= (largeText ? 3 : 4.5), controlBorderRatio: borderRatio,
        controlBorderThreshold: borderRatio === null ? null : 3,
        controlBorderPass: borderRatio === null ? null : borderRatio >= 3,
        rendered: r.width > 0 && r.height > 0,
        caveat: 'Computed foreground against composited solid ancestors; border is one control-boundary sample, not a complete non-text contrast verdict' };
    });
  });
  await page.getByRole('combobox', { name: 'Theme', exact: true }).focus();
  return { focus, contrast, gating: false };
}

async function capture(page, context, observed, run, pin, theme, view, readiness, accessibility) {
  const live = await getJSON(context, `/api/v1/sessions/${encodeURIComponent(pin.session_id)}`);
  assertPinned(live, pin);
  assertPinned(observed.frame, pin);
  assert.equal(run.nativeCreateRequests, 1, 'Expected exactly one native create per fresh viewport context');
  assert.equal(run.nativeCreateResponses.length, 1);
  await page.mouse.move(0, 0);
  await page.evaluate(() => document.fonts.ready);
  const measured = await geometry(page);
  assert.deepEqual(measured.viewport, { ...run.viewport, dpr: run.dpr });
  assert.equal(measured.theme, theme);
  // Preset is a command select, not persisted selection. Inspect the mounted
  // grid's logical inline geometry; responsive CSS may stack these same cells.
  measured.grid = await page.locator('[data-grid-panel]').evaluateAll(nodes => nodes.map(node => ({
    id: node.getAttribute('data-grid-panel'),
    x: Number(node.style.gridColumnStart) - 1, y: Number(node.style.gridRowStart) - 1,
    w: Number(node.style.gridColumnEnd.replace('span ', '')), h: Number(node.style.gridRowEnd.replace('span ', '')),
  })));
  assert.deepEqual(Object.fromEntries(measured.grid.map(({ id, ...cell }) => [id, cell])), {
    chart: { x: 0, y: 0, w: 9, h: 8 }, orderBook: { x: 9, y: 0, w: 3, h: 4 },
    obi: { x: 9, y: 4, w: 3, h: 2 }, timeSales: { x: 9, y: 6, w: 3, h: 5 },
    cvd: { x: 0, y: 8, w: 4, h: 3 }, volumeZ: { x: 4, y: 8, w: 5, h: 3 },
  }, 'Mounted panels must retain the balanced grid independently of chart navigation');
  assert.equal(await page.getByRole('combobox', { name: 'All candle intervals', exact: true }).inputValue(), '1m');
  assert(measured.document.height <= 20000 && measured.document.width <= 10000, 'Unbounded document geometry');
  const filename = `${phase}-${theme}-${run.viewport.width}x${run.viewport.height}-dpr${run.dpr}-${view}.jpg`;
  // Reserve paths; never replace previous evidence, including partial runs.
  await writeFile(join(output, filename), '', { flag: 'wx' });
  const bytes = await page.screenshot({ path: join(output, filename), type: 'jpeg', quality: 88, fullPage: false });
  report.captures.push({ filename, sha256: sha(bytes), bytes: bytes.length, capturedAt: new Date().toISOString(),
    comparisonKey: `${theme}/${run.viewport.width}x${run.viewport.height}/dpr${run.dpr}/${view}`,
    theme, view, source: live.source, scope: 'primary', instrument: INSTRUMENT, interval: '1m', layout: 'balanced',
    nativeCreateCount: run.nativeCreateRequests, admittedFrame: frameSummary(live), geometry: measured, readiness, accessibility });
  if (phase === 'after' && measured.horizontalOverflow > 0) report.failures.push({ filename,
    reason: 'horizontal-document-overflow', pixels: measured.horizontalOverflow });
  await persist();
}

let browser;
const deadline = setTimeout(() => {
  report.failures.push({ reason: 'collector exceeded bounded 15-minute deadline' });
  void browser?.close();
}, 15 * 60 * 1000);
try {
  browser = await chromium.launch({ headless: true, args: ['--disable-background-networking',
    '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1, EXCLUDE [::1]'] });
  report.browserVersion = browser.version();
  for (const { width, height, dpr } of contexts) {
    const context = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: dpr,
      locale: 'en-US', timezoneId: 'UTC', serviceWorkers: 'block', reducedMotion: 'reduce' });
    const run = { viewport: { width, height }, dpr, nativeCreateRequests: 0, nativeCreateResponses: [],
      httpErrors: [], notModifiedResponses: 0, responseReadErrors: [], pageErrors: [], unexpectedNetwork: [], requestFailures: [] };
    report.runs.push(run);
    const observed = { frame: null, candles: [], grids: [], market: [] }, pending = new Set();
    const page = await context.newPage();
    page.setDefaultTimeout(25000);
    page.setDefaultNavigationTimeout(30000);
    const acceptFrame = body => {
      if (body?.type !== 'frame') return;
      const old = observed.frame;
      if (!old || body.session_id !== old.session_id || BigInt(body.generation) > BigInt(old.generation)
        || body.generation === old.generation && BigInt(body.frame_id) >= BigInt(old.frame_id)) observed.frame = body;
    };
    const checkNetwork = address => {
      const url = new URL(address);
      if (['data:', 'blob:', 'about:'].includes(url.protocol)) return;
      const same = ['http:', 'ws:'].includes(url.protocol) && url.hostname === base.hostname && url.port === base.port;
      if (!same) {
        run.unexpectedNetwork.push(address);
        // Fail closed at the first observation. No route/response interception.
        void context.close();
      }
    };
    context.on('request', request => {
      checkNetwork(request.url());
      if (new URL(request.url()).pathname === '/api/v1/sessions' && request.method() === 'POST') run.nativeCreateRequests++;
    });
    page.on('websocket', socket => {
      checkNetwork(socket.url());
      socket.on('framereceived', event => {
        try { acceptFrame(JSON.parse(String(event.payload))); }
        catch (error) { run.responseReadErrors.push({ transport: 'websocket', error: String(error) }); }
      });
    });
    page.on('pageerror', error => run.pageErrors.push(String(error)));
    page.on('requestfailed', request => {
      if (run.requestFailures.length < 100) run.requestFailures.push({ path: new URL(request.url()).pathname, error: request.failure()?.errorText });
    });
    page.on('response', response => {
      const pathname = new URL(response.url()).pathname;
      if (!pathname.startsWith('/api/v1/')) return;
      const task = (async () => {
        if (response.status() === 304) { run.notModifiedResponses++; return; }
        if (response.status() >= 400) { run.httpErrors.push({ path: pathname, status: response.status() }); return; }
        if (!response.ok()) return;
        if (!['sessions', 'subscriptions', 'replay', 'candles', 'visualization', 'market-data'].includes(pathname.split('/').at(-1))) return;
        const body = await response.json();
        acceptFrame(body);
        if (pathname === '/api/v1/sessions' && response.request().method() === 'POST') run.nativeCreateResponses.push(frameSummary(body));
        if (pathname === '/api/v1/visualization' && Array.isArray(body.columns)) {
          assert.equal(response.request().method(), 'POST');
          observed.grids.push({ request: response.request().postDataJSON(), body,
            status: response.status(), receivedAt: new Date().toISOString() });
          if (observed.grids.length > 48) observed.grids.shift();
        } else {
          const target = Array.isArray(body.candles) ? observed.candles
            : pathname === '/api/v1/market-data' && body.trades?.some(trade => trade.instrument_id === INSTRUMENT)
              ? observed.market : null;
          // Empty drain/poll responses must not evict retained print evidence.
          if (target) { target.push(body); if (target.length > 48) target.shift(); }
        }
      })().catch(error => {
        if (run.responseReadErrors.length < 100) run.responseReadErrors.push({ path: pathname, error: String(error),
          note: 'Recorded, not silently classified; successful matching DTO and paint are separately required' });
      });
      pending.add(task); void task.finally(() => pending.delete(task));
    });
    try {
      const catalog = await getJSON(context, '/api/v1/instruments');
      const source = catalog.sources.find(item => item.id === SOURCE && item.mode === 'demo' && item.synthetic && item.status === 'ready');
      assert(source, `Required offline native source ${SOURCE} unavailable; no fallback`);
      const directory = await getJSON(context, `/api/v1/instruments?source_id=${encodeURIComponent(SOURCE)}&include_sources=false`);
      const instrument = directory.instruments.find(item => item.instrument_id === INSTRUMENT && item.venue === 'coinbase' && item.market_type === 'spot');
      assert(instrument, `Required native instrument ${INSTRUMENT} unavailable`);
      run.source = source; run.instrument = instrument;
      await page.goto(`${base.origin}/?source=${encodeURIComponent(SOURCE)}`, { waitUntil: 'domcontentloaded' });
      await ready(page, observed);
      if (observed.frame.selection.instrument_id !== INSTRUMENT) {
        const search = page.getByLabel('Search instruments', { exact: true });
        await reveal(search); await search.fill('BTC');
        for (const label of ['Filter venue', 'Filter quote', 'Filter market']) {
          await page.getByRole('combobox', { name: label, exact: true }).selectOption('');
        }
        await action(page, observed, 'subscriptions', () => page.getByTitle(INSTRUMENT, { exact: true }).click());
        const watchlist = search.locator('xpath=ancestor::details[1]');
        if (await watchlist.getAttribute('open') !== null) await watchlist.locator(':scope > summary').click();
      }
      const venue = page.getByRole('button', { name: /^Venue:/ });
      if (await venue.getAttribute('aria-label') !== 'Venue: Coinbase') {
        await venue.click();
        await action(page, observed, 'subscriptions', () => page.getByRole('listbox', { name: 'Venue', exact: true })
          .getByRole('option', { name: /^Coinbase(?:\s|$)/ }).click());
      }
      const seek = page.getByLabel('Seek delivery', { exact: true });
      await reveal(seek);
      assert(BigInt(observed.frame.replay.total_deliveries) >= BigInt(DELIVERY));
      await seek.fill(DELIVERY);
      const pin = await action(page, observed, 'replay', () => page.getByRole('button', { name: 'Seek', exact: true }).click());
      assertPinned(pin, pin); run.pinnedFrame = frameSummary(pin);
      await page.evaluate(() => document.fonts.ready);
      await page.getByRole('combobox', { name: 'Layout preset', exact: true }).selectOption('balanced');
      const workspace = page.getByRole('tablist', { name: 'Chart workspace', exact: true });
      await workspace.getByRole('tab', { name: 'Price action', exact: true }).click();
      await page.getByRole('button', { name: 'Session trades', exact: true }).click();
      await page.getByRole('combobox', { name: 'All candle intervals', exact: true }).selectOption('1m');
      const positioning = page.getByRole('button', { name: 'OI & funding', exact: true });
      if (await positioning.getAttribute('aria-pressed') === 'true') await positioning.click();
      const picker = page.getByRole('combobox', { name: 'Theme', exact: true });
      run.availableThemes = await picker.locator('option').evaluateAll(nodes => nodes.map(node => ({ id: node.value, label: node.textContent, disabled: node.disabled })));
      const selectedThemes = dpr === 2 ? ['dark-dull'] : phase === 'after' && width === 1440
        ? themes : ['dark-dull', 'light-dull'];
      for (const theme of selectedThemes) {
        assert(run.availableThemes.some(item => item.id === theme && !item.disabled), `Missing theme ${theme}`);
        await picker.selectOption(theme);
        const accessibility = ['dark-dull', 'light-dull'].includes(theme) ? await accessibilitySamples(page) : null;
        const readiness = await priceReady(page, observed, pin);
        await capture(page, context, observed, run, pin, theme, 'price', readiness, accessibility);
      }
      if (phase === 'after' && width === 1440 && dpr === 1) {
        await picker.selectOption('dark-dull');
        await workspace.getByRole('tab', { name: 'Liquidity & flow', exact: true }).click();
        await capture(page, context, observed, run, pin, 'dark-dull', 'liquidity', await liquidityReady(page, observed, pin), null);
        await workspace.getByRole('tab', { name: 'Footprint', exact: true }).click();
        await capture(page, context, observed, run, pin, 'dark-dull', 'footprint', await footprintReady(page, observed, pin), null);
      }
      assert.equal(run.nativeCreateRequests, 1);
      assert.equal(run.nativeCreateResponses.length, 1);
      assert.equal(run.pageErrors.length, 0, 'Browser runtime error');
      assert.equal(run.unexpectedNetwork.length, 0, 'Non-loopback traffic attempted');
    } finally {
      await context.close();
      await Promise.all([...pending]);
      await persist();
    }
  }
  assert.equal(report.captures.length, phase === 'before' ? 13 : 19);
  report.status = report.failures.length ? 'failed' : 'passed';
} catch (error) {
  report.status = 'failed';
  report.failures.push({ reason: String(error.stack || error) });
} finally {
  clearTimeout(deadline);
  await browser?.close();
  report.finishedAt = new Date().toISOString();
  report.nativeCreateCount = report.runs.reduce((sum, run) => sum + run.nativeCreateRequests, 0);
  await persist();
}
console.log(JSON.stringify({ status: report.status, manifest: manifestPath, images: report.captures.length,
  nativeCreates: report.nativeCreateCount }));
process.exitCode = report.status === 'passed' ? 0 : 1;
