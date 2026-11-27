> **Publication editor's note — not part of the independent review.** The complete report below is reproduced verbatim from private review snapshot `1933ec89c242057c41e23d3ca2de0e7c11ad576a` (historical **LOCAL-ONLY** provenance, not a public dependency). Its author's findings, corrections, dispositions and execution limits are unchanged. “Current,” “final,” and “retained” below refer to that historical packet, not this smaller public selection.
>
> Public evidence: [selected capture manifest and hashes](evidence/captures.json), [original failed clipped-bid PNG](evidence/regressions/clipped-bid-1280.png), and [public verification history and limits](verification.md). The manifest identifies retained images; it does not recreate the full reviewed matrix. Original inline path citations absent from this public packet—including omitted manifests, attempt directories, raw receipts, inspection files and design references—are historical **LOCAL-ONLY** citations, not public links. Shorthand paths and source line numbers retain their historical context; the regression PNG above is the public copy of the original attempt image.
>
> The owner subsequently approved the bounded implementation. That later decision does not rewrite this review or authorize release/PR2. Publication curation is neither a new independent review nor a fresh full offline/Chromium rerun; the new publication SHA still requires owner approval before pushing.

---

# Independent production visual review — PR1

> The initial reviewed-candidate disposition is retained below as history. The dated final independent disposition at the end governs the repaired candidate.

## Disposition

**Local PR1 visual acceptance is blocked on the current evidence set.** The Graphite/Paper token direction, squared compact shell, field hierarchy at 1440 px and above, and native book/tape treatment are credible and materially better than the baseline. However, both 1280×800 after images show a laptop-layout regression: the Price field is reduced to the upper part of its scale after the enlarged toolbar wraps, and the order-book bid touch is clipped at the bottom of its tile.

This retains the original B.2 disposition: **approve the design direction, not every production paint and not a release.** A repaired candidate needs fresh pixels before either 1280 defect can be called resolved. No PR2 navigation, lens, density, resize, or layout work is requested here.

**Release recommendation:** no release judgment is available from this review. Screenshot review is neither accessibility proof nor speed evidence, and this PR1 review does not authorize merge, deployment, a default-theme change, PR2, or financial/live action.

## Evidence status vocabulary

- **Observed:** I opened and inspected the named production JPEG pixels in this review.
- **Source-reviewed:** I read the production source or receipt named in the statement.
- **Inferred:** the conclusion follows from observed pixels plus source/geometry, but was not exercised interactively by this reviewer.
- **Missing:** the current packet does not show the state; no positive claim is made.

## Exact sampled scope

### Reviewed candidate — all 19 historical images opened

| Slice | Files opened | What it establishes |
|---|---|---|
| Matched Graphite/Paper | `evidence/attempts/reviewed-candidate/after-{dark-dull,light-dull}-{1280x800,1440x900,1920x1080,2560x1440,3840x2160}-dpr1-price.jpg` | Ten same-resolution neutral-theme paints. |
| Narrow Graphite/Paper | `evidence/attempts/reviewed-candidate/after-{dark-dull,light-dull}-390x844-dpr1-price.jpg` | Two initial narrow viewport paints, not a scrolled journey. |
| Retained identities | `evidence/attempts/reviewed-candidate/after-{dark,light,forest,futuristic}-1440x900-dpr1-price.jpg` | Midnight, Daylight, Forest, and Neon at one CSS size. |
| Other native views | `evidence/attempts/reviewed-candidate/after-dark-dull-1440x900-dpr1-{liquidity,footprint}.jpg` | One Graphite liquidity and one Graphite footprint paint. |
| Scale sample | `evidence/attempts/reviewed-candidate/after-dark-dull-1440x900-dpr2-price.jpg` | One 1440×900 CSS / DPR 2 paint. |

### Production before — matched comparison pixels opened

I opened all 13 pairwise baseline files in `evidence/before`: Graphite and Paper Price at 1280×800, 1440×900, 1920×1080, 2560×1440, 3840×2160 and 390×844, plus Graphite 1440×900/DPR 2. I also opened `evidence/before/footprint-graphite-1920.jpg` and `evidence/before/footprint-paper-1920.jpg` as ancillary pre-change native-view references. `attempts/` was not treated as final evidence.

### Original B.2 and native references opened

I opened `docs/terminal-redesign/b2/evidence/match-b2-market-1920.jpg`, `match-production-market-1920.jpg`, `market-1280.jpg`, `market-390.jpg`, `market-3840.jpg`, `footprint-dense.jpg`, `light-market.jpg`, `native-pixel-details.jpg`, `native-positioning-5000.jpg`, `tokens-graphite-states.jpg`, and `tokens-paper-states.jpg`. I also source-reviewed `docs/terminal-redesign/b2/tokens.css`, `review-visual.md`, and `verification.md`.

The design renders are comparison references only. They are not implementation evidence and are not used to override actual production pixels.

## Provenance and receipt limits

**Source-reviewed.** `evidence/attempts/reviewed-candidate/after-manifest.json` reports `status: "passed"`, Chromium `145.0.7632.6`, Playwright `1.58.1`, and collector SHA-256 `2306cd9c236b633cf21ac1da8d958fedc90b033b87e8eff49beabc0d1dd76a67`; the before manifest records the same collector SHA. The after selection is offline `synthetic-demo-v3-180s`, Coinbase Advanced Trade spot `BTC-USD`, primary scope, session trades, 1m, paused at delivery/ordinal **3485 of 6970**. Each viewport receipt reports one native create request. This is the actual production UI and native synthetic demo, not the authored B.2 specimen.

The matrix is deliberately not a six-theme-by-six-viewport cross product. It uses viewport screenshots (`fullPage: false`), and the manifest explicitly says the narrow image need not include the top bar. The 390 document is 390×2976 CSS px with no horizontal document overflow; the screenshot is only the first 844 px. The packet therefore establishes source identity in receipts and visible bottom status, not visual reachability of every offscreen pane.

The manifest records request cancellations and two response-body read errors at 1920/DPR 1 and 1440/DPR 2 while separately requiring a matching DTO and meaningful paint. I did not reinterpret those as visual defects or silently describe them as a clean-network run.

## Blocking findings

### B1 — Preserve the usable Price field at 1280×800

**Severity: high; local PR1 blocker. Confidence: high.**

**Observed.** In both `after-dark-dull-1280x800-dpr1-price.jpg` and `after-light-dull-1280x800-dpr1-price.jpg`, the chart toolbar wraps to a second line and the resulting plotted Price field shows only the upper portion of the price scale before the volume and lower-study regions. The same source at 1440×900 shows the complete upper/lower scale around 60,000, so this is not a sparse-data explanation. The baseline 1280 images retained more vertical Price field despite their weaker styling.

**Source-reviewed.** The patch raises chart control/text sizes in `frontend/src/price-chart.css:17-30` and permits `.price-chart__toolbar` and `.price-chart__view-actions` to wrap. The balanced grid still gives the chart a fixed existing tile allocation; the receipt records the 1280 primary canvas at only 862×193 CSS px. This is an introduced interaction between PR1 chrome sizing and the existing layout.

**Impact.** Laptop users lose the balanced upper/lower price context in the primary analytical surface, directly weakening the chart-centric PR1 goal. A scrollable chart container does not make a clipped first view equivalent to a usable field.

**Minimal fix.** Keep this existing toolbar to one compact row at the 1280 laptop breakpoint (horizontal control overflow is acceptable), or otherwise reserve enough of the unchanged chart tile for the Price plot. Do not add PR2 layout behavior. Re-capture both neutral themes at 1280×800 after the repair.

### B2 — Keep both native book touches visible at 1280×800

**Severity: high; local PR1 blocker. Confidence: high.**

**Observed.** In both current 1280 after images, the 59,999 bid touch is cut by the order-book tile boundary while the ask and spread remain visible. At 1440 and larger the ask, spread and bid touch are simultaneously readable. This is specifically the existing compact laptop composition, not the future adaptive/resizable book request.

**Source-reviewed.** The patch makes the short order-book tile itself scroll and prevents its body/ladder rows from shrinking in `frontend/src/orderflow/orderflow.css:391-395`, while 22 px ladder rows are retained for short screens at `frontend/src/orderflow/orderflow.css:497-504`. That preserves content in the DOM but leaves a clipped initial native touch in the actual paint.

**Impact.** A user comparing the current two-sided touch must scroll a very short tile to recover the bid, defeating the visible ask/spread/bid relationship that PR1 is styling and that the desktop evidence otherwise demonstrates.

**Minimal fix.** Within the existing 1280/short-screen tile, reclaim enough vertical space from book chrome/caption or use a breakpoint-specific compact row treatment so ask, spread, and bid are simultaneously visible. Do not introduce adaptive widths, resizing, or a new layout owner. Re-capture Graphite and Paper 1280×800.

## Visual assessment of the non-blocked surfaces

### Color discipline and six identities

**Observed and source-reviewed.** Graphite and Paper use the approved neutral ladder almost exactly: Graphite chart `#0c0c0c`, panel `#111111`, surface `#141414`; Paper chart `#fafafa`, panel `#f4f4f4`, surface `#eeeeee`. `frontend/src/orderflow/lib/theme.ts:201-277` maps jade/rose to buy/sell, amber to warning, neutral foreground to funding, and separate critical/focus/control-edge roles. `THEME_OPTIONS` at `theme.ts:279-286` still exposes exactly Midnight, Daylight, Forest, Neon, Graphite and Paper under the existing six IDs.

The actual Graphite/Paper pixels keep general chrome neutral. Selected controls use neutral fill/edge and underline rather than lime/violet decoration. Buy/sell color remains attached to labelled sides, values, rows and bars; the order book and tape retain textual Price/Size/Total and Buy/Sell/Unknown context. Zero session change no longer receives an up/down arrow, and funding is no longer colored as candle direction. These are successful PR1 corrections.

Midnight, Daylight, Forest and Neon remain visibly distinct in their 1440 captures. Forest and Neon intentionally retain identity color in chrome; they should not be cited as examples of the flagship neutral palette. Their preservation is a compatibility success, not evidence that all four have been reviewed at every viewport.

### Field versus panels

**Observed.** Compared with the before images, Graphite removes rounded dashboard cards and luminous decorative selection. The near-black chart field reads as the primary working area, while surface/panel steps and fine rules organize controls, book, tape and studies without pretending every region is a card. Paper uses a white chart field against light-grey shell and panel bands; it remains denser and more workstation-like than the baseline’s rounded admin-card treatment.

At 1440, 1920, 2560 and 3840, the main Price field grows with the viewport and the lower studies remain bounded. The 1920 after receipt records a 1340×209 primary candle canvas plus its volume/time panes; 2560 records 1820×409; 3840 records 2780×809. This is not the original design’s exact geometry, but the field/panel hierarchy is coherent at these sampled widths.

### Legibility, controls, and focus

**Observed.** The move to system sans plus tabular mono and mostly 12–13 px text improves the actual baseline’s faint 10–11 px labels. Numeric columns align; exact values remain distinct from labels; control edges are consistently visible; active tabs/buttons have neutral fill and a redundant lower rule. The Graphite/Paper DPR 1 and Graphite DPR 2 pixels are crisp at source resolution.

**Source-reviewed.** The receipt sampled six keyboard-focus targets per neutral capture with a 2 px light/dark outline and `withinViewport: true`. This is useful focus evidence for those paths, not a keyboard journey or accessibility audit.

At unscaled 3840 CSS px the interface is physically tiny in the displayed raster even though source pixels are crisp. Physical-monitor acuity, OS scaling, zoom, and operator distance remain untested. No readability claim beyond the raster should be made.

### Native book, tape, liquidity, and footprint touches

**Observed.** At 1440 and larger, the order book shows labelled ask and bid rows, a textual spread row, exact quantities, side-colored depth bars, and a labelled bounded-depth warning. Time & Sales keeps Time (UTC), Price, Size, filtered controls, exact rows and a textual Buy/Sell/Unknown summary. These are useful native touches without importing the static B.2 book/tape renderer.

The liquidity capture keeps bid/ask marks and footprint cells tied to labelled SELL/BUY rows and exact axes. The footprint field preserves its native VWAP/POC/bid×ask treatment, but the sparse D3485 scene does not stress the original dense zero/unknown cases. R3/R5-level precision and inspection refinements remain future PR3 constraints and are not demanded here.

### Narrow/mobile source and coverage

**Observed.** The 390 Graphite/Paper capture was intentionally body-positioned at the analytical field, where it gives the Price chart priority, then exposes the order book; the fixed bottom bar visibly says `Synthetic demo` and `Coverage · gap`. BTC-USD · 1m is visible. Ask, spread and bid rows fit in the narrow order-book tile in this initial viewport.

**Source-reviewed.** The receipt reports no horizontal document overflow and a 2976 px document height. Additional canvases are below the 844 px viewport.

**Missing.** The reviewed-candidate packet had no paired page-entry and body-scrolled production JPEGs showing both the top source controls and the later Time & Sales/study positions on the same narrow document. Geometry and DOM presence are not substitutes for pixels. This is an ordinary evidence limit for PR1, not a request to force desktop panes onto a phone and not a PR2 request.

## Contrast evidence — bounded, not accessibility proof

**Source-reviewed.** The after manifest labels contrast samples as diagnostics and `gating: false`. For sampled Graphite/Paper `.status`, `.price-chart__legend`, layout preset, and selected tab text, recorded ratios are approximately 8.264:1–13.259:1 and pass the diagnostic thresholds. The selected tab and layout preset control-edge samples exceed 3:1. Theme and interval selects are explicitly `unavailable` to the sampler because of gradient/group/unsupported color handling; no ratio was fabricated.

The `.telemetry` boundary sample reports only **1.571:1 in Graphite and 1.503:1 in Paper**, failing the diagnostic’s 3:1 non-text threshold. This may be a panel boundary rather than a required control edge, but it prevents a blanket “all non-text contrast passed” claim. The screenshots and these partial computed samples are not WCAG certification, assistive-technology evidence, or proof across all controls, overlays, states and physical displays.

## Earlier-failure disposition

| Earlier issue | Current production disposition |
|---|---|
| Initial design primary field collapsed to a 160 px strip | **Closed only for the original repaired design evidence and production widths ≥1440 in this packet. Reopened at 1280 as B1** because the PR1 toolbar wrap again consumes primary field height. |
| Initial design zero side received footprint tint | **Not disproved, but not re-proven in production.** The current sparse footprint does not exercise the original dense zero/positive and unknown-only matrix. Original design disposition remains historical, not production certification. |
| Paper risked reading as a generic admin surface | **Materially improved in sampled Price views.** Neutral field/panel steps, mono financial values, compact rules and visible native sidecars preserve workstation character. |
| Native book/tape legibility | **Improved at 1440+; blocked at 1280 by B2.** The analytical-field-positioned narrow capture shows the book but, by itself, did not show tape reachability. |
| Footprint needed a current production paint | **Current saved JPEG is intact; preview artifact closed.** Independent source-pixel comparison found the footprint and liquidity JPEG regions byte-identical in the wordmark, source/replay, delivery, and source/coverage footer rectangles (empty `ImageChops.difference` bounding boxes; RGB maximum difference 0). The apparent glyph loss in the review preview was not present in the saved raster and is not attributed to runtime. |
| 4K source-pixel legibility | **Source pixels inspected; physical-display risk remains.** No arm’s-length or OS-scaling claim. |

## Ordinary limits and practical constraints

1. The offline D3485 scene has only two retained 1m candles at 60,000. It is honest native evidence but a weak stress case for candle overlap, dense labels, depth ranges, footprint zeros/unknowns, and rapid visual updates.
2. The four non-neutral themes are sampled only at 1440×900 Price. Their availability is proven; viewport-wide visual quality is not.
3. Graphite DPR 2 is sampled once. Paper DPR 2, browser zoom, font scaling, Safari/Firefox, mobile hardware and OS magnification are missing.
4. The reviewed-candidate narrow evidence was one analytical-field-positioned viewport per neutral theme, not the body top. It does not visually prove all offscreen panes or controls are reachable.
5. Contrast receipts are partial computed diagnostics. They do not cover every text/control/state and include a failed telemetry-boundary sample.
6. The 4K captures prove CSS geometry and source-pixel rendering only, not comfortable physical size.
7. The packet contains no basis for FPS, latency, throughput, resource, trader-performance, or accessibility claims. “Synthetic lag 85 ms” is fixture/status content, not a benchmark.
8. Future adaptive book/tape widths, resizable footprint detail, configurable precision, overlays and richer workflow population remain outside PR1. The absence of those PR2/PR3+ features is not a PR1 defect.

## Recommendation

### Local PR1 review

Hold visual acceptance until B1 and B2 are repaired and fresh matched Graphite/Paper 1280×800 pixels show a usable full Price field and simultaneous ask/spread/bid touch. The saved footprint JPEG has no shell-text defect; inspect the forthcoming fresh capture and unscaled crop as additional evidence, not as repair proof for a nonexistent B3. Preserve the current token mapping, six IDs, native owners, stores, views and grid; the minimal fixes are compact responsive chrome changes inside the existing layout.

### Release

Even after those local blockers close, this report remains only a bounded PR1 visual disposition. Release still requires its separately authorized evidence and decision. Do not cite these screenshots as accessibility, performance, native-feed, financial-correctness, trader-validation, or physical-monitor proof.

---

## Final independent disposition — 2026-11-27

### Decision

**Approve the repaired production PR1 visual treatment for local PR1 acceptance. No visual blocker remains in the final evidence.** The two 1280×800 findings above are closed by fresh Graphite and Paper pixels: the Price controls occupy one horizontally scrollable row, the exact 60,000 Price field and its volume/time context remain visible, both lower study plots have usable ink, and the order book simultaneously shows the 60,001 ask, 60,000 spread/current row, and 59,999 bid. This is a bounded approval of the authorized token/shell work, not release approval and not authorization for PR2.

The historical findings remain above because they were genuine defects in `evidence/attempts/reviewed-candidate/`. They must not be read as defects in the final `evidence/after/` set.

### Final evidence independently inspected

**Observed.** I opened all 19 final JPEGs in `evidence/after/`:

- Graphite/Paper Price at 1280×800, 1440×900, 1920×1080, 2560×1440, 3840×2160 and 390×844;
- Midnight, Daylight, Forest and Neon Price at 1440×900;
- Graphite liquidity and footprint at 1440×900;
- Graphite 1440×900 CSS at DPR 2.

I additionally opened `evidence/inspection/final-footprint-chrome-crop.png`, `narrow-entry-390.jpg`, and `narrow-tape-and-status-390.jpg`. I source-reviewed `evidence/after/after-manifest.json`, `evidence/inspection/native-inspection.json`, `evidence/inspection/contrast-final.json`, and `evidence/gates/laptop.txt`. The baseline contains **13**, not 14, matched `before-*` images: six Graphite/Paper viewport pairs plus one Graphite DPR 2 image. The two ancillary 1920 footprint references and `fresh-entry-1440.jpg` are separate from that count.

The final manifest reports `status: passed`, no collector failures, Chromium 145.0.7632.6, Playwright 1.58.1, the same collector SHA-256 as before, offline `synthetic-demo-v3-180s`, Coinbase spot BTC-USD, primary/session-trades/1m, and D3485/6970. This remains a sparse honest native scene, not an authored dense substitute.

### B1 — 1280 Price field: closed

**Observed.** `evidence/after/after-dark-dull-1280x800-dpr1-price.jpg` and its Paper peer show the full resting composition without the prior two-row toolbar collision. The exact legend reads O/H/L/C 60000 and Vol 0.48 BTC; the current line is 60000; the volume mark and 15:01 time remain visible; chart help/provenance remains below; Cumulative Delta and Volume Z-Score both retain plotted ink and exact summaries. The source has two flat 1m candles, so an empty-looking candle body is accurate evidence rather than a field failure.

**Source-reviewed.** `frontend/src/price-chart.css` now adds a short-desktop rule (`min-width: 966px` and `max-height: 900px`) that keeps the existing toolbar on one horizontal row, gives its overflow to that row, compacts the exact-value legend, and reserves a bounded plot. It does not add a new layout owner or PR2 behavior. `evidence/gates/laptop.txt` records the orchestrator’s bounded full-text, primary-touch, latest-print, and study-plot check as passed; I did not run that check.

The final manifest’s individual Lightweight Charts pane rectangles are 76 px price, 15 px volume and 28 px time-axis canvas at 1280. Those numbers are compact, but the opened pixels show the complete available flat-price evidence rather than the earlier clipped upper scale. This closes the defect for the sampled laptop composition; it does not claim every zoom/font setting.

### B2 — 1280 native touches: closed

**Observed.** Both final 1280 neutral-theme images simultaneously show `60,001 / 3.00`, the `60,000` spread/current row, and `59,999 / 5.00`; the bid row is no longer cut by the tile boundary. The adjacent Time & Sales tile also shows its header and multiple exact rows. The repair stays within existing compact shell spacing and does not implement adaptive widths or resize ownership.

`evidence/inspection/native-inspection.json` independently records the 1280 Binance multi-level check with 60,001 ask and 59,999 bid readable at center and a farther ask reached by wheel. That separate document is useful supporting native evidence, not a replacement for the final matched Coinbase pixels.

### Footprint preview artifact: definitively closed

**Observed.** The final 1440 footprint and liquidity JPEGs have coherent shell, controls, delivery count, source band and footer. `evidence/inspection/final-footprint-chrome-crop.png` shows the wordmark, market controls, source/replay lines and delivery control at source scale without missing glyphs.

**Source-reviewed.** Pillow RGB comparison of both the initial and final saved footprint-versus-liquidity source rasters found zero difference in the wordmark, source/replay, delivery, and source/coverage footer rectangles (`ImageChops.difference(...).getbbox() is None`, maximum RGB difference 0). The earlier apparent loss existed only in the review preview and is not attributed to the JPEG or runtime.

### Final visual assessment

#### Laptop, desktop and 4K hierarchy

At 1280 the final shell is dense but coherent: source/replay controls, instrument context, layout controls, the complete Price evidence, both book touches, tape rows, two studies, workflow dock and status line all fit. At 1440 and 1920, chart field, book/tape and studies retain clearer breathing room. At 2560 and 3840, the plotted field grows while the sidecar remains bounded and exact values stay aligned. The 4K pixels remain source-pixel evidence only; physical size, viewing distance and OS scaling are still untested.

The compact shell no longer relies on rounded consumer cards. Graphite/Paper preserve clear chart (`#0c0c0c`/`#fafafa`), panel (`#111111`/`#f4f4f4`) and shell/surface (`#141414`/`#eeeeee`) steps. Midnight, Daylight, Forest and Neon remain six-ID-compatible and visually distinct. Functional side color is redundant with labels and aligned values.

#### Footprint and liquidity

The final footprint gives its native Bid × Ask field 1063×280 CSS px and keeps VWAP, POC/VA, side percentages, exact summary and manipulation hint visible. The final liquidity view gives each native chart 1065×132 px and retains labelled SELL/BUY footprint rows, exact axes, cursor/freeze/max controls and book/tape sidecar. These sparse Coinbase captures establish PR1 chrome integration, not dense-bin or configurable-precision acceptance for future PR3.

#### Narrow body scrolling and source/coverage

The matched `after-*-390x844-dpr1-price.jpg` files are **body-positioned at the analytical field**, not screenshots of the document’s first page. They show the chart and full ask/spread/bid composition with the fixed `Synthetic demo` / `Coverage · gap` bar.

`evidence/inspection/narrow-entry-390.jpg` separately shows the true narrow page entry: brand, Perp/Spot, BTC/USDT, Hyperliquid, interval, theme, source/workspace, replay controls, context, presets and explicit Price/Liquidity/Flow/Book/Tape destinations. `evidence/inspection/narrow-tape-and-status-390.jpg` is a later body-scroll position showing the book-imbalance tail, a readable Time & Sales table, Cumulative Delta, and the same fixed source/coverage bar. Together they demonstrate ordinary body scrolling from entry to tape/status; they do not force desktop simultaneity onto 390 px.

#### Contrast evidence

`evidence/inspection/contrast-final.json` samples 36 actual surface pixels: six named financial/label inks for each of the six themes (book bid, book ask, tape buy, tape sell, bounded-depth warning and instrument label). The minimum recorded ratio is **5.243:1**. Graphite samples range from 6.392:1 to 16.129:1; Paper from 5.477:1 to 14.814:1. This directly addresses the financial ink inspected here and is stronger than computed-token assertions alone.

It remains a bounded pixel sample, not accessibility certification. It does not cover every control, focus state, overlay, zoom, browser, assistive technology or physical display. The manifest’s separate telemetry-border diagnostic still has a low boundary ratio; that panel boundary is not among the sampled financial inks and no blanket non-text-contrast claim is made.

### Remaining practical constraints

1. D3485 contains only two flat 1m candles at 60,000. It honestly proves sparse native presentation, not dense candle or footprint stress.
2. The four non-neutral themes are visually sampled at 1440 Price and contrast-sampled for six named inks; they are not a full viewport/state cross product.
3. Graphite DPR 2 is sampled once. Paper DPR 2, browser zoom, font scaling, Safari/Firefox, OS magnification and physical mobile hardware remain outside this packet.
4. The 4K review is source-pixel inspection, not arm’s-length acuity testing.
5. Screenshot and pixel evidence does not establish FPS, latency, throughput, resource use, accessibility, trader performance, financial correctness, or live-feed behavior.
6. Adaptive/resizable book/tape, footprint detail resizing, configurable precision, overlays, navigation and richer destinations remain later milestones. Their absence is not a PR1 defect.

### Final recommendation

**Local PR1 visual review: accept.** B1 and B2 are closed by fresh actual production pixels; the footprint-preview concern was never present in source rasters. Preserve this bounded token/shell scope and the six existing IDs, stores, views, grid, and native owner.

**Release: no recommendation.** This review does not authorize merge, deployment, public media, default changes, PR2, release, or live/financial action. Those require their separately scoped review and owner decision.
