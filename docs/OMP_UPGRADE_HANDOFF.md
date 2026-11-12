# OMP upgrade handoff

## Delivery ledger

| Milestone | Branch | Commit / push | PR / merge | State |
| --- | --- | --- | --- | --- |
| M0 | `upgrade/m0-contracts` | `a9f3259a22a358c4578f59f9b0d9762cad61e14f`, pushed successfully | [PR #1](https://github.com/karisheen/Limit-Orderbook/pull/1), merged `56b7c0dfbf9be5e146faffdf6afb77dc523257c0` | Complete; local gates attached to PR, no remote checks configured |
| M1 | `upgrade/m1-feed-integrity` | `65502311ff6e4e7f3ebdba41191e85c9f7104ee1`, `df8d22e900f0d62d94d0463871a597d41b7487e0`, final `1b25192d5c837ccda4b9a0cdf47b6ea6c04c50b7`; all pushed | [PR #2](https://github.com/karisheen/Limit-Orderbook/pull/2), both final offline checks green; merged normally as `d96c14d6d68a0a971a94bd08c947cc760a231eb7` | Feed milestone integrated; explicitly retained cross-milestone consumer acceptance |
| M2 | `upgrade/m2-record-replay`, from merged M1 | `e37a28946ebb79a2e0c74572c2083e6894ab1458`, final `b5b4030df5b4bd7dc5bfd371aea461acef09d0b5`; both pushed | [PR #3](https://github.com/karisheen/Limit-Orderbook/pull/3), both final offline checks green; merged normally as `380922168ab82da66fb2bb45f1a2cf6ca8810a71` | Recording/replay core and actual three-asset Binance-global/Hyperliquid integration verified. Immediate terminal startup and downstream analytics/alert/paper seek acceptance remain explicitly tracked in M3/M5 |
| M3 | `upgrade/m3-analytics-terminal`, from merged M2 | prerequisite `d18f9d5b889b0034466138f1cafdd695db57738d`; terminal `dbf20bd9b4a8c90696c0b6a40f70d0a62c489bb6`; final review correction `df5a64f089337eb9ff7332472c74cc004ab5db0e`; all pushed | [PR #4](https://github.com/karisheen/Limit-Orderbook/pull/4), both final-head offline/browser checks green; normal merge `eb7ff5e40cdf0f7ce1d0708f197c57922e0979eb` | Complete analytics/cost/runtime/thin terminal increment; sustained soak and later consumer acceptance remain explicitly M5/M6 |
| M4 | `upgrade/m4-liquidity-workspace`, from merged M3 | Backend `ea8f571e78bf5ec8b5427d16adb5203278aaa5f9`, workspace/legacy `eb0066da55caaca6144ac28e098eadc374235ad6`, final measured/review correction `3eccaac0b9a1b9f62c79b4076288e55f9509bbc3`; all pushed and remote head verified | [PR #5](https://github.com/karisheen/Limit-Orderbook/pull/5), both final-head offline/browser checks green; normal merge `95b9f8875bc23b8c8c5ee776489266ecfe7fadbe` | Integrated workspace with certified real1×/2× laptop rendering; actual alert/paper/journal tabs remain the explicitly linked M5 acceptance |
| M5 | `upgrade/m5-simulation-research`, from merged M4 | Prerequisites `54cabdf`, `12915c0`, `1aa63f2`, `31ba0d8`; feature `366e68ad6784a6ec54a17c9e5c3eeacae636e600`; final evidence `54131d6eb9f50e5dfd6f78e664542c7707d67bef`; all pushed and remote heads verified | [PR #6](https://github.com/karisheen/Limit-Orderbook/pull/6); both exact-final-head checks succeeded; normal merge `6588c44210140a2854398bbdf24b1977d420e06c` | Integrated:1,166 native tests,23 browser scenarios, finite production-capacity full-cohort workflows. Sustained and final readiness remain separately measured in M6 |
| M6 | `upgrade/m6-readiness`, from merged M5 | Configuration/operations `a2e57dd49f93c0ace60c374f5ebcac38a15a2f7f`; final evidence/disclosure `e3447f224e0eed64575a11387ad8d48b004d4ba7`; both pushed and exact remote heads verified | [PR #7](https://github.com/karisheen/Limit-Orderbook/pull/7), both exact-final-head offline/browser checks succeeded; normal merge `612309c905c311310e620563c764e97304465d98` at2026-11-01T19:56:20Z | Complete and integrated: full sustained replay, finite normal-load processing/rendering, recovery/setup and capacity evidence; failed saturation, startup/tail limits and unrun24-hour soak remain explicit |

## M0 verified evidence

- Starting checkout: clean `main`, `e7dceaf9492e4b5413ea3a972b66db7474b0232f`; origin `https://github.com/karisheen/Limit-Orderbook.git`; remote default `main`, unprotected. No applicable AGENTS.md found.
- Existing authenticated GitHub account: `karisheen`, repository ADMIN. Existing configured author `karisheen <dakariantoine@gmail.com>` matches the GitHub-associated author of the baseline commit. Email-list endpoint lacks user scope; no authentication/global identity settings changed.
- Baseline `.venv/bin/python -m pytest tests/ -q`: **35 passed**, one existing Python 3.9 LibreSSL/urllib3 warning.
- Modern local verification runtime: Python **3.13.5**, Node **22.22.0**, npm **10.9.4**. Ignored `.venv-upgrade` and `.tools/node`. M1 dependency synchronization also upgraded ignored `.venv` to Python 3.13.5; subsequent explicit `UV_PROJECT_ENVIRONMENT=.venv-upgrade` selects the intended verification environment. No language servers configured; CLI checks are the available diagnostic gate.
- `.venv-upgrade/bin/python -m pytest tests/ -q`: **35 passed, 24 strict xfailed**. These xfails capture unfixed baseline defects, not waived M1 acceptance. M1 must remove them as defects are corrected.
- `.venv-upgrade/bin/ruff format --isolated tests/test_upgrade_regressions.py tools/connector_spike.py`; `.venv-upgrade/bin/ruff check --isolated --select E4,E7,E9,F tests/test_upgrade_regressions.py tools/connector_spike.py`: passed after formatting. Broad inherited workstation lint policy was not used as this project's policy.
- `.venv-upgrade/bin/python tools/connector_spike.py --inspect-sources --output docs/evidence/m0-connector-spike.json`: all ten contract controls passed; published Kraken CRC **3310070434** matched; pinned library source/license inspection succeeded. Competing library runtimes were **not** installed/executed.
- Actual Chromium dense-chart runs at **1440×900** and **1280×800**: native Canvas2D and SVG rendered identical 28,800-cell synthetic grids. Raw samples in `docs/evidence/m0-chart-spike*.json`; screenshot `docs/evidence/m0-chart-1280.png`. These headless submission/layout/frame-opportunity timings are not production publish-to-render measurements. Canvas selected provisionally; production incremental tiles still require M4 measurement.
- Actual public access: Binance global market-data-only metadata and BTC/ETH/SOL depth/trades HTTP 200; BTC diff100ms and trade stream frames received. Coinbase Advanced Trade public BTC metadata and unauthenticated level2/trades/heartbeats received. Kraken metadata and BTC book/trade frames received. Hyperliquid metadata, spot metadata, contexts, mids, BTC/ETH/SOL/HYPE depth and trades HTTP 200; BTC l2Book/trades/activeAssetCtx frames received. See `docs/evidence/m0-access.json` and `m0-hyperliquid-access.json`. These are access probes, **not synchronized adapter validation**, nor permission to trade.

## M1 verified integration evidence

- Exact contracts and corrected legacy safety suite: `.venv-upgrade/bin/python -m pytest tests -q` **217 passed**, no xfails. `.venv-upgrade/bin/mypy src/workstation`: **9 source files clean**. Scoped Ruff passed after unused-import cleanup; parent formats the changed-file union.
- Actual Coinbase full snapshot initially exceeded a 4 MiB parser cap. Already observed payload contained 40,405 updates (~4.78 MB when reserialized). Shared market-data cap is now explicitly **32 MiB**, with bounded-input regression coverage. Direct live legacy adapter then accepted the initial snapshot and six subsequent updates; subscription acknowledgement did not produce a book.
- Browser smoke launched the real Streamlit application on loopback. Independent tabs held BTC and ETH without replacing each other's selections; Coinbase and Kraken live books rendered. Screenshots: `docs/evidence/m1-legacy-btc.png`, `m1-legacy-eth.png`. File watching was disabled for the stable smoke while agents edited source; an earlier reload/watcher run stalled and is not counted as successful stability evidence. New registry ownership/long-duration stability remain pending.
- Early full-depth reducer measurement: synthetic 20,000 levels per side, 30 single-level deltas; initial snapshot 2229.48 ms, delta p50 39.86 ms/p95 75.13 ms/p99 95.29 ms. `docs/evidence/m1-core-initial-benchmark.json` retains dataset hash/environment. This is a core-only diagnostic, not end-to-end ingest/render latency. Normal p95 target is not yet met; Python allocation/validation optimization is underway, not an unmeasured native rewrite.
- `uv.lock` generated and intended upgrade environment synchronized with `env UV_PROJECT_ENVIRONMENT=.venv-upgrade uv sync --locked`. Actual full modern feed adapter/supervisor validation, milestone publication and merge remain pending.
- Optimized the same declared core workload in Python: snapshot 937.89 ms, delta p50 3.51 ms/p95 8.49 ms/p99 10.55 ms; original evidence retained alongside `m1-core-optimized-benchmark.json`. Cold full-book initialization still exceeds normal-update budgets and must be reported separately. Post-change core suite **63 passed**, mypy six core modules clean. No native component introduced.
- Integrated seven venue adapters and supervised shared owners: final local gate **509 passed**; mypy **19 source files clean**; Ruff check and format check passed. Network is opt-in. Independent protocol/runtime review is in progress before the M1 merge.
- Actual 35-second multi-venue run: Coinbase, Kraken and Binance global BTC/ETH/SOL WS books and live trades passed; Coinbase REST snapshots and all required Binance global REST/metadata dependencies passed. Hyperliquid BTC/ETH/SOL/HYPE REST snapshots, bounded WS replacement books and live trades passed. Bybit metadata returned **HTTP 403**; downstream live checks remain unrun. Binance.US BTC had no live print within the short window; ETH/SOL prints were observed. KuCoin REST books/history passed with unknown aggressor side.
- Preserved `docs/evidence/m1-live-initial.json` (failed) and `m1-live-corrected.json`. Actual Coinbase heartbeat acknowledgments use `["heartbeats"]` rather than the product ID; Hyperliquid echoes nullable aggregation/default `fast=false`. Strict semantic acknowledgment validation now accepts these observed schemas without accepting wrong identities. Initial Coinbase reconnect/overflow and Hyperliquid decode failures are not counted as successful validation.
- Restored Kraken REST Depth separately from WS synchronization. `m1-kraken-final.json`: BTC/ETH/SOL REST snapshots, WS books and live trades passed; normalized callbacks retained **65/255/29 matched CRCs**, respectively; all six owners joined cleanly. Other venues in this focused matrix are explicitly unrun. No REST observation supplies WS checksum or continuity.
- One regression tail incorrectly expected a specific rejection after an unannounced REST→WS source switch. Removed that redundant incidental-reason assertion rather than changing correct source fencing or re-pinning its wording. The test retains exact REST precision, replacement/degraded coverage and rejection by the WS-only reducer.
- Full-depth finite core benchmark is in `m1-core-benchmark.json`: normal 50 events/s p95 **9.43 ms**, p99 **11.14 ms**; 2× burst p95 **30.08 ms**, p99 **62.86 ms**, maximum **122.18 ms**, with approximately 300 ms scheduler lateness. These are core-only timings, not queue/render/capture proof. Cold snapshots remain separate. No native code introduced.
- Independent protocol/runtime review identified and closed Hyperliquid denomination, Bybit order-type/reference metadata, USD trade-increment validation, public-credential boundary and loss-recovery defects. Reviewers rechecked their reported paths and found no remaining defect. A claimed stable Bybit Spot rename guarantee was withdrawn after its primary source proved limited to TradFi options.
- Hyperliquid BTC/ETH/SOL prices are **USDT-denominated**, with independently identified HyperCore USDC collateral and explicit `quanto_no_fx` contract semantics; HYPE/PURR are USDC-denominated exceptions. This is not a USDT/USDC market conversion. Builder DEX denomination remains unsupported without verified deployer specifications. Stable instrument IDs are unchanged; normalization revision belongs to metadata. Draft pre-correction wire metadata is rejected rather than silently migrated; no M2 captures have been released.
- An additional actual synthetic smoke exposed falsely expanded Binance coverage: one sparse deeper update moved the reported known bid boundary from **9950.01 to 8000**. The corrected reducer preserves its bootstrap frontier (**9950.01**, 5,000 retained known levels), retains original source events, checkpoints the boundary and resynchronizes if known depth is exhausted. Both results are retained in `m1-binance-coverage.json`; regression fixtures cover both sides, complete small books, restoration and exhaustion.
- Latest public runtime evidence: `m1-reviewed-live.json` validates Coinbase/Kraken BTC/ETH/SOL and KuCoin REST capabilities after safety/increment changes. `m1-boundary-final-live.json` validates Binance global BTC/ETH/SOL and correctly denominated Hyperliquid BTC/ETH/SOL/HYPE books/trades/snapshots, with no recorded diagnostic faults and **14/14 owners joined**. The broader prior probe had incorrectly selected only USDC-quoted Hyperliquid products after the denomination fix; selection now uses exact default-perpetual metadata and records its actual quote.
- `m1-registry-live.json` exercises a real Binance BTC feed through the registry: implicit and explicit 5,000-depth requests share **one owner/two references**; closing the tab leaves **one owner/one headless reference**, and releasing both leaves **zero owners**. Actual eligible comparison evidence now resolves discovery-time `data_access=unknown` from that exact validated observation without changing immutable metadata or execution permissions. Durable recorder-lease proof remains M2, not claimed by this smoke.
- Final M1 code gate after these corrections: **609 tests passed in 40.02 s**, mypy **19 source files clean**, Ruff clean; parent formatter reported **27 files unchanged**. Actual network/runtime proofs are listed separately, not inferred from the tests.
- Cross-milestone ledger rows M1-18/23/24 intentionally remain open until their actual analytics/alert/paper, durable-recorder and disk/query consumers are integrated. M1 establishes and verifies those boundaries; later milestones must exercise the complete consumers rather than treating interface tests as end-to-end proof.


## M2 integration evidence and open acceptance

- Shared storage/replay/CLI contracts establish actual normalized callback `delivery_index`, atomic message boundaries, selected-stream capture scope, lexical Decimal preservation and writer-owned projection/checkpoints. Replay clocks use recorded monotonic high-water marks without rewriting original receive evidence.
- First union run: **719 passed, six failed**. Corrected DuckDB 2 tuple-range optimizer incompatibility, missing receive-provenance verification, invalid synthetic gap lineage and obsolete normalization-loss expectations. Type integration was corrected without broad suppressions.
- Subsequent union gate: **727 passed**. Independent storage/replay review then exposed missing mandatory public raw evidence, manifest-before-catalog recovery, metadata bounds and retention-accounting defects. Corrections added shared explicit raw/local origin markers and consumer regressions.
- Latest complete gate before two additional review-edge corrections: `bash tools/check.sh -q` — **769 passed in 77.76 s**, Ruff clean, **36 files formatted**, mypy **24 source files clean**. Frontend does not yet exist. Later maximum-length stream-reference and transient SQLite journal retention corrections still require the next union gate.
- Actual standalone offline integration exercised the default 180-second synthetic capture: **4,368 raw frames, 4,410 batches/events, 24 streams and 1,452 checkpoints**. Source projection matched replay; raw and normalized book/state/clock/audit-observer hashes matched. Backward observer seek/replay and compatible checkpoint seek matched prefix state; bounded DuckDB range queries, SQLite counts and Zstd Parquet metadata were checked.
- Actual CLI `inspect` and raw `replay` succeeded. Paced raw replay at **2× and 20×**, including pause/resume, produced identical book/state/clock hashes. Truncated raw gzip and byte-corrupted Parquet copies were rejected. `docs/evidence/m2-integration-before.json` preserves this exercised result and its dataset/source hashes; observer-augmented hashes are distinguished from projection-only hashes.
- **Startup acceptance is not passed:** the default demo took **46.1088 s** and sealed **60,481,710 bytes**. Normalized storage accounted for **840 files / 41,096,545 bytes**; checkpoints for **1,452 files / 14,436,736 bytes**. The small two-second demo took **4.4117 s**. Profiling and focused storage/startup corrections are underway; no duration/stream reduction or native rewrite is being substituted.
- A prior measured codec hotspot was corrected by caching immutable schema lookup/type hints. Identical 100-roundtrip/32-change profiler workload fell from **2.0419 s** to **0.6152 s** without concurrent parent tests; an earlier **1.1505 s** sample overlapping tests/demo is also retained. These profiler-enabled samples are not end-to-end latency or unprofiled throughput claims. See `m2-wire-profile-before.json` and `m2-wire-profile-after.json`.
- Before the final live smoke below, actual durable recorder ownership, short Binance-global/Hyperliquid capture/replay and the approximately 30-minute recorded soak had not run for M2. M1 public-feed evidence was not substituted for these consumers. M3 analytics and M5 alert/paper seek equivalence remain downstream acceptance.

- Schema-2 storage cutover batches Parquet rows and checkpoint blocks, shares hashed auxiliary state and bounds reader caches. The unchanged 180-second fixture now seals **7,215,169 bytes**. Independent review closed aggregate-row bounds, interrupted retention deletion, reader-lock recovery and stale initialization-reservation defects.
- Post-correction offline gate: **789 passed in 59.15 s**, Ruff/format **36 files clean**, mypy **24 source files clean**. Repeated actual full CLI/persistence/replay/seek/query/corruption smoke passed in **44.91 s total**; cold default generation+verification+replay was **16.3071 s**, small demo **3.8909 s**. Exact evidence is `m2-integration-final.json`; this is not an instant terminal-startup claim. M3 must keep full durable construction off its first-usable-data path.
- Actual Binance-global durable ownership smoke retained one physical owner across recorder attach, advanced epoch **1→2**, kept recording after browser closure (**24 additional deliveries**, eligible book), and retired **20 distinct clients** under a concurrent-client cap of four. It then **failed graceful completion**: intentional owner cancellation discarded captured normalization work (**39 raw / 35 batches**), correctly producing incomplete status and replay refusal. `m2-ownership-before.json` preserves the failure. Cooperative producer quiescence/in-flight completion/bounded queue drain is being corrected before M2 publication; this failure is not counted as successful durable finalization.
- The first drain correction passed **798 tests** but a second live run still inherited capture loss from the pre-attach owner (**34 raw / 36 batches**, no missing normalized raw ordinal in the new capture). `m2-ownership-drain-before.json` preserves it. Production teardown was corrected to join producers and drain required input **before** waiting for the WebSocket close handshake; a slow close no longer consumes the drain budget before retention. Sticky real-loss status is not cleared.
- The production-context two-stop regressions passed (**2 tests in 3.21 s**). Actual Binance ownership then passed: epoch **1→2**, one owner/two leases at attach, one recorder lease after browser closure with **23 additional deliveries**, **20 retired clients**, zero final owners, **32 raw / 34 batches**, complete capture and identical raw/normalized book/state/clock hashes. See `m2-ownership-final.json`. This closes the durable ownership acceptance; the multi-asset/venue CLI integration is a separate run.
- The separate **20-second, 12-stream BTC/ETH/SOL Binance-global+Hyperliquid** CLI run failed honestly with writer `queue_overflow`: **440 raw, 533 events, 445 batches, 60 checkpoints**, exit **2**. All Binance dependency probes passed; subsequent failed sinks invalidated all owners. `m2-live-capture-before.json` retains exact source/hash/limit evidence. A single-stream pass is not substituted for this scope.
- Profiling the preserved real-data prefix identified CPU costs rather than slow disk: three Binance bootstrap checkpoint/restore round trips and repeated full-depth wire serialization. Native candidate transfer reduced profiled normalized application **13.958→4.270 s**, retaining exact book/state/checkpoint/auxiliary hashes at deliveries **100/200/300/400/445** and the full retained depth (up to **5,019 levels/side**). Bounded lexical/schema wire caching is being measured separately; no queue enlargement, depth reduction or loss suppression is substituted. Profiling timings include overhead/concurrent work, not a live throughput guarantee.
- Integrated writer profiling reduced **31.688→19.714 s** on the same preserved real-data prefix, with raw/event/checkpoint hashes identical to both baseline and original capture. Queue **512 items / 128 MiB**, depth, schema and checkpoint cadence were unchanged; no storage algorithm change was justified by the profile. `m2-full-depth-profile.json` retains source/code hashes, full comparisons and attribution.
- Final local tests: **816 passed in 55.33 s**, Ruff clean, **38 files formatted**. A schema-cache typing error was then corrected using the registered class type; Ruff/format and mypy **24 source files** passed afterward. The actual final-source CLI integration below passed; remote CI will gate the published head.
- Actual full-scope CLI capture/replay **passed**: **20 seconds**, **12 BTC/ETH/SOL Binance-global+Hyperliquid book/trade streams**, all owners live in epoch1 at the pre-stop observation, **679 raw / 826 events / 703 batches / 216 checkpoints**, complete capture, zero incomplete reasons. Raw and normalized CLI replays both exited0 with identical book/state/cursor hashes. Total record command **47.642 s** includes discovery/probes/start/finalize; raw replay **19.313 s** and normalized replay **16.916 s** include verification. These are not steady-state latency quantiles. See `m2-live-capture-final.json`.
- Final typed-cache boundary regression run: **9 passed in 0.37 s**. Executed temporary smoke/profile scripts were removed after verification; profiling workload text is retained with the evidence for reproduction against the retained local capture. Raw exchange recordings, environments and generated captures remain ignored and are not pushed.

## M3 verified prerequisite and active integration

- Explicit derived-Decimal wire policy preserves p80 results without weakening native constructors or changing released capture schemas. `ruff format` on the three touched files, `ruff check`, and mypy **24 source files** passed; final wire regressions **16 passed in 0.21 s**.
- Actual normalized CLI replay of the released 12-stream real-data capture passed in **17.127 s**, with unchanged book/state/cursor hashes and complete-capture status. `docs/evidence/m3-precision-native-compat.json` records the exact command and baseline manifest hash. The public recorder soak was running concurrently; this is compatibility proof, not a latency-budget claim.
- The **1800-second** BTC/ETH/SOL Binance-global + Hyperliquid recorder **failed**, exit **2**, after **30m17s** total command time. M2 source revision `6f5665b8342651897e66da18d4f0198f190b3b74035418a1937d776fc71df3ab`, capture `51d3dc95-be2e-4210-ad4a-b1b29e84476c`, manifest SHA-256 `f970da57bf56929251420243cb46be2d901445d31622d194cfe31dffcbfadc64`. It retained only **609 raw / 522 events / 432 batches / 60 checkpoints** before `queue_overflow`; the command continued futile source reconnects against the failed sink. This is a partial durable prefix, **not** 30 minutes of healthy data. `docs/evidence/m3-soak-before.json` preserves counts, limits and final source states; full local evidence remains ignored under `data/m2-soak/run-1793513547763496000/`. The earlier 20-second pass remains valid but is not sustained-capacity proof. A 24-hour soak remains unrun.
- M3 independent ownership: analytics plus additive trade evidence; exact cost/comparison engine; runtime/API plus replay-source/demo-memory integration; frontend only. Parent owns precision/wire, atomic live completed-message boundary, measured ingestion stages, shared docs and union verification. All later acceptance remains open until actual integration proof.
- First frontend gate passed: `npm --prefix frontend run lint`, `npm --prefix frontend test` (**3 passed**), and `npm --prefix frontend run build` (TypeScript clean; Vite production assets built) with Node **22.22.0**. This is not browser interaction/render-latency proof. Python union verification and real terminal smoke remain pending.
- Independent review corrections cover analytical continuity/window gates, derivative inventory assumptions, live bootstrap/owner lifecycle, capture identity, readiness side effects, and frontend stale/reconnect/session fences. Parent is adding a failed-handshake ownership regression and fatal-recorder early-stop regression before the union gate. Recorder profiling and a reusable bounded full-depth load benchmark are underway; no larger queue, dropped depth, altered capture completeness or unmeasured throughput claim is substituted.
- Actual full-scenario analytics smoke passed: the immutable 180-second/24-stream source contains **4,368 raw frames / 4,410 deliveries**, prepared in **2.030 s** in this run. Raw/normalized replay, 2×/20× pacing, backward seek, reset and observer restoration produced equal book/analytics/clock/snapshot hashes. The released real Binance-global/Hyperliquid capture also matched raw/normalized analytics. See `m3-analytics-integration.json`; this is deterministic integration proof, not a standalone latency benchmark.
- The first complete Python union reached **944 passed / 6 failed**. Corrections address invalid reset lineage in a fixture, a genuine missing pre-reset checkpoint, WebSocket Host/cancellation cleanup, spot-only prefunding expectations, asynchronous live-bootstrap observation and an obsolete exact Gap-count assertion. The focused recovery/cadence/fatal-writer gate then passed **12 tests in 6.61 s**. The final union remains in progress; these partial results are not substituted for it.
- Preserved failed-soak profiling and bounded arrival-load evidence in `m3-recorder-load.json`. Caching resident-size field metadata cut the isolated sizing CPU median by approximately **35%**. Periodic checkpoint cadence **100→1000** allowed all **609 raw / 432 batches / 522 events** of that prefix at the synthetic 2× arrival schedule with queue high-water **274**, versus overflow at cadence100. Queue **512 / 128 MiB**, raw/event fidelity and native depth remain unchanged. Recovery/final checkpoints remain mandatory, including a pre-source-change checkpoint when pristine reset state cannot be indexed natively. Worst-case observer-free periodic seek work increases **99→999 batches**. Both unpaced loads still overflow; sustained live collection is not yet verified.
- Integrated Python gate: **951 passed in 121.62 s**, one retained Starlette/httpx deprecation warning; Ruff clean and **48 files formatted**. The gate then found 13 remaining runtime typing diagnostics (TypedDict updates, empty pair-tuple inference and callable truthiness); explicit typed corrections passed Ruff and mypy **29 source files**. Actual final-source HTTP startup/cost smokes below passed afterward.
- Actual final-source cold CLI/API startup passed in **3.47 s total**, process→liveness **1.040 s**, process→usable complete-scope demo **2.642 s**. Readiness correctly changed **503→200→503** around session ownership; serving built frontend, exact-string values, cleanup and bounded SIGINT shutdown passed. CLI intentionally reports interrupted exit2. See `m3-startup.json`; one sample, not a startup percentile.
- Actual HTTP cost integration passed **21 economic scenarios / 33 requests** against the final local server, including asymmetric depeg FX, conversion costs, stale/wrong-clock rates, unknown/explicit fees, quantity/budget accounting, spot-only prefunding, incompatible market/quote cohorts, lot limits, insufficient depth and multilevel fee/slippage accounting. See `m3-cost-api.json`.
- Actual Chromium end-to-end run passed **8 tests in 33.4 s**, followed by clean TypeScript/Vite build; frontend lint and three unit tests also passed. Initial browser run was **7 passed / 1 failed** because Playwright's disabled matcher did not recognize a native disabled option; the regression now inspects its DOM disabled property. Manual visual review at **1440×900/1280×800** covered healthy demo, Hyperliquid bounded-depth/unknown-fee comparisons and reset-to-empty safety; retained screenshots are `m3-terminal-*.png`. No horizontal laptop overflow or manual-workflow browser errors were observed. These are not M4 heatmap or render-budget claims.
- Final-code managed-recording benchmark passed baseline/2× evidence and state/checkpoint parity on the complete real capture; streaming queue high-waters **51/27**, with generator-limited unpaced high-water90. The failed-soak prefix's bounded-predecoded baseline/2× retained all609raw/432batches, high-waters **228/256**, 2× producer **3.400 s** against3.387 s schedule. Unpaced input still overflows512 and is explicitly incomplete. Full distributions/CPU/RSS/depth/hash evidence: `m3-recorder-final-streaming.json`, `m3-recorder-final-predecoded.json`; long checkpoint/queue tails are reported, not treated as live receive→valid success.
- New actual **30-second / 12-stream BTC/ETH/SOL Binance-global+Hyperliquid** capture passed: **1,377 raw / 1,401 batches / 1,528 events / 156 checkpoints**, complete with no incomplete reasons, recorder high-water **182 items / 12,271,337 accounted bytes**. All12 owners were live in epoch1 before stop; actual receive/queue/decode/validation/normalized/publication observations were retained. Inspect and raw/normalized CLI replay passed exact book/state/cursor parity. See `m3-live-capture.json`, manifest SHA-256 `e53b6b46df610a7352e099abaad33830c1e0c69c973e6b192818b4484de0a09f`. This is not the replacement30-minute soak.
- Actual recorded browser playback exposed two provenance defects: searching prose for `synthetic` misclassified “no synthetic overlays”, and a finalized recorded source said “Recording active”. Source classification now uses declared demo fixture metadata rather than free text; the banner distinguishes recorded playback from active collection. A catalog regression and final real-recording browser confirmation gate this correction; before screenshot retained as `m3-recorded-label-before.png`.
- Final publication-candidate gate `bash tools/check.sh -q` passed **952 tests in 117.42 s**, Ruff/format48 files, mypy29 modules, frontend lint,3 unit tests, TypeScript and Vite build. Corrected actual recorded playback showed **REPLAY / Recorded capture · playback**, `synthetic=false`, ready at delivery300 with **4,997 bids / 5,005 asks** retained and15 displayed rows; no browser errors. See `m3-recorded-browser.json` and before/after screenshots. Completed analytics/startup/cost throwaway scripts were removed; evidence remains.
- Published terminal increment **`dbf20bd9b4a8c90696c0b6a40f70d0a62c489bb6`**. Initial push failed with HTTP400/sideband disconnect; remote remained at the prerequisite SHA. A per-command HTTP/1.1 +16MiB POST buffer retry succeeded; no global settings/authentication/history were changed.
- Replacement1800-second soak ran from detached immutable worktree `/tmp/limit-orderbook-m3-soak-dbf20bd` at published `dbf20bd`, using the shared locked Python environment. Command: `.venv-upgrade/bin/python data/m3-smoke/live_capture_smoke.py --duration 1800 --output-root data/m3-live-soak --evidence docs/evidence/m3-live-soak.json`. It overlapped development, not an isolated performance run. The final evidence is now promoted to `docs/evidence/m3-live-soak.json`; raw capture/logs remain ignored in that worktree.
- Both remote offline checks passed on published terminal head `dbf20bd`; PR#4 is ready for review. Targeted ownership/provenance/API/CI review found no fix-required defect. Recorder review found two bounded-failure issues: final sealing errors were not classified terminal, and a benchmark source with64 reasons lost its final reason in the new writer's diagnostic list.
- Actual sealing-fault regression failed before the fix (manifest absent, terminal=false, fatal reason discarded). Adding `finalization_failed` to terminal classification made the focused gate pass **4 tests in3.00s**; Ruff/format and mypy29 modules passed. An actual before/after benchmark smoke against published and corrected tools retained **63→64 original source reasons**, with raw/event parity in all three finite loads. Source reasons now live in indexed manifest configuration; over-capacity/reserved namespace inputs fail explicitly. See `m3-recorder-review-boundaries.json`. The throwaway smoke was removed. Final-head remote checks must run again after this correction.
- Recorder reviewer rechecked both corrections and found no remaining fix-required defect. The soak stayed pinned to `dbf20bd`, before those diagnostic/provenance-only corrections. Its actual manifest, not terminal-health interpretation alone, establishes incompleteness.
- Final review-correction head **`df5a64f089337eb9ff7332472c74cc004ab5db0e`** passed both remote offline/browser checks. PR#4 merged normally as **`eb7ff5e40cdf0f7ce1d0708f197c57922e0979eb`**; local main fast-forwarded, then M4 branched from it. No approvals or protection bypass were fabricated.
- The replacement soak **failed** with `queue_overflow` at **1610.440s** after attachment; the CLI now stopped promptly instead of reconnecting for the remaining requested duration. It retained **78,199 raw / 78,021 batches / 79,754 events / 948 checkpoints**, queue high-water **512 items / 12,246,245 accounted bytes**, and **140,412,206 written bytes**. Manifest SHA-256 **`95c254077b622c29efc86c9d159482aef7b8141434db50bc77ec1fd584d829dd`**. Storage verification passed; complete-capture replay was deliberately not claimed. Record command took1633.220s including startup/shutdown; verification took194.707s. All harness-owned processes were reaped. Sustained-load remediation remains actionable; neither a healthy30-minute nor24-hour run is claimed.

## M4 shared contracts and active implementation

Three isolated worktrees start at mergedM3: `/tmp/limit-orderbook-m4-visuals` (exact bounded historical core/runtime/API), `/tmp/limit-orderbook-m4-canvas` (native tiled Canvas and linked visual modes), `/tmp/limit-orderbook-m4-workspace` (persistent/resizable terminal and actual API lifecycle). Parent remains sole integration, verification and Git-delivery owner. Agents do not run gates or publish competing commits.

Shared interfaces define visualization/grid/column/trade/profile/footprint DTOs, grouped exact cumulative ladder, hypothetical entry/stop risk, and chart-specific publication/paint acknowledgments. The latter avoids falsely treating M3 frame ACK as proof that a separately fetched chart painted. Exact source/Decimal/generation/quality contracts remain unchanged; missing cells are distinct from observed zero; full native capture is independent of sampled visual history. Actual alert/paper/journal tabs remain M5, not placeholders.

The three slices and independent review corrections are integrated. The first union retained1036 passing tests and one fixture failure: it assumed a1-unit native tick rather than0.01. Correcting the fixture's declared tick produced1045 passing Python tests. Subsequent focused visualization/API and completed-presentation gates passed92 and12 tests respectively. These precede the legacy cutover; the current union result is recorded below.

Actual browser observations before corrections reproduced hidden/collapsed pane polling, a DOM-linked time at the exclusive window endpoint, and inaccessible lower chart readouts at1280×800. Before/after screenshots and `m4-workspace-review-fixes.json` retain the findings and corrected scrolling/cursor behavior. Actual Node executions prove failing-before/passing-after exclusive-end merging and1800-second zoom bounds in `m4-window-boundary.json` and `m4-zoom-boundary.json`. Backend trade-quality/ambiguity regressions and browser hidden/collapsed polling checks passed. The20-scenario browser union initially passed18 and failed2; exact fee selectors and source-clock fee dates corrected both, and their targeted rerun passed2. The final full browser union and final-state screenshots remain pending.

The sustained recorder failure remains separate from chart correctness. `docs/evidence/m4-soak-checkpoint-profile.json` profiles real retained checkpoints at deliveries1000,65000,78000: forced-cold checkpoint preparation took581.777–800.887ms across nine rounds. This read-only CPU profile excludes disk/fsync/live operation and is not full-capture parity. Its hottest paths are checkpoint/wire preparation; no native-depth reduction or unbounded buffering was applied.

The first coherent M4 backend increment was committed and pushed as **`ea8f571e78bf5ec8b5427d16adb5203278aaa5f9`** on `upgrade/m4-liquidity-workspace`; draft [PR#5](https://github.com/karisheen/Limit-Orderbook/pull/5) is open. It includes exact historical grids/flow gating, grouped native ladder, proposed entry/stop risk, bounded chart ACKs and same-generation diagnostic presentation during owner contention. Frontend, legacy cutover, benchmark and final evidence remain subsequent uncommitted integration work; no M4 merge is claimed.

Two additional isolated worktrees delivered the Streamlit native-API client and retirement of the duplicate legacy collectors/reducers/analytics. Parent integrated them, migrated meaningful corruption/recovery/retention coverage into native tests, removed source-text/mock-echo assertions, and retained the concurrent completed-presentation regression. Streamlit now owns a bounded loopback HTTP/API session per document and consumes server grids, cumulative depth, metrics, tape and cost results. Unsupported frame versions and non-ASCII counters were accepted before the parent correction:2 failed/4 passed in the reproducer. After correction the combined legacy/recovery gate passed **50 tests in1.64s**. Actual Streamlit browser smoke remains pending.

The retirement regression reproduced immediate REST fallback bypassing HTTP Retry-After (**2 failed/1 passed** before correction). The native supervisor now waits the existing deadline before fallback and remains cancellable. `PYTHONPATH=. .venv-upgrade/bin/python data/m4-smoke/real_cooldown_smoke.py` exercised the actual supervisor with an unpatched monotonic clock and explicitly injected HTTP429 responses: two150ms cooldowns produced153.625ms and151.430ms request intervals; recovery published a valid/degraded native book. Cancelling during a2-second cooldown joined in0.949ms with only one HTTP attempt. This is local fault evidence, not a live rate-limit claim; see `docs/evidence/m4-cooldown-real-clock.json`.

After the cutover, `uv lock --python .venv-upgrade/bin/python` and locked environment sync removed the obsolete `websocket-client` dependency using uv0.8.4. The full `./tools/check.sh` gate passed **983 Python tests in178.57s**, Ruff checks/format on57 files, mypy on30 source files, Node tool syntax, frontend lint, **15 frontend unit tests**, TypeScript and the production build. The enclosing smoke+gate command took201.04s. The existing Starlette/httpx TestClient deprecation warning remains visible. The lower test count reflects retirement of the duplicate legacy engine/tests, not a narrowed gate.

A fresh **90-second** public capture from immutable M3 head `dbf20bd` included Binance global and Hyperliquid BTC/ETH/SOL book/trade streams at default native depths, without queue/depth overrides. It finalized **4,252 raw /4,276 batches /4,523 events /192 checkpoints**, with queue high-water204 items/12,342,472 accounted bytes and16,944,515 written bytes. Manifest SHA-256 **`d12553c14ee31de1e66db3d2e9247000b81a976826f4f3da8ae072d4622ee5f5`**. Storage verification and complete raw/normalized cursor/book/state parity passed; all owned processes were reaped. See `docs/evidence/m4-live-capture.json`. This does not supersede the failed sustained soak.

Render benchmark revisions retain their failed attempts: occupied-port refusal; native-tick blank/pinned workload; option-value setup failures; a superseded query during mixed control/streaming measurement; and a legitimate pre-cohort obsolete-generation409. Pan/zoom completion is now measured separately during replay, followed by a steady, visibly populated, advancing chart cohort with cursor input. Only proven older-generation setup409s from the same session are classified expected; every cohort or unknown API error remains fatal. Final owned-server runs, negative measurement proofs and independent boundary reviews are in progress. No render budget pass is asserted from an incomplete/erroring cohort.

The strengthened owned-server render run `data/m4-benchmark/runtime-1440.json` retained matching source/runtime/assets provenance and responsive genuine pan/zoom controls, but did **not** certify the workload: requested1×/2× source rates measured0.8674×/1.6486×. Publish-to-ACK p95 values148.835176ms/196.937731ms do not override that failure. Relative replay sleeps accumulated processing time; both replay owners now use absolute deadlines, with focused raw/normalized scheduling regressions passing. Actual corrected-rate browser measurement remains pending. A narrowly classified pre-cohort obsolete-generation ACK cancellation is retained separately; cohort API/ACK failures remain fatal.

Actual final callback-based legacy workflow11 and the focused generation/reconnect scenario passed in the same43.72-second command as50 focused Python regressions. The browser exercised unknown/explicit fees, seek-cleared comparisons, play/pause/single-step, exact native grid geometry, explicit2× selection, independent documents, reset/reconnect form replacement and close, at1440×900/1280×800 without horizontal overflow or app exceptions. `docs/evidence/m4-legacy-workflow.json` retains the final actions, source hashes, earlier failures and replay-parameter before/after evidence. Both final screenshots `m4-legacy-callbacks-{1440,1280}.png` were visually inspected: native-price categories and partial/truncated-history reasons are visible. This confirms the supported legacy observation workflow, not React render-budget acceptance.

Corrected owned-server30-second demo cohorts at1440×900 and1280×800 passed both1×/2× achieved-rate, lifecycle, API, bounded-evidence and code/runtime/assets provenance checks. Achieved rates were1.0046×/2.0350× and1.0040×/1.9909×; publication-to-ACK p95 was150.971301/203.463948ms and158.288256/196.094903ms. These are conservative round-trip upper bounds, not GPU or synchronized one-way paint timestamps. Both2× screenshots were visually inspected. Genuine pan/zoom and separate Follow recovery were exercised; no budget threshold was relaxed.

The full-depth real12-stream capture did **not** pass the requested-rate workload:1×/2× requests achieved0.7685×/0.7320× and ACK p95 was438.695485/381.673751ms, despite functional/provenance checks passing. An earlier incorrect capture-path invocation failed before startup and is retained. `docs/evidence/m4-render-attempts.json` preserves successful, failed and inconclusive attempts rather than promoting the synthetic result to real-load acceptance. The600-delivery cProfile diagnostic in `m4-real-replay-profile.json` separates projection/reader from visualization/analytics; frame serialization/query/owner contention and sustained capture remain M6 profiling/acceptance work.

Actual measurement fault injection rejected a native replay pause, post-window obsolete-generation409 and aborted cohort ACK. A normally delayed ACK was canceled by next-view cleanup and correctly failed certification. A separate real-request harness held one noncancelable ACK across the window end: issue43236.243830ms < end45596.245285ms < settle47249.655355ms, retained4116.827709ms as the27-sample cohort's p99/max. This proves the issue-time fence does not drop successful late responses; p95 remained155.327725ms. Full injection evidence and exact loader hashes are included in `m4-render-attempts.json`.

The bounded wire memo correction retained exact serialized/gzip hashes for all three real checkpoints. Later-checkpoint median total construction/serialization/compression improved from762.64→718.55ms and763.56→715.89ms, while the early checkpoint was effectively unchanged. `m4-wire-capacity-comparison.json` retains all samples/profiles and separate traced-memory peaks. This diagnostic does not resolve the retained recorder overflow or establish receive-to-valid latency. Full-depth, queue and fidelity limits are unchanged.

Final integrated workspace/legacy gate passed: `bash tools/check.sh -q` → **1003 Python tests in175.30s**, Ruff clean/60 files formatted, mypy30 source files, Node syntax, frontend lint,15 unit tests, TypeScript and production build. `npm --prefix frontend run test:e2e` passed **all20 actual-backend Chromium scenarios** in1.5minutes. The following actual visual-state smoke passed10 observations covering streaming replay, bounded Hyperliquid depth, reset-to-empty, dropped-transport disconnect and independent-session recovery at both1440×900/1280×800, without document overflow or page exceptions. `m4-terminal-browser.json` and its screenshots retain those states; both sizes' partial/empty/disconnected surfaces and recovered laptop state were visually inspected. The combined command took325.10s. Existing Starlette/httpx deprecation and Node color-environment warnings remain visible.

Full-depth presentation profiling now isolates avoidable work: at deliveries300/900/2000, five unchanged production `_view` calls under cProfile took1.839/2.077/2.845s and emitted0.892/1.174/1.703MB JSON. DTO conversion dominates; the frame transports roughly10,000 native levels plus323/671/1398 displayed-liquidity evidence records even though the terminal uses separately bounded ladder/history APIs. `m4-real-presentation-profile.json` retains exact source hashes/call profiles. A bounded **presentation-only** correction and indexed analytical evidence matching are next; native reconstruction, financial inputs and recording fidelity must remain unchanged.

Presentation now carries first100 native levels/side, first200 profile rows and first100 displayed-evidence records, with explicit omission metadata in both clients. Full native quality/coverage and all financial inputs remain unchanged. A real250-level regression fills150 ask levels for exact notional9011325 despite the100-level frame; full-window replenishment remains100 while200 detail records are omitted. Indexed exact-price/taker-side trade matching preserves interval boundaries and reference order. The focused presentation/analytics/runtime gate passed55 tests and the production build.

The next real-capture benchmark passed1× workload and both original250ms presentation budgets, but remained **inconclusive at2×**: achieved1.0431×/1.5211× with ACK p95 122.492967/113.046112ms. All functional/provenance checks passed. `data/m4-benchmark/bounded-presentation-real-1440.json` is retained, not relabeled a2× success. A2500-delivery cProfile in `m4-real-replay-bounded-profile.json` attributes the remaining owner cost to full-depth membership scans and repeated immutable persistence-record reconstruction. The next correction searches sorted native levels and mutates only private owner-held standing evidence, freezing it at public snapshot/checkpoint boundaries.

Independent native/client review found four corrections: future-derived anchor retained on reset, offscreen DOM price selecting chart row zero, failed legacy reconnect deleting a working session, and missing legacy frame-depth disclosure. The first three were reproduced against actual observer/Chromium/Streamlit-native-HTTP paths. Anchor and private-evidence regressions passed in a95-test focused gate; legacy replacement before/after evidence changes the old session's HTTP status404→200 while preserving its identity. The initial corrected Chromium run passed unknown-row semantics but exposed a test locator mismatch; final exact-role verification follows. Both reviewers rechecked the corrected source and reported no remaining fix-required finding in their stated scopes; they did not execute runtime gates or fabricate GitHub approval.

Corrected exact-role Chromium regression passed in7.0s, including offscreen linked-price unknowns, unavailable selection and absence of falsely selected aggregate rows. Actual2500-delivery before/after profiling preserved both native book and complete observer/state hashes at delivery2800; visualization/analytics fell30.456→19.437profiled seconds while projection-only stayed12.853→12.929. The optimized real-capture browser rate gate is now running with its own pinned backend.

Both optimized real-capture laptop runs are now certified at1×/2× with the original250ms budget and rate tolerance. Rates were1.024933×/2.065725× at1440 and1.004050×/2.046775× at1280; ACK p95 was221.510819/124.768204ms and102.686728/127.937354ms. Both2× screenshots were visually inspected, including explicit deep-history truncation and diagnostic busy-owner state. Public evidence retains the earlier failures and full correction progression. Final union `bash tools/check.sh -q` passed **1007 tests in173.70s**, Ruff/format60 files, mypy30 modules, Node syntax, frontend lint,15 unit tests, TypeScript and build. Fresh-backend full browser/failure-state gates follow before publication.

Fresh final backend passed **all21 Chromium E2E scenarios (1.5m)** and the10-state laptop visual smoke in127.87s combined. `m4-terminal-browser-final.json` and its screenshots retain the final source's streaming, Hyperliquid partial depth, reset-empty, dropped-transport/reconnecting and recovery states. The desktop streaming/reconnecting and laptop partial/empty surfaces were visually inspected; empty rewind now has no future-derived price anchor. Final legacy real-capture depth disclosure is being exercised separately. Completed temporary M4 profile/smoke scripts were removed; evidence and permanent benchmark/regression tools remain.

The final real-recording legacy smoke exposed a genuine client timeout mismatch: actual managed-capture creation returned HTTP201 in18.175697s, beyond the ordinary10-second socket timeout. Session creation now uses the existing30-second request budget; ordinary polling/control timeouts, capture verification and response bounds remain intact. The focused legacy gate passed29 tests, Ruff/format and mypy. An intermediate smoke then correctly rendered an empty initial prefix at delivery25; its assumption that Binance's later bootstrap was already present was wrong. The corrected actual-browser workflow seeks through recorded delivery300 and passes at both laptop widths: native4995/4998 bid/ask levels,100 presented per side and explicit4895/4898 omissions. Both screenshots were visually inspected. `m4-legacy-real-create-latency.json` retains its optional-null-metadata diagnostic error alongside successful HTTP timing; all failed smoke attempts remain preserved. `m4-legacy-depth-disclosure-recorded-bootstrap.json` contains the final passing evidence. The final source-union gate follows this last client correction.

After the initialization correction, the final complete source gate again passed: `bash tools/check.sh -q` → **1007 tests in177.21s**, the retained Starlette/httpx warning, Ruff/format60 files, mypy30 modules, Node syntax, frontend lint,15 unit tests, TypeScript and production build;192.25s total. The production frontend asset hashes remain the previously browser-tested build. Both legacy smoke services were deliberately stopped and the final temporary harness removed. Publication and final-head remote checks precede normal M4 merge; M5 implementation starts from that integrated result.

## M5 verified prerequisite and active integration

The immutable merged-M4 30-minute attempt failed with `queue_overflow`, retaining16,392 raw /16,178 normalized batches /16,510 events /204 checkpoints. Its512-item high-water used12,318,571 accounted bytes under the unchanged128MiB cap. Incomplete storage inspection succeeded; complete-capture replay is not claimed. `m5-merged-m4-soak.json` preserves the actual result, separately from the earlier harness-transfer syntax failure in `m5-soak-launch-failure.json`.

The finite retained-prefix capacity experiment found repeated empty queues and burst/service-pause exhaustion, not demonstrated sustained average overload. At2× original arrival pacing,512 slots overflowed;2048 slots retained all8,258 raw/batch items, peaked at591 items /9,226,391 accounted bytes, and drained repeatedly. Raw/normalized final book/state hashes and persisted native/auxiliary/checkpoint parity matched at full depth. Exact original checkpoint/callback overlap remains an inference. `m5-recorder-capacity.json` retains workload, measurements and limitations; these incomplete profile captures are not a successful live soak.

The first recorder runtime correction was default count512→2048; the128MiB resident byte cap, in-flight accounting, native depth, checkpoint cadence, wire format and terminal overflow behavior were unchanged. Parent replaced a proposed default-pinning assertion with an explicit bounded16-slot raw/normalized admission/drain/overflow regression. The isolated complete-increment gate `env UV_PROJECT_ENVIRONMENT=<main>/.venv-upgrade bash tools/check.sh tests/test_recording.py -q` passed **93 tests in14.44s**, Ruff/format60 files, mypy30 modules, Node syntax, frontend lint,15 frontend unit tests, TypeScript and production build;43.00s total. This gate did not certify the then-uncommitted M5 engines.

Recorder prerequisite `54cabdfa727d505d7733310713ac80098dab4e99` was pushed and its remote head verified. Draft [PR#6](https://github.com/karisheen/Limit-Orderbook/pull/6) is open; both exact-head remote offline checks succeeded: [push](https://github.com/karisheen/Limit-Orderbook/actions/runs/41313661552/job/124869096665) and [PR](https://github.com/karisheen/Limit-Orderbook/actions/runs/41313747601/job/124869354736). This is not an M5 completion or merge.

Paper/journal and alert reviews found causal metadata, source-clock ownership, Hyperliquid limit-validation, journal provenance, limit/resource reasoning, cooldown-update and nested-evidence-bound defects. Corrections are integrated; final API and UI reviews report no remaining findings, not runtime certification. Actual context identity smoke reproduced and corrected Binance/Hyperliquid basis rejection caused by comparing venue-native identifiers rather than the canonical underlying asset identity; synthetic before/after evidence remains explicitly labeled.

The corrected-capacity1,800-second attempt had **no terminal recorder overflow**, peaking at1,425/2,048 items and9,135,110 accounted bytes. It nevertheless remained **incomplete** because upstream required queues lost input. It retained111,172 raw /108,641 batches /112,426 events /1,675 checkpoints and239,559,231 written bytes; incomplete storage verification passed, complete replay parity was deliberately skipped, and all owned processes were reaped. `m5-capacity-live-soak.json` preserves the result; total harness time2124.77s includes capture and verification.

Five retained BTC/ETH trade loss ranges each contained514 raw frames against the512-frame required queue, at only about86KiB payload occupancy. The original production reader, given the first buffered burst, overflowed before a decoder call. A cooperative `await asyncio.sleep(0)` after successful admission lets the runnable consumer progress; no upstream count/byte bound is widened. All five bursts drained under both buffered and receive-offset schedules with exact raw/trade values and within-output raw/normalized/checkpoint parity. Corrected maximum occupancy was418 frames /70,224 bytes. Exact live scheduling remains an inference, and achieved finite receive spans are retained rather than claiming the requested pacing. See `m5-recorder-upstream-capacity.json`.

The isolated fair-reader focused gate passed **180 ingestion/recording tests in17.76s**, Ruff/format60 files, mypy30 modules, Node syntax, frontend lint,15 frontend unit tests, TypeScript and production build;39.79s total. Only the ingestion regression file needed formatting. This was a focused gate, not complete-suite or uncommitted-M5 certification.

Fair-reader `12915c0666ada79d62f6e6005c8af688a593fc0f` was pushed with its remote head verified. Both exact-head full-suite checks failed ([push](https://github.com/karisheen/Limit-Orderbook/actions/runs/41318469511/job/124883449261), [PR](https://github.com/karisheen/Limit-Orderbook/actions/runs/41318472988/job/124883459116)): the controlled-time recovery test incorrectly treated the new cooperative `sleep(0)` as a gated timer. The helper now yields without advancing controlled time; production deadlines are unchanged. The failure is not waived.

Its immutable1,800-second public capture also remained **incomplete**:91,133 raw /89,624 batches /91,898 events /1,352 checkpoints and186,644,709 written bytes. Recorder occupancy peaked at1,493/2,048 items and14,108,280 accounted bytes without terminal overflow; BTC and ETH trade required-input queues still lost input. Other10 streams remained capture-complete. Incomplete storage verification and owned-process cleanup passed; complete replay was correctly skipped. `m5-fair-reader-live-soak.json` retains the result, including full native Binance5000/Hyperliquid20 depth; total harness2075.02s.

Finite profiling of the retained mixed interval67500–68684 identifies per-frame executor/task/queue-wait scheduling overhead: baseline processed226/740 received frames before loss; direct-dequeue alone227/741; bounded inline small-trade processing delivered806/806 with queue high-water1. A3880-byte retained Hyperliquid array decoded in at most1.584277ms wall in this sample; larger arrays stayed threaded. Full-depth mixed bootstrap and native trade/raw/normalized/checkpoint/auxiliary parity are retained in `m5-queue-service.json`. This is finite diagnostic evidence, not a new sustained pass. The correction changes placement/scheduling, not queue/depth/acceptance limits. Permanent burst fixtures contain original explicitly synthetic activity rather than assuming redistribution rights to captured market payloads.

The complete isolated queue-service increment passed `env UV_PROJECT_ENVIRONMENT=<main>/.venv-upgrade bash tools/check.sh -q`: **1,016 Python tests in216.15s**, retained Starlette/httpx warning, Ruff/format60 files, mypy30 modules, Node syntax, frontend lint,15 unit tests, TypeScript and production build;246.58s total. An earlier run passed1,014 with two fixture failures because the new synthetic Binance payload omitted the required combined-stream envelope; the fixture was corrected without relaxing production validation. This gate includes the controlled-time regression that failed published12915c0 CI, but does not certify the separately integrated M5 engine/UI union.

Queue-service `1aa63f273131282e0083f010fe20d25c144fc188` was pushed and its remote head verified. Both exact-head checks succeeded: [push](https://github.com/karisheen/Limit-Orderbook/actions/runs/41325121031/job/124903335655) and [PR](https://github.com/karisheen/Limit-Orderbook/actions/runs/41325116920/job/124903323487). Its actual requested1,800-second capture failed at **484.082s** with recorder `queue_overflow`:28,163 raw /28,173 batches /29,183 events /348 checkpoints, high-water2,048 items /12,195,402 accounted bytes. The failed capture remained incomplete, complete replay was skipped and all subprocesses were reaped. `m5-queue-service-live-soak.json` retains this failure; the earlier missing-environment launch failure is separately retained.

`m5-recorder-service.json` identifies avoidable cold Arrow conversion and measured finite burst pressure. Real-schema conversion now prepares before admission; the count bound is4,096, while128MiB, in-flight accounting, native depth, cadence and fail-closed behavior remain unchanged. The retained envelope contained2,432 offers/2s and3,542/5s, with273 sampled backlog; the corrected final60 receive-seconds at2× peaked at2,314 items and drained/finalized in2.814s. Every raw/normalized/checkpoint/auxiliary file matched the original descriptor/hash, with native/raw/normalized/checkpoint parity. Original source and diagnostic manifests remain incomplete. This is not a sustained live pass or a checkpoint speedup.

Recorder service commit `31ba0d8af7b4a80d9234a5b892a1c6b8171128f6` was pushed to `upgrade/m5-simulation-research`; the remote PR head matched exactly. Both checks passed: [push](https://github.com/karisheen/Limit-Orderbook/actions/runs/41332073866/job/124924090219) and [PR](https://github.com/karisheen/Limit-Orderbook/actions/runs/41332078172/job/124924102306). PR#6 remains draft; this publishes the recorder prerequisite, not the uncommitted M5 feature union.

Integrated M5 consumers now include seven native alert kinds and bounded supporting evidence; required Hyperliquid reference/context and reference-only basis with exact quote/FX identity; explicit spot-only IOC paper funds/fees/three latencies, durable run archive and linked journal; and offline chronological purged/embargo research with persisted attempts/all outcomes. The React terminal exposes actual Alerts/Context/Paper/Journal/Research tabs and read-only research reports. Failed resume was reproduced against real HTTP and corrected to preserve both active cursor/generation/engine and durable pending-order archive before atomic cutover; see `m5-paper-resume-*-before.json` and `m5-paper-resume-*-after.json`. Session-close/reopen proof is in `m5-native-api-reopen.json`; full process restart and live-cohort proof remain in progress.

The first complete M5 native union passed1,147 tests with three failures: two incidental demo/count assertions were removed rather than re-pinned, and the controlled-time helper was corrected. The focused CLI/recovery run then passed36 tests in72.69s. Current mypy passes35 modules without broad suppression; frontend lint,15 unit tests, TypeScript and production Vite build pass. Final combined gates remain required after integration. Initial browser runs exposed test selector and paused-observation assumptions; those are corrected against actual accessible controls and causal source delivery, not by changing financial behavior.

Actual public45-second CLI recording of BTC/ETH/SOL on Binance global and Hyperliquid included18 book/trade/reference/context streams: **complete**,2,283 raw /2,319 batches /2,462 events /360 checkpoints, no incomplete reason or terminal failure; recorder high-water130 items /8,230,999 bytes. Native raw/normalized observer state, checkpoint and cursor parity passed, with required auxiliary observations and eligible reference basis observed for all three assets. `m5-context-cli-capture.json` and `m5-context-capture-replay.json` retain the actual source revision and results. This is a finite functional integration proof, not sustained load or independent OI-unit verification.

Actual offline900-second synthetic-v3 research completed in131.34s:12,600 observations,151,200 labels,2,592 trials;5,446 train /1,456 validation /2,310 test /1,708 purged /1,680 embargoed. Attempt `3e41fc4916f84642827febad25cd733d` reports **insufficient_evidence**, with unknown fees/net outcome rather than invented profitability. Report hash `391f557c0dbe2310751cf1e4287ae17d11c0d72e1d5ee64cf813236b370821ee`; evidence `m5-research-cli-demo.json`. The source activity is original CC0 synthetic data, not empirical predictive-edge evidence.

### M5 browser and lifecycle integration

The complete choose/observe/alert/explicit-paper-order/journal/rewind/resume workflow runs against the native backend. Review corrected two real UI mutation races: a newer WS generation could abort its own successful HTTP resume, and a failed resume could be falsely confirmed after an unrelated seek. Presentation buffering is bounded to one latest frame during an owned mutation; confirmation requires the exact accepted generation. Pane cancellation is visible locally and never automatically retries uncertain mutations.

Actual manual Chromium proof at1440×900 and1280×800 is in `m5-workflow-visual.json` and six `m5-{alerts,paper,journal,context,research}-*.png` screenshots. The explicitly configured simulated0.01ETH IOC spent30.01USDT plus0.015005USDT assumed fee, leaving969.974995USDT and0.01ETH. The persisted note retained the exact source/session/delivery/run/order links. Native hourly funding, reference-only basis and unverified OI-unit provenance were visible. The real900-second research report reopened with its unchanged report hash and `insufficient_evidence`; no responses were mocked in this manual proof.

Whole-process restart and explicit durable-run adoption are retained in `m5-lifecycle-restart-20261101T160701Z.json`. Actual live cohort evidence preserves initial failures and the corrected69.36-second HYPEUSDT→ENSUSDT→Hyperliquid spot@107 transition, including retained funds/run/rules, departed pending-order cancellation and quiet-clock advancement. These smaller cohorts do not certify BTC/ETH/SOL's full16-product selection. Large unrelated discovery catalogs are explicitly excerpted; original files/hashes remain identified.

Live selection now fills the same16-product bound fairly across venues and excludes inactive implicit peers without weakening explicit-selection validation. Event sizing is reused, trade-only delivery does not rebuild the current-book tuple, and immutable trade-stream indexing avoids repeated scans. The dedicated-producer703-delivery experiment passes exact complete state/history parity at the unchanged8MiB analytical bound. `m5-lifecycle-capacity-summary.json` preserves same-loop starvation confounds and diagnostic capacity trials; it does not justify widening production capacity. Native source snapshots are SHA256-addressed once in `m5-lifecycle-source-snapshots.json`. Focused runtime integration passed **19 tests in64.44s**.

The first fresh-backend full browser run after that optimization exposed a genuine paper activation regression: **22 passed,1 failed**. Identity deduplication skipped baseline rescheduling during rewind. Both zero-latency and delayed-eligible native cases failed before correction; the early-arrival exclusion still passed. Activation now clears its scheduling identity cache before reconstructing the baseline under configured feed latency. Evidence: `m5-paper-activation-before.json` and `m5-paper-activation-regression-before.json`. The prior full native gate was cancelled rather than certified against a known regression.

The corrected fresh backend passed **all23 Chromium E2E scenarios in128.55s** using `env PATH="<repo>/.tools/node/bin:$PATH" npm --prefix frontend run test:e2e`. The paper/recorder focused gate passed128 cases with one incidental retained-byte equality failure: the recomputed allocator-dependent charge was16 bytes smaller after checkpoint decoding. Removed that heap-layout assertion; the test instead verifies exact canonical checkpoint restoration and retained resource-bound behavior. The subsequent complete `bash tools/check.sh -q --tb=short` gate passed **1,162 Python tests in311.48s**, the existing Starlette/httpx warning, Ruff/format70 files, mypy35 modules, Node syntax, frontend lint,15 unit tests, TypeScript and production build;338.20s total. `m5-integration-final.json` pins this passing source and served assets.

The next actual native full16-product BTC/ETH/SOL run failed for all three assets at the unchanged8MiB analytical queue bound. Public discovery succeeded, but sessions became explicitly unavailable; no live workflow pass is claimed. Failed atomic offers required1,147,091 /1,661,140 /931,028 accounted bytes over existing7,466,879 /7,980,075 /7,679,494-byte queues, respectively, with39/42/64 queued deliveries. Every session was removed and the backend reaped. `m5-integrated-cohort-20261101T172219Z.json` retains the native failures and complete diagnostic source. A bounded64MiB diagnostic-only trial now measures full native depth and owner service; the production default is still8MiB, with no depth or cohort reduction.

The diagnostic64MiB trial also failed BTC and ETH while SOL completed its actual native IOC/alert/context workflow. Ordered owner batching was then tried without weakening production bounds: `m5-integrated-cohort-20261101T173829Z.json` still records BTC/ETH overload and SOL success. The next instrumented run, `m5-integrated-cohort-20261101T174454Z.json`, measured every owner operation: batch processing dominates (BTC2.555s wall /2.015s thread CPU; ETH12.210s /9.975s; SOL10.271s /9.283s), not paper configuration (all below26ms) or view generation (all below49ms). These live intervals differ and are not a controlled speedup comparison. Further profiling is required; neither64MiB nor batching alone is certified as the fix.

The single-thread native synthetic10,000-level benchmark isolates the repeated work:100 snapshot queries made4,000,000 valuation calls and20,000 standing-evidence freezes. Caching only immutable book-derived results reduced unprofiled wall time3.415725s→0.094368s and CPU2.935197s→0.091319s. All100 complete snapshots retain SHA256 `d500b09b8b93c6ff8ebac290dbfc79644bb760bc554fafbf03dc248270af5957`, and the native checkpoint retains `45f1baad1bf2dd28ac1e48a79545fb6d06abbd500a4fb6d0d59bb94656322c42`. Source/script/profile evidence is in `m5-depth-service-{before,after}.json`. Clock/quality/window gates remain per query; these are snapshot-service timings, not end-to-end feed latency. The earlier live cProfile report captured unrelated asynchronous work too and is not treated as exclusive owner timing.

With that correction, actual diagnostic64MiB full16-product BTC/ETH/SOL workflows all completed: ready books, configured fees/funds, native IOC fills, local alert and context consumers, no terminal failure, all sessions removed and backend reaped (`m5-integrated-cohort-20261101T175259Z.json`,120.41s). The maximum BTC charge64,597,682 bytes leaves insufficient room for another approximately5MiB full-depth book. Production admission therefore uses128MiB finite burst headroom; count256, original events, full depths, fail-closed behavior and independent8MiB history retention remain unchanged. This is not a sustained-load pass. Three new native queue regressions passed in0.91s, covering ordered complete-observer parity and count/byte ownership through cancellation joins; the full integration gate remains required.

The production128MiB run also passed all three full-cohort workflows (`m5-integrated-cohort-20261101T175620Z.json`,120.15s). Observed maximum charges were62,400,848 /57,145,382 /20,653,249 bytes for BTC/ETH/SOL, with70 /58 /40 admitted deliveries; each owner repeatedly drained (634 /784 /1,017 boundaries). Process RSS high-water was280,432,640 bytes, not a universal memory guarantee. Explicit session closure left10 BTC and3 ETH offers beyond the observed analytical prefix; this is not claimed as complete delivery through shutdown or as recording. Full original reports remain locally retained with byte hashes; published reports preserve all requests/statuses/errors/health/telemetry and financial/context responses, excerpt only intermediate analytics detail, and identify those excerpts explicitly.

The fresh production-code backend passed all23 actual Chromium scenarios again in166.09s. Backend PID77111 was stopped with owned SIGINT and reported exit2, not zero. `m5-service-integration.json` ties the production run, cache parity, focused ownership regressions, browser command and source hashes together; the subsequent complete native gate is recorded below.

Final corrected union: `bash tools/check.sh -q --tb=short` passed **1,166 Python tests in398.70s**, one existing Starlette/httpx deprecation warning, Ruff/format70 files, mypy35 modules, Node syntax, frontend lint,15 unit tests, TypeScript and production build;435.10s total. This gate includes the native cache freshness/coverage/restore regression and cancellation-safe ordered queue tests. Built assets remain `index-DjcEpben.js` and `index-DNEp5Wv-.css`.

Feature integration `366e68ad6784a6ec54a17c9e5c3eeacae636e600` was committed and pushed; `git ls-remote` matched the exact SHA. The final evidence increment deduplicates repeated immutable values through explicit in-document `publication_ref` pointers; expanding every pointer reproduced each initial published report exactly. Each report pins its initial immutable public revision and SHA. This reduces review repetition without hiding failures or changing measurements. Two attempted additional read-only scouts could not run because their configured provider quota was exhausted; explicit model override was disabled by the tool. The integration owner performed the new service investigation and verification inline; no additional independent review is claimed.

## M6 verified operational increment

- M5 final evidence head `54131d6eb9f50e5dfd6f78e664542c7707d67bef` passed both remote offline/browser jobs: [124934755729](https://github.com/karisheen/Limit-Orderbook/actions/runs/41335637136/job/124934755729) and [124934769386](https://github.com/karisheen/Limit-Orderbook/actions/runs/41335642024/job/124934769386). PR#6 was normally merged as `6588c44210140a2854398bbdf24b1977d420e06c`; M6 branched from that integrated result.
- The actual Streamlit1.66.1 default had configured address `None`, resolved public wildcard `0.0.0.0`/dual-stack `::`. Added only `[server] address = "127.0.0.1"` to project configuration. Launched the actual client without a CLI address override; `lsof` showed only `127.0.0.1:8510`, root HTTP200 and native health `ok`. Owned PID83182 was stopped and reaped with exit0. Evidence: `m6-legacy-loopback.json`. No permanent copied-default assertion was added.
- Fresh isolated locked Python installation initially failed because offline cache lacked the pinned PyArrow26.0.0 wheel. Retried only dependency acquisition with package-network access, then native2-second durable demo/replay, `npm ci` and production build succeeded in85.53s. All four lock/manifest hashes were unchanged; exact frontend assets reproduced. The pinned unsupported ESLint warning remains disclosed. Both attempts are retained in `m6-locked-setup-20261101T182042Z.json` and `m6-locked-setup-complete.json`.
- Actual native HTTP/process operations observed live200 versus initial ready503,6.955602s cold-process-to-usable-HTTP while the independent recorder ran,12 selection switches without moving a second client's cursor, stale generation409, Origin403, Host400, body413 and unknown fields422. The initial harness incorrectly expected capacity429 instead of the implemented503 and stopped; that failed overall attempt remains retained. The continuation corrected only that expectation, observed actual slow-WebSocket eviction with independent-client state unchanged, empty-session cleanup and process restart/new session identity. `m6-operations-20261101T182915Z.json` and `m6-operations-20261101T183107Z.json` distinguish the phases.
- Fresh native CLI `record --network --venue binance-global --venue hyperliquid --assets BTC ETH SOL --duration 1800 --root data/m6-live` completed with exit0, child reaped, unchanged native source hash `1a09935b23d1094ec90ce20d61ba5512a586785402189854d993dc6c78759e17`. Session `048a4437-1069-44f0-b11a-b63d158b1cdd` has18 streams,81,095 raw frames,81,131 batches,83,344 events,1,782 checkpoints,846 files and no manifest incompleteness reasons. Recorder high-water1,072 items/12,223,120 bytes; final queue empty; process RSS220,700,672 bytes. The1831.818404s process included563.456296 user and67.384502 system CPU seconds. Setup/operations ran concurrently on the workstation; this is not an isolated-host latency claim. `m6-sustained-capture-20261101T180952Z.json` retains the script, exact logs and native output. Full replay integrity is a separate running check.
- Current native/browser render benchmark ran separately after collectors stopped: actual1x/2x source-clock rates, all provenance/rate/sample checks certified at both1440x900 and1280x800. The unchanged250ms p95 publish-to-ACK bound passed all four finite cohorts. The1440 burst p95 is249.670420ms and p99435.666095ms: little p95 margin, not a no-outlier guarantee. Exact values, commands, screenshots and limitations are in `m6-render-1440.json`, `m6-render-1280.json` and `READINESS.md`. Both burst screenshots were visually inspected; partial/truncated history remains explicitly hatched. Owned native servers were reaped with their actual interrupted exit2.
- Independent agents prepared a real per-owned-socket venue-fault harness and a native finite raw replay/timing harness; separate reviews corrected classification, provenance and lifecycle/reporting defects before execution. They did not run checks or mutate native source. Parent owns execution and integration. The exact live receive-to-valid boundary is being measured separately rather than relabelling replay/synthetic-queue timing.
- Corrected stale setup/probe commands and obsolete legacy-service/demo timing claims. `READINESS.md` separates correctness, UI/operations, research and unsupported execution, documents recovery/health/attribution, and provides the runnable24-hour capture command with explicit **not run** status.

- The first full sustained replay verification ran796.42s but its final report conversion rejected floating-point timing metadata in the intentionally strict domain serializer. No lost in-memory checks are certified. `m6-sustained-replay-integrity.json` retains that failed attempt/script/error. The harness now uses ordinary report JSON floats with native conversion only for domain objects and persists each completed mode; reconstructing the evidence uses a fresh output. Native source/capture bytes were not changed.

- Exact live measurement used the unchanged native CLI and existing `ProcessingObservation` stages for a requested60-second18-channel capture. Every one of2,145 raw receipts matched exactly one timing observation by clock/stream/ordinal/receive time. The initial class-wide wrapper also saw329 discovery-probe observations and four cancelled probe operations outside that capture; those remain retained, not misreported as capture errors. The first analysis's blanket zero-gap requirement also incorrectly rejected18 legitimate terminal `owner_stopped` boundaries. The source-fenced, lifecycle-aware analysis verified all14 checks without recollecting live data:1,403 normal-load book updates over55.700506613s had p50 **4.994096ms**, p95 **12.724556ms**, p99 **52.890941ms**, maximum196.755258ms. The unchanged25/100ms normal targets pass; cold/cohort bootstrap does not. The six-book first-publication boundary is3.449303042s after first receipt, independent of percentile values. All original startup samples and failed unsegmented comparison remain in `m6-live-latency-20261101T190710Z.json`; both analyses are retained, with final `m6-live-latency-source-analysis-v2.json`.
- Actual12-stream Binance-global/Hyperliquid BTC/ETH/SOL fault probe established all requested book/trade owners healthy, held one genuine Hyperliquid BTC book message8s and released it unchanged, then closed only its own socket. No watchdog recovery occurred before explicit close. All11 unrelated streams progressed during the hold. A processed valid book then appeared in epoch2/new segment. Native recorder/registry/sampler and13 opened sockets joined; owner/task/active-socket counts ended at0, recorder thread stopped, child PID86221 reaped normally with exit0. Full evidence/script/limits: `m6-venue-fault-20261101T191543904982Z.json`; complete-as-received recording is not uninterrupted venue-global coverage.
- The full sustained replay rerun completed all12 checks: all846 files verified; every81,131 delivery/83,344 event replayed without sampling; raw/normalized book hash `8edcd6a93291585209b942ae1a267ba3be68e65f84e0b83c1e09fd75b09ff5f4`, projection hash `c62b2225a881dec6ab09ed033fca1b7319cbdae199184c6ab30bbc521d2b0a35`, context-inclusive hash `04406c12323a2e652a7e572ea9a07fb1dafd4b8682e4d625b8fd9555b761f20b`, context hash `7115fe94bbaa66c2f38f78fd64506cb31a64334bf4b2ea6b7305f8de9edc16ea` all matched. Strict stored native checkpoint restore matched projection; context observers without matching persisted state recompute history. Total821.123014s wall/766.445242s CPU/326,868,992B RSS; final-delivery checkpoint seek65.135486s. No instant-seek claim. Evidence: `m6-sustained-replay-integrity-complete.json`; source unchanged.
- M6 first increment `a2e57dd49f93c0ace60c374f5ebcac38a15a2f7f` initially failed publication with HTTP400 and no remote branch. Retrying only transport with command-scoped `http.version=HTTP/1.1` and `http.postBuffer=67108864` succeeded; no global configuration or history rewrite. Draft PR#7 targets main. Both first-head checks succeeded: [124953719590](https://github.com/karisheen/Limit-Orderbook/actions/runs/41341968388/job/124953719590),7m14s; [124954024681](https://github.com/karisheen/Limit-Orderbook/actions/runs/41342073041/job/124954024681),6m59s. These do not substitute for the forthcoming final-head gates.
- Finite pipeline evidence `m6-pipeline-90s.json` preserves the complete90-second source:4,252 raw frames/4,276 batches/4,523 events. Both1x/2x runs decoded every raw frame exactly once, matched each native normalized transaction and final state, joined producers and drained bounded512-token queues without rejected offers. Metadata queue highwaters111/228 and incremental queue p99 **1292.972496/2354.861307ms** are synthetic serial replay observations, not live receive-to-valid latency passes. Original historical capture timestamps also exceed the upper-bound budgets; retained unchanged. The separate current native live measurement supplies the finite normal-load latency evidence.
- Final production-writer benchmark `m6-recording-90s.json` completed whole-source1x/2x loads with exact raw/normalized/regenerated-checkpoint bytes, full raw/normalized state and strict seek parity. Highest tested complete paced input89.998083 batches/s over47.512123 producer seconds;83.540845 written batches/s including startup/drain/seal. Queue highwaters161/229; no rejected or accepted-but-unwritten records. Unpaced load deliberately reached4,096 queued items, rejected one offer and sealed incomplete after draining2,054 raw/2,065 batches; no sustainable-capacity pass is claimed for its326.744213 partial-write rate. All timing/resource details and p50/p95/p99 are in `CORE_PERFORMANCE.md`; original failed evidence is preserved.
- Review caught an inaccurate benchmark disclosure: version1 said original processing/publication timestamps were absent, although the writer simply does not analyze them. Corrected only report metadata to version2 and `source_processing_and_publication_timestamps_analyzed=false`; no production or timing algorithm change. Actual tool run on the existing2-second native demo completed all three loads with source/replay parity; Ruff lint/format passed. `m6-recording-disclosure-smoke.json` records the new tool SHA; original measured report is unchanged.
- Preserved the complete90-second source outside `/private/tmp` at ignored `data/m6-reference/4498db3b-f4a2-4fff-a32e-a97221fa21ee`; all77 regular files matched SHA256. `m6-source-retention.json` retains the full inventory. Completed throwaway harnesses were removed after their sources were embedded in evidence; large captures, local environments and unrelated user artifacts remain uncommitted.
- Final M6 head `e3447f224e0eed64575a11387ad8d48b004d4ba7` was pushed and verified on origin. Both exact-head jobs succeeded: [124958709708](https://github.com/karisheen/Limit-Orderbook/actions/runs/41343633218/job/124958709708),6m10s; [124958719077](https://github.com/karisheen/Limit-Orderbook/actions/runs/41343636408/job/124958719077),6m43s. `gh pr merge 7 --repo karisheen/Limit-Orderbook --merge --match-head-commit e3447f224e0eed64575a11387ad8d48b004d4ba7` succeeded normally, without force/admin bypass. GitHub returned merged state, main base and merge `612309c905c311310e620563c764e97304465d98` at2026-11-01T19:56:20Z.
- Local main fast-forwarded from verified origin to integrated M6. The documentation-only `upgrade/m6-delivery-receipt` branch records that now-known merge SHA and closes the delivery ledger through a separate PR, rather than rewriting the milestone or bypassing its checks. No native engine, frontend, benchmark algorithm or retained measurement changes in this receipt.

## Post-upgrade live terminal visuals

The reported screenshots were paused synthetic-demo sessions, not low-volume
public books. The correction branches from `cabbc0ad8fd4b1cedbfa5ae74595f7f111b62dc9`
on `fix/live-terminal-visuals`.

- Explicit network startup prefers live; URL and explicit saved source choices
  remain authoritative. Legacy automatic demo preferences migrate visibly.
  Startup chooses an exact active native instrument before creating a live
  session; unavailable live never substitutes synthetic evidence.
- Initial/recenter price fitting covers genuine nearby book levels and admitted
  prints, using exact native-compatible quanta. Default time bounds follow the
  actually retained source-clock history. Rewind discards future fitted scale.
  Explicit aggregation and manual panning remain authoritative.
- Instrument buttons are bounded to 100 per page without restricting full
  catalog search or native collection. DOM layout changes restore the quote
  boundary; normal updates preserve user scrolling without native scroll-anchor
  jumps. Native retained depth, bounded frame rows and displayed L2 aggregates
  are distinguished from individual orders and executed prints.
- Footprint/profile use 32 native price rows for legibility. Other modes retain
  128; changing density refits only automatic unpanned views.
- Actual Chromium reproduced a stale canvas with a batched `false, true`
  intersection notification. The old callback retained its first entry and
  stopped painting despite advancing view IDs. It now consumes the final
  entry. `live-visuals-intersection-before.json` retains actual geometry and
  notifications; the regression checks changed canvas pixels after a native
  instrument change, not only request wiring.
- One-sided heatmap liquidity fills the full observed time bucket when the
  opposite side is known zero. Mixed-side and unknown-side cells retain their
  separate evidence; the browser regression samples actual native canvas pixels.
- Live Hyperliquid BTC → Binance SOL selection reproduced
  `analytical_queue_overflow`: new deliveries accumulated while old collectors
  shut down, before the new observer could consume them. Consumer initialization
  now occurs at lease commit and drains during departed-owner cleanup. Selection
  still joins old owners, cancellation joins the charged bootstrap consumer, and
  queue/depth bounds remain unchanged. The regression injects 300 real-schema
  snapshots while old cleanup is held, checks the exact final quantity, and
  covers cancellation. The focused native run passed **140 tests in 61.76s**.
- `bash tools/check.sh -q --tb=short` passed **1,179 native tests in 298.85s**
  and Ruff/format (71 files), then exposed an optional-disposer mypy error.
  Retaining the explicitly owned bootstrap disposer corrected the type/lifetime
  ambiguity. The follow-up passed **140 focused tests in 57.42s**, Ruff,
  formatting, and mypy (35 modules). Earlier failed runs and commands are
  retained in `docs/evidence/live-visuals-validation.json`.
- That frontend lint, **20 unit tests**, TypeScript and production build passed;
  assets are `index-Ch9eMnhW.js` and `index-oTep9C8S.css`. Build output remains
  uncommitted. The real paused-demo screenshot now explicitly explains fixture
  sparsity, pause state, and the operator's live-network requirement.
- A later concurrent-client check reached the unchanged analytical queue bound
  and correctly became unavailable. `live-visuals-live-overflow-before.json`
  retains that failure. Diagnostic owner timing ran separately on port 8001;
  its cProfile records also contain unrelated asynchronous activity and cannot
  establish per-operation cumulative CPU attribution. No queue/depth/cohort
  bound was relaxed, no stale warning was suppressed, and no general sustained
  capacity improvement is claimed.
- A five-minute Hyperliquid check initially failed after **187.76s**, despite
  continued painting. Real owner timings isolated visualization calls up to
  **649.24ms**; `live-visuals-live-progress-before-cache.json` and
  `live-visuals-owner-timing-before-cache.json` retain that failure. Reusing
  canonical column bytes alone was insufficient. Closed native metric
  provenance is now encoded once per sample, charged to the unchanged history
  budget, decoded into independent query containers, and removed/rebuilt on
  eviction/reset/restore. The **63 visual regressions passed in 12.10s**,
  including consumer-mutation isolation and checkpoint restoration.
- Real Hyperliquid DOM recovery was exercised from two coarse native buckets
  (no scrolling) back to 30 displayed rows: both sides recentered at scroll
  offset 214. Ordinary updates still preserve deliberate manual scrolling.
  Final frontend lint, 20 unit tests, TypeScript and build passed with
  `index-imMpJONh.js`; the CSS asset remains `index-oTep9C8S.css`.
- Caching alone still failed the real Hyperliquid run after **156.24s / 70
  observations**; `live-visuals-live-progress-after-cache-failure.json` and
  `live-visuals-owner-timing-after-cache-failure.json` retain its failure and
  measured timings. The ingestion owner was still blocked by the whole chart
  query, not merely duplicate serialization.
- Chart projection now runs on one runtime-wide read worker over a bounded,
  shallow immutable-evidence snapshot, without holding the session lock or
  ingestion worker. Source/generation/lifecycle fences remain authoritative;
  publication transfers only presentation state, never newer native history.
  Cancellation joins the query before session removal. The held-query
  regression failed both cases before the ownership change, then passed both
  cases in **2.27s** while 300 real-schema Hyperliquid updates advanced to their
  exact final quantity with zero overflow.
- The complete post-snapshot gate, `bash tools/check.sh -q --tb=short`,
  passed **1,183 native tests in 291.47s**, Ruff/format (71 files), mypy
  (35 modules), frontend lint, **20 frontend tests**, TypeScript and the
  production build (**312.48s** overall). The only native warning is the
  dependency's existing Starlette/httpx TestClient deprecation. The four focused
  snapshot/cache regressions passed first in **2.23s**, including eviction of
  the live owner's old history and rejection of a changed source clock.
- Sustained post-snapshot Hyperliquid verification passed **300.50s / 123 native
  observations**, with advancing revisions and zero overflow. Real Hyperliquid
  BTC → Binance SOL cutover also retained the session, advanced generation and
  showed **4,685 bid / 4,987 ask levels** with zero overflow.
- At the snapshot-only checkpoint, Binance BTC still overflowed after **58.82s /
  27 observations**. `live-visuals-binance-after-snapshot-failure.json` retains
  that failure; merging was held until the closure below. Real-thread component
  timing measured analytical DTO conversion up to **242.72ms** and a complete
  owner view up to **586.29ms** across the observed runs.
- Incremental Git checkpoint: branch `fix/live-terminal-visuals`, commit
  `1bfd81f43ededbda40224328d69be0fc02b7be65`, pushed to the verified origin.
  The first push received HTTP 400; command-scoped HTTP/1.1 with a 10 MiB
  non-chunked request buffer succeeded without changing persistent Git config.
  [PR #9](https://github.com/karisheen/Limit-Orderbook/pull/9) was opened as a
  draft against `main`, with integration held for the sustained Binance gate.
- The remaining investigation also found catalog conversion and framework JSON
  serialization running directly on the ingestion event loop. The new
  production-path regression failed before the change (**6.54s**): an actual
  normalized book update could not advance until held catalog work released.
  Catalog encoding now runs on the existing catalog worker and retains one
  response keyed by exact metadata plus copied readiness/reasons. The ingestion
  and metadata/readiness-invalidation regressions passed together (**6.34s**).
- Repeated immutable provenance conversion is now per-call memoized, with a
  bounded field-name cache; sorted depth-band scans stop beyond their boundary.
  **133 native API/analytics/visual regressions passed in 73.43s**. The corrected
  cache-key typing passed mypy (35 modules), Ruff and formatting. The standalone
  native-demo benchmark retained an identical **282,124-byte / 41-metric**
  payload hash while using **796 rather than 1,586** transient containers.
  Interleaved 50-run median conversion CPU was **3.76ms versus 8.18ms**.
  This synthetic-domain measurement is not live-feed capacity certification.
  The actual post-change venue checks below are the functional acceptance.
- Final-code live checks passed in one actual Chromium session:
  Binance BTC **300.01s / 140 observations**, Hyperliquid BTC **300.01s / 141**,
  and Hyperliquid → Binance BTC return **120.01s / 54**. Each had advancing
  native revisions and **zero analytical overflows**. Selection retained the
  session through generations 1 → 2 → 3 → 4, ending with Binance SOL; the
  diagnostic component harness also recorded no overflow.
- All six Binance BTC modes painted current native responses; Hyperliquid
  heatmap/candles/profile and Binance SOL heatmap/footprint were captured with
  both DOM sides visible. The SOL capture retained **4,742 bid / 5,000 ask**
  native levels; Hyperliquid retained its honest **20 / 20** slow-feed scope.
  Display row limits are not ingestion limits or counts of individual orders.
- Completed screenshots and exact response/UI identities are indexed in
  [`live-visuals-completed-proof.json`](evidence/live-visuals-completed-proof.json).
  Representative surfaces:
  [Binance BTC heatmap](evidence/live-visuals-binance-btc-heatmap-complete.png),
  [Hyperliquid candles](evidence/live-visuals-hyperliquid-btc-candles-complete.png),
  [Binance SOL footprint](evidence/live-visuals-binance-sol-footprint-complete.png).
  Each venue's `live-progress` report retains first/last native observations,
  every sampled progress row and the read-only observation harness.
- These are finite functional checks, not a 24-hour soak, isolated-host
  throughput certification, or uninterrupted venue-global coverage. Full-depth
  Binance history remains bounded by the unchanged 32 MiB visual budget; a
  requested 180-second range does not fabricate absent retained history.
  Hyperliquid slow20 snapshots are not exact event-flow deltas, so unavailable
  event OFI remains unavailable. No queue/depth/cohort bound was increased.
- The final native sweep passed **1,185 tests in 306.12s** before Ruff found an
  obsolete `api.dto` import. Removing that import also exposed the regression's
  obsolete hook for the old API binding; the test now holds only the actual
  runtime conversion entrypoint. The post-cleanup command
  `bash tools/check.sh -q --tb=short -k 'catalog_serialization or catalog_refresh'`
  passed both catalog regressions (**3.79s**), Ruff/format (71 files), mypy
  (35 modules), frontend lint, **20 frontend tests**, TypeScript and production
  build (**20.04s** overall). The assets remain `index-imMpJONh.js` and
  `index-oTep9C8S.css`; generated build output is not committed.
- Pre-contrast offline Chromium suite: `npm --prefix frontend run test:e2e` passed
  **28 tests / 1 expected network-only skip in 144.71s**. This covers actual
  source selection, replay, chart pixels, viewport/lifecycle and workspace
  workflows; no public live source was substituted with fixture frames.
- Public-live source/DOM browser suite subsequently passed **4 tests in 44.7s**
  (**52.02s** including lint). A prior run failed an incidental CSS assertion
  requiring an explicit `option[value]` attribute; the options correctly derive
  their submitted value from text. That assertion was deleted, not re-pinned;
  actual coarse aggregation, shallow → scrollable DOM recovery and both-side
  visibility remain exercised.
- Final screenshot review exposed a separate SOL scale limitation: **101 of
  128** positive native cells exceeded even the old 10-unit contrast ceiling.
  The fixed full-intensity quantity now uses a logarithmic `1e-12`–`1e12`
  slider, with quantity/unit announced accessibly; transfer remains linear and
  never follows incoming maxima. Preference validation preserves that range.
  The new reload-boundary regression failed before (**1,000 became 10**) and
  passed afterward. Frontend lint, **21 tests**, TypeScript and production build
  passed in **21.68s**, producing `index-Dou23D6P.js` and the unchanged CSS.
- On the plain native server at `http://127.0.0.1:8000/?source=live`, trusted
  keyboard interaction selected **1,000 SOL**. After the existing preference
  write settled, a real reload restored exactly that quantity. The refreshed
  SOL heatmap shows differentiated liquidity with **4,668 bid / 5,003 ask**
  native levels and zero overflow. The evidence index distinguishes this final
  contrast capture from the earlier, otherwise unchanged chart-mode captures.
  This native server is left persistent, without diagnostic monkeypatches.
- Runtime closure checkpoint committed and pushed separately as
  `e2b3c49487ebfa1605e03dcab3de1b264ade8807`
  (`fix(runtime): isolate catalog encoding from live ingestion`), preserving
  the first visibility/ownership checkpoint. PR #9 remains the integration
  boundary; the final UI/evidence increment follows its browser gates.
- Final post-contrast integration gate passed frontend lint, **28 offline
  browser tests / 1 expected network-only skip (2.4m)**, then **4 actual public
  live browser tests (53.9s)**; combined command wall time was **205.03s**.
  Offline verification used the existing Playwright config with only baseURL
  overridden to port 8002, leaving the operator's live port 8000 available.
  That isolation exposed an old test's hardcoded origin; it now uses the
  configured context fixture and relative URLs, retaining its session-owner
  assertions. The temporary config and diagnostic scripts were removed;
  reproducible harness sources and failures remain in the evidence reports.
- Final UI/evidence checkpoint committed and pushed as
  `009ba53b886bdfd1c9be4c5732af137846cd0ad6`. Its push CI passed
  ([run 41369220865](https://github.com/karisheen/Limit-Orderbook/actions/runs/41369220865));
  its independent PR CI exposed a fixture startup race
  ([run 41369228240](https://github.com/karisheen/Limit-Orderbook/actions/runs/41369228240)):
  **1,184 native tests passed / 1 failed** with `obsolete_ordinal`.
  The cutover test injected messages directly while its fixture could still
  initialize the same source epoch and decode the first snapshot. It now waits
  for that first completed native transaction before taking over injection.
  All **300 updates**, held departed-collector cleanup, exact final quantity,
  cancellation and zero-overflow assertions remain. Production ingestion and
  queue limits are unchanged; the failed run is retained, not retried away.
  The corrected runtime/API suite passed **27 tests in 60.24s**, followed by
  Ruff/format, mypy, frontend lint, **21 frontend tests**, TypeScript and the
  unchanged production bundle; total gate wall time **77.50s**.

### Live-visuals Git delivery receipt

- Verified origin: `karisheen/Limit-Orderbook`; branch:
  `fix/live-terminal-visuals`. Four cohesive commits were pushed successfully:
  `1bfd81f43ededbda40224328d69be0fc02b7be65`,
  `e2b3c49487ebfa1605e03dcab3de1b264ade8807`,
  `009ba53b886bdfd1c9be4c5732af137846cd0ad6`, and
  `b7732069bf52bb83d7c5125f93c8817b259bc41c`.
- [PR #9](https://github.com/karisheen/Limit-Orderbook/pull/9) was **merged
  normally**, retaining those commits, at **2026-11-02T01:10:13Z**. Merge SHA:
  `725d821cc28f1aace71aef78a38663489bd8564b`. Local `main` fast-forwarded to
  that merge without altering unrelated work.
- Both GitHub gates passed on exact final head `b7732069bf52bb83d7c5125f93c8817b259bc41c`:
  [push run](https://github.com/karisheen/Limit-Orderbook/actions/runs/41370351375)
  (**5m35s**) and
  [PR run](https://github.com/karisheen/Limit-Orderbook/actions/runs/41370355699)
  (**6m1s**). Each passed **1,185 native tests**, **21 frontend tests**, **28
  real offline browser tests / 1 expected skip**, lint, format, types and
  production build. Public-live browser and finite venue-soak evidence is
  recorded above; CI itself runs offline.
- The plain native server remains persistent at
  `http://127.0.0.1:8000/?source=live`; its health endpoint returned
  `{"live":true}` after integration. Next operator action: open that explicit
  live URL and hard-refresh an older browser tab. No diagnostic patches,
  fabricated orders, raised queue limits or concealed quality gaps remain.
  The 24-hour soak remains unrun; this receipt does not assert execution or
  long-duration production readiness.

## Post-upgrade visual depth ladder

Integration branch: `feat/visual-depth-ladder`, based on
`998bd577e3f2a2503437f29bb51823ff13b8899d`. Native and visual implementation
were delegated independently behind one additive `/ladder` contract; the
integration owner migrated API types, workspace preferences, chart linkage,
existing browser assertions and operator documentation.

### Resulting behavior

- Bid/ask bars use one exact, held denominator across both sides. Incoming
  quantities do not rescale it; **Rescale depth** is explicit. Values over the
  cap saturate without changing the underlying quantities.
- The center row exposes native midpoint, spread, tick count when known, and
  bps. Coarse DOM buckets do not replace the native best quotes in that
  calculation. Financial values stay native decimal strings; abbreviated
  presentation retains the exact value in its title/evidence.
- Independent DOM grouping, base/quote-notional units and optional cumulative
  depth persist across reloads. Native notional values each original level at
  its own price before grouping, including the existing contract conventions.
- Aligned numbers, bold native-best buckets and brief same-side/same-price
  quantity-change outlines improve scanning. Changes are not labeled as
  trades, cancellations or individual-order identities. Reduced motion
  disables the animation.
- Follow-midpoint ownership survives ordinary quantities and automatic scroll
  clamping. Manual wheel/scroll ownership survives resizing and row-count
  changes; **Recenter DOM** explicitly resumes following.
- Hover/focus links an exact native DOM price to its containing chart bucket
  and horizontal cursor. Leaving/blurring clears the link; off-viewport prices
  do not clamp to unrelated evidence.
- Stale/ineligible retained snapshots have no active bars or spread summary.
  Source mode, actual scope, unknown tick and all native reasons remain
  visible. Controls wrap without overlap at the supported 250px pane width.

### Executed verification

- `bash tools/check.sh -q --tb=short tests/test_workstation_visuals.py
  tests/test_workstation_visual_api.py`: **110 native tests passed in 102.63s**.
  Ruff passed; one newly edited test expression needed wrapping. The corrected
  file passed the format check, and mypy passed **35 modules**. The existing
  Starlette/httpx deprecation warning remains unrelated.
- With `.tools/node/bin` on `PATH`, final frontend lint, **28 unit tests**,
  TypeScript and production build passed in **17.87s**. Final assets:
  `index-CTUoBLmO.js` and `index-BkZukdN3.css`; generated bundles are not committed.
- Final isolated offline Chromium suite: **28 passed / 1 expected network-only
  skip in 122.90s**. It used the existing Playwright configuration with only
  baseURL overridden to port 8002. The temporary configuration was removed.
  The public-live source suite separately passed **4 tests in 49.5s** against
  the real network-enabled server; combined lint/build/browser wall time was
  **69.62s**.
- A finite real-Binance browser run sampled **60 distinct eligible native book
  observations**. Its cap stayed exactly **12.78352 BTC** throughout; both-side
  bar ratios and all displayed native quantity strings were checked.
  Quote/cumulative conversion, explicit rescale and freeze-while-tape-advances
  passed. Manual scroll stayed at **272px** through scope/size changes, and
  Recenter restored both native-best buckets.
- Exact DOM price **77815.99** linked to chart bucket **77815** at step **1**;
  actual cursor-canvas pixels, pointer leave, keyboard focus and blur were
  checked. DOM group **5**, quote units and cumulative depth survived a real
  reload independently of chart group **1**.
- Final narrow-surface check: **1280 × 850** viewport, **250px** pane,
  **248px** pane scroll width, **110px** grouping selector, no intersecting
  control labels, follow still enabled and reduced-motion animation `none`.
  Screenshot review caught the initial collapsing grouping control; its
  minimum flex basis was fixed and the actual surface was rechecked.
- Binance retained thousands of native levels while displaying 25 grouped
  levels per side. Hyperliquid exposed its actual **20 bid / 20 ask** bounded
  snapshot scope and an unknown native tick, not fabricated full depth.

Evidence and screenshots:
[`depth-ladder-validation.json`](evidence/depth-ladder-validation.json),
[Binance base-depth workspace](evidence/depth-ladder-binance-btc.png),
[quote/cumulative depth](evidence/depth-ladder-binance-quote.png),
[Hyperliquid](evidence/depth-ladder-hyperliquid-btc.png), and
[minimum-width controls](evidence/depth-ladder-narrow.png).
API responses, DOM readouts and images are sequential observations with their
own asofs, not an atomic multi-panel snapshot.

### Limits and operational handoff

The broader live spot cohort reached `analytical_queue_overflow` during
interrupted/long-running capture attempts. The new DOM correctly kept the
retained rows diagnostic, with **zero bars and zero summary values**; explicit
reselection established fresh history. Later capture attempts also encountered
that existing fail-closed boundary. Owner-busy/last-completed-presentation,
venue stale/continuity and partial-history warnings remain visible in evidence.
The managed browser initially auto-froze between turns; later checks used a
persistent tab and interval-based waits. None of this is an uninterrupted-feed,
isolated-host throughput or 24-hour-soak certification.

No ingestion/cohort/depth/history bound was raised and no missing liquidity was
invented. Verification tabs and the isolated offline server were closed. The
plain network-enabled native server remains persistent at
`http://127.0.0.1:8000/?source=live`; hard-refresh an older tab for the new bundle.
The existing API, visualization and terminal documents describe the new
contract and controls.

### Visual-depth-ladder Git delivery receipt

- Commit `c9897265b89e2ec3930be5ca605bee35d5891b80`
  (`feat(terminal): add exact visual depth ladder`) was pushed successfully to
  `feat/visual-depth-ladder` on verified origin `karisheen/Limit-Orderbook`.
  The account-associated GitHub noreply identity was used for this commit
  without changing global Git settings.
- [PR #12](https://github.com/karisheen/Limit-Orderbook/pull/12) was merged
  normally at **2026-11-02T15:03:37Z**, preserving the feature commit. Merge SHA:
  `7dd26853858d114c0adfb348e7fceaf8489d2006`. Local `main` fast-forwarded to
  that merge without staging or deleting unrelated root `test-results/`.
- Both gates passed on exact head
  `c9897265b89e2ec3930be5ca605bee35d5891b80`:
  [push run 41466393604](https://github.com/karisheen/Limit-Orderbook/actions/runs/41466393604)
  (**6m54s**) and
  [PR run 41466588935](https://github.com/karisheen/Limit-Orderbook/actions/runs/41466588935)
  (**7m30s**). The full push log records **1,191 native tests passed in
  198.19s**, Ruff, **71 formatted files**, mypy over **35 modules**, frontend
  lint, **28 frontend tests**, TypeScript, production build, and **28 actual
  offline browser tests / 1 expected network-only skip**. The independent PR
  run passed the same workflow gates. Public-live checks remain the separate
  local evidence above; CI does not claim public-network validation.
- This receipt is a documentation-only follow-through to the merged feature,
  not a new behavior change or a claim that the known live-cohort overflow
  boundary was removed. Next operator action: open the explicit live URL,
  hard-refresh, and use DOM grouping/units/Rescale/Recenter independently of
  chart grouping.

## M7 — dedicated order-flow workstation redesign

Historical M7 audit retained as the pre-M8 record (2026-11-02). The gate counts,
assets, screenshots and timings in this section belong to that implementation,
not to the M8 source tree; see the [M8 handoff](#m8--market-first-workstation).

Started from verified `main` at `a372354` on
`upgrade/m7-orderflow-workstation`. The preexisting untracked root
`test-results/` was preserved. README, this handoff and the terminal/API/
visualization/development contracts were read before implementation.

### Visual direction and implementation

The running terminal and [Flowsurface](https://flowsurface.com/) website hero,
[repository](https://github.com/flowsurface-rs/flowsurface) and
[application screenshot](https://raw.githubusercontent.com/flowsurface-rs/flowsurface/main/assets/screenshot.png)
were visually inspected before design. No Flowsurface code or assets were copied.
The implementation uses neutral charcoal surfaces, subtle separators, restrained
teal/red market marks, compact headers and aligned exact-number text.

- `App.tsx`, `workspace.ts` and `workstation.css`: simultaneous Liquidity,
  Order flow, DOM and tape panes; pointer/keyboard separators; four-pane
  maximize/restore; bounded browser-local workspace slots; independent tape
  controls; optional watchlist and research drawers.
- `ChartPane.tsx` and `useMarketView.ts`: independent native queries, settings,
  freezes, time/price geometry and explicit cursor links. Frozen navigation
  honors deliberate pan/zoom. Settings/Inspector preserve chart space; Escape
  restores trigger focus. Compact headers wrap rather than reducing type size,
  and settings anchor below the actual wrapped header.
- `Freshness.tsx` and `data-freshness.ts`: primary-layer source-clock lag,
  evidence residence and complete-response residence remain separate from
  drawing/paint/ACK performance. Fresh overlays cannot rejuvenate stale primary
  evidence. Cleared/failed canvases have no presented identity.
- Native `VisualizationRequest.price_min` supplies exact bounded pane-owned
  origins. Sibling recentering cannot mutate a pinned projection; out-of-domain
  derived origins are rejected before publication. Existing economic bins,
  causal/source fences, depth/history/queue bounds and quality policies remain.
- Provenance, explanations and engineering telemetry moved into inspectors,
  tooltips and diagnostics. Exact instrument/venue, source mode, readiness,
  freshness and actionable errors remain visible. Existing native research,
  costs, alerts, context, paper and journal workflows remain reachable.

### Actual visual and interaction evidence

The recorded comparison uses verified public Binance-global/Hyperliquid capture
`4498db3b-f4a2-4fff-a32e-a97221fa21ee`: 12 streams, 4,276 deliveries, source
clock `4a9613c2-dc07-476e-bf1c-5863324e0735`. Screenshots identify **RECORDED**,
not live data; the selected instrument is Binance-global spot BTCUSDT.
Paused comparisons use delivery 900. Viewports below are CSS pixels; the managed
browser screenshot device scale was 1.25.

| Viewport | Heatmap plot | Order-flow plot | Horizontal overflow | Both quality/freshness strips visible |
|---|---:|---:|---|---|
| 1280 × 800 | 960 × 298 | 960 × 218 | None | Yes |
| 1440 × 900 | 1120 × 354 | 1120 × 262 | None | Yes |
| 1920 × 1080 | 1600 × 452 | 1600 × 340 | None | Yes |

Before: [1280](evidence/m7-before-1280.png),
[1440](evidence/m7-before-1440.png), [1920](evidence/m7-before-1920.png),
and [recorded 1440](evidence/m7-before-recorded-1440.png).
Final after: [1280](evidence/m7-after-recorded-1280.png),
[1440](evidence/m7-after-recorded-1440.png),
[1920](evidence/m7-after-recorded-1920.png).
[Baseline provenance](evidence/m7-baseline.json) records the original saved
DOM-focus layout; these are sequential observations, not synchronized snapshots.

Actual pointer drags changed chart split at all three sizes; keyboard DOM width
changed 314 → 324 px without document overflow. A saved slot restored candles
and 334 px DOM width while retaining the current recording source.
Frozen keyboard zoom changed native time bounds; Escape restored Settings
focus. [Maximized footprint](evidence/m7-footprint-maximized.png) exposed numeric
cells that the compact view deliberately omits rather than squeezing the text.
See [layout measurements](evidence/m7-final-layout.json) and
[interaction evidence](evidence/m7-final-interactions.json).

Twenty one-second observations per requested viewport exercised 1× recorded
streaming, with no visible errors and valid primary-layer ages in all 120 pane
observations. Render statistics below deduplicate repeated painted view IDs
(18 per pane/viewport). These are sampled, automation-perturbed observations,
not exhaustive per-frame or exchange-to-screen latency.

| Viewport | Primary layer | p95 draw ms | p95 receive→paint ms | p95 source-clock lag ms |
|---|---|---:|---:|---:|
| 1280 × 800 | Heatmap | 2.1 | 63.1 | 1900.5 |
| 1280 × 800 | Footprint | 0.7 | 54.3 | 1481.1 |
| 1440 × 900 | Heatmap | 3.5 | 52.3 | 1797.8 |
| 1440 × 900 | Footprint | 0.7 | 50.6 | 1296.1 |
| 1920 × 1080 | Heatmap | 1.4 | 51.6 | 1879.8 |
| 1920 × 1080 | Footprint | 0.8 | 50.9 | 1447.0 |

Fast drawing does **not** imply equally fresh evidence. Source sampling,
selected-instrument observations and bounded historical retention remain
visible independently of rendering. [Raw samples](evidence/m7-final-recorded-stream.json),
[summary methodology](evidence/m7-final-age-summary.json) and
[streamed screenshot](evidence/m7-stream-recorded-1440.png) retain that distinction.

With heatmap frozen, the same painted view's source lag grew from 1.006 s to
8.201 s and browser evidence residence from 0.505 s to 7.564 s while the sibling
continued updating. When the footprint's price window no longer contained
eligible primary evidence, it reported unavailable rather than borrowing fresh
overlay/DOM timestamps. Explicit reconnect replaced session `815d6abc…` with
`163435d5…`, reset generation 6 → 1 and replay to its native initial prefix
(delivery 25), cleared freezes and withheld obsolete evidence.
[Freeze/reconnect record](evidence/m7-final-freeze-reconnect.json),
[visible frozen state](evidence/m7-frozen-evidence.png).

### Executed gates and retained limits

`bash tools/check.sh -q --tb=short` passed: **1,246 native tests**
(344.19 s), Ruff, 71 formatted files, mypy over 35 modules, frontend lint,
**53 frontend tests**, TypeScript and production build. The only native warning
was the existing Starlette/httpx deprecation. Measured frontend:
`index-CQREprSa.js` / `index-CadCRKZ8.css`.

The final serialized Chromium run passed **46 tests with one expected
network-only skip** (4.3 minutes), against a separate demo-only server on
port 8004. The temporary base-URL override was removed afterward; CI retains
its standard offline server on port 8000. Coverage includes both panes' query/
paint ownership, independent origins/settings/freezes, source overrides,
save/load, pointer and keyboard layout, 320/390 px accessibility, reconnect,
silent-socket recovery, actual Canvas failure, native cost/research workflows
and replay causality. Frontend unit regressions separately verify complete-body
receipt timing, exact clocks and primary-layer freshness selection.

The owned-server recorded benchmark then completed at 1440 × 900 in Chromium
145.0.7632.6, with no concurrent project gates or live collector. Each cohort ran
for 30 seconds with both chart panes visible:

| Replay speed | Chart ACK samples | Draw samples | p95 publication→ACK upper bound ms | Existing 250 ms budget |
|---|---:|---:|---:|---|
| 1× | 55 | 55 | 104.0 | Met |
| 2× | 55 | 55 | 159.4 | Met |

Both scenarios passed functional, sample-sufficiency and unchanged-budget
checks with zero API errors, source mismatches, frame failures or queue
overflows. Dataset and code hashes were unchanged; provenance errors were empty
and the owned server was reaped. These are aggregate two-pane round-trip upper
bounds, not per-pane, one-way, GPU or live-capacity certification.
[Full benchmark evidence](evidence/m7-render-1440.json) retains the command,
raw samples, runtime/assets/dataset hashes, workload progression and cleanup.
Manifest SHA-256:
`d12553c14ee31de1e66db3d2e9247000b81a976826f4f3da8ae072d4622ee5f5`.

[Integration attempts](evidence/m7-integration-attempts.json) retain failed
checks and their corrections, including the wrong recording-enabled browser
test target, concurrent build race, insufficient-history zoom precondition,
initial clipped quality strips, source-restoration and freshness defects.
No timeout, native quality rule or performance threshold was relaxed.

The actual public-live run reached the existing
`analytical_queue_overflow` boundary again, as the baseline had. The redesigned
terminal made retained charts diagnostic, exposed unavailable freshness, and
withheld current DOM/tape evidence:
[raw live observation](evidence/m7-live-stream.json),
[failure screenshot](evidence/m7-live-1440.png).
This milestone does not certify sustained live-cohort capacity, uninterrupted
feeds, isolated-host throughput or the unrun 24-hour soak.

## M8 — market-first workstation

### Implemented scope and design references

M8 retains the native financial, quality, causal replay and ownership contracts
while improving how the four simultaneous panes expose their evidence:

- Compact responsive chart/DOM/tape headers and contextual settings preserve
  readable market text. Balanced, Analysis and DOM-focus presets, keyboard
  separators, maximize/restore and four browser-local workspace slots remain
  independent of native feed/session ownership.
- Native piecewise receive-clock projection supplies UTC/local time labels,
  date boundaries and clock-step markers. Exact admission coordinates still
  govern buckets; exchange timestamps remain original references, not the
  chart's coordinate clock. Missing mapping remains unavailable.
- Footprints show a held, explicitly disclosed crop of native rows with readable
  SELL/BUY quantities, separate unknown-side evidence and exact Inspector access.
  Compact marks disclose cells that cannot fit numbers; cropping does not
  regroup or invent executions. Fixed contrast never rescales incoming history.
- DOM grouping, quantity units, cumulative depth, held bar cap, follow/recenter
  and independent freeze expose native aggregate-L2 evidence and exact spread.
  Tape filters are presentation-only; per-print inspection pins exact receive/
  exchange/source/side evidence while new prints arrive.
- Cursor links and optional chart-time links are independent. Time navigation
  leaves price origins, aggregation, buckets and freeze flags separate.
  Follow latest retains the painted duration; Fit time uses native sample bounds
  or **returned flow aggregates**, not a nonexistent all-retained trade extent;
  Recenter price changes only price geometry and footprint crop.
- Scoped keyboard shortcuts and focus restoration preserve access through
  menus, inspectors, drawers and maximize. One prioritized incident keeps all
  concurrent native causes inspectable; reconnect is not a collector-capacity
  repair.

Design influence comes from [Flowsurface](https://flowsurface.com/) and its
[primary screenshot](https://raw.githubusercontent.com/flowsurface-rs/flowsurface/main/assets/screenshot.png),
[OpenMarket's footprint guide](https://openmarket.xyz/learn/order-flow/footprint)
and [TradingView's footprint documentation](https://www.tradingview.com/support/solutions/43000726164/):
dense pane hierarchy, readable executed-flow numbers and contextual navigation.
[aggr.trade](https://aggr.trade/) informs economical tape presentation;
[Hyperliquid](https://app.hyperliquid.xyz/) and [Lighter](https://app.lighter.xyz/)
inform the chart/DOM/tape column hierarchy.
No reference code or assets are copied, no chart package is adopted and no
external product's financial semantics or feature parity are claimed.

### Source-specific implementation evidence

The [matched baseline](evidence/m8-baseline.json) uses verified recorded public
capture `4498db3b-f4a2-4fff-a32e-a97221fa21ee`, Binance-global spot BTCUSDT at
delivery **2057 / 4276**, with device scale **1.25**:
[1280 × 800](evidence/m8-before-recorded-1280.png),
[1440 × 900](evidence/m8-before-recorded-1440.png) and
[1920 × 1080](evidence/m8-before-recorded-1920.png). This is recorded evidence,
not a live-feed or capacity claim. Server catalog source IDs are opaque runtime
identifiers, not the recording UUID.

Final matched screenshots use the same recording/delivery and DPR:
[1280 × 800](evidence/m8-after-recorded-1280.png),
[1440 × 900](evidence/m8-after-recorded-1440.png) and
[1920 × 1080](evidence/m8-after-recorded-1920.png). All before/after images were
visually inspected. The exact instrument is
`binance-global:spot-v3:spot:BTCUSDT`; source clock is
`4a9613c2-dc07-476e-bf1c-5863324e0735`, manifest SHA-256
`d12553c14ee31de1e66db3d2e9247000b81a976826f4f3da8ae072d4622ee5f5`.
The browser's opaque catalog source ID in this run is
`4ea4bf7e768141aa926f84b72a394f65`, not a portable recording identifier.
Native automatic price quanta remain **0.5 / 1** for heatmap/footprint,
matching the baseline; readability did not come from coarsening native prices.

| Viewport (CSS px) | Heatmap plot (CSS px) | Footprint plot (CSS px) | Readable native rows | Horizontal overflow |
|---|---:|---:|---:|---|
| 1280 × 800 | 938 × 294.625 | 938 × 267.375 | 11 | None |
| 1440 × 900 | 1074 × 346.625 | 1074 × 315.375 | 13 | None |
| 1920 × 1080 | 1494 × 440.234375 | 1494 × 401.765625 | 17 | None |

The [browser receipt](evidence/m8-validation.json) also records real
unavailable/earlier/frozen states: delivery **25** exposes `no_validated_book`
without fabricated depth ([unavailable view](evidence/m8-unavailable-recorded-1440.png));
delivery **900** retains quiet, narrow-price activity
([earlier view](evidence/m8-earlier-recorded-1440.png)).
An actual reconnect created a new session and reset replay to delivery 25;
seeking to 900 then succeeded.

At paused delivery 900, fresh query IDs retained unchanged source evidence and
source lag while browser evidence residence increased approximately **2.2 s**.
The primary chart's **Space** shortcut then froze its presentation; playback
advanced **900 → 1022** while its exact painted view/evidence stayed fixed and
source lag increased **901.287 → 3505.862 ms**. The independent footprint and
tape continued advancing. The [frozen streaming screenshot](evidence/m8-streaming-frozen-recorded-1440.png)
is evidence of that separation, not a healthy-live throughput observation.

Phase receipts retain [clock projection](evidence/m8-clock-phase.json),
[readable charts](evidence/m8-chart-phase.json),
[compact workstation](evidence/m8-workstation-phase.json) and
[interaction/incident evidence](evidence/m8-interaction-phase.json).
They preserve their own source/assets and exercised results, including failed
attempts; they are not substitutes for a final matched-source acceptance run.

Source-clock lag measures the primary **painted** evidence against the comparable
source frontier. Browser evidence residence measures how long that identity has
remained displayed; a new response carrying old evidence cannot rejuvenate it.
Response residence, CPU draw, receive-to-paint opportunities and server
publication-to-ACK upper bounds are separate measurements. None is synchronized
exchange-to-screen latency, GPU completion or a freshness SLA. Paused replay and
silent source frontiers require this distinction.

### Executed project gate

`bash tools/check.sh -q --tb=short` passed **1,261 native tests** and
**97 frontend tests**, Ruff/format, mypy, ESLint, TypeScript and Vite production
build in **353.59 s**. The existing Starlette/httpx TestClient deprecation warning
remains visible. The intermediate compact-marker build passed frontend lint,
**97 tests**, TypeScript and Vite in **31.09 s**. A subsequent regression
reproduced the only available projected time disappearing at a clock-step
marker. After retaining that time with `*`, all **98 frontend tests**, lint,
TypeScript and Vite passed in **14.06 s**. Final production assets are
`index-BOT8nKm2.js` / `index-BwIcajpp.css`; all three matched screenshots were
recaptured on this build at delivery **2057**, DPR **1.25**, with unchanged
geometry, native price quanta and readable row counts.
Independent displayed-age measurement review reported no findings; chart review
reported no remaining findings after memoizing the footprint-center scan.

The first full browser attempt passed **54**, failed **1**, and skipped the
network-only scenario in **392.78 s**. Its transient disconnected-text assertion
missed automatic recovery already showing Awaiting stream; invalidated panes/
actions remained safe. The corrected test asserts durable safety and arms the
replacement response before fault injection; its focused actual-socket recovery
run passed **1 test in 12.1 s**, plus lint. A focused correction is not a full
browser-suite result or a rendering/live-capacity claim.

The final production build's complete actual-backend Chromium suite passed
**55 tests with one expected network-only skip** in **6.4 minutes**.
The earlier failed attempt remains in the validation receipt; neither its
focused correction nor the earlier 55-pass build substitutes for this final run.

At recorded delivery **3733**, the [readable step screenshot](evidence/m8-clock-step-readable-1440.png)
shows **`09:47:18*`** followed by 09:47:20/21. The corresponding
[Inspector](evidence/m8-clock-step-inspector-1440.png) exposes exact native offset
**1000155 ns** and available projection bounds
**104486968960846–104489308325295 ns**, with retained-history truncation.
The marker preserves actual projected time; it does not invent earlier labels.

### Actual public-live observations

The [validation receipt](evidence/m8-validation.json) retains **40 samples at
each of 1280 × 800, 1440 × 900 and 1920 × 1080**, including real ready ↔
`owner_busy` / `last_completed_presentation` transitions. The fixed status
strip stayed **28 px** high; each viewport retained one unchanged plot geometry
and no horizontal overflow. Source clock:
`26f3aaa3-3ca9-4a67-8a07-319bdf73d621`. This is finite public-live evidence, not
sustained collector-capacity certification.

An initially held footprint origin of **77302.5** left a latest print at
**77272** outside its price range. Focused **R** requested a native origin of
**77264**, restoring eligible displayed flow while preserving the primary
origin and secondary preferences. This was held-price behavior, not a missing
feed. Use **R** or **More → Recenter price** on that pane when market activity
leaves its held range; no automatic price rescale was added.

Four-pane live screenshots on the earlier `index-EC_OstmG.js` /
`index-BwIcajpp.css` build, after explicit independent recentering:
[1280](evidence/m8-final-live-1280.png),
[1440](evidence/m8-final-live-1440.png),
[1920](evidence/m8-final-live-1920.png). All were visually inspected.
Ending samples had available native ages in all panes: heatmap approximately
**1.4–2.4 s**, footprint **1.1–3.0 s**, DOM **281–519 ms**, tape
**160–3049 ms**. These are observed ending samples across viewports, **not
benchmark percentiles or an SLA**.

The live [Inspector evidence](evidence/m8-clock-inspector-live-1440.png) contains
one initial native segment in that specific view, not an invented clock step.
These finite live observations precede the final time-plus-`*` marker format;
they are not screenshots of `index-BOT8nKm2.js`. Earlier `m8-after-live-*`
images additionally retain pre-compact-marker and held-price out-of-range
observations. The validation receipt pins builds per scenario.

### Final recorded rendering and displayed-age acceptance

The final same-tool, hash-bracketed acceptance runs completed successfully at
[1280 × 800](evidence/m8-render-1280-acceptance.json),
[1440 × 900](evidence/m8-render-1440-acceptance.json) and
[1920 × 1080](evidence/m8-render-1920-acceptance.json). Each report records
`completed`, `certified: true`, `budgets_met: true`, unchanged code and no
provenance errors. Chromium **145.0.7632.6** ran recorded **30-second 1× and 2×**
cohorts serially on the final `index-BOT8nKm2.js` build. Benchmark DPR is **1**,
distinct from the matched/manual screenshots' **1.25**. All six benchmark
screenshots were visually inspected. Tool SHA-256 is identical in all reports:
`59e1e3ac50e394fe578b63defc02f98a64bb03e3c13a8cb1ca97a8e0e6e98606`.
The serialized acceptance command completed in **412.42 s**; this includes
startup/preparation/cleanup, not just the measured cohorts.

| Viewport | Speed | Chart ACK samples | p95 publication→ACK ms | p95 receive→paint ms | p95 CPU draw ms |
|---|---:|---:|---:|---:|---:|
| 1280 × 800 | 1× | 55 | 117.757 | 50.9 | 2.0 |
| 1280 × 800 | 2× | 54 | 118.994 | 52.4 | 2.6 |
| 1440 × 900 | 1× | 55 | 122.727 | 51.1 | 1.9 |
| 1440 × 900 | 2× | 55 | 152.047 | 51.7 | 2.4 |
| 1920 × 1080 | 1× | 55 | 130.053 | 49.9 | 2.5 |
| 1920 × 1080 | 2× | 55 | 148.367 | 52.5 | 2.3 |

Every cohort met the unchanged **250 ms** publication-to-ACK budget. These are
aggregate chart round-trip upper bounds and browser paint opportunities, not
per-pane one-way latency, GPU completion or public-live capacity certification.
The benchmark explicitly selects a 30-second primary window and native price
quantum 10; it is not the default matched-screenshot preset. Missing retained
history remains hatched, and the independently held footprint price window
can lose eligible visible flow.

Displayed-age badges were sampled nominally every **500 ms**, separately from
render events. Available/unavailable counts below refer to source-clock lag.
Each nearest-rank p95 uses that metric's own valid finite observations, rounded
to one decimal: residence can remain measurable when source lag is unavailable.
Missing values are never zero or counted as fresh. Response residence is
chart-only (`—` for DOM/tape).
Exact samples, clock/evidence/view identities, transitions and all quantiles
remain in each report.

| Viewport / speed | Pane | Available / unavailable | Source lag p95 ms | Evidence residence p95 ms | Response residence p95 ms |
|---|---|---:|---:|---:|---:|
| 1280 / 1× | Heatmap | 56 / 0 | 2201.2 | 1058.5 | 1105.5 |
| 1280 / 1× | Footprint | 56 / 0 | 1968.6 | 1004.4 | 1048.1 |
| 1280 / 1× | DOM | 50 / 6 | 617.1 | 697.7 | — |
| 1280 / 1× | Tape | 56 / 0 | 686.6 | 507.8 | — |
| 1280 / 2× | Heatmap | 56 / 0 | 3249.5 | 1055.2 | 1071.4 |
| 1280 / 2× | Footprint | 23 / 33 | 3921.8 | 1195.0 | 1002.6 |
| 1280 / 2× | DOM | 48 / 8 | 1302.2 | 670.4 | — |
| 1280 / 2× | Tape | 56 / 0 | 823.0 | 250.2 | — |
| 1440 / 1× | Heatmap | 56 / 0 | 2209.0 | 1097.5 | 1057.4 |
| 1440 / 1× | Footprint | 56 / 0 | 2121.9 | 1362.1 | 1053.2 |
| 1440 / 1× | DOM | 52 / 4 | 721.6 | 751.1 | — |
| 1440 / 1× | Tape | 56 / 0 | 685.8 | 523.0 | — |
| 1440 / 2× | Heatmap | 57 / 0 | 3106.6 | 1033.9 | 1089.7 |
| 1440 / 2× | Footprint | 27 / 30 | 6521.3 | 2667.2 | 1097.2 |
| 1440 / 2× | DOM | 49 / 8 | 1300.3 | 751.5 | — |
| 1440 / 2× | Tape | 57 / 0 | 782.2 | 362.1 | — |
| 1920 / 1× | Heatmap | 56 / 0 | 1986.4 | 1261.3 | 1050.6 |
| 1920 / 1× | Footprint | 56 / 0 | 1912.5 | 1596.3 | 1039.1 |
| 1920 / 1× | DOM | 52 / 4 | 700.9 | 751.5 | — |
| 1920 / 1× | Tape | 56 / 0 | 775.4 | 602.7 | — |
| 1920 / 2× | Heatmap | 56 / 0 | 3241.6 | 1051.1 | 1104.2 |
| 1920 / 2× | Footprint | 26 / 30 | 6019.7 | 1757.8 | 1002.6 |
| 1920 / 2× | DOM | 47 / 9 | 1680.5 | 752.0 | — |
| 1920 / 2× | Tape | 56 / 0 | 580.9 | 346.0 | — |

All cohorts reported zero invalid ages and zero identity mismatches.
At 2×, footprints explicitly reported `eligible displayed evidence unavailable`
for **33/56**, **30/57** and **30/56** observations as activity left the held
price view; their available-sample quantiles cannot describe the missing
observations. DOM unavailable samples reported
`source clock skew / evidence ahead of highwater`: independently returned
ladder evidence was ahead of the comparable observed Frame frontier. Those
samples remain unavailable rather than mixing clocks or manufacturing zero lag.
Heatmap lag p95 spans approximately **1.99–3.25 s** despite **118–152 ms**
chart ACK p95. Rendering acceptance therefore establishes **no freshness SLA**.

### Retained attempts and measurement corrections

Earlier reports remain evidence, not rewritten successes:

- [Initial 1280](evidence/m8-render-1280.json) passed its workload, while
  [initial 1440](evidence/m8-render-1440.json) failed the strict API gate despite
  meeting timing budgets. An old Demo request survived a pre-cohort source
  replacement into teardown. Setup now resolves the verified recording's opaque
  catalog ID and opens its explicit source URL directly. Exact request-failed
  state also prevents reporting the same cancellation twice.
- [Direct-source 1440 attempt](evidence/m8-render-1440-final.json) failed its
  first cold catalog lookup at a newly introduced ten-second deadline; setup
  now uses the remaining existing **120-second startup deadline**, not a
  relaxed measured-cohort budget.
- [1920 bad-path attempt](evidence/m8-render-1920-verified.json) used an invalid
  capture path and produced no native runtime/cohorts. Its
  [corrected-path report](evidence/m8-render-1920-verified-capture.json) is
  separate; neither is silently substituted for final production acceptance.
- [1920 release attempt](evidence/m8-render-1920-release.json) failed the 1×
  strict gate when a post-window response reader remained until browser-context
  close; 2× passed. The request was issued **411 ms after** the measured window.
  Playwright response completion did not resolve the already failed request.
  Cleanup now races the exact request-failed event, removes its listener,
  navigates away and drains readers before closing the context. Known HTTP
  errors remain errors; genuine cohort failures remain fatal. Final acceptance
  reports have **zero pending response readers** after drain.

Node syntax and three real tool smokes passed. No native quality rule, source
history, queue/depth limit, API failure gate or **250 ms** rendering threshold
was relaxed. Final acceptance uses one corrected tool version and one final
production build at all three viewports; previous build-specific successes
remain historical rather than becoming final-head claims.

### Retained boundaries

Native missing/partial history, source-clock discontinuities, unknown fees/FX,
bounded depth and flow eligibility remain explicit. The known public-live
`analytical_queue_overflow` boundary remains a separate capacity limitation.
No 24-hour soak, uninterrupted live ingestion, execution readiness or profitable
signals are established by the redesigned interface.

## Unified candles, perpetual paper and positioning — resume handoff

Branch `unified-candles-perpetual-paper`, from `main` at `837fae0`. Core commit
`67ddab7` (native trade candles, isolated perpetual accounting). The integration
phase described here was resumed on 2026-11-11 after the previous session's
`unified-regression-migration` and `unified-perpetual-documentation` jobs were
cut off mid-edit. Commit, push, PR and merge receipts belong in the delivery
ledger once they exist; nothing below claims them.

### Reconciled interrupted work

| Interrupted job | State found | Resolution |
| --- | --- | --- |
| regression-migration 1 (`workspace.spec.ts`, `pane-evidence.spec.ts`) | Specs already migrated to explicit workspaces and to a `Pin DOM price <price>` control that did not exist in source | Implemented the control: `DepthLadder` `pinned`/`onPin`, `NativeCharts` `domPin` fenced by session fence, cleared by Esc, clock-scope reset and `terminal-clear-cursor`. Hover/focus stay transient |
| regression-migration 2–4 | Completed before the cutoff | Kept. One live freeze assertion compared query fills with archive fills; the query adds `consumed_levels_omitted`, so the assertion now states exactly that difference |
| documentation 1, 3 | Completed | Kept |
| documentation 2 (`API.md`, `CONTRACTS.md`) | All three edits had landed; no final report | Read for truncation and consistency with source; kept |

Integration defects fixed before the full gate (the full-gate failures below remained unresolved at that point):

- `useCandleHistory` (uncommitted phase-2 change) dropped held candles on a
  failed refresh, an `unavailable` refresh or a lost connection. Docs, the
  chart's stale warning and two specs require same-owner candles to be kept and
  labelled stale. Restored; a response from another source, cohort, market or
  generation still clears the series.
- `perpetual-paper.spec.ts`: `getByLabel` matched no `<select>` inside the
  `Field` label wrapper (now `getByRole('combobox')`, as `workflow.spec.ts`);
  the freeze scenario's `0.0001`-per-nanosecond funding liquidated the position
  before the 10,000-record bound could freeze paper (now `0.000000001`); the
  fork step toggled the form before the resumed run had rendered.
- `workspace.spec.ts` asserted the obsolete readiness wording `Unavailable`;
  it now asserts the warning state.

### Open interest and funding beside price

Implemented on the existing architecture, without new venue transport:

- `src/workstation/positioning.py`: `PositioningHistory` replay observer over
  perpetual `context` streams, minute bars on the admitted candle clock, bounded
  to 10,080 minutes per stream, checkpoint/restore; `positioning_series`
  aggregates to any of the 15 candle intervals with `session_bucket`.
- `POST /api/v1/positioning`; `PositioningRequest`; observer order is
  visuals, trades, positioning, context, alerts, paper.
- `PriceChart`: **OI & funding** toggle, two panes under volume on the shared
  axis, cursor-linked readout; `positioning-model.ts`, `positioning-api.ts`.
- Demo fixture context varies (open interest ramp, signed funding steps).
  Both memory descriptor and durable configuration now include
  `positioning_scenario=ramp-signed-v1`, so older constant-context demos cannot
  share the new scenario's source identity. Existing captures remain unchanged.
- Documented in `API.md`, `CONTRACTS.md`, `VISUALIZATION.md`, `TERMINAL.md`.

Data availability. In this codebase only `HyperliquidAdapter` has `reference`
and `context` channels, and `_select_live` subscribes context only for
Hyperliquid perpetuals of BTC/ETH/SOL/HYPE or the selected base (at most 8).
So live positioning is Hyperliquid's, shown as `related_underlying` when another
venue or spot is selected. Provider documentation is not proof of live access.
Hyperliquid's [subscription schema](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions),
Bybit's [ticker schema](https://bybit-exchange.github.io/docs/v5/websocket/public/ticker),
Bitget's [public positioning endpoints](https://www.bitget.com/docs/catalog/market/derivatives),
and Deribit's [funding history schema](https://docs.deribit.com/api-reference/market-data/public-get_funding_rate_history)
were consulted during resume. Remaining integration details need validation
before implementing any additional provider.

| Venue | Streaming open interest / funding | Historical |
| --- | --- | --- |
| Hyperliquid | `activeAssetCtx` (implemented): funding, open interest, mark, oracle | `fundingHistory` REST; no open-interest history |
| Binance USDⓈ-M | `<symbol>@markPrice`: mark, index, funding rate, next funding time. No open-interest stream | REST `openInterest` (current), `openInterestHist`, `fundingRate` |
| Bybit linear | `tickers.<symbol>` snapshots/deltas; omitted fields are unchanged; funding interval supplied in hours. Both-sided and single-sided OI fields are distinct | REST open-interest and funding history; do not assume every symbol funds every 8h |
| OKX swap | `open-interest`, `funding-rate`, `mark-price` channels | REST open-interest and funding-rate history |
| Bitget USDT-M | Ticker integration not implemented | v3 current and historical OI (base units, both/single-sided distinguished), plus funding endpoints; v3 is a separate integration from current adapter transport |
| Deribit perpetual | Ticker integration not implemented | Hourly history contains separate `interest_1h` and `interest_8h`; never annualize one using the other's interval |
| Coinbase, Kraken, KuCoin spot | Not applicable | Not applicable |

Natural extension, in order: (1) add a `context` channel to the Bybit, OKX,
Bitget and Deribit adapters that decodes their ticker into the existing
`AssetContext` (unit, funding interval and next-funding provenance per venue;
OKX open interest is in contracts); (2) widen the `references` selection in
`_select_live` to the selected cohort's perpetuals; (3) Binance needs REST
polling for open interest; (4) optional explicit venue backfill as a separate
source, like `venue_ohlcv`, never merged with observed bars. Geo-denied
metadata (Binance USDⓈ-M 451, Bybit 403 from this network) does not by itself
establish whether those streams are reachable.

### Remaining work

1. **Live verification is blocked, not done.** A second `serve --network`
   (port 8002) could not create a live session: the collector is a singleton
   and the running port-8000 server holds its lock
   (`another feed collector owns the singleton lock`, surfaced as HTTP 500).
   Cross-venue live chart/paper checks, live market switching, reconnect and
   live recording remain unverified for this branch, as does `/positioning`
   on real Hyperliquid context. The latest user instruction explicitly preserves
   the running port-8000 collector: do not stop or restart it for verification.
2. The prior full gate reported **18 failures / 1,894 passes**. The isolated
   clean `/tmp/lob-head-check` at `67ddab71043c88a2b42240ba72fd41f94a2c824d`
   reproduced **17 failures / 10 passes**. Repaired fixtures now deliver through
   native analytics/admission; retired prints are not resurrected. Capture
   shutdown gaps intentionally retire current trades: tests inspect the valid
   prefix before shutdown and prove complete raw/normalized replay parity,
   including after restart. Raw archived fills are compared with their explicit
   presentation-only omission field. **103 targeted Python tests passed**.
3. **Full repository gate passed:** 1,923 Python tests, 186 frontend tests,
   Ruff (94 files), mypy (44 files), ESLint, TypeScript and production build.
   **79 offline browser scenarios passed; 3 network-dependent scenarios skipped.**
   The existing Starlette/httpx deprecation and Lucide directive build warnings
   remain non-fatal. Publication must use the protected PR and its required
   `offline` check; receipts are available in the
   [branch PR](https://github.com/karisheen/Limit-Orderbook/pulls?q=head%3Aunified-candles-perpetual-paper)
   and merge history rather than inferred from local checks.
4. Preserve port 8000 on its existing running revision after merge; report the
   deployment distinction rather than claim the merged code is running there.

### Resume correctness review

- Sparse positioning observations use canonical larger-bucket starts, including
  calendar intervals; late unit changes cannot erase valid history.
- Clock transitions on any admitted stream clear incomparable context, so
  returning to an old clock cannot resurrect its earlier observations.
- Positioning is fenced to the displayed candle source. Inspection follows the
  candle timestamp; related-venue attribution stays explicit.
- Funding remains a fraction per declared interval. Simple annualization is
  `rate × (365 days / interval) × 100` percent; mixed/unknown intervals are
  labelled rather than silently scaled as the latest period.
- Context discontinuities suppress connecting OI lines. Missing buckets and
  missing fields are not drawn as zeros or joined.
- Actual narrow-browser inspection found the mobile 140px rule overriding the
  positioning plot's 320px minimum and clipping funding. The corrected final
  production build rendered both panes at **390px viewport / 320px plot height**
  with **no horizontal overflow**.
- Desktop browser inspection showed OI **1010 base / change -85**, then
  **1100 / -105** at the previous shared candle cursor. Funding **-0.00002 per
  hour** displayed **approximately -17.52% simple annualization**. Switching to
  Binance spot labelled Hyperliquid as a **related perpetual, not the selected
  instrument**; the 5m aggregation displayed OI **1010 / change 10**.

Temporary verification uses an owned offline server on `127.0.0.1:8003` with
`/tmp/flow-resume-verify-state`. Earlier probes used port 8001 and
`/tmp/unified-integration-state`; `/tmp/unified-resume-*` and
`/tmp/unified-positioning-*` probes are not repository deliverables.

## Decisions and next action

Use one local asyncio backend worker, exact immutable domain contracts, explicit quality gates, direct venue adapters, a React/TypeScript terminal, native Canvas heatmap, Parquet/raw compressed recording and SQLite metadata. Preserve the legacy entrypoint during cutover; do not duplicate analytics. Detailed decisions/contracts/backlog are in the accompanying M0 documents.

M8 implementation and local verification are complete; this supersedes the M7 next-action statement while preserving its historical audit above. Start with `README.md`, inspect exact source identity/quality and use **R / Recenter price** when activity leaves a held chart range. Use `READINESS.md` for separate correctness, UI/operations, research and execution boundaries. The next capacity work is investigation and measured remediation of the known live `analytical_queue_overflow`, not another browser reconnect or an inference from recorded rendering passes. The optional documented 24-hour soak remains unrun; Bybit live validation remains conditional on access. Funding/OI provenance, native returned-flow Fit limits, unknown fees/FX and research limitations remain explicit. No live exchange execution or external deployment is authorized or implemented. Git publication, remote CI and PR/merge outcomes require their own actual receipts; local evidence is not a claim of those outcomes.
