# Local terminal API (version 1)

## Start

Install the repository's locked Python environment and build `frontend/` using its documented npm command. From the repository root:

```sh
python -m src.workstation.cli serve
```

Default startup performs no live discovery, collector locking, exchange socket
connection, recording or durable demo generation. It generates a complete
**180-second multi-venue synthetic scenario** in a bounded immutable memory
source, then feeds the same `ReplaySession`, projection, analytics observer and
cost engine used by recorded replay. This sparse fixture is not a historical
market recording or full exchange book. The source descriptor says
`synthetic: true`, `recording: false`; native identity is not rewritten.
Initial presentation consumes bootstrap/early deliveries and pauses; remaining
data is available through replay controls, not leaked into current windows.
Explicit `source="venue_ohlcv"` candle requests can fetch public REST history
without `--network`; the flag controls shared live discovery/collection, not
all outbound HTTP.

`/health/live` is available while memory preparation runs. Source catalog status is `loading` until generation finishes. Creating a demo session waits for that preparation, not durable capture generation or verification. This removes the durable path from first-data startup; it is not a measured startup-time guarantee.

```sh
# Operator-authorized local paths become opaque managed HTTP source IDs.
python -m src.workstation.cli serve --capture data/workstation/CAPTURE_ID

# Explicit network opt-in: one shared live SubscriptionRegistry.
python -m src.workstation.cli serve --network
```

`serve --network` declares `preferred_source_id: 'live'`; offline startup declares
the managed demo ID. This preference does not create a live session, override
source readiness, or enable live collection on a server without `--network`. The browser
uses URL `?source=<catalog ID>`, then an explicit saved source, then this server
preference. `http://127.0.0.1:8000/?source=live` explicitly requests public live
data; unavailable requests retain their error instead of substituting demo.
Live session creation still requires an exact native instrument ID. When no
instrument was explicitly saved for that source, the browser resolves one from
the source-filtered catalog. Within entries with available access evidence (or
all selectable entries when none has that evidence), it prefers Binance BTCUSDT
spot, then Hyperliquid BTC perpetual, then another streamable native entry.
It never sends a foreign demo instrument or weakens the API identity requirement.

Runtime-owned live catalogs seed 116 subscription candidates for 29 crypto assets
across Binance spot/USDⓈ-M and Bybit spot/linear before asynchronous REST discovery. They carry
`metadata_status="streaming_only"`, `status="unknown"`, null tick/lot sizes and
explicit bundled provenance. Active products and these unknown-status candidates
are streamable; suspended/delisted/unavailable products are not. Candidate presence
does not establish current listing, access or valid market evidence.

Only complete successful metadata families enrich the catalog. Successful families
retire absent products; failed families retain candidates and their failure evidence.
`VenueStatus.dependencies` records aggregate `metadata` plus `metadata:<api_family>`;
mixed success is `degraded`/`partial`. A metadata HTTP failure is not a WebSocket
failure. Source `reasons` retains these warnings without blocking candidate startup.
Exact-product probes separately retain `probed_instrument_id` and `metadata:product`;
their snapshot/trade/WebSocket evidence cannot certify other products or families.
Discovery is cancelled and joined at runtime shutdown.
See [adapter access and endpoints](ADAPTERS_ASIAN.md#metadata-and-identity).

`--capture` may be repeated. The catalog verifies each capture using the released `RecordingReader`; incomplete/corrupt/unfinalized captures become unavailable with reasons, never healthy. Each client opens its own verified reader on its dedicated owner thread. Readers, SQLite connections, caches and iterators are never shared between threads or clients. There is no `verify=False` terminal path. Catalog IDs are opaque; operator-configured IDs are valid for this server lifetime, while managed recording IDs are rediscovered from the state directory. HTTP never accepts paths or SQL.

Each managed ID pins the verified immutable manifest plus directory/manifest filesystem identity. Client open compares both to its catalog pin, and each subsequent owner operation checks the filesystem identity before accessing reader caches/history. Removed/replaced/rewritten captures become explicitly unavailable rather than silently substituting a new capture under old metadata. ReplaySource declares `total_deliveries`; callers do not depend on a concrete memory batch-array implementation.

`--host` permits only `127.0.0.1` (default), `localhost`, or `::1`; `--port` defaults to 8000. `--max-sessions` defaults to 8 (maximum 32). Exactly one backend worker is required, including offline mode; conflicting `WEB_CONCURRENCY`/`UVICORN_WORKERS` fail explicitly. Live registry acquisition retains its independent OS singleton fence. Do not run a second live collector beside an active recorder process.

`--state-dir` defaults to `data/workstation-state`. The server opens worker-owned
`paper.sqlite3` (runs, commands and accounting archives), `journal.sqlite3`
(manual entries) and `research.sqlite3` (CLI attempts, evidence and reports).
Managed captures live under its `recordings/` directory.
Keep this directory to retain local state across restarts. The Python
`TerminalRuntime` constructor without `state_dir` instead uses temporary state;
that embedding default is not the `serve` CLI persistence policy. HTTP accepts
neither database paths nor research execution requests. Point the research CLI
at the same state directory; see [DEMO](DEMO.md).

The supported Streamlit entrypoint is a thin loopback client of these same APIs; see [LEGACY](LEGACY.md). It owns no independent collector or analytics engine. The FastAPI root serves `frontend/dist/index.html` and a traversal-safe `/assets` mount when built; otherwise root returns an explicit 503 directing the operator to build or use Vite. No arbitrary filesystem/static catch-all is exposed. Vite development origins on local port 5173 are allowed; production origins match the configured loopback port. Host validation rejects DNS rebinding names. Cross-site/unknown-origin requests are rejected, including WebSocket handshakes. This is a local trusted-user service, not an authenticated public deployment.

## Exact JSON boundary

All routes below are prefixed `/api/v1`, except health. Native immutable domain models become plain JSON field objects (no persisted-wire tags), tuples become arrays, Decimal and **all domain integer/timestamp/ordinal fields become strings**. Presentation counters, the `/market-data` cursor's bounded `event` offset, and measured `*_ms` durations remain JSON numbers. Null means absent/unavailable, never a financial zero. Instrument `base`, `quote`, and optional `settlement` remain full `AssetIdentity` objects, preserving `asset_id` and `ticker`; no ticker-only identity simplification.

Native recording identities are unchanged. Paper's additive durable records have explicit legacy-shape migrations; unknown or otherwise missing fields remain invalid (see [CONTRACTS](CONTRACTS.md#durable-state-archive-resume-and-manual-provenance)). Derived financial output uses the bounded `DERIVED_DECIMAL_POLICY`; market input validation remains stricter. Browser plotting/measurement numbers must never be submitted as economic inputs.

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
| POST | `/market-data` | `{session_id,generation,after_delivery?,after_event?,limit?}` → exact cohort books and cursor-paged native trades/continuity boundaries; see below. |
| GET | `/recordings` | `{version:'1',recordings:[...]}`; managed capture status, active owner and replay source IDs. |
| POST | `/recordings/start` | `{session_id,generation,scope?:'instrument'|'cohort'}` → recording list plus `recording_id`; freezes owned live keys in the requested scope (default `instrument`). |
| POST | `/recordings/stop` | `{session_id,generation,id}` → recording list plus `recording_id`; owner-only finalization and verification. |
| POST | `/costs` | Shared-core depth walk and directed comparison matrix; see below. |
| POST | `/visualization` | Exact bounded historical liquidity, trades, candles, footprints, profile and shared flow metrics; session/generation and explicit price/time grid. Half-open visible window, quality/retention reasons, incremental column token and chart publication ID. See [VISUALIZATION](VISUALIZATION.md). |
| POST | `/visualization/ack` | Chart-specific finite browser receive-to-paint timing for an issued view ID; returns server publication-to-ACK upper bound. Not an exchange latency or GPU-completion claim. |
| POST | `/ladder` | Current eligible native book grouped with explicit price quantum; exact side/cumulative base quantities and original-price quote notionals, ungrouped midpoint/spread summary, native step options and depth scope. Presentation freezing does not alter collection. See [VISUALIZATION](VISUALIZATION.md#ladder-and-proposal-risk). |
| POST | `/proposal-risk` | Hypothetical entry/stop, side and quantity or risk budget; explicit fee assumptions, native constraints and unavailable reasons. Neither an order nor guaranteed maximum loss. |
| WS | `/stream?session_id=...` | One socket per session. Full Frames at up to 5 Hz. Disconnect destroys the session; reconnection creates an independent session, never steals another tab's state. |

`GET /instruments` returns a strong `ETag` and `Cache-Control: private, no-cache`.
`If-None-Match` revalidates that exact URL's previously received representation;
a match returns **304 with no body**, not an empty catalog. Instrument metadata,
source status/reasons and the preferred source all participate in the validator.
Unknown source IDs still return 404 before conditional handling. The catalog
worker owns a bounded **8-scope / 64MiB encoded-response cache**; root and
source-scoped queries do not evict each other on every poll. JSON encoding and
hashing remain off the event loop. The UI retains validators only within its
current lifecycle and exact request URL, reuses data only after a successful
304, and keeps refresh failures visible. Its polling cadence is unchanged.

### Native market-data delivery

Live requests capture one bounded immutable, owner-completed prefix, then
perform paging, valuation and DTO construction on the read worker without
blocking analytical ingestion. The captured clock, source identity, admission
and retention floors remain attached to that prefix; final lifecycle checks
reject obsolete or unavailable sessions. Replay readers remain thread-affine.
The HTTP market-data and session-frame routes encode completed JSON-safe DTOs
off the event loop without a second framework traversal.

`POST /market-data` requires the current `session_id` and decimal-string
`generation`. Optional `after_delivery` defaults to `'0'` (at most 24 decimal
digits), `after_event` to `-1` (integer −1–1,000,000,000), and `limit` to 1000
(integer 1–2000 returned trades plus boundaries, subject to atomic fan-out below).
Delivery zero requires event −1.

The response is:

```text
{version:'1',session_id,generation,
 cursor:{delivery:string,event:number},highwater:string,gap:boolean,has_more:boolean,
 retention:{floor:{delivery:string,event:number},
   stream_floors:{[stream_id]:{delivery:string,event:number}}},
 books:[...],instruments:[...],trades:[...],boundaries:[...],
 now:{clock_id:string,mono_ns:string,wall_ns:string|null}|null,
 display_frontier_ns:string|null,source_identity:string,
 time_basis:'receive_utc',display_time_reasons:string[]}
```

The cursor is an **exclusive (delivery, event offset) pair**, not a trade ID,
timestamp or ingest ordinal. Start with `('0',-1)`; pass the returned
`cursor.delivery` and `cursor.event` as the next request's `after_delivery` and
`after_event`. Paging can stop inside a multi-event delivery without replaying
earlier prints. Nontrade and out-of-cohort events also advance the cursor;
an empty delivery ends at event −1. Therefore a response with no trades can
still advance. An event offset beyond an inspected delivery's event array is
invalid, not silently clamped.

Only owner-completed canonical deliveries are inspected, in recorded total
order. `highwater` is the current replay delivery cursor or completed live
delivery index, never a future recording endpoint. `has_more` means a request
bound interrupted scanning; continue from the returned pair, including after
empty trade pages. Each request inspects at most 10,000 events and 10,000
batches, applies the combined trade/boundary limit, and bounds their estimated
representation to 2 MiB. One canonical event's fan-out boundary group is atomic:
it is deferred to the next page if needed, or admitted whole as the first group
even when it exceeds the requested limit or size estimate. This estimate is
not a total HTTP response-byte cap. Live history separately retains at most
4096 batches / 8 MiB of accounted events; replay reads verified source history
only through its current cursor.

`gap:true` means the supplied pair precedes the retained live boundary or its
delivery is ahead of `highwater`; scanning restarts at the available boundary.
It does not fabricate lost events or prove exchange continuity. Clear obsolete
trade history on a cursor gap; session/generation/source/instrument changes
invalidate all display caches. Do not join histories across these fences.
Trade-history resets preserve independent depth/OBI. Books are current snapshots
at the query frontier, not reconstructed snapshots at each trade-page cursor.

`retention.floor` is the effective raw/admitted-history inclusive floor;
`stream_floors` independently retire affected streams. Drop cached rows at or
before these exact cursor pairs **on every page**, even when `gap:false` and
already caught up. Do not erase unaffected streams or depth/OBI on a trade-only
retirement. The session-owned admitted history has independent 10,000-trade /
8 MiB bounds and is also pruned when live raw history retires its prefix.

`instruments` contains the selected native instrument and streamable same-base,
same-market peers from the session's streams, bounded to 16 products with
venue-fair selection. Quote and settlement identities remain separate.
`books` retains native quality, coverage, capabilities and provenance, with at
most 100 levels per side and
`presentation:{depth_limit,bids_omitted,asks_omitted}`. Each book also has
`eligibility`, evaluated for display at `now`, or null before clock evidence.
Ineligible last-known books may be present; receiving a book is not permission
to trade or a claim of full depth.

Live subscriptions keep the exact primary and one instrument per venue/market,
bounded to 16 products. Peers must share its exact quote identity or belong to
the canonical USD/USDT/USDC display family. Exact primary quote wins, then
USDT/USDC/USD and native ID; selected-market groups precede other markets.
Unrelated fiat quotes and similarly named/scoped assets are not interchangeable.
The browser applies the same display boundary to older captures too. This
indicative selection never changes native financial conversion requirements.

Each displayed level preserves exact native `price` and `quantity` and adds
`base_quantity` and `quote_notional`. Each trade is
`{trade,asof,stream_id,base_quantity,quote_notional,instrument_id,cursor,source_identity,display_time_ns}`,
where `trade` retains quantity units, taker side/provenance and
`is_subscription_history`, and `asof` is the original event reference.
`source_identity` is
`{session_id:string,segment_id:string,clock_id:string,connection_epoch:string}`:
the native event's continuity identity, not the terminal session/generation.
These financial fields are decimal strings or null, never plotting numbers.
Native base units and documented linear base-valued contracts use
`base_quantity = quantity * contract_multiplier`; documented inverse
quote-valued contracts use `quantity * contract_multiplier / price`.
For inverse contracts, `quote_notional = quantity * contract_multiplier`
directly, preserving the exact quote product rather than multiplying a rounded
base quotient back by price. Other supported valuations use
`quote_notional = base_quantity * price`, under the native Decimal context,
in the instrument's own quote asset. Unsupported contract valuation, or a
trade quantity-unit mismatch, yields null for both derived fields.
`reference_multiplier` is not a valuation conversion. No quote conversion or
settlement/P&L model is implied.

The additive `boundaries` array contains
`{instrument_id,stream_id,cursor,asof,kind,reason}`. `kind` is `gap`, `reset`,
`disconnect`, `source_change` or `rejection`; `reason` preserves the native
reason code. Only affected trade streams in the display cohort are emitted.
A native gap can fan out to its own stream and declared `affected_streams`,
sharing one canonical cursor; book-only controls do not reset trade studies.
Merge trades and boundaries in cursor order. Compare `source_identity` per
trade stream across pages, including transitions without an explicit boundary.
Restart affected trade continuity on either signal, without discarding
unrelated stream evidence or valid depth. Ordinary acknowledgments/heartbeats
are not trade boundaries. The top-level `gap` flag instead describes cursor
retention/frontier loss and requires a broader display-history reset.

Trades are delivered evidence, not individually quality-certified executions:
unknown aggressors stay unknown and subscription-history flags stay visible.
Use book quality/eligibility and coverage limitations alongside display studies.
`now` is the native source clock; replay uses its recorded monotonic highwater
and cannot expose events beyond its current delivery. Original exchange/receive
timestamps remain in `asof`. Never substitute browser wall time for replay
freshness or send display aggregates back as economic inputs.

`display_time_ns` is the exact admitted trade timestamp: the source's first
receive-wall anchor plus its same-clock receive-monotonic delta. It is not an
exchange timestamp or browser time. `display_frontier_ns` projects the source
monotonic highwater using that same origin; it may advance without trades and
is independent of the returned page cursor. Use it to close existing buckets
without inventing empty bars. Later admitted prints can revise a closed bucket:
closed denotes elapsed source time, not completeness. Discontinuities are
disclosed in `display_time_reasons`. For plotting milliseconds, divide exact
nanoseconds first (`Number(BigInt(ns) / 1_000_000n)`), then check the safe plotting
range; never round `Number(ns) / 1e6` across a bucket boundary.

The display bridge recomputes timeframe, tick/indicator and venue changes from
retained trades without restarting the native session; only a changed depth
cohort resets depth history. Busy mutations pause polling without clearing
same-owner caches or cursor. Retention does not certify freshness.

Status lag uses exact `receive_wall_ns - exchange_event_wall_ns` before
conversion to display milliseconds. Missing exchange timestamps,
subscription-history prints, negative differences or nonfinite projections
are unavailable (`—`), not zero. Positive differences still include clock skew.
**Recorded lag** describes original capture-time receipt, not replay delivery
latency; demo uses **Synthetic lag**. Neither measures browser paint latency.

### Managed recordings

`GET /recordings` returns:

```text
{version:'1',recordings:[{
 id:string,label:string,status:'recording'|'complete'|'incomplete'|'error',
 source_id:string|null,reasons:string[],session_id:string|null,
 scope:'instrument'|'cohort',instruments:string[],
 channels:[{venue:string,instrument_id:string,stream:string}]
}]}
```

`session_id` is the active owning terminal session, not the native recording
manifest identity; it becomes null after finalization and after restart.
An active recorder can already report `incomplete` or `error`, so status alone
does not determine ownership. Start/stop return this same list **plus**
`recording_id`, the ID affected by that mutation.

`POST /recordings/start` requires `{session_id,generation,scope?}` and a live
session. There are exactly two scopes:

- `instrument` (default): all currently owned keys for the selected primary
  instrument, including its book/trades and any owned reference/context channels.
- `cohort`: all currently owned native session keys, including reference/context
  channels. This is the native session cohort, not merely visible chart venues.

The scope and key set are fixed at start. `RecorderBinding` holds its own
leases; later display, instrument, cohort or scope-selector changes do not
retarget an active capture. Default instrument capture does not acquire or
require unrelated cohort channels. Attaching starts a fresh bootstrap/epoch on
the shared owners, preserving other leases rather than pretending a mid-book
join is complete. Pre-attachment loss remains recorded by existing captures;
it does not poison the new capture's completeness. Loss after attachment still
makes that capture incomplete.
Capture lifecycle operations remain serialized against selection and closure,
but recorder disk work and owner reconnects do not block analytical draining.

Rows persist `scope`, sorted native `instruments` IDs and `channels` summaries
(`stream` is the channel name, not a stream ID) in managed capture metadata for
restart discovery. Bounds are 24 instruments, 48 channel/key entries and 64 KiB
metadata; the native manifest retains the full subscription keys and evidence.
Scope metadata declares intended capture membership, not successful receipt:
missing channel evidence, gaps and incomplete captures are not made complete
by selecting either scope, stopping successfully or restarting the server.

Bounds are **one active capture per session, two active captures per server,
64 retained managed catalog entries, and a 128 MiB managed budget per capture**.
These are admission/storage bounds, not sustained-capacity or RSS guarantees.
Capacity rejects with 429; an already-recording owner gets 409. Non-live
sessions, scopes with no live streams or scopes exceeding native bounds get
422. Failed/incomplete rows count toward the catalog bound; this API has no
delete or automatic oldest-row eviction route.

`POST /recordings/stop` requires `{session_id,generation,id}`, with `id` exactly
32 lowercase hexadecimal characters. Only its active owner may stop it;
missing, already-stopped or foreign active IDs return 404. Stale generation
returns 409. A failed live feed remains stoppable. Finalization status and
reasons must be inspected even after HTTP success: stop can return an error
row. Only a complete, verified capture gets a non-null replay `source_id` and
is registered immediately in `/instruments`, without restart.

Closing the owning session (including its stream disconnect) finalizes its
captures; server shutdown finalizes all active captures before registry close.
Other sessions cannot adopt or stop them. Mutation cancellation is shielded
through completion: refresh the list before retrying an interrupted request.
The browser gives these durable mutations their own owner-fenced cancellation
scope rather than the ten-second analytical-query deadline. Reconnect/attachment
and replay validation can take longer; an interrupted client must inspect the
recording list instead of assuming the operation failed.
The server chooses opaque paths under `--state-dir/recordings`; browsers never
submit filesystem paths. Startup recovers and verifies managed entries from
that directory, rediscovers usable replay sources, and retains incomplete/error
reasons such as `capture_interrupted` or `capture_verification_failed` rather
than advertising interrupted history as healthy. Preserve the state directory
for restart discovery; embedded temporary runtime state is not durable storage.

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
| `/positioning` | Required `interval` (any of the 15 candle intervals); `limit=300` (integer 1–500) | Observed open-interest/funding `series` on the session-candle axis; see below |
| `/paper/query` | `orders_limit=20`, `fills_limit=20`, `funding_payments_limit=20`, `liquidations_limit=20` (each integer 1–100) | `capability`, `snapshot` |
| `/paper/configure` | Required `balances`, `fees`; optional `risk_models=[]`, `funding_models=[]` (each list at most 64); three latency fields below; query limits | `capability`, new run's `snapshot` |
| `/paper/orders` | Required `side:'buy'|'sell'`, `quantity`; optional `instrument_id`, `order_type:'market'|'limit'` (default `market`), `limit_price`, `reduce_only=false`; query limits | `capability`, `snapshot` |
| `/paper/cancel` | Required `order_id`; query limits | `capability`, `snapshot` |
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

#### Observed open interest and funding

`POST /api/v1/positioning` returns the history of the same admitted
`AssetContext` observations that `/context` shows only as a latest value. It is
session-observed, venue-reported context: not an executable price, an exchange
guarantee, account state or a paper funding input.

```text
{version:'1',session_id,generation,source_identity,delivery_index,
 instrument_id,interval,status:'ready'|'empty'|'unavailable',
 source:'session_context',time_basis:'receive_utc',fetched_at_ns,
 supported_intervals,bucket_alignment,partial:true,highwater,reasons,
 label:'venue_reported_positioning_not_executable',
 series:[{stream_id,instrument_id,venue,api_family,native_symbol,
   base_asset_id,quote_asset_id,relation:'selected'|'related_underlying',
   open_interest_unit:string|null,open_interest_unit_provenance,
   funding_rate_unit:'fraction_per_funding_interval',
   coverage_start_ns,coverage_end_ns,
   retention:{base_interval:'1m',max_minutes:10080,retained_minutes},
   bars:[{start_ns,end_ns,first_ns,last_ns,samples,closed,
     open_interest:{open,high,low,close,change}|null,
     funding_rate:string|null,funding_interval_ns:string|null,
     mark:string|null,oracle:string|null}]}]}
```

`series` holds every perpetual context stream of the session whose canonical
base asset equals the selected instrument's, selected first. A perpetual on
another venue (or the perpetual behind a selected spot product) is
`related_underlying`; it is never relabeled as the selection. Other assets are
excluded. No such stream yields `series:[]`, status `empty` and
`context_channel_not_subscribed`.

Bars use exactly the `session_trades` candle buckets, clock projection and
`fetched_at_ns` frontier, so a bar and a candle with equal `start_ns` describe
the same interval. Observations are stored as UTC-minute bars (at most 10,080
per stream) and aggregated to the requested interval: open-interest
`open/high/low/close` over the observations, `change = close - open` inside the
bucket, and the **last** observed `funding_rate`, `funding_interval_ns`, `mark`
and `oracle`. A field absent from later observations keeps the bucket's earlier
value; nothing is carried into an unobserved bucket and no bar is synthesized.
`closed` is a time boundary, not completeness.

Continuity controls add `context_discontinuities` and keep earlier bars. A
changed open-interest unit or an incomparable source clock clears that history
(`open_interest_unit_changed`, `source_clock_changed`). Replay seek/reset rebuilds
only the delivered prefix; rewinding to delivery 0 is `unavailable`. Obsolete
generations fail with 409 like other consumer routes.

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

`PaperConfig` retains balances, fees, feed/decision/order latency, core eligibility
`policy`, `risk_models` and `funding_models`; HTTP does not accept a policy override.
Risk inputs are `{instrument_id,leverage,maintenance_margin_rate,
liquidation_fee_rate,provenance,asof_wall_ns}`. Leverage is 1–1000; maintenance
is nonnegative and below `1/leverage`; liquidation fee rate is nonnegative and
below one. Funding inputs are `{instrument_id,mode,provenance,asof_wall_ns,
rate?,interval_ns?,first_wall_ns?,events?}`:

- `none` explicitly models no funding; no periodic fields or events are allowed.
- `periodic` requires signed `rate`, positive `interval_ns` and `first_wall_ns`,
  with no events.
- `events` uses at most 10,000 `{wall_ns,rate}` entries in strictly increasing
  order, with no periodic fields.

Rates have magnitude below one. Boundaries/events cannot predate the model's
as-of. Provenance is required, not `"unknown"` (HTTP maximum 4096 characters);
as-of/boundary/interval timestamps are digit strings of at most 20 digits.
All economics remain exact strings. Duplicate instrument models are invalid.
Missing funding is unavailable, not an implicit zero; risk and funding are
explicit assumptions, not fetched venue maintenance tiers or guaranteed rates.

Orders use positive exact **native-unit** `quantity`; limit orders require
positive `limit_price`, market orders forbid it. Supported spot/base-unit IOC
behavior is unchanged. Supported perpetuals use isolated, one-way signed native
exposure: buy opens long/reduces short; sell opens short/reduces long.
`reduce_only` cannot increase or flip exposure. Opposite orders close first,
then open only an eligible, collateral-funded remainder. Native lot/order bounds,
funds, fresh eligible books, dated known fees and available unconsumed depth gate
fills. Spot fees use native base or quote; perpetual fees use native settlement.
Future-dated fees or unavailable arrival wall clocks exclude fills. Market and
marketable-limit orders are IOC; partial/unfilled remainders cancel. Cancellation
targets pending orders; no passive queue or market-impact model is implied.
`order_id`, `run_id` and journal IDs are 1–128 characters.

Active paper queries, configuration, orders, cancellation and run lists include
`capability:{instrument_id,metadata_version,market_type,quantity_unit,
settlement_asset_id,supported,reasons,native_order_constraints}` for the exact
session-pinned primary, not latest discovery metadata. With no pinned primary,
identity/constraints are null, `supported:false`, reason `no_pinned_primary`.
`native_order_constraints` is the derived `native_order_constraints(instrument)`
result, not a rewritten captured descriptor. Fence by ID **and** metadata version.
Intrinsic support is not current fill eligibility, sufficient collateral or
verified venue access. Streaming-only metadata cannot authorize financial use.

Paper snapshots expose `run,delivery_index,at,balances,orders,fills,positions,
perpetual_positions,funding_payments,liquidations,reasons,assumptions,presentation`.
Run metadata presentation uses `instrument_metadata:[{instrument_id,
metadata_version}]`; durable runs retain full captured descriptors. Run lists omit
configuration. `balances[].available` is free collateral/inventory, not isolated
margin. Spot `positions` retain average-cost accounting; initial inventory basis
and affected P&L can be null, and marks are gross best bid.

Perpetual records preserve these defining fields:

```text
PaperPerpetualPosition:
 venue,instrument_id,settlement_asset_id,instrument,quantity,entry_price,
 initial_margin,isolated_collateral,realized_pnl,fees_paid,funding_paid,
 liquidation_fees_paid,debt,unrealized_pnl,mark_price,maintenance_margin,
 equity,shortfall,liquidating,reasons,mark_source
PaperFundingPayment:
 instrument_id,settlement_asset_id,at,rate,quantity,mark_price,amount,
 provenance,reasons
PaperLiquidation:
 instrument_id,settlement_asset_id,at,requested_quantity,filled_quantity,
 remaining_quantity,fee_amount,debt,reasons,fill_id
```

Money is in `settlement_asset_id`; quantity is signed native exposure. Null
valuation/payment fields mean unresolved, never zero. Positive funding `amount`
debits isolated collateral. Liquidation `fee_amount` is the protective penalty;
the linked `PaperFill` retains ordinary trading fees. `mark_source` is
`eligible_executable_side_book_proxy_not_exchange_mark`: long bid/short ask,
not an exchange mark or guaranteed exit. Supported settlement, P&L, liquidation
and debt rules are detailed in [CONTRACTS](CONTRACTS.md#paper-funds-fees-time-and-consumed-liquidity).

Orders, fills, funding payments and liquidations are sliced **before**
serialization to the newest requested rows. `presentation` supplies
`orders_limit/orders_total/orders_omitted`, `fills_limit/fills_total/fills_omitted`,
`funding_payments_limit/funding_payments_total/funding_payments_omitted`,
`liquidations_limit/liquidations_total/liquidations_omitted`, and
`fill_levels_limit:20` (JSON number counters). Each fill exposes at most 20
`consumed_levels` plus `consumed_levels_omitted`. These are presentation windows,
not a claim that omitted accounting vanished; archive storage retains full
bounded accounting. Exact fees, book key/revision/as-of and source clocks remain.

Funding catch-up is bounded before monetary mutation. `funding_catchup_limit`
freezes paper at its last valid accounting prefix; snapshot `reasons` contains
`paper_frozen` and the cause, and `delivery_index` remains that accounting
frontier even while charts continue. Frozen queries do not advance accounting;
time/order observation is rejected rather than skipping payments. Explicit
configuration creates a fresh child run using **current** streams, pinned
metadata, source time/delivery and books, not stale frozen books or an abandoned
cohort. It does not repair or mutate the old run.

Archives are separately queried durable historical accounting transitions,
including live audit history; 404 means no archived transition. Archive queries
are bounded presentations, not current replay state or a source of future fills.
Opening a client does not auto-adopt an old run. Resume is replay/demo-only,
requires the same native source identity, and rebuilds an independent candidate
from commands at the current cursor. A valid historical prefix **may resume**
even when future commands exist: `future_commands_fork_required` keeps further
order/cancel mutations fenced until the timeline catches up or configuration
creates a child run. Genuine frozen/inactive/incomplete candidates are rejected
atomically with `paper_resume_incomplete_accounting_prefix`; current projection
and durable historical accounting are not replaced by failed staging. Successful
publication pauses playback, replaces paper ownership, increments generation and
clears old presentation/ACK state. Live histories cannot resume across ownership.

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
- `selection:{instrument_id,metadata_status,metadata_version}`, `source`, exact selected `book` or null, `analytics` (`AnalyticsSnapshot`) or null. Metadata fields describe the session-pinned book descriptor, not the latest discovery catalog; both are null when no descriptor is selected.
- `replay`: null for live; otherwise `{cursor:{delivery_index,ordinal,event_index},speed,status,total_deliveries}` with exact string counters.
- `health:{ready,reasons}` using centralized book eligibility, not transport activity.
- `telemetry` described below; `error` string or null.

Streaming-only data can support display/flow and durable recording/replay, but
comparison, exact costs, proposed risk and paper/order validation reject
`instrument_metadata_unverified`. Background discovery never relabels old evidence.
Book and trade leases may pin different versions; book presentation resolves its
own key/version, while trades retain their own StreamSpec metadata. Shared streams
adopt newer metadata only after their existing session/recording leases release.
Close other owners and reconnect/reselect before expecting the verified version.

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

## Unified native candles and explicit venue history

`POST /api/v1/candles` takes strict `CandleRequest` fields:
`{session_id,generation,interval,limit?,source?,instrument_ids?}`.
`source` defaults to `session_trades` in **live, demo and replay**; the only other
value is explicit `venue_ohlcv`. `interval` is case-sensitive, 1–8 characters,
matching `^[1-9][0-9]*[mhdwM]$`; `limit` is an integer 1–500, default 300.
`instrument_ids` defaults to `[]`, at most 16 unique nonempty strings of at most
256 characters. Empty/omitted means primary only. An explicit cohort must include
the exact selected primary and belong to the active pinned same-base,
same-market session cohort. Foreign, duplicate or incompatible IDs are rejected,
not silently discarded. Canonical response order is primary first, then sorted
peer IDs. `venue_ohlcv` requires exactly the primary, never aggregate substitution.
Unknown fields/URLs, numeric economics and malformed intervals are rejected;
syntactically valid unsupported intervals return `status:"unavailable"` and
actual `supported_intervals`, not a replacement timeframe.

Native session trades support all fifteen intervals:
`1m,3m,5m,15m,30m,1h,2h,4h,6h,8h,12h,1d,3d,1w,1M`.
Fixed intervals floor UTC nanoseconds against Unix epoch; `1w` therefore has a
Thursday 1970-01-01 origin, not Monday alignment. `1M` spans the UTC first day
of a calendar month to the next first, never a fixed 30-day approximation.

All responses contain:

```text
version,session_id,generation,source_identity,instrument_id,instrument_ids,
scope,venue_label,quote_asset_ids,interval,status,source,venue,time_basis,
fetched_at_ns,supported_intervals,candles,reasons,volume_unit,partial,
coverage_start_ns,coverage_end_ns
```

`scope` is `primary` or `aggregate`; native aggregate `venue` is `"aggregate"`
and `venue_label` lists contributing cohort venues, not just the primary.
`quote_asset_ids` preserves native quote identities. Cross-quote aggregate
prices are indicative with `indicative_quote_family_no_conversion`; no FX or
financially executable merged book is implied. `status` is `ready`, `empty`
or `unavailable`. `source_identity` is the native session source fence.

Each candle contains `start_ns`, exclusive `end_ns`, exact-string
`open,high,low,close,volume`, and boolean `closed`. `volume_unit` is `"base"`,
never quote turnover. `closed` means only that source time reached `end_ns`.
It does not certify completeness or prohibit later retained prints revising a
bucket. No empty buckets are synthesized.

### Retained session-trade candles

`session_trades` uses `time_basis:"receive_utc"` and the same admitted-trade
history and exact `display_time_ns` projection as `/market-data`, not selected-only
visualization history or a browser venue socket. Open/close follow native
delivery order; unrelated clocks, future, unadmitted, subscription-history and
unknown-base-size prints cannot fabricate bars/volume. Wall-clock corrections
do not rewrite the origin; the source-monotonic frontier determines closure.
`fetched_at_ns` is that projected frontier (string `"0"` when unavailable), not
request wall time. Source/generation/selection/cohort/interval changes fence old
responses; seeks rebuild only the admitted prefix.

Additional native fields are `highwater` (delivery string),
`bucket_alignment:"utc_epoch_floor"|"utc_calendar_month"`,
`missing_instrument_ids`, and
`retention:{max_trades:10000,max_bytes:8388608,retained_trades,retained_bytes,truncated}`.
Retention counters describe the session-wide admitted history, not only returned
bars/cohort. Live raw-history rollover also prunes this history. Native results
always disclose `partial:true`, `session_history_partial` and
`retained_session_trades_only`; truncation, discontinuity, missing receive UTC
and cohort members without contributing admitted trades have explicit reasons.
Partial coverage is not silently upgraded because a bar closed. Coverage bounds
are nullable first/last contributing retained trade timestamps of the returned
bars, not bucket edges. Native chart operation needs no REST backfill.

### Explicit venue OHLCV

`venue_ohlcv` is a separate request, not a live default or a replay-prefix
reconstruction. It uses `time_basis:"exchange_utc"` and native exchange bucket
starts; multiday/week alignment must not be assumed identical to session epoch
flooring. Its coverage defaults to first returned bucket start through the lesser
of the last bucket end and fetch time. It discloses `native_history_may_have_gaps`
and `closed_is_time_boundary_not_completeness`.

Supported families include Binance global spot/USD-M and Bybit spot/linear,
plus supported native Binance.US, Coinbase, Kraken, KuCoin and Hyperliquid
products. Exact family/market/interval support is returned, not inferred from
venue name or catalog presence. Unsupported history stays unavailable; it is
never substituted from another venue, instrument or API family. This request
does not prove any provider is currently reachable. Native access restrictions
and transport Retry-After/circuit policy remain authoritative.

One attempt per query, four concurrent queries, no admission wait queue, at most
32 successful exact-instrument/metadata/interval/limit cache entries, five-second
TTL, at most 500 candles and a 2 MiB JSON parsing bound apply. Transport retains
its 32 MiB response cap. Overload yields `candle_capacity`; network/access/rate
failures have explicit unavailable reasons. Cancellation releases capacity without
a background fetch. No observer/lifecycle lock is held during HTTP; source and
generation are checked before publication. Book health is not a prerequisite for
venue history. See [provider details](VISUALIZATION.md#historical-price-candles).
