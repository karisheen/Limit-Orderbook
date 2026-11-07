#!/usr/bin/env node
/** Descriptive browser measurement reproducer; real transport, no fixtures or routing. */
import { createRequire } from 'node:module';
import { open } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { platform, release } from 'node:os';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../../..');
const require = createRequire(`${ROOT}/frontend/package.json`);
const options = { url: null, output: null, screenshot: null, width: 1440, seconds: 20, speed: '2' };
const supplied = new Set();
for (let i = 2; i < process.argv.length; i += 2) {
  const name = process.argv[i]?.replace(/^--/, ''), value = process.argv[i + 1];
  if (!Object.hasOwn(options, name) || value === undefined || supplied.has(name)) throw new Error(`Invalid or repeated option ${process.argv[i]}`);
  supplied.add(name);
  options[name] = ['width', 'seconds'].includes(name) ? Number(value) : value;
}
if (!options.url || !options.output) throw new Error('--url and --output are required');
const origin = new URL(options.url);
if (origin.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(origin.hostname) || origin.username || origin.password || origin.pathname !== '/' || origin.search || origin.hash) throw new Error('--url must be a loopback HTTP origin');
if (![1440, 390].includes(options.width) || ![20, 30].includes(options.seconds) || options.speed !== '2') throw new Error('Use --width 1440/390, --seconds 20/30, --speed 2');
options.output = resolve(options.output);
if (options.screenshot) options.screenshot = resolve(options.screenshot);
if (options.screenshot === options.output) throw new Error('Screenshot and JSON paths must differ');
// Exclusive reservation: existing reports are never overwritten, including failures.
const output = await open(options.output, 'wx');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function bounded(promise, ms, label) {
  let timer;
  try { return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`${label}: ${ms}ms timeout`)), ms); })]); }
  finally { clearTimeout(timer); }
}
const quantiles = values => {
  const valid = values.filter(value => typeof value === 'number' && Number.isFinite(value) && value >= 0).sort((a, b) => a - b);
  const at = p => valid.length ? valid[Math.ceil(valid.length * p) - 1] : null;
  return { count: valid.length, invalid_count: values.length - valid.length, p50_ms: at(.5), p95_ms: at(.95), p99_ms: at(.99), max_ms: valid.at(-1) ?? null };
};
const report = {
  schema: 'lob-browser-comparison-descriptive-1', status: 'starting', certification: false,
  command: [process.execPath, ...process.argv.slice(1)], options,
  viewport: { width: options.width, height: 900 }, runtime: process.version, platform: `${platform()} ${release()}`,
  errors: [], frames: [], visualization_acks: [], interactions: [], session_cleanup: [],
  duplicate_frames: 0, duplicate_visualization_acks: 0, frame_acks_sent: [],
  query_requests: [],
  definitions: {
    quantiles: 'Nearest rank over finite nonnegative samples; missing is not zero.',
    frame_telemetry: 'Native WebSocket received frames, unique session_id/generation/frame_id; receipt in Node monotonic measurement window. Values are server snapshots and may repeat previous ACK observations in distinct frames.',
    publish_to_ack_upper_bound_ms: 'Server monotonic publication-to-client-ACK round trip upper bound; not one-way GPU latency.',
    browser_receive_to_paint_ms: 'Application browser receive through committed DOM and two rAF opportunities; frame and visualization cohorts stay separate.',
    observer_duration_ms: 'Backend observer duration reported in native frame telemetry, not client drawing time.',
    draw_ms: 'Unique painted pane/view IDs observed in the actual chart DOM; JavaScript Canvas submission duration, not GPU completion. Missing attributes are explicitly unavailable.',
    raf_interval_ms: 'Consecutive real requestAnimationFrame callback timestamps wholly inside the browser measurement interval.',
    input_issue_to_focus_2raf_ms: 'Node time immediately before an actual Playwright click through visible active-element confirmation and two rAF callbacks; includes automation and actionability waits.',
    input_browser_focus_to_2raf_ms: 'Browser focus event to two rAF opportunities; reported separately, never substituted for full interaction latency.',
    long_task_ms: 'PerformanceObserver longtask durations whose starts fall in the browser measurement interval.',
    query_requests: 'Actual visualization/ladder requests issued within the Node measurement window; response status, completed body, aborted transport, failed transport, and unresolved are separate. pane_id is copied only when present in the native payload.'
  },
  limitations: [
    'Descriptive local sample only: no certification, SLA, significance claim, real-market or live-capacity inference.',
    'Real synthetic demo data and real HTTP/WebSocket traffic; no data injection, routing, fake socket, wallet, collector or real orders.',
    'Fresh browser context does not evict OS caches. Headless rendering, observers, automation and concurrent host load affect timings.',
    'Node and browser clocks have different origins and are never subtracted. No GPU/pixel completion is measured.',
    'Frame telemetry snapshots may repeat a last-ACK value across unique frame IDs; these are snapshot distributions, not independent ACK event distributions.',
    'DOM draw sampling sees only observed painted view IDs; intermediate views can be missed. Both visible chart panes contribute when present.',
    'Interaction focus is DOM/style evidence, not pixel-level focus-ring verification. Clicking Seek delivery does not change its value or source.',
    'CDP duration deltas are renderer counters, not whole-machine CPU usage; heap delta is retained heap, not allocation volume.',
    'Before/current dist comparisons use the same backend implementation, not the original historical backend; compare sequential runs at the same viewport/duration.'
  ]
};
let browser, context, page, cdp, measuring = false, latestFrame = null, failed = false, stopping = false;
const sessions = new Set(), frameIds = new Set(), ackIds = new Set(), sentAckIds = new Set(), tasks = new Set();
const queryRequests = new WeakMap();
const addError = (kind, detail) => { if (report.errors.length < 200) report.errors.push({ kind, detail: String(detail).slice(0, 4000), during_measurement: measuring }); else report.errors_omitted = (report.errors_omitted ?? 0) + 1; };
function task(fn) {
  const promise = bounded(Promise.resolve().then(fn), 10000, 'Response observation').catch(error => addError('observation', error)).finally(() => tasks.delete(promise));
  tasks.add(promise);
}
const summaryFrame = frame => ({ session_id: frame.session_id, generation: frame.generation, frame_id: frame.frame_id, source: frame.source, instrument_id: frame.selection?.instrument_id, status: frame.status, replay: frame.replay, health: frame.health });
async function api(path, init = {}) {
  const response = await fetch(new URL(path, origin), { ...init, signal: AbortSignal.timeout(10000), redirect: 'error' });
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}
async function until(predicate, label, ms = 60000) {
  const end = performance.now() + ms;
  while (performance.now() < end) { if (stopping) throw new Error('Workload stopping'); if (await predicate()) return; await sleep(100); }
  throw new Error(`Timed out: ${label}`);
}
async function metrics() {
  if (!cdp) return null;
  try { return Object.fromEntries((await bounded(cdp.send('Performance.getMetrics'), 5000, 'CDP metrics')).metrics.map(({ name, value }) => [name, value])); }
  catch (error) { report.limitations.push(`CDP metrics unavailable: ${error}`); return null; }
}
async function run() {
  let catalog;
  await until(async () => {
    catalog = await api('/api/v1/instruments');
    const source = catalog.sources?.find(item => item.id === catalog.preferred_source_id);
    if (!source || source.mode !== 'demo' || source.synthetic !== true) throw new Error('API preferred source is not synthetic demo; refusing live/unknown startup');
    if (source.status === 'unavailable') throw new Error(`Synthetic source unavailable: ${JSON.stringify(source.reasons)}`);
    return source.status === 'ready';
  }, 'ready API-preferred synthetic demo', 60000);
  report.source = catalog.sources.find(item => item.id === catalog.preferred_source_id);
  const { chromium } = require(`${ROOT}/frontend/node_modules/@playwright/test`);
  // Default headless launch uses the already-installed Playwright Chromium shell.
  // No install/download, custom browser, persistent profile, or external CDP target.
  browser = await chromium.launch({ headless: true, timeout: 30000 });
  report.browser_version = browser.version();
  context = await browser.newContext({ viewport: report.viewport, deviceScaleFactor: 1, serviceWorkers: 'block' });
  page = await context.newPage();
  page.setDefaultTimeout(15000);
  page.setDefaultNavigationTimeout(60000);
  page.on('pageerror', error => addError('pageerror', error));
  page.on('console', message => { if (message.type() === 'error') addError('console', message.text()); });
  page.on('requestfailed', request => { if (!stopping) addError('requestfailed', `${request.method()} ${request.url()} ${request.failure()?.errorText}`); });
  page.on('websocket', socket => {
    socket.on('socketerror', error => addError('websocket', error));
    socket.on('framereceived', event => {
      try {
        const frame = JSON.parse(typeof event.payload === 'string' ? event.payload : event.payload.toString());
        if (frame.type !== 'frame') return;
        sessions.add(frame.session_id);
        latestFrame = frame;
        if (!measuring) return;
        const key = `${frame.session_id}:${frame.generation}:${frame.frame_id}`;
        if (frameIds.has(key)) { report.duplicate_frames++; return; }
        frameIds.add(key);
        report.frames.push({ received_at_node_ms: performance.now(), ...summaryFrame(frame), telemetry: frame.telemetry });
        if (frame.source?.id !== report.source.id || frame.source?.mode !== 'demo' || frame.selection?.instrument_id !== 'binance-global:spot-v3:spot:BTCUSDT') addError('frame_identity', key);
      } catch (error) { addError('websocket_decode', error); }
    });
    socket.on('framesent', event => {
      if (!measuring) return;
      try {
        const value = JSON.parse(typeof event.payload === 'string' ? event.payload : event.payload.toString());
        if (value.type !== 'ack') return;
        const key = `${socket.url()}:${value.frame_id}`;
        if (!sentAckIds.has(key)) { sentAckIds.add(key); report.frame_acks_sent.push({ sent_at_node_ms: performance.now(), ...value }); }
      } catch (error) { addError('ack_decode', error); }
    });
  });
  const ackRequests = new WeakMap();
  page.on('requestfinished', request => {
    const record = queryRequests.get(request);
    if (record) { record.state = 'completed'; record.settled_at_node_ms = performance.now(); }
  });
  page.on('requestfailed', request => {
    const record = queryRequests.get(request);
    if (record) {
      record.error = request.failure()?.errorText ?? 'unknown failure';
      record.state = /ABORTED|cancelled|canceled/i.test(record.error) ? 'aborted' : 'failed';
      record.settled_at_node_ms = performance.now();
      record.during_cleanup = stopping;
    }
  });
  page.on('request', request => {
    const path = new URL(request.url()).pathname;
    if (measuring && ['/api/v1/visualization', '/api/v1/ladder'].includes(path)) {
      let payload = null;
      try { payload = request.postDataJSON(); } catch (error) { addError('query_payload', error); }
      const record = { path, method: request.method(), issued_at_node_ms: performance.now(), pane_id: payload?.pane_id ?? null, state: 'issued', status: null, session_id: payload?.session_id ?? null, generation: payload?.generation ?? null };
      queryRequests.set(request, record);
      report.query_requests.push(record);
    }
    if (!measuring || !request.url().endsWith('/api/v1/visualization/ack')) return;
    try { ackRequests.set(request, { issued_at_node_ms: performance.now(), ...request.postDataJSON() }); }
    catch (error) { addError('visual_ack_request', error); }
  });
  page.on('response', response => {
    const path = new URL(response.url()).pathname;
    const query = queryRequests.get(response.request());
    if (query) { query.status = response.status(); query.response_at_node_ms = performance.now(); }
    if (!path.startsWith('/api/v1/')) return;
    if (!response.ok()) addError('http', `${response.status()} ${path}`);
    if (path === '/api/v1/sessions' && response.request().method() === 'POST' && response.ok()) task(async () => { const frame = await response.json(); sessions.add(frame.session_id); });
    const request = ackRequests.get(response.request());
    if (request) task(async () => {
      const body = await response.json();
      const key = `${request.session_id}:${request.generation}:${request.view_id}`;
      if (ackIds.has(key)) { report.duplicate_visualization_acks++; return; }
      ackIds.add(key);
      report.visualization_acks.push({ request, status: response.status(), response: body });
    });
  });
  await page.addInitScript(() => {
    const state = { active: false, start_ms: null, end_ms: null, raf: [], long_tasks: [], draws: [], missing_draw: [], last: null, seen: new Set(), long_task_supported: false, dropped: 0 };
    const put = (array, value) => { if (array.length < 20000) array.push(value); else state.dropped++; };
    const collectLong = entries => { for (const entry of entries) if (state.start_ms !== null && entry.startTime >= state.start_ms && (state.end_ms === null || entry.startTime < state.end_ms)) put(state.long_tasks, { start_ms: entry.startTime, duration_ms: entry.duration }); };
    try { state.observer = new PerformanceObserver(list => collectLong(list.getEntries())); state.observer.observe({ type: 'longtask', buffered: false }); state.long_task_supported = true; } catch { /* Report unsupported explicitly. */ }
    const frame = stamp => {
      if (state.active) {
        if (state.last !== null) put(state.raf, stamp - state.last);
        state.last = stamp;
        for (const chart of document.querySelectorAll('[data-testid="market-charts"]')) {
          const rect = chart.getBoundingClientRect();
          if (!rect.width || !rect.height || rect.bottom <= 0 || rect.top >= innerHeight || rect.right <= 0 || rect.left >= innerWidth) continue;
          const view = chart.dataset.paintedViewId, pane = chart.closest('[data-testid^="chart-pane-"]')?.getAttribute('data-testid') ?? 'unknown';
          if (!view || state.seen.has(`${pane}:${view}`)) continue;
          state.seen.add(`${pane}:${view}`);
          // Discover actual DOM evidence rather than inventing a timing from ACK latency.
          const attribute = chart.getAttributeNode('data-draw-ms');
          const value = attribute && attribute.value !== '' ? Number(attribute.value) : null;
          if (chart.dataset.viewId !== view) { state.seen.delete(`${pane}:${view}`); continue; }
          if (value !== null && Number.isFinite(value) && value >= 0) put(state.draws, { pane, view_id: view, draw_ms: value, attribute: attribute.name, observed_at_browser_ms: performance.now() });
          else put(state.missing_draw, { pane, view_id: view });
        }
      }
      state.rafId = requestAnimationFrame(frame);
    };
    state.rafId = requestAnimationFrame(frame);
    state.stop = () => {
      state.active = false; state.end_ms = performance.now(); cancelAnimationFrame(state.rafId);
      if (state.observer) { collectLong(state.observer.takeRecords()); state.observer.disconnect(); }
      const { observer, seen, stop, ...data } = state;
      return data;
    };
    window.__lobComparison = state;
  });
  // API preference is authoritative; never use a guessed ?source=demo alias.
  await page.goto(origin.href);
  await page.locator('.status').filter({ hasText: /Connected/ }).waitFor({ timeout: 60000 });
  await until(() => latestFrame?.source?.id === report.source.id && latestFrame?.source?.mode === 'demo', 'real synthetic source frame');
  const watchlist = page.getByRole('button', { name: 'Watchlist', exact: true });
  if (await watchlist.getAttribute('aria-pressed') !== 'true') await watchlist.click();
  await page.getByLabel('Search instruments').fill('BTCUSDT');
  const instrument = page.locator('[data-instrument-id="binance-global:spot-v3:spot:BTCUSDT"]');
  await instrument.click();
  await until(() => latestFrame?.selection?.instrument_id === 'binance-global:spot-v3:spot:BTCUSDT', 'exact native instrument selection');
  const seekResponse = page.waitForResponse(response => response.url().endsWith('/api/v1/replay') && response.request().postDataJSON()?.action === 'seek' && response.ok(), { timeout: 45000 });
  await page.getByLabel('Seek delivery').fill('2000');
  await page.getByRole('button', { name: 'Seek', exact: true }).click();
  const sought = await (await seekResponse).json();
  if (sought.replay?.cursor?.delivery_index !== '2000') throw new Error('Seek API did not return actual delivery 2000');
  await until(() => latestFrame?.replay?.cursor?.delivery_index === '2000' && latestFrame?.health?.ready === true && latestFrame.status !== 'playing', 'paused, eligible source cursor 2000');
  await page.waitForFunction(() => document.querySelector('.cursor')?.textContent.trim().startsWith('2000 /'));
  if (await watchlist.getAttribute('aria-pressed') === 'true') await watchlist.click();
  const domToggle = page.getByRole('button', { name: 'DOM / tape', exact: true });
  if (await domToggle.count() && await domToggle.getAttribute('aria-pressed') !== 'true') await domToggle.click();
  const mobileTabs = page.getByRole('tablist', { name: 'Market panes' });
  if (await mobileTabs.isVisible()) await mobileTabs.getByRole('tab', { name: 'Book', exact: true }).click();
  await page.getByTestId('grouped-ladder').locator('tbody tr[data-side]').first().waitFor({ state: 'visible', timeout: 45000 });
  report.eligible_dom = { health: latestFrame.health, row_count: await page.getByTestId('grouped-ladder').locator('tbody tr[data-side]').count(), observed_at_delivery: latestFrame.replay.cursor.delivery_index };
  if (await mobileTabs.isVisible()) await mobileTabs.getByRole('tab', { name: 'Liquidity', exact: true }).click();
  await page.waitForFunction(() => {
    const chart = document.querySelector('[data-testid="chart-pane-heatmap"] [data-testid="market-charts"]');
    return Boolean(chart?.dataset.paintedViewId && chart.dataset.paintedViewId === chart.dataset.viewId && chart.getBoundingClientRect().width > 0);
  }, null, { timeout: 45000 });
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  report.fixed_delivery = summaryFrame(await api(`/api/v1/sessions/${latestFrame.session_id}`));
  if (report.fixed_delivery.replay?.cursor?.delivery_index !== '2000' || report.fixed_delivery.status === 'playing') throw new Error('Screenshot fence is not paused delivery 2000');
  report.fixed_layout = await page.evaluate(() => {
    const bounds = element => {
      if (!element) return null;
      const rect = element.getBoundingClientRect();
      return { x: rect.x, document_y: rect.y + scrollY, width: rect.width, height: rect.height };
    };
    return {
      width: innerWidth, height: innerHeight, scroll_width: document.documentElement.scrollWidth,
      document_height: document.documentElement.scrollHeight, scroll_y: scrollY,
      chrome: Object.fromEntries(['.topbar', '.source-banner', '.replay-controls', '.market-pane-tabs'].map(selector => [selector, bounds(document.querySelector(selector))])),
      charts: [...document.querySelectorAll('[data-testid="market-charts"]')].map(element => ({ ...element.dataset, bounds: bounds(element) })),
      book: bounds(document.querySelector('.dom-pane')), tape: bounds(document.querySelector('.tape-panel'))
    };
  });
  if (options.screenshot) { await page.screenshot({ path: options.screenshot, fullPage: false, timeout: 15000 }); report.screenshot = { path: options.screenshot, delivery_index: '2000', paused: true }; }
  const speedResponse = page.waitForResponse(response => response.url().endsWith('/api/v1/replay') && response.request().postDataJSON()?.action === 'speed' && response.ok());
  await page.getByLabel('Replay speed').selectOption(options.speed);
  await speedResponse;
  try { cdp = await context.newCDPSession(page); await bounded(cdp.send('Performance.enable'), 5000, 'CDP enable'); }
  catch (error) { report.limitations.push(`CDP unavailable: ${error}`); }
  report.cdp_before = await metrics();
  report.cursor_before_play = summaryFrame(latestFrame);
  await page.evaluate(() => {
    const state = window.__lobComparison;
    // Exclude pre-existing painted views from the measured draw cohort.
    for (const chart of document.querySelectorAll('[data-testid="market-charts"]')) {
      const pane = chart.closest('[data-testid^="chart-pane-"]')?.getAttribute('data-testid') ?? 'unknown';
      if (chart.dataset.paintedViewId) state.seen.add(`${pane}:${chart.dataset.paintedViewId}`);
    }
    state.start_ms = performance.now(); state.last = null; state.active = true;
  });
  report.window_start_node_ms = performance.now(); measuring = true;
  const playResponse = page.waitForResponse(response => response.url().endsWith('/api/v1/replay') && response.request().postDataJSON()?.action === 'play' && response.ok());
  await page.getByTestId('replay-play').click();
  report.play_response = summaryFrame(await (await playResponse).json());
  if (report.play_response.status !== 'playing' || report.play_response.replay?.speed !== '2') throw new Error('Replay did not enter requested playing speed');
  const end = report.window_start_node_ms + options.seconds * 1000;
  // Twenty focus interactions distributed across the run. Search is deliberately
  // closed; Seek delivery is a visible non-mutating input with no source changes.
  const focusInput = page.getByLabel('Seek delivery');
  for (let index = 0; index < 20; index++) {
    const target = report.window_start_node_ms + (index + 1) * options.seconds * 1000 / 21;
    if (performance.now() < target) await sleep(target - performance.now());
    if (performance.now() >= end) { report.limitations.push('Measurement window ended before all 20 focus interactions; no missing interactions were fabricated.'); break; }
    const before = await focusInput.inputValue();
    await focusInput.evaluate(element => {
      element.blur();
      window.__lobFocus = { event_ms: null };
      element.addEventListener('focus', () => { window.__lobFocus.event_ms = performance.now(); }, { once: true });
    });
    const issued = performance.now();
    await focusInput.click({ timeout: Math.max(1, Math.min(5000, Math.ceil(end - issued))) });
    const evidence = await bounded(focusInput.evaluate(element => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => {
      const rect = element.getBoundingClientRect(), style = getComputedStyle(element);
      resolve({ focused: document.activeElement === element, visible: rect.width > 0 && rect.height > 0 && rect.top >= 0 && rect.bottom <= innerHeight, value: element.value, event_ms: window.__lobFocus.event_ms, end_ms: performance.now(), outline: style.outline, border: style.border, focus_visible: element.matches(':focus-visible') });
    })))), 5000, 'Focus two-rAF paint opportunity');
    const settled = performance.now();
    report.interactions.push({ index, input_issue_to_focus_2raf_ms: settled - issued, input_browser_focus_to_2raf_ms: evidence.event_ms === null ? null : evidence.end_ms - evidence.event_ms, issued_at_node_ms: issued, settled_at_node_ms: settled, crossed_window_end: settled > end, evidence });
    if (!evidence.focused || !evidence.visible || evidence.value !== before) throw new Error('Focus interaction changed input or lacked visible focus');
  }
  if (performance.now() < end) await sleep(end - performance.now());
  measuring = false; report.window_end_node_ms = performance.now();
  report.browser_samples = await page.evaluate(() => window.__lobComparison.stop());
  report.cdp_after = await metrics();
  report.cursor_after_run = summaryFrame(latestFrame);
  const pauseResponse = page.waitForResponse(response => response.url().endsWith('/api/v1/replay') && response.request().postDataJSON()?.action === 'pause' && response.ok());
  await page.getByTestId('replay-pause').click();
  report.paused_after_run = summaryFrame(await (await pauseResponse).json());
  await bounded(Promise.all([...tasks]), 12000, 'Observation drain');
  const queryDrainDeadline = performance.now() + 10000;
  while (report.query_requests.some(record => record.state === 'issued') && performance.now() < queryDrainDeadline) await sleep(50);
  for (const record of report.query_requests) if (record.state === 'issued') record.unresolved_before_cleanup = true;
  const endCursor = report.cursor_after_run.replay?.cursor?.delivery_index;
  report.progress = { from: '2000', to: endCursor, delivery_delta: endCursor && /^\d+$/.test(endCursor) ? String(BigInt(endCursor) - 2000n) : null, actual_window_node_ms: report.window_end_node_ms - report.window_start_node_ms };
  if (!endCursor || BigInt(endCursor) <= 2000n) throw new Error('No source cursor progress observed');
  report.status = 'completed_descriptive_sample';
}
const onSignal = signal => { failed = true; addError('signal', signal); stopping = true; if (page) void page.close().catch(() => {}); };
const sigint = () => onSignal('SIGINT'), sigterm = () => onSignal('SIGTERM');
process.on('SIGINT', sigint); process.on('SIGTERM', sigterm);
try { await bounded(run(), 300000, 'Whole browser workload'); }
catch (error) { failed = true; report.status = 'failed'; addError('workload', error.stack ?? error); }
finally {
  measuring = false; stopping = true;
  if (page && !page.isClosed() && !report.browser_samples) {
    try { report.browser_samples = await bounded(page.evaluate(() => window.__lobComparison?.stop()), 3000, 'Final browser samples'); } catch (error) { addError('final_samples', error); }
  }
  for (const [label, close] of [['cdp', () => cdp?.detach()], ['page', () => page?.close()], ['context', () => context?.close()], ['browser', () => browser?.close()]]) {
    try { await bounded(Promise.resolve().then(close), 10000, `Closing ${label}`); } catch (error) { failed = true; addError('cleanup', error); }
  }
  // Drain late session-creation observations before explicitly releasing every
  // document-owned session, including reconnects. Never delete unrelated owners.
  try { await bounded(Promise.all([...tasks]), 12000, 'Final observation drain'); } catch (error) { addError('cleanup', error); }
  for (const id of sessions) {
    if (!/^[a-f0-9]+$/.test(id)) continue;
    try {
      const response = await fetch(new URL(`/api/v1/sessions/${id}`, origin), { method: 'DELETE', redirect: 'error', signal: AbortSignal.timeout(5000) });
      report.session_cleanup.push({ id, status: response.status, released_or_already_absent: response.ok || response.status === 404 });
      if (!response.ok && response.status !== 404) failed = true;
    } catch (error) { failed = true; addError('session_cleanup', `${id}: ${error}`); }
  }
  const samples = report.browser_samples;
  const telemetryFields = ['publish_to_ack_upper_bound_ms', 'browser_receive_to_paint_ms', 'observer_duration_ms', 'queue_wait_ms'];
  report.summary = {
    raf_interval_ms: quantiles(samples?.raf ?? []), long_task_ms: quantiles((samples?.long_tasks ?? []).map(item => item.duration_ms)),
    unique_painted_view_draw_ms: quantiles((samples?.draws ?? []).map(item => item.draw_ms)),
    unique_painted_views_missing_draw_ms: samples?.missing_draw?.length ?? 0,
    native_frame_snapshots: Object.fromEntries(telemetryFields.map(field => [field, { ...quantiles(report.frames.map(frame => frame.telemetry?.[field]).filter(value => value !== null && value !== undefined)), missing_count: report.frames.filter(frame => frame.telemetry?.[field] == null).length }])),
    native_frame_ack_sent_browser_receive_to_paint_ms: quantiles(report.frame_acks_sent.map(ack => ack.browser_receive_to_paint_ms)),
    visualization_ack_response: Object.fromEntries(['publish_to_ack_upper_bound_ms', 'browser_receive_to_paint_ms'].map(field => [field, quantiles(report.visualization_acks.filter(ack => ack.status >= 200 && ack.status < 300).map(ack => ack.response[field]).filter(value => value != null))])),
    input_issue_to_focus_2raf_ms: quantiles(report.interactions.map(item => item.input_issue_to_focus_2raf_ms)),
    input_browser_focus_to_2raf_ms: quantiles(report.interactions.map(item => item.input_browser_focus_to_2raf_ms).filter(value => value !== null)),
    unique_native_frames: report.frames.length, unique_visualization_ack_responses: report.visualization_acks.length,
    actual_interaction_count: report.interactions.length, long_task_supported: samples?.long_task_supported ?? false
  };
  report.query_summary = [];
  for (const path of ['/api/v1/visualization', '/api/v1/ladder']) {
    const records = report.query_requests.filter(record => record.path === path);
    for (const pane of [undefined, ...new Set(records.map(record => record.pane_id))]) {
      const group = pane === undefined ? records : records.filter(record => record.pane_id === pane);
      report.query_summary.push({
        path, scope: pane === undefined ? 'all_panes' : 'payload_pane_id', pane_id: pane ?? null,
        issued: group.length, completed: group.filter(record => record.state === 'completed').length,
        aborted: group.filter(record => record.state === 'aborted').length,
        failed: group.filter(record => record.state === 'failed').length,
        unresolved: group.filter(record => record.state === 'issued').length,
        unresolved_before_cleanup: group.filter(record => record.unresolved_before_cleanup).length,
        aborted_during_cleanup: group.filter(record => record.state === 'aborted' && record.during_cleanup).length,
        statuses: Object.fromEntries([...new Set(group.map(record => record.status).filter(status => status !== null))].map(status => [status, group.filter(record => record.status === status).length]))
      });
    }
  }
  report.queue_counters = Object.fromEntries(['queue_depth', 'queue_highwater', 'coalesced', 'overflows', 'ack_expired'].map(field => {
    const values = report.frames.map(frame => frame.telemetry?.[field]).filter(value => value !== null && value !== undefined).map(Number).filter(Number.isFinite);
    return [field, { count: values.length, first: values[0] ?? null, last: values.at(-1) ?? null, max: values.length ? Math.max(...values) : null, delta: values.length ? values.at(-1) - values[0] : null }];
  }));
  if (!report.summary.unique_painted_view_draw_ms.count) report.limitations.push('No finite draw_ms attribute was observed for a new painted view; draw latency is unavailable, not zero.');
  if (!samples?.long_task_supported) report.limitations.push('Long Tasks API unavailable; no long-task absence claim.');
  if (report.cdp_before && report.cdp_after) {
    const names = ['TaskDuration', 'ScriptDuration', 'LayoutDuration', 'RecalcStyleDuration', 'JSHeapUsedSize', 'JSHeapTotalSize'];
    report.cdp_deltas = Object.fromEntries(names.filter(name => Number.isFinite(report.cdp_before[name]) && Number.isFinite(report.cdp_after[name])).map(name => [name, { before: report.cdp_before[name], after: report.cdp_after[name], delta: report.cdp_after[name] - report.cdp_before[name], unit: name.endsWith('Duration') ? 'seconds' : 'bytes' }]));
  }
  if (failed) report.status = 'failed';
  report.finished_at = new Date().toISOString();
  try { await output.writeFile(JSON.stringify(report, null, 2) + '\n'); } finally { await output.close(); }
  process.off('SIGINT', sigint); process.off('SIGTERM', sigterm);
  if (failed) process.exitCode = 1;
}
