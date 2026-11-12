# Crypto order-flow terminal

## One session, one source

The `/` page is a single crypto terminal. Its footprint, studies, market panes,
native charts and workflow drawers share one Python-backed session. Python owns
exchange connections, exact values, quality, replay and local accounting. The
browser consumes native events and projects display-only studies; it does not
open independent exchange feeds. Each browser document owns its own selection
and replay cursor, while native feed subscriptions can be shared safely.

There are no fiat currency products, browser API keys or alternate workspace
application. Crypto quote identities still matter: USD, USDT and USDC are not
interchangeable, and missing conversion evidence never becomes a 1:1 rate in
exact financial workflows.

## Launch

Use Python 3.13.5, uv 0.8.4 and Node 22.22.0/npm 10.9.4:

```sh
uv sync --locked --python 3.13.5 --group dev
npm --prefix frontend ci
npm --prefix frontend run build
uv run --locked --offline --no-sync python -m src.workstation.cli serve --network
```

Open `http://127.0.0.1:8000/`. The backend serves `frontend/dist`; no production
Node server is needed. `--network` explicitly enables public crypto discovery
and collection and makes live the server-preferred source. Startup does not
create a durable recording.

Omit `--network` for the sparse, initially paused synthetic offline demo. **Play**
advances fixture deliveries, not real exchange orders. Use
`serve --capture /operator/path/to/capture` to register a verified recording.
HTTP clients select a catalog source ID, never a filesystem path. Live data
being unavailable must not silently select demo.

`serve --state-dir data/workstation-state` selects durable local storage (also
the CLI default). Keep that directory across restarts for managed recordings,
paper, journal and research. CLI research must use the same directory to appear
in the terminal. Reload creates a new session, not automatic adoption of an old
paper run.

For development, keep the Python API running on port 8000 and run
`npm --prefix frontend run dev`. Open `http://127.0.0.1:5173/`; Vite proxies
`/api`, including WebSocket upgrades, and `/health` to loopback port 8000. Port
5173 is strict. Neither Vite alone nor `vite preview` supplies the native API.
See [development](DEVELOPMENT.md), [API](API.md), [demo/CLI](DEMO.md) and
[replay](REPLAY.md).

## Market and display controls

The top bar exposes **Instrument**, **Venue**, **Market** (**Perp / Spot**),
**Candle interval**, **Workspace**, **Panels** and **Theme**. Instruments come
from the active native source catalog, including initial streaming-only crypto
candidates while official metadata loads. Venue labels are friendly (**Binance**,
**Bybit**); native IDs remain unchanged. Venue selection includes native venues and
**All perps / All spot** aggregates. Aggregate inputs are the actual native
display cohort for the selected base asset and market type, not a guess from
the newest catalog. Price Action and footprint/studies share that cohort and
the persisted **Candle interval**. The exact primary remains the financial
order target even when aggregate display is selected.

**Indicative aggregate · N venues · quotes retained** means a display aggregate,
not normalized execution prices. USD/USDT/USDC prices may be combined without
conversion. Do not use that display as an executable arbitrage quote or paper
fill input. Quote, settlement, contract units and source quality belong to each
native instrument.

Native public adapters cover Binance global spot/USDT USDⓈ-M perpetuals and Bybit
spot/USDT linear crypto perpetuals, OKX spot/linear perpetual swaps, Bitget
spot/USDT perpetuals, Coinbase spot,
Hyperliquid perpetuals and Deribit BTC/ETH inverse perpetuals, plus Binance.US,
Kraken and KuCoin spot. Availability is catalog-, region- and provider-dependent.
Depth continuity, aggressor-side provenance and historical-candle support differ
by feed; adapter presence is not all-symbol or all-channel certification.
Discovery excludes Bybit linear TradFi products (stock, ETF, commodity and
forex classifications) and Bitget RWA/non-perpetual futures classifications.
Native crypto quantity, quote and settlement metadata remain intact.

Binance and Bybit begin direct public WebSocket subscriptions without waiting for
REST metadata. Their spot/perpetual metadata requests run independently in the
background. A **Streaming-only metadata** notice means tick, lot and order limits
are unknown—not that delivered market data is synthetic. Charts, studies and
recording/replay can work; exact costs, comparison, proposed risk and paper orders
remain disabled until the session's pinned metadata is verified.
Catalog candidates, observed native books, retained ordered prints and verified
financial metadata are distinct signals. Trade readiness uses retained
non-history prints, not book presence or only the recent tape window.
REST metadata restrictions do not prove WebSocket unavailability, and source
readiness does not certify every feed.
**Source & instrument details** retains metadata warnings. If newer verified
metadata becomes available, close other sessions and stop recordings holding that
instrument, then reconnect/reselect; existing shared streams keep their old version.
Bybit uses the [official equivalent mainnet REST host](https://bybit-exchange.github.io/docs/v5/guide)
`api.bytick.com`, which remains subject to regional restrictions.

**Workspace** presets **Order Flow**, **Liquidity** and **Volatility** configure
the display studies/sidebar. **Panels** adjusts visibility; visible card borders
resize the sidebar/studies panels. Six themes and display preferences are
browser-local. These controls do not create another financial session.

### Footprint and studies

Use **Bid × Ask / Delta / Volume**, **Profile**, **Heatmap** and **Indicators**
to configure the footprint. Indicators include session VWAP/deviation bands,
POC/value area, high/low, imbalance ratios and stacked levels. Drag to pan,
wheel to scroll, Ctrl/⌘+wheel to change row height and Alt+wheel to change candle
width; **Live** or double-click restores following. The footprint heatmap shows
executed volume, not resting liquidity.

Book grouping, primary/consolidated depth, tape size filters and study
resolution/window controls are local to their panels. Primary-book mode reports
a missing primary; it never silently substitutes a consolidated book.
CVD (including its display variants), volume z-score, depth/imbalance and
realized volatility use delivered native observations.
Insufficient windows remain warming; unknown aggressors contribute total volume
but zero directional delta, never silently sells. The status strip exposes
coverage reasons, unknown-side and history-trade counts. Subscription-history
prints retain provenance on the tape but are excluded from ordered studies;
they are not evidence of continuous observed live flow.

Session/generation/source/instrument boundaries clear obsolete presentation
history. Price Action, footprint and interval-based studies share **1m, 3m, 5m,
15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d, 1w, 1M**. Fixed buckets floor
against the Unix UTC epoch, including Thursday-aligned weeks; `1M` runs from
one UTC month start to the next. Interval, tick/indicator and venue changes
recompute retained trade inputs without restarting the native session.
Trade-study rebuilds retain independent depth/imbalance history; changing the
depth cohort resets that history separately. Busy workflow mutations pause
polling without clearing same-owner caches or the delivery cursor. Bounded
coverage is not full exchange history; missing prints are never invented.

The display bridge reads `/api/v1/market-data`, polling about every 150 ms when
caught up and draining bounded pages sequentially when `has_more` is set.
Its exclusive delivery/event cursor advances over nontrade events too. Trades
and additive continuity boundaries are consumed in canonical cursor order.
A trade-stream gap/reset or per-trade source-identity change removes that
stream's obsolete trades and restarts its studies, not unrelated stream evidence
or valid depth. Ordinary heartbeats and book-only controls do not reset trade
continuity. Authoritative global and per-stream retention floors prune obsolete
prints even when a caught-up page reports no cursor gap; unrelated retained
streams survive. The browser retains at most 100,000 exact cohort trades and
discloses truncation, but cannot extend the server's available history. Native
admitted history is capped at 10,000 trades / 8 MiB; live raw delivery retention
is separately bounded to 4096 batches / 8 MiB and can retire trades earlier.
The source's projected `display_frontier_ns`, not browser wall time, closes an
existing bucket after its boundary even without another print. This creates no
empty buckets and does not prove complete trade coverage.

Books expose up to 100 levels per side, with native quality/eligibility and
omission counts. Base quantities and quote notionals come from native contract
valuation, not a browser assumption that every contract is one base unit;
unavailable conversions remain null. Replay pages stop at the actual delivery
cursor and use source time, not today's wall clock. See the
[market-data contract](API.md#native-market-data-delivery).

The status strip's **Feed lag** is the latest ordered print's native
exchange-event-to-receive wall-clock difference, including clock skew, not
browser rendering latency. Missing exchange timestamps, subscription-history
prints, negative differences or unusable projections yield **—**, never zero.
Replay labels the original capture-time observation **Recorded lag**, not
playback latency; demo labels it **Synthetic lag**. Timestamp subtraction is
exact before conversion to display milliseconds.

## Source, workspace and recordings

Open **Source / workspace** for **Data source** and **Workspace preset**. Source
choices distinguish live, recorded replay and synthetic demo, with readiness and
reasons. URL `?source=<catalog ID>` takes precedence over an explicitly saved
choice, then the server preference. A requested unavailable source stays
unavailable rather than being substituted. Source changes fence foreign
instrument, clock and workflow evidence; connection status alone is not data
quality.

**Watchlist** retains its search and filters. Selecting an instrument closes the
popover and returns focus to its summary. Floating source/workspace, recording,
DOM settings and tape settings disclosures close on outside interaction or
**Escape**; inline **Diagnostics** stays open across reconnects.

**Workspaces** contains **Workspace slot**, **Save workspace** and **Load
workspace** for four browser-local slots. Slots store native view/layout
preferences, not backend sessions, capture files, paper ledgers or cross-device
state. Loading retains the current source/instrument and clears pinned ranges,
freezes and cursors. Preferences never restore old market data or a shared
session ID. Shell themes/panel preferences and native view preferences remain
browser-origin-local.

### Managed recording

1. Select a ready live source and the native instrument to capture.
2. Open **Recordings** and choose **Capture scope**: **Selected instrument**
   (`instrument`, default) records all primary-instrument owned channels,
   including book/trades and any reference/context channels. **Session cohort**
   (`cohort`) records all native session-owned keys, including context/reference
   streams, not just visible chart venues. Choose **Record selected instrument**
   or **Record session cohort**. Recording is live-only. Scope and keys are fixed
   at start; later selection changes do not retarget recording. Default capture
   does not require unrelated cohort channels.
3. Choose **Stop recording** to finalize and validate the capture. Closing its
   owning session (including disconnect) also finalizes it; server shutdown
   finalizes all active captures. The server owns paths under
   `--state-dir/recordings`; the browser never chooses filesystem paths.
4. Use **Refresh recordings** to inspect status/reasons. **Replay recording**
   is available for a completed usable capture once its replay source is ready.
   It selects that source in the same terminal; no server restart or second
   application is required. Recording ownership is not transferred.

Only the active owning session can stop its capture; another tab cannot adopt
it. Bounds are one active capture per session, two per server, 64 retained
managed entries and 128 MiB per capture. Capacity rejects new captures instead
of silently deleting older entries; no browser deletion API is provided.
Each row exposes **Captured instruments and channels**. Scope, instrument IDs
and channel summaries persist across restart; they declare capture membership,
not proof that every channel delivered evidence.

Incomplete/error states remain visible and are not advertised as complete
replay history. An active recorder can already report incomplete/error, so
inspect its ownership as well as status. On restart, the server recovers and
verifies managed entries, rediscovers complete replay sources and preserves
interruption/verification reasons. Keep the state directory for persistence.
A successful recording validates only its frozen scope, not full exchange depth
or all symbols. Missing channel evidence or an incomplete capture is not made
complete by choosing either scope, stopping successfully or restarting.
Interrupted mutations may already have committed: refresh before repeating
them. See [managed recording API](API.md#managed-recordings) and operator
[recording/storage contracts](RECORDING.md).

### Replay

**Play**, **Pause**, **Step**, **Reset**, **Speed** and **Delivery → Seek** operate
the actual native replay engine. Seek uses an exact delivery index, not browser
wall time. Seeking disables obsolete action surfaces; reset/rewind/source or
generation changes invalidate old evidence. Two tabs do not share a mutable
cursor. Replay controls are unavailable in live mode.

Pausing replay stops its source clock. Freezing a chart, DOM or tape only holds
that presentation; it does not pause replay, collection or recording. Old
recorded timestamps are judged against the replay cursor, not today's wall
clock. Demo remains explicitly synthetic even while playing.

## Integrated native charts

The chart tabs are **Footprint**, **Price action**, **Liquidity & flow** and
**Native book & tape**. They are views of the same source/session, not separate
feed applications. Native charts and exact inspectors retain native units,
quality reasons, provenance and bounded history.
First load opens **Footprint**; an explicitly saved chart-view choice is retained.
Native views preserve usable plot heights and scroll when the available screen
is too short; the default footprint layout is unchanged. Normal chart views use
one reference-style book/tape sidebar. Selecting **Native book & tape** replaces
that sidebar with exact inspection; Price Action has no duplicate embedded
book/tape, and there is no secondary DOM/tape sidecar toggle. Returning to a
chart restores the normal surface. Bring a chart into view to resume rendering
and receive-to-paint acknowledgments.

### Price action

The native `/candles` API defaults to **session trades** in live, demo and replay,
for spot and perpetuals. Current bars need no venue REST response. It uses the
actual selected cohort, primary-first, and labels primary versus aggregate scope.
Aggregate quotes remain indicative without conversion and never become fills.
Quick controls and **More** edit the same persisted interval as the top bar and
footprint; all 15 intervals above are supported for native session trades.

Trades carry exact `display_time_ns`: a source-owned receive-wall anchor plus
the matching receive-monotonic delta. Integer nanoseconds are floored before
bounded display conversion; exchange timestamps and browser time do not select
buckets. Native delivery order determines open/close within a bucket. Replay
cannot expose future prints, and source/generation/cohort/interval changes
discard obsolete responses.

Retained already-delivered prints form bounded, explicitly partial coverage.
Unknown base sizes and subscription-history prints are excluded with reasons.
A short demo cannot supply days of hourly history; no missing/zero-volume filler
bars are invented. A time-closed bar is not a completeness certificate.
**Venue history** (`venue_ohlcv`) is an explicit separate primary-only source,
exchange-UTC aligned where supported. It is not stitched into session candles
or substituted for a cohort aggregate. Its single bounded page is up to 300
requested bars in this UI; panning does not fetch unlimited older history.

Drag to pan, scroll or **+/−** to zoom, **Fit** for returned history and **Latest**
for the recent window. Hover/focus and **←/→** inspect exact OHLC/base volume;
**Shift+←/→** pans, **F/Home** fits, **L/End** follows and **Esc** clears inspection.
History refreshes about five seconds after each completed request while visible
and usable. Failed refreshes label retained same-owner candles stale; missing
history or unsupported queries remain explicit. Fetched/checked times are not
exchange freshness, and a closed time bucket does not certify complete history.
See [native candle contracts](VISUALIZATION.md#historical-price-candles).

**OI & funding** adds open-interest and funding panes under volume on the same
time axis, so positioning can be read against each price move. It is on by
default for a perpetual and opt-in for spot, where the related perpetual is
shown and labeled as such. The readout follows the inspection cursor: exact
open interest and its change inside the bucket, the exact funding fraction per
interval and an approximate annualization. Values are venue-reported context
observed by this session (Hyperliquid today), not executable prices and not the
funding model used by Paper. Unobserved intervals stay empty; the toggle is
disabled for Venue history. With the panes on, the plot needs at least 320 px:
use **Expand** or collapse the workflow drawer on short windows. See
[positioning panes](VISUALIZATION.md#open-interest-and-funding-beside-price).

### Liquidity & flow

The native paired charts expose resting liquidity, short-horizon candles,
executed-trade footprint/profile and shared-core CVD/OFI. Each retains its own
mode, interval, price grouping, viewport, freeze and inspection settings.
**More**, settings and **Inspector** expose exact data, navigation, quality and
paint diagnostics. Missing history and row cropping remain disclosed.

**Auto group** fits eligible native evidence. **Follow latest** moves with the
source-clock frontier while preserving the painted duration. **Fit time** uses
available native extent; **Recenter price** resets price geometry only. These
are distinct operations, not synonyms for resuming or unfreezing a chart.

**Cursor** links exact source time/price without synchronizing ranges, grouping
or freezes. Moving from a chart into the native ladder transfers the linked
cursor to that exact price. **Link chart time ranges** separately synchronizes
time navigation, not price origins, buckets or overlays. Off-screen exact prices
never select an unrelated nearest row. Ownership/clock changes invalidate obsolete links.
Native receive-clock UTC/local labels are not exchange timestamps or
browser-estimated historical times. See [visualization](VISUALIZATION.md).

### Native book & tape

The native ladder shows aggregate L2 resting quantities, not individual orders.
**Group**, **Rows**, **Size** and **Cumulative** affect presentation, not feed
reconstruction depth. **Rescale depth** explicitly resets its held bar cap;
quantity changes do not continuously normalize it. **Follow midpoint** and
**Recenter DOM** control centering; manual scrolling suspends following.
DOM and tape remain bounded to their pane and scroll internally; a deep live
book cannot expand the workspace or prevent manual scrolling.
**Freeze DOM** holds its snapshot without freezing collection or tape.
**Scope & provenance** exposes retained/subscribed depth and native reasons.
Invalid or unavailable evidence is not a zero-sized book.

Hovering or focusing a row is a transient cursor. Activating a row's price
(click, Enter or Space) pins that exact price: with the DOM **Cursor** link on,
the pin stays on linked charts after leaving the row and after switching to
**Liquidity & flow**, which is how a ladder price is carried to charts now that
book and charts are separate workspaces. Activate it again or press **Esc** to
release; a source, generation or selection change clears it.

**Time & sales** shows receive UTC, exact price, unit-bearing size and side.
**More** contains **Rows** and **Side** filters; these do not change collection.
Select a time cell for the pinned **Trade inspector**, including exact receive/
exchange references, subscription-history and side provenance. **Freeze tape /
Resume tape** is independent of DOM/charts/replay. Native pane **Cursor** and
**Max / Restore** controls preserve independent view settings.

## Workflow drawer

The workflow tabs are **Costs & proposed risk**, **Core analytics**, **Alerts**,
**Context**, **Paper**, **Journal** and **Research**. **Collapse workflow drawer**
closes the drawer without creating another session. Inputs are exact decimal
strings; inspect native eligibility/reasons before interpreting a value.

### Costs, risk and exact analytics

**Estimate & compare** sends side, base quantity or native quote budget, fee
assumption/provenance/date, adverse-latency bps and native-quote rebalance cost
to Python. Results are labeled snapshots, not continuously executable quotes.
Changed inputs require recalculation. Unknown fees remain unknown; invalid
books, missing depth or incompatible quotes remain exclusions. The comparison
table exposes same-feasible-base quantity, costs, fees, conversion assumptions,
net/bps and supporting exact inputs. Inventory, prefunding and non-atomic
execution assumptions are not invented.

The hypothetical entry/stop scenario sends exact entry, stop, side and either
base quantity or native-quote risk budget to `/proposal-risk`. Python computes
supported lot-constrained size/loss; unknown fees or insufficient metadata stay
unavailable. Gaps, slippage and unknown stop liquidity can cause greater losses.

**Core analytics** has **Metrics / Profile / Evidence** and a **Depth band**
selector for native 5/10/25/50 bps results. Units, windows, formula/configuration
and source references remain inspectable. Warming/incomplete windows are not
zero. Executed-volume profile is not resting liquidity; depletion/replenishment
heuristics do not establish order identity, hidden size or intent. Display
studies never override these exact quality gates.

### Alerts and supporting evidence

Choose an exact instrument, kind/direction, threshold/rearm levels in the
server-declared unit and cooldown. Event OFI and persistent displayed liquidity
also require a positive rolling window. Depth shock is a low absolute depth
level, not percent change. Net-cost rules require an observed sell instrument,
quantity and explicit fee/stress assumptions; unknown fees never become zero.

Native rule mutations and evaluations expose qualification, latch/cooldown,
retained fires and omitted counts. Request rule/event evidence explicitly; it
remains a labeled snapshot until requested again. Depth disappearance is not a
trade, and displayed liquidity does not prove spoofing or predictive edge.
Rewind/reset rebuilds only prefix-valid rules/latches/fires, not future evidence.

### Hyperliquid context and quote conversion

**Context** shows native mids, mark/oracle, funding rate/interval and open
interest where available, with missing-field reasons. These are reference-only,
not replacement executable depth. Funding is a fraction per declared interval;
open-interest units are adapter-declared.

Related spot/perpetual basis preserves underlying, quote and settlement
identity. USD, USDT and USDC remain distinct. Explicit side-aware FX/conversion
rates and fee evidence may be required; absent, stale or skewed conversion
evidence leaves basis unknown. These are crypto quote-conversion assumptions,
not fiat currency product feeds. Assumptions are source-scoped, not saved market
facts. “Funding cost” in conversion evidence means the cost to acquire a
reference amount, not perpetual funding P&L. Basis excludes trading fees,
funding P&L, inventory and account access; it is not executable arbitrage.

### Paper workflow: spot and isolated perpetuals

1. Select an exact native instrument with verified session-pinned financial
   metadata. The backend capability/reasons determine supported economics and
   native order constraints; a chart or newer discovery descriptor does not
   authorize paper orders.
2. Expand **Explicit new run / fork — starting funds, sourced fees and latency**.
   Enter exact venue/asset balances with provenance, dated instrument fee
   schedules and fee asset IDs. Feed, decision and order latency nanoseconds
   require explicit values, including zero. Perpetual funds must be in the
   native settlement collateral asset, not a display quote or spot inventory.
3. For perpetuals, supply per-instrument leverage (1–1000), maintenance-margin
   rate below `1 / leverage`, and liquidation-penalty rate below 1, each model
   with provenance/as-of wall nanoseconds. Rates are fractions, not fee bps.
   Choose an explicit funding model: none, a signed periodic rate with
   interval/first boundary, or up to 10,000 strictly chronological dated events.
   Signed funding-rate magnitude must be below 1; boundaries cannot predate the
   model. No-funding is a disclosed assumption; missing funding is not zero.
   These are user models, not exchange risk tiers or automatic Context funding.
4. Confirm intent and **Create new paper run** or **Create explicit fork**.
   A fork records its parent but starts from entered funds, not copied accounting.
5. Submit **Market IOC** or **Marketable limit IOC** in exact native quantity:
   spot base units or the perpetual's declared base/contract units. Buy opens
   long or closes short; sell opens short or closes long. An opposite order
   closes first before an eligible opening remainder. **Reduce only — never
   increase or flip exposure** forbids additional risk. **Prepare full reduce-only
   close** fills the opposite side and exact position quantity into the form;
   **Submit simulated order** remains explicit. Insufficient depth/collateral or
   native constraints can leave residual exposure, cap or reject fills.
6. In paused replay, advance source time to pending arrival; browser time does
   not advance fills or funding. Inspect orders, balances, perpetual positions,
   fees, funding, liquidation records, clocks and consumed levels. Pending orders
   can be cancelled.

Feed delay gates usable books; decision plus order delay fixes arrival. Funds
are contested at arrival. Native depth, order constraints, quality and dated
fees govern fills. IOC remainders cancel; nonmarketable limits do not rest.
Consumed depth requires observed replenishment, not an unchanged snapshot,
revision or instrument switch. No passive fills, borrowing or market impact is
modeled. Spot initial-inventory cost basis stays unknown; gross bid marks are
not liquidation proceeds.

#### Perpetual accounting and protective liquidation

Positions are one-way and isolated per exact venue/instrument. Free settlement
collateral, initial margin, isolated collateral, realized/unrealized P&L,
trading fees, funding, liquidation fees and debt are separate exact values.
Linear contracts use native base exposure (including any contract multiplier);
inverse quote-face contracts settle reciprocal-price P&L in the base asset.
Verified native payout rules such as Hyperliquid's documented settlement
denomination are not a USD/USDT/USDC conversion assumption. Unknown contract
economics remain unsupported.

Funding applies once at deterministic source-time boundaries against isolated
collateral. Positive payment amounts debit it; negative amounts credit it, and
unresolved amounts stay unknown. Longs mark to an eligible executable bid and
shorts to an ask: these are conservative **book proxies**, not exchange marks.
Missing/stale marks remain unavailable. Maintenance breaches trigger protective
IOC closure through remaining observed depth, sharing consumed-depth reservations
with orders. Trading fees and liquidation penalties remain separate. Partial or
missing depth cannot invent full closure, clear residual exposure or erase debt;
breach/shortfall remains visible and new risk is blocked.

#### Freeze, archive, replay and resume

A bounded funding catch-up/record limit can freeze paper at its last valid
accounting prefix while charts continue. Inspect the frozen reason and last
accounting delivery; later market progress is not later accounting. Frozen paper
rejects further observer/time/order mutation rather than skipping obligations.
Explicit reset/seek reconstructs replay; explicit configuration starts a new run
at the current source with current books. There is no automatic thaw.

**Open read-only archived audit** is separate from active accounting. Retaining
`--state-dir` preserves run assumptions, commands and accounting across process
restart. **Resume this run at cursor** reconstructs a matching demo/replay prefix
before a new-generation cutover; live archives cannot resume under a new owner.
Frozen/incomplete candidates cannot replace the active ledger. Historical
prefixes can be resumed, but future recorded commands fence new mutations until
an explicit fork. Rewind before creation leaves intent only, not an active run.
Funding/liquidation lists expose bounded windows with totals/omissions; archived
accounting retains full historical records. The run list is bounded to 100
without pagination/omitted counts; known older IDs remain addressable by API.

### Journal and read-only research

**Journal** creates/edits manual rationale, optional entry/stop and context.
New links require an active run and visible selected-instrument order. Edits
retain original source/session/delivery/clock and links. Notes survive rewind;
later authorship is not evidence known at an earlier cursor. Read-only archives
do not populate active link choices.

**Research** lists persisted attempts and exact configuration, source/hash
provenance, exclusions, support counts, limitations and paged trials. The browser
cannot launch experiments or tune parameters. Use `research run`, `research list`
and `research show` with the server's `--state-dir`; see [DEMO](DEMO.md).

All candidate outcomes remain, including failures. Chronological partitions
purge boundary-overlapping outcomes and embargo later partitions; selection
uses train/validation only. Unknown fees exclude net economics. Independent
unfunded markouts are not realized portfolio returns; turnover/drawdown require
a sequential funded model. Synthetic results cannot establish an edge.

## Quality, lifecycle and safety

Transport connection, source readiness and market evidence quality are distinct.
Exact source/instrument details retain quote, settlement, feed scope and native
reasons. `analytical_queue_overflow` is an evidence failure requiring collector
capacity investigation; browser reconnect does not repair lost observations.
A shallow/truncated book is never promoted to complete exchange depth.

Native freshness distinguishes source-clock progress, browser evidence residency
and response residency. A new response carrying old evidence does not make it
fresh. Replay uses its own comparable source clock; paused replay is not stale
merely because the recording is old. Missing clocks stay unavailable, not zero.
Rendering and ACK durations are separate diagnostic bounds, not GPU completion,
exchange-to-screen latency or proof of new market data.

The browser keeps exact financial inputs/outputs as strings; conversion to
numbers is for display coordinates and bounded UI counts. Native histories and
queries are bounded. Hidden/inactive native views suspend their queries;
collection and recording remain independent of presentation sampling. Frozen
or stale snapshots retain their original ownership and quality.

Session/source replacement, generation changes and seek/reset fence requests
and clear obsolete results. Socket/setup recovery creates a new independent
session rather than restoring a saved mutable backend session. Read polling
and reconnect can retry; **workflow mutations never retry automatically**.
Abort, timeout or disconnect can happen after a mutation committed. Refresh and
inspect persisted native state before repeating orders, configuration, capture
operations, alert edits or journal writes.

## Development, licenses and evidence

See [DEVELOPMENT](DEVELOPMENT.md) for locked setup and gate commands,
[API](API.md) for request/response bounds and [CONTRACTS](CONTRACTS.md) for domain
policies. Source and redistributed license notices remain in
`frontend/public/THIRD_PARTY_NOTICES.txt` and `frontend/public/licenses`.

This guide describes controls and restrictions, not executed verification.
Historical test/live observations in the [handoff](OMP_UPGRADE_HANDOFF.md) and
[feed validation](FEED_VALIDATION.md) do not certify this release's full catalog,
all channels, long-running soak or trading readiness. Public availability must
be checked separately from offline fixtures. The terminal has no exchange
submission, credentials, wallet/signing or private execution access. Costs,
alerts, reference basis and paper fills are analysis/simulation, not guaranteed
fills, profitable signals or trading advice.
