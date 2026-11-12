import { expect, test } from '@playwright/test';
import type { Catalog, Frame } from '../src/api';
import type { PaperResponse } from '../src/workflow-api';

// Native sessions and delivered frames only; no manufactured feed or precision.
test('streaming-only native candidates start and switch without enabling financial operations', async ({ page }) => {
  const response = await page.request.get('/api/v1/instruments?source_id=live');
  test.skip(!response.ok(), 'Requires the operator-enabled public live source.');
  const catalog = await response.json() as Catalog;
  const live = catalog.sources.find(source => source.id === 'live' && source.status === 'ready');
  const candidates = catalog.instruments.filter(instrument => ['bybit', 'binance-global'].includes(instrument.venue)
    && instrument.metadata_status === 'streaming_only' && instrument.status === 'unknown' && instrument.data_access !== 'blocked');
  test.skip(!live || !candidates.length, 'Requires at least one native streaming-only subscription candidate.');
  const initial = candidates[0];
  await page.addInitScript(instrument => localStorage.setItem('limit-orderbook.workspace.v1', JSON.stringify({
    version: 1, source: { id: 'live', mode: 'live' }, sourceExplicit: true, instrument,
  })), initial.instrument_id);
  const created = page.waitForResponse(item => item.url().endsWith('/api/v1/sessions') && item.request().method() === 'POST');
  await page.goto('/?source=live');
  const creation = await created;
  expect(creation.ok()).toBe(true);
  const frame = await creation.json() as Frame;
  expect(creation.request().postDataJSON().instrument_id).toBe(initial.instrument_id);
  expect(frame.selection.instrument_id).toBe(initial.instrument_id);
  expect(frame.selection.metadata_status).toBe('streaming_only');
  await expect(page.locator('.status')).toContainText('Connected');
  await expect(page.getByTestId('metadata-capability-notice')).toBeVisible();
  await page.getByRole('tab', { name: 'Costs & proposed risk', exact: true }).click();
  await expect(page.getByTestId('estimate')).toBeDisabled();
  await expect(page.getByTestId('proposal-risk-calculate')).toBeDisabled();
  const paperQuery = page.waitForResponse(item => item.url().endsWith('/api/v1/paper/query')
    && item.request().method() === 'POST'
    && item.request().postDataJSON().session_id === frame.session_id
    && item.request().postDataJSON().generation === frame.generation);
  await page.getByRole('tab', { name: 'Paper', exact: true }).click();
  const paperResponse = await paperQuery;
  expect(paperResponse.ok()).toBe(true);
  const paper = await paperResponse.json() as PaperResponse;
  expect(paper.capability).toMatchObject({
    instrument_id: frame.selection.instrument_id,
    metadata_version: frame.selection.metadata_version,
    supported: false,
  });
  await expect(page.getByRole('button', { name: 'Refresh native state', exact: true })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Submit simulated order', exact: true })).toBeDisabled();
  await expect(page.getByLabel(/^(Base|Native) quantity — exact$/)).toBeDisabled();

  const other = candidates.find(instrument => instrument.venue !== initial.venue) ?? candidates.find(instrument => instrument.instrument_id !== initial.instrument_id);
  if (other) {
    await page.locator('.native-controls summary').filter({ hasText: /^Watchlist$/ }).click();
    await page.getByLabel('Search instruments', { exact: true }).fill(other.instrument_id);
    const choice = page.getByTitle(other.instrument_id, { exact: true });
    await expect(choice).toBeEnabled();
    const selection = page.waitForResponse(item => item.url().endsWith('/api/v1/subscriptions') && item.request().method() === 'POST');
    const switchedPaperQuery = page.waitForResponse(item => item.url().endsWith('/api/v1/paper/query')
      && item.request().method() === 'POST'
      && item.request().postDataJSON().session_id === frame.session_id
      && item.request().postDataJSON().generation !== frame.generation);
    await choice.click();
    const selected = await selection;
    expect(selected.ok()).toBe(true);
    const switched = await selected.json() as Frame;
    expect(switched.session_id).toBe(frame.session_id);
    expect(switched.generation).not.toBe(frame.generation);
    expect(switched.selection).toMatchObject({ instrument_id: other.instrument_id, metadata_status: 'streaming_only' });
    const switchedPaperResponse = await switchedPaperQuery;
    expect(switchedPaperResponse.ok()).toBe(true);
    const switchedPaper = await switchedPaperResponse.json() as PaperResponse;
    expect(switchedPaper.capability).toMatchObject({
      instrument_id: switched.selection.instrument_id,
      metadata_version: switched.selection.metadata_version,
      supported: false,
    });
    await expect(page.getByRole('button', { name: 'Refresh native state', exact: true })).toBeEnabled();
    await expect(page.getByRole('button', { name: 'Submit simulated order', exact: true })).toBeDisabled();
    await expect(page.getByLabel(/^(Base|Native) quantity — exact$/)).toBeDisabled();
    await expect(page.getByTestId('metadata-capability-notice')).toBeVisible();
  }
});
