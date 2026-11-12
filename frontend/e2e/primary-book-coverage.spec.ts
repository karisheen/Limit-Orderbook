import { expect, test } from '@playwright/test';
import type { Catalog, Frame, NativeMarketData } from '../src/api';
import { venueLabel } from '../src/format';

// Real offline demo sessions and native books. Fault overlays remove eligibility
// or truncate delivered rows; no prices, sizes, identities or duplicate books
// are manufactured. Install before navigation so every poll has the same fault.
for (const fault of ['missing', 'ineligible'] as const) {
  test(`single book never substitutes peer depth when primary is ${fault}`, async ({ page }) => {
    const catalogResponse = await page.request.get('/api/v1/instruments');
    expect(catalogResponse.ok()).toBe(true);
    const catalog = await catalogResponse.json() as Catalog;
    const source = catalog.sources.find(item => item.mode === 'demo' && item.status === 'ready');
    if (!source) throw new Error('Requires the ready offline synthetic demo source');
    const scopedResponse = await page.request.get(`/api/v1/instruments?source_id=${encodeURIComponent(source.id)}`);
    expect(scopedResponse.ok()).toBe(true);
    const scoped = await scopedResponse.json() as Catalog;
    // Binance supplies two genuine native levels; the existing All spot view
    // admits Kraken's USD peer only as an explicitly indicative cross-quote cohort.
    const primary = scoped.instruments.find(item => item.venue === 'binance-global' && item.market_type === 'spot' &&
      item.base.ticker === 'BTC' && item.quote.ticker === 'USDT');
    const peer = primary && scoped.instruments.find(item => item.venue === 'kraken' && item.market_type === primary.market_type &&
      item.base.asset_id === primary.base.asset_id && item.quote.ticker === 'USD');
    if (!primary || !peer) throw new Error('Demo must provide Binance BTC/USDT and Kraken BTC/USD spot instruments');

    let boundedPrimary = false;
    let depthLimit = 2;
    let primaryBidPrices: number[] = [];
    let primaryAskPrices: number[] = [];
    let peerBidPrices: number[] = [];
    let peerAskPrices: number[] = [];
    let omittedBids = 0;
    let omittedAsks = 0;
    await page.route('**/api/v1/market-data', async route => {
      const response = await route.fetch();
      if (!response.ok()) return route.fulfill({ response });
      const data = await response.json() as NativeMarketData;
      const nativePrimary = data.books.find(book => book.instrument_id === primary.instrument_id);
      const nativePeer = data.books.find(book => book.instrument_id === peer.instrument_id);
      peerBidPrices = nativePeer?.bids.slice(0, 24).map(level => Number(level.price)) ?? [];
      peerAskPrices = nativePeer?.asks.slice(0, 24).map(level => Number(level.price)).reverse() ?? [];
      const books = nativePeer ? [nativePeer] : [];
      if (nativePrimary && boundedPrimary) {
        const bids = nativePrimary.bids.slice(0, depthLimit);
        const asks = nativePrimary.asks.slice(0, depthLimit);
        primaryBidPrices = bids.map(level => Number(level.price));
        primaryAskPrices = asks.map(level => Number(level.price)).reverse();
        omittedBids = nativePrimary.presentation.bids_omitted + nativePrimary.bids.length - bids.length;
        omittedAsks = nativePrimary.presentation.asks_omitted + nativePrimary.asks.length - asks.length;
        books.push({
          ...nativePrimary, bids, asks,
          quality: { ...nativePrimary.quality, coverage: { ...nativePrimary.quality.coverage, full_depth: false },
            reason_codes: [...nativePrimary.quality.reason_codes, 'browser_fixture_partial_depth'] },
          presentation: { depth_limit: depthLimit, bids_omitted: omittedBids, asks_omitted: omittedAsks },
        });
      } else if (nativePrimary && fault === 'ineligible') {
        books.push({ ...nativePrimary, eligibility: { eligible: false, reasons: ['browser_fixture_primary_ineligible'] } });
      }
      await route.fulfill({ response, json: { ...data, books } });
    });
    await page.addInitScript(({ source, instrument }) => {
      localStorage.setItem('limit-orderbook.workspace.v1', JSON.stringify({
        version: 1, source, sourceExplicit: true, instrument,
      }));
    }, { source: { id: source.id, mode: source.mode }, instrument: primary.instrument_id });
    const created = page.waitForResponse(response => response.url().endsWith('/api/v1/sessions') && response.request().method() === 'POST');
    await page.goto(`/?source=${encodeURIComponent(source.id)}`);
    const creation = await created;
    expect(creation.ok()).toBe(true);
    const frame = await creation.json() as Frame;
    expect(frame.selection.instrument_id).toBe(primary.instrument_id);
    await expect(page.locator('.status')).toContainText('Connected');

    await page.getByRole('button', { name: /^Venue:/ }).click();
    await page.getByRole('listbox', { name: 'Venue', exact: true }).getByRole('option', { name: /^All spot/ }).click();
    const book = page.getByRole('region', { name: 'Order Book', exact: true });
    const modes = book.getByRole('radiogroup', { name: 'Book source', exact: true });
    const raw = book.getByRole('radiogroup', { name: 'Price grouping', exact: true }).getByRole('radio', { name: 'Raw', exact: true });
    await raw.click();
    await expect(raw).toHaveAttribute('aria-checked', 'true');
    const bidPrices = async () => (await book.locator('.ladder-bids .ladder-price').allTextContents()).map(text => Number(text.replaceAll(',', '')));
    const askPrices = async () => (await book.locator('.ladder-asks .ladder-price').allTextContents()).map(text => Number(text.replaceAll(',', '')));
    await modes.getByRole('radio', { name: 'Single', exact: true }).click();
    await expect(modes.getByRole('radio', { name: 'Single', exact: true })).toHaveAttribute('aria-checked', 'true');
    await expect(book.getByTestId('primary-book-missing')).toContainText('Primary book unavailable');
    await expect(book.locator('.panel-subtitle')).toContainText(`Single · ${venueLabel(primary.venue)} · ${primary.native_symbol}`);
    await expect(book.locator('.ladder-row')).toHaveCount(0);
    const bookCoverage = book.getByTestId('book-coverage');
    await expect(bookCoverage.locator('details')).not.toHaveAttribute('open');
    await expect(bookCoverage.locator('strong')).toBeVisible();
    await expect(bookCoverage.locator('p')).toBeHidden();
    await bookCoverage.locator('summary').click();
    await expect(bookCoverage.locator('p')).toBeVisible();
    if (fault === 'ineligible') {
      await expect(bookCoverage.getByRole('listitem').filter({ hasText: 'browser_fixture_primary_ineligible' })).toBeVisible();
    }

    await page.getByRole('radiogroup', { name: 'Workspace', exact: true }).getByRole('radio', { name: 'Liquidity', exact: true }).click();
    const depth = page.getByRole('region', { name: 'Market Depth', exact: true });
    await expect(depth.getByTestId('primary-book-missing')).toContainText('No peer book is substituted');
    await expect(depth.locator('.panel-subtitle')).toContainText(`Single · ${venueLabel(primary.venue)} · ${primary.native_symbol}`);
    await expect(depth.locator('.stat').filter({ hasText: 'Observed ±10 bp bid' })).toContainText('—');
    const depthCoverage = depth.getByTestId('book-coverage');
    await expect(depthCoverage.locator('details')).not.toHaveAttribute('open');
    await expect(depthCoverage.locator('strong')).toBeVisible();
    await expect(depthCoverage.locator('p')).toBeHidden();
    await depthCoverage.locator('summary').click();

    for (const mode of ['Consolidated', 'Compact']) {
      await modes.getByRole('radio', { name: mode, exact: true }).click();
      await expect(modes.getByRole('radio', { name: mode, exact: true })).toHaveAttribute('aria-checked', 'true');
      await expect(book.locator('.panel-subtitle')).toContainText(`${mode} cohort · ${venueLabel(peer.venue)} · ${peer.native_symbol}`);
      await expect(book.locator('.panel-subtitle')).not.toContainText(venueLabel(primary.venue));
      await expect(book.getByTestId('primary-book-missing')).toHaveCount(0);
      await expect.poll(() => peerBidPrices.length).toBeGreaterThan(0);
      await expect.poll(() => peerAskPrices.length).toBeGreaterThan(0);
      await expect(book.locator('.ladder-bids .ladder-row')).toHaveCount(peerBidPrices.length);
      await expect(book.locator('.ladder-asks .ladder-row')).toHaveCount(peerAskPrices.length);
      await expect.poll(bidPrices).toEqual(peerBidPrices);
      await expect.poll(askPrices).toEqual(peerAskPrices);
      await expect(bookCoverage.locator('p')).toContainText(/24\/side/);
      await expect(bookCoverage.getByRole('listitem').filter({ hasText: /cross-quote/ })).toBeVisible();
      await expect(depth.getByTestId('primary-book-missing')).toHaveCount(0);
      await expect(depth.locator('.panel-subtitle')).toContainText(`${mode} cohort · ${venueLabel(peer.venue)} · ${peer.native_symbol}`);
      await expect(depthCoverage.locator('p')).toContainText(/100 levels\/side/);
    }

    boundedPrimary = true;
    await modes.getByRole('radio', { name: 'Single', exact: true }).click();
    await raw.click();
    await expect(raw).toHaveAttribute('aria-checked', 'true');
    await expect.poll(() => primaryBidPrices.length).toBeGreaterThan(1);
    await expect.poll(() => primaryAskPrices.length).toBeGreaterThan(1);
    await expect(book.locator('.ladder-bids .ladder-row')).toHaveCount(primaryBidPrices.length);
    await expect(book.locator('.ladder-asks .ladder-row')).toHaveCount(primaryAskPrices.length);
    await expect.poll(bidPrices).toEqual(primaryBidPrices);
    await expect.poll(askPrices).toEqual(primaryAskPrices);
    await expect(book.getByTestId('primary-book-missing')).toHaveCount(0);
    await expect(depth.getByTestId('primary-book-missing')).toHaveCount(0);
    for (const panel of [book, depth]) {
      const coverage = panel.getByTestId('book-coverage');
      await expect(coverage.locator('p')).toContainText(new RegExp(`Received ${primaryBidPrices.length} bid / ${primaryAskPrices.length} ask`));
      await expect(coverage.locator('p')).toContainText(new RegExp(`limit ${depthLimit}/side`));
      await expect(coverage.getByRole('listitem').filter({ hasText: 'browser_fixture_partial_depth' })).toBeVisible();
    }

    // Truncate genuine native depth separately from restoring its two Raw rows.
    // A two-level source cannot produce omitted rows when sliced to two.
    depthLimit = 1;
    await expect(book.locator('.ladder-bids .ladder-row')).toHaveCount(1);
    await expect(book.locator('.ladder-asks .ladder-row')).toHaveCount(1);
    await expect.poll(() => omittedBids).toBeGreaterThan(0);
    await expect.poll(() => omittedAsks).toBeGreaterThan(0);
    await expect.poll(bidPrices).toEqual(primaryBidPrices);
    await expect.poll(askPrices).toEqual(primaryAskPrices);
    for (const panel of [book, depth]) {
      const coverage = panel.getByTestId('book-coverage');
      await expect(coverage.locator('p')).toContainText(/Received 1 bid \/ 1 ask/);
      await expect(coverage.locator('p')).toContainText(/limit 1\/side/);
      await expect(coverage.locator('p')).toContainText(new RegExp(`omitted ${omittedBids} bid / ${omittedAsks} ask`));
      await coverage.locator('summary').click();
      await expect(coverage.locator('details')).not.toHaveAttribute('open');
      await expect(coverage.locator('p')).toBeHidden();
      await expect(coverage.locator('strong')).toBeVisible();
    }
    // Display mode does not retarget financial/native session selection.
    const current = await page.request.get(`/api/v1/sessions/${frame.session_id}`);
    expect(current.ok()).toBe(true);
    expect((await current.json() as Frame).selection.instrument_id).toBe(primary.instrument_id);
  });
}
