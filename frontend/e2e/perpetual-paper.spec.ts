import { expect, test } from '@playwright/test';
import type { Page, Response } from '@playwright/test';
import type { Catalog, Frame } from '../src/api';
import type { PaperArchiveResponse, PaperResponse } from '../src/workflow-api';

// Real deterministic native demo, no intercepted financial responses or venue network.
const responseFor = (page: Page, endpoint: string) => page.waitForResponse(response => response.url().endsWith(`/api/v1/${endpoint}`) && response.request().method() === 'POST');
async function result<T>(pending: Promise<Response>): Promise<T> {
  const response = await pending;
  expect(response.ok(), `${response.status()} ${await response.text()}`).toBe(true);
  return response.json() as Promise<T>;
}

for (const scenario of ['none', 'periodic', 'events', 'freeze', 'liquidation'] as const) {
  const mode = scenario === 'freeze' ? 'periodic' : scenario === 'liquidation' ? 'events' : scenario;
  test(`verified native perpetual: ${scenario} funding, accounting and lifecycle`, async ({ page, request }) => {
    const sessions = new Set<string>();
    const creations: Promise<void>[] = [];
    page.on('response', response => {
      if (response.url().endsWith('/api/v1/sessions') && response.request().method() === 'POST' && response.ok()) {
        creations.push(response.json().then((frame: Frame) => { sessions.add(frame.session_id); }));
      }
    });
    await page.addInitScript(() => localStorage.removeItem('limit-orderbook.workspace.v1'));
    try {
      const creation = responseFor(page, 'sessions');
      const catalogRead = page.waitForResponse(response => response.url().endsWith('/api/v1/instruments') && response.request().method() === 'GET');
      await page.goto('/');
      const initial = await result<Frame>(creation);
      expect(initial.source.mode).toBe('demo');
      const catalog = await (await catalogRead).json() as Catalog;
      const instrument = catalog.instruments.find(item => item.venue === 'hyperliquid' && item.market_type === 'perpetual' && item.base.ticker === 'BTC' && item.metadata_status === 'verified');
      if (!instrument?.settlement) throw new Error('Verified native Hyperliquid BTC perpetual and settlement metadata required');
      const watchlist = page.locator('.native-controls summary').filter({ hasText: /^Watchlist$/ });
      if (await watchlist.locator('..').getAttribute('open') === null) await watchlist.click();
      await page.getByLabel('Search instruments').fill('BTC');
      const select = responseFor(page, 'subscriptions');
      await page.getByTitle(instrument.instrument_id, { exact: true }).click();
      let frame = await result<Frame>(select);
      expect(frame.selection.metadata_status).toBe('verified');
      expect(frame.book?.instrument_id).toBe(instrument.instrument_id);
      const paperRead = responseFor(page, 'paper/query');
      await page.getByRole('tablist', { name: 'Research pane', exact: true }).getByRole('tab', { name: 'Paper', exact: true }).click();
      const pane = page.getByRole('tabpanel', { name: 'Paper', exact: true });
      const before = await result<PaperResponse>(paperRead);
      expect(before.capability).toMatchObject({ instrument_id: instrument.instrument_id, metadata_version: frame.selection.metadata_version, supported: true, settlement_asset_id: instrument.settlement.asset_id });
      const wall = before.snapshot.at?.wall_ns ?? frame.book?.asof.receive_wall_ns;
      if (!wall) throw new Error('Native source wall clock required for explicit models');
      const boundary = (BigInt(wall) + 1_000_000_000n).toString();
      await pane.getByLabel('Venue', { exact: true }).fill(instrument.venue);
      await pane.getByLabel('Asset ID', { exact: true }).fill(instrument.settlement.asset_id);
      await pane.getByLabel('Starting available — exact', { exact: true }).fill('10000');
      await pane.getByLabel('Balance provenance', { exact: true }).fill('Explicit browser regression collateral assumption; not account funds');
      await pane.getByRole('combobox', { name: 'Fee instrument', exact: true }).selectOption(instrument.instrument_id);
      await pane.getByLabel('Fee basis points — exact', { exact: true }).fill('10');
      await pane.getByLabel('Fee asset ID', { exact: true }).fill(instrument.settlement.asset_id);
      await pane.getByLabel('Fee source / provenance', { exact: true }).fill('Explicit simulation fee, not exchange tier');
      await pane.getByLabel('Fee source date', { exact: true }).fill(new Date(Number(BigInt(wall) / 1_000_000n)).toISOString().slice(0, 10));
      await pane.getByRole('button', { name: 'Add perpetual model', exact: true }).click();
      await expect(pane.getByLabel('Modeled leverage — exact', { exact: true })).toHaveValue('');
      await expect(pane.getByRole('combobox', { name: 'Funding assumption', exact: true })).toHaveValue('');
      for (const [label, value] of [
        ['Modeled leverage — exact', '2'], ['Maintenance margin rate — exact', '0.01'], ['Liquidation penalty rate — exact', '0.005'],
        ['Risk model provenance', 'Explicit test isolated risk assumption'], ['Risk as-of wall ns', wall],
        ['Funding model provenance', `Explicit test ${mode} funding assumption`], ['Funding as-of wall ns', wall],
      ]) await pane.getByLabel(label, { exact: true }).fill(value);
      await pane.getByRole('combobox', { name: 'Funding assumption', exact: true }).selectOption(mode);
      if (mode === 'periodic') {
        // Nanosecond funding must exhaust the bounded record history before it can drain 2x margin into liquidation.
        await pane.getByLabel('Periodic funding rate — exact', { exact: true }).fill(scenario === 'freeze' ? '0.000000001' : '0.0001');
        await pane.getByLabel('Funding interval ns', { exact: true }).fill(scenario === 'freeze' ? '1' : '1000000000');
        await pane.getByLabel('First funding boundary wall ns', { exact: true }).fill(boundary);
      } else if (mode === 'events') {
        await pane.getByRole('button', { name: 'Add funding event', exact: true }).click();
        await pane.getByLabel('Event wall ns 1', { exact: true }).fill(boundary);
        await pane.getByLabel('Event funding rate — exact 1', { exact: true }).fill(scenario === 'liquidation' ? '0.9' : '-0.0001');
      }
      for (const label of ['Feed latency (ns)', 'Decision latency (ns)', 'Order latency (ns)']) await pane.getByLabel(label, { exact: true }).fill('0');
      await pane.getByRole('checkbox', { name: /^I explicitly intend a new run \/ fork/ }).check();
      const configuredRead = responseFor(page, 'paper/configure');
      await pane.getByRole('button', { name: 'Create new paper run', exact: true }).click();
      const configured = await result<PaperResponse>(configuredRead);
      expect(configured.snapshot.run?.config.risk_models[0]).toMatchObject({ instrument_id: instrument.instrument_id, leverage: '2', maintenance_margin_rate: '0.01', liquidation_fee_rate: '0.005', asof_wall_ns: wall });
      expect(configured.snapshot.run?.config.funding_models[0].mode).toBe(mode);
      const runId = configured.snapshot.run!.run_id;
      await pane.getByLabel('Native quantity — exact', { exact: true }).fill('0.001');
      const openRead = responseFor(page, 'paper/orders');
      await pane.getByRole('button', { name: 'Submit simulated order', exact: true }).click();
      const opened = await result<PaperResponse>(openRead);
      expect(opened.snapshot.orders.at(-1)).toMatchObject({ status: 'filled', request: { side: 'buy', reduce_only: false } });
      expect(opened.snapshot.positions).toEqual([]);
      expect(opened.snapshot.perpetual_positions[0]).toMatchObject({ quantity: '0.001', settlement_asset_id: instrument.settlement.asset_id, debt: '0' });
      await expect(pane.getByRole('table', { name: 'Perpetual positions', exact: true })).toContainText('0.001');
      const target = (BigInt(opened.delivery_index) + BigInt(frame.source.streams * 3)).toString();
      const seekRead = responseFor(page, 'replay');
      await page.getByLabel('Seek delivery', { exact: true }).fill(target);
      await page.getByRole('button', { name: 'Seek', exact: true }).click();
      frame = await result<Frame>(seekRead);
      const afterRead = responseFor(page, 'paper/query');
      await pane.getByRole('button', { name: 'Refresh native state', exact: true }).click();
      const advanced = await result<PaperResponse>(afterRead);
      if (scenario === 'freeze') {
        expect(advanced.snapshot.reasons).toContain('paper_frozen');
        expect(advanced.snapshot.reasons).toContain('funding_catchup_limit');
        expect(BigInt(advanced.snapshot.delivery_index)).toBeLessThan(BigInt(frame.replay!.cursor.delivery_index));
        await expect(pane.getByRole('alert')).toContainText('PAPER FROZEN');
        await expect(pane.getByRole('button', { name: 'Submit simulated order', exact: true })).toBeDisabled();
        const frozenRead = responseFor(page, 'paper/query');
        await pane.getByRole('button', { name: 'Refresh native state', exact: true }).click();
        const frozen = await result<PaperResponse>(frozenRead);
        expect(frozen.snapshot.delivery_index).toBe(advanced.snapshot.delivery_index);
        expect(frozen.snapshot.perpetual_positions).toEqual(advanced.snapshot.perpetual_positions);
        return;
      }
      if (mode === 'none') expect(advanced.snapshot.funding_payments).toEqual([]);
      else {
        expect(advanced.snapshot.funding_payments.length).toBeGreaterThan(0);
        expect(advanced.snapshot.funding_payments[0].amount).not.toBeNull();
        await expect(pane.getByRole('table', { name: 'Funding payments', exact: true })).toContainText(boundary);
      }
      if (scenario === 'liquidation') {
        expect(advanced.snapshot.liquidations.length).toBeGreaterThan(0);
        const liquidation = advanced.snapshot.liquidations[0];
        expect(liquidation.instrument_id).toBe(instrument.instrument_id);
        expect(liquidation.settlement_asset_id).toBe(instrument.settlement.asset_id);
        await expect(pane.getByRole('table', { name: 'Protective liquidations', exact: true })).toContainText(instrument.instrument_id);
        await expect(pane.getByRole('table', { name: 'Perpetual positions', exact: true })).toContainText(`Debt ${advanced.snapshot.perpetual_positions[0].debt}`);
        return;
      }
      await pane.getByRole('button', { name: 'Prepare full reduce-only close', exact: true }).click();
      await expect(pane.getByRole('combobox', { name: 'Side', exact: true })).toHaveValue('sell');
      await expect(pane.getByRole('checkbox', { name: /^Reduce only/ })).toBeChecked();
      const closeRead = responseFor(page, 'paper/orders');
      await pane.getByRole('button', { name: 'Submit simulated order', exact: true }).click();
      const closed = await result<PaperResponse>(closeRead);
      expect(closed.snapshot.perpetual_positions[0].quantity).toBe('0');
      expect(closed.snapshot.orders.at(-1)?.request.reduce_only).toBe(true);
      await pane.getByRole('checkbox', { name: /^Reduce only/ }).uncheck();
      await pane.getByLabel('Native quantity — exact', { exact: true }).fill('0.001');
      const shortRead = responseFor(page, 'paper/orders');
      await pane.getByRole('button', { name: 'Submit simulated order', exact: true }).click();
      const short = await result<PaperResponse>(shortRead);
      expect(short.snapshot.perpetual_positions[0].quantity).toBe('-0.001');
      await expect(pane.getByRole('button', { name: 'Prepare full reduce-only close', exact: true })).toBeEnabled();
      const runRow = pane.locator('.paper-run-list article').filter({ hasText: runId });
      const archiveRead = responseFor(page, 'paper/archive');
      await runRow.getByRole('button', { name: 'Open read-only archived audit', exact: true }).click();
      const archived = await result<PaperArchiveResponse>(archiveRead);
      expect(archived.snapshot.perpetual_positions).toEqual(short.snapshot.perpetual_positions);
      await expect(pane.getByRole('region', { name: 'Read-only historical accounting audit', exact: true })).toContainText('ARCHIVED AUDIT');
      await pane.getByRole('button', { name: 'Close archive', exact: true }).click();
      const rewindRead = responseFor(page, 'replay');
      await page.getByLabel('Seek delivery', { exact: true }).fill('0');
      await page.getByRole('button', { name: 'Seek', exact: true }).click();
      await result<Frame>(rewindRead);
      await expect(pane.getByRole('button', { name: 'Submit simulated order', exact: true })).toBeDisabled();
      const restoreRead = responseFor(page, 'replay');
      await page.getByLabel('Seek delivery', { exact: true }).fill(short.delivery_index);
      await page.getByRole('button', { name: 'Seek', exact: true }).click();
      await result<Frame>(restoreRead);
      const restoredRead = responseFor(page, 'paper/query');
      await pane.getByRole('button', { name: 'Refresh native state', exact: true }).click();
      const restored = await result<PaperResponse>(restoredRead);
      expect(restored.snapshot.perpetual_positions).toEqual(short.snapshot.perpetual_positions);
      expect(restored.snapshot.funding_payments).toEqual(short.snapshot.funding_payments);
      const resumeRead = responseFor(page, 'paper/resume');
      await runRow.getByRole('button', { name: 'Resume this run at cursor', exact: true }).click();
      await result<Frame>(resumeRead);
      const resumedRead = responseFor(page, 'paper/query');
      await pane.getByRole('button', { name: 'Refresh native state', exact: true }).click();
      const resumed = await result<PaperResponse>(resumedRead);
      expect(resumed.snapshot.run?.run_id).toBe(runId);
      expect(resumed.snapshot.perpetual_positions).toEqual(short.snapshot.perpetual_positions);
      expect(resumed.snapshot.funding_payments).toEqual(short.snapshot.funding_payments);
      if (mode === 'none') {
        // The form collapses once the resumed run is rendered; opening it earlier would be undone by that render.
        const fork = pane.locator('details.paper-section');
        await expect(fork).not.toHaveAttribute('open', '');
        await fork.locator(':scope > summary').click();
        await pane.getByRole('checkbox', { name: /^I explicitly intend a new run \/ fork/ }).check();
        const forkRead = responseFor(page, 'paper/configure');
        await pane.getByRole('button', { name: 'Create explicit fork', exact: true }).click();
        const forked = await result<PaperResponse>(forkRead);
        expect(forked.snapshot.run?.parent_run_id).toBe(runId);
        expect(forked.snapshot.run?.run_id).not.toBe(runId);
        expect(forked.snapshot.perpetual_positions).toEqual([]);
        expect(forked.snapshot.run?.config.funding_models[0].mode).toBe('none');
      }
    } finally {
      await page.close();
      await Promise.all(creations);
      for (const id of sessions) {
        const deleted = await request.delete(`/api/v1/sessions/${id}`);
        expect(deleted.ok() || deleted.status() === 404).toBe(true);
      }
    }
  });
}
