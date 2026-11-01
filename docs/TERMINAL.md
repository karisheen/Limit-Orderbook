# M4 liquidity workspace

The React/TypeScript terminal is a presentation client of the real versioned Runtime HTTP/WebSocket API. It does not collect feeds, construct books, calculate financial metrics, estimate fills, or inject fallback fixtures. Python analytics/cost/visualization engines supply authoritative values. M4 adds the historical Canvas workspace and proposed-risk consumer; alerts, paper trading and journals remain separate milestone work, not placeholder tabs.

## Launch

From the repository root with the locked Python environment already installed:

```sh
export PATH="$PWD/.tools/node/bin:$PATH"
npm --prefix frontend ci
npm --prefix frontend run build
.venv/bin/python -m src.workstation.cli serve
```

Open `http://127.0.0.1:8000`. The backend serves `frontend/dist`; no second production Node server is needed. The default source is explicitly synthetic, nonrecording offline demo replay. A durable recording may be made available with `serve --capture /operator/path/to/capture`; HTTP clients select the managed source ID, never a filesystem path. `serve --network` explicitly enables public live discovery/collection; it is not necessary for demo/replay and must not compete with another collector owner. See [API](API.md), [DEMO](DEMO.md), and [REPLAY](REPLAY.md) for source readiness, recorded-capture verification and operator prerequisites.

Development uses the same backend on port 8000 plus:

```sh
npm --prefix frontend run dev
```

Open `http://127.0.0.1:5173`. Vite proxies `/api` (including WebSocket upgrades) and `/health` to loopback port 8000. Port 5173 is strict; no silent move to an unapproved origin. `vite preview` is a static build-inspection tool, not an API server; use the Python server for the working production terminal.

## Working controls

- Search by exact instrument ID, native symbol, asset, venue or market. Venue, exact quote-asset and market filters are independent; instrument buttons retain native IDs. Quote identities are not inferred from ticker alone. The selected instrument exposes metadata, tick/lot, settlement, execution access and native feed mode.
- The source selector identifies demo/replay/live, synthetic provenance, recording state, readiness and reasons. Loading/unavailable sources remain visible with status/reasons but cannot replace a usable session. A single-flight catalog refresh runs every three seconds after each request completes, with a five-second request deadline; newly prepared recordings/live sources appear without resetting replay. Active instruments are fetched with `/instruments?source_id=<exact source ID>` before presentation and on refresh, so identical instrument IDs never inherit another capture's tick/lot metadata. Failed refreshes expose an error and hide stale instrument metadata until recovery. Changing recorded sources intentionally selects that source's reflected exact default because catalogs may differ. A live switch retains an explicit instrument; unsupported mappings produce an API error, not a replacement instrument. Streaming itself preserves selections, text inputs, open evidence disclosures and pane scroll positions.
- Core metrics show exact string values, units, eligibility and expandable window/quality/formula/config/source references. The depth-band selector displays the engine's 5/10/25/50 bps outputs; it does not recompute depth in JavaScript. Incomplete/warming windows stay unavailable. Profile and observed display evidence are additional table views, not heatmaps or predictive signals.
- The price-grouped ladder requests `/ladder`, with exact backend base quantities and best-outward cumulative totals. Bid/ask bucket rules remain backend-owned. Rows controls presentation only; it never changes reconstruction depth. **Freeze DOM** holds the grouped snapshot independently; **Recenter DOM** scrolls back to the best-quote boundary. Tape continues showing normalized public prints, aggressor/unknown status, receive UTC, exchange-time tooltip and explicit subscription-history labels. Missing evidence never becomes zero.
- Proposed order controls send either base quantity or native quote budget, side, fee assumption/provenance/date, adverse latency bps and native-quote rebalance cost to `/costs`. Inputs are text, not JavaScript numbers. Unknown fees remain unknown. A result is an explicitly labeled snapshot, not a continuously current executable quote; press **Estimate & compare** again to refresh. Changed inputs are flagged until recalculation. Selection/seek/reset/session replacement clears old results.
- The pair table exposes same-feasible-base quantity, gross reference, depth cost, both fees, FX, rebalance, adverse latency, net/bps, exact inputs and exclusions. Default UI comparisons use native quote/market cohorts; USD, USDT and USDC are distinct. Unknown inventory, prefunding and non-atomic execution assumptions remain visible. The UI does not invent FX rates or inventory. Core/API support for explicitly supplied rates/inventory is documented separately.
- Play, pause, step, speed, seek by exact delivery index and reset operate the session's actual replay engine. Seeking disables stale action surfaces and clears old analytics. A new document has a new session; there is no shared localStorage session ID. Two tabs do not share a mutable cursor or selection.
- The center charts consume `/visualization`: historical resting bid/ask liquidity, candles, executed-trade footprints/profile and shared-core CVD/OFI. **Price aggregation** uses declared source quantum options, not invented ticks. **Time bucket** and **Chart range** select bounded time/price projections. Panning pins the source-monotonic window; **Follow time** resumes following. **Recenter** explicitly restores the price anchor and following window. Incoming frames do not reset pane sizes, contrast, mode or price anchor. Linked cursor uses exact server price rows and source-monotonic time, not exchange wall-clock interpolation.
- **Pause view** freezes chart and DOM presentation, not replay or recording. The tape remains independent. The separate **REPLAY Pause** changes the actual replay clock. Selection, seek/reset and source replacement invalidate obsolete frozen evidence. Explicit aggregation changes request a fresh projection/snapshot; unavailable history is labeled rather than fabricated.
- **Costs & proposed risk** contains the existing exact cost comparison plus a collapsible hypothetical entry/stop scenario. Submit exact strings for entry, stop, side and either base quantity or native-quote risk budget. Explicit assumed fees require basis points, provenance and date. `/proposal-risk` supplies loss, fees and supported lot-constrained size; the browser never derives size or risk. Unknown fees and insufficient metadata remain unavailable. Estimated loss is not guaranteed: gaps, slippage and unknown stop liquidity can cause greater losses.
- Presets (**Balanced**, **Analysis**, **DOM focus**), pane toggles and draggable separators control layout. Focus a separator and use arrow keys for keyboard resizing. Narrow layouts prioritize the chart, DOM/tape, research and exact watchlist as real stacked panes. **/** focuses search; **Space** pauses/resumes view; **R** recenters; **D** toggles independent DOM freeze; **1–6** select chart modes. Shortcuts ignore editable fields and focused controls.
- Browser-local preferences use bounded, validated schema `limit-orderbook.workspace.v1`: exact source/instrument preference, pane dimensions/collapse, preset, aggregation, range/follow/freeze, chart mode/contrast/overlays and DOM rows. Session IDs are never saved. Reload opens a new independent session. Stale source IDs may fall back only to an explicitly reported ready source in the same mode; unavailable live/replay never silently becomes demo. Invalid saved instruments use the selected source's declared default with a notice. Storage failures are visible and do not prevent use.

## Lifecycle, precision and bounds

The frontend consumes API DTO v1, with string economics, IDs and domain timestamps. `src/api.ts` names the consumer types. Exact values are shown without financial arithmetic; long values are visually abbreviated only and their full string remains in title/evidence disclosures. Number conversions are limited to plotting/measurement and bounded UI counts; time-window arithmetic uses BigInt. No formatted or plotted value feeds a financial input.

Each hook instance creates one HTTP session and one socket. Unmount/source replacement closes the socket and sends idempotent DELETE. Page exit uses keepalive deletion; late session-creation responses are explicitly released. WebSocket loss or five seconds without a valid frame immediately makes evidence unavailable and starts reconnecting to a **new** independent session, retaining selection/source but resetting replay cursor. The same capped exponential backoff (1/2/4/8/10 seconds, capped at 10) retries HTTP setup failures; successful receipt of valid stream evidence resets it. Runtime's nominal 200 ms publication cadence is not a hard scheduling guarantee; the five-second deadline is a conservative client liveness guard, including paused/ended/seeking replay. Visibility restoration also checks expired evidence. Manual Reconnect replaces the session. Replay/selection requests are abortable and owned by a session-scoped operation token: retiring a session releases its busy lock immediately, and old responses/finalizers cannot alter a new session. No fixture fallback appears on errors; backend HTTP status/detail text remains readable.

The browser retains one current presentation and one coalesced pending frame, not an ever-growing book/tape history. Frames over two million characters are rejected. Tape renders at most 100 prints, profile 200 levels, evidence 100 items and grouped ladder at most 100 requested rows. Visualization queries request 128 price rows and a visible-range-derived column budget capped at 600; incremental merging retains at most 600 columns within the explicit server window. Native collection and recording remain independent of UI sampling/coalescing. Chart and DOM requests are debounced, single-flight and scheduled after completion (one second / 750 milliseconds respectively), with cancellation, bounded deadlines and obsolete session/generation rejection. Failed requests label retained snapshots stale; disconnected evidence is not usable current evidence.

Historical ranges are half-open: `[start_ns, end_ns)`. Incremental merges discard
columns starting at the exclusive end, including when panning backward; an old
future column cannot remain the exact-data readout's selected sample.

Hidden documents suspend chart and ladder queries; a collapsed DOM pane suspends its ladder query. Restoring visibility/panes resumes fresh requests unless explicitly frozen. Replay/collection remains independent. In constrained laptop layouts the chart container scrolls vertically, keeping its quality disclosure and exact-data controls reachable.

A busy read worker can retain one explicitly labeled **last completed presentation** instead of blanking tape/cursor on every historical query. Its presentation health is diagnostic/unready until ownership is available; it is never a new market validation. Seek, source failure and generation changes clear access to old evidence. Replay without a currently available cursor is labeled replay-unavailable, never live.

After a committed visible-document render, two animation-frame callbacks provide a paint opportunity before the frame ACK is sent. Receipt timing starts at the beginning of the WebSocket message callback, before JSON parsing/validation, and includes decoding/coalescing/render work. The server receives `frame_id` and browser receive-to-paint-opportunity duration. Hidden tabs do not report painted frames. This is **not** GPU completion or synchronized one-way latency. The footer labels server publication-to-ACK as a conservative round-trip upper bound; historical exchange/source clocks are not subtracted from browser clocks. The distinct ACK-expired counter reports bounded server ACK-buffer eviction, not presentation coalescing. Queue/coalescing/overflow and observer measurements come from Runtime. Source & health includes an expandable live-ingestion disclosure of native SubscriptionStatus records: actual processing stages/clock, processed frames, queue counts/bytes/highwaters, lifecycle and reasons; demo/replay truthfully show an empty list. Production performance budgets require actual measured evidence.

Chart timing is separate from frame timing. Visualization HTTP receipt is recorded before body decoding. The Canvas callback reports CPU draw duration and two animation-frame paint opportunities for a specific `view_id`; only an actually painted visible view is acknowledged once via `/visualization/ack`. Chart receive→paint and publication→ACK upper bound have their own readout. Neither frame ACK nor HTTP arrival alone proves chart paint. Source clocks are never subtracted from browser clocks; these durations do not claim GPU completion.

## Dependencies and licensing

`frontend/package.json` pins direct versions; `package-lock.json` locks transitives. Node 22.22.0/npm 10.9.4 are the native toolchain. Runtime uses React/React DOM **19.2.4** ([MIT license](https://github.com/facebook/react/blob/v19.2.4/LICENSE)); their installed licenses and Scheduler's were read. Redistributed runtime notices live in `frontend/public/THIRD_PARTY_NOTICES.txt` and are copied into production assets.

Build/tool dependencies: Vite **7.3.1** ([MIT](https://github.com/vitejs/vite/blob/v7.3.1/LICENSE)), TypeScript **5.9.3** ([Apache-2.0](https://github.com/microsoft/TypeScript/blob/v5.9.3/LICENSE.txt)), ESLint/@eslint/js **9.39.2** (MIT), typescript-eslint **8.54.0** (MIT), eslint-plugin-react-hooks **7.0.1** (MIT), @types/react **19.2.10** and @types/react-dom **19.2.3** (MIT), and Playwright Test **1.58.1** ([Apache-2.0](https://github.com/microsoft/playwright/blob/v1.58.1/LICENSE)). Development packages retain their own license/notice files in locked npm distributions. npm reports the selected ESLint 9 release as deprecated; it remains an explicit lock, not a silent update. No project-wide license was inferred: the private frontend package is marked `UNLICENSED` rather than granting rights absent from the repository.

M0's [ADR-004](ARCHITECTURE.md#adr-004--native-canvas2d-heatmap-measured-before-expanding-dependencies) selected independently implemented native Canvas2D. **No chart package is adopted.** No external fonts, icons, UI source, screenshots or chart examples are copied. Accessible HTML controls, legends, evidence and summaries accompany the Canvas. M4 performance and screenshot acceptance must be measured by the integration owner; implementation alone is not a latency or parity certification.

## Verification commands and interaction selectors

The following M3 evidence is historical, not a claim that the M4 changes have passed gates: M3 integration passed frontend lint, three exact-format unit tests, TypeScript and production Vite build; real-server Chromium passed **8 tests in 33.4 s** after correcting a disabled-option matcher. The M4 workspace owner does not run gates mid-flight. The integration owner must run the union below after backend and renderer integration.

M4 adds `frontend/e2e/workspace.spec.ts` actual-server workflows for grouped depth, aggregation/range, independent freeze, persisted layout/new sessions, streaming pane geometry, risk availability and exact costs. `frontend/src/workspace.test.ts` covers preference trust boundaries and bounded incremental history/seek replacement. These additions are unrun by the workspace owner; the parent integration gates remain required.

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

Stable accessible selectors:

| Workflow | Selector |
| --- | --- |
| Source/search | `getByLabel('Data source')`, `getByLabel('Search instruments')` |
| Exact cohort filters | `getByLabel('Filter venue')`, `getByLabel('Filter quote')`, `getByLabel('Filter market')` |
| Exact instrument | `[data-instrument-id="<native ID>"]`; selected has `aria-pressed="true"` |
| Metrics/ladder | `getByLabel('Depth band')`, `getByLabel('Ladder rows')`, role tab Metrics/Profile/Evidence |
| Size/side | `getByLabel('Order side')`, `getByLabel('Size convention')`, `getByLabel('Order size', {exact:true})` |
| Fees/stress | `getByLabel('Fee assumption')`, `getByLabel('Fee basis points')`, `getByLabel('Fee date')`, `getByLabel('Fee provenance')`, `getByLabel('Latency stress basis points')`, `getByLabel('Rebalance cost')` |
| Estimate | `getByTestId('estimate')` |
| Replay | `getByTestId('replay-play'/'replay-pause'/'replay-step'/'replay-reset')`, `getByLabel('Replay speed')`, `getByLabel('Seek delivery')`, button `Seek` |
| Recovery/errors | `getByLabel('Reconnect terminal')`, `getByRole('alert')` |
| Chart controls | `getByLabel('Price aggregation')`, `getByLabel('Time bucket')`, `getByLabel('Chart range')`, tablist `Chart view` |
| View/DOM freeze | `getByTestId('view-freeze')`, `getByTestId('view-recenter')`, `getByTestId('dom-freeze')` |
| Layout | `getByLabel('Workspace preset')`, separators `Watchlist width`, `DOM width`, `Research pane height` |
| Chart timing | `getByTestId('chart-telemetry')` (distinct from frame footer timing) |

The terminal is read-only public-market analysis. It has no exchange submission, credentials, wallet/signing, alerts, paper ledger or journal in this milestone. Cost estimates do not establish execution readiness, profitable signals, account access or guaranteed fills.
