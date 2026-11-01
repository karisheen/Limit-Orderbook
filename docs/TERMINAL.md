# M3 thin terminal

The React/TypeScript terminal is a presentation client of the real versioned Runtime HTTP/WebSocket API. It does not collect feeds, construct books, calculate financial metrics, estimate fills, or inject fallback fixtures. Python analytics/cost engines supply the authoritative values. Streamlit remains available: this M3 slice is not the M4 historical heatmap/parity cutover.

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
- The bounded book ladder shows native price/quantity and depth scope. Rows controls presentation only; it never changes reconstruction depth. Tape shows normalized public prints, aggressor/unknown status, receive UTC, exchange-time tooltip and explicit subscription-history labels. History prints are not relabeled newly executed flow. Missing evidence never becomes zero.
- Proposed order controls send either base quantity or native quote budget, side, fee assumption/provenance/date, adverse latency bps and native-quote rebalance cost to `/costs`. Inputs are text, not JavaScript numbers. Unknown fees remain unknown. A result is an explicitly labeled snapshot, not a continuously current executable quote; press **Estimate & compare** again to refresh. Changed inputs are flagged until recalculation. Selection/seek/reset/session replacement clears old results.
- The pair table exposes same-feasible-base quantity, gross reference, depth cost, both fees, FX, rebalance, adverse latency, net/bps, exact inputs and exclusions. Default UI comparisons use native quote/market cohorts; USD, USDT and USDC are distinct. Unknown inventory, prefunding and non-atomic execution assumptions remain visible. The UI does not invent FX rates or inventory. Core/API support for explicitly supplied rates/inventory is documented separately.
- Play, pause, step, speed, seek by exact delivery index and reset operate the session's actual replay engine. Seeking disables stale action surfaces and clears old analytics. A new document has a new session; there is no shared localStorage session ID. Two tabs do not share a mutable cursor or selection.

## Lifecycle, precision and bounds

The frontend consumes API DTO v1, with plain native/model fields and string economics, IDs and domain timestamps. `src/api.ts` names the consumer types. Exact values are shown without numeric conversion; long values are visually abbreviated only and their full string remains in the title and evidence disclosure. The only timestamp numeric conversion is display-only `BigInt(ns) / 1_000_000n` to a bounded JavaScript Date. No formatted value feeds a financial input.

Each hook instance creates one HTTP session and one socket. Unmount/source replacement closes the socket and sends idempotent DELETE. Page exit uses keepalive deletion; late session-creation responses are explicitly released. WebSocket loss or five seconds without a valid frame immediately makes evidence unavailable and starts reconnecting to a **new** independent session, retaining selection/source but resetting replay cursor. The same capped exponential backoff (1/2/4/8/10 seconds, capped at 10) retries HTTP setup failures; successful receipt of valid stream evidence resets it. Runtime's nominal 200 ms publication cadence is not a hard scheduling guarantee; the five-second deadline is a conservative client liveness guard, including paused/ended/seeking replay. Visibility restoration also checks expired evidence. Manual Reconnect replaces the session. Replay/selection requests are abortable and owned by a session-scoped operation token: retiring a session releases its busy lock immediately, and old responses/finalizers cannot alter a new session. No fixture fallback appears on errors; backend HTTP status/detail text remains readable.

The browser retains one current presentation and one coalesced pending frame, not an ever-growing book/tape history. Frames over two million characters are rejected. Tape renders at most 100 prints, profile 200 levels, evidence 100 items and ladder 50 levels. Native source analytics/recording run independently of presentation coalescing. Generation and monotonic frame counters reject reordered HTTP/WS results, and stale cost responses are rejected. A disconnected book is not usable current evidence.

After a committed visible-document render, two animation-frame callbacks provide a paint opportunity before the frame ACK is sent. Receipt timing starts at the beginning of the WebSocket message callback, before JSON parsing/validation, and includes decoding/coalescing/render work. The server receives `frame_id` and browser receive-to-paint-opportunity duration. Hidden tabs do not report painted frames. This is **not** GPU completion or synchronized one-way latency. The footer labels server publication-to-ACK as a conservative round-trip upper bound; historical exchange/source clocks are not subtracted from browser clocks. The distinct ACK-expired counter reports bounded server ACK-buffer eviction, not presentation coalescing. Queue/coalescing/overflow and observer measurements come from Runtime. Source & health includes an expandable live-ingestion disclosure of native SubscriptionStatus records: actual processing stages/clock, processed frames, queue counts/bytes/highwaters, lifecycle and reasons; demo/replay truthfully show an empty list. Production performance budgets require actual measured evidence.

## Dependencies and licensing

`frontend/package.json` pins direct versions; `package-lock.json` locks transitives. Node 22.22.0/npm 10.9.4 are the native toolchain. Runtime uses React/React DOM **19.2.4** ([MIT license](https://github.com/facebook/react/blob/v19.2.4/LICENSE)); their installed licenses and Scheduler's were read. Redistributed runtime notices live in `frontend/public/THIRD_PARTY_NOTICES.txt` and are copied into production assets.

Build/tool dependencies: Vite **7.3.1** ([MIT](https://github.com/vitejs/vite/blob/v7.3.1/LICENSE)), TypeScript **5.9.3** ([Apache-2.0](https://github.com/microsoft/TypeScript/blob/v5.9.3/LICENSE.txt)), ESLint/@eslint/js **9.39.2** (MIT), typescript-eslint **8.54.0** (MIT), eslint-plugin-react-hooks **7.0.1** (MIT), @types/react **19.2.10** and @types/react-dom **19.2.3** (MIT), and Playwright Test **1.58.1** ([Apache-2.0](https://github.com/microsoft/playwright/blob/v1.58.1/LICENSE)). Development packages retain their own license/notice files in locked npm distributions. npm reports the selected ESLint 9 release as deprecated; it remains an explicit lock, not a silent update. No project-wide license was inferred: the private frontend package is marked `UNLICENSED` rather than granting rights absent from the repository.

M0's [ADR-004](ARCHITECTURE.md#adr-004--native-canvas2d-heatmap-measured-before-expanding-dependencies) selected independently implemented native Canvas2D for the later heatmap. **No chart package is adopted here.** No external fonts, icons, UI source, screenshots or chart examples are copied. M3 uses accessible HTML tables and controls. M4 adds stable Canvas historical liquidity/trade overlays and richer panes; this page makes no heatmap, browser-performance or parity claim.

## Verification commands and interaction selectors

Integration passed frontend lint, all three exact-format unit tests, TypeScript and production Vite build. The real-server Chromium suite passed **8 tests in 33.4 s** after correcting its disabled-option matcher; the initial run retained **7 passes / 1 test failure** despite the native option carrying `disabled`. No production behavior was changed for that matcher correction.

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

The terminal is read-only public-market analysis. It has no exchange submission, credentials, wallet/signing, alerts, paper ledger or journal in this milestone. Cost estimates do not establish execution readiness, profitable signals, account access or guaranteed fills.
