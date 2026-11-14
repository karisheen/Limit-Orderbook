# Quickstart and operating manual

Public crypto observation, exact analysis, recording/replay and **simulated**
trading. No exchange orders, credentials or wallet are involved. Engineering
stabilization is not live-execution certification; current verification status
is in [STABILITY_REVIEW](STABILITY_REVIEW.md).

## 1. Start, update and keep your data

Use Python **3.13.5**, uv **0.8.4**, Node **22.22.0** and npm **10.9.4**.
From the repository root:

```sh
uv python install 3.13.5
uv sync --locked --python 3.13.5 --group dev
npm --prefix frontend ci
npm --prefix frontend run build
uv run --locked --offline --no-sync python -m src.workstation.cli serve --network --state-dir data/workstation-state
```

Open **http://127.0.0.1:8000/**. Python serves the built frontend and public feeds;
there is no production Node server. Keep one backend process/worker. Live startup
does **not** start a recording. Keep `data/workstation-state`: it holds managed
recordings, paper archives, journal and research. Back it up with the server
stopped; do not delete it to fix a chart.

For an update, first stop/finalize your recordings and inspect their status,
retain paper run IDs/audits, then shut down your server normally. Preserve any
local changes; only fast-forward a clean checkout:

```sh
git pull --ff-only
uv sync --locked --python 3.13.5 --group dev
npm --prefix frontend ci
npm --prefix frontend run build
uv run --locked --offline --no-sync python -m src.workstation.cli serve --network --state-dir data/workstation-state
```

If Git refuses, resolve your local work rather than resetting it. Reloading the
page or restarting creates a new session, **not** adoption of an old live paper
account. For development, run `npm --prefix frontend run dev` alongside Python
and use port **5173**; Vite proxies port 8000. See [DEVELOPMENT](DEVELOPMENT.md).

## 2. Choose the source and exact market

One browser document owns one native session. Its views and workflow drawer
share source, selected instrument and replay cursor. Another tab is another
session; backend feed subscriptions may be shared, but accounting/cursors are not.

| Task | Action and meaning |
| --- | --- |
| Observe live | Start with `--network`; select live in **Source / workspace → Data source**. Provider access/readiness still applies. |
| Practice offline | Omit `--network`. The synthetic demo starts paused; **Play** advances invented fixture data. |
| Replay a managed capture | **Recordings → Replay recording** on a completed usable row. |
| Register an existing capture | Start with `serve --capture /absolute/path/to/capture --state-dir data/workstation-state`, then select its catalog source. |

`/?source=live` or `/?source=<catalog ID>` overrides saved source preference.
An unavailable requested source never silently becomes demo. Explicit **Ticker
history** can request current public REST data even without `--network`; use
Observed flow for causal/offline replay evidence.

1. Choose **Market: Spot / Perp**, then **Instrument** and **Venue**. Search accepts
   normalized symbols and venue names; inspect **Source & instrument details**
   for exact native ID, quote, settlement, contract units and metadata status.
2. **All spot / All perps** chooses a display cohort. Footprint, observed candles
   and studies use that cohort; financial actions and ticker candles target the
   exact primary. A single-venue **display** is not a promise that the backend
   stops collecting the session's other owned channels.
3. Read quality reasons. **Streaming-only metadata** allows observation but does
   not supply verified tick/lot/order rules for costs or paper. Chart activity,
   book readiness, ordered-trade readiness and financial eligibility differ.

**USD, USDT and USDC are distinct.** Indicative aggregate prices may combine
quotes **without conversion**. They are not executable spreads or paper prices.
Spot, perpetual and wrapped-underlying identities are not interchangeable.
See [market controls](TERMINAL.md#market-and-display-controls) and [COSTS](COSTS.md).

## 3. Arrange the workspace

Choose a **Preset**: **Order flow**, **Price focus**, **Liquidity**, **Volatility**
or **Execution**. **Theme** offers six themes. **Edit layout** adds/removes panels
and exposes Move/Resize; desktop handles drag, or focused controls accept arrow
keys. Escape cancels a drag; **Finish editing** exits. Narrow screens stack
panels: dragging is disabled; choose Move/Resize and use direction buttons or
arrow keys to edit saved desktop geometry.

For an uncluttered price chart, choose **Price focus** and **Collapse workflow
drawer**. Minimum chart heights can require workspace/panel scrolling in short
windows; reopening the drawer does not discard its inputs.

**Saved layouts** stores named geometry/display preferences, not source,
instrument, cohort, active paper state or market history. Loading a layout does
not rewind a session. Closing drawers and hiding panels changes presentation,
not required collection. Chart/DOM/tape **Freeze** is independent of replay Pause.

Layouts and theme preferences are browser-local; a state-directory backup does
not include them. Another browser/profile or hostname has separate preferences.

## 4. Read the views correctly

### Price action

- **Auto** chooses **Ticker history** for live, **Observed flow** for demo/replay.
  Ticker = exact-primary exchange-time REST candles; observed = retained selected
  cohort trades on projected receive time. They are never stitched/substituted.
  A healthy ticker can coexist with a failed observed-flow collector.
- The top-bar **Candle interval** is global: 1m through calendar-month **1M**,
  subject to provider support. Observed fixed intervals use UTC epoch buckets;
  ticker preserves provider alignment. Short demo/observed histories cannot
  supply years of candles, and missing buckets are not invented.
- Pan left for older ticker pages; current bars use labeled **5-second polling**.
  An offscreen chart pauses presentation/polling; **View chart** scrolls it into
  view to resume. Collection is separate and continues.
  Drag/pinch/wheel navigates; **Auto**, **Log**, **Fit**, **Latest** and **Right
  margin** control scale/range/following. Fit means *loaded* history. Hover or
  focus and use left/right arrows for exact OHLC/volume; scroll the exact readout
  when needed rather than treating truncated coordinates as financial values.
- At **10,000 mounted bars**, growth pauses explicitly. Use the history-reset
  action to reload the recent page; Fit does not clear the cap. Older-history
  failure retains loaded bars. Kraken/Hyperliquid have bounded provider history;
  empty responses alone do not prove exhaustion.
- **OI & funding** is opt-in for compatible **Observed flow**, not ticker time.
  Inspect supplied instrument, units, interval and missing-data reasons. A related
  Hyperliquid perpetual is labeled if it is not the selected instrument. Observed
  funding is **not** Paper's funding model, and no unobserved history is backfilled.

Current external ticker history selected during replay is **not replay evidence**.
Full controls: [Price action](TERMINAL.md#price-action).

### Footprint, liquidity, book and studies

| View/tool | Use it for; do not infer |
| --- | --- |
| **Footprint: Bid × Ask / Delta / Volume** | Executed trade distribution; unknown aggressors add volume but zero directional delta. Profile/Indicators expose VWAP, POC/value area, imbalance and bands. |
| **Executed heatmap** | Traded-volume intensity, **not resting orders**. |
| **Indicators → Resting liquidity** | Optional retained historical primary-book depth. Set opacity and stable quantity ceiling; blank spans mean unknown. Not today's book stretched backward or consolidated depth. |
| **Indicators → Large prints** | Individual retained trades with venue badges and exact references, not merged footprint cells. |
| **Liquidity & flow** | Native resting heatmap, footprint/profile, CVD/OFI. **Inspector**, **More**, Follow latest, Fit time and Recenter price expose exact evidence/navigation; these do not unfreeze a pane. |
| **Native book & tape** | DOM is aggregate L2, not individual orders. Group/Rows/Size/Cumulative only change display. Recenter DOM/Follow midpoint control centering. Click/Enter/Space a price to pin it; Escape releases. |
| **Time & Sales (grid)** | Newest-first quick display in UTC; All / ≥2× / ≥5× / ≥10× compares size with the session average. Values use compact display formatting; use the native tape for exact evidence. |
| **Tape inside Native book & tape** | Receive UTC, exact price/size/side; select a time cell for Trade inspector. Side/size/row filters and Freeze tape do not filter the source recorder. |
| **CVD, OFI, volume z-score, depth/imbalance, volatility** | Bounded observed studies. Warming, gaps and unknown-side reasons matter; depth depletion is not proof of executions or intent. |

The native **Cursor** link connects liquidity/book price/time evidence; **Link
chart time ranges** is separate. Price action's OHLC cursor is independent.
Closed Inspector avoids expensive tables; reopen it for exact data. Offscreen
presentation may pause while collection continues. [TERMINAL](TERMINAL.md) and
[VISUALIZATION](VISUALIZATION.md) document gestures, clocks and coverage bounds.

## 5. Costs, risk and alerts

Open the workflow drawer. In **Costs & proposed risk**, enter side and exact
quantity/native quote budget, sourced dated fees, adverse-latency and rebalance
assumptions; **Estimate & compare** produces a labeled snapshot. Recalculate after
input changes. Unknown fees/depth/conversion are unavailable, not zero. Proposed
entry/stop risk is hypothetical lot-constrained sizing, not a guaranteed stop loss.

**Core analytics** offers Metrics/Profile/Evidence and native depth bands.
**Alerts** uses exact instrument, kind/direction, declared-unit threshold/rearm
and cooldown; OFI/persistent-liquidity rules also need a positive rolling window.
Inspect qualification, latch and fires; fetch evidence again for a newer snapshot.
These are local conditions, not external notification delivery or profitable
signals. **Context** supplies reference-only marks, OI/funding and explicit quote
conversion evidence, never replacement executable depth.

## 6. Record, stop, verify, replay

1. On live, open **Recordings**. **Capture scope → Selected instrument** records
   its owned channels; **Session cohort** records the full session-owned scope,
   including reference/context. Press **Record selected instrument** or **Record
   session cohort**. Scope is fixed at start even if display selection changes.
2. Press **Stop recording**; allow finalization/verification. **Refresh recordings**
   and inspect status/reasons and **Captured instruments and channels**. Only
   the owning session can stop it. Disconnect/session closure also finalizes;
   explicitly stopping and inspecting first is clearer.
   Final verification can outlast the 30-second response deadline. Do not press
   Stop again blindly: listing refresh reconciles that same recording's released
   final state and clears a resolved timeout. Incomplete/error reasons remain
   visible; a missing row alone is not proof of success.
3. **Replay recording** requires a completed usable capture and ready replay
   source. Incomplete/error is not complete history, even after a successful stop.
4. Use **Play / Pause / Step / Speed / Reset / Delivery → Seek**. Seek is an exact
   native delivery index. Paused source time stops fills/funding; freezing a chart
   does not. Failed replay reconstruction requires explicit seek/reset, not
   continuing a partially applied observer state.

Managed limits: one active capture/session, two/server, 64 retained entries,
128 MiB/capture. Capacity rejects rather than silently deleting old captures;
there is no browser deletion API. Preserve the state directory. See
[RECORDING](RECORDING.md) for durable completeness/recovery and [REPLAY](REPLAY.md).

## 7. Run paper safely, then journal/research

1. Select an exact supported native instrument with **verified pinned metadata**.
   Open **Paper → Explicit new run / fork — starting funds, sourced fees and latency**.
2. Enter venue/asset balances with provenance, dated instrument fees and fee asset,
   and explicit feed/decision/order latency (including deliberate zero). Spot needs
   prefunded cash/inventory; perpetuals need native settlement collateral.
3. Perpetuals also require explicit leverage, maintenance/liquidation assumptions
   and funding model (none, periodic or dated events). Rates are fractions;
   provenance/as-of times are required. These are user assumptions, not exchange
   tiers or automatically adopted Context funding. Respect native quantity/lot,
   tick, minimum and supported settlement constraints.
4. Confirm **Create new paper run** or **Create explicit fork**. A fork starts from
   entered funds, not copied balances. Submit **Market IOC** or **Marketable limit
   IOC**; unfilled remainders cancel. **Reduce only** forbids increasing/flipping
   exposure; **Prepare full reduce-only close** only prepares the form—submission
   remains explicit and depth can leave residual exposure.
5. Inspect pending orders, consumed depth, fees, balances, position/funding,
   liquidation/debt and accounting clock. Advance replay to pending arrival.
   No passive queue fills, borrowing or market impact are modeled.

**If Paper freezes:** inspect its durable reason and last complete accounting
prefix. Global analytical loss, or a relevant book/source gap affecting pending
orders/open perpetuals, can make later accounting unknowable. Fresh books or
market recovery do not repair missed fills, funding or liquidation paths and do
not thaw the run. Funding catch-up/record bounds can also freeze. Keep the old
audit; use an **explicit new run/fork with entered starting funds** to establish a
new accounting boundary. Do not repeatedly submit orders to test recovery.

**Open read-only archived audit** is not resume. **Resume this run at cursor** is
limited to reconstructible matching demo/replay prefixes; a new live session
cannot adopt a previous live account. See [paper controls](TERMINAL.md#paper-workflow-spot-and-isolated-perpetuals).

**Journal** records manual rationale/entry/stop/context and eligible active
run/order links; authorship remains later-authored even when viewed after rewind.
**Research** reads persisted experiments; the browser cannot launch/tune them.
For example, deliberately generate a synthetic experiment in the same state dir:

```sh
uv run --locked --offline --no-sync python -m src.workstation.cli research run --demo --duration 900 --state-dir data/workstation-state
uv run --locked --offline --no-sync python -m src.workstation.cli research list --state-dir data/workstation-state
```

Use [DEMO](DEMO.md#offline-causal-research) for capture/configuration commands and
[STRATEGY_RESEARCH](STRATEGY_RESEARCH.md) for limitations. Independent unfunded
markouts and synthetic results are not portfolio returns or evidence of an edge.

## 8. Recover without hiding evidence loss

| Symptom | Safe response |
| --- | --- |
| 403 / 451 or regional/provider denial | Read the exact provider/channel reason. Use a supported reachable venue or explicit demo/replay; do not bypass restrictions or call denied data zero. REST and WebSocket access can differ. |
| `analytical_queue_overflow` | Treat observed analytics as unavailable. Retain diagnostics: queue/highwater, owned streams, service-batch duration, queue wait, source/instrument and time. Inspect paper freeze and recording status separately. Reconnect restores transport, not missing observations. After resolving capacity and inspecting ownership, deliberate reselection/new session establishes a fresh boundary, never recovers the missing accounting/history. Do not inflate queues, drop events or lower depth to conceal loss. |
| `owner_busy; last_completed_presentation` | The native owner is processing a turn and the UI is holding its last complete snapshot. This is not lost history. Current financial eligibility is not inferred from held pixels; sustained high queue waits still require a capacity check. |
| Connected but stale/empty | Check source, exact instrument/cohort, metadata/quality, replay Pause, pane Freeze and retained-history bounds. Socket connection is not book/trade readiness. Demo may need Play; sparse intervals may legitimately be empty. |
| Ticker stale or older page failed | Inspect the reason; use **Retry candles / Retry older history**. Retained bars stay stale, not fresh merely because displayed. Check provider interval/history support; use the explicit reset at the history cap. |
| Replay operation failed | Use explicit **Reset** or **Delivery → Seek** to rebuild a complete prefix; do not assume rollback or press Play through failure. |
| Mutation times out/disconnects | Selection/replay/paper-resume response ownership is bounded to 15 seconds. The server may already have committed. Inspect current/persisted state before a deliberate repeat; mutations never auto-retry. Other workflows also require this ambiguity discipline. |
| Canvas error | Treat that panel as failed, not valid stale evidence. Other panels may remain usable. Retain the visible error and source details; do not infer market failure solely from a paint failure. |

**Feed lag** is exchange-event-to-receive time (including skew), not browser paint
latency. Recorded/Synthetic lag is explicitly labeled. Observer duration measures
a **batch**, not a single event. Busy live turns service up to 128 already-admitted
deliveries; bootstrap uses up to 32. Neither figure is queue capacity.

## 9. Practical use on two cores / 8 GiB

Start with one backend and one browser tab, one useful visible chart/preset, and
closed expensive inspectors/drawers. Avoid concurrent development builds,
research runs and broad capture/profiling jobs while assessing live behavior.
Use replay for repeatable investigation; monitor Activity Monitor, memory
pressure and native diagnostics rather than guessing from chart FPS.

Hiding panels, selecting a simpler preset or a single-venue **display** may reduce
browser work; it **does not certify fewer backend subscriptions**. Inspect actual
owned streams. Stop unneeded owned recordings deliberately before closing tabs;
recorders and other clients can retain shared feeds. Never trade integrity for
smaller queues/history by dropping required events or silently reducing depth.

Observed host: MacBookPro14,1, 2 physical/4 logical cores, 8 GiB, macOS 13.7.8.
A `memory_pressure` snapshot reported 55% free and backend physical footprint was
about 287 MiB. Neither proves absence of pressure under every workload or that
hardware explains the incident. See [measured evidence and operating limits](STABILITY_REVIEW.md).
