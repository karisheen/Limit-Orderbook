# Market-workspace improvement cycle

The initial cycle below is historical. The current chart-first desktop and
hourly-history follow-up is recorded under [Desktop price-action redesign](#desktop-price-action-redesign).

## Evidence and scope

**O — observed:** actual public interfaces inspected in Chromium at **1440×1000**
and **390×844**. **D — documented:** official product documentation or local
implementation. **[INFERENCE] — decision:** adaptation rationale, not a verified
product capability. Research and local verification were performed on 2026-11-07.
No wallet was connected, real order submitted or access restriction bypassed.
Linked public pages identify the inspected products, not immutable captures or
backend timing evidence.

The project is a local public-market observation/replay workstation: Python
adapters and exact Decimal L2 books, HTTP/WebSocket synchronization, a React
dual-Canvas workspace, and a thin Streamlit client. It has analytics, alerts,
journal/research and durable funded **spot IOC paper execution**. It is not an
exchange matching engine: no L3 order queue, passive fills or live execution.
The [operator guide](TERMINAL.md) documents supported controls.
The [M8 handoff](OMP_UPGRADE_HANDOFF.md#m8--market-first-workstation) is historical;
this cycle's [evidence](evidence/workspace-cycle/manifest.json) is separate.

Scope is a bounded presentation and native book-construction change to a local
public-market/replay and **paper IOC** workstation. There are no new financial
calculations, wallets, exchange orders, leverage, private execution, live-feed
integrations or widget framework. References inform organization, not feature
parity, copied code/assets or exchange infrastructure guarantees.

## Primary references

### TradeXYZ

- **O:** The actual [TradeXYZ frontend](https://app.trade.xyz/), linked from
  [trade.xyz](https://trade.xyz/), used chart-left/book-middle/ticket-right with
  account/history below. `ANT-USDC` identity included an icon, `xyz` deployment
  badge, leverage and a plain-language description. `/` opened market discovery
  with market/asset-class filters, Names/Tickers and price/change/volume/OI/funding;
  `⌘/` opened shortcut settings.
- **O:** Dense dark columns and Long/Short, Market/Limit/Pro, units, TIF and
  estimated costs made entry context explicit. Public chart/book rendered despite
  “Trade.xyz is not available from this connection.” Mainnet, WebSockets and
  Wallet Disconnected were separate labels. Narrow Chart/Order Book/Trades tabs
  replaced simultaneous panes.
- **D:** [Relationship to Hyperliquid](https://docs.trade.xyz/overview/relationship-to-hyperliquid)
  distinguishes XYZ deployments/interface from HyperCore matching, positions and
  settlement; the same markets can appear in other frontends.
- **Limits:** Disconnected account data was unavailable. Exact precision rules,
  depth completeness/rescale stability, stale transitions, complete keyboard
  accessibility and execution receipts were not verified. No response-latency
  measurement or settled chart-performance claim follows from rendering.

### Hyperdash

- **O:** [Hyperdash](https://hyperdash.com/) showed a dense dark grid with
  Starter/News, Widget/Order controls, chart, narrow book, right ticket and bottom
  analytics. Top Traders exposed address, notional, entry, liquidation, PNL,
  funding and account value. `⌘K` opened Assets/Predictions/Traders discovery with
  categories, metrics and explicit keyboard hints.
- **O:** Initial chart/book skeletons and blank narrow content later gave way to
  populated book/analytics and a narrow asset summary, Chart/Order Book/Trades,
  stacked trader cards and Markets/Trade navigation. Connecting/Connected was
  separately visible. The initial loading screenshot is not settled performance
  evidence.
- **D:** The [official overview](https://docs.hyperdash.com/about-hyperdash) and
  [Layouts](https://docs.hyperdash.com/layouts.md) describe rearranging, resizing,
  tab-stacking and saved layouts. These manipulations were not exercised. Its
  documented five-item mobile navigation differed from the observed two-item
  disconnected view.
- **Limits:** Numerical precision/unit consistency, exact alignment, depth
  scope/rescaling, execution feedback, stale/error transitions, full keyboard
  coverage and response timings were not verified. Density alone proves no
  operational efficiency.

### JTX

- **O:** The actual [JTX frontend](https://app.jtx.com/), from
  [jtx.com](https://jtx.com/), used a restrained light chart/book/ticket composition
  with thin separators. Asset-or-mint search opened named trending assets with
  price/change. Identity included token icon/symbol, shortened mint, price,
  liquidity and market cap.
- **O:** Pay with / Receive, presets, Market/Smart Fill/Limit/Pro, estimated
  impact/slippage and fee/network/route summary supplied input/outcome context.
  Balances/Open Orders/TWAP/Trade History/Transfers had an explicit disconnected
  empty state; Data and Exchange indicators were separate. Narrow identity was
  compact with Chart/Order Book/Trades, Buy/Sell and Crypto/Equities/Portfolio.
- **D:** [What is JTX](https://jtx.com/learn/what-is-jtx) describes a self-custodial
  interface, not an exchange; transactions use Solana/third-party protocols and
  some may use Jito Block Engine/BAM. These are not local-app capabilities.
- **Limits:** Intro/privacy overlays partly obscured content; intro was dismissed
  without login. Narrow chart was still loading. Authenticated portfolio,
  receipts, exact precision/depth integrity, rescale stability, keyboard coverage,
  stale transitions and response timings were not verified.

## Supplementary references and exclusion

### Pacifica — retained supplementary

**O:** The [public BTC terminal](https://app.pacifica.fi/trade/BTC) rendered a live
book/chart with regional restriction banners and an unavailable ticket;
“Operational” did not establish user eligibility. Chart loading/remount left the
book visible. Expanded rail search filtered ETH; selection was not exercised.
Sample book prices used InterApp **11px/16px, weight 400**, header price
**12px/16px**, numeric variant `normal`; totals were right-aligned and size
centered. Tab from search reached Oracle with a visible cyan **2px box-shadow**
focus ring. At 390px there was no document horizontal overflow, but chart above
side-by-side book/ticket and an automatic announcement obscured substantial
content. Disconnected positions said “No results” while balances showed zero:
this is not evidence of an empty feed. **D:**
[IOC semantics](https://docs.pacifica.fi/trading-on-pacifica/order-types/classic/limit.md)
are documented, not exercised fills. **[INFERENCE]:** retain units, visible focus
and unavailable reasons; reject obstructive announcements and wallet/leverage
machinery.

### Jupiter — retained supplementary

**O:** The [Perps terminal](https://jup.ag/perps/long/SOL-SOL) had searchable named
market buttons, favorites and JLP/Beta badges. Enter on “Select ETH market”
changed header and URL; `/` opened/focused Global Search. Escape subsequently
dismissed it, but the immediate post-key state still showed the modal, so exact
focus-restoration timing is unverified. Selected mark used **ui-monospace 14px**,
high/low **Inter 14px**, both numeric variant `normal`; `$2,231.49` in the list
versus `$2231.49` selected is a concrete inconsistency, not a standard to copy.
Header data existed during the chart spinner. Unavailable estimates used dashes;
positions said “Connect your wallet to see your trades.” At 390px Chart/Trade
actually replaced the main panel, retained amount/slippage/estimate details in
Trade and had no document horizontal overflow. Programmatic focus lacked
outline/shadow; that does not establish keyboard-focus failure. **D:**
[Partial-fill/unhealthy-market behavior](https://docs.jup.ag/user-docs/trade/perps/faq.md)
was not triggered. **[INFERENCE]:** retain keyboard market selection, mode badges
and selective narrow visibility, not guaranteed fills.

### Drift — excluded as a functioning layout baseline

**O:** The [legacy terminal](https://app.drift.trade/BTC-PERP) reported “Drift is
being paused until further notice due to irregular activity in the protocol,”
with skeletons/empty book, a restricted-territory Unavailable ticket and Connecting
progressing to RPC Connection Error. The footer still said Online. Pause/error
persisted at 390px without document overflow. No RPC change, selection or keyboard
investigation followed. **[INFERENCE]:** this is a negative example of conflating
network status with feed readiness, not a functioning market-layout reference.

Across references, stale-feed transitions, economic correctness, subscribed
versus complete depth, long-run stability and order execution were not tested.
Search/tab interactions establish observable behavior, **not latency**. Spinners
and skeletons preclude blanket “instant” or “fast” claims. Supplementary direct
observations above supersede the researchers' earlier access limitations; the
regional, disconnected and loading limitations still apply.

## Selected adaptations and observable criteria

The direction is one restrained, information-dense workspace: native identity
and search first; independent Liquidity/Flow charts beside Book/Tape on desktop;
one keyboard-selectable market pane on narrow screens; research and explicit
paper execution below. Existing exact values, units and data-quality semantics
remain authoritative. No decorative animation, new widget framework or copied
branding. [Working controls](TERMINAL.md#working-controls) describes operation.

| Adaptation | Rationale and bounded implementation | Tradeoff and observable criterion |
| --- | --- | --- |
| Identity/search before configuration | Existing identity competed with configuration and search accepted only a single substring. TradeXYZ/JTX identity and Hyperdash/Jupiter discovery inform an **18px native identity**, case-insensitive all-term search across native fields and a **Source / workspace** disclosure. Search already existed in the topbar; it was not moved out of Watchlist. | Disclosure adds a step. Existing bounded catalog paging/memoization remains. Keyboard search, editable-control protection, Escape/focus return and source-owned selections are covered; matched narrow chrome falls from 334.94 to 316.94px. |
| Desktop dual-chart hierarchy retained | The existing Liquidity/Flow pair and DOM/tape carry distinct native evidence; replacing them with a competitor's single chart/ticket would lose context. TradeXYZ/JTX's restrained hierarchy informs compact headers and secondary configuration, not a new ticket/layout engine. | Both charts and DOM/tape remain visible at 1440px. Independent settings, resize/maximize/restore and exact identity/bounds remain covered; no claim that greater density improves every draw or ACK tail. |
| Narrow state-preserving market tabs | The old four-pane stack put Book below the fold. TradeXYZ/Hyperdash/JTX tabs and Jupiter's selective narrow panel inform **Liquidity / Flow / Book / Tape** at **≤965 CSS px**, with Left/Right wrapping and Home/End. Panes remain mounted; hidden chart/ladder queries are gated. | Only one market pane is visible; comparison is deliberately not simultaneous content parity. At 390px the document falls from 1895 to 922px and the active-Liquidity workload issues 17 visualization/0 ladder requests versus 30/24. Keyboard and state retention are covered at 320/390px. Source/generation/clock changes still invalidate obsolete evidence; no claim of zero hidden component work. |
| Precise values and distinct states | Existing source, transport, replay, freshness and quality evidence must not collapse into one health light. JTX's Data/Exchange separation, Pacifica's unavailable reasons and Drift's contradictory status support retaining exact strings, units, held-cap bars and independent freezes. | Compact summaries keep missing values unavailable, not zero; transport does not establish eligibility. Tests cover source selection, loading/empty/error/disconnected/stale states, source-clock age and independent freezes. Recorded held/disconnected/empty captures supplement tests; live-feed network correctness was not measured. |

### Paper entry and execution-feedback boundary

**D — existing local capability:** [Spot IOC paper workflow](TERMINAL.md#spot-ioc-paper-workflow)
requires exact base quantity, explicit funds, dated/sourced fees and feed/decision/
order latency. Native arrival, depth, constraints and quality govern fills;
partial remainder cancels. Paused replay must advance source time for arrival.
Orders, balances, positions, fills, fees, clocks and consumed levels remain
inspectable; there is no passive-fill, derivative, borrowing or market-impact
model. Mutations are not automatically retried.

**[INFERENCE]:** JTX's input/output/fee context and TradeXYZ's unit/TIF labels
support keeping existing paper inputs and native outcomes reachable and clearly
labeled **paper**, with requested/filled/cancelled quantities and native reasons
intact. This is not a new ticket or execution mode. Competitor receipts were not
observed. The local paper checks below exercise the existing native workflow only.

## Backend decision and measurement limits

**D — implemented path:** sparse delta sides use incremental sorted candidates
only when distinct touched prices per side are **≤min(64, n // 64)** for prior
side size `n`. Binary search locates changed prices in the committed tuple;
unchanged runs are copied and replacements come from the final candidate map,
preserving latest lexical Decimal values. Snapshots and dense changes retain full
sorting. A payload with `len(changes)` greater than the **sum of both side budgets**
conservatively disables sparse work, even if duplicates would reduce its distinct
count. Touched sets are collected outside the per-level validation loop, avoiding
dense per-level bookkeeping. No validation is skipped.

Ordering, lexical values, immutable old books, atomic rollback, checksums and
protocol hooks remain native contracts. Binance's observed-depth boundary is
unchanged: retained/subscribed depth does not become a complete book. Changed-side
map copying and tuple construction still scale with depth; this is not constant
time and adds no economic or live capability.

**Selection and rejected designs:** the original instrumented profile attributed
**22.865 of 25.727 seconds (about 89%)** of `apply` cumulative time to `_levels`
([profile](evidence/workspace-cycle/profile-before-summary.json)). This is
instrumentation/hotspot evidence, not latency. The initial `n // 8` threshold
regressed dense updates; a cap of 64 still regressed 128-level sides with 16
changes. The final threshold plus moving dense bookkeeping out of the validation
loop removed the systematic large dense penalty in the repeated crossover
comparison. Tiny-book overhead and dense variance remain; this is not a universal
win.

### Final native measurements

Only `verified-core-{before,after}-{a,b,c}.json`,
`verified-crossover-{a,b,c}.json` and
[verified recorded replay](evidence/workspace-cycle/verified-recorded-replay-after.json)
are final native evidence. Intermediate `core-*-final-*`, `core-summary` and
discarded crossover files are not used below. The
[final evidence summary](evidence/workspace-cycle/summary.json) indexes
provenance and aggregation; raw results remain authoritative.

Three alternating before/after runs used **20,000 levels/side**, 300 deltas/load,
100 messages/s and 200 messages/s. Each has the same deterministic dataset SHA256
`a4a7292d6c1c71550c9a50f087150fc5cad3841eb6259ce2dbb66e2b713261a3`:
75 replacements, 75 deletions, 75 reinsertions and 75 repeated-price messages,
375 level changes, no trades. Below are **median of three run quantiles
[minimum–maximum]**, milliseconds; not pooled percentiles or confidence intervals.

| Load / variant | p50 | p95 | p99 |
| --- | ---: | ---: | ---: |
| 100/s before | 5.106 [4.886–5.222] | 8.557 [7.264–8.799] | 10.296 [8.399–11.877] |
| 100/s after | 2.107 [2.098–2.200] | 3.487 [3.225–3.603] | 4.272 [3.870–5.060] |
| 200/s before | 4.242 [3.937–4.374] | 7.428 [6.517–8.214] | 10.785 [9.122–17.472] |
| 200/s after | 1.912 [1.890–1.956] | 3.223 [3.055–3.993] | 4.556 [4.326–9.159] |

| Load / variant | Snapshot ms | Process CPU seconds | Lifetime peak RSS MiB |
| --- | ---: | ---: | ---: |
| 100/s before | 815.622 [760.856–1522.055] | 2.270 [2.154–2.377] | 71.410 [71.227–71.824] |
| 100/s after | 765.569 [743.768–788.958] | 1.359 [1.332–1.361] | 71.398 [70.969–71.586] |
| 200/s before | 720.259 [674.164–780.640] | 1.858 [1.825–1.914] | 71.410 [71.227–71.824] |
| 200/s after | 672.784 [611.542–678.536] | 1.214 [1.160–1.224] | 71.398 [70.969–71.586] |

All loads completed 300 messages with zero reducer recoveries/drops and retained
20,000 levels per side. The baseline-derived goal was repeatably lower sparse
median/p95 latency; all three final runs improve both. The runner's existing
p95 ≤25ms/p99 ≤100ms ceilings also pass for both versions; they are not evidence
of improvement by themselves. Timers cover `BookReducer.apply`, not generation,
decoding, network, publication or rendering. CPU includes snapshots/pacing; RSS
includes preparation and both loads. Finite offered rates and synthetic schedule
lateness are not queue residence or sustainable-capacity evidence.

The crossover has **24 alternating samples per variant/case, three repeats,
17 cases**, with exact final **lexical price/quantity** parity throughout.
Its p50 is the sample median, p95 nearest rank. Representative median run p50s
(ms) show the intended boundary and remaining costs:

| Levels/side; changes | Before | After | Interpretation |
| --- | ---: | ---: | --- |
| 20; 1 | 0.083 | 0.084 | Paired median overhead 1.479µs; range 1.123–1.523µs |
| 128; 16 | 0.280 | 0.283 | Full-sort fallback; paired differences −61.523 to +3.101µs |
| 4096; 1 | 0.771 | 0.344 | Sparse |
| 4096; 64 | 1.555 | 1.267 | Sparse limit |
| 4096; 65 | 1.571 | 1.614 | Dense fallback; variable |
| 4096; 1024 | 15.753 | 16.743 | Dense median regression remains |
| 4096; 4096 | 68.585 | 68.136 | Full-side replacement; variable |
| 20,000; 1 | 3.588 | 1.230 | Sparse |
| 20,000; 64 | 4.400 | 2.266 | Sparse limit |
| 20,000; 2048 | 40.143 | 38.590 | Dense fallback |
| 20,000; 2500 | 45.570 | 46.513 | Dense variance, not a win claim |

For the last case, run p95 median rises **56.578 → 83.941ms**, with ranges
**52.932–85.305 → 53.849–88.709ms**. For 4096/1024, p50 paired differences span
**−2.545 to +1.539ms**. Threshold selection bounds incremental work; it does not
promise faster dense execution under every host load.

### Recorded replay and paper boundaries

The local recording has **4,276 deliveries / 4,252 raw records**, 54 snapshots,
2,317 deltas, 51,718 level changes and 2,068 trades; its receive span is 95.012s.
Retained native sides range from 20 to roughly 5,000 levels. Final normalized
and raw replay each complete all deliveries and match the original partial
benchmark's exact book, state and clock hashes:

- Book: `ddc8dd2be8805eb50757e93bb39ba623ef7e4443b9ffe675ea3bd38368c8a08c`
- State: `7301e06734738dbda0d028b38f79fac81352018888332d3f1dd4a448b7faae13`
- Clock: `53c6da2be137de538b1b55d9b7b1b1aab5c0a0e1bfa2f7cf534f91b7e64dcba8`

| Replay step inclusive | Before p50 / p95 / p99 ms | Final p50 / p95 / p99 ms | Before → final wall seconds |
| --- | ---: | ---: | ---: |
| Normalized | 3.573 / 16.424 / 43.982 | 1.268 / 4.097 / 7.346 | 41.014 → 12.966 |
| Raw | 3.724 / 10.584 / 18.021 | 2.594 / 6.415 / 10.315 | 28.892 → 19.573 |

The baseline was a **contended single run**; these are descriptive timings,
not a causal whole-application speedup. Step samples include the final end-of-input
call (4,277). Final normalized/raw CPU is 12.082/18.602s and lifetime peak RSS
186,966,016/224,038,912 bytes; normalized/raw step maxima remain 764.131/1286.335ms.
The original writer benchmark reached its **600s process deadline** after
completing **1× writer, raw/normalized, regenerated-checkpoint, final-state and
checkpoint-seek parity**. It did not complete 2× or saturation. This cycle does
not repair collector overflow, demonstrate sustained writer capacity or measure
public-feed network latency.

[Paper before](evidence/workspace-cycle/paper-before.json) and
[after](evidence/workspace-cycle/paper-after.json) each contain 30 isolated,
funded synthetic IOC submissions backed by temporary SQLite: quantity 4 fills 3,
cancels 1, and repeat submission consumes zero additional depth. p50/p95/p99 are
**10.349/212.236/558.752ms before**, **4.353/6.088/6.588ms after**. Host load
differed; no paper optimization or causal latency improvement is claimed.
The actual UI exercise explicitly used **1,000 fake USDT**, **10bps fees** and
**zero latency**: **0.01 ETH** filled, notional **30.01**, fee **0.03001**, remaining
quote balance **969.95999**. These are paper funds, not real money or live orders.

## Matched UI evidence and outcomes

Twelve retained `browser-{before,after}-{1440,390}-{a,b,c}.json` reports compare
saved original/current frontend builds against the **same backend**, three
20-second windows per variant/width, synthetic replay at **2×**, starting at
delivery **2000**, viewport height **900**, DPR **1**. The backend already used
the `n // 64` threshold but preceded the final dense-bookkeeping move: loaded
`book.py` SHA256
`d435981fb1d1c71a9506b37a58d2eaf522aaf93ddd9fff0585d549bd943a2dcd`.
Final native code is measured separately above. These browser comparisons isolate
presentation more closely than a whole-stack before/after, but are finite local
observations, not exchange traffic or evidence of backend-to-UI causality.

At 390px, chrome before the market area drops **334.94 → 316.94px** and document
height **1895 → 922px**, with scroll width **390px**. The final 316.94px comprises
116px topbar + 102.94px source banner + 57px replay controls + 41px tabs.
An intermediate **368.94px** chrome attempt failed the reduced-chrome criterion
and was superseded, not counted as final acceptance. Narrow measurement selects
Liquidity: requests fall from **30 visualization + 24 ladder** per run to
**17 visualization + 0 ladder**. At 1440px both charts and DOM/tape remain
visible: visualization requests are 30 before and 28–30 after, ladder 24–25
before and 23–24 after. Native replay advances **1478–1512 deliveries before**
and **1512 after** across both widths.

The following uses the same median-of-run-quantiles [range] convention, ms:

| Metric | 1440 before | 1440 after | 390 before | 390 after |
| --- | ---: | ---: | ---: | ---: |
| Canvas submission p95 | 3.200 [2.000–3.400] | 2.600 [2.600–2.900] | 2.600 [2.400–3.900] | 3.300 [3.300–4.400] |
| Frame publish→ACK upper bound p95 | 150.442 [139.515–182.870] | 192.732 [152.490–222.021] | 186.681 [157.814–192.294] | 82.407 [77.992–84.893] |
| Frame receive→paint p95 | 49.300 [49.000–50.400] | 49.300 [49.300–50.500] | 48.600 [48.000–50.400] | 49.200 [48.900–50.000] |
| Click issue→focus + 2 rAF p95 | 119.743 [112.711–127.530] | 121.466 [109.150–124.304] | 119.133 [94.755–137.979] | 106.652 [103.988–119.603] |

Median rAF p95/p99 stay near **16.7/16.8ms**; one after-desktop run has p99
**33.3ms**. Narrow draw p95 increases with the larger active canvas; desktop ACK
and interaction tails also do not universally improve. Canvas submission is not
GPU completion, two-rAF presentation is not source freshness, and server ACK is
a round-trip upper bound, not one-way network time. Twenty real automated
focus interactions per run include Playwright/actionability overhead. Native frame
and visualization cohorts remain separate; missing queue-wait observations are
unavailable, not zero.

All measured visualization/ladder requests complete with HTTP 200 and zero
failed/aborted/unresolved requests in these twelve windows; measurement-window
error entries total zero. Outside the window, logs retain **51 HTTP 409 entries,
51 corresponding console 409 entries and five aborted-request entries**, marked
`during_measurement: false`. These are startup/generation/ACK or cleanup
observations, not counted as measured-window failures; the logs do not distinguish
every warmup from teardown event. This is not a blanket error-free startup claim.

### Captures and interaction coverage

Recorded source is session `4498db3b-f4a2-4fff-a32e-a97221fa21ee`, manifest SHA256
`d12553c14ee31de1e66db3d2e9247000b81a976826f4f3da8ae072d4622ee5f5`.
Recorded comparison captures use delivery **2057**, desktop **1440×900** or
narrow **390×844**, reported DPR **1.25**, full-page captures. Actual PNG sizes
and scrollbar caveats are in the manifest. The after-narrow capture deliberately
selects **Book**, versus the old stack: these are not identical-pane pixel crops.
Held/disconnected captures do not establish a matched delivery fence.

- Recorded desktop: [before](evidence/workspace-cycle/before-recorded-desktop-1440.png), [after](evidence/workspace-cycle/after-recorded-desktop-1440.png).
- Recorded narrow: [before stack](evidence/workspace-cycle/before-recorded-narrow-390.png), [after Book](evidence/workspace-cycle/after-recorded-narrow-390-book.png).
- Actual state exercises: [held Book](evidence/workspace-cycle/after-held-book.png), [server-kill/disconnected](evidence/workspace-cycle/after-disconnected.png), [empty search](evidence/workspace-cycle/after-empty-search.png).
- Matched synthetic 1440: [before](evidence/workspace-cycle/before-synthetic-1440.png), [after](evidence/workspace-cycle/after-synthetic-1440.png).
- Matched synthetic 390: [before](evidence/workspace-cycle/before-synthetic-390.png), [after](evidence/workspace-cycle/after-synthetic-390.png).

The final integrated browser batch passed **50 cases**, with **one intentional network skip**.
Coverage includes 320/390px arrows/Home/End/Escape, hidden-query gating, independent
freeze, DOM manual follow, chart settings/cursor mode, tape controls and desktop
restoration; source selection and empty/error/loading/disconnected/stale states
remain explicit. The actual recorded freeze and server-kill screenshots supplement
mocked lifecycle cases. Hover inspection is **not sticky**: pointer leave clears
the tooltip, preserving existing behavior. Pinned tape inspection is separate.

## Environment, commands and reproduction

Native evidence: Python **3.13.5**, macOS **13.7.8 x86_64**, MacBookPro14,1,
Intel i5-7360U 2.30GHz, four logical CPUs, 8GiB RAM. Browser evidence: Node
**22.22.0**, Darwin **22.6.0**, repository-pinned Playwright **1.58.1**, Chromium
**145.0.7632.6**.
Use **`.venv-upgrade/bin/python`**; this workstation's `.venv` lacks `uvicorn`.
Use **`.tools/node/bin`** on PATH. Runs share a host, not isolated hardware;
baseline replay/paper in particular experienced different host load.

The original reducer is loaded read-only from revision
`49f2d0a4bb07a4d0816c0644890756d740b3cc7d`, verified SHA256
`f908201f41719b1d7726af4a38df0a3a166660c7312d21c4e9428e63053e4a74`.
The historical wrapper's `core_source_sha256` field hashes the current checkout,
**not** its in-memory historical module; the wrapper's verified hash is
authoritative. Final native `book.py` SHA256 is
`467c852a33e480733083ed7554d557a0a8ad64c6f3d9ccc945103f566434dc63`.

Portable measurement commands (repository root; choose fresh outputs, do not
overwrite retained evidence). These call the same archived workloads as the
executed comparisons; repeat three times, alternating before/after order:

```sh
export PATH="$PWD/.tools/node/bin:$PATH"
OUT="$(mktemp -d /tmp/lob-workspace-repro.XXXXXX)"
.venv-upgrade/bin/python docs/evidence/workspace-cycle/core-before.py \
  --depth 20000 --events 300 --normal-rate 100 --burst-multiplier 2 \
  --output "$OUT/core-before.json"
.venv-upgrade/bin/python tools/benchmark_core.py \
  --depth 20000 --events 300 --normal-rate 100 --burst-multiplier 2 \
  --output "$OUT/core-after.json"
.venv-upgrade/bin/python docs/evidence/workspace-cycle/crossover.py > "$OUT/crossover.json"
.venv-upgrade/bin/python docs/evidence/workspace-cycle/replay-workload.py > "$OUT/replay.json"
.venv-upgrade/bin/python docs/evidence/workspace-cycle/paper-workload.py > "$OUT/paper.json"
```

Replay requires the operator's local recording at the documented path; capture
payloads and saved frontend build bytes are not redistributed. The original
partial writer command was:

```sh
.venv-upgrade/bin/python tools/benchmark_recording.py \
  --source data/m6-reference/4498db3b-f4a2-4fff-a32e-a97221fa21ee \
  --output-root /tmp/lob-recording-before --max-load-seconds 180 \
  --preload-byte-limit 134217728
```

For reproduction use a **new nonexistent output root**; the original process had
a separate 600-second deadline. Do not interpret the tool's 180-second per-load
bound as a whole-command deadline or claim the uncompleted loads passed.

To rebuild the original frontend without changing the checkout, archive only
its frontend and reuse the unchanged installed dependencies. This is a recipe,
not a claim that rebuild bytes match the saved original assets on another machine:

```sh
ROOT="$PWD"
BASE="$(mktemp -d /tmp/lob-original-ui.XXXXXX)"
git archive 49f2d0a4bb07a4d0816c0644890756d740b3cc7d frontend | tar -x -C "$BASE"
ln -s "$ROOT/frontend/node_modules" "$BASE/frontend/node_modules"
(cd "$BASE/frontend" && npm run build)
npm --prefix frontend run build
```

Run the following servers in separate terminals, with the same exported paths
or absolute equivalents; stop them after comparison:

```sh
.venv-upgrade/bin/python docs/evidence/workspace-cycle/serve-comparison.py \
  --port 8871 --dist "$BASE/frontend/dist"
.venv-upgrade/bin/python docs/evidence/workspace-cycle/serve-comparison.py \
  --port 8872 --dist frontend/dist
```

The helper binds **127.0.0.1**, uses temporary state, has no registry/live source
and never enables a collector. Optional
`--capture data/m6-reference/4498db3b-f4a2-4fff-a32e-a97221fa21ee`
exposes read-only recorded replay, not live funds. Browser workloads below are
synthetic-only, sequential, 20 seconds/2×; use fresh names for each repetition and
repeat with `--width 390`. Executed comparisons used ports **8014/8015** and
the retained `browser-...json` outputs; each JSON retains its exact argv.

```sh
node docs/evidence/workspace-cycle/browser-workload.mjs \
  --url http://127.0.0.1:8871/ --output "$OUT/browser-before-1440.json" \
  --width 1440 --seconds 20 --speed 2
node docs/evidence/workspace-cycle/browser-workload.mjs \
  --url http://127.0.0.1:8872/ --output "$OUT/browser-after-1440.json" \
  --width 1440 --seconds 20 --speed 2
```

### Verification status

Executed commands and outcomes, rather than an assumed all-green gate:

1. `./tools/check.sh`: **1,270 Python tests passed in 709.73s**, ruff passed,
   71 files format-checked; mypy then failed on a missing annotation. The
   annotation was fixed; this invocation was **not** an overall gate pass.
2. `./tools/check.sh tests/test_workstation_core.py tests/test_workstation_asian.py`:
   **191 passed in 13.01s**, ruff/71-file format check passed, mypy **35 files**
   passed, frontend eslint, **98 Node tests**, TypeScript and Vite build passed.
3. After the final dense-bookkeeping change:
   `.venv-upgrade/bin/python -m ruff format src/workstation/book.py && .venv-upgrade/bin/python -m pytest tests/test_workstation_core.py tests/test_workstation_asian.py -q && .venv-upgrade/bin/python -m mypy src/workstation`:
   **191 passed in 10.79s**, mypy **35 files** passed.
4. From `frontend`, using the absolute repository `.tools/node/bin/node`:
   `node_modules/@playwright/test/cli.js test --config /tmp/lob-playwright.config.mjs workspace.spec.ts pane-accessibility.spec.ts source-experience.spec.ts terminal.spec.ts`:
   **46 passed, one intentional network skip, two new test-setup failures**.
   After fixing setup, `workspace.spec.ts --grep 'narrow pane selection'`
   passed those **two in 25.8s**. The separate
   `workspace.spec.ts workflow.spec.ts --grep 'narrow pane selection|paper|alert|journal|research'`
   invocation passed the additional workflow case (**13.7s**). These yield
   **49 distinct passing cases**, not 49 plus every rerun.

The temporary config used the existing `frontend/playwright.config.ts`
conventions with a reused loopback API. The retained
[portable config](evidence/workspace-cycle/playwright.config.mjs) reproduces
one worker, no retries, 45s test/15s expect timeouts and 1440×900 Chromium, but
creates a **unique temporary output directory**, avoiding the user-owned root
`test-results`. It accepts only a loopback HTTP origin. Start the current
comparison server above, then from the repository root:

```sh
LOB_E2E_BASE_URL=http://127.0.0.1:8872/ \
  .tools/node/bin/node frontend/node_modules/@playwright/test/cli.js test \
  --config docs/evidence/workspace-cycle/playwright.config.mjs \
  workspace.spec.ts pane-accessibility.spec.ts source-experience.spec.ts \
  terminal.spec.ts workflow.spec.ts
./tools/check.sh
```

Do not run benchmarks concurrently with these gates. The installed Chromium
binary and native/frontend dependencies are prerequisites; no browser download
or dependency replacement is implied by this recipe.

**Final integration:** the five-spec browser batch completed **50 passed,
one intentional network skip** in **6.3 minutes**. It ran against the restarted
server with the final reducer, then `../tools/check.sh` completed **1,270 Python
tests in 408.44s**, ruff and 71-file format checks, mypy across 35 files,
frontend eslint, **98 Node tests**, TypeScript and the production Vite build.
Combined elapsed time was **811.50s**. The existing Starlette TestClient
deprecation warning remains; no dependency replacement was made.

The retained portable Playwright config was then exercised with
`workspace.spec.ts --grep 'narrow pane selection'`: **2 passed in 22.6s**,
including explicit DOM manual-follow retention at 320/390px. Frontend lint
passed again; all five Python reproducers passed ruff/format checks, and both
JavaScript reproducers/configuration passed Node syntax checks. These focused
reruns are not additional distinct cases.

[Verification outcomes](evidence/workspace-cycle/verification.json) and the
[complete final gate log](evidence/workspace-cycle/verification.log) retain the
commands, counts, warning and elapsed time. Temporary investigation scripts,
comparison builds/state and owned application servers were removed or stopped;
the documented reproducers and measured evidence remain.

## Desktop price-action redesign

### Problem and bounded implementation

The earlier refinement still presented two short-horizon order-flow charts.
Their 1/5/10-second buckets and six-second secondary follow window were useful
for microstructure, not for reading hours or days of price action. Merely slowing
rendering would not create historical candles and would conceal incoming data.

The new default is a large **Price action** chart with **1h** candles, exact OHLC,
base volume, native UTC history, pan/zoom, Fit/Latest, keyboard inspection and
grouped timeframe selection. A forming bar updates in place. The existing
liquidity/footprint workspace remains under **Order flow**, and the right book/tape
keeps its own update and freeze controls. A stronger native-market header and
persistent bottom workflow dock make the composition materially different.
The React frontend, Python core and supported Streamlit client remain; no language
rewrite, chart dependency, wallet, execution surface or new venue was added.

| Reference and observed pattern | Local problem and adaptation | Tradeoff and acceptance |
| --- | --- | --- |
| [TradeXYZ](https://app.trade.xyz/): supplied screenshot's grouped minute/hour menu, selected 1h, candles/volume beside book | Replace event-scale price reading with native time buckets; quick 1m/5m/15m/1h/4h/1d plus grouped longer choices | Only actual native intervals are enabled. A non-quick choice remains visible. Keyboard selection must request that exact interval without changing replay speed or book ownership. |
| [Hyperdash](https://hyperdash.com/): supplied screenshot's dominant price chart, compact timeframe strip and narrow book | Give historical price action the full left workspace; put the original dual charts behind Order flow rather than shrinking all three | One primary desktop view at a time; hidden queries stop. Hour bars, volume and readable axes must remain usable at 1440×900, with native book/tape alongside. No widget framework, account tools or copied branding. |
| [JTX](https://app.jtx.com/): previously inspected thin separators and explicit market identity | Native symbol, venue, market type, quote unit, observed trade and native bid/ask replace ambiguous context; existing tools occupy a bottom dock | Show only existing native evidence, not invented funding/OI/leverage. Narrow Price/Liquidity/Flow/Book/Tape navigation and visible focus must remain usable. |

These are frontend references, not interchangeable protocols. TradeXYZ's markets
can appear in other Hyperliquid frontends; its supplied screenshot is not evidence
about Hyperliquid execution speed. Hyperdash is a terminal reference; JTX is a
Solana-facing interface reference. This follow-up uses the supplied images and
the public-interface observations above, not a new authenticated product review.

### Candle and ownership contracts

- `/candles` is separate from `/visualization`, book reconstruction and replay
  playback. The browser asks five seconds after each completed request; it does
  not request one new candle per book frame. Source/session/generation/instrument/
  interval fences reject superseded responses.
- Existing live venues use their own credential-free public OHLCV endpoints,
  one recent page (UI target 300, API maximum 500), a bounded five-second cache
  and four-query native concurrency cap. No historical pagination or venue fallback.
- Demo/replay uses only retained admitted trades. Hourly bars can be partial and
  sparse; observation coverage is not expanded to the bucket boundaries. Native
  receive-clock projection handles replay clocks with no wall timestamp, rejected
  clock pairs, offset changes and rewind.
- Native strings remain exact; display projection is relative BigInt arithmetic.
  Calendar months retain 28/29/30/31-day boundaries. Missing buckets are not filled.
- Failed refreshes retain valid same-owner history with a stale label. Receipt
  timestamps are not market-freshness or latency claims. Replaced sessions clear
  obsolete history. Preference writes retain their drag debounce and flush on
  page hide, fixing an observed immediate-reload loss of the selected timeframe.

### Exercised native history

Read-only `/api/v1/candles` requests with `interval: "1h", limit: 300` returned
300 bars each for these actual live catalog identities:

| Venue | Native instrument |
| --- | --- |
| Binance Global | `binance-global:spot-v3:spot:BTCUSDT` |
| Coinbase Advanced Trade | `coinbase:advanced-trade:spot:BTC-USD` |
| Hyperliquid | `hyperliquid:info:perpetual:BTC` |
| Kraken | `kraken:spot-v2:spot:BTC/USD` |
| KuCoin | `kucoin:classic-spot:spot:BTC-USDT` |

Every returned hourly start/end was checked for UTC alignment and a
3,600,000,000,000 ns duration. Binance browser controls also returned 300 native
1m, 4h, 1d and 1h bars, and 112 calendar-month bars with all four genuine month
lengths. The 2h grouped menu was exercised by keyboard. Binance.US and Bybit were
not present in this active catalog; their parsers/requests have deterministic
coverage, not a claim of live access from this connection.

### Evidence and measurement limits

The earlier benchmark results in this document remain the previous cycle's
results. This follow-up does **not** claim a backend or rendering speedup from
switching to a different chart workload. Its measurable change is semantic:
1h native historical buckets instead of a seconds-scale retained order-flow view,
with independently advancing book evidence.

Real desktop inspection exposed a clipped chart footer during integration.
Compact chart controls and flexible plot sizing fixed it: at 1440×900 the plot
measured 1072×469 CSS px, with the complete footer inside the chart and no
horizontal overflow. The 390 px and 320 px layouts were inspected; the grouped
menu fit inside 320 px, and keyboard Book → Home returned to Price.

Actual loading was captured by delaying a real candle request, empty history by
seeking the real demo to delivery 1, and recovery by seeking to delivery 5000.
An injected HTTP 503 preserved all five exact OHLC/volume values, showed
**Stale snapshot**, and recovered after the fault was removed. These are explicit
transport faults around real data, not replacement financial fixtures.

Concurrent backend/browser checks reproduced the existing
`analytical_queue_overflow` capacity boundary. The interface marked the source
unavailable and retained candles stale instead of claiming fresh evidence.
Restarting the local collector preserved its durable state directory; reconnect
alone is not a capacity repair. Sustained collector performance is not solved
by this desktop redesign.

Reproduction: build with `npm --prefix frontend run build`, launch the documented
read-only server, open `/?source=live`, select BTCUSDT and compare 1m/1h/4h/1d and
More → 1M. Use the existing browser suite against an isolated offline server for
deterministic seek, delayed-response, refresh-failure, ownership and keyboard
scenarios. Do not run a second live collector beside the workstation.

### Final screenshots and verification

[Before desktop](evidence/desktop-redesign/before-live-1568.png) ·
[After desktop](evidence/desktop-redesign/after-live-1568.png) ·
[1440 px desktop](evidence/desktop-redesign/after-live-1440.png) ·
[Timeframe menu](evidence/desktop-redesign/after-menu-1568.png) ·
[390 px layout](evidence/desktop-redesign/after-live-390.png) ·
[320 px menu](evidence/desktop-redesign/after-menu-320.png).
Desktop before/after use the same native market but different live moments, not
a controlled price/performance comparison. At 1568×1000 the final plot measured
1184×569 CSS px. Flat-price axes retain distinct labels rather than repeating
rounded integers. See the [manifest](evidence/desktop-redesign/manifest.json) for
source/viewport provenance, explicit fault-injection captures and verification
qualifications.

Two 20-second observations of this feature illustrate independent cadence and
host-load sensitivity, **not a software before/after speedup**:

| Observation | Concurrent checks/build | Final, no parallel checks |
| --- | ---: | ---: |
| Received market frames | 43 | 76 |
| Distinct received book revisions | 28 | 74 |
| Completed candle requests | 3 | 3 |
| Completed ladder requests | 14 | 23 |
| Hidden order-flow visualization requests | 0 | 0 |
| Candle HTTP duration range, ms (3 samples) | 380.2–2303.1 | 817.8–904.7 |
| Browser long tasks (>50 ms) | 13 | 3 |
| Longest task, ms | 347 | 61 |

The contended observation ended unavailable/stale; the final observation began
and ended **Ready · partial depth**, with **1h / ~5s refresh** still selected.
Three requests are insufficient for a latency distribution. These HTTP durations
include local server/provider work and are not feed or execution latency.
Raw results: [contended](evidence/desktop-redesign/cadence-under-load.json),
[final](evidence/desktop-redesign/cadence-final.json). Reproduce by waiting for
native history and ready book evidence, then observing 20 seconds without
interaction: count CDP `Network.webSocketFrameReceived` frames/distinct
`frame.book.revision`, clear/size the Resource Timing buffer before counting
`/candles`, `/ladder` and `/visualization` completions, and collect browser
`longtask` entries. Activity and host load naturally vary.

Executed checks (Node 22.22.0, Python 3.13.5):

- `npm --prefix frontend run lint`, `npm --prefix frontend test`:
  **113 unit tests passed**; `npm --prefix frontend run build`: TypeScript and
  production Vite build passed.
- `.venv-upgrade/bin/python -m ruff check src/workstation` and the affected tests;
  `.venv-upgrade/bin/python -m mypy src/workstation`: passed, 36 source files.
- `.venv-upgrade/bin/python -m pytest -q`: **1300 passed, two new test-helper
  failures**. Their runtime candle assertions passed; the rejected-field test
  accidentally supplied `endpoint` twice to its Python helper. It now uses a
  direct HTTP request, preserving the 422 check.
  `pytest -q tests/test_workstation_visual_api.py -k candle`: **4 passed**,
  including both corrected cases. The dedicated candle tests also passed
  (**28 cases**). Existing Starlette/httpx deprecation warning remains.
- The full offline Playwright suite: **63 passed, one 1920 px timeout, one
  live-only skip**. The trace completed its user assertions before teardown.
  The same 1920 px scenario passed alone in **25.9 s**, without changing its
  workload or 45 s deadline. No clean single full-suite pass is claimed.
- Final `price-action.spec.ts`: **4 passed** after the last chart-axis change.
  The actual `--network` startup/search/DOM/Hyperliquid-switch scenario also
  passed (**25.3 s**), covering the offline suite's live-only skip.

Browser commands used the repository's Playwright CLI/config conventions:
one worker, no retries, 45 s test/15 s expectation deadlines. The isolated
offline run used loopback port 8015 and temporary output/config paths; the
live-only run used `frontend/playwright.config.ts` on port 8000.
For reproduction, run the full suite against an offline server, then the
`network launch migrates` case against the explicitly network-enabled server.
The four new price-action cases explicitly select demo and can use either server.

Temporary probe code/config was removed; retained images, raw measurements and
the manifest are evidence, not application fixtures. The updated read-only
workstation is served locally with its existing durable state directory.
