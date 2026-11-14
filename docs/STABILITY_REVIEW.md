# Terminal stabilization review

Status: **implemented and locally verified within the finite scope below**.
This record separates observed incidents, measured work and source-audit risks.
It does not certify every provider, a 24-hour soak, profitable research, HFT
latency or exchange execution. Publishing runs the same locked offline gate;
remote CI is a separate release check, not an inference from these local results.

Operator path: [QUICKSTART](QUICKSTART.md). Detailed contracts:
[TERMINAL](TERMINAL.md), [ARCHITECTURE](ARCHITECTURE.md),
[recording](RECORDING.md) and [financial evidence](CONTRACTS.md).
The existing [chart-redesign record](CHART_REDESIGN_PLAN.md) remains separate;
its screenshots/results are not fresh stabilization verification.

Committed [offline measurement receipt](evidence/terminal-stability-offline.json)
retains exact counts, CPU totals, source/input fingerprints and parity hashes
without publishing raw market captures.

## Evidence and scope

1. **Observed failure:** user screenshot/native session reported
   `analytical_queue_overflow`, **256** queue highwater and **34 owned ingestion
   streams**. Failed-session observations included **1,079.9 ms queue wait**,
   **66.0 ms last observer batch**, **94.6 ms receive→paint** and **97.4 ms
   publication→ACK**. The observer service batch holds **up to 32 deliveries**;
   66 ms is not per-event cost. These readings do not describe healthy sustained
   throughput, nor do ACKs prove exchange-to-screen latency.
2. **Measured finite workload:** identical recorded-prefix profiling below
   isolated costly retention/admission traversal and observer snapshot work.
   This is stronger evidence for those costs than hardware speculation, but it
   does not reproduce live scheduling or the 34-stream incident.
3. **Static audits:** scheduling, rendering and financial/lifecycle reviews
   identified concrete source paths and missing regressions. Their possible
   failure consequences are **inferences**, not proof that the user's account,
   capture or browser experienced every identified failure.
4. **Environment:** MacBookPro14,1; **2 physical / 4 logical cores**, **8 GiB**,
   **macOS 13.7.8**. One `memory_pressure -Q` snapshot reported **55% free** and a
   backend sample about **287 MiB physical footprint**. Neither establishes no
   pressure under every workload, a process RSS ceiling or hardware-only cause.

Preserved boundaries: existing chart redesign; exact native values and source
identity; complete required analytical event order; original queue/depth bounds;
active paper/capture ownership; provider access restrictions. No event dropping,
queue inflation, reduced reconstruction depth, provider bypass or invented
accounting is accepted as a performance fix.

## Ranked defects and chosen repairs

Priority describes consequence, not observed frequency. Actual verification and
qualification limits appear below.

| Priority / evidence | Problem | Chosen repair and consumer guarantee |
| --- | --- | --- |
| P0 / static safety audit | Overflow, reselection backlog or relevant native gaps could leave paper able to advance across missing causal observations. | Owner-thread gap boundary preserves the last complete financial prefix and durable reason/provenance. Global analytical loss freezes activated accounting; scoped book/source loss targets pending orders/open perpetuals. Unrelated trade-only gaps do not indiscriminately freeze native-book accounting. Recovered books never thaw the old run; explicit new configuration starts a separate run. |
| P1 / observed overflow + profiles | Required analytical queue exhausted; repeated retention traversal, identifier validation and full-window execution matching consumed service capacity. | Shared identity accounting, atomic accounting reset after a joined complete drain, native-regex text validation and receive-time range indexing remove redundant work. Exact native depth, analytical order and **256 deliveries / 128 MiB** limits remain. Finite live results below qualify only the tested workload. |
| P1 / observed live stage delays | Sub-millisecond bounded book work waited tens of milliseconds for worker dispatch/return; analytical consumption added another task/shield round trip per batch. | Inline only byte- and native-state-bounded small books; deep/unbounded/buffered/custom reducers remain isolated. Direct context-preserving executor futures and a persistent analytical drain remove redundant scheduling. Cooperative stop still joins detached ownership and freezes failed financial prefixes. No input, depth or queue bound changes. |
| P1 / overdue-loop stack sampling | WebSocket connections synchronously loaded default certificate stores on the event loop; large discovery responses also parsed there. | Coalesced off-loop verified WebSocket trust initialization/reuse, including secure proxies, plus off-loop exact metadata parsing/assembly. Certificate, hostname, environment trust and family failure semantics remain enforced. |
| P1 / static ownership audit | Repeated explicit/AnyIO cancellation could release batch/lock/capture ownership before a worker finished. | Shared `join_owned` shields and joins owned work before release while preserving cancellation and worker failure. Capture stop/finalization remains owned until completion; other clients and recorder leases remain independent. |
| P1 / static publication audit | A terminal status transition during a frame build could publish actionable stale eligibility. | Recheck terminal publication eligibility after work; terminal frame cannot reuse an otherwise ready book/analytics cache. |
| P1 / static replay audit | A later observer failure during step/seek/selection could expose a partially applied prefix. | Explicit failed-operation fence prevents publication/continuation; explicit seek/reset rebuilds all consumers. No atomic rollback or hidden retry is claimed. |
| P1 / static browser lifecycle audit | A stalled mutation response could indefinitely hold busy/deferred-generation presentation despite healthy WebSocket traffic. | **15-second** owned selection/replay/paper-resume response deadline releases busy/deferred presentation through the existing settlement path; report ambiguous server outcome. No automatic repeat or session replacement to conceal it. |
| P1 / static rendering audit | Cursor/readout could describe new props while the canvas still showed an older view; closed Inspector still built large tables/options/JSON. | Successfully painted view/mode/crop owns pixel-linked evidence; invalidate on identity/clear/failure. Lazily mount expensive Inspector bodies, preserving disclosure preferences and memoizing data-only indexes. |
| P2 / static scheduling audit + frontend baseline | Retention rollover replayed expensive study statistics; unchanged pages rebuilt profile/books and repeatedly published. | Cache/invalidate statistics by evidence, batch necessary reconstruction/publication and retain unchanged derived identities. Preserve frontier closure, late trades, retention floors, stream retirement, quality and settings changes; do not skip required events. |
| P2 / static catalog audit | Startup downloaded full catalogs twice; volatile source status replaced metadata and needlessly restarted the cursor drain. | `/sources` directory plus selected `/instruments?...&include_sources=false`; independent URL/projection validators and freshness. Stable semantic metadata identity owns drain lifetime; genuine scope/version/availability changes still fence it. Memoize catalog/cohort work separately from telemetry frames. |
| P2 / static canvas audit | One throwing draw could prevent sibling panels painting; static theme/DPR or offscreen updates were inconsistent. | Per-panel failure isolation with visible failure; event-driven theme/DPR invalidation; atomic bitmap/transform changes; presentation suspension/re-entry without suspending collection. |
| P2 / static input/layout audit | Foreign contacts could move gestures; zero/horizontal wheel input and CVD/OFI price gestures mutated unsupported geometry. Exact legends could change chart height under the cursor. | Single-pointer ownership, supported-axis wheel normalization, consistent scalar-mode navigation restrictions, stable scrollable exact readout, and visible narrow-grid direction/keyboard controls. |
| P2 / observed clock mismatch + formatting audit | Grid tape/study clocks used local time while ticker/native views used UTC; very small positive quantities could display as zero. | Consistent UTC axes/tape labels, tested across DST and three host timezones; compact scientific notation preserves the sign/nonzero meaning of small quantities. Exact native inspectors remain authoritative. |

The audited code had a **three-second catalog refresh**, not a three-second
catalog-abort loop. In the served repaired browser, DevTools reported
`ERR_ABORTED` for conditional catalog requests that actually returned **304** with
matching validators; the application retained its valid catalog. Those messages
are not evidence of failed catalog refreshes. Equivalent instrument replacement
separately cancelled `/market-data`; stable metadata ownership addresses that
distinct defect.

The later unprofiled 180-second browser window still failed before the scheduling
repair: analytical highwater 256 / one overflow, plus Kraken raw-queue loss.
Unique latest-processing observations sampled from WebSocket telemetry isolated
the dispatch problem; these are **not all-input-frame latency percentiles**:

| Bounded book source | Unique observations | Queue p95 ms | Dispatch p95 ms | Decode + validation p95 ms | Return + normalization p95 ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| Kraken, 25 levels | 533 | 5901.023 | 21.025 | 0.918 | 72.487 |
| Bybit, 50 levels | 1370 | 1506.438 | 17.791 | 1.569 | 68.115 |

Return includes normalization as well as executor/loop handoff; it is not a pure
thread-wait measurement. The native small-book correction requires ≤4096 input
bytes, an exact bounded/truncating base reducer, effective depth ≤50, no buffered
protocol transactions and current sides ≤50. Larger/deeper inputs are still
accepted through the worker, not truncated or rejected by this scheduling rule.

Subsequent overdue-loop sampling found 106 samples in default certificate loading
through `asyncio.sslproto` / the native WebSocket connector, with up to **1.123 s**
timer lateness and 80 queued callbacks. Metadata parsing showed up to **1.427 s**
lateness. Measured access-log stalls reached 15 ms and did not explain the main
failure; logging was not disabled and certificate verification was not relaxed.
Real loopback TLS regressions exercise trusted/untrusted roots, hostname rejection
and SNI. HTTP and WebSocket trust policies remain intentionally distinct.

After these corrections, the next 34-stream run had no reported native raw-queue
loss but still exhausted the analytical queue during a burst. Native worker
service was short compared with dispatch/completion waits. Busy analytical turns
now drain up to **128 already-admitted deliveries**, without waiting to fill;
bootstrap remains at 32. The total queue remains **256 / 128 MiB**. This explicitly
trades a longer busy owner turn for amortized scheduling and faster backlog drain;
it is not an increased buffer or analytical coalescing. Final live proof follows.

## Recorded backend profile: first measured patch

Artifacts read: `/tmp/orderbook-observer-before.json` and
`/tmp/orderbook-observer-after.json` (local measurements, not committed raw market
captures). Capture ID `4498db3b-f4a2-4fff-a32e-a97221fa21ee`, manifest SHA-256
`d12553c14ee31de1e66db3d2e9247000b81a976826f4f3da8ae072d4622ee5f5`.
The capture contains 4,276 batches / 4,523 events across 12 streams, complete with
zero reported source discontinuities. Both profiles service the same first
**2,000 deliveries / 2,161 events**, exact-primary Binance BTC spot, in **63**
service batches. No recorded stream or full post-reduction book is reduced.

| Stage, measured thread CPU | Before | First patch |
| --- | ---: | ---: |
| Enqueue/admission | 8.236 s | 2.024 s |
| Observer service | 3.581 s | 3.241 s |
| Release | 1.794 s | **3.621 s** |
| Sum of these three measured stages | 13.612 s | 8.885 s |

The patch used Counter retention plus selective flow snapshots. Enqueue improved
substantially, but **release CPU regressed by about 1.827 s (roughly doubled)**.
The measured component sum is lower by about 35%, **not a 4× total speedup**.
These are stage CPU totals, not end-to-end wall time or live producer capacity.
The first-patch record is retained separately; subsequent identical-workload
measurements follow below rather than relabeling this result.

Identical before/after parity values:

| Evidence | SHA-256 / exact hash |
| --- | --- |
| Driver checkpoint | `8a832ca905b4811f39f63c301e42db46294723b1b583b58bef997e8cbe203ddf` |
| Native books | `f9849111e259a5a694bc9b2b53e221919360640cb70d6bc3ec67438336d2b76b` |
| Native history | `2039ae761127b67d048bb8a3f2a531745dd237aa1d273487a7627e098604f857` |
| Native/replay clock | `a4bb4f050822107a484238957b4e0c0395d21b4325bce04de095eb7742ee69cb` |
| Admitted market trades | `14fcdd5a9aa7dc0a7732a4c132ad6578d7424439a9ea2495e48d2f491aef1251` |
| Alerts | `89b5cd627c0bb0695f5b21b9ffcc6f5c39feb88c6eb27202a0c4139505cc8102` |
| Analytics | `ad7aecc03555a8f25fee85e5d5178f798013ca342cf05751cf090d86f245fd02` |
| Reference context | `f6b84e2e02a8c87b50705079fbcef68269e4f5a9ecb7995bd4d24a5f073d96d7` |
| Paper checkpoint | `844d0bcbf4f8fb2dcda91b81665d3229bf29ef3d64a4650e7a866c3540d077a0` |
| Positioning history | `e17f04ca51c50d37ae698ca8df682f84a70da71f359d56861cae0883a0d6849e` |
| Visualization with analytics | `e65df28fccb8edfc049105e62ddf8ba19aeaa301fc3c389dd507a55e06ea2377` |
| Replay books | `8299b18dd2fbccf029269f5245cf28a08e414675d05bbc49f665cf90a2f9a954` |
| Replay projection | `ca86855bd7bf239be54acdc3706ec865b1f3126380052e0be431dcf9541abb86` |
| Replay state | `39ba03816046ed7dda31d553f9a87c7dab2265411a2e1fb9c2fa3bc6371bae4b` |

**Profile limitations:** cProfile/timing adds overhead. Thread CPU excludes other
threads; wall measurements include preemption. Replay decoding/reduction, setup
and checkpoint hashing are outside stage profiles. There is no network producer,
executor handoff, browser or concurrent live arrival. Harness queue waits are not
the reported live waits. Observers use actual empty initial paper/rule
configuration; hash parity here is not active-account gap verification. Matching
hashes establish this prefix's measured outputs, not all-input equivalence.

The subsequent release-binding measurement (`/tmp/orderbook-observer-final.json`)
retained every parity hash above: enqueue **1.951 s**, observer **3.141 s**,
release **2.669 s** thread CPU, sum **7.762 s**. This is still an instrumented
offline prefix, not a live-capacity result.

After the drained-budget reset and further exact hot-path corrections,
`/tmp/orderbook-observer-latest.json` measured enqueue **1.942 s**, observer
**2.307 s**, release **0.086 s** thread CPU for the same prefix, with every
checkpoint/hash unchanged. Total of these stages: **4.335 s**, versus **13.612 s**
initially. This excludes decoding, setup and output hashing.

A separate mature fixture (3,000 same-price trades, 300 book transitions,
20 snapshots) measured **1.619 s → 0.223 s** snapshot thread CPU. Identical output:
446,227 bytes, SHA-256
`a1d2347c7ec50efb50cdaf92797c28a7c37cd077e3665ef73245e289941a4f63`.
Range indexing preserves `(start, end]`, duplicate-time delivery order, exact
Decimal summation and evidence references; it does not approximate quantities.
The identifier microprobe measured 40,000 calls at **0.407 s → 0.058 s** for its
explicit mix of native IDs and 512-character strings. That mix is not live traffic.
The regex preserves the old rule exactly: reject U+0000–U+001F, empty/non-string
or over-512-character values; do not silently impose a different Unicode policy.

**First repaired live validation failed:** one browser on the target Mac, full
**34-stream** BTC cohort, reached queue highwater **256** and **one overflow**
within seconds. Its final observer batch was **27.0 ms**, receive→paint
**40.1 ms**, publication→ACK upper bound **46.0 ms**. No queue/depth reductions
were made. This explicitly disproved treating the offline speedup as a complete
repair; it prompted further producer/owner profiling and corrections.

## Recorded frontend comparison

Production-bridge workload: **12,000 trades**, **80 unchanged pages**,
**10 retirements of 30 trades**, and **100 book levels**. The before/after
serialized evidence is identical: **2,898,957 bytes**, SHA-256
`9e0635ccdcf1db35b0157285415e79c87d976c328e18fdeb6bd9022bc3fec1d2`.

| Stage | Before | After |
| --- | ---: | ---: |
| Unchanged-page store publications | 160 | 0 |
| Unchanged-page profile replacements | 80 | 0 |
| Unchanged-page book replacements | 80 | 0 |
| Retirement study applications | 118,350 | 118,350 |
| Retirement publications | 421 | 40 |
| Settings-change publications | 199 | 6 |

Sources: `/tmp/orderbook-display-before.json` and
`/tmp/orderbook-display-after.json`. All required retained-prefix applications
remain. Timings overlapped other verification work and are not used to claim
isolated latency or live responsiveness.

## Final local verification

- `./tools/check.sh`: **2,209 Python tests passed**, **279 frontend tests passed**,
  Ruff lint/format passed (98 files), mypy passed (45 source files), ESLint,
  TypeScript and the production build passed.
- Complete actual-backend browser run: **107 passed, 3 failed, 3 live opt-ins
  skipped**. The failures were corrected: the remaining pointer assertion moved
  across actual bars rather than empty chart space, and two book-coverage cases
  migrated from removed workspace radios to the current layout preset. All
  **3 targeted reruns passed**, preserving exact evidence/ownership assertions.
  Earlier browser corrections also exposed and fixed keyboard candle inspection
  being moved by synthetic SDK resize events.
- Later UTC/small-quantity formatting and recording-settlement changes passed
  their regressions and final frontend gate. Real Coinbase recording recovery
  exercised the latter through the actual UI and native backend.
- Security checks include real loopback WebSocket handshakes with environment
  trust, untrusted certificates, hostname rejection and SNI; no external provider
  access was substituted for these deterministic checks.
- Ownership, observation-gap freezing, archived-prefix preservation, repeated
  cancellation, capture finalization, replay failure fences, 1024-frame bounded
  ingress bursts, complete-prefix parity and async metadata discovery are inside
  the passing native gates.

## Finite live acceptance

[Committed receipt](evidence/terminal-stability-live.json). One backend and one
browser on the target Mac, unchanged 34-stream scope and native reconstruction
depth. No tests/builds ran during the measured intervals.

| Four-minute frame-sampled observation | Result |
| --- | ---: |
| Live WebSocket frames | 773 / 773 |
| Analytical overflows | 0 |
| Analytical queue highwater | 228 / 256 |
| Largest native raw-queue highwater | 22 |
| Browser receive→paint p95 | 47.6 ms |
| Publish→ACK upper-bound p95 | 140.9 ms |
| Analytical queue-wait p95 | 333.3 ms |
| Analytical queue-wait maximum | 788.3 ms |

All final native capture-completeness flags were true. These are sampled
presentation/queue observations, not all-event or one-way exchange-latency SLOs.
Earlier failed attempts remain failed evidence above.

Additional actual workflows:

- Explicit isolated **1000 USDT simulated allocation**, assumed 10-bp fee and
  zero modeled latency; native 0.001 BTC market IOC filled with exact recorded
  book references. No real account or exchange order was involved.
- **60 seconds** with active paper plus native selected-instrument recording:
  all 197 sampled frames live, no analytical overflow or reported native loss.
- Binance recording completed and registered; real, non-synthetic replay stepped
  to delivery 7 and sought exactly to 500 of 4077 deliveries. Finalization
  remained live with zero analytical overflows; highwater reached **248**.
- Coinbase BTC/USDT selection and recording completed. A deliberately held stop
  response exceeded 30 seconds; authoritative listing resolved the timeout,
  ownership released, and only one stop request was sent. Highwater 83, zero
  analytical overflows and no reported native loss.

Actual [focused live price chart](evidence/terminal-stability/live-price-1440-focused.png):
workflow drawer collapsed, complete price/volume/time axes visible. The captured
`owner_busy; last_completed_presentation` notice remains visible: it denotes a
held native snapshot, not missing history or proof of current financial eligibility.

## Operating limits

- Highwater 248 leaves limited burst margin on two cores. Avoid concurrent
  builds/research loads and unnecessary client tabs; do not inflate queues or
  discard observations to conceal overload.
- Provider HTTP 451/403 and unsupported metadata/history remain explicit. No
  bypass, all-provider availability or exchange completeness claim.
- No 24-hour soak, physical cross-monitor qualification, GPU-completion timing,
  profitable strategy or private execution certification was performed.
- Chromium DPR emulation can disagree with physical pixel boxes; the SDK-backed
  canvas test checks the browser's authoritative physical boxes.
- Existing Starlette/httpx test-client deprecation and Vite chunk-size warnings
  remain disclosed; dependency refresh/code splitting was not used to disguise
  measured ingestion failures.
