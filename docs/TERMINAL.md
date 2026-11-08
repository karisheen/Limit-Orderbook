# Order-flow terminal and native research workbench

## Default live screener

The default `/` page adapts the Apache-2.0
[order-flow-screener](https://github.com/zadescoxp/order-flow-screener) v2.1.0 UI,
engine and provider adapters. **Perp / Spot / FX**, searchable **Instrument**,
**Venue** (including aggregates), and **1m–4h** select the session. The footprint
starts with newly received trades; it does not backfill history. Switching
instrument, venue, market or timeframe resets collected session data.

**Order Flow**, **Liquidity**, and **Volatility** configure the studies/sidebar.
**Panels** fine-tunes visibility. Drag visible card borders to resize the sidebar,
studies dock and sidebar panels. Narrow screens use a scrolling single column.
Six themes, panels and indicator preferences persist in browser-local storage
under `limit-orderbook.orderflow.ui-preferences`.

Use **Bid × Ask / Delta / Volume**, **Profile**, **Heatmap**, and **Indicators** to
configure the footprint. Indicators include session VWAP/bands, POC, value area,
high/low, imbalance ratios and stacked levels. Drag to pan, wheel to scroll,
Ctrl/⌘+wheel to change row height, Alt+wheel to change candle width; **Live** or
double-click restores following. Heatmap means executed volume, not resting depth.
Book grouping, tape size filters and study resolution/window controls are local
to their panels. Studies stay warming until enough real observations exist.

All reference crypto providers are retained: Binance, Bybit, OKX, Bitget,
Coinbase, Hyperliquid and Deribit. Public browser sockets operate independently
of the native `--network` flag. Binance uses its official data-only spot host and
separate futures public/market routes. Region, symbol listings and provider
restrictions can affect availability; the status strip identifies errors.

FX requires `VITE_FINNHUB_API_KEY` in `frontend/.env.local` and a rebuild. This is
a browser-exposed Finnhub key, not a server secret. OANDA EUR/USD, GBP/USD,
USD/JPY, AUD/USD and USD/CAD ticks have inferred direction and provider/unit-tick
volume. FX quote/depth/OBI are **synthetic proxies, not native L2**. Crypto
aggregates combine USD/USDT/USDC prices without conversion and are indicative.

**Workbench** navigates to `/?workspace=native`; **Order-flow terminal** returns
here. Navigation starts a new document, isolating feeds, state and styles. Native
`?source=...` links still open Workbench directly. Screener session data is not
silently routed into native replay, paper accounting or research.

Verification for this adaptation: a finite 35-second BTCUSDT probe received both
trades and books from all 11 configured crypto venue/market combinations. The
browser also displayed six live perpetual venues in aggregate mode. This is a
smoke observation, not all-symbol coverage, continuous-depth certification or a
long-running soak. Live Finnhub was not verified without a key; the missing-key
state and offline FX proxy/market-hours behavior were exercised.

The Binance routing change follows the
[official stream migration contract](https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Connect).
Pristine upstream fixtures reproduced Bybit update rejection, ignored Binance
futures depth, Hyperliquid object-level parsing failure and Coinbase side
inversion before corrections. Adapter regressions retain these cases.

Local verification: lint, strict TypeScript/Vite build and 123 frontend tests
passed. All 64 offline native browser scenarios passed across the broad run and
an isolated completion run; one native-network scenario was skipped. The broad
run hit its 15-minute command deadline and three desktop keyboard scenarios
hit their per-test deadlines during concurrent live capture. The eight selected
desktop/unfinished scenarios then passed unchanged with capture browsers closed.
Desktop/mobile visual checks, persisted themes, indicators, presets, resizing,
replay seek, research access and document-level stylesheet isolation were also
exercised. The README video records the actual browser-direct terminal.

## Native workbench

The retained native React/TypeScript workspace is a presentation client of the
versioned Runtime HTTP/WebSocket API. **Price action** is its default desktop
view: native historical **1h candles** and volume beside L2 book and tape.
**Order flow** retains the liquidity and footprint charts. All native panes share
one exact instrument; Python supplies financial values and replay boundaries.
The remaining sections describe this native workspace, not the browser screener.

The desktop redesign follows the chart-first composition and minute/hour/day
hierarchy visible in the supplied TradeXYZ and Hyperdash screenshots, with
restrained separators and explicit market identity rather than decorative cards.
No wallet, ticket, leverage, private execution or new venue was added. See
[research, screenshots and verification limits](WORKSPACE_IMPROVEMENT.md#desktop-price-action-redesign).
The [M8 handoff](OMP_UPGRADE_HANDOFF.md#m8--market-first-workstation) remains
historical evidence, not the current layout.

## Launch

From the repository root with the locked Python environment already installed:

```sh
export PATH="$PWD/.tools/node/bin:$PATH"
npm --prefix frontend ci
npm --prefix frontend run build
.venv/bin/python -m src.workstation.cli serve --network
```

Open `http://127.0.0.1:8000/?source=live`. The backend serves `frontend/dist`; no second production Node server is needed. The explicit `--network` flag enables public live discovery/collection and makes live the server-preferred source. Omit it for the sparse, initially paused, nonrecording synthetic demo; **Play synthetic demo** advances fixture deliveries, not real exchange orders. A durable recording may be made available with `serve --capture /operator/path/to/capture`; HTTP clients select the managed source ID, never a filesystem path. Live collection must not compete with another collector owner. See [API](API.md), [DEMO](DEMO.md), and [REPLAY](REPLAY.md) for readiness, recorded-capture verification and operator prerequisites.

`serve --state-dir data/workstation-state` selects durable paper, journal and
research SQLite storage (also the CLI default). Use the same directory for CLI
research. Reload creates a new independent session, not automatic adoption of an
old paper run. Keep the state directory across server restarts.

Development uses the same backend on port 8000 plus:

```sh
npm --prefix frontend run dev
```

Open `http://127.0.0.1:5173/?workspace=native`. Vite proxies `/api` (including WebSocket upgrades) and `/health` to loopback port 8000. Port 5173 is strict; no silent move to an unapproved origin. `vite preview` can run the browser-direct screener, but does not provide the native API; use the Python server for Workbench.

## Working controls

- The existing topbar **Search instruments** field searches exact instrument ID, native symbol, base/quote ticker, venue and market. Space-separated terms are case-insensitive and all must match the instrument's combined fields (for example, `BTC binance spot`). Focusing or editing search opens the watchlist results; search was not newly added or previously hidden behind Watchlist. Venue, exact quote-asset and market filters remain independent; instrument buttons retain native IDs. Quote identities are not inferred from ticker alone. The selected instrument exposes metadata, tick/lot, settlement, execution access and native feed mode.
- Public catalogs may contain thousands of instruments. The watchlist renders at most 100 per page with **Prev/Next**; search and filters still cover the entire source catalog and reset pagination. Paging never changes the selected instrument, subscriptions or native order-book depth. Catalog-derived filter/search work is reused between market frames rather than rebuilding thousands of rows on every update.
- Open **Source / workspace** for **Data source**, **Workspace preset** and the nested **Workspaces** saved-slot controls. The disclosure is keyboard reachable; Escape closes the focused disclosure and returns focus to its summary. Source readiness, transport connection and pane freshness remain separate evidence, not one combined health claim.
- The source selector identifies demo/replay/live, synthetic provenance, recording state, readiness and reasons. URL `?source=<catalog ID>` wins over an explicit saved choice, which wins over the server preference. Loading/unavailable requests expose their reasons and never silently substitute demo. Catalog refresh is single-flight every three seconds after completion, with a five-second deadline. Active instruments use `/instruments?source_id=<exact source ID>` so another source cannot supply their metadata. Source changes clear foreign instrument/viewport preferences. Live startup resolves an exact active source-native instrument, preferring accessible Binance-global BTCUSDT spot, then Hyperliquid BTC perpetual; replay uses its reflected default. Streaming preserves current selections, text inputs, open disclosures and pane scroll positions.
- Core metrics show exact string values, units, eligibility and expandable window/quality/formula/config/source references. The depth-band selector displays the engine's 5/10/25/50 bps outputs; it does not recompute depth in JavaScript. Incomplete/warming windows stay unavailable. Profile and observed display evidence are additional table views, not heatmaps or predictive signals.
- The depth ladder requests `/ladder`. Teal bids and restrained red asks carry subdued per-level bars, right-aligned decimal text and bold best-price buckets. **Size** switches between exact native base quantity and quote notional; the header names the asset units. **Cumulative** optionally shows best-outward totals. These are **aggregate L2 resting quantities**, not individual orders or executed trades. **Rows** changes presentation only, never reconstruction depth.
  **Group** selects the DOM price quantum independently of chart aggregation; unknown ticks are labeled source quanta. The center row shows the exact native spread in quote units and ticks. **Settings** exposes midpoint, basis points, native best prices, rows, size units, cumulative depth, follow and rescale controls. Coarse grouping never changes the quoted native spread.
  Bars on both sides use one held cap from the displayed depth, not independently normalized sides. Incoming maxima never rescale it; larger values saturate. **Rescale depth** explicitly captures the current displayed maximum. Selection, grouping, row scope or quantity-unit changes establish a new cap. Brief outlines indicate observed increases/decreases at the same side and price; disappearance outside the displayed scope is not treated as removal. Reduced-motion preferences disable animation.
  **Follow midpoint** centers initially, on best-quote movement and layout changes; ordinary quantity refreshes do not reposition rows. Wheel, touch, keyboard or scrollbar/manual scroll suspends following, including across resize and regrouping, until **Recenter DOM** or re-enabling follow. Source/selection replacement starts a new ladder. A shallow or unavailable book can recover its initial center without treating automatic scroll clamping as user input.
  **Freeze DOM** holds its snapshot independently of tape/collection. Compact partial/frozen/error health stays visible; errors/ineligible evidence have no active depth bars or spread summary. **Settings → Scope & provenance** exposes native reasons, subscribed/retained depth, interpretation limits and exact grouped evidence. Missing evidence never becomes zero.
- Proposed order controls send either base quantity or native quote budget, side, fee assumption/provenance/date, adverse latency bps and native-quote rebalance cost to `/costs`. Inputs are text, not JavaScript numbers. Unknown fees remain unknown. A result is an explicitly labeled snapshot, not a continuously current executable quote; press **Estimate & compare** again to refresh. Changed inputs are flagged until recalculation. Selection/seek/reset/session replacement clears old results.
- The pair table exposes same-feasible-base quantity, gross reference, depth cost, both fees, FX, rebalance, adverse latency, net/bps, exact inputs and exclusions. Default UI comparisons use native quote/market cohorts; USD, USDT and USDC are distinct. Unknown inventory, prefunding and non-atomic execution assumptions remain visible. The UI does not invent FX rates or inventory. Core/API support for explicitly supplied rates/inventory is documented separately.
- Play, pause, step, speed, seek by exact delivery index and reset operate the session's actual replay engine. Seeking disables stale action surfaces and clears old analytics. A new document has a new session; there is no shared localStorage session ID. Two tabs do not share a mutable cursor or selection.
- **Price action** uses `/candles`, independently of `/visualization` and the live book. New preferences select **1h**. Quick controls offer **1m / 5m / 15m / 1h / 4h / 1d**; **More** groups minutes, hours and longer native intervals. A non-quick selection remains visible on the menu button. Unsupported choices are disabled, not approximated; **1M** means a calendar month, not thirty days. Selection changes never change replay speed or book subscriptions.
  Live sources request up to 300 recent venue OHLCV bars, subject to native history and availability. One bounded page is retained; panning does not download unlimited older history. Demo/replay aggregates only retained, already-delivered trades on admitted receive UTC, with explicit **partial history** and actual observation coverage. A three-minute demo cannot provide days of hourly history. Gaps stay gaps; zero-volume filler candles are never invented. See [native candle contracts and provider limits](VISUALIZATION.md#historical-price-candles).
  Drag to pan, scroll or **+/−** to zoom, **Fit** for returned history, and **Latest** for the recent window. A forming bar updates in place; following moves only when the newest bucket boundary advances. Hover or focus the plot and use **←/→** to inspect exact OHLC and base volume. **Shift+←/→** pans; **F/Home** fits, **L/End** follows latest, **Esc** clears inspection. The grouped menu supports arrows, Home/End and Escape with visible focus.
  Polling runs five seconds after each completed request, only while the chart/document is visible and the session usable. Book/tape cadence is independent. Loading, empty, unsupported and failed queries are explicit. Refresh failure retains same-owner candles as **stale**; disconnected evidence is stale until session replacement clears it. Retry is explicit and periodic recovery continues. **Fetched/Queried** is server receipt time; **Checked** is browser receipt time, not exchange freshness or execution latency. Bar **Closed** means its time boundary ended, not independently verified complete history. Chart help exposes provenance and coverage limits.
- In **Order flow**, the simultaneous charts consume `/visualization`: resting liquidity, short-horizon candles, executed-trade footprints/profile and shared-core CVD/OFI. Primary **Liquidity** and secondary **Order flow** retain independent settings. Compact headers expose mode, interval, price grouping, cursor link, freeze and maximize. **More** opens settings, Inspector and navigation actions over the plot without consuming rows. **Inspector** contains legend/navigation, exact data, quality reasons and engineering diagnostics. Escape/Close returns focus to More. Date/timezone, quality, explicit row crop and primary-data freshness stay visible. Cells that cannot fit exact numbers use explicitly labeled compact marks without suppressing readable neighboring cells; hover or Inspector retains exact values.
  **Auto group** shows the resolved native quantum and initially fits genuine nearby depth and observed trades; it refits when selected again or on **Recenter price**, not on each incoming frame. Explicit aggregation and price panning remain authoritative. The primary default time window expands with collected evidence up to 180 seconds; the secondary follows six seconds. Explicit ranges/panning retain missing-history hatching, and truncation remains disclosed.
  **Follow latest** follows the qualified source-clock frontier while preserving the painted window's exact duration. Explicit range presets replace that duration; **Auto retained** restores the primary retained-history policy. **Fit time** pins the available native extent without changing price or freeze: retained sample bounds for liquidity/OFI, returned native aggregates for flow. The backend does not expose a separate all-retained trade extent; receive-clock label coverage is never substituted for market history. **Recenter price** resets only that pane's price offset, native fit and held footprint crop; it neither restores time following nor resumes a frozen chart.
- Order-flow chart freezing stops automatic view refresh, not replay or recording. DOM and tape have independent freezes. Explicit chart aggregation, pan/zoom or linked-time navigation can request a replacement while its freeze flag remains set. The separate replay **Pause** changes the actual replay clock. Selection, seek/reset and source replacement invalidate obsolete frozen evidence; native clock-scope changes also invalidate held views and cursors without changing independent freeze flags. Unavailable history is labeled rather than fabricated.
- Independent price viewports are supported by native `VisualizationRequest.price_min`, not by rebuilding economic grids in JavaScript. Each chart resends its own fitted exact origin/step so a sibling's recenter cannot move an established viewport. The optional origin is a finite nonnegative decimal string aligned to an explicit supported step; it cannot be combined with native `recenter: true`. Native Decimal arithmetic applies row offsets while the economic bins remain zero-origin. Selection, rewind/reset and source-clock fences still invalidate obsolete evidence. See [native viewport geometry](VISUALIZATION.md#historical-liquidity); this is query geometry, never browser price/volume aggregation or financial recomputation.
- **Costs & proposed risk** contains the existing exact cost comparison plus a collapsible hypothetical entry/stop scenario. Submit exact strings for entry, stop, side and either base quantity or native-quote risk budget. Explicit assumed fees require basis points, provenance and date. `/proposal-risk` supplies loss, fees and supported lot-constrained size; the browser never derives size or risk. Unknown fees and insufficient metadata remain unavailable. Estimated loss is not guaranteed: gaps, slippage and unknown stop liquidity can cause greater losses.
- **Cursor** on the two order-flow charts, DOM and tape explicitly opts into the shared source-time/price cursor; it does not synchronize ranges, zoom, aggregation, buckets, follow or freeze. Hover or keyboard-focus a DOM row or tape price to publish its exact native price and source receive-monotonic timestamp. Charts publish native row prices and source time; CVD/OFI may publish time with no price. Unlinked panes keep local interaction. A chart draws the exact linked price and identifies its containing bucket separately; an off-screen price never selects a nearest unrelated row. DOM/tape row highlighting requires exact price-string equality. Leaving/blurring clears the cursor, and session/generation/source/instrument changes fence obsolete links. Price action uses its own UTC inspection cursor; exchange-history time is not substituted for receive-monotonic coordinates.
- **More → Link chart time ranges** independently opts both charts into time navigation. Enabling it aligns the other chart to the initiating pane's painted window; subsequent pan, zoom, range, follow and fit actions synchronize time only. Disabling it leaves both current views intact. Price origins/quanta, time buckets, modes, overlays, timezones and freezes remain independent; DOM/tape are not range-linked. The header shows **Time linked** when enabled. Missing views cannot supply invented coordinates.
- **Time & sales** displays receive UTC, exact price, unit-bearing size and side; ordinary rows omit repetitive origin prose. History, synthetic and unknown-side exceptions remain visible. **More** holds **Rows** (25/50/100) and **Side** (all/buy/sell/unknown); filtering affects presentation, not collection or aggregation. Activate a time cell to inspect a pinned exact print, receive/exchange timestamps, source and side provenance. Streaming cannot replace the inspected print; Escape/Close restores its trigger or the stable pane header if it left retention. Independent **Freeze tape / Resume tape** does not pause charts, DOM or replay; source/generation changes clear obsolete snapshots. Empty filtered history is not invented.
- Presets (**Balanced**, **Analysis**, **DOM focus**) change layout, not chart settings. **Watchlist** opens exact-instrument results/filter/pagination; **DOM / tape** toggles the right market column; **Costs / analytics** opens research. The persistent bottom workflow dock opens the existing costs, analytics, alerts, context, paper, journal and research tools without adding live execution. Each market pane has **Max/Expand / Restore**. Maximizing preserves sibling settings. Escape closes a focused context/disclosure before restoring the workspace; selecting a preset, toggling a drawer or loading a slot exits maximize. Balanced uses 52/48 chart splits in Order flow and 60/40 DOM/tape splits. Automatic right-column widths are 336/360/420 px at 1280/1440/1920 px.
  Drag separators or focus them and use arrows: Left/Right for widths, Up/Down for vertical splits; **Home/End** select the minimum/maximum. Width/research-height steps are 10 pixels; chart and DOM/tape splits use 2 percentage points. Right/bottom edge handles reverse direction to match their geometry. The separators are **Watchlist width**, **DOM width**, **Chart split**, **DOM and tape split**, and **Research pane height**.
- At **≤965 CSS px**, the **Market panes** tablist shows **Price / Liquidity / Flow / Book / Tape**. Left/Right wraps selection and focus; **Home/End** selects the first/last tab. Only the selected tab is in the tab sequence, with a labeled tabpanel and explicit selected state. Selecting Book/Tape opens the right market column if needed and selecting a tab exits maximize. Above the breakpoint, **Price action / Order flow** chooses the large candle chart or original dual charts beside DOM/tape.
  Switching tabs hides rather than unmounts panes: independent chart settings, viewport/freeze state, tape settings and mounted DOM manual-follow state survive visibility changes. Hidden panels leave focus navigation and hidden chart/ladder queries are gated; tab selection does not stop replay/collection or thaw a frozen pane. Source/selection/generation and clock-scope invalidation still clear obsolete evidence; this is state retention within valid ownership, not restoration of old market data.
- **/** opens the watchlist and focuses search; **D** toggles only DOM freeze. On the active **order-flow** chart, **Space** freezes/resumes presentation, **R** recenters price only, **F** follows latest with the painted width, and **Shift+F** fits native time evidence. Body focus uses the active chart, if any; DOM/tape interactions never dispatch chart shortcuts. **1–6** select modes only when the primary chart owns interaction; the secondary has its own footprint/candles selector. Chart shortcuts ignore Ctrl/Meta/Alt, repeated keydown, forms, controls, menus, dialogs and editable targets. Focused-plot arrows/+/− navigate. Escape closes the focused disclosure/context or Inspector first, then restores maximize, then clears the cursor. Focus returns to the triggering control or a stable pane fallback. Research tabs support Left/Right and Home/End. Price action has the separate plot shortcuts listed above.
- Browser-local preferences retain schema `version: 1` at `limit-orderbook.workspace.v1`. Valid older settings survive; malformed/out-of-range values are sanitized. New fields default to **Price action / 1h**; selected candle interval and desktop mode are saved independently of order-flow settings. Order-flow defaults remain heatmap/1-second primary with up to 180 seconds of retained history, footprint/1-second secondary with a six-second follow window and overlays off, cursor links on, time-range linking off, tape 50/all, and closed watchlist/research drawers. Explicit saved intervals, overlays and ranges are not silently replaced. Preference writes stay debounced during dragging and flush on page hide so immediate reload preserves the latest choice.
  **Source / workspace → Workspaces** exposes four numbered local slots with **Save workspace / Load workspace** at `limit-orderbook.workspace.slots.v1`. Slots capture layout and view controls, not source/instrument ownership or backend sessions. Loading retains the current source/instrument, clears both pinned chart ranges and chart/DOM/tape freezes, exits maximize and clears the shared cursor. They are browser-origin-local preferences, not saved market sessions, recordings, paper runs or cross-device sync.
  Preference reads are bounded to 8,192 characters and slot reads to 32,768. Watchlist widths are 180–360 px, manual DOM widths 250–500 px, research heights 170–460 px, chart splits 30–70% and DOM/tape splits 30–75%. Legacy numeric right widths migrate to manual; viewport clamping does not overwrite the requested width, including ineffective boundary gestures. Automatic sizing retains a 320 px minimum. DOM rows are bounded to 1–100; tape rows to 25/50/100; offsets to ±10,000 rows; contrast to `1e-12…1e12`. Range presets are 6/30/60/180/300/600 seconds and buckets 1/5/10 seconds. Explicit source-time ranges require increasing nonnegative integer endpoints of at most 24 digits and span at most 30 minutes; invalid ranges restore follow. Price-step strings are bounded to 128 characters and remain subject to native validation; source/instrument IDs to 512. Secondary modes are footprint/candles. Saved fields cannot restore session ownership.
  Custom follow durations are exact positive integer nanosecond strings bounded to 30 minutes, never absolute source coordinates. Duration and time-link choices survive serialization and workspace slots; loading a slot still clears pinned coordinates and freezes.
  DOM manual-scroll/follow state belongs to the mounted ladder, not a saved scroll position. Session IDs are never saved; reload creates an independent session. Legacy automatically saved demo selections do not pin a network launch to demo; migration is reported in source details. Explicit demo and legacy non-demo choices remain respected. Unavailable requested sources are not replaced; invalid instruments use a source-native initial selection with notice. Storage failures remain visible and do not prevent use.
  Startup URL/server source replacement clears foreign clock ranges, grouping,
  offsets and freezes; a same-source reload preserves valid saved chart state.
  A saved tape freeze captures the first eligible snapshot in the new session
  rather than remaining permanently empty. It never restores old session data.

## Identity, source health and freshness

The pinned workspace identity names the source and exact native instrument;
quote, settlement, feed scope and quality must not be inferred from an abbreviated
symbol. Long capture labels may be abbreviated visually, with the full identity
available in the title/details. **Source & instrument details** holds expanded
metadata, provenance and benign preference-migration notices. One prioritized
incident row exposes the current actionable failure or recovery state; all
concurrent causes remain inspectable in Diagnostics and source details. Routine
bounded depth remains compact pane quality, not a repeated warning paragraph.
`analytical_queue_overflow` remains an explicit evidence failure requiring
operator investigation of collector capacity; browser reconnect is not a repair.
A connected transport is not proof of fresh or eligible market evidence.

Order-flow chart dates and time ticks use the native piecewise **receive-clock**
projection of source receive-monotonic admission coordinates, with selectable UTC/local
labels and explicit clock-step boundaries. They are not exchange timestamps or
a browser-estimated wall-clock offset. Detailed labels identify their timezone;
missing native coverage says clock time is unavailable instead of fabricating
an epoch or extrapolating history. Exact original receive/exchange references
remain inspectable. See [receive-clock projection](VISUALIZATION.md#receive-clock-display-projection).
**Inspector → Receive-clock mapping & steps** exposes the exact native
`time_axis` object and offset evidence. Axis step markers keep the projected
time readable (`HH:mm:ss*`, or `HH:mm*` for wider ranges); the footer tooltip and
Inspector explain `*`. Missing older mapping is not extrapolated. The disclosure
serializes exact evidence only while open.

The pinned freshness strips distinguish the displayed chart, DOM and tape
evidence from the newest session evidence. A frozen or retained old snapshot
must not acquire a fresh label merely because a newer frame or HTTP response
arrived. Read these clocks separately:

- **Source-clock lag** compares the displayed evidence's native source clock
  with the current comparable source clock. It describes progress within the
  source/session, not elapsed browser time. Recorded replay is judged against
  its replay cursor, never against today's wall clock; paused replay is not
  stale merely because the recording is old.
- **Evidence resident** measures browser-monotonic elapsed time since that
  evidence identity was first displayed here. New frame/view IDs carrying
  unchanged evidence do not rejuvenate it. **Response resident** separately
  measures chart response age after the complete body arrives and before JSON
  decoding. A recent response can carry old evidence; neither residency is
  exchange-to-screen latency or proof of a new market event.
- **Rendering and ACK** durations describe receipt, CPU drawing, paint
  opportunities and server publication-to-acknowledgement bounds. They are not
  source-clock freshness, synchronized one-way network delay or GPU completion.
  Frame timing and each chart's timing are separate; see
  [Lifecycle, precision and bounds](#lifecycle-precision-and-bounds).

A silent source can stop advancing its observed clock frontier, so steady
source-clock lag alone cannot prove continued ingestion. Paused replay may
hold lag steady while browser residency increases. Chart freshness belongs
to the actually painted view, not a newer unpainted response. Its primary mode
selects eligible depth, contributing trade or metric references within native
clock/time/price bounds. Trade-derived modes can report their own evidence age
without eligible depth; fresh overlays cannot mask an old or missing primary
layer.

Unavailable, unknown, frozen, pending paint, stale retained evidence and native
quality exclusions remain explicit. Missing clocks or incompatible ownership
cannot be converted to a reassuring zero or “fresh” state. Diagnostics are
evidence to inspect, not a performance or trading-readiness certification.

## Native workflow tabs

The **Research pane** tablist contains **Costs & proposed risk**, **Core
analytics**, **Alerts**, **Context**, **Paper**, **Journal** and **Research**.
Open the research drawer if hidden. Inputs are exact strings; inspect native
eligibility/reasons before interpreting a number. [API](API.md) specifies fields
and response bounds; [CONTRACTS](CONTRACTS.md) specifies domain policies.

### Alerts and supporting evidence

Select an exact instrument, open **Alerts**, choose kind/direction and enter
threshold/rearm levels in the server-declared unit. Enter cooldown and, only for
event OFI or persistent displayed liquidity, a positive rolling window. Other
kinds are instantaneous. A depth shock means a low absolute depth level, not
percent change. Net-cost rules additionally require an observed sell instrument,
quantity and explicit fee/stress assumptions; unknown fees never become zero.

Create/remove rules through native mutations. Current evaluations and retained
fires expose qualification, latch/cooldown and omission counts. Request current
rule or historical event evidence explicitly: applicable metric, book/trade
references, prints, displayed liquidity and pair inputs remain a labeled snapshot
until requested again. Depth disappearance is not a trade; displayed liquidity
does not prove spoofing or predictive edge. Rewind/reset rebuilds prefix-valid
rules/latches/fires, not future evidence.

### Hyperliquid context and basis

**Context** exposes the reference watchlist, selectable exact instruments and
required Hyperliquid mark/oracle, funding rate/interval and open interest when
native channels are present. Missing fields stay missing with reasons. Mids and
context are reference-only, not replacement executable depth. Funding units are
fractions per interval; open-interest units are adapter-declared.

Related spot/perpetual mark/oracle/mid basis preserves underlying, quote and
settlement identity. USD, USDT and USDC remain distinct. Enter explicit
side-aware FX and conversion-fee evidence when needed; absent/stale/skewed FX
leaves basis unknown. Applied assumptions are source-scoped, not saved market
facts. “Funding cost” here means FX cost to acquire a reference amount, not
perpetual funding P&L. Basis is not executable arbitrage and excludes trading
fees, funding P&L, inventory and account access.

### Spot IOC paper workflow

1. Select a supported spot/base-quantity instrument and expand **Explicit new
   run / fork — starting funds, sourced fees and latency**.
2. Enter venue/asset balances and provenance using native asset IDs shown in
   metadata. Add dated/sourced instrument fee schedules and fee asset IDs.
   Enter feed, decision and order latency nanoseconds; the UI requires explicit
   values, including zero.
3. Confirm intent and **Create new paper run** or **Create explicit fork**.
   Every configuration creates a durable run; forks record a parent but start
   from entered funds, not copied accounting.
4. Submit exact base quantity as **Market IOC** or **Marketable limit IOC**.
   In paused replay, advance source time to reach pending arrival; browser time
   cannot advance replay fills. Inspect orders, balances, positions, fills,
   exact fees, clocks and consumed levels. Pending orders can be cancelled.
5. Use **Open read-only archived audit** separately from active accounting.
   **Resume this run at cursor** is explicit and available only for matching
   demo/replay sources. Success is confirmed by a new-generation native run
   snapshot, not merely by HTTP completion.

Feed delay gates usable books; decision plus order delay fixes arrival. Funds
are contested at arrival. Native depth, constraints, quality and known dated
fees govern fills. Partial IOC remainder cancels; nonmarketable limits do not
rest. Consumed liquidity requires observed replenishment—not an unchanged
snapshot, revision or instrument switch. No passive fills, derivative economics,
borrowing or market impact is represented.

Initial inventory basis stays unknown and affected P&L unavailable. Marks are
gross best bid, not liquidation proceeds. Rewind before creation leaves intent
only; later commands require an explicit fork before new mutations. Live
archives remain inspectable but cannot resume under a new owner. The run list
is bounded to 100 without pagination/omitted counts; known older IDs remain
addressable through the API.

### Journal and read-only research

**Journal** creates/edits manual rationale, optional entry/stop and context.
New links require an active run and visible selected-instrument order. Edits
retain original source/session/delivery/clock and links. Query current source,
all authored sources or an explicit run filter. Notes survive rewind/reopen;
later notes are later authorship, not evidence known at an earlier cursor.
Archives do not populate active link choices.

**Research** lists persisted attempts and displays exact configuration, source/
hash provenance, exclusions, support counts, selection, limitations and paged
trials. It cannot launch experiments or tune parameters in the browser. Use
`research run`, `research list` and `research show` with the same `--state-dir`;
see [DEMO](DEMO.md) for the full CLI/config tutorial.

All candidate outcomes are retained, not just winners. Chronological train/
validation/test partitions purge boundary-overlapping outcomes and embargo later
partitions; selection uses train/validation only. Unknown fees exclude net
economics. Independent unfunded directional markouts are not realized portfolio
returns: turnover/drawdown remain unavailable without a sequential funded model.
Synthetic results cannot establish edge; failed or insufficient-evidence attempts
remain inspectable.


## Lifecycle, precision and bounds

The frontend consumes API DTO v1, with string economics, IDs and domain timestamps. `src/api.ts` and `src/workflow-api.ts` name the consumer types. Exact values are shown without financial arithmetic; long values are visually abbreviated only and their full string remains in title/evidence disclosures. Number conversions are limited to plotting/measurement and bounded UI counts; time-window arithmetic uses BigInt. No formatted or plotted value feeds a financial input.

Each hook instance creates one HTTP session and one socket. Unmount/source replacement closes the socket and sends idempotent DELETE. Page exit uses keepalive deletion; late session-creation responses are explicitly released. WebSocket loss or five seconds without a valid frame immediately makes evidence unavailable and starts reconnecting to a **new** independent session, retaining selection/source but resetting replay cursor. The same capped exponential backoff (1/2/4/8/10 seconds, capped at 10) retries HTTP setup failures; successful receipt of valid stream evidence resets it. Runtime's nominal 200 ms publication cadence is not a hard scheduling guarantee; the five-second deadline is a conservative client liveness guard, including paused/ended/seeking replay. Visibility restoration also checks expired evidence. Manual Reconnect replaces the session. Replay/selection requests are abortable and owned by a session-scoped operation token: retiring a session releases its busy lock immediately, and old responses/finalizers cannot alter a new session. No fixture fallback appears on errors; backend HTTP status/detail text remains readable.

The browser retains one current presentation and one coalesced pending frame, not an ever-growing book/tape history. Frames over two million characters are rejected. Tape renders at most 100 prints, profile 200 levels, evidence 100 items and grouped ladder at most 100 requested rows. Visualization queries request 32 price rows for footprint/profile legibility and 128 for other modes, with a visible-range-derived column budget capped at 600; incremental merging retains at most 600 columns within the explicit server window. Changing row density refits automatic unpanned price views; explicit aggregation and manual panning are preserved. All price/volume aggregation remains native. Native collection and recording remain independent of UI sampling/coalescing. Chart and DOM requests are debounced, single-flight and scheduled after completion (one second / 750 milliseconds respectively), with cancellation, bounded deadlines and obsolete session/generation rejection. Failed requests label retained snapshots stale; disconnected evidence is not usable current evidence.

Historical ranges are half-open: `[start_ns, end_ns)`. Incremental merges discard
columns starting at the exclusive end, including when panning backward; an old
future column cannot remain the exact-data readout's selected sample.

Hidden documents suspend chart and ladder queries; hidden/maximized-away market panes, including inactive narrow tabs, gate their chart/ladder queries without unmounting stateful panes. Restoring visibility/panes resumes fresh requests unless explicitly frozen. Replay/collection remains independent; this is not a promise that every hidden component performs zero work. Each chart's bounded Settings/Inspector overlays scroll independently, keeping the plot usable and exact-data controls reachable in constrained laptop layouts.

Live instrument selection initializes its native consumer when the new lease set
commits, so accepted events drain while departed collectors shut down. Selection
still joins old owners before returning; cancellation joins bootstrap work before
releasing its charged queue ownership. Neither event loss nor a larger queue is
used to make a slow collector shutdown appear successful.

Native sorted-side construction uses incremental candidates only for sparse
delta sides with distinct touched prices **≤min(64, n // 64)**, where `n` is the prior
side's level count. Snapshots and dense changes retain full sorting. Payloads
larger than the sum of both side budgets conservatively take the full-sort path,
even when duplicate prices would reduce the distinct count. Touched-price sets
are collected outside the per-level validation loop; every level is still
validated. This changes construction work, not exact prices/quantities, event
ordering or book validation. Binance's observed-depth boundary remains
authoritative; a subscribed/truncated book is never promoted to complete depth.
No new economic calculations, execution behavior or live-feed integrations are
introduced. See [measured results and limits](WORKSPACE_IMPROVEMENT.md#backend-decision-and-measurement-limits):
sparse core timings improve in the finite repeated workload, while tiny-book
overhead and dense variance remain. Frame/ACK timing, data readiness and
sustainable collector capacity are distinct claims.

Historical chart projection runs on a separate bounded read worker over a
captured native evidence prefix, so growing chart history does not stall live
ingestion. Its source clock remains the captured clock; it is not relabeled as
the newest book. Snapshot ownership and cancellation are documented in
[Visualization](VISUALIZATION.md#ownership-bounds-and-incremental-delivery).

A busy read worker can retain one explicitly labeled **last completed presentation** instead of blanking tape/cursor on every historical query. Its presentation health is diagnostic/unready until ownership is available; it is never a new market validation. Seek, source failure and generation changes clear access to old evidence. Replay without a currently available cursor is labeled replay-unavailable, never live.

Workflow reads are single-flight and completion-scheduled (normally two seconds).
Inactive tabs, hidden documents, disconnect/loading/seek, source/selection or
generation changes suspend queries, abort owned requests and make old results
unavailable. Mutations serialize against reads/lifecycle actions; obsolete
responses cannot overwrite a newer session. Paper resume returns a new-generation
Frame and pauses replay.

**Workflow mutations are never automatically retried.** An abort, timeout or
disconnect can occur after a mutation committed. Refresh and inspect native
persisted state before repeating configuration, orders, alert edits or journal
writes. Query polling and socket/setup reconnects retry independently; neither
grants permission to resubmit a mutation.

After a committed visible-document render, two animation-frame callbacks provide a paint opportunity before the frame ACK is sent. Receipt timing starts at the beginning of the WebSocket message callback, before JSON parsing/validation, and includes decoding/coalescing/render work. The server receives `frame_id` and browser receive-to-paint-opportunity duration. Hidden tabs do not report painted frames. This is **not** GPU completion or synchronized one-way latency. **Diagnostics** labels server publication-to-ACK as a conservative round-trip upper bound; historical exchange/source clocks are not subtracted from browser clocks. The distinct ACK-expired counter reports bounded server ACK-buffer eviction, not presentation coalescing. Queue/coalescing/overflow and observer measurements come from Runtime. The source inspector includes an expandable live-ingestion disclosure of native SubscriptionStatus records: actual processing stages/clock, processed frames, queue counts/bytes/highwaters, lifecycle and reasons; demo/replay truthfully show an empty list. Production performance budgets require actual measured evidence.

Chart timing is separate from frame timing. Visualization HTTP receipt is recorded after complete body transfer and before JSON decoding. The Canvas callback reports CPU draw duration and two animation-frame paint opportunities for a specific `view_id`; only an actually painted visible view is acknowledged once via `/visualization/ack`. Chart receive→paint and publication→ACK upper bound have their own readout. Neither frame ACK nor HTTP arrival alone proves chart paint. Source clocks are never subtracted from browser clocks; these durations do not claim GPU completion.

## Dependencies and licensing

`frontend/package.json` pins direct versions; `package-lock.json` locks transitives. Node 22.22.0/npm 10.9.4 are the native toolchain. Runtime uses React/React DOM **19.2.4** ([MIT license](https://github.com/facebook/react/blob/v19.2.4/LICENSE)); their installed licenses and Scheduler's were read. Redistributed runtime notices live in `frontend/public/THIRD_PARTY_NOTICES.txt` and are copied into production assets.

Build/tool dependencies: Vite **7.3.1** ([MIT](https://github.com/vitejs/vite/blob/v7.3.1/LICENSE)), TypeScript **5.9.3** ([Apache-2.0](https://github.com/microsoft/TypeScript/blob/v5.9.3/LICENSE.txt)), ESLint/@eslint/js **9.39.2** (MIT), typescript-eslint **8.54.0** (MIT), eslint-plugin-react-hooks **7.0.1** (MIT), @types/react **19.2.10** and @types/react-dom **19.2.3** (MIT), and Playwright Test **1.58.1** ([Apache-2.0](https://github.com/microsoft/playwright/blob/v1.58.1/LICENSE)). Development packages retain their own license/notice files in locked npm distributions. npm reports the selected ESLint 9 release as deprecated; it remains an explicit lock, not a silent update. No project-wide license was inferred: the private frontend package is marked `UNLICENSED` rather than granting rights absent from the repository.

M0's [ADR-004](ARCHITECTURE.md#adr-004--native-canvas2d-heatmap-measured-before-expanding-dependencies) selected independently implemented native Canvas2D. **No chart package is adopted.** The M7/M8 visual direction references the primary [Flowsurface site](https://flowsurface.com/), [repository](https://github.com/flowsurface-rs/flowsurface) and [screenshot](https://raw.githubusercontent.com/flowsurface-rs/flowsurface/main/assets/screenshot.png) for dense market panes and restrained chrome; [OpenMarket's footprint guide](https://openmarket.xyz/learn/order-flow/footprint) and [TradingView's footprint documentation](https://www.tradingview.com/support/solutions/43000726164/) inform readable sell/buy rows and contextual chart controls. These are design references, not adopted financial semantics or feature-parity claims. This is an independent implementation, not a port; no reference source code or assets are copied. No external fonts, icons, UI source, screenshots or chart examples are redistributed as terminal assets. Accessible HTML controls, legends, evidence and summaries accompany the Canvas. Current performance, screenshots and acceptance belong in the [integration handoff](OMP_UPGRADE_HANDOFF.md); implementation alone is not a latency or parity certification.

## Verification commands and interaction selectors

The following M3 evidence is historical, not a claim that current M8 changes have passed gates: M3 integration passed frontend lint, three exact-format unit tests, TypeScript and production Vite build; real-server Chromium passed **8 tests in 33.4 s** after correcting a disabled-option matcher. Current executed checks belong in the integration handoff.

M4 workspace tests cover grouped depth, aggregation/range, independent freeze, persisted layout/new sessions, streaming pane geometry, risk availability and exact costs. Workspace unit coverage includes preference trust boundaries and bounded incremental history/seek replacement. Commands below are integration entrypoints, not results from this documentation update.

```sh
npm --prefix frontend run typecheck
npm --prefix frontend run lint
npm --prefix frontend test
npm --prefix frontend run build
# With the actual built backend running on 127.0.0.1:8000 and Chromium installed:
npm --prefix frontend run test:e2e
```

`frontend/e2e/terminal.spec.ts` uses the actual server without API/WS interception. It covers exact inputs, selection, estimates, visible validation errors, replay seek/step, separate document sessions, close cleanup and reconnect. The configured run is serial. Install the corresponding browser separately if needed with `npm --prefix frontend exec playwright install chromium`; no browser binaries are committed. Browser outputs are ignored under `frontend/test-results`/`playwright-report`. Capture and visually inspect both 1440×900 and 1280×800 plus reconnect/empty/warming/seeking/partial states; source-level checks cannot certify layout or measured latency.

`frontend/e2e/lifecycle.spec.ts` exercises explicit transport fault injection around that same real backend: suppressing upstream frames without closing the socket, repeated HTTP setup failures, holding a real replay response while replacing its session, catalog readiness transitions, and deliberate decode delay included in ACK timing. It does not manufacture market frames or financial responses. All five scenarios passed in the integrated eight-test run.

Actual visual review retained `docs/evidence/m3-terminal-1440.png`, `m3-terminal-hyperliquid-1280.png` and `m3-terminal-empty-1280.png`. The laptop surface had no horizontal document overflow; vertical workspace/table scrolling remains intentional. Hyperliquid's bounded depth, USDT quote/USDC settlement and unavailable exact-flow metrics remain visible. Reset removed the ladder/tape/analytics; requesting costs at cursor zero returned `no_validated_book`, not a zero-valued estimate. Browser error log was empty in the manual workflow. These are thin-terminal usability observations, not M4 chart/render-budget acceptance.

Stable accessible selectors (scope repeated chart controls to
`getByTestId('chart-pane-heatmap')` or `getByTestId('chart-pane-flow')`;
open Settings or Inspector before selecting their disclosed controls):

| Workflow | Selector |
| --- | --- |
| Source/search | Open `Source / workspace`, then `getByLabel('Data source')`; topbar `getByLabel('Search instruments')` |
| Exact cohort filters | `getByLabel('Filter venue')`, `getByLabel('Filter quote')`, `getByLabel('Filter market')` |
| Exact instrument | `[data-instrument-id="<native ID>"]`; selected has `aria-pressed="true"` |
| Metrics/ladder | `getByLabel('Depth band')`, `getByLabel('Ladder rows')`, role tab Metrics/Profile/Evidence |
| Size/side | `getByLabel('Order side')`, `getByLabel('Size convention')`, `getByLabel('Order size', {exact:true})` |
| Fees/stress | `getByLabel('Fee assumption')`, `getByLabel('Fee basis points')`, `getByLabel('Fee date')`, `getByLabel('Fee provenance')`, `getByLabel('Latency stress basis points')`, `getByLabel('Rebalance cost')` |
| Estimate | `getByTestId('estimate')` |
| Replay | `getByTestId('replay-play'/'replay-pause'/'replay-step'/'replay-reset')`, `getByLabel('Replay speed')`, `getByLabel('Seek delivery')`, button `Seek` |
| Recovery/errors | `getByLabel('Reconnect terminal')`, `getByRole('alert')` |
| Chart modes/settings | Pane-scoped `getByLabel('Chart mode')`, `getByLabel('Price aggregation')`, `getByLabel('Time bucket')`, `getByLabel('Chart range')`; buttons `Liquidity settings` / `Order flow settings` |
| Chart/DOM freeze | Pane-scoped `getByTestId('view-freeze')`, `getByTestId('view-recenter')`; `getByTestId('dom-freeze')` |
| Pane links/maximize | Buttons `Link Liquidity cursor`, `Link Order flow cursor`, `Link DOM cursor`, `Link tape cursor`; `Maximize Liquidity` / `Order flow` / `DOM` / `tape` (then `Restore …`) |
| Layout/drawers | `Source / workspace` disclosure, then `getByLabel('Workspace preset')`; buttons `Watchlist`, `DOM / tape`, `Costs / analytics`; separators `Watchlist width`, `DOM width`, `Chart split`, `DOM and tape split`, `Research pane height` |
| Narrow market tabs | Tablist `Market panes`; tabs `Liquidity`, `Flow`, `Book`, `Tape`; labeled tabpanels at ≤965 CSS px |
| Local workspace slots | `Source / workspace` then `Workspaces` disclosures; `getByLabel('Workspace slot')`; buttons `Save workspace`, `Load workspace` |
| Tape | `[data-pane="tape"]`; `getByLabel('Tape rows')`, `getByLabel('Tape side')`; button `Freeze tape` / `Resume tape` |
| Source/diagnostics | `Source & instrument details` and `Diagnostics` disclosures; `[data-testid="native-depth"]`, `[data-testid="source-preference-notice"]` inside source details |
| Freshness | Pane-scoped `getByTestId('chart-freshness')`; DOM/tape-scoped `getByTestId('data-freshness')` |
| Chart inspectors/timing | Buttons `Liquidity Inspector` / `Order flow Inspector`; pane-scoped `getByTestId('chart-telemetry')` inside `Paint & cursor diagnostics` (distinct from frame Diagnostics timing) |
| Workflow tabs | `getByRole('tab', {name:'Alerts', exact:true})` (also `Context`, `Paper`, `Journal`, `Research`) within tablist `Research pane` |

The terminal permits local alert configuration, simulated paper accounting and manual journal writes, but has no exchange submission, credentials, wallet/signing or private execution access. Research reports are read-only. Cost estimates, alerts, reference basis and paper fills do not establish execution readiness, profitable signals, account access or guaranteed fills.
