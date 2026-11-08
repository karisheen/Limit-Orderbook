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
from the active native source catalog, not a fixed coin list. Venue selection
includes native venues and **All perps / All spot** aggregates. Aggregate inputs
are the selected base asset's same-market cohort; the native primary instrument
remains explicit for exact workflows.

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

Binance and Bybit publish complete reachable metadata families independently:
spot can remain selectable when perpetual metadata is restricted, and vice versa.
Mixed discovery is explicitly partial; selecting a known instrument still needs
usable live book/trade evidence. Metadata success is neither execution permission
nor proof of every family or channel. Failed families retain their native access
reasons rather than fabricated instruments or a hidden switch to another exchange.
Open **Source & instrument details** to inspect these venue warnings. A ready
selected instrument does not imply that every other provider is available.
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
resolution/window controls are local to their panels. CVD, volume z-score,
depth/imbalance and realized volatility use delivered native observations.
Insufficient windows remain warming; unknown aggressors contribute total volume
but zero directional delta, never silently sells. The status strip exposes
coverage reasons, unknown-side and history-trade counts. Subscription-history
prints retain provenance on the tape but are excluded from ordered studies;
they are not evidence of continuous observed live flow.

Session/generation/source/instrument boundaries clear obsolete presentation
history. Display interval, tick/indicator and venue changes recompute retained
trade inputs without restarting the native session. Trade-study rebuilds retain
independent depth/imbalance history; changing the depth cohort resets that
history separately. Busy workflow mutations pause polling without clearing
same-owner caches or the delivery cursor. Bounded coverage is not full exchange
history, and unavailable data is never filled with invented trades.

The display bridge reads `/api/v1/market-data`, polling about every 150 ms when
caught up and draining bounded pages sequentially when `has_more` is set.
Its exclusive delivery/event cursor advances over nontrade events too. Trades
and additive continuity boundaries are consumed in canonical cursor order.
A trade-stream gap/reset or per-trade source-identity change removes that
stream's obsolete trades and restarts its studies, not unrelated stream evidence
or valid depth. Ordinary heartbeats and book-only controls do not reset trade
continuity. A cursor-retention gap clears obsolete trade history across streams;
missing prints are not filled. The browser retains the latest 100,000 exact
cohort trades for display recomputation and discloses truncation. Native live
retention is separately bounded to 4096 batches / 8 MiB, so this is not a
full-history guarantee.

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
is too short; the default footprint layout is unchanged. Closing **DOM / tape**
returns to the remembered Price or Liquidity view. Bring a chart into view to
resume its rendering and receive-to-paint acknowledgments.

### Price action

Historical candles use the native `/candles` API independently of live book
updates. Quick controls offer **1m / 5m / 15m / 1h / 4h / 1d**; **More** groups
additional supported intervals. Unsupported choices are disabled, not
approximated; **1M** is a calendar month. One candle represents the chosen
duration, not one incoming event.

Live history is one bounded venue OHLCV page, up to 300 bars where supported;
panning does not fetch unlimited older history. Demo/replay aggregates only
retained, already-delivered trades with explicit partial coverage. A short demo
cannot supply days of hourly history. Gaps remain gaps; zero-volume filler
candles are not invented.

Drag to pan, scroll or **+/−** to zoom, **Fit** for returned history and **Latest**
for the recent window. Hover/focus and **←/→** inspect exact OHLC/base volume;
**Shift+←/→** pans, **F/Home** fits, **L/End** follows and **Esc** clears inspection.
History refreshes about five seconds after each completed request while visible
and usable. Failed refreshes label retained same-owner candles stale; missing
history or unsupported queries remain explicit. Fetched/checked times are not
exchange freshness, and a closed time bucket does not certify complete history.
See [native candle contracts](VISUALIZATION.md#historical-price-candles).

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

### Spot IOC paper workflow

1. Select a supported spot/base-quantity instrument and expand **Explicit new
   run / fork — starting funds, sourced fees and latency**.
2. Enter venue/asset balances and provenance using native asset IDs, dated/sourced
   instrument fee schedules and fee asset IDs. Feed, decision and order latency
   nanoseconds require explicit values, including zero.
3. Confirm intent and **Create new paper run** or **Create explicit fork**.
   A fork records its parent but starts from entered funds, not copied accounting.
4. Submit exact base quantity as **Market IOC** or **Marketable limit IOC**.
   In paused replay, advance source time to reach pending arrival; browser time
   does not advance fills. Inspect orders, balances, positions, fees, clocks and
   consumed levels. Pending orders can be cancelled.
5. **Open read-only archived audit** is separate from active accounting.
   **Resume this run at cursor** requires matching demo/replay sources and
   confirms success with a new-generation native snapshot. Live archives cannot
   resume under a new owner.

Feed delay gates usable books; decision plus order delay fixes arrival. Funds
are contested at arrival. Native depth, constraints, quality and known dated
fees govern fills. Partial IOC remainder cancels; nonmarketable limits do not
rest. Consumed depth requires observed replenishment, not an unchanged snapshot,
revision or instrument switch. No passive fills, derivative economics,
borrowing or market impact is modeled, even when a perpetual chart is available.

Initial inventory cost basis remains unknown and affected P&L unavailable.
Marks are gross best bid, not liquidation proceeds. Rewind before creation
leaves intent only; further mutations require an explicit fork. The run list is
bounded to 100 without pagination/omitted counts; known older IDs remain
addressable through the API.

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
