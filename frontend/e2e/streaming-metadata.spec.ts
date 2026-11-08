import { expect, test } from '@playwright/test';
import type { Catalog, Frame } from '../src/api';

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
  await expect(page.getByTestId('metadata-capability-notice')).toContainText('Streaming-only metadata');
  await page.getByRole('tab', { name: 'Costs & proposed risk', exact: true }).click();
  await expect(page.getByTestId('estimate')).toBeDisabled();
  await expect(page.getByTestId('proposal-risk-calculate')).toBeDisabled();
  await page.getByRole('tab', { name: 'Paper', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Submit simulated order', exact: true })).toBeDisabled();
  await expect(page.getByLabel('Base quantity — exact', { exact: true })).toBeDisabled();

  const other = candidates.find(instrument => instrument.venue !== initial.venue) ?? candidates.find(instrument => instrument.instrument_id !== initial.instrument_id);
  if (other) {
    await page.locator('.native-controls summary').filter({ hasText: /^Watchlist$/ }).click();
    await page.getByLabel('Search instruments', { exact: true }).fill(other.instrument_id);
    const choice = page.getByTitle(other.instrument_id, { exact: true });
    await expect(choice).toBeEnabled();
    const selection = page.waitForResponse(item => item.url().endsWith('/api/v1/subscriptions') && item.request().method() === 'POST');
    await choice.click();
    const selected = await selection;
    expect(selected.ok()).toBe(true);
    expect((await selected.json() as Frame).selection).toMatchObject({ instrument_id: other.instrument_id, metadata_status: 'streaming_only' });
    await expect(page.getByTestId('metadata-capability-notice')).toContainText('Streaming-only metadata');
  }
});
