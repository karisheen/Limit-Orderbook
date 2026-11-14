# Chart redesign plan

Status: **implemented and verified**. Final gate receipts, actual implementation screenshots and finite live/browser observations are recorded below. These checks do not imply all-provider availability or sustained collector capacity.

## Goal

Make Price action behave like a conventional historical ticker chart without weakening the application's exact economics, observed-flow provenance, or causal replay. Improve the visual consistency of price, volume, positioning, footprint and liquidity views.

Design preview: [chart-redesign-mockup.html](media/chart-redesign-mockup.html). The preview remains a static layout study with synthetic illustrative data, not a working chart, measured performance result, or verified provider history. Production uses TradingView Lightweight Charts **5.3.0**, not the preview's SVG. [Actual implementation captures and observations](#recorded-implementation-observations) are separate evidence.

## Decisions

- Adopt TradingView Lightweight Charts **5.3.0** for standard price, volume and compatible positioning panes, pinned in the manifest/lockfile with license, NOTICE and attribution. It is a renderer, not a market-data supplier or the complete TradingView drawing/indicator product.
- Retain custom liquidity, footprint and other specialized order-flow rendering. Do not rewrite those renderers merely to standardize colors.
- Keep two explicit data products: **Ticker history** (one exact native instrument, exchange timestamps) and **Observed flow** (selected cohort, projected receive time, partial retained evidence).
- Live Price action defaults to Ticker history. Demo/replay defaults to Observed flow. Never silently substitute sources or venues when historical data is unsupported.
- Use existing native venue adapters first. OpenMarket is not a renderer dependency: a separately licensed data-provider integration would require its own decision on access, redistribution rights and provenance.
- No synthetic filler candles, cross-quote conversion assumptions, retrospective book reconstruction, or current-history leakage into replay.
- Keep authoritative values as exact decimal strings/integer timestamps. Convert only bounded plotting coordinates; original values drive readouts and financial calculations.

## Implementation scope and ownership

The authorized implementation includes historical acquisition, the standard
renderer, shared visual controls, named editable layouts, and optional footprint
resting-liquidity/individual-print layers. Selective upstream v2.3 interaction
ideas are adapted into existing native ownership; no second market-state store
or market-data provider is introduced.

| Slice | Implementation boundary |
| --- | --- |
| Backend candles | Exact native providers, exclusive older-page boundaries, stable series identity, explicit history capabilities and labeled polling |
| Frontend history | Sorted exact merges, source/generation fences, older-page coalescing, reconnect overlap and bounded retained history |
| Standard renderer | Lightweight Charts 5.3.0 candles/volume, compatible opt-in positioning, exact accessible readouts and navigation |
| Workspace | Versioned preferences, one global interval, source-preserving migration and named editable grid layouts |
| Specialized charts | Shared six-theme presentation; optional native historical resting depth and retained individual trade badges |
| Verification | Parent-owned final gates; finite Binance browser observations and screenshots below do not certify the complete redesign |

The implementation is not accepted solely because the renderer can show a finite
page. Backfill, live reconciliation, cap/reset and source/replay fences are
end-to-end acceptance gates below.

## 1. Contracts, identity and source selection

Primary files: `frontend/src/candle-model.ts`, `frontend/src/candle-api.ts`, `frontend/src/integration/NativeCharts.tsx`, `frontend/src/workspace.ts`, `src/workstation/api.py`, `src/workstation/candles.py`, `src/workstation/runtime.py`.

- Venue `series_key` covers exact provider/native instrument metadata, family, interval, native bucket alignment and time basis; session/generation fences additionally govern publication.
- Ticker history normalizes the request to the exact selected primary before identity/gating. Observed flow keeps the actual cohort. Aggregate quotes remain indicative without conversion.
- Ticker acquisition is independent of observed-cohort readiness and collector health: a connected session with a selected native instrument may request it even while observed analytics is unavailable. Session/generation/source identity fences and loading/seeking gates still apply; this is not permission to reuse foreign evidence.
- `priceChart` preferences persist `source: auto | session_trades | venue_ohlcv`, `scale: linear | log`, `autoScale`, `rightOffset` and `positioning`. Defaults are auto, linear, autoscale on, five bars of right offset and positioning off. Explicit source choices win.
- One global TopBar interval selector controls price and interval-based studies. Existing saved intervals survive migration; unsupported provider intervals remain explicit rather than silently changing.
- `before_ns` is an optional positive signed-64-bit nanosecond timestamp, exclusive on candle start and valid only for venue history. Returned `history` distinguishes `more | exhausted | limited | unavailable | unsupported`, `time | recent_only` pagination, requested/next boundary, maximum page size and polling metadata.
- Observed fixed buckets floor against the Unix UTC epoch (Thursday weeks); `1M` is a real UTC calendar month. Ticker history preserves each native provider's alignment, never an approximate 30-day month or a relabeled observed clock.
- Source/instrument/interval changes invalidate old responses. Same-series failures retain stale bars; a replacement ticker must never inherit another identity's data.

Acceptance: the UI can distinguish ticker/observed/aggregate, supported/unsupported, loading/exhausted/error, forming/closed/partial, and stale/current without guessing from an empty array.

## 2. Historical acquisition and live continuity

Primary files: `src/workstation/candles.py`, native provider adapters, `src/workstation/runtime.py`, `frontend/src/candle-api.ts`.

### Provider capability inventory

Native candle adapters cover exact supported products for Binance Global spot/USD-M, Binance.US spot, Coinbase spot, Kraken spot, Bybit spot/linear, KuCoin spot, Hyperliquid, OKX spot/linear swaps, Bitget spot/USDT perpetuals and Deribit BTC/ETH inverse perpetuals. Catalog/book support does not imply candle support for every family or interval. Kraken is explicitly **recent-only (720 native bars)**; Hyperliquid is limited to its **recent 5,000-candle window**. Other providers retain endpoint/page/interval restrictions, not a promise of unlimited history. Returned capabilities and reasons are authoritative; see [the API](API.md) and [visualization contract](VISUALIZATION.md).

### Backfill

- Selection requests a recent target of **300 bars**, subject to provider limits; initial visible density follows available width rather than fitting every loaded bar.
- Near-left-edge navigation requests older pages, preserving logical viewport position when prepending. Repeated page requests coalesce.
- Sorted exact merges deduplicate native starts and reconcile overlaps/corrections. Empty or short pages alone do not certify exhaustion; errors, inaccessible history and unknown boundaries remain distinct.
- Each mounted query retains at most **10,000 bars**. At the cap, growth pauses with an explicit history-limit state. Loaded history is not silently evicted; an explicit reset returns to recent data to resume growth.
- Selection/lifecycle changes cancel or ignore obsolete requests. Older fetches cannot replace a newer forming-bar revision.

### Live updates

- Updates deliberately use **5-second polling**, exposed as `update_mode: poll` and `poll_interval_ms: 5000`; no candle-streaming claim is made.
- The forming bucket updates in place, rollover appends one native bucket and overlapping corrections replace the correct bucket. Reconnect recovery uses overlapping history without inventing candles across an unknown span.
- The bounded observed session-trade ring is not ticker backfill. Demo/replay auto source never starts venue HTTP; explicit ticker selection warns that current external history is not replay evidence.
- Following is separate from acquisition. Panning back detaches follow; refresh must not snap the viewport forward.

Acceptance: initial history, leftward loading, bar rollover, reconnect and stale recovery work for each enabled provider/family without duplicates, disappearing visible history or identity leakage. Provider limitations are visible rather than hidden behind a generic empty chart.

## 3. Standard price renderer migration

Primary files: `frontend/package.json`, lockfile, `frontend/src/PriceChart.tsx`, `frontend/src/candle-model.ts`, `frontend/src/price-chart.css`.

- Lightweight Charts is integrated behind a typed series adapter; React owns lifecycle, toolbar, provenance, errors and accessible inspection, without a second chart-state framework.
- Candlesticks and a compact volume histogram share compatible time coordinates. Optional OI/funding use independent scales only with verified receive-clock/instrument semantics.
- Exact decimal strings/integer timestamps remain authoritative. Bounded plotting conversion must preserve distinguishable values or expose an unsupported display state; original values drive inspection.
- The plotting adapter derives a decimal quantum using exact integer arithmetic and supplies Lightweight Charts both `minMove` and an explicit decimal-power `base`, rather than reciprocating a tiny floating-point step. Linear coordinates use a bounded origin/scale; log requires positive representable values. Colliding coordinates fail explicitly and exact original strings remain inspectable. Changing precision during synthetic backfill still has an open viewport regression; normal-precision live anchoring is not proof of that edge case.
- Keyboard inspection and a non-canvas data path remain available.
- Navigation includes drag/pinch/wheel, price-scale interaction, **Auto**, **Log**, **Fit**, **Latest/follow** and configurable right margin in bars. Nonpositive or unrepresentable log values must remain explicit.
- Sparse observed history stays sparse and visibly partial, with no filler candles.
- Series updates, resize/disposal and subscription cleanup are lifecycle-owned. Hidden native liquidity/flow/book/tape panes remain mounted to preserve frozen evidence while query/paint work is suspended; source/generation/clock fences still invalidate obsolete snapshots.
- User-drawn studies and full TradingView indicator menus are outside this implementation.

Acceptance: price/volume geometry remains aligned after backfill, resizing, interval changes and live updates; cursor readouts retain exact original values; navigation feels conventional without changing financial semantics.

## 4. Workspace and visual hierarchy

Primary files: `frontend/src/integration/NativeCharts.tsx`, `frontend/src/orderflow/components/TopBar.tsx`, `frontend/src/price-chart.css`, existing workspace/theme styles.

- A compact native symbol/venue/product header provides normalized symbol search grouped by native market/quote identity. One global timeframe toolbar replaces duplicate Price action interval controls.
- The desktop KPI strip stays on one compact horizontal line with internal scrolling; source/clock/coverage provenance remains compact and visible, with full details in disclosures. Narrow layouts may wrap/stack without turning collector errors into ticker errors.
- Price receives the dominant space; volume is compact and observed OI/funding is **opt-in**, with no empty panes for missing/incompatible series.
- Standard and specialized renderers resolve the existing six-theme tokens for colors, typography, grids, separators and statuses.
- Tick-aware price display and unit-bearing volume retain exact inspection; funding sign is not a candle-direction color.
- Persistent provenance distinguishes source/market identity, stale/current, loading older and bounded-history states.
- Native book/tape remains one surface, not a duplicate chart sidecar.
- Desktop uses 12 columns and a responsive **12-logical-row** page sized from remaining workspace height, with a **24px minimum row height** and 6px gaps. Resizing banners/toolbars changes row pixels, not saved cells; minimum-height/custom extents may scroll. At widths ≤965px, panels stack in reading order without horizontal page overflow.

### Named editable layouts

Layouts extend versioned `workspace.ts` preferences. Edit mode enables panel
add/remove, drag/resize and keyboard geometry controls with minimum sizes and
collision/bounds handling. Built-ins include Order flow, Price focus, Liquidity,
Volatility and Execution. Named layouts support save/load/rename/delete, up to
**12 names of 1–40 characters**; duplicate names are rejected.
Price focus uses the full 12-row chart page. Saved layouts dismiss on outside
pointer interaction, focus leaving the menu, or Escape; Escape restores summary
focus, and successful load closes the menu. Pending names/selections survive
dismissal. These controls do not change market ownership.


Migration preserves existing market-source/timeframe preferences and imports
the four legacy workspace slots. Layout application never restores a source,
instrument, cohort, session or replay cursor; the current price-source choice
and temporal/freeze state are retained. Browser-local layout storage does not
store market evidence or financial ledgers.

Acceptance: all six themes, collapsed/expanded positioning, desktop and narrow layouts remain readable; no duplicate timeframe controls or unlabeled source changes.

## 5. Positioning and specialized charts

Primary files: `frontend/src/positioning-api.ts`, `frontend/src/positioning-model.ts`, `frontend/src/MarketCharts.tsx`, specialized components under `frontend/src/orderflow/components/`.

- Preserve current observed Hyperliquid positioning, its source instrument and units. It remains compatible with observed receive-time candles, not automatically with exchange-time ticker history.
- For Ticker history, enable positioning only when its native timestamp/interval semantics and instrument identity are verified compatible. Any acquisition change needs explicit source metadata and clock handling; never simply relabel receive timestamps as exchange timestamps.
- Historical OI/funding backfill is capability-dependent. Show the actual shorter coverage; do not carry forward missing OI or invent funding observations. A price chart may legitimately have months of bars and only recent positioning.
- Never substitute related-perpetual context for selected-instrument context without an explicit label. Funding sign describes payment direction, not price direction; retain distinct colors and actual units/intervals.
- Bring footprint, liquidity and study charts onto shared typography, palette, legends, pane separators and density controls. Preserve their specialized aggregation, missing-data and exact-inspection semantics.
- Link cursor/time ranges only for compatible identities and clocks. Linking ticker exchange time to observed receive time requires a defined mapping with uncertainty; until then, keep those cursors explicitly separate.

### Optional footprint layers

- **Indicators → Resting liquidity** draws eligible retained native historical primary depth behind footprint cells. It is not the existing **Executed heatmap**, not aggregate cohort depth and not a current book projected backward.
- Opacity and **Stable quantity ceiling** are explicit persisted user controls; the legend carries units, primary scope and sampling. Incoming data does not renormalize the ceiling. Unknown/discontinuous/unmatched-clock spans stay blank.
- **Indicators → Large prints** uses a minimum **base-size** filter on individual retained native trade references. Venue text badges support exact venue/price/time/quantity inspection; aggregate footprint cells are not substituted for individual prints.
- Both layers are optional, bounded and source/clock fenced. Hidden panes do not query resting history. They do not establish complete exchange trade/depth history.


Acceptance: OI/funding never imply unsupported history or clock alignment, and visual consistency does not erase differences between resting liquidity, traded volume and ticker prices.

## 6. Verification, clean cutover and documentation

Use the narrowest checks for each completed change. Regression tests target consumer-visible risks, not canvas implementation details or source-text assertions.

### Deterministic data cases

- Overlapping/duplicate/out-of-order pages; exhausted versus temporarily failed pages.
- Partial latest bucket, exact boundary rollover, provider revision and reconnect overlap.
- Calendar-month/week alignment and unsupported intervals.
- Source/instrument/cohort changes during in-flight fetches; same-owner stale retention.
- History-cap pause/reset without silent visible-range eviction; backfill without moving the visible historical candle.
- Replay seek/reset, future-history exclusion and incompatible positioning clocks.
- Exact decimal inspection and display conversion edge cases.
- Named-layout save/load/rename/delete, legacy migration, collision/minimum-size rules, keyboard editing and preservation of source/cohort/session.
- Historical resting-depth projection, stable color ceiling, missing spans, hidden-pane lifecycle and exact retained-print inspection.

### Actual browser acceptance

- Supported live ticker opens with historical candles; scrolling left loads older data without jumping.
- Forming bar changes in place; rollover adds one bar; panned history stays stationary.
- Auto/Log, Fit, Latest, keyboard inspection, touch navigation and resizing work on the real surface.
- Unsupported/empty/rate-limited/disconnected/stale states keep their distinctions and recover correctly.
- Validate 1440x900, 1280x800 and 390px-wide layouts, all six themes, optional panes and large-price precision cases.
- Capture actual implemented screenshots separately from the design preview. Inspect sparse observed flow and real historical price data, not only a dense happy path.
- Measure bounded memory, update work and interaction responsiveness using representative history/live/replay loads. Make performance claims only from recorded measurements.

### Removal and documentation

- Remove obsolete custom price drawing, price-only viewport math and superseded request paths once the replacement is verified. Retain shared exact/source helpers still used by other consumers; no permanent dual-renderer switch or compatibility shim.
- Update existing `docs/ARCHITECTURE.md`, `docs/API.md`, `docs/VISUALIZATION.md`, `docs/TERMINAL.md`, `docs/READINESS.md` and README chart descriptions; retain historical evidence as historical.
- Document actual provider capabilities, polling cadence, history cap/reset, clock provenance, replay behavior, layout migration and overlay units. License/NOTICE/attribution belong with the dependency distribution.

### Recorded implementation observations

These finite observations come from the actual Binance spot ticker surface,
not the synthetic design preview:

- History loaded **300 → 600 → 900 bars** through two 300-bar older pages.
  Inspection stayed on **2026-11-04 04:00:00 UTC**, with unchanged exact OHLC
  `76496.05 / 77595.64 / 76434.11 / 77390.01` and volume `1000.78114 BTC`.
  The separate synthetic changing-axis-precision browser regression also passes:
  detached candle pixels remain stable through deferred axis-width changes.
- Polling revised the same forming bucket (`1794628800000000000` ns): close
  `67072.01 → 67082.76`, volume `1556.56946 → 1557.82485`; historical inspection
  remained retained. The actual hour rollover appended exactly one bucket at
  `1794632400000000000` ns, giving **901 retained bars**.
- All **18 theme/size states** (Midnight, Daylight, Forest, Neon, Graphite,
  Paper at 1440, 1280 and 390px widths) showed matching sampled canvas/background
  colors, no horizontal overflow and two chart panes with no empty positioning
  panes. Continuous touch pan detached following; pinch was dispatched and the
  chart remained visible without overflow. This is a finite interaction
  observation, not exhaustive touch-device qualification.
- A bounded Chrome 150, DPR 1.25, 1440×900 sample with **300 loaded bars**
  recorded 239 frame opportunities: p50/p95 approximately **16.7ms**, maximum
  **16.8ms**, zero observed long tasks; heap **66,390,529 → 39,341,689 bytes**,
  11 canvases and **4,091,104 backing bytes**. Frame opportunities are not GPU
  completion, exchange latency or sustained throughput; a falling heap sample
  is not memory-leak proof and does not characterize the 10,000-bar cap/replay.
- Native synthetic-demo verification displayed **256 historical depth tiles** and
  **129 retained individual-print candidates**, with explicit partial coverage.
  Optional Hyperliquid OI/funding panes rendered actual demo observations; pointer
  resizing changed their pane heights without changing their units or source.
  Pointer grid resizing persisted the chart's logical height from 12 to 10 rows.

Actual implementation screenshots:
[1440 dark](evidence/chart-redesign/live-price-1440-dark.png),
[1440 light](evidence/chart-redesign/live-price-1440-light.png),
[1280 dark](evidence/chart-redesign/live-price-1280-dark.png),
[390 dark](evidence/chart-redesign/live-price-390-dark.png).
[Observed-flow demo](evidence/chart-redesign/observed-demo-footprint-1440.png) and
[positioning demo](evidence/chart-redesign/observed-demo-positioning-1440.png)
are actual application captures of explicitly synthetic native demo data, not
public market observations.
**Caption:** live ticker history can remain current while the collector's
`analytical_queue_overflow` incident is visible. Independent REST polling and
backfill do not establish healthy observed books/trades or repair that incident.
Other adapters have deterministic parser/request coverage, not equivalent live
endpoint qualification.

Final verification receipts:

| Gate | Result |
| --- | --- |
| Complete Python suite, excluding the network marker | **2,009 passed** |
| Backend Ruff lint / format check | Passed; **94 files** formatted |
| Backend mypy | Passed; **44 source files** |
| Complete frontend unit suite | **249 passed** |
| Frontend ESLint, TypeScript, Vite build and tooling syntax checks | Passed |
| Chart/workspace browser acceptance | **46 passed** |

The browser run covers `price-pagination`, `price-action`, `chart-redesign`,
`unified-shell` and `workspace`: precision-changing prepends, forming revisions,
rollover, exact inspection, source/replay fences, stale recovery, named layouts,
six themes, compact liquidity pixels, hidden/frozen panes and mobile navigation.
These are final run totals, not sums of earlier repair runs.

[Raw browser observations](evidence/chart-redesign/browser-observations.json)
retain the finite source, geometry and resource measurements. Existing
Starlette/httpx deprecation and Vite dependency/chunk-size warnings remain;
they did not fail the gates. No live-provider capacity or trading-readiness
certification is implied.


## Completion criteria

A user can open a supported live ticker and read historical price action immediately, scroll back for more data, watch the forming bar update, and inspect exact values without understanding internal session retention. Observed cohort flow and replay remain available and honest. Standard and specialized charts look like one application, while unsupported data remains explicitly unsupported.
