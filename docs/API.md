# Local terminal API (M3–M4, version 1)

## Start

Install the repository's locked Python environment and build `frontend/` using its documented npm command. From the repository root:

```sh
python -m src.workstation.cli serve
```

Open `http://127.0.0.1:8000`. The default is offline: no network discovery, collector lock, exchange socket, recorder, or durable demo generation. It generates the complete **180-second / 24-stream synthetic scenario** in a bounded immutable memory source, then feeds the same `ReplaySession`, projection, analytics observer and cost engine used by recorded replay. This is not a historical market recording. The source descriptor says `synthetic: true`, `recording: false`; native exchange/source identity is not rewritten. Initial presentation consumes bootstrap/early deliveries and pauses; remaining scenario data is available through replay controls, not leaked into current windows.

`/health/live` is available while memory preparation runs. Source catalog status is `loading` until generation finishes. Creating a demo session waits for that preparation, not durable capture generation or verification. This removes the durable path from first-data startup; it is not a measured startup-time guarantee.

```sh
# Operator-authorized local paths become opaque managed HTTP source IDs.
python -m src.workstation.cli serve --capture data/workstation/CAPTURE_ID

# Explicit network opt-in: one shared live SubscriptionRegistry.
python -m src.workstation.cli serve --network
```

`--capture` may be repeated. The catalog verifies each capture using the released `RecordingReader`; incomplete/corrupt/unfinalized captures become unavailable with reasons, never healthy. Each client opens its own verified reader on its dedicated owner thread. Readers, SQLite connections, caches and iterators are never shared between threads or clients. There is no `verify=False` terminal path. Catalog IDs are opaque and valid for this server lifetime; HTTP never accepts paths or SQL.

Each managed ID pins the verified immutable manifest plus directory/manifest filesystem identity. Client open compares both to its catalog pin, and each subsequent owner operation checks the filesystem identity before accessing reader caches/history. Removed/replaced/rewritten captures become explicitly unavailable rather than silently substituting a new capture under old metadata. ReplaySource declares `total_deliveries`; callers do not depend on a concrete memory batch-array implementation.

`--host` permits only `127.0.0.1` (default), `localhost`, or `::1`; `--port` defaults to 8000. `--max-sessions` defaults to 8 (maximum 32). Exactly one backend worker is required, including offline mode; conflicting `WEB_CONCURRENCY`/`UVICORN_WORKERS` fail explicitly. Live registry acquisition retains its independent OS singleton fence. Do not run a second live collector beside an active recorder process.

The existing Streamlit entrypoint remains unchanged. The FastAPI root serves `frontend/dist/index.html` and a traversal-safe `/assets` mount when built; otherwise root returns an explicit 503 directing the operator to build or use Vite. No arbitrary filesystem/static catch-all is exposed. Vite development origins on local port 5173 are allowed; production origins match the configured loopback port. Host validation rejects DNS rebinding names. Cross-site/unknown-origin requests are rejected, including WebSocket handshakes. This is a local trusted-user service, not an authenticated public deployment.

## Exact JSON boundary

All routes below are prefixed `/api/v1`, except health. Native immutable domain models become plain JSON field objects (no persisted-wire tags), tuples become arrays, Decimal and **all domain integer/timestamp/ordinal fields become strings**. Presentation counters and measured `*_ms` durations remain JSON numbers. Null means absent/unavailable, never a financial zero. Instrument `base`, `quote`, and optional `settlement` remain full `AssetIdentity` objects, preserving `asset_id` and `ticker`; no ticker-only identity simplification.

The native persisted recording/wire schemas are unchanged. Derived financial output uses the explicit bounded `DERIVED_DECIMAL_POLICY`; market input validation remains stricter. Browser plotting/measurement numbers must never be submitted as economic inputs.

Direct runtime dependencies pin FastAPI 0.162.0, Pydantic 2.14.2, Starlette 1.10.0 and AnyIO 4.15.1 in the lock. Pydantic and AnyIO use MIT licenses; Starlette uses BSD-3-Clause. Their installed license notices were inspected; no framework implementation source was copied. AnyIO's shielded cancellation scope protects owned-worker cleanup, not request success. Starlette's test client currently warns that the pinned httpx 0.28.1 transport is deprecated in favor of httpx 2; this warning is retained, not suppressed.

Requests reject unknown fields, numeric JSON economics, nonfinite decimals, excessive lengths and invalid bounds. Bodies are bounded to 64 KiB including chunked requests. Session/generation mismatches return 409; unavailable sources/state return 503; unknown IDs return 404; invalid values return 422. Errors have `detail`. Framework validation errors additionally retain structured field locations.

## Routes

| Method | Route | Contract |
|---|---|---|
| GET | `/health/live` | Process liveness only, `{live}`. |
| GET | `/health/ready` | `{ready,reasons,sources}`; 503 until a ready source and synchronized active session exist, and whenever a required active session is ineligible. Reads completed immutable health evidence without publishing Frames, creating ACKs, changing access times, or treating a busy history/cost worker as a market-data fault. |
| GET | `/instruments` | `{version:'1',instruments: Instrument[],sources: Source[]}`. Optional `source_id` query selects that source's exact metadata versions; use it for active presentation. The unfiltered union is discovery only and must not override replay/live session metadata. |
| GET | `/sessions` | Bounded active summaries `{sessions:[{id,generation,mode,source_id,status}]}`. |
| POST | `/sessions` | `{mode:'demo'|'replay'|'live',source_id?,instrument_id?}` → complete Frame, 201. Demo defaults to Coinbase BTC if present, otherwise first unambiguous book. Live requires exact instrument. Explicit unavailable selection is rejected, not silently replaced. |
| GET | `/sessions/{id}` | Complete current Frame. |
| DELETE | `/sessions/{id}` | Idempotent `{closed:true}`; joins workers and releases only this client's live leases. |
| POST | `/subscriptions` | `{session_id,generation,instrument_id}` → Frame; exact-key selection and generation increment. |
| POST | `/replay` | `{session_id,generation,action,count?,speed?,delivery_index?}` → Frame. Actions `play`, `pause`, `step`, `speed`, `seek`, `reset`. Step 1–1000; speed exact string 0.01–100. Seek targets delivery index string. |
| GET | `/history` | Query `session_id,generation,start_delivery?,end_delivery?,limit?` → `{generation,cursor,events,truncated}`. End is clamped to current cursor. Limit 1–1000 events, at most 10,000 inspected deliveries and 4 MiB estimated result per request. Live history retains at most 4096 batches and 8 MiB accounted event representation; missing retained prefixes are marked truncated. |
| POST | `/costs` | Shared-core depth walk and directed comparison matrix; see below. |
| POST | `/visualization` | Exact bounded historical liquidity, trades, candles, footprints, profile and shared flow metrics; session/generation and explicit price/time grid. Half-open visible window, quality/retention reasons, incremental column token and chart publication ID. See [VISUALIZATION](VISUALIZATION.md). |
| POST | `/visualization/ack` | Chart-specific finite browser receive-to-paint timing for an issued view ID; returns server publication-to-ACK upper bound. Not an exchange latency or GPU-completion claim. |
| POST | `/ladder` | Current eligible native book grouped with explicit price quantum; exact side and cumulative base quantities. Presentation freezing does not alter collection. |
| POST | `/proposal-risk` | Hypothetical entry/stop, side and quantity or risk budget; explicit fee assumptions, native constraints and unavailable reasons. Neither an order nor guaranteed maximum loss. |
| WS | `/stream?session_id=...` | One socket per session. Full Frames at up to 5 Hz. Disconnect destroys the session; reconnection creates an independent session, never steals another tab's state. |

No alerts/paper/journal stub endpoints are exposed; those are M5 additions.

### Source and Frame

`Source` contains `id`, `mode`, `label`, `status`, `synthetic`, `recording`, `duration_ns` (string or null), `streams` (presentation count), and `reasons`.

For managed captures, `recording: true` means **recorded source**, not a currently active recorder. Synthetic classification uses the demo producer's explicit fixture-version/hash configuration, never a substring in human-readable provenance (a public capture can say “no synthetic overlays”). Replay without demo markers is labeled recorded replay, not fabricated live data; its full manifest provenance remains authoritative.

Every Frame has:

- `version:'1'`, `type:'frame'`, `frame_id`, `session_id`, `generation`: frame ID and generation are decimal strings. Frame ID strictly increases per session; generation changes on selection and seek/reset. Clients discard reordered old generations/frames.
- `status`: `loading`, `paused`, `playing`, `seeking`, `ended`, `live`, `unavailable`, or `error`.
- `selection:{instrument_id}`, `source`, exact selected `book` or null, `analytics` (`AnalyticsSnapshot`) or null.
- `replay`: null for live; otherwise `{cursor:{delivery_index,ordinal,event_index},speed,status,total_deliveries}` with exact string counters.
- `health:{ready,reasons}` using centralized book eligibility, not transport activity.
- `telemetry` described below; `error` string or null.

Read-worker contention may retain one last completed book/analytics/replay presentation from the same session generation and instrument. It is explicitly diagnostic (`health.ready=false`, reasons `owner_busy` and `last_completed_presentation`), not newly validated market evidence. This keeps a slow historical query from erasing the tape/cursor or relabeling replay as live. Loading, seek, unavailable/error and generation changes never reuse that cache. Action ownership and fresh execution/analytics eligibility remain authoritative; the cache is not an execution input.

Native `Book` includes exact key, segment/epoch/revision, bids/asks, as-of and Quality/Coverage. Analytics includes `metrics`, `tape`, `profile`, `display_evidence` and unavailable reasons as defined in [ANALYTICS](ANALYTICS.md). Trade subscription history remains labeled in the core tape. Selection never reconstructs a second browser financial model.

Seek/reset first stop and join pacing, mark seeking, increment generation, and run complete observer reconstruction on the client's dedicated worker. While seeking, book/analytics/actions are unavailable rather than exposing partially rebuilt windows. Backward seek clears future observer/dedup state using `ReplaySession.seek`; no future history is returned. Speed changes preserve playing state; pause/step stop playback. Every API mutation is serialized by a client action fence and every reader/observer operation by its owner fence. Cancellation joins outstanding worker work before releasing ownership.

### Costs and explicit assumptions

Required input: `session_id`, `generation`, `side:'buy'|'sell'`, and **exactly one** string `base_quantity` or `quote_budget`. Optional `fee_bps`, `fee_provenance`, and ISO `fee_date` must be supplied together for a known assumed fee. No fee means unknown, not zero. A supplied rate applies explicitly to both legs, not a fabricated exchange schedule. `latency_bps` and `rebalance_quote` are exact nonnegative strings, defaulting to an explicitly user-scenario zero.

Additional optional inputs expose the same cost engine without implicit stablecoin conversion:

- `numeraire`: exact asset ID.
- `fx_rates` (maximum 16): `{base_asset_id,quote_asset_id,bid,ask,asof:{clock_id,mono_ns,wall_ns?},source,conversion_fee_rate?,assumed?}`. Values are exact strings, absent fee means unknown (explicit `"0"` is a zero-cost assumption), default assumed true. Freshness/clock/skew, orientation and costs are validated by core `FXRate`/comparison; old or wrong-clock assumptions are not refreshed automatically.
- `inventory` (maximum 64): `{venue,asset_id,available,provenance?,asof?}`. Exact string balance; optional same clock DTO. These are supplied prefunded assumptions, not fetched account balances or permission to transfer.

Response: `{version:'1',generation,estimate:ExecutionEstimate|null,matrix:PairEstimate[],reasons}`. The matrix is directed, including unavailable diagonal pairs, and compares the selected base's known books. It retains incompatible quote/market exclusions and never turns spot/perpetual or USD/USDT/USDC into one executable cohort without the engine's explicit valid rules/conversion. Budget comparisons use the actual selected-side filled base quantity, not rounded VWAP reconstruction. Requested/filled/unfilled quantities, accumulated cash, fees, consumed depth, eligibility, FX, inventory and stress are the core immutable result fields described in [COSTS](COSTS.md). No future execution/market-impact claim is made.

## Ownership, backpressure and instrumentation

A runtime has one shared live registry, one bounded catalog worker, and at most one worker per active client. Each client owns its selection/generation, independent ReplaySession/cursor, AnalyticsEngine and history. Memory source data alone is shared immutably. Browser close never releases a recorder's independent lease.

Creation reserves capacity privately and exposes a session ID only after initialization succeeds. Shutdown cancels and joins private initializers before disposing executors. Reaping checks active requests, action/owner fences and worker activity, then rechecks idleness under the lifecycle fence; completed requests refresh their idle deadline.

Live attachment installs a bounded temporary processed sink **before** acquiring leases. Registration and a baseline snapshot of already-completed registry state occur together without an await. Baseline books are reconciled against actual lease owner IDs; buffered real deliveries are then replayed in order before presentation becomes live. `AnalyticsEngine.initialize_books` seeds only exact existing book evidence for static metrics, not fictitious historical events, tape, OFI samples or complete window anchors. Thus a quiet existing healthy owner appears immediately, while its missing trade/window history remains honestly unavailable. Failed initialization removes temporary sinks and releases only the failed client's leases.

The live consumer receives `subscribe_processed(events, books)` at the **completed atomic source-message boundary**. It never samples `registry.books()` in a pre-publication callback or deferred callback. Offers retain only affected-key books (including explicitly scoped gap targets); a reset with an absent current book removes the previous epoch from the client map. The full current client-scoped map is then reconstructed in queue order for AnalyticsEngine. Events are loss-intolerant analytical input: each client's queue is capped at 256 batches and an 8 MiB accounting budget covering event representation plus conservatively charged immutable book levels (not a measured process-RSS claim). Overflow marks that client unavailable, suppresses all analytical/cost action, and requires explicit reselection to establish a fresh history anchor. This is a client analytical loss, not a fabricated market event or a claim that the independent recorder lost data. Other clients/recording continue. Snapshot presentation may coalesce only after validated processing.

WebSocket output holds only one in-flight Frame and no unbounded output FIFO. A send stalled for five seconds evicts the client. Each session retains at most 64 pending ACK IDs. Unattached HTTP sessions expire after 60 idle seconds. Repeated disconnect/switch/close joins playback and client work; no lifetime session-ID quota accumulates. The server limits concurrent connections/requests to 128 and WebSocket messages to 4096 bytes with a bounded protocol queue.

An aborted WebSocket acceptance also releases the session, processed subscription and live leases; ownership cleanup begins at the handshake, not only after publisher tasks start. The regression reproduces a peer disconnect during actual ASGI acceptance with native live-source workers and verifies no session or collector owner remains.

WebSocket ownership cleanup is shielded from ASGI/AnyIO level-triggered cancellation while publisher tasks, in-flight owner work and live leases are joined. The connection cancellation itself is still propagated after cleanup; it is not swallowed or converted into success. A deterministic regression cancels a real ASGI stream during an owned history operation and verifies both complete client cleanup and survival of an independent headless live lease.

Telemetry separates:

- `queue_depth`, `queue_highwater`, `overflows`: actual per-client analytical queue measurements.
- `queue_wait_ms`, `observer_duration_ms`: current-runtime queue waiting and worker-processing wall durations (worker execution includes real callback/reduction/scheduling work, not exchange age).
- `coalesced`: skipped validated selected-book revisions between emitted Frames, segmented by generation/epoch; `ack_expired` is separately bounded ACK eviction, not data loss.
- `ingestion`: live registry `SubscriptionStatus` snapshots including parent's actual processing observation, source queue high-water and capture state. Historical replay processing timestamps are preserved native evidence, never subtracted from the present monotonic clock.
- `published_mono_ns`: server presentation publication timestamp, not a replacement source timestamp.

After actual paint, the browser sends `{type:'ack',frame_id,browser_receive_to_paint_ms}`. The server reports `publish_to_ack_upper_bound_ms`: server-publication-to-returned-ACK round trip, a **conservative upper bound**, not exact one-way publish-to-paint latency. Browser receive-to-paint is a separate browser-clock duration. No cross-origin monotonic clock subtraction is performed. ACKs for unknown/expired IDs do not invent timing. Counts/durations are instrumentation, not a benchmark certification.

## Verification entrypoints

This slice adds `tests/test_workstation_api.py` and `tests/test_workstation_api_runtime.py` covering full-source provenance/precision/readiness, independent clients, cursor-bounded history/reset, actual costs/fees/prefunding, all-or-none fee input groups, controls and invalid/stale selection, host/origin/payload/capacity bounds, WebSocket ACK/disconnect, raw/normalized memory parity, generic ReplaySource consumers, durable-reader thread ownership and capture identity changes, analytical overload invalidation, immediate source deliveries/quiet joins, private capacity reservations, initializer shutdown and active-owner reaping. Run with the project's locked Python and full existing replay/CLI suite; frontend principal browser workflows and real live singleton-safe smoke are integration-owner gates. No benchmark or test pass is claimed merely by this document.
