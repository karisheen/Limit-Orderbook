# Workstation contracts (implemented M1 core, schema version 1)

The exact instrument/event/quality/book/wire contracts and shared adapter boundary below are implemented in `src/workstation`. Venue implementations, supervision, persistence, analytics and UI sections remain downstream contracts until their respective milestones land; this document does not certify those features. Legacy `src/domain` consumers remain a compatibility path. Core modules have no Streamlit, React, network or database imports.

## Conventions and module ownership

| Module under `src/workstation/` | Owns |
| --- | --- |
| `instruments.py` | Asset identity, versioned product catalog and constraints |
| `events.py` | Immutable raw/envelope/payload types and schema version |
| `precision.py` | Exact parsing, bounds, tick/lot validation and canonical serialization |
| `quality.py` | State, capabilities, coverage and centralized eligibility |
| `adapters/base.py`, venue modules | Transport requests, pure decoding and venue synchronization rules |
| `book.py` | Single-owner mutable reducer internals; immutable published Book/checkpoints |
| `ingestion.py`, `subscriptions.py` | Transport lifecycle, bounded queues, epochs and shared leases |
| `analytics.py`, `comparison.py` | Pure metrics, depth walks, costs and cohort/conversion policy |
| `recording.py`, `catalog.py`, `replay.py` | Capture, manifests, recovery, deterministic clock/order |
| `alerts.py`, `paper.py`, `journal.py` | Quality-gated local alerts, simulated ledger, linked rationale |
| `api.py`, `wire.py` | FastAPI routes, input limits, origin policy and explicit wire codecs |

Types below are Python annotations: `Decimal`, `Optional[T]`, `Tuple[T, ...]`, `FrozenSet[T]`, and immutable `@dataclass(frozen=True)` records. Enum values are lower-case strings. Domain containers are tuples/frozensets, not mutable nested lists/dictionaries. Internal reducer maps may mutate under their single owner; published records never expose them. Runtime version/locked dependencies are established by the integration owner, not by these annotations.

- `InstrumentId`, `AssetId`, `SessionId`, `SegmentId`, `StreamId`, `ClientId`, `LeaseId` are distinct validated string types. IDs are opaque to consumers. Native IDs and sequence tokens are strings even when the exchange uses numbers.
- `WallNs`, `MonoNs`, `Ordinal` are nonnegative Python integers. Wire timestamps, ordinals and arbitrary-sized IDs are strings; small bounded counts and schema version may be JSON integers. Monotonic timestamps have meaning only within their `clock_id` process origin.
- All authoritative prices, quantities, fees, rates, balances and P&L are `Decimal` constructed from original text or exact integer scaling. `parse_float=Decimal`, reject JSON nonfinite constants; never `Decimal(float_value)` or an intermediate binary float. Bound input length, exponent, digit count, message size and price/quantity against explicit instrument/parser policies before arithmetic. Check `is_finite` and sign before comparisons. Preserve decimal lexical scale needed by checksum algorithms; raw bytes remain authoritative.
- Arithmetic uses an explicit versioned Decimal context (precision, rounding and traps), recorded in configuration. Validation never rounds incoming book levels to fit a tick. Derived division may round only under this declared context; fee and order rounding obey instrument rules. Unknown constraints remain unknown, not permissive zeroes. Scaled-integer optimization requires a versioned scale and overflow checks.
- Canonical wire Decimal is base-10 fixed-point text, no scientific notation or negative zero; numeric equality has one canonical form for hashes. Original lexical representation remains in raw capture and checksum inputs. JSON `null` means absent; no invented timestamp or fee. Floats are allowed only for validated plotting coordinates and measured durations, never sent back as trading inputs.

Implemented precision entry points are `decimal(value: str | int | Decimal, *, positive=False, nonnegative=False, policy=DecimalPolicy())`, `validate_increment(value, increment, *, name="value")`, `scaled_decimal(value: int, scale: int)`, and `exact_json_loads(payload: bytes | str, *, max_bytes=MAX_MARKET_DATA_BYTES)`. `MAX_MARKET_DATA_BYTES=32*1024*1024` is shared by the decoder and RawFrame (supervisors must use it for WS receive bounds); a real Coinbase snapshot observed during integration was approximately 4.78 MB with 40,405 updates, exceeding the prior 4 MiB limit. Outbound request bodies retain their smaller 4 MiB cap. Defaults bound decimal text to 128 characters, 38 coefficient digits, exponent magnitude 38 and absolute magnitude `1e30`; floats/bools, nonfinite constants and duplicate JSON keys are rejected. Parsed Decimal scale is retained. `DECIMAL_CONTEXT_VERSION="decimal-v1-p80-half-even"` declares precision 80 and half-even rounding. Book lots are not order minima: `Instrument.validate_order(price, quantity)` additionally checks configured spot order constraints; contract valuation requires explicit downstream conversion.

`wire.to_wire(value, *, canonical=True)` and `wire.dumps(value, *, canonical=True) -> bytes` emit tagged records (`"$type"`), decimal/integer fields as strings, immutable arrays and base64 bytes. The outer object is `{"schema_version":1,"value":...}`. `from_wire(value, expected_type=None)` / `loads(payload, expected_type=None, *, max_bytes=268435456)` validate the allowlisted record type, every field and nested constructor; unknown/missing fields and numeric decimal fields fail. The larger bounded wire limit accommodates full-book checkpoints with state, provenance, buffered frames and base64 overhead; it does not increase the inbound market-frame limit. Default decimal text is canonical; `canonical=False` retains Decimal scale/exponent text for checksum-sensitive state. Checkpoints explicitly use this lossless mode internally. Raw bytes preserve the complete original notation. This schema is a domain codec, not the later presentation-stream schema.

## Instruments (`instruments.py`)

`AssetIdentity(asset_id: AssetId, ticker: str, native_token_id: Optional[str], network: Optional[str], contract_address: Optional[str], reference_asset_id: Optional[AssetId])`. A reference link does not assert fungibility or authorize executable-book merging.

`OrderConstraints(min_base: Optional[Decimal], max_base: Optional[Decimal], min_notional: Optional[Decimal], max_notional: Optional[Decimal], min_notional_currency: Optional[AssetId], price_rule: str, size_rule: str)`; named rules resolve to versioned venue rules, including market/limit differences and significant-figure price rules where a constant tick is inadequate.

`Instrument` fields:

| Field | Type / meaning |
| --- | --- |
| `instrument_id`, `metadata_version` | Stable InstrumentId; immutable content-hash/version string |
| `venue`, `api_family`, `native_symbol`, `native_asset_id` | str, str, str, Optional[str]; Binance global and US are different venues |
| `dex_namespace` | Optional[str]; preserved for Hyperliquid DEX identity |
| `base`, `quote`, `settlement` | AssetIdentity, AssetIdentity, Optional[AssetIdentity] |
| `market_type` | `spot`, `perpetual`, or `future` |
| `expiry_wall_ns` | Optional[WallNs] |
| `tick_size`, `lot_size` | Optional[Decimal], Decimal; positive when known; variable ticks require `price_rule` |
| `constraints` | OrderConstraints |
| `contract_multiplier`, `quantity_unit` | Decimal; `base` or `contracts`; multiplier unit is explicit in `contract_value_currency` |
| `contract_value_currency`, `inverse` | Optional[AssetId], bool; inverse contract conversion is explicit, never base-size multiplication by assumption |
| `status` | `active`, `suspended`, `delisted`, `unavailable`, `unknown` |
| `data_access`, `execution_access` | Each `available`, `blocked`, `unsupported`, `unknown`; public readability does not prove trading eligibility |
| `metadata_asof_wall_ns`, `metadata_source` | WallNs, source endpoint/revision string |

`InstrumentCatalog.resolve(venue: str, api_family: str, native_symbol: str, metadata_version: Optional[str] = None) -> Instrument` raises `UnknownInstrument`, never USD fallback. `get(instrument_id, metadata_version=None) -> Instrument`; `list(query=InstrumentQuery()) -> Tuple[Instrument, ...]`; `apply_metadata(snapshot: MetadataSnapshot) -> Tuple[Instrument, ...]` is transactional and retains old immutable versions for replay. `InstrumentQuery(base_id=None, venue=None, market_type=None, quote_id=None, status=None)` filters latest versions. `MetadataSnapshot(source, fetch_wall_ns, payload_hash, instruments)` is immutable. Content changes under an existing version, stable identity changes and native-symbol collisions fail. Latest selection uses metadata as-of time; discovery remains outside the pure catalog.

Default executable-comparison candidate cohort is Coinbase/Kraken BTC/ETH/SOL spot USD subject to discovery/access. USDT and USDC are separate cohorts. Hyperliquid BTC/ETH/SOL perpetuals, HYPE where listed, and metadata-discovered spot tokens stay separate throughout capture, UI and replay.

## Event boundary (`events.py`)

`SubscriptionKey(venue: str, api_family: str, instrument_id: InstrumentId, channel: str, depth: Optional[int], aggregation: Tuple[Tuple[str, str], ...])` is hashable. Sorted aggregation pairs include source-level precision/cadence options (e.g. Hyperliquid `nSigFigs`, `mantissa`, `fast`); UI aggregation is not part of reconstruction. Different keys never share mutable books. Connection multiplexing may share transport, not logical ownership.

`RawFrame(session_id: SessionId, stream_id: StreamId, connection_id: str, connection_epoch: int, clock_id: str, ingest_ordinal: Ordinal, receive_wall_ns: WallNs, receive_mono_ns: MonoNs, transport: str, endpoint_id: str, payload: bytes, payload_sha256: str)` is captured before JSON decoding. REST snapshot responses use the same envelope with request/response correlation in capture metadata. Session-global ordinals are assigned atomically at the single receive boundary across all streams; every inbound frame and local reset/gap/clock-control event occupies an ordinal. Rejected frames are retained with their diagnostic.

`EventEnvelope` required constructor fields are `session_id`, `segment_id`, `stream_id`, `key: SubscriptionKey`, `instrument_metadata_version`, `connection_id`, `connection_epoch`, `clock_id`, `ingest_ordinal`, `receive_wall_ns`, `receive_mono_ns`, and `payload: EventPayload`. Defaults: `event_index=0`, `schema_version=1`, `source_mode="ws_snapshot"`; exchange time/kind, process-start/end, publish time, sequence, checksum and raw reference are all `None`. Unknown process timings are not invented. Supplied monotonic processing/publish timestamps must not precede receive or earlier supplied stages. `.order_key` returns `(ingest_ordinal,event_index)`; `.ref()` returns `EventEnvelopeRef`.

`SequenceInfo(scope: str, first: Optional[str], last: Optional[str], previous: Optional[str])` describes venue semantics, not universal contiguous arithmetic. `ChecksumInfo(algorithm: str, supplied: str, calculated: Optional[str], scope: str, result: str)` has result `unchecked`, `match`, `mismatch`. `RawRef(file_id: str, frame_ordinal: Ordinal, payload_sha256: str)` points to exact bytes. Local gap events may have no raw reference. Multi-event frames retain message order via `event_index` starting at zero; replay sort key is `(ingest_ordinal, event_index)`, never unsynchronized exchange time. Publish timing is finalized by replacing the immutable record, not by mutating a shared instance; timing-only fields are excluded from deterministic economic-state hashes.

Payload union:

- `BookUpdate(kind: str, changes: Tuple[LevelChange, ...], subscribed_depth: Optional[int], reset_reason: Optional[str])`, kind `snapshot`/`delta`. `LevelChange(side: str, price: Decimal, quantity: Decimal, quantity_semantics: str, exchange_event_wall_ns: Optional[WallNs])`; side `bid`/`ask`; semantics `absolute`/`incremental`. The adapter emits only documented semantics. Preserve multiple changes at one price in message order. Snapshot duplicate prices are rejected unless the specific protocol explicitly defines them; deltas are never arbitrarily merged or repaired. Zero absolute quantity deletes; negative absolute quantity is invalid. Entire protocol message is one validation transaction even when it contains multiple events.
- `Trade` as below.
- `ReferencePrice(kind: str, price: Decimal, currency: AssetId, interval_ns: Optional[int])`, kind `mid`/`mark`/`oracle`; never a Book.
- `AssetContext(mark: Optional[Decimal], oracle: Optional[Decimal], funding_rate: Optional[Decimal], funding_interval_ns: Optional[int], open_interest: Optional[Decimal], open_interest_unit: Optional[str])`; missing values remain absent.
- `Control(kind: str, reason_code: str, detail: str)`, kind `ack`, `heartbeat`, `rejection`, `reset`, `disconnect`, `source_change`, `clock_tick`. Controls do not establish book validity.
- `Gap(reason_code: str, first_missing_ordinal: Optional[Ordinal], last_missing_ordinal: Optional[Ordinal], continuity_lost: bool, capture_lost: bool, affected_streams: Tuple[StreamId, ...])`. Do not invent missing exchange ranges when unknowable.

`Trade(trade_id: Optional[str], dedup_key: str, price: Decimal, quantity: Decimal, quantity_unit: str, taker_side: str, side_provenance: str, is_subscription_history: bool)` is payload inside EventEnvelope, which supplies identity/time/quality association. Taker side is `buy`, `sell`, `unknown`; absent semantics must remain unknown. Dedup scope is venue/API/instrument, not connection epoch. Hyperliquid's documented globally identifying tuple is `(block_time, coin, tid)`; include venue/namespace. Bounded dedup retention and its limitation are recorded. Where no reliable trade ID exists, a documented heuristic cannot silently eliminate distinct identical prints. Preserve duplicates in raw capture, annotate excluded normalized events, and reset trade-window completeness at gaps rather than inventing missed volume.

## Book and quality (`book.py`, `quality.py`)

`Level(price: Decimal, quantity: Decimal)` is positive and finite. `Book(instrument_id: InstrumentId, metadata_version: str, key: SubscriptionKey, segment_id: SegmentId, connection_epoch: int, revision: int, asof: EventEnvelopeRef, bids: Tuple[Level, ...], asks: Tuple[Level, ...], quality: Quality)` is immutable. `EventEnvelopeRef(session_id, ingest_ordinal, event_index, exchange_event_wall_ns, receive_wall_ns, receive_mono_ns, clock_id)` supplies provenance without raw bytes. Bids strictly descend, asks strictly ascend, each side unique. A valid Book requires two nonempty sides and best bid < best ask; there is no implicit auction exception. Invalid updates yield recovery reasons and preserve the previous levels with invalid quality for diagnostic display; an invalid initial snapshot publishes no Book.

`Coverage(subscribed_depth: Optional[int], retained_bid_levels: int, retained_ask_levels: int, worst_bid: Optional[Decimal], worst_ask: Optional[Decimal], full_depth: bool, source_aggregation: Tuple[Tuple[str, str], ...], expected_cadence_ns: Optional[int])` describes what is known, not a promise that a requested bps band is covered. Display depth never alters these fields or reconstruction depth.

`Capabilities(snapshot: bool, deltas: bool, trades: bool, sequence_scope: Optional[str], checksum_scope: Optional[str], exact_event_flow: bool, reference_prices: FrozenSet[str], asset_context: FrozenSet[str])` is adapter/source-mode specific. Hyperliquid l2Book, Binance.US partial books, and REST polling do not claim exact event-flow capability.

`Quality` fields:

- `lifecycle`: `starting`, `waiting_snapshot`, `synchronizing`, `live`, `degraded`, `stale`, `resynchronizing`, `disconnected`, `unsupported`, `blocked`.
- `integrity`: `unknown`, `valid`, `invalid`; valid means invariants plus the checks actually available, not an invented exchange guarantee.
- `transport_alive: bool`, `source_mode`: `ws_delta`, `ws_snapshot`, `rest_snapshot`, `replay`, `demo`; `recorded_source_mode: Optional[SourceMode]` retains original mode in replay/demo.
- `last_transport_mono_ns`, `last_book_observation_mono_ns`, `last_book_change_mono_ns`, `last_healthy_observation_mono_ns`: Optional[MonoNs], all under `clock_id: str`. Unchanged validated observations can refresh observation time without claiming a price change; heartbeats refresh only transport.
- `coverage: Coverage`, `capabilities: Capabilities`, `continuous_since_ordinal: Optional[Ordinal]`, `reason_codes: Tuple[str, ...]`, `capture_complete: bool`.

`EligibilityPolicy(max_age_ns=5000000000, max_asof_skew_ns=1000000000, allow_snapshot_quotes=True, require_execution_access=False, required_capabilities=frozenset(), version="eligibility-v1")` is immutable configuration. `Eligibility(eligible, reasons, evaluated_at_mono_ns, policy_version)` is returned by `evaluate(book, instrument, purpose, now, policy=EligibilityPolicy())`. Purposes are `display`, `comparison`, `depth_walk`, `event_ofi`, `alert`, `paper_fill`. Display returns the same eligibility verdict but may render explicitly ineligible last-known levels. `ClockReading(clock_id, mono_ns, wall_ns=None)` supports injected live/replay clocks. Age uses last healthy book observation, not heartbeat or price-change time; mismatched clocks and future observations are ineligible. `evaluate_cohort(books, instruments, now, policy=EligibilityPolicy()) -> CohortEligibility(eligible, reasons, members)` requires one instrument record per book and matching native base/quote/market/settlement/expiry/quantity/contract identity, distinct instruments and receive-monotonic as-of skew. `members` is a tuple of `(instrument_id, Eligibility)` pairs. There is no implicit conversion parameter: converted cost comparisons are a separate downstream operation. Reasons include `integrity_invalid`, `lifecycle_waiting_snapshot`, `stale`, `clock_mismatch`, `clock_regression`, `asof_skew`, `incompatible_instruments`, `missing_exact_event_flow`, `snapshot_quotes_disabled`, and `execution_access_unknown`. Execution access is independent of data access and enforced when policy requires it; unavailable fees remain a later cost-engine gate, not an invented zero.

## Adapter decode and reducer contract

`Adapter` is a Protocol with:

```text
async discover() -> MetadataSnapshot
capabilities(instrument: Instrument, key: SubscriptionKey) -> Capabilities
subscription_requests(instrument: Instrument, key: SubscriptionKey) -> Tuple[TransportRequest, ...]
snapshot_request(instrument: Instrument, key: SubscriptionKey) -> Optional[TransportRequest]
ws_endpoint(instrument: Instrument, key: SubscriptionKey) -> Optional[str]
decode(frame: RawFrame, instrument: Instrument, key: SubscriptionKey) -> DecodeResult
new_reducer(instrument: Instrument, key: SubscriptionKey) -> BookReducer
```

`adapters.base.TransportRequest(method, endpoint_id, body=None, headers=())` permits public `GET`, `POST`, and `WS` (outbound subscription frame) requests; body is bytes. The supervisor executes requests and captures responses. `DecodedEvent(payload, sequence=None, checksum=None, exchange_event_wall_ns=None, exchange_time_kind=None)` represents one ordered payload. `DecodeResult(events=(), controls=(), error=None)` cannot contain partial events/controls alongside an error. `DecodeError(code, raw_hash, detail)` bounds detail to 4096 characters. `decode` is synchronous/pure, with no clock/network reads. `wrap_decoded(frame, instrument, key, result, segment_id, source_mode)` returns envelopes preserving frame receive identity and zero-based event indices; it raises on a decode error. Sequence-bearing acknowledgements/heartbeats and interleaved controls must be `DecodedEvent(Control(...), sequence=...)` in `events`. The optional unsequenced `controls` tuple appends after `events`. Raw-file references and actual processing/publish timings are attached later via immutable replacement. Checksum validation uses the complete message transaction before publication.

`BookReducer(instrument, key, *, capabilities=Capabilities(), epoch=0, segment_id="initial", truncate_depth=False, max_levels=100000, sequence_validator=None, checksum_calculator=None, price_validator=None, candidate_validator=None, strategy_id="generic-v1", adapter_revision="core-v1", config_hash="default")` owns exact level maps and lifecycle evidence. `apply(events: tuple[EventEnvelope,...], *, synchronized=True) -> Reduction` accepts one complete source message, preserving repeated-price delta order and validating its final candidate before commit. `synchronized=False` stages a bootstrap snapshot but returns no published Book until a subsequent accepted update with `synchronized=True`. `invalidate(gap)`, `checkpoint()`, `restore(checkpoint)` and `reset(epoch, segment_id)` are the other operations. Reset clears maps/sequence/quality/protocol state and cannot regress epoch; every input must match the active subscription, metadata version, segment and epoch. `Reduction(book=None, events=(), recovery=None, changed=False)` carries accepted/checksum-annotated events and an optional diagnostic invalid Book after failure. `RecoveryRequest(reason, action="request_snapshot")` also supports `resubscribe`, `reconnect`, `stop_unsupported`. Duplicate protocol IDs produce no accepted events or book revision/healthy-observation refresh; obsolete local ordinals/epochs are rejected without mutation. Invalid candidates retain previous maps, mark quality invalid and require a new snapshot.

Hooks are synchronous/pure and run before commit:

- `sequence_validator(previous: SequenceInfo | None, incoming: SequenceInfo | None, snapshot: bool) -> str`: `accept`, `ignore`, or `gap`. Identical evidence within one source message is evaluated once; controls participate in connection-wide sequencing. `monotonic_sequence` permits numeric jumps; `contiguous_sequence` enforces the supplied inclusive numeric range bridge. Only use these for a venue's documented scope. No hook means no invented continuity guarantee.
- `checksum_calculator(bids: tuple[Level,...], asks: tuple[Level,...], supplied: ChecksumInfo) -> str`: receives the final ordered, optionally depth-truncated candidate with retained Decimal scale. A required missing checksum, absent calculator or mismatch invalidates before publication; successful evidence is returned as `result="match"`.
- `price_validator(instrument, price) -> None`: replaces fixed-tick validation for documented variable-price rules; generic finite/positive Decimal and quantity/lot validation still apply.
- `candidate_validator(bids, asks, events) -> None`: additional venue invariant checks raise `ValueError` before commit.

Depth truncation is opt-in, suitable for Kraken subscribed-depth maintenance; default unbounded-known-depth reconstruction never drops Binance levels merely because a display depth was selected. `max_levels` is a hard per-side capacity and overflow invalidates rather than dropping required state. Venue wrappers own bootstrap mechanics and may store bounded immutable `buffered_events: tuple[EventEnvelope,...]` and `protocol_state: bytes` on the reducer; both are checkpointed. Hooks/config are not serialized executable objects: restore requires compatible strategy/config/adapter identifiers and the same capabilities.

Protocol obligations: Kraken v2 applies all changes, truncates to subscription depth, checks top-10 CRC32 on every update; Coinbase Advanced Trade `level2` receives `l2_data` with absolute `new_quantity` and per-connection sequence handling; Binance global buffers diff events while fetching REST snapshot, bridges update IDs according to documented spot rules and resyncs gaps; US partial top-N replaces snapshots; Bybit new snapshots and service restart `u=1` replace state without invented `u+1` continuity; Hyperliquid l2Book replaces bounded snapshots and records actual aggregation/cadence with no fictional checksum/sequence. Missing sequence support is a capability limitation, not a pass.

## Subscription and supervisor ownership

`SubscriptionRegistry` is process-scoped, single asyncio loop, one backend worker. Public operations:

```text
async acquire(client_id: ClientId, key: SubscriptionKey) -> SubscriptionLease
async replace(client_id: ClientId, keys: FrozenSet[SubscriptionKey], generation: int) -> Tuple[SubscriptionLease, ...]
async release(lease_id: LeaseId) -> None
async release_client(client_id: ClientId) -> None
snapshot() -> Tuple[SubscriptionStatus, ...]
async close() -> None
```

`SubscriptionLease(lease_id, client_id, key, generation, stream_id)` is immutable. `SubscriptionStatus(key, stream_id, reference_count, lifecycle, connection_epoch, queue_depth)` is diagnostic. Registry owns an entry `{leases, supervisor task, active epoch, closing future}` per key. Acquire is atomic under a registry lock; start once on 0→1. Release is idempotent and cancels/awaits the owner on 1→0 outside the lock; a new acquire waits for closing or receives a new fenced epoch. Replacement rejects obsolete client generations, acquires the new set before releasing that client's removed leases, rolls back new leases on failure, and never edits another client's set. Disconnect releases that client's leases. Recorder/headless analytics hold their own leases so closing a tab or pausing a view does not stop recording. Shutdown rejects new leases, cancels and awaits every task/transport with a bounded timeout and reports any failure. Repeated switches must leave no orphan workers or growing references.

Supervisor owns ack/heartbeat/snapshot deadlines, silence detection, jittered exponential backoff, Retry-After and rate budgets, bounded retry/circuit state, periodic WS recovery while polling, and connection/source epoch transitions. Meaningful recovery requires valid synchronized data, not an ack/heartbeat frame. Transport sharing must retain independent per-key integrity and handle connection-wide sequence validation before routing to reducers.

Required-event queues have configured count/byte bounds. Overflow atomically invalidates affected continuity, records Gap and starts recovery; a reserved control slot/out-of-band invalidation flag prevents a full queue hiding its own gap. Trade loss marks analytical windows incomplete until a fresh declared anchor. Slow recorder overflow marks capture incomplete and alerts; it cannot block reconstruction indefinitely. UI queues may replace validated presentation snapshots with the latest revision; clients receive sequence/lag/resync indicators. Never coalesce raw events or the trade stream feeding analytics. Disk/query work runs off the reconstruction loop.

## Recording, replay and downstream contracts

`BookCheckpoint(schema_version, session_id, stream_id, segment_id, ordinal, event_index, metadata_version, adapter_revision, config_hash, book, reducer_state: bytes, reducer_state_version: int, state_hash: str)` includes synchronization flags, active epoch/segment, revision, exact lexical levels, sequence, last accepted/diagnostic order and envelope, complete quality, last Book, protocol buffers and protocol-state bytes. Before the first event session/stream/ordinal/index are `None`. State uses versioned allowlisted JSON, never pickle, and SHA256 over exact serialized state; restore validates hash, configuration, metadata, capabilities, position and level consistency before mutation. This exact restore hash includes recorded timing/protocol evidence and is not the later timing-excluding economic metric hash. Analytical windows, deduplication and paper/alert state remain separate downstream checkpoint namespaces.

`Recorder.append_raw(frame)`, `append_events(events)`, `append_checkpoint(checkpoint)` enqueue bounded batches; `async finalize() -> SessionManifest` commits files safely. `SessionManifest` records schema/config/adapter/code revisions, instruments and versions, merge order, start/end/coverage, gaps, capture state, file paths/hashes/row counts and provenance/license. Finalized files are immutable. Corruption/missing ordinals prevents claiming complete replay. Restart reconciles temporary files and catalog rather than marking them complete by existence.

`ReplayClock.now() -> ClockReading`; `pause()`, `play(speed: Decimal)`, `step(count: int = 1)`, `seek(ordinal: Ordinal, event_index: int = 0)` are replay controls. `ReplaySession` restores the nearest compatible checkpoint at or before target, clears all later book/trade/metric/alert/paper/window state, then feeds ordered events through the same reducers and analytics. Raw replay runs the same `decode`; normalized replay bypasses only decoding and verifies schema/adapter compatibility. Missing data stays missing. Replay speed changes delivery pacing, not event order, virtual eligibility time or state hashes. Record local clock ticks needed to reproduce expiry/alert decisions. Economic hashes exclude machine performance timings but include config/metadata/quality/coverage and deterministic rounding.

`MetricResult(name: str, value: Optional[Decimal], unit: str, instrument_ids: Tuple[InstrumentId, ...], venue_ids: Tuple[str, ...], asof: EventEnvelopeRef, window_start_ordinal: Optional[Ordinal], window_end_ordinal: Ordinal, window_definition: str, quality: Quality, eligibility: Eligibility, formula_version: str, config_hash: str, input_refs: Tuple[EventEnvelopeRef, ...])` is used by analytics, alerts, comparison, API, research and paper UI. Multi-value metrics use named results, not anonymous vectors. Unavailable values have reasons. Exact-event OFI and snapshot-derived flow have different names/capabilities. Trades alone feed CVD.

`walk_book(book, side, base_quantity: Optional[Decimal], quote_budget: Optional[Decimal], constraints, fees, policy, now) -> ExecutionEstimate` accepts exactly one size input and returns requested/filled/unfilled base, quote spent/received, VWAP, best-price slippage, fee amounts/currencies, effective price, coverage and eligibility. No extrapolation beyond known depth. `compare_pair(buy_book, sell_book, feasible_base_quantity, conversion, fees, inventory_assumptions, latency_stress, now) -> PairEstimate` uses the same feasible size on both sides and explicit conversion orientation/cost. Paper orders use this shared engine at simulated arrival with an explicit liquidity-consumption model; they do not submit exchange orders.

API JSON is versioned (`schema_version`, request/subscription ID, generation, message kind, decimal/time strings, quality and reasons). Planned `/api/v1/instruments`, `/subscriptions`, `/history`, `/sessions`, `/replay`, `/alerts`, `/paper`, `/journal` and stream endpoints validate IDs, range/row limits, size inputs and allowed local origins. `/health/live` reports process liveness; `/health/ready` reports required synchronized capabilities and recording status, not just HTTP success. Slow-client resync uses a fresh validated snapshot plus revision; UI never reconstructs authoritative exchange books from coalesced display frames. Accessible tables use exact formatted values; chart floats are disposable projections.

## Native replacement boundary

Only `BookReducer.apply/checkpoint/restore` and measured pure numeric kernels (`walk_book`, band-depth accumulation) may later cross PyO3. Batch exact scaled integers plus explicit scale/protocol IDs; return the same immutable Book/Reduction semantics. No networking, async scheduling, persistence, credential handling or frontend state crosses this boundary. Parity fixtures must match Python state hashes, invalidation reasons, checksum behavior and deterministic replay before selecting a native path. Until profiling demonstrates a failing budget, Python is the sole implementation.

## Synthetic fixture constructors

`synthetic_instrument(*, venue="synthetic", base="BTC", quote="USD", tick_size="0.01", lot_size="0.001", **overrides)` provides explicit synthetic metadata with available data access and unknown execution access. `SubscriptionKey.for_instrument(instrument, **options)` derives venue/API/ID without invented product mapping. `synthetic_frame(payload=b"{}", *, ordinal=1, epoch=0, mono_ns=1, **overrides)` computes and validates SHA256. `synthetic_envelope(instrument, payload, *, ordinal=1, epoch=0, mono_ns=None, **overrides)` uses demo mode, `synthetic-session`, `synthetic-stream`, `synthetic-clock`, and `synthetic-segment`; default monotonic time is the supplied ordinal. A matching fixture reducer therefore uses `segment_id="synthetic-segment"`. These helpers never participate in live discovery.

Offline consumer regression coverage is in `tests/test_workstation_core.py`: malformed precision/JSON, metadata version collisions, complete-message corruption, ordered repeated-price changes, initial/protocol synchronization, checksum/sequence hooks, depth scope, epoch/order fencing, heartbeat-versus-observation freshness, exact identity/cohorts, string-safe wire roundtrips and complete checkpoint restore. Check execution/results are recorded by the integration owner, not asserted here.
