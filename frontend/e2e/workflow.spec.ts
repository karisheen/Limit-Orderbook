import { expect, test as base } from '@playwright/test';
import type { Locator, Page, Response } from '@playwright/test';
import type { Catalog, Frame, Instrument } from '../src/api';
import type {
  AlertEvidenceResponse, AlertsResponse, ContextResponse, JournalPutResponse, JournalResponse,
  PaperResponse, PaperRunsResponse, ResearchResponse,
} from '../src/workflow-api';

// Real synthetic-demo v3, native persistence and economics only. Explicit starting
// funds/fees below are simulation inputs, not fabricated API balances or venue fees.
// Every test owns its sessions; durable records are addressed by returned IDs, never
// by global counts, list position, or an assumption that the server store is empty.
const test = base.extend<{ cleanWorkflow: void }>({
  cleanWorkflow: [async ({ page, request }, use) => {
    const sessions = new Set<string>();
    const creations: Promise<void>[] = [];
    page.on('response', response => {
      if (response.url().endsWith('/api/v1/sessions') && response.request().method() === 'POST' && response.ok()) {
        creations.push(response.json().then((frame: Frame) => { sessions.add(frame.session_id); }));
      }
    });
    await page.addInitScript(() => localStorage.removeItem('limit-orderbook.workspace.v1'));
    try { await use(); }
    finally {
      // Stop the document before deleting sessions so its reconnect loop cannot
      // create a replacement during cleanup. Pagehide may already have deleted one.
      await page.close();
      await Promise.all(creations);
      for (const id of sessions) {
        const response = await request.delete(`/api/v1/sessions/${id}`);
        expect(response.ok() || response.status() === 404).toBe(true);
      }
    }
  }, { auto: true }],
});

const nextResponse = (page: Page, endpoint: string, matches: (input: Record<string, unknown>) => boolean = () => true) =>
  page.waitForResponse(response => response.url().endsWith(`/api/v1/${endpoint}`)
    && response.request().method() === 'POST' && matches(response.request().postDataJSON() as Record<string, unknown>));

async function body<T>(pending: Promise<Response>): Promise<T> {
  const response = await pending;
  expect(response.ok(), `${response.status()} ${response.url()}: ${await response.text()}`).toBe(true);
  return await response.json() as T;
}

async function panel(page: Page, name: 'Alerts' | 'Context' | 'Paper' | 'Journal' | 'Research') {
  const drawer = page.getByRole('button', { name: 'Costs / analytics', exact: true });
  if (await drawer.getAttribute('aria-pressed') !== 'true') await drawer.click();
  await page.getByRole('tablist', { name: 'Research pane', exact: true }).getByRole('tab', { name, exact: true }).click();
  const pane = page.getByRole('tabpanel', { name, exact: true });
  await expect(pane).toBeVisible();
  return pane;
}

async function openDemo(page: Page) {
  const created = nextResponse(page, 'sessions');
  const catalogResponse = page.waitForResponse(response => response.url().endsWith('/api/v1/instruments') && response.request().method() === 'GET');
  await page.goto('/');
  let frame = await body<Frame>(created);
  const catalog = await body<Catalog>(catalogResponse);
  expect(frame.source.mode).toBe('demo');
  expect(frame.source.synthetic).toBe(true);
  await expect(page.locator('.status')).toHaveText('Connected');
  await expect(page.locator('.source-banner')).toContainText('SYNTHETIC DEMO');
  await page.getByRole('button', { name: 'Costs / analytics', exact: true }).click();
  for (const name of ['Alerts', 'Context', 'Paper', 'Journal', 'Research']) {
    await expect(page.getByRole('tablist', { name: 'Research pane', exact: true }).getByRole('tab', { name, exact: true })).toBeVisible();
  }
  const instrument = catalog.instruments.find(item => item.venue === 'binance-global' && item.market_type === 'spot' && item.base.ticker === 'ETH');
  if (!instrument) throw new Error('Native synthetic demo must expose its ETH spot instrument');
  await page.getByLabel('Search instruments').fill('ETH');
  const selected = nextResponse(page, 'subscriptions', input => input.instrument_id === instrument.instrument_id);
  await page.getByTitle(instrument.instrument_id, { exact: true }).click();
  frame = await body<Frame>(selected);
  expect(frame.selection.instrument_id).toBe(instrument.instrument_id);
  await expect(page.getByTitle(instrument.instrument_id, { exact: true })).toHaveAttribute('aria-pressed', 'true');
  expect(frame.book?.instrument_id).toBe(instrument.instrument_id);
  expect(BigInt(frame.replay!.cursor.delivery_index)).toBeGreaterThan(0n);
  return { frame, instrument };
}

async function seek(page: Page, delivery: string) {
  const response = nextResponse(page, 'replay', input => input.action === 'seek' && input.delivery_index === delivery);
  await page.getByLabel('Seek delivery', { exact: true }).fill(delivery);
  await page.getByRole('button', { name: 'Seek', exact: true }).click();
  const frame = await body<Frame>(response);
  expect(frame.replay?.cursor.delivery_index).toBe(delivery);
  await expect(page.locator('.cursor')).toContainText(`${delivery} /`);
  return frame;
}

async function readPaper(page: Page, pane: Locator, generation?: string) {
  const response = nextResponse(page, 'paper/query', input => generation === undefined || input.generation === generation);
  await pane.getByRole('button', { name: 'Refresh native state', exact: true }).click();
  return body<PaperResponse>(response);
}

async function configure(page: Page, frame: Frame, instrument: Instrument) {
  const initial = nextResponse(page, 'paper/query');
  const pane = await panel(page, 'Paper');
  const before = await body<PaperResponse>(initial);
  expect(before.snapshot.run).toBeNull();
  // No inferred funds, fees, fee date or latency; even zero is explicitly typed.
  await expect(pane.getByLabel('Starting available — exact', { exact: true })).toHaveValue('');
  await expect(pane.getByLabel('Fee basis points — exact', { exact: true })).toHaveValue('');
  await expect(pane.getByLabel('Feed latency (ns)', { exact: true })).toHaveValue('');
  await pane.getByLabel('Venue', { exact: true }).fill(instrument.venue);
  await pane.getByLabel('Asset ID', { exact: true }).fill(instrument.quote.asset_id);
  await pane.getByLabel('Starting available — exact', { exact: true }).fill('1000');
  await pane.getByLabel('Balance provenance', { exact: true }).fill('Explicit E2E simulation allocation; not a real account balance');
  await pane.getByRole('combobox', { name: 'Fee instrument', exact: true }).selectOption(instrument.instrument_id);
  await pane.getByLabel('Fee basis points — exact', { exact: true }).fill('10');
  await pane.getByLabel('Fee asset ID', { exact: true }).fill(instrument.quote.asset_id);
  await pane.getByLabel('Fee source / provenance', { exact: true }).fill('Explicit E2E simulation assumption; not a verified venue fee');
  const wall = frame.book?.asof.receive_wall_ns;
  if (!wall) throw new Error('Fee assumption date requires the actual demo source wall clock');
  const date = new Date(Number(BigInt(wall) / 1_000_000n)).toISOString().slice(0, 10);
  await pane.getByLabel('Fee source date', { exact: true }).fill(date);
  for (const name of ['Feed latency (ns)', 'Decision latency (ns)', 'Order latency (ns)']) {
    await pane.getByLabel(name, { exact: true }).fill('0');
  }
  const create = pane.getByRole('button', { name: 'Create new paper run', exact: true });
  await expect(create).toBeDisabled();
  await pane.getByRole('checkbox', { name: /^I explicitly intend a new run \/ fork/ }).check();
  const configured = nextResponse(page, 'paper/configure');
  await create.click();
  const result = await body<PaperResponse>(configured);
  expect(result.snapshot.run?.config).toMatchObject({
    feed_latency_ns: '0', decision_latency_ns: '0', order_latency_ns: '0',
    balances: [expect.objectContaining({ venue: instrument.venue, asset_id: instrument.quote.asset_id, available: '1000' })],
    fees: [expect.objectContaining({ instrument_id: instrument.instrument_id, schedule: expect.objectContaining({ rate: '0.001', asset_id: instrument.quote.asset_id, assumed: true }) })],
  });
  expect(result.snapshot.balances).toEqual([{ venue: instrument.venue, asset_id: instrument.quote.asset_id, available: '1000' }]);
  await expect(pane.getByRole('button', { name: 'Submit simulated order', exact: true })).toBeEnabled();
  return { pane, configured: result };
}

function runRow(pane: Locator, id: string) {
  return pane.locator('.paper-run-list article').filter({ has: pane.page().locator('strong').filter({ hasText: id }) });
}

test('native alert support, explicit spot IOC and linked journal survive rewind and persisted-run resume', async ({ page }) => {
  const streamedGenerations = new Map<string, bigint>();
  page.on('websocket', socket => socket.on('framereceived', event => {
    const delivered = JSON.parse(event.payload.toString()) as Frame;
    streamedGenerations.set(delivered.session_id, BigInt(delivered.generation));
  }));
  const { frame, instrument } = await openDemo(page);
  const alertRead = nextResponse(page, 'alerts/query');
  const alerts = await panel(page, 'Alerts');
  const kinds = await body<AlertsResponse>(alertRead);
  expect(kinds.kinds.some(item => item.kind === 'spread_shock')).toBe(true);
  await alerts.getByText('Create alert rule — all seven native kinds', { exact: true }).click();
  await alerts.getByRole('combobox', { name: 'Alert kind', exact: true }).selectOption('spread_shock');
  await alerts.getByRole('combobox', { name: 'Trigger direction', exact: true }).selectOption('above');
  await alerts.getByLabel(/^Threshold \(/).fill('1000000');
  await alerts.getByLabel(/^Rearm threshold \(/).fill('999999');
  await alerts.getByLabel('Window (ns; 0 for instantaneous kinds)', { exact: true }).fill('0');
  await alerts.getByLabel('Cooldown (ns; 0 = none)', { exact: true }).fill('0');
  const put = nextResponse(page, 'alerts/rules/put');
  await alerts.getByRole('button', { name: 'Create local rule', exact: true }).click();
  const saved = await body<AlertsResponse>(put);
  const rule = saved.snapshot.rules.find(item => item.key.instrument_id === instrument.instrument_id && item.kind === 'spread_shock');
  if (!rule) throw new Error('Created native alert rule missing from returned snapshot');
  expect(rule).toMatchObject({ threshold: '1000000', rearm_threshold: '999999', window_ns: '0', cooldown_ns: '0' });
  // A rule registered at a paused cursor cannot fabricate an observation.
  // Deliver the next real source transaction before requesting its evidence.
  const advance = nextResponse(page, 'replay', input => input.action === 'step');
  await page.getByRole('button', { name: 'Step', exact: true }).click();
  await body<Frame>(advance);
  const row = alerts.getByRole('region', { name: 'Rules and last-delivered evaluations', exact: true }).getByRole('row').filter({ hasText: rule.rule_id });
  const evidenceResponse = nextResponse(page, 'alerts/evidence', input => input.rule_id === rule.rule_id);
  await row.getByRole('button', { name: 'Load rule evidence', exact: true }).click();
  const evidence = await body<AlertEvidenceResponse>(evidenceResponse);
  expect(evidence.evaluation.rule).toEqual(rule);
  expect(evidence.evaluation.eligibility.eligible).toBe(true);
  expect(evidence.evaluation.metric?.instrument_id).toBe(instrument.instrument_id);
  const support = alerts.getByRole('region', { name: 'Loaded alert evidence', exact: true });
  await support.getByText('Full bounded native evidence', { exact: true }).click();
  expect(JSON.parse(await support.locator('pre').innerText())).toEqual(evidence);

  const { pane: paper, configured } = await configure(page, frame, instrument);
  const run = configured.snapshot.run!;
  await paper.getByRole('combobox', { name: 'Side', exact: true }).selectOption('buy');
  await paper.getByLabel('Base quantity — exact', { exact: true }).fill('0.01');
  await paper.getByRole('combobox', { name: 'Order type', exact: true }).selectOption('market');
  const submitted = nextResponse(page, 'paper/orders');
  await paper.getByRole('button', { name: 'Submit simulated order', exact: true }).click();
  const executed = await body<PaperResponse>(submitted);
  expect(executed.snapshot.orders).toHaveLength(1);
  const order = executed.snapshot.orders[0];
  expect(order).toMatchObject({ run_id: run.run_id, status: 'filled', filled_quantity: '0.01', remaining_quantity: '0', request: { instrument_id: instrument.instrument_id, side: 'buy', order_type: 'market', quantity: '0.01' } });
  expect(executed.snapshot.fills).toHaveLength(1);
  const fill = executed.snapshot.fills[0];
  expect(fill).toMatchObject({ order_id: order.order_id, run_id: run.run_id, instrument_id: instrument.instrument_id, quantity: '0.01', fee_asset_id: instrument.quote.asset_id, fees: { rate: '0.001', assumed: true } });
  expect(fill.book_key.instrument_id).toBe(instrument.instrument_id);
  expect(fill.consumed_levels.length).toBeGreaterThan(0);
  await expect(paper.locator('.paper-accounting')).toContainText(order.order_id);
  await expect(paper.locator('.paper-accounting')).toContainText(fill.fill_id);

  const journal = await panel(page, 'Journal');
  const rationale = `Explicit synthetic IOC workflow for ${run.run_id}; inspect native fill evidence, not a market recommendation.`;
  await journal.getByLabel('Rationale (required)', { exact: true }).fill(rationale);
  await journal.getByLabel('Authored context', { exact: true }).fill(`Synthetic source ${executed.source_identity}; native order ${order.order_id}`);
  await journal.getByRole('checkbox', { name: /^Optionally link visible active run / }).check();
  await journal.getByRole('combobox', { name: 'Optional visible order for selected instrument', exact: true }).selectOption(order.order_id);
  const noteResponse = nextResponse(page, 'journal/put');
  await journal.getByRole('button', { name: 'Persist new journal note', exact: true }).click();
  const note = (await body<JournalPutResponse>(noteResponse)).entry;
  expect(note).toMatchObject({ run_id: run.run_id, order_id: order.order_id, instrument_id: instrument.instrument_id, rationale, delivery_index: executed.delivery_index });
  await expect(journal.locator('.paper-journal-list article').filter({ hasText: note.entry_id })).toContainText(rationale);

  const emptyFrame = await seek(page, '0');
  await panel(page, 'Paper');
  const empty = await readPaper(page, paper, emptyFrame.generation);
  expect(empty.snapshot.run?.run_id).toBe(run.run_id);
  expect(empty.snapshot.reasons).toContain('run_not_active_at_cursor');
  for (const values of [empty.snapshot.balances, empty.snapshot.orders, empty.snapshot.fills, empty.snapshot.positions]) expect(values).toEqual([]);
  await expect(paper.getByRole('button', { name: 'Submit simulated order', exact: true })).toBeDisabled();
  await expect(paper.locator('.paper-accounting')).toContainText('No balances visible at this accounting prefix');
  await expect(paper.locator('.paper-accounting')).toContainText('No orders visible at this prefix');
  await expect(paper.locator('.paper-accounting')).toContainText('No fills visible at this prefix');

  const contextResponse = nextResponse(page, 'context');
  const context = await panel(page, 'Context');
  const contextData = await body<ContextResponse>(contextResponse);
  expect(contextData.delivery_index).toBe('0');
  expect(contextData.snapshot.watchlist.every(item => item.value === null)).toBe(true);
  expect(contextData.snapshot.contexts.every(item => item.value === null)).toBe(true);
  await expect(context).toContainText(/Unavailable|No native .*available/);
  await expect(page.getByTestId('grouped-ladder')).toHaveCount(0);
  await panel(page, 'Journal');
  const futureNote = journal.locator('.paper-journal-list article').filter({ hasText: note.entry_id });
  await expect(futureNote).toContainText('FUTURE-TO-CURSOR MANUAL NOTE');
  await expect(futureNote).toContainText(rationale);
  await expect(journal.getByRole('checkbox', { name: /^Optionally link visible active run / })).toBeDisabled();

  const restoredFrame = await seek(page, executed.delivery_index);
  await panel(page, 'Paper');
  const restored = await readPaper(page, paper, restoredFrame.generation);
  expect(restored.snapshot.balances).toEqual(executed.snapshot.balances);
  expect(restored.snapshot.orders).toEqual(executed.snapshot.orders);
  expect(restored.snapshot.fills).toEqual(executed.snapshot.fills);

  // A genuinely new terminal session must explicitly resume the durable run;
  // neither HTTP completion nor a stale snapshot is proof of successful resume.
  const replacementResponse = nextResponse(page, 'sessions');
  await page.getByLabel('Reconnect terminal', { exact: true }).click();
  let replacement = await body<Frame>(replacementResponse);
  expect(replacement.session_id).not.toBe(frame.session_id);
  await expect(page.locator('.status')).toHaveText('Connected');
  replacement = await seek(page, executed.delivery_index);
  await panel(page, 'Paper');
  const unconfigured = await readPaper(page, paper, replacement.generation);
  expect(unconfigured.snapshot.run).toBeNull();
  // Hold the actual successful HTTP response until the same backend has
  // published its new generation over WS. That ordering must not abort resume.
  const { promise: heldResume, resolve: releaseResume } = Promise.withResolvers<void>();
  await page.route('**/api/v1/paper/resume', async route => {
    const response = await route.fetch();
    await heldResume;
    await route.fulfill({ response });
  });
  const resumeResponse = nextResponse(page, 'paper/resume', input => input.run_id === run.run_id);
  try {
    await runRow(paper, run.run_id).getByRole('button', { name: 'Resume this run at cursor', exact: true }).click();
    await expect.poll(() => streamedGenerations.get(replacement.session_id) ?? -1n).toBeGreaterThan(BigInt(replacement.generation));
    await page.evaluate(() => {
      const { promise, resolve } = Promise.withResolvers<void>();
      requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
      return promise;
    });
  } finally {
    releaseResume();
  }
  const resumedFrame = await body<Frame>(resumeResponse);
  await page.unroute('**/api/v1/paper/resume');
  expect(resumedFrame.session_id).toBe(replacement.session_id);
  expect(BigInt(resumedFrame.generation)).toBeGreaterThan(BigInt(replacement.generation));
  expect(resumedFrame.replay?.cursor.delivery_index).toBe(executed.delivery_index);
  const resumed = await readPaper(page, paper, resumedFrame.generation);
  expect(resumed.snapshot.run?.run_id).toBe(run.run_id);
  expect(resumed.snapshot.balances).toEqual(executed.snapshot.balances);
  expect(resumed.snapshot.orders).toEqual(executed.snapshot.orders);
  expect(resumed.snapshot.fills).toEqual(executed.snapshot.fills);
  await expect(paper.locator('.paper-notice').filter({ hasText: /^Resume requested:/ })).toContainText('Confirmed by a new-generation native run snapshot');

  await panel(page, 'Journal');
  await journal.getByLabel('Optional exact run filter', { exact: true }).fill(run.run_id);
  const persistedNotes = nextResponse(page, 'journal/query', input => input.run_id === run.run_id);
  await journal.getByRole('button', { name: 'Apply run filter', exact: true }).click();
  expect((await body<JournalResponse>(persistedNotes)).entries).toContainEqual(note);
  await expect(journal.locator('.paper-journal-list article').filter({ hasText: note.entry_id })).toContainText(rationale);

  const researchResponse = nextResponse(page, 'research/query');
  const research = await panel(page, 'Research');
  const history = await body<ResearchResponse>(researchResponse);
  expect(history.report).toBeNull();
  await expect(research).toContainText('Research execution is offline CLI only');
  if (history.attempts.length === 0) await expect(research).toContainText('No persisted research attempts');
  else for (const attempt of history.attempts) await expect(research.getByRole('row').filter({ hasText: attempt.attempt_id })).toContainText(attempt.status);
});

test('failed and pane-aborted resume never claim success or retry; explicit retry confirms native generation', async ({ page }) => {
  const opened = await openDemo(page);
  let frame = opened.frame;
  const { instrument } = opened;
  const { pane: paper, configured } = await configure(page, frame, instrument);
  const run = configured.snapshot.run!;
  // Compare like-for-like bounded queries: configure uses the native default
  // presentation limit, whereas the visible panel explicitly requests 100.
  const baseline = await readPaper(page, paper, frame.generation);
  let attempts = 0;
  const resumePath = '**/api/v1/paper/resume';
  await page.route(resumePath, async route => { attempts++; await route.abort('connectionfailed'); });
  await runRow(paper, run.run_id).getByRole('button', { name: 'Resume this run at cursor', exact: true }).click();
  await expect(paper.getByRole('status').filter({ hasText: 'no automatic retry was made' })).toBeVisible();
  await expect(paper.locator('.paper-notice').filter({ hasText: /^Resume requested:/ })).toContainText('Not yet confirmed');
  const unchanged = await readPaper(page, paper, frame.generation);
  expect(unchanged.snapshot).toEqual(baseline.snapshot);
  expect(attempts).toBe(1);
  await page.unroute(resumePath);
  // Neither an unrelated seek generation nor preserved inactive run metadata
  // can turn a failed resume of the already selected run into a success.
  frame = await seek(page, '0');
  const inactive = await readPaper(page, paper, frame.generation);
  expect(inactive.snapshot.reasons).toContain('run_not_active_at_cursor');
  await expect(paper.locator('.paper-notice').filter({ hasText: /^Resume requested:/ })).toContainText('Not yet confirmed');
  frame = await seek(page, baseline.delivery_index);
  const restored = await readPaper(page, paper, frame.generation);
  expect(restored.snapshot).toEqual(baseline.snapshot);
  await expect(paper.locator('.paper-notice').filter({ hasText: /^Resume requested:/ })).toContainText('Not yet confirmed');

  // Hold transport before it reaches the actual backend. Losing panel ownership
  // must cancel the action and release the terminal lock, without inventing a
  // successful response or assuming an interrupted server mutation rolled back.
  const { promise: intercepted, resolve: captured } = Promise.withResolvers<void>();
  const { promise: held, resolve: release } = Promise.withResolvers<void>();
  const { promise: finished, resolve: routed } = Promise.withResolvers<void>();
  await page.route(resumePath, async route => {
    attempts++;
    captured();
    await held;
    try { await route.abort('aborted'); } finally { routed(); }
  });
  try {
    await runRow(paper, run.run_id).getByRole('button', { name: 'Resume this run at cursor', exact: true }).click();
    await intercepted;
    const contextResponse = nextResponse(page, 'context');
    await panel(page, 'Context');
    const context = await body<ContextResponse>(contextResponse);
    expect(context.generation).toBe(frame.generation);
  } finally {
    release();
    await finished;
    await page.unroute(resumePath);
  }
  await panel(page, 'Paper');
  const afterAbort = await readPaper(page, paper, frame.generation);
  expect(afterAbort.snapshot).toEqual(baseline.snapshot);
  expect(attempts).toBe(2);
  await expect(paper.locator('.paper-notice').filter({ hasText: /^Resume requested:/ })).toContainText('Not yet confirmed');
  await expect(page.locator('.error-banner')).toHaveCount(0);

  const resumedResponse = nextResponse(page, 'paper/resume', input => input.run_id === run.run_id);
  await runRow(paper, run.run_id).getByRole('button', { name: 'Resume this run at cursor', exact: true }).click();
  const resumedFrame = await body<Frame>(resumedResponse);
  expect(BigInt(resumedFrame.generation)).toBeGreaterThan(BigInt(frame.generation));
  const resumed = await readPaper(page, paper, resumedFrame.generation);
  expect(resumed.snapshot).toEqual(baseline.snapshot);
  await expect(paper.locator('.paper-notice').filter({ hasText: /^Resume requested:/ })).toContainText('Confirmed by a new-generation native run snapshot');
  // Wait for the actual bounded read chain, not a sleep, before checking that no
  // automatic resume request was issued after either transport interruption.
  const runs = await body<PaperRunsResponse>(nextResponse(page, 'paper/runs'));
  expect(runs.runs.some(item => item.run_id === run.run_id)).toBe(true);
  expect(attempts).toBe(2);
});
