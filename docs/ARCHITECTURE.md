# Workstation architecture and decisions

Status: **M0 decisions; not a claim that M1–M6 are implemented.** The authoritative product brief is `LIMIT_ORDERBOOK_OMP_UPGRADE_PROMPT.md`. This increment preserves the runnable legacy application, records regressions and establishes a shared implementation contract. Acceptance work is tracked in [UPGRADE_BACKLOG.md](UPGRADE_BACKLOG.md); exact planned Python interfaces are in [CONTRACTS.md](CONTRACTS.md). Commands, actual checks and Git delivery belong in [OMP_UPGRADE_HANDOFF.md](OMP_UPGRADE_HANDOFF.md). Read-only architecture inspection found no applicable `AGENTS.md` in this checkout or its ancestors.

## Actual baseline audit

The audited starting point is `e7dceaf9492e4b5413ea3a972b66db7474b0232f`. The integration owner reports 35 baseline offline tests passing in Python 3.9 with a LibreSSL warning. That is baseline evidence, not a feed-integrity or trading-readiness certification. M0 provisioning and access/spike results are recorded separately by the integration owner; no timing result below is asserted without evidence.

Current data flow:

```text
crypto_lob.py (cached Streamlit service)
  -> OrderBookService (one mutable asset/venue subscription)
  -> daemon FeedWorker per venue -> requests / websocket-client
  -> direct adapter -> float LiveBook / make_snapshot
  -> lock-protected snapshots, status and bounded memory history
  -> Streamlit HTML ladder + Plotly charts/comparison
```

| Existing source | Observation and consequence |
| --- | --- |
| `crypto_lob.py` | Argumentless `st.cache_resource` service and global mutable subscription let independent browsers replace one another's requested asset/venues. Rendering is already separated from worker polling, a useful concept to retain. |
| `src/domain/models.py` | Float price/size, one ambiguous timestamp, partial identity; permissive row filtering. Duplicate prices, nonfinite values and crossed books lack validation. Current tests endorse some permissive behavior that must change deliberately. |
| `src/domain/symbols.py` | Static BTC/ETH/SOL/XRP/DOGE/ADA/LTC/LINK/AVAX/DOT mappings, including an unknown-venue USD fallback; no metadata/product status. |
| `src/domain/orderbook_service.py` | Daemon threads, generation guards, bounded history and stale status; stop signals without joins. Fallback becomes permanent polling; valid synchronized data is not the recovery criterion. Race-proof shutdown/ownership is absent. |
| `src/data/clients.py` | HTTP retry/jitter exists, but no Retry-After budget. Any WS frame counts as success before parsing; ack/heartbeat-only sessions can reset failure counters. |
| `src/data/adapters/base.py` | Generic absolute-size float-map reducer, default 200 levels, silent malformed-row skips and no initialization/sequence/checksum contract. Display-depth slider itself only slices presentation and does not currently redefine reconstruction. |
| `src/data/adapters/kraken.py` | WS v2 subscribes depth 25 but uses default 200-level reducer; checksum and exact payload symbol ignored. Scope-exiting levels can persist; update-before-snapshot is not explicitly prevented. |
| `src/data/adapters/bybit.py` | v5 spot snapshot/delta, `u` copied but not validated; no explicit service-reset or exact-topic validation. Do not fix by inventing contiguous IDs. |
| `src/data/adapters/binance.py` | Binance.US REST and partial depth20@1000ms snapshots only. No global Binance adapter. |
| `src/data/adapters/coinbase.py` | Coinbase Exchange REST level 2 only; inaccurate blanket auth assumption disables WS. It is not an Advanced Trade parser. |
| `src/data/adapters/kucoin.py` | Public REST level2_100 only, 2s polling, no WS bootstrap; preserve honestly labeled capability rather than pretend event continuity. |
| `src/ui/components.py` | Comparison includes all stored books regardless of quality and mixes USD/USDT into synthetic NBBO. Heatmap recomputes percentile bins across visible history, merges side quantities and lacks gaps/trades/stable color bounds. |
| Tests/dependencies | Three offline test modules cover basic normalization, fixtures and retry/status behavior, not the missing integrity contracts. Optional smoke script probes BTC REST only. Requirements were lower bounds, not a reproducible lock. |

No executed-trade pipeline, durable recording, replay, cost engine, paper ledger, journal, API/React frontend or signal-evaluation harness exists in the audited baseline. Baseline README's “No-crash guarantee” is not evidence and must be replaced with specific tested behavior.

## ADR-001 — One asyncio ingestion owner, exact shared core

**Decision:** modular Python backend, FastAPI, one asyncio process/worker initially; React/TypeScript/Vite presentation. Use frozen dataclasses, exact Decimal values and explicit wire helpers, not an event framework or distributed message bus. M1 implements only instruments/events/quality/book plus adapters and supervision/registry; recording, analytics and product consumers arrive with their milestones. Modules in CONTRACTS are ownership boundaries, not a requirement for empty packages or speculative abstractions.

```text
metadata catalog -> public transport -> raw envelope / ordinal
                                  -> pure venue decode
                                  -> venue book reducer / trade normalization
                                  -> quality + eligibility
                 bounded recorder <- events -> shared analytics / cost engine
                                             -> alerts / simulated ledger
                                             -> coalesced API presentation -> React / legacy view
raw or normalized capture -> replay clock ----^ same reducers and analytics
```

Physical subscriptions are keyed by venue, API family, instrument, channel, reconstruction depth and source aggregation/cadence. Registry leases are per client, reference-counted, fenced by generation/connection epoch and cancelled/awaited at last release. Recorder and research consumers own leases independently of browsers. One transport may multiplex several keys, but there is only one logical reconstruction owner per key. Multiple API workers are prohibited until a separate singleton collector provides explicit IPC ownership.

Bounded required-event queues never silently discard book deltas or analytical trades. Overflow invalidates state, emits a gap and recovers; recording overflow marks incomplete capture. Validated UI snapshots may be coalesced. Disk/query work and slow clients cannot block reconstruction. Readiness reports synchronized capabilities separately from process liveness. No Kafka, Kubernetes or mandatory Redis.

**Alternatives:** keep threaded shared mutable service (rejected: browser ownership/races and weak replay boundary); browser-owned collection (rejected: recording/analytics would depend on rendering); distributed collectors (unnecessary local complexity).

**Migration:** keep Streamlit runnable during cutover. Port its data access to the exact core when available, then use the same analytics results in both views; do not create a second indicator engine. Legacy source remains until replaced end-to-end; feature-parity terminal changes its status to explicitly supported legacy. Existing float contracts are not promoted into the new domain. No matching engine, credentials, wallet/signing, exchange execution or deployment is introduced.

## ADR-002 — Direct adapters selected; library reuse evaluated against exact contracts

**Decision:** retain and correct direct public adapters for the initial integration, rather than install multiple competing ingestion stacks. This is a correctness/contract decision, not a claim direct adapters are faster. The concrete M0 spike methodology is [SPIKE_METHOD.md](SPIKE_METHOD.md); integration-owned evidence distinguishes fixture/static-source checks from actual library execution and live access. A stdlib checksum/precision fixture and static inspection are not an end-to-end Cryptofeed or CCXT runtime evaluation. Reconsider one library only after it satisfies the same raw/precision/validation/replay fixtures at a pinned revision.

Primary sources were consulted during M0. Source pins used by the connector spike are Cryptofeed `61438078e330bc5277f30a4ef241de34b28fab76` and CCXT `a1bd1dd550c67db735107447b698a34a34c2efa6`; neither is adopted as a runtime dependency. The live documentation may evolve; record the exact revision and license again before adoption.

| Requirement | Direct venue adapters | Cryptofeed | CCXT Pro |
| --- | --- | --- | --- |
| Actual initial coverage | Existing US/Kraken/Bybit plus Exchange REST/KuCoin REST; add global Binance, public Advanced Trade and Hyperliquid explicitly. | README lists Coinbase, Kraken, Binance, Binance US, Bybit, KuCoin and Hyperliquid; channel/API-family correctness still requires inspecting each implementation. | Pro exchange inventory includes initial venues; runtime `has` capabilities and selected class/API family must be checked, not inferred from venue branding. |
| Book/trade API | Own exact raw decode, venue reducer and typed Trade envelope. | `FeedHandler.add_feed`, async `L2_BOOK`/`TRADES` callbacks; `OrderBook.book`, `delta`, `sequence_number`, `checksum`; normalized taker side and Decimal fields. | Python `watch_order_book`, `watch_trades`, `load_markets`, `un_watch_*`/close lifecycle; returns unified cached structures, not a guarantee of every original book event. |
| Validation | Every protocol rule and missing guarantee is explicit/testable; maintenance is our responsibility. | Book metadata and venue validation support are useful, but checksum presence alone does not prove enabled/complete validation. Need adapter-specific fixtures and scope checks. The brief's old `docs/book_validation.md` URL returned 404 during review. | Sequence/checksum checks are exchange implementation details; unified watch interface does not expose the complete quality/epoch contract. Must retain validation evidence rather than infer success from a returned book. |
| Precision/time | Capture bytes first; Decimal decoding, lexical checksum precision, nanosecond integer clocks and local ordinals. | README documents Decimal prices/sizes, but exchange/receipt timestamps are float seconds; callback boundary alone cannot recover original nanosecond/lexical/receive-order evidence. | Unified number conversion and raw handler hook require inspection before claiming original numeric precision. Formatting helpers are not proof of lossless JSON input. |
| Raw/record/replay | Capture original inbound bytes and HTTP bootstrap payloads; normalized events and reset boundaries under one schema. | Current README offers compressed pcap recording with metadata and replay. Useful functionality, but our manifest, epoch, gap, deterministic merge and checkpoint contracts still require integration. | Manual supports overriding `handle_message`; the documented hook receives a message object, so do not equate it with original transport bytes. `newUpdates=True` still uses an internal sliding cache; it is not a durable lossless recorder. |
| Burden/maintenance | Small dependency surface, highest venue protocol maintenance cost; retain useful fixtures/retry concepts. | Specialized feed library, Cython types/build considerations; current README requires Python 3.13+. Do not assume compatibility with the baseline Python 3.9 environment or quote upstream benchmarks as local results. | Broad exchange/REST/private-trading surface and transpiled implementations; useful reuse but more surface than a read-only collector needs. Explicitly disable/omit all private/execution APIs. |
| License | Independently implemented adapters do not import third-party connector code; venue data terms still apply to recordings. | **Current pinned-era LICENSE: AGPL-3.0-or-later plus section 7(b) attribution requirement.** Historical assumptions about permissive releases are unsafe. Adoption/distribution/network-use obligations need deliberate compatibility review; no code copied here. | **MIT**; Pro is now a free part of CCXT, not a separately assumed paid license. Retain copyright/license notices if adopted. |

Sources: [Cryptofeed README](https://github.com/bmoscon/cryptofeed/blob/61438078e330bc5277f30a4ef241de34b28fab76/README.md), [Cryptofeed LICENSE](https://github.com/bmoscon/cryptofeed/blob/61438078e330bc5277f30a4ef241de34b28fab76/LICENSE), [CCXT Pro manual](https://github.com/ccxt/ccxt/wiki/ccxt.pro.manual), [CCXT LICENSE](https://github.com/ccxt/ccxt/blob/a1bd1dd550c67db735107447b698a34a34c2efa6/LICENSE.txt). The manual specifically documents sliding caches, `newUpdates`, background subscriptions continuing until unwatch, and `handle_message` overrides. No library broad feature matrix substitutes for the following venue rules.

The connector spike's pinned Kraken source inspection adds concrete reasons not to adopt a unified callback blindly: Cryptofeed parses with `parse_float=Decimal`, passes `raw=msg` (a parsed object, not transport bytes), and has checksum handling plus a separate pcap recorder; CCXT's pinned Kraken `watchOrderBook.checksum` default is `False`, and `custom_handle_deltas` uses `safe_number` before resolving the maintained book. See immutable [Cryptofeed Kraken source](https://github.com/bmoscon/cryptofeed/blob/61438078e330bc5277f30a4ef241de34b28fab76/cryptofeed/exchanges/kraken.py) and [CCXT Kraken source](https://github.com/ccxt/ccxt/blob/a1bd1dd550c67db735107447b698a34a34c2efa6/python/ccxt/pro/kraken.py). These are source observations, not claims that either library was installed, exercised or benchmarked here.

### Initial adapter capability decisions

| Venue/API | Required interpretation and validation | Evidence source |
| --- | --- | --- |
| Coinbase Advanced Trade spot | Public `wss://advanced-trade-ws.coinbase.com`, no JWT required. Subscribe `level2`, receive `l2_data`; absolute `new_quantity`, zero deletes; sequence scope is per connection. Heartbeats keep quiet subscriptions open, not proof of valid book. Add `market_trades` with verified side semantics. Do not combine Exchange and Advanced schemas. Public USD/USDC aliases described by docs require exact identity treatment, not invented independent liquidity. | [Endpoints](https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/websocket/websocket-endpoints), [level2](https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/websocket/level2) |
| Kraken WS v2 | Whole-message updates, subscribed-depth truncation, top-10 CRC32 every update by default; exact decimal decode; initialization and resync gates. | [Checksum guide](https://docs.kraken.com/exchange/guides/websockets/book-checksum-v2) |
| Binance global spot | Prefer data-only REST `https://data-api.binance.vision` and WS `wss://data-stream.binance.vision:443`. Probe metadata, depth bootstrap, trades and streaming separately. Snapshot+diff bridge, 100ms where supported, deletions/duplicates/gaps, lifetime/rate limits. | [Data-only hosts](https://developers.binance.com/docs/binance-spot-api-docs/faqs/market_data_only), [stream/local-book rules](https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams) |
| Binance.US spot | Separate ID/configuration; existing partial top-N remains snapshot-only unless its diff protocol is independently verified. Visible fallback, new history/analytics segment, original venue preserved in recording. No testnet substitution or restriction bypass. | [US official API docs](https://github.com/binance-us/binance-official-api-docs) |
| Bybit v5 spot | New snapshots reset; service restart `u=1` replaces state. Absolute quantity updates and zero deletion. `seq` indicates generation ordering across book levels, not a promise that every update ID is consecutive. Level-1 repeated snapshots may retain `u`. RPI liquidity is excluded by feed. | [Orderbook docs](https://bybit-exchange.github.io/docs/v5/websocket/public/orderbook) |
| Hyperliquid public | `l2Book` bounded snapshot replacement, `trades`, `allMids` reference watchlist and `activeAssetCtx` context. Current docs: default slow depth20, optional fast depth5; actual selected cadence/aggregation must be exposed. Snapshot description is at least 0.5s since last push, not every underlying event. No book checksum/sequence guarantee supplied. Trade key `(time, coin, tid)` plus venue/namespace; verify taker-side semantics independently. | [Subscriptions/types](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions), [Info identity](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint), [health](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/timeouts-and-heartbeats), [limits](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits) |
| KuCoin existing REST | Retain public snapshot capability and supported asset discovery; label polling/degraded and do not claim exact event flow. Any added WS mode needs separate protocol validation. | Existing adapter is baseline evidence only; verify current KuCoin docs before changing its protocol. |

Global Binance is preferred only when dependencies for the requested mode are usable. Data-readable and user execution-eligible remain separate flags. Hyperliquid perpetuals, spot native token IDs/wrappers and DEX namespaces are distinct instruments; mids/marks/oracles are never executable best quotes. Snapshot-derived flow is explicitly named, never exact event-level OFI.

M0 live-access evidence locations are `docs/evidence/m0-access.json` and `docs/evidence/m0-hyperliquid-access.json` when generated by the integration owner. HTTP 200 or observing WS frames establishes endpoint access only: it does not establish synchronized reconstruction, checksums, complete trades, performance, a 30-minute soak, or future venue access. Pending tests remain pending, even with successful offline fixtures.

## ADR-003 — Exact data, explicit quality and comparable cohorts

**Decision:** immutable Decimal domain records; original bytes captured before parsing; separate exchange/receive wall/receive monotonic/process/publish times; stream identity, connection epoch and deterministic local ingest ordinal. CONTRACTS is the sole schema plan. Reject malformed/nonfinite/crossed inputs and unknown instruments, rather than silently repair them. Whole-message protocol semantics define duplicates and reset behavior.

Eligibility is centralized for ladder state, comparisons, alerts, replay and simulated fills. Transport alive, valid book, last change, healthy observation, coverage and source mode are independent facts. Receive minus exchange time is timestamp age, not verified one-way latency. Replay uses recorded receive order, not exchange-clock sorting.

Use “best observed comparable bid/ask,” not NBBO. Native USD/USDT/USDC and spot/perpetual cohorts remain separate; a converted view requires fresh side-appropriate conversion books and conversion fees. Quantity-aware comparisons walk both legs at a common feasible size and common numeraire, include known fee provenance/date and separate latency/rebalancing risk. Unknown inventory makes a view informational. No post-signal instant transfer or atomic-leg assumption.

## ADR-004 — Native Canvas2D heatmap, measured before expanding dependencies

**Decision:** use an independently implemented native Canvas2D tiled heatmap primitive with React HTML controls/tables, not thousands of SVG nodes or a candlestick chart masquerading as liquidity history. Native browser Canvas has no chart-package dependency/version to pin; record browser build and renderer revision in every benchmark. The concrete Canvas-versus-SVG M0 spike and inputs live with SPIKE_METHOD. This choice is conditional on measured dense-grid behavior; no FPS or latency is asserted here.

Reviewed alternative: TradingView Lightweight Charts has [Apache-2.0 license](https://github.com/tradingview/lightweight-charts/blob/master/LICENSE) and [custom series/series/pane primitives](https://tradingview.github.io/lightweight-charts/docs/plugins/intro), including custom Canvas rendering. It could supply candles and axes but is not itself the required historical liquidity implementation. It is not installed/adopted in M0, and no third-party example code/assets are copied. If chosen for auxiliary panes, pin an exact reviewed release and lockfile plus its license/NOTICE/attribution before integration; do not add an unpinned “latest.” WebGL is an alternative only if Canvas measured limits warrant its resource/context-loss complexity. Existing Plotly stays legacy-only.

Spike/acceptance plan: declare hardware/OS/browser/device pixel ratio, seeded dataset hash, grid shape, depth/venues/assets, burst rate and renderer commit; compare cold draw, incremental column update, pan/zoom/recenter, trade overlays and linked cursor. Record p50/p95/p99 frame/update latency, publish-to-render, long tasks, CPU/memory, cache allocations and input responsiveness at normal and 2x recorded peak. Run real Chromium at 1440x900 and 1280x800; inspect streaming/reconnect/empty/partial/replay screenshots, not a single static happy state. Preserve viewport while data arrives. M0 small draw timings do not certify the full terminal or GPU resource behavior. Keep the <=250ms p95 publish-to-render target explicit until measured.

### Stable grid specification

- Grid identity is `(instrument_id, source segment, metadata_version, grid_version, origin_wall_ns, time_bucket_ns, price_origin_ticks, price_bucket_ticks, quantity_unit, sampling_policy)`. Tick scale/rule is immutable within a grid; variable-price rules use a declared exact display quantum compatible with metadata. Never derive historical price bins from current min/max or percentiles.
- Price cell is `floor((price_ticks - price_origin_ticks) / price_bucket_ticks)`; time cell is `floor((recorded_receive_wall_ns - origin_wall_ns) / time_bucket_ns)`. Preserve recorded ordinal for deterministic intra-cell selection. If wall clock regresses, create a clock-discontinuity segment rather than imply time ran backward; replay retains it.
- Initial sampling policy: last valid observable book at each fixed sampling boundary, with sample-and-hold only while that stream remains healthy and within its freshness limit. Store per-side summed **base quantity** within each price cell at that sample, not a sum of all event sizes over time. Optional quote-notional views are explicitly labeled and computed from exact levels. Perpetual contract quantities use explicit conversion rules. A later time-weighted policy gets a different grid version.
- Missing/unhealthy time intervals have a validity mask and reason, distinct from observed zero size. Coverage outside subscribed depth is unknown, never zero. Gap/source/venue changes segment tiles and overlays; no connection-spanning interpolation.
- Color transfer function/legend and saturation bound are explicit persisted view settings. Streaming history cannot silently change the color of an old cell. User contrast changes are deliberate; user aggregation changes reproject from retained underlying history into a new grid ID. If full-resolution history is unavailable, display that limitation rather than fabricate finer bins.
- Maintain fixed-size time/price tiles, ring-buffer live columns and bounded LRU historical tiles; request only visible ranges plus small margins. Incrementally upload/draw changed regions. Hard cap tile count/bytes, canvas resolution/device ratio and overlay points; release resources when panes close. Coalescing presentation does not drop recorded raw/trade events. Pausing viewport does not pause recording.
- Render best bid/ask paths and executed trade bubbles as distinct layers with units, time/side/size tooltips; resting liquidity is not traded volume. Preserve missing spans and unknown aggressor side. Never overlay demo fixtures into live mode.

## ADR-005 — Files for event history, SQLite for small state

**Decision:** partitioned compressed Parquet normalized events/checkpoints, rotated compressed raw logs, DuckDB local queries and SQLite catalog/settings/journal/ledger metadata. Preserve exact decimal strings or validated fixed decimal scales in Parquet; never silently coerce mixed scales to float. Partition by session/date/venue/instrument/channel without tiny per-message files. Raw log records include exact bytes (lossless encoding), hashes, receive metadata and ordinal; compression choice/version is in manifest. No per-event synchronous SQL insert on the collector loop.

Writer batches to temporary files, flushes/fsyncs as appropriate, closes/verifies file counts/hash, atomically renames, then transactionally publishes catalog entries/manifest. On crash, reconcile uncommitted files, retain recoverable coverage and mark incomplete captures; never accept a filename alone as proof of completeness. Manifests carry schema/config/adapter/code revision, metadata versions, initial/recovery snapshots, reset/gap boundaries, merge order, counts/hash/coverage and fixture provenance/license. Queries read finalized ranges off-loop. Retention and low-disk thresholds are configurable; alert before exhaustion and stop/segment capture explicitly on failure. Slow disk cannot indefinitely block ingestion.

Replay from checkpoint restores complete reducer state, dedup/window anchors and virtual clock, then executes the same normalization/book/analytics rules. Seek clears all future information; speed affects pacing only. Raw replay verifies decoder behavior, normalized replay verifies downstream parity. No current REST request reconstructs missing past depth. Ship small legally redistributable recordings where allowed and explicitly synthetic edge fixtures; offline demo must start without network and remain visibly labeled.

Alternatives rejected: SQLite per-message event store (avoidable ingestion contention/poor analytical layout), in-memory-only history (not research/replay evidence), mandatory server databases/message brokers (unnecessary local operations).

## ADR-006 — Native code only for a measured pure hotspot

**Decision:** Python remains the only book/analytics implementation until profiling identifies a budget failure. Narrow possible PyO3 boundary: batch apply exact scaled-integer book updates/checkpoints or pure depth-walk/band-aggregation kernels. Keep network, supervision, ownership, clock, files, APIs and UI outside native code. No Rust/C++/WASM rewrite as a prerequisite and no generic upstream benchmark justification.

Acceptance before native selection: retain original Python benchmark, identify CPU/queue/allocation bottleneck on declared fixture/hardware, demonstrate the smallest replacement meets the measured need and passes identical state/metric hashes, checksum/error/overflow behavior and replay seek/speed parity. Do not pay conversion/copy cost per price level if a batch suffices.

## Operational and delivery boundaries

Target initial budgets are p95 receive-to-valid <=25ms, p99 <=100ms and p95 publish-to-render <=250ms at declared normal load, plus responsive 2x peak replay bursts. Separate queue wait, processing, publish-to-render and timestamp age; disclose throughput, high-water queues, CPU/memory, gap/drop counts and capture completeness. Budget changes require retaining original measurements and rationale. At least BTC/ETH/SOL across available initial venues plus a deliberately slow/disconnected venue are required. A finite replay benchmark and approximately 30-minute soak are implementation gates when feasible; a runnable 24-hour command and truthful run status are required, not an invented pass.

Bind to loopback by default; validate API origins/inputs and bound history/query ranges. Native development is primary, optional Compose uses persistent data volumes. Use locked dependencies, offline CI, type/lint/test/build gates and opt-in live probes. No external deployment/services/contact, private keys, signing, real orders or withdrawals. Readiness dimensions remain independent: data correctness; UI/operational stability; research validity; future live execution (not implemented).
