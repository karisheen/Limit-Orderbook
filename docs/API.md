# Local terminal API (M3–M5, version 1)

## Start

Install the repository's locked Python environment and build `frontend/` using its documented npm command. From the repository root:

```sh
python -m src.workstation.cli serve
```

Open `http://127.0.0.1:8000`. The default is offline: no network discovery, collector lock, exchange socket, recorder, or durable demo generation. It generates a complete **180-second multi-venue synthetic scenario** in a bounded immutable memory source, then feeds the same `ReplaySession`, projection, analytics observer and cost engine used by recorded replay. This deliberately sparse fixture is not a historical market recording or a full exchange book. The source descriptor says `synthetic: true`, `recording: false`; native exchange/source identity is not rewritten. Initial presentation consumes bootstrap/early deliveries and pauses; remaining scenario data is available through replay controls, not leaked into current windows.

`/health/live` is available while memory preparation runs. Source catalog status is `loading` until generation finishes. Creating a demo session waits for that preparation, not durable capture generation or verification. This removes the durable path from first-data startup; it is not a measured startup-time guarantee.

```sh
# Operator-authorized local paths become opaque managed HTTP source IDs.
python -m src.workstation.cli serve --capture data/workstation/CAPTURE_ID

# Explicit network opt-in: one shared live SubscriptionRegistry.
python -m src.workstation.cli serve --network
```

`serve --network` declares `preferred_source_id: 'live'`; offline startup declares
the managed demo ID. This preference does not create a live session, override
source readiness, or authorize network access on an offline server. The browser
uses URL `?source=<catalog ID>`, then an explicit saved source, then this server
preference. `http://127.0.0.1:8000/?source=live` explicitly requests public live
data; unavailable requests retain their error instead of substituting demo.
Live session creation still requires an exact native instrument ID. When no
instrument was explicitly saved for that source, the browser resolves one from
the source-filtered catalog, preferring accessible active Binance-global
BTCUSDT spot, then Hyperliquid BTC perpetual, then another active native entry.
It never sends a foreign demo instrument or weakens the API identity requirement.

`--capture` may be repeated. The catalog verifies each capture using the released `RecordingReader`; incomplete/corrupt/unfinalized captures become unavailable with reasons, never healthy. Each client opens its own verified reader on its dedicated owner thread. Readers, SQLite connections, caches and iterators are never shared between threads or clients. There is no `verify=False` terminal path. Catalog IDs are opaque and valid for this server lifetime; HTTP never accepts paths or SQL.

Each managed ID pins the verified immutable manifest plus directory/manifest filesystem identity. Client open compares both to its catalog pin, and each subsequent owner operation checks the filesystem identity before accessing reader caches/history. Removed/replaced/rewritten captures become explicitly unavailable rather than silently substituting a new capture under old metadata. ReplaySource declares `total_deliveries`; callers do not depend on a concrete memory batch-array implementation.

`--host` permits only `127.0.0.1` (default), `localhost`, or `::1`; `--port` defaults to 8000. `--max-sessions` defaults to 8 (maximum 32). Exactly one backend worker is required, including offline mode; conflicting `WEB_CONCURRENCY`/`UVICORN_WORKERS` fail explicitly. Live registry acquisition retains its independent OS singleton fence. Do not run a second live collector beside an active recorder process.

`--state-dir` defaults to `data/workstation-state`. The server opens worker-owned
`paper.sqlite3` (runs, commands and accounting archives), `journal.sqlite3`
(manual entries) and `research.sqlite3` (CLI attempts, evidence and reports).
Keep this directory to retain local state across restarts. The Python
`TerminalRuntime` constructor without `state_dir` instead uses temporary state;
that embedding default is not the `serve` CLI persistence policy. HTTP accepts
neither database paths nor research execution requests. Point the research CLI
at the same state directory; see [DEMO](DEMO.md).

The supported Streamlit entrypoint is a thin loopback client of these same APIs; see [LEGACY](LEGACY.md). It owns no independent collector or analytics engine. The FastAPI root serves `frontend/dist/index.html` and a traversal-safe `/assets` mount when built; otherwise root returns an explicit 503 directing the operator to build or use Vite. No arbitrary filesystem/static catch-all is exposed. Vite development origins on local port 5173 are allowed; production origins match the configured loopback port. Host validation rejects DNS rebinding names. Cross-site/unknown-origin requests are rejected, including WebSocket handshakes. This is a local trusted-user service, not an authenticated public deployment.

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
| GET | `/instruments` | `{version:'1',instruments: Instrument[],sources: Source[],preferred_source_id:string}`. Optional `source_id` query selects that source's exact metadata versions; use it for active presentation. The unfiltered union is discovery only and must not override replay/live session metadata. Server preference is a startup hint, not a readiness claim. |
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
| POST | `/ladder` | Current eligible native book grouped with explicit price quantum; exact side/cumulative base quantities and original-price quote notionals, ungrouped midpoint/spread summary, native step options and depth scope. Presentation freezing does not alter collection. See [VISUALIZATION](VISUALIZATION.md#ladder-and-proposal-risk). |
| POST | `/proposal-risk` | Hypothetical entry/stop, side and quantity or risk budget; explicit fee assumptions, native constraints and unavailable reasons. Neither an order nor guaranteed maximum loss. |
| WS | `/stream?session_id=...` | One socket per session. Full Frames at up to 5 Hz. Disconnect destroys the session; reconnection creates an independent session, never steals another tab's state. |

### Explicit visualization viewport origin

`VisualizationRequest` accepts optional `price_min`: an exact, finite,
nonnegative decimal **string**, 1–128 characters, subject to the shared native
decimal precision and magnitude bounds. Numeric JSON values are not accepted.
An explicit origin requires an explicit supported `price_step`, must
be aligned to that step, and cannot be combined with `recenter: true`. The
native visible lower bound is
`max(0, price_min + price_offset_rows * price_step)`, computed with exact Decimal
arithmetic. Unsupported or misaligned values are rejected, not rounded. A pan
whose derived origin exceeds the same bounded input domain is rejected before
publication; returned origins remain valid inputs for subsequent pinned queries.

This additive field controls a query viewport, not native price bins or retained
history. A caller resends its own origin and step to keep its viewport stable
when a sibling chart recenters; it does not create a stateful backend pane.
Omitting `price_min` retains the existing fitted-anchor/recenter semantics.
Session, generation, source-clock and selection fences, history/depth bounds,
and quality eligibility remain unchanged. Multiple panes within one browser
session share the selected instrument and source; independent viewports are not
independent feeds. See [VISUALIZATION](VISUALIZATION.md).

### Visualization receive-clock axis

Every `VisualGrid` (`version: '1'`) includes a required `time_axis` object. It is
complete even when `replace: false` and `columns` is empty; replace the previous
axis rather than merging it as a column delta. No request fields are added.

```ts
type ReceiveClockSegment = {
  id: string;
  start_ns: string;
  reference: Reference;
  cause: 'initial_pair' | 'wall_offset_change';
  offset_change_ns: string | null;
};
type ReceiveTimeAxis = {
  version: '1';
  basis: 'receive_mono_highwater';
  projection: 'piecewise_receive_pair_v1';
  source_identity: string;
  clock_id: string;
  scope_id: string | null;
  available_start_ns: string | null;
  available_end_ns: string | null;
  offset_change_threshold_ns: '1000000';
  segments: ReceiveClockSegment[];
  last_unapplied_pair: {
    admission_ns: string;
    reference: Reference;
    offset_change_ns: string;
    reason: 'nonadvancing_receive_pair';
  } | null;
  reasons: string[];
};
```

`Reference` is the unchanged exact native event reference: `session_id`,
`ingest_ordinal`, `event_index`, `exchange_event_wall_ns` (nullable),
`receive_wall_ns`, `receive_mono_ns`, and `clock_id`. Timestamps, ordinals, offsets
and counters remain decimal strings; offset changes may be negative. Segment
starts are strictly increasing admission boundaries, not replacement receive
timestamps. Compute `reference.receive_wall_ns + (t - reference.receive_mono_ns)`
using exact integer arithmetic. Coverage endpoints are inclusive and clipped to
the requested range and captured frontier; segment validity ends exclusively at
the next segment boundary. Never extrapolate outside coverage.

The axis clock agrees with `VisualGrid.clock_id`. Source identity is native
recording/demo/live provenance, not a browser identifier; `scope_id` is opaque
contiguous-epoch identity, **not** the reference's session ID. It distinguishes
A → B → A clock transitions and is null before receive evidence. No-book views
can still have clock coverage. Runtime source/generation/selection fences remain
required; the axis is not a freshness or financial eligibility substitute.

At an advancing original receive frontier, an offset difference of at least
1 ms creates a new segment. Smaller pair variation does not move established
labels. Late/equal-frontier conflicts are diagnostic only. Historical responses
omit future segments and unapplied-pair diagnostics, preserving original
predecessor anchors when clipping. Axis reasons are `receive_clock_unavailable`,
`receive_clock_history_truncated`, `requested_receive_clock_history_unavailable`,
`wall_offset_change`, `nonadvancing_receive_pair`, and `source_clock_changed`.
These do not change financial `reasons` or `complete_window`.
See [receive-clock projection](VISUALIZATION.md#receive-clock-display-projection)
for bounds, checkpoints, and replay reconstruction semantics.

### M5 consumer routes

All routes in this section are **POST** and require `session_id` (1–64 lowercase
hex characters) and decimal-string `generation` (at most 20 digits). Unknown
fields are rejected. These are local analysis/simulation mutations, not exchange
orders. Runtime checks generation both before and under ownership fences.

Unless stated otherwise, responses carry
`{version:'1',session_id,generation,source_identity,delivery_index,...}`.
`source_identity` is the native source fence, not the server-lifetime catalog ID;
domain delivery indices, clocks, rates, quantities and counters remain strings.
Consumer results are explicit queries, not extra full evidence on every Frame.

| Route | Additional request fields | Response |
|---|---|---|
| `/alerts/query` | `event_limit=20` (integer 1–100) | `snapshot`, `kinds:[{kind,unit}]`, `evaluation_scope:'last_delivered_batch'`, `presentation` |
| `/alerts/rules/put` | Alert fields below; also `event_limit` | Same as query |
| `/alerts/rules/remove` | Required `rule_id`; also `event_limit` | Same as query |
| `/alerts/evidence` | Exactly one `rule_id` or digit-string `event_id` (up to 20 digits) | Full bounded `evaluation`; 404 if absent from retained prefix |
| `/context` | `fx_rates=[]`, at most 16 | `snapshot:{instrument_id,now,watchlist,contexts,basis,reasons}` |
| `/paper/query` | `orders_limit=20`, `fills_limit=20` (each integer 1–100) | `snapshot` |
| `/paper/configure` | Required `balances`, `fees` (each at most 64); three latency fields below; query limits | New run's `snapshot` |
| `/paper/orders` | Required `side:'buy'|'sell'`, `quantity`; optional `instrument_id`, `order_type:'market'|'limit'` (default `market`), `limit_price`; query limits | `snapshot` |
| `/paper/cancel` | Required `order_id`; query limits | `snapshot` |
| `/paper/runs` | `limit=20` (integer 1–100) | `runs` summaries across stored sources |
| `/paper/archive` | Required `run_id`; query limits | `archived:true`, `archive_scope:'historical_accounting_not_current_replay_state'`, `snapshot` |
| `/paper/resume` | Required `run_id`; query limits accepted | A new-generation **Frame**, not a consumer snapshot |
| `/journal/query` | `limit=50` (integer 1–100), `all_sources=false`, optional `run_id` | `entries`, `scope`, `limit` |
| `/journal/put` | Required `rationale`; optional `entry_id`, `entry`, `stop`, `context=''`, `run_id`, `order_id` | Saved `entry` |
| `/research/query` | `limit=20` (integer 1–100), optional `attempt_id`, `trial_offset=0` (integer 0–50000), `trial_limit=50` (integer 1–200) | `{version,session_id,generation,attempts,attempt,config,source,report}` |

#### Alert rules and evidence

Required fields: `kind`, `direction:'above'|'below'`, exact-string `threshold`
and `rearm_threshold`. Kinds are `spread_shock`, `depth_shock`,
`large_executed_print`, `event_ofi`, `best_quote_imbalance`,
`persistent_displayed_liquidity` and `net_cost`. Optional `rule_id` (1–128
characters) updates a rule; omission creates an ID. Optional `instrument_id`
(1–512) defaults to selection; it must identify a unique currently observed book.

`window_ns='0'` and `cooldown_ns='0'` are nonnegative digit strings (up to 20
digits). Only `event_ofi` and `persistent_displayed_liquidity` require a positive
window; other kinds require zero. `depth_side='bid'|'ask'` defaults to `bid`;
`depth_bps='10'` must be positive and below 10000. Above rules rearm strictly
below their threshold; below rules rearm strictly above it. Thresholds are
absolute metric levels, not percent changes. Returned `kinds` supplies units.

`net_cost` additionally requires `sell_instrument_id` and positive
`base_quantity`; these two fields are forbidden for other kinds. `buy_fees`
and `sell_fees` default to unknown fee objects. `latency_bps='0'` and
`rebalance_quote='0'` are explicit nonnegative stress assumptions.

Fee objects contain optional `fee_bps`, `fee_provenance`, `fee_date` and
`fee_asset_id`. A known fee requires the first three together, finite
`0 <= fee_bps < 10000`, nonempty provenance (up to 256 characters) and a valid
ISO date. Omitted rates are unknown, never zero. Economics strings are bounded
to 128 characters; asset IDs to 256. These fee objects are also used by paper.

The engine bounds rules to 64, retained fires to 256, commands to 4096 and
supporting evidence collections to 200; oversized evidence is unavailable, not
silently treated as complete. Query returns the newest requested fires, all
current rules/evaluation summaries, `dropped_events` and
`presentation:{event_limit,events_omitted}`. Summaries omit references, metric,
prints, displayed liquidity, pair, trade and book evidence and expose
`evidence_available`; fetch `/alerts/evidence` explicitly for those records.
Evidence is a retained-prefix snapshot, not a continuously refreshed claim.

#### Context and FX

Each FX input has required `base_asset_id`, `quote_asset_id`, positive exact
`bid`, `ask`, `asof:{clock_id,mono_ns,wall_ns?}`, and `source`; optional
`conversion_fee_rate` and `assumed=true`. Clock IDs/sources are at most 256
characters, clock integers at most 24 digits. Quotes must be ordered and
conversion fees nonnegative and below one. Missing conversion fees do not imply
free conversion. Context uses the same native conversion/quality checks as costs.

Reference watchlist and context channels are independently qualified, each
bounded to 64 streams; the engine accepts at most 256 streams/64 instruments.
Related spot/perpetual candidates expose three basis rows (`mark`, `oracle`,
`mid`), bounded by 192 rows. Fields preserve source instruments, quote/settlement
identities, as-of, eligibility and per-field reasons. Missing fields/channels,
incompatible underlying identity/units, stale or skewed clocks and missing FX
leave basis unavailable. USDT, USDC and USD are not interchangeable.
`funding_cost_in_spot_quote` is the FX cost to acquire the reference amount,
**not funding-rate P&L**. Basis cost scope includes FX spread/conversion fees,
not trading fees, funding P&L, executable arbitrage or a realizable hedge.

#### Paper and journal semantics

Balance objects are `{venue,asset_id,available,provenance?,asof?}`; venue/asset IDs
and provenance are at most 256 characters. Default provenance is
`user supplied prefunded balance`; available amounts are exact nonnegative
strings. Fee objects add required `instrument_id`. Duplicate venue/asset balances
or instrument fee schedules are invalid. `feed_latency_ns`,
`decision_latency_ns`, `order_latency_ns` default to `'0'`, each nonnegative,
at most 20 digits and at most one day. Feed latency delays usable books;
decision plus order latency determines arrival. Funds are competed for at
arrival, not reserved at submission.

Orders use positive exact `quantity`; limit orders require positive
`limit_price`, market orders forbid it. Only supported spot/base-unit instruments
are fillable. Native constraints, funds, eligibility, dated known fees and
available unconsumed depth gate fills. Fees may use native base or quote, not a
third token; future-dated fees or unavailable arrival wall clocks exclude fills.
Marketable-limit and market orders are IOC: partial/unfilled remainders cancel.
Cancellation targets pending orders; there is no passive order queue or impact
model. `order_id`, `run_id` and journal IDs are 1–128 characters.

Paper snapshots expose `run,delivery_index,at,balances,orders,fills,positions,
reasons,assumptions,presentation`. Run configuration retains funds, fees and
latencies; metadata presentation includes instrument ID/version pairs rather
than full duplicated instruments. Run-list summaries omit configuration.
Orders/fills are the newest requested rows. Each presented fill includes at most
20 consumed levels and `consumed_levels_omitted`; snapshot presentation reports
order/fill limits and omissions. Exact fill fees, book key/revision/as-of and
native source clocks remain visible. Initial inventory basis and derived P&L
can be null; marks are gross best bid, not liquidation proceeds.

Archives are separately queried durable accounting transitions, including live
audit history; 404 means no archived transition. Opening a new client does not
auto-adopt an old run. Resume is replay/demo-only and must match native source
identity. It rebuilds a candidate from commands against an independent replay
projection at the current cursor; failure does not replace current state.
Successful publication pauses playback, replaces paper ownership, increments
generation and clears old presentation/ACK state. It does not import archived
future fills into a past cursor. Live histories cannot resume across ownership.

Journal rationale is 1–8192 characters, context up to 16384; optional `entry`
and `stop` are positive exact strings (up to 128 characters).
New entries capture native source/session, delivery/clock and instrument/venue.
A linked run must be active at this prefix; linked orders must also be visible
and match the selected instrument. Edits preserve original provenance and cannot
reassign run/order links. Query scope is `current_source` by default or
`all_manually_authored_sources` when requested. Manual notes are not causal
replay observations and can remain visible after rewind.

#### Persisted research reports and lifecycle

Research HTTP is read-only: run experiments with the CLI and inspect them here.
Without `attempt_id`, detail/config/source/report are null. Unknown attempts
return 404; running/failed attempts may have no report. A report retains all
trial outcomes in storage; `report.presentation` carries `trial_offset`,
`trial_limit`, `trial_total` for the returned page. Reports retain source/config/
observation/label/report hashes, exclusions and limitations. They are historical
artifacts, not current-prefix trading decisions. Catalog-worker report reads
hold the lifecycle action fence but do not block source delivery under the replay
owner lock. See [CONTRACTS](CONTRACTS.md) and [DEMO](DEMO.md).

Consumer calls serialize lifecycle actions and owner work; request cancellation
joins already dispatched work before releasing ownership. An aborted/failed
mutation may have committed and must not be blindly retried. Frontend
visualization queries and chart ACKs have independent ownership per caller:
superseding one caller's request does not cancel a sibling merely because both
use the same endpoint. Callers abort
inactive, hidden, disconnected or superseded work, fence session/generation/
selection and discard obsolete responses. Hidden panes, including siblings
hidden by a maximized pane, suspend queries; hidden browser documents also
suspend polling. These presentation controls do not pause shared collection.
Polling queries and socket reconnects are distinct from mutations: **workflow
mutations are never automatically retried**. Refresh and inspect persisted
native state before deciding to repeat an interrupted action.

### Source and Frame

`Source` contains `id`, `mode`, `label`, `status`, `synthetic`, `recording`, `duration_ns` (string or null), `streams` (presentation count), and `reasons`.

For managed captures, `recording: true` means **recorded source**, not a currently active recorder. Synthetic classification uses the demo producer's explicit fixture-version/hash configuration, never a substring in human-readable provenance (a public capture can say “no synthetic overlays”). Replay without demo markers is labeled recorded replay, not fabricated live data; its full manifest provenance remains authoritative.

Every Frame has:

- `version:'1'`, `type:'frame'`, `frame_id`, `session_id`, `generation`: frame ID and generation are decimal strings. Frame ID strictly increases per session; generation changes on selection, seek/reset and explicit paper resume. Clients discard reordered old generations/frames.
- `status`: `loading`, `paused`, `playing`, `seeking`, `ended`, `live`, `unavailable`, or `error`.
- `selection:{instrument_id}`, `source`, exact selected `book` or null, `analytics` (`AnalyticsSnapshot`) or null.
- `replay`: null for live; otherwise `{cursor:{delivery_index,ordinal,event_index},speed,status,total_deliveries}` with exact string counters.
- `health:{ready,reasons}` using centralized book eligibility, not transport activity.
- `telemetry` described below; `error` string or null.

Read-worker contention may retain one last completed book/analytics/replay presentation from the same session generation and instrument. It is explicitly diagnostic (`health.ready=false`, reasons `owner_busy` and `last_completed_presentation`), not newly validated market evidence. This keeps a slow historical query from erasing the tape/cursor or relabeling replay as live. Loading, seek, unavailable/error and generation changes never reuse that cache. Action ownership and fresh execution/analytics eligibility remain authoritative; the cache is not an execution input.

Frame `book` includes exact key, segment/epoch/revision, as-of and native Quality/Coverage, but carries only the first **100 levels per side**. Its `presentation:{depth_limit,bids_omitted,asks_omitted}` discloses transport truncation; native coverage counts still describe reconstruction, not displayed rows. Grouped ladder, costs, recording and analytics continue to use the full native book. Frame analytics retains every metric and its bounded supporting references, and carries the first **200 profile rows / 100 display-evidence records**, matching the observation surface's bounds. `analytics.presentation:{profile_limit,profile_omitted,evidence_limit,evidence_omitted}` makes omitted detail explicit; the full eligible native window still drives metric values. Tape remains core-bounded and subscription history labeled. Neither presentation prefix changes financial inputs or creates a second browser financial model.

Seek/reset first stop and join pacing, mark seeking, increment generation, and run complete observer reconstruction on the client's dedicated worker. While seeking, book/analytics/actions are unavailable rather than exposing partially rebuilt windows. Backward seek clears future observer/dedup state using `ReplaySession.seek`; no future history is returned. Speed changes preserve playing state; pause/step stop playback. Every API mutation is serialized by a client action fence and every reader/observer operation by its owner fence. Cancellation joins outstanding worker work before releasing ownership.

Rewind/reset clears the book-derived price anchor and reconstructs it from the available prefix. User-selected aggregation/time quantum remains a display setting; a later book's midpoint cannot leak into an empty or earlier replay viewport.

### Costs and explicit assumptions

Required input: `session_id`, `generation`, `side:'buy'|'sell'`, and **exactly one** string `base_quantity` or `quote_budget`. Optional `fee_bps`, `fee_provenance`, and ISO `fee_date` must be supplied together for a known assumed fee. No fee means unknown, not zero. A supplied rate applies explicitly to both legs, not a fabricated exchange schedule. `latency_bps` and `rebalance_quote` are exact nonnegative strings, defaulting to an explicitly user-scenario zero.

Additional optional inputs expose the same cost engine without implicit stablecoin conversion:

- `numeraire`: exact asset ID.
- `fx_rates` (maximum 16): `{base_asset_id,quote_asset_id,bid,ask,asof:{clock_id,mono_ns,wall_ns?},source,conversion_fee_rate?,assumed?}`. Values are exact strings, absent fee means unknown (explicit `"0"` is a zero-cost assumption), default assumed true. Freshness/clock/skew, orientation and costs are validated by core `FXRate`/comparison; old or wrong-clock assumptions are not refreshed automatically.
- `inventory` (maximum 64): `{venue,asset_id,available,provenance?,asof?}`. Exact string balance; optional same clock DTO. These are supplied prefunded assumptions, not fetched account balances or permission to transfer.

Response: `{version:'1',generation,estimate:ExecutionEstimate|null,matrix:PairEstimate[],reasons}`. The matrix is directed, including unavailable diagonal pairs, and compares the selected base's known books. It retains incompatible quote/market exclusions and never turns spot/perpetual or USD/USDT/USDC into one executable cohort without the engine's explicit valid rules/conversion. Budget comparisons use the actual selected-side filled base quantity, not rounded VWAP reconstruction. Requested/filled/unfilled quantities, accumulated cash, fees, consumed depth, eligibility, FX, inventory and stress are the core immutable result fields described in [COSTS](COSTS.md). No future execution/market-impact claim is made.

## Ownership, backpressure and instrumentation

A runtime has one shared live registry, one bounded catalog worker, one bounded
visual-query worker, and at most one ingestion/observer worker per active client.
Each client owns its selection/generation, independent ReplaySession/cursor,
analytics, visualization, context, alerts and paper observers plus history.
Paper/journal SQLite connections are owner-thread-local; research queries use
the catalog worker. Historical chart queries pin at most one immutable evidence
prefix per session and run outside the ingestion lock; cancellation joins before
releasing lifecycle ownership. See [Visualization](VISUALIZATION.md#ownership-bounds-and-incremental-delivery)
for snapshot accounting and source-clock fences. Memory source data is shared
immutably. Browser close never releases a recorder's independent lease.

Instrument catalog conversion and JSON encoding also run on the catalog worker,
not on the ingestion event loop. One encoded response is retained per runtime
and reused only for identical metadata and copied source-readiness/reason
snapshots. Changed versions, source scope or health produce a fresh response;
unknown source IDs still fail explicitly. Returning the encoded response avoids
a second recursive framework conversion of thousands of instruments.

Exact DTO conversion reuses shared immutable provenance within one conversion
only, with a bounded cache of dataclass field names. HTTP fields, exact scalar
encodings and reference bounds are unchanged; no mutable DTO cache is shared
between conversions. Native depth-band summation stops after crossing the
requested boundary on the already sorted side, without reducing book depth.

Creation reserves capacity privately and exposes a session ID only after initialization succeeds. Shutdown cancels and joins private initializers before disposing executors. Reaping checks active requests, action/owner fences and worker activity, then rechecks idleness under the lifecycle fence; completed requests refresh their idle deadline.

Live attachment installs a bounded temporary processed sink **before** acquiring leases. Registration and a baseline snapshot of already-completed registry state occur together without an await. Baseline books are reconciled against actual lease owner IDs; buffered real deliveries are then replayed in order before presentation becomes live. `AnalyticsEngine.initialize_books` seeds only exact existing book evidence for static metrics, not fictitious historical events, tape, OFI samples or complete window anchors. Thus a quiet existing healthy owner appears immediately, while its missing trade/window history remains honestly unavailable. Failed initialization removes temporary sinks and releases only the failed client's leases.

The live consumer receives `subscribe_processed(events, books)` at the **completed atomic source-message boundary**. It never samples `registry.books()` in a pre-publication callback or deferred callback. Offers retain only affected-key books (including explicitly scoped gap targets); a reset with an absent current book removes the previous epoch from the client map. The full current client-scoped map is then reconstructed in queue order for AnalyticsEngine. Events are loss-intolerant analytical input: each client's admission is capped at 256 batches and a 128 MiB accounting budget covering queued and in-flight event representation plus conservatively charged immutable book levels (not a measured process-RSS claim). Overflow marks that client unavailable, suppresses all analytical/cost action, and requires explicit reselection to establish a fresh history anchor. This is a client analytical loss, not a fabricated market event or a claim that the independent recorder lost data. Other clients/recording continue. Snapshot presentation may coalesce only after validated processing.

WebSocket output holds only one in-flight Frame and no unbounded output FIFO. A send stalled for five seconds evicts the client. Each session retains at most 64 pending ACK IDs. Unattached HTTP sessions expire after 60 idle seconds. Repeated disconnect/switch/close joins playback and client work; no lifetime session-ID quota accumulates. The server limits concurrent connections/requests to 128 and WebSocket messages to 4096 bytes with a bounded protocol queue.

An aborted WebSocket acceptance also releases the session, processed subscription and live leases; ownership cleanup begins at the handshake, not only after publisher tasks start. The regression reproduces a peer disconnect during actual ASGI acceptance with native live-source workers and verifies no session or collector owner remains.

WebSocket ownership cleanup is shielded from ASGI/AnyIO level-triggered cancellation while publisher tasks, in-flight owner work and live leases are joined. The connection cancellation itself is still propagated after cleanup; it is not swallowed or converted into success. A deterministic regression cancels a real ASGI stream during an owned history operation and verifies both complete client cleanup and survival of an independent headless live lease.

Telemetry separates:

- `queue_depth`, `queue_highwater`, `overflows`: per-client analytical admission measurements, including detached in-flight deliveries until the worker completes or cancellation joins it.
- `queue_wait_ms`, `observer_duration_ms`: oldest-delivery queue wait and wall duration of the current ordered owner batch (at most32 original atomic deliveries). These are current-runtime scheduling/processing measurements, not exchange age or per-event latency.
- `coalesced`: skipped validated selected-book revisions between emitted Frames, segmented by generation/epoch; `ack_expired` is separately bounded ACK eviction, not data loss.
- `ingestion`: live registry `SubscriptionStatus` snapshots including parent's actual processing observation, source queue high-water and capture state. Historical replay processing timestamps are preserved native evidence, never subtracted from the present monotonic clock.
- `published_mono_ns`: server presentation publication timestamp, not a replacement source timestamp.

After actual paint, the browser sends `{type:'ack',frame_id,browser_receive_to_paint_ms}`. The server reports `publish_to_ack_upper_bound_ms`: server-publication-to-returned-ACK round trip, a **conservative upper bound**, not exact one-way publish-to-paint latency. Browser receive-to-paint is a separate browser-clock duration. No cross-origin monotonic clock subtraction is performed. ACKs for unknown/expired IDs do not invent timing. Counts/durations are instrumentation, not a benchmark certification.

`ChartFreshness` separately describes the actually painted visualization view,
not the newest HTTP response or a WebSocket Frame ACK. Its same-clock source
lag and evidence/response browser residency are distinct from response
receipt and render/paint-ACK timing. A cleared canvas has no presented identity;
retained frozen pixels can accumulate source lag as observed same-clock
highwater advances. Recording timestamps are never subtracted from today's
wall clock or a different monotonic clock. See
[chart-specific paint acknowledgement](VISUALIZATION.md#chart-specific-paint-acknowledgement)
for the measurement boundaries.

## Verification entrypoints

This slice adds `tests/test_workstation_api.py` and `tests/test_workstation_api_runtime.py` covering full-source provenance/precision/readiness, independent clients, cursor-bounded history/reset, actual costs/fees/prefunding, all-or-none fee input groups, controls and invalid/stale selection, host/origin/payload/capacity bounds, WebSocket ACK/disconnect, raw/normalized memory parity, generic ReplaySource consumers, durable-reader thread ownership and capture identity changes, analytical overload invalidation, immediate source deliveries/quiet joins, private capacity reservations, initializer shutdown and active-owner reaping. Run with the project's locked Python and full existing replay/CLI suite; frontend principal browser workflows and real live singleton-safe smoke are integration-owner gates. No benchmark or test pass is claimed merely by this document.
