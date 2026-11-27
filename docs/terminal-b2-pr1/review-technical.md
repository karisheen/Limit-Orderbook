> **Publication editor's note — not part of the independent review.** The complete report below is reproduced verbatim from private review snapshot `1933ec89c242057c41e23d3ca2de0e7c11ad576a` (historical **LOCAL-ONLY** provenance, not a public dependency). Its author's findings, corrections, dispositions and execution limits are unchanged. “Current,” “final,” and “retained” below refer to that historical packet, not this smaller public selection.
>
> Public evidence: [selected capture manifest and hashes](evidence/captures.json), [original failed clipped-bid PNG](evidence/regressions/clipped-bid-1280.png), and [public verification history and limits](verification.md). The manifest identifies retained images; it does not recreate the full reviewed matrix. Original inline path citations absent from this public packet—including omitted manifests, attempt directories, raw receipts, inspection files and design references—are historical **LOCAL-ONLY** citations, not public links. Shorthand paths and source line numbers retain their historical context; the regression PNG above is the public copy of the original attempt image.
>
> The owner subsequently approved the bounded implementation. That later decision does not rewrite this review or authorize release/PR2. Publication curation is neither a new independent review nor a fresh full offline/Chromium rerun; the new publication SHA still requires owner approval before pushing.

---

# Independent technical review — terminal B.2 PR1

## Scope and evidence labels

Reviewed the production worktree against `7b3c0768ed4bff30b4ef89c8030da458e48a6e47`, including the focused frontend diff, the current collector, the real before/after manifests and images, and the supplied gate logs. I did not run builds, tests, formatters, or collectors.

- **Observed** means directly present in source, a stored manifest/image, or a supplied gate transcript.
- **Source-reviewed** means established from the current implementation and its dispatch/consumer path, without executing it.
- **Inferred** means a conclusion from those observations.
- **Missing** identifies evidence not supplied by this PR1 packet.

## Concrete defect

### P2 — Keep both primary book touches visible in the bounded tile

**Affected source:** `frontend/src/orderflow/orderflow.css:392-394` (`.orderbook-panel .panel-body` and `.orderbook-panel .ladder, .orderbook-panel .ladder-side`).

**Observed:** this patch changes the order-book body, ladder, and both sides from their existing flexible bounded layout to `flex: none; overflow: visible`, while the outer `.orderbook-panel` becomes the scroll owner. At the balanced 1280×800 layout, the resulting full ladder is taller than its four-row grid tile. The final stored image `evidence/after/after-dark-dull-1280x800-dpr1-price.jpg` shows the best ask and spread but clips the best-bid row at the lower panel edge. The supplied current `evidence/gates/shell-acceptance.txt` records the same condition as a failed real-session assertion at `shell-frame.spec.ts:431`: `.ladder-bids .ladder-price` is not readable at rest. The failure screenshot shows only the upper portion of the bid row. The baseline image retains a bounded order-book tile rather than exposing an over-height ladder.

**Impact:** the default balanced laptop composition no longer presents both primary touch prices simultaneously. A user must scroll inside a compact reference tile to recover one side of top of book, contrary to this PR's chart-centric compact-shell objective. This is a presentation defect, not evidence that the native book value or financial state is wrong.

**Practical fix:** restore flex ownership to the bounded body and sides; retain horizontal scrolling/min-content handling separately for long exact strings. For example:

```css
.orderbook-panel .panel-body { flex: 1 1 0; overflow: hidden; }
.orderbook-panel .ladder, .orderbook-panel .ladder-side { flex: 1 1 0; overflow: hidden; }
```

The existing `.ladder-asks { justify-content: flex-end; }` then keeps the best ask adjacent to the spread, while the bid side starts with the best bid. Recheck the existing 1280×800 and 1440×900 real-session assertions after the CSS repair.

**Disposition:** concrete local-review blocker for the compact balanced shell. Severity P2; confidence 0.99.

## Theme, token, and preference disposition

### Default and six identities

- **Source-reviewed:** `useUIStore` changes only the unsaved default from `dark` to `dark-dull` (`uiStore.ts:77`); the persisted store remains `limit-orderbook.orderflow.ui-preferences`, version 3. `THEME_OPTIONS` still exposes exactly the six established IDs: `dark`, `light`, `forest`, `futuristic`, `dark-dull`, and `light-dull`. No seventh production theme appears.
- **Source-reviewed:** the static `:root` values in `orderflow.css:7-48` match the Graphite values in `THEMES["dark-dull"]`, providing a Graphite first paint before hydration. `Terminal.tsx:36-43` manually rehydrates after mount and applies the current store theme in a layout effect.
- **Source-reviewed:** v3 saved values merge over the new default. A v2 persisted UI payload also enters Zustand's unchanged additive `migrate` and `merge`, so its theme and indicators are retained. The workspace store remains `limit-orderbook.workspace.v1` payload v2; `workspace.ts` still validates v1/v2 and retains the four legacy slot migration path. The focused patch does not change those contracts.
- **Observed:** `shell-frame.spec.ts:126-194` uses actual native demo sessions and proves all six v3 theme choices survive reloads without changing the saved layout or session within an owner. Midnight is explicitly included as the former default.
- **Missing:** there is no direct test that pre-seeds a version-2 **UI** payload and demonstrates its migration under this change. The source path is intact, so this is a coverage limitation rather than a demonstrated defect. A separate migration test should seed v2 with `theme: "dark"`, custom indicators, and panel visibility, then assert version 3 persistence without resetting unrelated values.

### Token APIs, fallback, and hydration

- **Source-reviewed:** `ThemeTokens` adds semantic roles for chart background, focus, control edge, critical, OI, and funding. Every one of the six palette records supplies every role. Existing IDs and `applyTheme` remain the sole runtime token application entry point.
- **Source-reviewed:** HTML/CSS uses the same roles as canvases. `readPalette` deliberately maps its stable existing `Palette.bgPanel` API to `--bg-chart` for plot fills, avoiding a second canvas palette API. `PriceChart.themeColors` and `MarketRenderer.updateTheme` read computed tokens and use the selected registered palette only when CSS is unavailable. The initial fallback is Graphite; an invalid runtime ID falls back to Midnight rather than yielding an empty color.
- **Source-reviewed:** `canvasThemeRevision` invalidates the shared palette cache when root theme/style/class changes. The shared scheduler and native chart renderer consume that invalidation; no new token engine or renderer was introduced.
- **Observed:** the changed pixel expectations in `canvas-presentation.spec.ts:72-78` and `chart-redesign.spec.ts:368-380` still compare the actual canvas corner pixel to a separately painted reference pixel. They now use `--bg-chart`, which is the production plot-fill role consumed by `readPalette` and `MarketRenderer`; this is a semantic migration, not a weakened assertion.

## Lifecycle and successful-paint ownership

- **Source-reviewed:** native chart identity includes interaction scope, instrument, generation, clock, source identity, and scope ID (`MarketCharts.tsx:89-90`). `ChartPane.tsx:50-77` additionally fences presented state by session, generation, source mode/ID, selected instrument, and clock-scope revision. A new value crosses into the presentation consumer only when that identity matches.
- **Source-reviewed:** hidden/offscreen native canvases do not draw. `MarketCharts` checks document visibility, intersection state, and positive size before scheduling. `PriceChart` similarly gates history/positioning presentation on active + visible + intersecting state while retaining its viewport and exact cursor state.
- **Source-reviewed:** the native renderer reports presentation only after a successful draw and two visible animation-frame opportunities (`MarketCharts.tsx:176-214`); clear/error paths call `onPresented(null, ...)`. Theme changes invalidate colored tiles without replacing source identity or viewport. Freeze remains pane-owned through `ChartPane` rather than theme-owned.
- **Source-reviewed:** shared order-flow canvases use one event-driven scheduler. It coalesces owners into one RAF, suspends hidden/offscreen owners, latches a failed owner until explicit retry, clears invalid pixels, and continues siblings. The cross-boundary failure/retry events are consumed by the panel failure UI; they are not silently dropped.
- **Observed:** `canvas-scheduler.test.ts` covers hidden/offscreen suspension, retained latest data, theme/DPR invalidation, failure isolation, disposal, and missing contexts. `canvas-presentation.spec.ts` uses a real backend session and injects only the draw operation for its failure case; successful market evidence is not replaced. `chart-redesign.spec.ts` separately labels its long-value test as representation stress and pads only trailing decimal zeroes in intercepted DTOs.

No patch-introduced source/session/generation, freeze, hidden-pane, or successful-paint ownership defect was found.

## Native meaning, units, unknowns, and long values

- **Source-reviewed:** Price action preserves exact decimal strings in its readouts and table. OI uses the native `open_interest_unit`; funding remains a signed exact fraction with explicit interval text and a neutral `--funding` series rather than buy/sell direction. Missing OI/funding leaves no empty pane and remains “not observed,” not zero.
- **Source-reviewed:** native market charts retain `quoteUnit` and `quantity_unit`; footprint unknown volume has its own `?` lane and neutral token. Resting-liquidity null cells remain hatchable/unknown rather than observed zero. Exact inspection remains available in the inspector when plotted labels compact.
- **Source-reviewed:** long price/value labels are measured before reducing axis type size. Exact DOM readouts use wrapping or local scrolling, and the Price action legend/OI strip use fixed-height horizontal overflow so inspection cannot resize the plot.
- **Observed:** `chart-redesign.spec.ts:649-705` verifies padded exact OHLC, volume, OI, and funding strings scroll while narrow plot geometry stays stable. Because that case intentionally intercepts and representation-pads native responses, it is precision/layout evidence, not proof of a naturally occurring venue payload.
- **Missing:** the final collector pins Coinbase spot and explicitly turns OI/funding off. It therefore does not provide real final-image evidence for a perpetual's OI/funding pane or naturally long venue values. Existing real-session E2E plus the injected representation stress are useful but should not be described as equivalent to a final perpetual collector capture.

## Focus, reachability, and PR2 boundary

- **Source-reviewed:** focus, control-edge, neutral selected, and disabled-state roles are applied across shell, native panes, grid controls, and chart controls. Scrollable session metrics and exact readouts are keyboard focusable; chart plots retain keyboard navigation and visible focus.
- **Observed:** the real-session shell tests measure text/control-edge contrast for Graphite/Paper and reach representative controls through actual Tab traversal. The collector stores additional focus/contrast diagnostics but explicitly marks them non-gating. Some collector samples are unavailable for native select gradients, and decorative telemetry borders are below 3:1; neither is represented as a complete WCAG audit.
- **Observed:** the compact at-rest test is stronger than mere `toBeVisible()`: it clips against every scroll/paint ancestor and hit-tests the readable area. That check exposed the order-book regression above; it should not be deleted or relaxed.
- **Source-reviewed:** no task rail, command directory, destination model, new density preference, lens ownership, new panel model, or renderer migration was added. Existing grid presets, named layouts, chart tabs, and one native owner remain. The styling changes do not authorize PR2 behavior.

## Collector and stored evidence

- **Observed:** `tools/capture_shell_frame.mjs` is a loopback-only, actual-backend collector. It requires the exact `synthetic-demo-v3-180s` source, Coinbase spot instrument, delivery 3485/6970, primary `session_trades`, balanced layout, and one native session per viewport context. It does not seed preferences, intercept responses, import prototype data, or perform direct state mutation. Public controls perform selection, seek, layout, view, interval, and theme changes.
- **Observed:** the current collector SHA-256 is `2306cd9c236b633cf21ac1da8d958fedc90b033b87e8eff49beabc0d1dd76a67`; both final manifests record that same hash. Before is `passed` with 13 captures/7 native creates; after is `passed` with 19 captures/7 native creates. Every final capture is pinned to the requested source, instrument, generation 2, delivery 3485, one create, no horizontal document overflow, and a meaningful matching paint.
- **Observed:** the early preset attempt failed because it treated the preset command select as persisted selected state. The final collector instead inspects mounted logical grid geometry. The superseded after attempt failed closed when liquidity paint could not be matched to a native visualization DTO. The final collector requires painted/drawn view identity, mode, tiles, generation, evidence timestamp, matching request/response DTO, and meaningful settled pixels before capture. These are valid rectifications; the failed attempts remain under `evidence/attempts` and are not presented as final.
- **Observed limitation:** final runs record expected aborted discovery requests and two response-body read races on obsolete/closing market-data requests. They have no page errors, HTTP errors, or unexpected network, and each capture separately requires successful matching evidence. The manifest correctly records rather than hides these events.
- **Important limitation:** collector `status: passed` means its price/liquidity/footprint identity and paint contracts passed. It does not assert both book touches, latest tape row, or study plot reachability in the captured balanced tile. The final 1280 image visibly contains the book clipping, and the stronger current real-session acceptance test fails it. The manifest cannot override that defect.

## Independent disposition and residual recommendations

The theme/token/default integration, six IDs, unchanged storage versions, renderer ownership, exact-value semantics, and real collector provenance are technically coherent in source. One concrete compact-shell regression remains: the new non-flex order-book ladder hides the best bid at rest in the default balanced laptop layout. Repair the two CSS ownership rules and preserve the new at-rest assertion unchanged.

For **local PR1 review**, treat the supplied current shell-acceptance failure as unresolved until the affected real-session test passes after that targeted repair. Also add direct v2 UI-preference migration coverage if the local gate is intended to claim old-user migration explicitly.

For any later **release evidence**, retain the stated pairwise/diagnostic limits: the collector is synthetic offline spot evidence, not live-feed, physical-display, complete WCAG, or perpetual OI/funding certification. PR2 remains outside this review and requires its own authorization and ownership review.


---

## Final corrected-source re-review

This addendum reviews the corrected sources and replacement receipts. It preserves the initial P2 rejection above as the historical finding and supersedes only its current-state disposition and the earlier evidence limitations that the correction addressed.

### P2 closure — bounded native book touches

**Source-reviewed:** the rejected `flex: none; overflow: visible` ownership is gone. The final implementation at `frontend/src/orderflow/orderflow.css:394-401` now:

- bounds the outer order-book panel;
- leaves the body flexible and scrollable;
- derives the ladder minimum from one ask row, one bid row, the spread, and heading dimensions;
- gives each side an independent bounded scroll region; and
- reverses the ask-side flex origin so the native best ask remains adjacent to the spread while farther asks remain reachable.

`OrderBook.tsx:209-213` supplies a non-flexing ask-row wrapper around the unchanged far-to-touch DOM order, and `OrderBook.tsx:229-233` keeps bids in native best-to-far order. The producing data remains `groupLevels`: asks are sorted ascending and bids descending before the fixed 24-row display bound. The correction therefore changes presentation ownership only; it does not reorder the native source, recompute prices, or substitute a peer book.

**Observed:** the replacement `evidence/after/after-dark-dull-1280x800-dpr1-price.jpg` visibly includes the best ask, spread, and best bid simultaneously in the balanced four-row tile. `evidence/gates/laptop.txt` records the unchanged focused real-session test passing at both 1280×800 and 1440×900; that test still matches both rendered touches to the admitted native book, the first tape row to the latest retained native print, checks usable CVD/volume plot space, and exercises keyboard reachability without replacing the owner.

**Observed independent inspection:** `evidence/inspection/native-inspection.json` records one separate native owner and an unmodified Binance two-level book. It identifies `60,001` and `59,999` as the touch rows, records farther `60,002`/`59,998` outside the centre hit area, and records the farther ask becoming reachable by a public wheel interaction. `binance-touch-1280.jpg` shows the touches adjacent to the spread. This directly challenges the risk that the fix merely deleted far depth or fabricated a one-level book.

**Disposition:** the original P2 blocker is resolved for local PR1 review. The exact failed receipt remains correctly retained at `evidence/attempts/laptop-touch/regression.txt`; it is no longer the current gate. Confidence 0.99.

### Corrected preference, focus, and financial-role evidence

- **Observed/source-reviewed — v2 → v3:** `shell-frame.spec.ts:193-273` now contains a direct historical browser-storage case. It starts with UI payload version 2, saved Midnight, Volume footprint mode, legacy order-book visibility, heatmap state, and indicator overrides. It asserts production writes version 3, removes retired `orderBook` from the UI payload, migrates visibility into workspace v2, preserves unrelated settings, and restores the same result after a real-session reload. The earlier “missing direct v2 test” limitation is closed. The supplied `focus-edge.txt` run records this case and all six v3 theme restores passing before its later, unrelated focus-edge failure.
- **Source-reviewed — focus edge:** `orderflow.css:357` now moves focus outlines inside the clipped single-row canvas-panel header. The final focused screenshot shows the last Volume Z lookback radio fully visible with its outline, and `laptop.txt` records the complete laptop scenario passing after that correction. The failed intermediate focus receipt remains in `attempts/laptop-touch/focus-edge.txt` rather than being represented as final success.
- **Observed/source-reviewed — six-theme financial ink:** `inspection/contrast-final.json` samples actual book bid/ask text, tape buy/sell text, bounded-depth warning, and instrument text for all six IDs. Every recorded final text ratio is at least 5.243:1, including Daylight after its `--buy`, `--sell`, related dim/background/border, and warning roles were darkened in `theme.ts`. The samples are targeted rendered combinations, not a complete WCAG audit.
- **Observed — natural perpetual units:** `native-inspection.json` and `hyperliquid-positioning-1920.jpg` use an unmodified native Hyperliquid perpetual response. They preserve base BTC, quote USDT, settlement USDC, OI `1260 base` with delta `55`, and funding `0.00002 fraction per 1h`, matching the exact retained observation and generation. This closes the prior lack of natural final OI/funding evidence without converting it into financial-accounting proof.
- **Observed — narrow and empty states:** `narrow-tape-and-status-390.jpg` shows the independently scroll-reached native tape and persistent source/coverage status; `empty-prefix-1440.jpg` and the JSON receipt show delivery zero with unavailable book/prints/OI/funding and em dashes rather than fabricated zero observations.

No new patch-introduced type/value routing defect was found in these corrections. Book rows still enter the existing ladder branch, persisted v2 values still enter Zustand's existing migration/merge and workspace migration consumers, and positioning values still enter the exact Price action readout and neutral OI/funding series.

### Replacement collector receipts

**Observed:** the original 19-image reviewed candidate is preserved under `evidence/attempts/reviewed-candidate`; it is not the final `after` directory. The final before and after manifests both record collector SHA-256 `2306cd9c236b633cf21ac1da8d958fedc90b033b87e8eff49beabc0d1dd76a67`, the same exact source/instrument/delivery/interval/layout contract, seven fresh viewport contexts, and exactly one native create in each context.

The 13 matched before/after comparison records agree on comparison key, source `synthetic-demo-v3-180s`, Coinbase spot instrument, primary scope, `session_trades` 1m interval, generation 2, delivery 3485, one owner, native-price readiness, and meaningful settled paint. Final after adds the four other theme identities plus native-liquidity and display-footprint views for 19 total captures. The after manifest records 16 request-abort notes but zero HTTP errors, page errors, response-body read errors, unexpected network, or collector failures. Those abort notes remain visible and are not reclassified as successful responses.

This replacement evidence is materially stronger than the reviewed candidate because the final 1280 capture contains the corrected touch layout and because response-body read races are absent. It remains pairwise synthetic offline evidence, not a six-by-all-view cross product or live-market capture.

### Final local technical disposition and residual limits

For the bounded PR1 local review, I find no remaining concrete defect in the corrected source/evidence set. The initial order-book P2 rejection is closed; the direct v2 migration gap and natural OI/funding evidence gap are also closed. This is a local technical disposition, not a blanket merge, release, deployment, or PR2 authorization.

Residual limits remain explicit:

1. I did not execute any gate. `laptop.txt`, `final-build.txt`, and `fast-final.txt` are observed lead-owned receipts only.
2. The last supplied full six-case shell run predates the final focus-edge correction and failed only that edge; the final supplied focused laptop run passed afterward. Full offline/browser gates are lead-owned and in flight, so no unobserved full-gate pass is asserted here.
3. The collector remains offline synthetic evidence. The separate Binance/Hyperliquid inspection improves native-content coverage but does not establish live-feed behavior, exchange completeness, performance capacity, physical-display readability, or financial/accounting suitability.
4. The contrast receipts cover deliberate financial text samples; they are not complete accessibility certification for every control, state, composited pixel, browser, or display.
5. PR2 navigation, lens, density-preference, and adaptive/resizable-layout work remains outside this patch and requires separate authorization and review.

### Test-only lifecycle correction after final disposition

**Observed:** the first full Chromium receipt at `evidence/attempts/lifecycle/full-first.txt` records 115 passes, three live-only skips, and one failure in the pre-existing held-reset lifecycle test setup. `native-responses.json` shows the real sequence on one session: bootstrap cursor 169/generation 1, Step cursor 170/generation 1, then committed held Reset cursor 0/generation 2. The old `not /^0/` setup assertion could succeed on bootstrap cursor 169 before the Step response was painted, so its later `before` comparison could incorrectly expect 169 while the authoritative Step paint advanced to 170.

**Source-reviewed:** the test-only change in `frontend/e2e/lifecycle.spec.ts:278-285` now awaits the real Step HTTP response, asserts its delivery index is positive, and awaits that exact authoritative cursor in the DOM before initiating the held Reset. The remaining assertions are unchanged: the reset body is held after a real backend commit; controls become busy then recover to an explicit ambiguous outcome; committed generation evidence arrives; there is no automatic retry or session replacement; a later explicit Step owns newer evidence; and releasing the late body cannot roll it back. This correction removes a race in setup and strengthens the intended lifecycle coverage without changing production behavior or narrowing the mutation-safety contract.

**Disposition:** test correction accepted for local review. No rerun result is claimed; the failed first full-browser receipt remains an attempt, and the pending rerun is lead-owned.

The canvas and laptop helpers' new pre-decode `POST` plus exact outgoing session/generation fence is a non-masking cutover fix: it excludes only provably obsolete requests before touching an unreadable body, while matching responses must still decode, match response-body session/generation, reach `has_more === false`, and satisfy the existing paint, idle, and negative assertions; no final rerun result is claimed.

The source-loss test's pre-suppression read-only `MutationObserver` strengthens rather than masks the stale-hold contract: it atomically snapshots the first exact `Stale snapshot` commit and requires the original generation plus all five original readouts and visible status before the legitimate reconnect can clear them, retains the same 8-second bound and post-release replacement/fresh-state assertions, injects no presentation state or retry, and has no claimed rerun result.
