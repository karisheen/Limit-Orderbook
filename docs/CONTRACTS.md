# Workstation contracts (schema version 1)

The exact instrument/event/quality/book/wire contracts, venue adapters, public transport, single-worker subscription supervision, recording/replay, analytics and visualization are implemented in `src/workstation`. React and the supported thin Streamlit client consume the same native API/core; the duplicate `src/data` and `src/domain` compatibility engines are retired. Implementation is not certification of acceptance: executed evidence belongs in `OMP_UPGRADE_HANDOFF.md`. Core domain modules have no Streamlit, React, network or database imports; transport and ingestion own network I/O.

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
| `transport.py` | Bounded public HTTP response bytes, per-origin retry/rate/circuit policy |
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

Wire encoding caches immutable schema field descriptors and at most 65,536 lexical Decimal conversions per public call; only texts of at most 512 characters enter this memo. Keys retain scale/exponent, rather than numeric Decimal equality; every new key passes the selected explicit policy. The lexical-key payload is bounded by 32 MiB, with dictionary/value overhead separate. Noncanonical encoding reuses the already-validated lexical text. Mutable wire dictionaries are never shared between calls. Native schema and default validation limits are unchanged.

Derived p80 results explicitly opt into `DERIVED_DECIMAL_POLICY`: at most 80
coefficient digits, exponent magnitude 256, 512 text characters and absolute
magnitude `1e120`. `canonical_decimal(value, *, policy=...)` and
`wire.to_wire/from_wire/dumps/loads(..., decimal_policy=...)` accept the policy;
their defaults remain native. This allows recurring division results to round-trip
without binary floats or truncating them to source precision. Nested native
constructors still enforce native bounds even under derived decoding. Derived
model checkpoints/configuration hashes must declare the same policy. Cash
accounting uses accumulated notional and fees, never reconstructed rounded VWAP.

## Instruments (`instruments.py`)

`AssetIdentity(asset_id: AssetId, ticker: str, native_token_id: Optional[str], network: Optional[str], contract_address: Optional[str], reference_asset_id: Optional[AssetId])`. A reference link does not assert fungibility or authorize executable-book merging.

`OrderConstraints(min_base: Optional[Decimal], max_base: Optional[Decimal], min_notional: Optional[Decimal], max_notional: Optional[Decimal], min_notional_currency: Optional[AssetId], price_rule: str, size_rule: str, max_limit_base: Optional[Decimal] = None, max_market_base: Optional[Decimal] = None)`; named rules resolve to versioned venue rules, including significant-figure price rules where a constant tick is inadequate. Known bounds are nonnegative; each order-type maximum cannot fall below known `min_base`. Unknown bounds remain `None`.

`Instrument.validate_order(price, quantity, *, order_type)` requires explicit `"limit"` or `"market"`. The generic `max_base` applies to both types; the corresponding `max_limit_base` or `max_market_base` adds an intersecting bound, never overrides the generic maximum. No post-only/RPI interpretation is inferred.

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
| `reference_multiplier` | Optional positive Decimal; separate underlying-reference ratio, not a contract multiplier or token-book conversion |
| `settlement_conversion` | `unknown` (default), `none`, or `quanto_no_fx`; `quanto_no_fx` requires non-spot and a settlement asset |
| `contract_specification_source` | Optional nonempty text; adapter-provided provenance for contract/settlement classification |
| `status` | `active`, `suspended`, `delisted`, `unavailable`, `unknown` |
| `data_access`, `execution_access` | Each `available`, `blocked`, `unsupported`, `unknown`; public readability does not prove trading eligibility |
| `metadata_asof_wall_ns`, `metadata_source` | WallNs, source endpoint/revision string |

`InstrumentCatalog.resolve(venue: str, api_family: str, native_symbol: str, metadata_version: Optional[str] = None) -> Instrument` raises `UnknownInstrument`, never USD fallback. `get(instrument_id, metadata_version=None) -> Instrument`; `list(query=InstrumentQuery()) -> Tuple[Instrument, ...]`; `apply_metadata(snapshot: MetadataSnapshot) -> Tuple[Instrument, ...]` is transactional and retains old immutable versions for replay. `InstrumentQuery(base_id=None, venue=None, market_type=None, quote_id=None, status=None)` filters latest versions. `MetadataSnapshot(source, fetch_wall_ns, payload_hash, instruments)` is immutable. Content changes under an existing version, stable identity changes and native-symbol collisions fail. Latest selection uses metadata as-of time; discovery remains outside the pure catalog.

Bybit xstock `reference_multiplier=m` describes reference stock quantity `token_quantity*m` and stock price `token_price/m`; it never changes native token quantities, `contract_multiplier`, or order notional. A `settlement_conversion="none"` classification does not require matching quote/settlement asset IDs: native collateral may have a distinct identity. Unknown conversion remains explicit, and no FX conversion is invented. Source provenance is optional at construction but supplied by adapters when sourced classifications are known.

These metadata fields are part of the current strict all-fields wire schema. Python constructors retain their positional prefix and appended defaults; old serialized `Instrument`/`OrderConstraints` records missing the new fields are rejected rather than silently upgraded. This is a draft M1 content cutover, not a compatibility promise for released M2 captures.

Default executable-comparison candidate cohort is Coinbase/Kraken BTC/ETH/SOL spot USD subject to discovery/access. USDT and USDC are separate cohorts. Hyperliquid BTC/ETH/SOL perpetuals, HYPE where listed, and metadata-discovered spot tokens stay separate throughout capture, UI and replay.

## Event boundary (`events.py`)

`SubscriptionKey(venue: str, api_family: str, instrument_id: InstrumentId, channel: str, depth: Optional[int], aggregation: Tuple[Tuple[str, str], ...])` is hashable. Sorted aggregation pairs include source-level precision/cadence options (e.g. Hyperliquid `nSigFigs`, `mantissa`, `fast`); UI aggregation is not part of reconstruction. Different keys never share mutable books. Connection multiplexing may share transport, not logical ownership.

`RawFrame(session_id: SessionId, stream_id: StreamId, connection_id: str, connection_epoch: int, clock_id: str, ingest_ordinal: Ordinal, receive_wall_ns: WallNs, receive_mono_ns: MonoNs, transport: str, endpoint_id: str, payload: bytes, payload_sha256: str)` is captured before JSON decoding. REST snapshot responses use the same envelope with request/response correlation in capture metadata. Session-global ordinals are assigned atomically at the single receive boundary across all streams; every inbound frame and local reset/gap/clock-control event occupies an ordinal. Rejected frames are retained with their diagnostic.

`EventEnvelope` required constructor fields are `session_id`, `segment_id`, `stream_id`, `key: SubscriptionKey`, `instrument_metadata_version`, `connection_id`, `connection_epoch`, `clock_id`, `ingest_ordinal`, `receive_wall_ns`, `receive_mono_ns`, and `payload: EventPayload`. Defaults: `event_index=0`, `schema_version=1`, `source_mode="ws_snapshot"`; exchange time/kind, process-start/end, publish time, sequence, checksum and raw reference are all `None`. Unknown process timings are not invented. Supplied monotonic processing/publish timestamps must not precede receive or earlier supplied stages. `.order_key` returns `(ingest_ordinal,event_index)`; `.ref()` returns `EventEnvelopeRef`.

`SequenceInfo(scope: str, first: Optional[str], last: Optional[str], previous: Optional[str])` describes venue semantics, not universal contiguous arithmetic. `ChecksumInfo(algorithm: str, supplied: str, calculated: Optional[str], scope: str, result: str)` has result `unchecked`, `match`, `mismatch`. Public frame-derived events require `RawFrame.ref()`: `RawRef(file_id="raw:<sha256(UTF8(stream_id))>", frame_ordinal=ingest_ordinal, payload_sha256=original_hash)`. This logical dataset identifier resolves through the session's raw index, not a predicted rotation filename. Exact reference, connection, epoch and receive clocks must match indexed bytes. `local_control_ref()` instead creates reserved `local:<sha256(UTF8(stream_id))>` evidence markers for supervisor Gap/source_change/clock_tick/reset/disconnect; their fixed empty-hash convention is not an exchange payload or existing raw file. Shared `is_local_control()` validates the marker and permitted payload; anonymous events cannot certify local origin, and local markers cannot conflict with indexed raw. Multi-event frames retain `event_index` order within one atomic callback transaction. Replay follows recorded `delivery_index`, which can differ from global receive-ordinal order across streams; it never sorts by unsynchronized exchange time. Publish timing is finalized by immutable replacement; machine performance timings are excluded from deterministic economic-state hashes.

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
- `last_transport_mono_ns`, `last_book_observation_mono_ns`, `last_book_change_mono_ns`, `last_healthy_observation_mono_ns`: Optional[MonoNs], all under `clock_id: str`. Unchanged validated book observations refresh observation/health without claiming a price change. A validated heartbeat may refresh transport and healthy-observation time only for an already synchronized, integrity-valid `ws_delta` book with delta/exact-event-flow capabilities and established continuity; it never changes book-observation/change time or book as-of/revision. Heartbeats cannot establish or repair book validity, and snapshot/REST/bookless/unsynchronized feeds receive transport evidence only.
- `coverage: Coverage`, `capabilities: Capabilities`, `continuous_since_ordinal: Optional[Ordinal]`, `reason_codes: Tuple[str, ...]`, `capture_complete: bool`.

`EligibilityPolicy(max_age_ns=5000000000, max_asof_skew_ns=1000000000, allow_snapshot_quotes=True, require_execution_access=False, required_capabilities=frozenset(), version="eligibility-v1")` is immutable configuration. `Eligibility(eligible, reasons, evaluated_at_mono_ns, policy_version)` is returned by `evaluate(book, instrument, purpose, now, policy=EligibilityPolicy())`. Purposes are `display`, `comparison`, `depth_walk`, `event_ofi`, `alert`, `paper_fill`. Display returns the same eligibility verdict but may render explicitly ineligible last-known levels. `ClockReading(clock_id, mono_ns, wall_ns=None)` supports injected live/replay clocks. Age uses last healthy evidence, including only the qualified continuous-book heartbeat described above—not an arbitrary transport heartbeat or price-change time. Mismatched clocks and future observations are ineligible. `evaluate_cohort(books, instruments, now, policy=EligibilityPolicy()) -> CohortEligibility(eligible, reasons, members)` requires one instrument record per book and matching native base/quote/market/settlement/expiry/quantity/contract identity, distinct instruments and receive-monotonic as-of skew. `members` is a tuple of `(instrument_id, Eligibility)` pairs. There is no implicit conversion parameter: converted cost comparisons are a separate downstream operation. Reasons include `integrity_invalid`, `lifecycle_waiting_snapshot`, `stale`, `clock_mismatch`, `clock_regression`, `asof_skew`, `incompatible_instruments`, `missing_exact_event_flow`, `snapshot_quotes_disabled`, and `execution_access_unknown`. Execution access is independent of data access and enforced when policy requires it; unavailable fees remain a later cost-engine gate, not an invented zero.

Instrument metadata access is immutable discovery evidence, not an automatically changing permission flag. `data_access="unknown"` is resolved for this exact Book by an integrity-valid reconstructed book observation with healthy-observation evidence; the remaining freshness, clock, lifecycle and purpose gates still apply. A heartbeat without a book cannot establish readability. Explicit `blocked`/`unsupported` restrictions remain ineligible. This does not promote a whole venue, alter metadata versions, or grant execution access.

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

Public requests allow only caller `Accept`, `Content-Type`, and `User-Agent` headers (case-insensitive); API-key, exchange credential, cookie and proxy-authorization headers are rejected, not forwarded. Endpoints must be credential-free HTTPS (`GET`/`POST`) or WSS (`WS`), with bounded valid host/port/path/query syntax and no userinfo or fragments. Public query fields are explicitly allowlisted (`symbol`, `limit`, `pair`, `count`, `category`, `product_type`, `offset`, `cursor`, `product_id`, `streams`, `stream`). GET bodies are forbidden. POST/WS bodies must be bounded JSON objects using the existing public discovery/subscription fields and operations; nested unknown/authentication fields and private operations are rejected. Validation preserves accepted endpoint/body bytes rather than rewriting them.

`BookReducer(instrument, key, *, capabilities=Capabilities(), epoch=0, segment_id="initial", truncate_depth=False, max_levels=100000, effective_depth=None, sequence_validator=None, checksum_calculator=None, price_validator=None, candidate_validator=None, strategy_id="generic-v1", adapter_revision="core-v1", config_hash="default")` owns exact level maps and lifecycle evidence. `apply(events: tuple[EventEnvelope,...], *, synchronized=True) -> Reduction` accepts one complete source message, preserving repeated-price delta order and validating its final candidate before commit. `synchronized=False` stages a bootstrap snapshot but returns no published Book until a subsequent accepted update with `synchronized=True`. `invalidate(gap)`, `checkpoint()`, `restore(checkpoint)` and `reset(epoch, segment_id)` are the other operations. Reset clears maps/sequence/quality/protocol state and cannot regress epoch; every input must match the active subscription, metadata version, segment and epoch. `Reduction(book=None, events=(), recovery=None, changed=False)` carries accepted/checksum-annotated events and an optional diagnostic invalid Book after failure. `RecoveryRequest(reason, action="request_snapshot")` also supports `resubscribe`, `reconnect`, `stop_unsupported`. Duplicate protocol IDs produce no accepted events or book revision/healthy-observation refresh; obsolete local ordinals/epochs are rejected without mutation. Invalid candidates retain previous maps, mark quality invalid and require a new snapshot.

Hooks are synchronous/pure and run before commit:

- `sequence_validator(previous: SequenceInfo | None, incoming: SequenceInfo | None, snapshot: bool) -> str`: `accept`, `ignore`, or `gap`. Identical evidence within one source message is evaluated once; controls participate in connection-wide sequencing. `monotonic_sequence` permits numeric jumps; `contiguous_sequence` enforces the supplied inclusive numeric range bridge. Only use these for a venue's documented scope. No hook means no invented continuity guarantee.
- `checksum_calculator(bids: tuple[Level,...], asks: tuple[Level,...], supplied: ChecksumInfo) -> str`: receives the final ordered, optionally depth-truncated candidate with retained Decimal scale. A required missing checksum, absent calculator or mismatch invalidates before publication; successful evidence is returned as `result="match"`.
- `price_validator(instrument, price) -> None`: replaces fixed-tick validation for documented variable-price rules; generic finite/positive Decimal and quantity/lot validation still apply.
- `candidate_validator(bids, asks, events) -> None`: additional venue invariant checks raise `ValueError` before commit.

Depth truncation is opt-in, suitable for Kraken subscribed-depth maintenance; default unbounded-known-depth reconstruction never drops Binance levels merely because a display depth was selected. `max_levels` is a hard per-side capacity and overflow invalidates rather than dropping required state. Venue wrappers own bootstrap mechanics and may store bounded immutable `buffered_events: tuple[EventEnvelope,...]` and `protocol_state: bytes` on the reducer; both are checkpointed. Hooks/config are not serialized executable objects: restore requires compatible strategy/config/adapter identifiers and the same capabilities.

Binance's private bootstrap candidate transfers immutable native `ReducerState` through the same configuration, instrument, consistency and boundary checks used by restore, building independent mutable maps. It no longer encodes/decodes a durable checkpoint merely to commit an in-memory candidate. Public checkpoint bytes, hashes, restore validation and periodic persistence remain unchanged.

`effective_depth` defaults to `key.depth`. An adapter may resolve an omitted depth to its documented bounded-feed depth without changing the physical key on events, books or checkpoints; an explicit key depth cannot be overridden with a conflicting value. Payload depth validation, coverage and opt-in truncation use this effective depth. Checkpoints store it and reject restore into a different reconstruction depth, even when the physical key and capacity match. REST reducer clones must preserve it. This does not enable truncation: Hyperliquid rejects snapshots beyond its 20/5-level source bound.

Protocol obligations: Kraken v2 applies all changes, truncates to subscription depth, checks top-10 CRC32 on every update; Coinbase Advanced Trade `level2` receives `l2_data` with absolute `new_quantity` and per-connection sequence handling; Binance global buffers diff events while fetching REST snapshot, bridges update IDs according to documented spot rules and resyncs gaps; US partial top-N replaces snapshots; Bybit new snapshots and service restart `u=1` replace state without invented `u+1` continuity; Hyperliquid l2Book replaces bounded snapshots and records actual aggregation/cadence with no fictional checksum/sequence. Missing sequence support is a capability limitation, not a pass.

Book and trade subscriptions are separate physical owners: Coinbase book keys subscribe to `level2` plus required `heartbeats`, trade keys to `market_trades` plus `heartbeats`; Kraken keys subscribe only to `book` or `trade` and receive automatic connection heartbeats. Neither book key claims trade capability or accepts the other data channel. Coinbase connection sequence still covers acknowledgments and heartbeats on each independent connection, including empty trade batches. Subscription acknowledgments validate channel-specific identities: Coinbase's connection-scoped heartbeat identity is `"heartbeats"`, while data-channel identities must match the instrument product symbol.

## Subscription and supervisor ownership

`SubscriptionRegistry(catalog=None, *, config=None, supervisor_factory=FeedSupervisor, lock_path=None, worker_count=1, max_clients=1024)` is process-scoped and bound to one asyncio loop. `FeedHub` names this same implementation, not an independent collector or a second lifecycle. Both declared names use the operations below:

```text
async acquire(client_id: ClientId, key: SubscriptionKey) -> SubscriptionLease
async replace(client_id: ClientId, keys: FrozenSet[SubscriptionKey], generation: int) -> Tuple[SubscriptionLease, ...]
async release(lease_id: LeaseId) -> None
async release_client(client_id: ClientId) -> None
subscribe_raw(callback: Callable[[RawFrame], None]) -> Callable[[], None]
subscribe_events(callback: Callable[[Tuple[EventEnvelope, ...]], None]) -> Callable[[], None]
presentation_queue(client_id: ClientId, *, capacity=16) -> PresentationQueue
books() -> Tuple[Book, ...]
snapshot() -> Tuple[SubscriptionStatus, ...]
async close() -> None
```

`SubscriptionLease(lease_id, client_id, key, generation, stream_id)` is immutable. `SubscriptionStatus(key, stream_id, reference_count, lifecycle, connection_epoch, queue_depth, queue_bytes, reason, capture_complete)` is diagnostic. `books()` returns the latest retained Books, including invalid diagnostic state, not an eligibility-filtered view. Raw/event subscription methods return an unsubscribe callable and do not themselves acquire a feed lease. Callbacks are synchronous and must promptly enqueue into their own bounded sinks, never perform blocking recording/query work.

Normalized event callbacks retain reducer-produced evidence annotations, including checksum `calculated` and `result="match"`, over the original receive-ordered source batch. Bootstrap-staged, ignored and rejected source events are not dropped merely because no Book was published or no event was accepted; rejection is followed by an explicit gap/recovery boundary. Trade dedup remains a separate, explicitly annotated downstream exclusion. Raw bytes are unchanged.

Registry owns an entry `{leases, supervisor task, closing task}` per key; the supervisor owns its active epoch. Acquire is atomic under a registry lock; start once on 0→1. Repeated acquire for the same client/key returns the existing lease. Release is idempotent and cancels/awaits the owner on 1→0 outside the lock; a new acquire waits for closing before creating another owner. Replacement rejects obsolete client generations, acquires the new set before releasing that client's removed leases, rolls back new leases on failure, and never edits another client's set. Repeating a generation is allowed only with the same key set. Disconnect releases that client's leases and closes its presentation queue. Recorder/headless analytics hold their own leases so closing a tab or pausing a view does not stop recording. `close()` (also used by the async context manager) rejects new leases and joins cancellation/transport cleanup before releasing ownership; cancellation does not bypass the join. There is no separate registry shutdown timeout.

`AdapterCatalog.canonical_key(key)` supplies omitted bounded book depths before registry acquire/replace: Binance global 5000, Binance.US 20, Kraken 25, Bybit 50, KuCoin 20, and Hyperliquid 20 (5 with `fast=true`). Explicit depth and aggregation are preserved; non-book keys are unchanged. Omitted and explicit default depths therefore share the same lease/physical owner, including same-generation replacement; different reconstruction settings remain different keys.

The default collector lock is `${tempfile.gettempdir()}/limit-orderbook-collector-${os.getuid()}.lock`, acquired nonblockingly on the first physical feed and retained until registry close. `worker_count`, `WEB_CONCURRENCY` and `UVICORN_WORKERS` must select one backend worker; a competing collector fails rather than silently duplicating feeds. Default client capacity is 1024 concurrently retained identities. Disconnected identity/generation/lock state retires only after its operation holders and waiters, leases and presentation queues are gone; no concurrent operation can acquire a competing lock for that identity.

`FeedSupervisor(adapter, instrument, key, *, boundary=None, stream_id=None, callbacks=None, config=None, ws_connect=connect, http_fetch=fetch_public, trade_dedup=None)` has one asynchronous `run()` owner. `FeedCallbacks(raw=None, events=None, book=None, diagnostic=None)` separates exact receive bytes, normalized events, immutable Book publications and diagnostic reasons. `ReceiveBoundary(session_id=None).frame(stream_id, connection_id, epoch, transport, endpoint, payload)` synchronously assigns shared session-global ordinals and receive clocks before decoding or enqueue. The registry shares this boundary across physical feeds. Buffered readers yield after successful admission. Trade payloads of at most4096 bytes decode/reduce inline and yield after complete atomic delivery; all books and larger trade arrays remain off-loop, serially awaited and joined during cancellation. The consumer dequeues ready input directly and installs a timeout only while waiting on an empty queue. Neither scheduling path changes native depth, required-input queue bounds, source order, deadlines or explicit loss behavior.

Supervisor owns ack/heartbeat/snapshot deadlines, silence detection, jittered exponential backoff, Retry-After, bounded retry/circuit state, periodic WS recovery while polling, and connection/source epoch transitions. Meaningful recovery requires valid synchronized data, not an ack/heartbeat frame. Each physical feed currently owns its transport; future transport sharing must retain independent per-key integrity and validate connection-wide sequence before routing. Binance global opens WS and sends its subscription before fetching the bootstrap REST snapshot; inbound combined-stream bodies remain byte-for-byte intact, and buffered diff events retain their original ordinals in checkpoints. REST uses the exact request endpoint, including query parameters; decoders validate transport, endpoint, stream and symbol rather than accepting test-only raw WS bodies. Bootstrap publication uses receive order, not exchange-time sorting.

A validated heartbeat can maintain the valid-data deadline only after a synchronized, valid continuous `ws_delta` book with delta/exact-event-flow evidence exists. It does not create a book, repair invalid continuity, update book observation/change time or keep snapshot/REST observations fresh. Binance global actively awaits a protocol pong and enqueues its observed receipt as a local `RawFrame(transport="local", endpoint_id="ws_pong", payload=b"")`, normalized to `Control("heartbeat", "ws_pong")` by the same serial consumer. This records an observation of protocol liveness, not raw network-frame bytes; neither merely sending a ping nor elapsed time is health evidence. Unanswered pings fault/recover.

`SupervisorConfig` defaults (all durations in seconds):

| Bound / policy | Default |
| --- | --- |
| `queue_count`, `queue_bytes` | 512 frames, 64 MiB |
| `ack_timeout`, `snapshot_timeout` | 10, 20 |
| `valid_data_timeout`, `silence_timeout` | 30, 30 |
| `heartbeat_interval`, `poll_interval`, `ws_recovery_interval` | 20, 2, 30 |
| `backoff_base`, `backoff_max` | 0.5, 30 (then jitter factor 0.5–1.5) |
| `circuit_failures`, `circuit_cooldown`, `ws_failures_before_rest` | 6 failures, 60 seconds, 2 failures |
| `dedup_capacity`, `session_lifetime` | 50,000 trade identities, 23 hours |

WS transport also bounds one library-queued frame, a 32 MiB message, and a five-second close timeout. Each new connection/source mode resets reconstruction and advances epoch/segment; obsolete epoch data cannot publish. REST fallback is degraded snapshot evidence, clears delta/exact-event-flow/sequence/checksum claims, and records polling cadence, not continuous exchange event flow. Trade dedup is shared across registry feeds/reconnects with `(venue, api_family, instrument_id, dedup_key)` identity and bounded oldest-entry eviction. Excluded repeats remain in raw bytes and become normalized `Control("ack", "duplicate_trade", ...)` records; repeats outside retention may appear again.

The adapter's WS endpoint is validated before `_start_epoch` and before invoking the connector, even when no subscription request is emitted. A fake connector may be injected for a secure `wss://` fixture; there is no plaintext or userinfo exception for tests.

`make_snapshot_reducer(adapter, instrument, key, *, epoch=0, segment_id="initial", poll_interval=None)` is shared by supervisor REST fallback and the live-probe REST preflight. It retains the adapter's effective depth, price validator, capacity, aggregation and strategy identity while removing WS bootstrap/sequence/checksum hooks and delta/exact-flow claims. Snapshot observations remain independently validated and cannot establish WS synchronization. With no polling interval (a one-shot preflight), expected cadence is unknown; supervised polling records its configured cadence.

Required-event queues have count/byte bounds. Overflow sets an out-of-band fault, invalidates affected continuity and initiates Gap/recovery; a full queue cannot hide its own invalidation. Raw sink failure marks capture incomplete, invalidates and recovers; failure to retain a gap also leaves capture incomplete in out-of-band status. Raw/event sinks are loss-intolerant and never silently coalesced. A normalized sink exception fails the session and triggers the same gap/recovery boundary. Downstream analytics must mark trade windows incomplete across loss until a fresh declared anchor; recovery never retroactively certifies missing events or raw capture.

The WS reader explicitly yields to the owner loop after each admitted frame:
an already-buffered websocket iterator can return without suspending. Without
that scheduling point, a finite burst can fill the required queue before its
runnable consumer or completed decode worker gets a turn. This is cooperative
scheduling, not dropping/coalescing frames, adding a timer delay, retrying a failed
offer or increasing a buffer. The per-feed defaults remain 512 queued frames and
64 MiB queued payload bytes, plus the existing single in-flight processing frame;
transport/message and downstream recorder bounds are independent and unchanged.

`evidence/m5-recorder-upstream-capacity.json` retains the observed five upstream
loss ranges from the otherwise recorder-capacity-safe 1,800-second run, the
buffered-reader starvation reproduction and finite corrected profiles. All five
retained 514-frame bursts drained under both receive-offset and already-buffered
delivery after the scheduling correction, preserving original raw payloads and
native trade values. Each output matched raw/normalized replay hashes and its
persisted checkpoint/auxiliary bytes against linear reconstruction. The original
source remains incomplete. Receive-offset profiles can run late, and these
single-feed offline experiments do not establish full-scope live capacity;
a corrected public soak remains required.

Recovery reasons are bounded machine-readable codes from typed feed/public-transport faults, not arbitrary exception `.reason` or message text. Malformed typed reasons become `invalid_failure_reason`; untyped failures use stable categories such as `unexpected_failure` or `network_error`. Raw/event/book sink failures produce `capture_overflow`, `event_sink_failed`, or `book_sink_failed`. `FeedSupervisor.diagnostic_detail` separately retains at most 4096 printable characters of diagnostic text, safely handling exceptions whose `__str__` fails. Failing gap, invalid-book, or diagnostic callbacks cannot interrupt recovery or owner cancellation/cleanup; retained out-of-band status remains available when a sink itself is unavailable.

When a raw sink, required queue, or normalized sink loses a known receive frame, `Gap.first_missing_ordinal` and `last_missing_ordinal` record the minimum/maximum known lost ordinals for `affected_streams`. Session teardown drains queued frames and includes those discarded normalization inputs, plus any in-flight frame not emitted downstream. These are stream-scoped endpoints, not a claim that every session-global ordinal between them is lost: other shared streams can occupy intervening ordinals. Unknown transport/exchange history is not inferred from the local counter; both fields remain absent when no specific local loss is known. Capture loss remains sticky across recovered valid snapshots.

`PresentationQueue(capacity=16)` alone may coalesce Books by key or evict the oldest presentation at capacity. `put(book)`, `async get() -> Presentation`, `retain(keys)`, `close()` and `len(queue)` operate on `Presentation(book, sequence, coalesced, resync_required)`. Sequence increases on each put; `coalesced` is cumulative and `resync_required` marks an enqueue that removed a queued item. These are disposable display snapshots (including diagnostic invalid Books), not an analytical event/trade stream. Close discards pending presentations and wakes waiting readers with `EOFError`; a new client queue is obtained explicitly after reconnect.

### Public HTTP transport (`transport.py`)

`async fetch_public(request: TransportRequest, *, client: Optional[httpx.AsyncClient]=None, attempts=3) -> bytes` accepts only credential-free HTTPS GET/POST requests and returns original response-body bytes without JSON decoding/re-encoding. Response bodies are capped at 32 MiB, each request has a 15-second timeout, and the owned HTTP client disables redirects. Attempt count is bounded to 1–10. Tests may inject an HTTP client; supervisors may inject `http_fetch` and `ws_connect` without changing strict adapter wire contracts.

The owned client also disables environment-derived credentials/proxies. Every attempt rejects injected-client authentication, cookies, or nonpublic default headers; harmless HTTP defaults (`Accept-Encoding`, `Connection`) remain permitted in addition to the caller header allowlist. Each request explicitly disables client authentication and redirects, including with injected clients. A response cookie cannot be forwarded on a later retry.

Per-event-loop/origin budgets reserve requests at 0.1-second intervals. HTTP 401/403/451 mark access blocked with a 60-second circuit; 404/410 are unsupported; other non-2xx statuses are rejected except retryable 429/5xx. Network errors also retry. Retry backoff is `min(8, 0.25 * 2**attempt)` seconds with 0.5–1.5 jitter, never earlier than Retry-After. Six accumulated retryable failures open a 30-second circuit; a successful response clears the failure count. Retry-After greater than 120 seconds opens a circuit for the supplied duration rather than sleeping indefinitely or retrying early.

`retry_after_seconds(value: Optional[str], *, now: Optional[float]=None) -> float` accepts delay seconds or an HTTP date and returns zero for absent/invalid/past values. `PublicTransportError(reason, *, status=None, retry_after=0)` preserves the reason, optional HTTP status and retry delay for supervisor diagnostics/recovery. HTTP access says nothing about execution access or valid book synchronization.

## Recording, replay and downstream contracts

`BookCheckpoint(schema_version, session_id, stream_id, segment_id, ordinal, event_index, metadata_version, adapter_revision, config_hash, book, reducer_state: bytes, reducer_state_version: int, state_hash: str)` includes synchronization flags, active epoch/segment, revision, exact lexical levels, sequence, last accepted/diagnostic order and envelope, complete quality, last Book, protocol buffers and protocol-state bytes. Before the first event session/stream/ordinal/index are `None`. State uses versioned allowlisted JSON, never pickle, and SHA256 over exact serialized state; restore validates hash, configuration, metadata, capabilities, position and level consistency before mutation. This exact restore hash includes recorded timing/protocol evidence and is not the later timing-excluding economic metric hash. Analytical windows, deduplication and paper/alert state remain separate downstream checkpoint namespaces.

`Recorder.append_raw(frame)`, `append_events(events)`, and `append_checkpoint(checkpoint, *, delivery_index=None, observer_state=b"")` offer count/byte-bounded work without producer-side serialization or disk/database I/O. `await finalize() -> SessionManifest` flushes with a deadline; failure remains explicitly incomplete. The writer owns a separate shared-code `RecordingProjection`, not an unsafe concurrent snapshot of the live reducer. `SessionManifest` records schema/config/adapter/code revisions, metadata, selected-stream scope, actual delivery order, gaps, completion state, file hashes/counts/ranges and provenance/license. Raw gzip, stream-partitioned Zstd Parquet, checkpoints and SQLite indexes are immutable after finalization. `RecordingReader` verifies provenance and physical evidence; bounded DuckDB history queries run off ingestion. Known required capture loss prevents trusted complete replay; skipped global ordinals belonging to unselected streams do not imply loss. Recovery quarantines interrupted captures, never promotes them by file existence. Exact APIs and limits: `RECORDING.md`.

`await RecorderBinding.start(registry, recorder, keys, client_id=None)` holds recorder-owned leases independently of browser clients. Attaching to existing owners joins and restarts those same supervisors with fresh bootstrap/epoch, preserving other leases and invalidating cached books; this is not a silent mid-book capture. `await binding.stop()` joins, detaches sinks, restarts remaining browser owners and finalizes. Stream descriptors are registered before capture callbacks.

`ReplaySession` exposes synchronous `pause()`, `set_speed(speed)`, `step(count=1)` and `seek(ordinal, event_index=0)`, plus asynchronous `play(speed)` and `run_to_end(speed=None)`. Cursor includes actual delivery position; seek resolves a whole source transaction. `ReplayClock.reading` uses the current recorded clock's monotonic high-water mark; `source_reading` preserves the exact last-delivered receive clocks. Stored envelopes remain unchanged. Compatible coherent checkpoint groups restore all projected streams; incompatible state follows explicit fallback/reject policy. Registered observers reset and recompute needed past when compatible observer checkpoints are unavailable, never retain future state. Raw replay uses the same decode/normalize/reduce path and strictly compares recorded delivery; scoped duplicate controls are validated local processing evidence, not permission to bypass source identity or provenance. Normalized replay bypasses only decoding. Missing history stays missing. Speed affects pacing, not order, virtual eligibility time or deterministic hashes. Exact controls and observer contract: `REPLAY.md`.

`MetricResult(name: str, value: Optional[Decimal], unit: str, instrument_ids: Tuple[InstrumentId, ...], venue_ids: Tuple[str, ...], asof: EventEnvelopeRef, window_start_ordinal: Optional[Ordinal], window_end_ordinal: Ordinal, window_definition: str, quality: Quality, eligibility: Eligibility, formula_version: str, config_hash: str, input_refs: Tuple[EventEnvelopeRef, ...])` is used by analytics, alerts, comparison, API, research and paper UI. Multi-value metrics use named results, not anonymous vectors. Unavailable values have reasons. Exact-event OFI and snapshot-derived flow have different names/capabilities. Trades alone feed CVD.

`walk_book(book, side, base_quantity: Optional[Decimal], quote_budget: Optional[Decimal], constraints, fees, policy, now) -> ExecutionEstimate` accepts exactly one size input and returns requested/filled/unfilled base, quote spent/received, VWAP, best-price slippage, fee amounts/currencies, effective price, coverage and eligibility. No extrapolation beyond known depth. `compare_pair(buy_book, sell_book, feasible_base_quantity, conversion, fees, inventory_assumptions, latency_stress, now) -> PairEstimate` uses the same feasible size on both sides and explicit conversion orientation/cost. Paper orders use this shared engine at simulated arrival with an explicit liquidity-consumption model; they do not submit exchange orders.

API JSON is versioned (`schema_version`, request/subscription ID, generation, message kind, decimal/time strings, quality and reasons). Planned `/api/v1/instruments`, `/subscriptions`, `/history`, `/sessions`, `/replay`, `/alerts`, `/paper`, `/journal` and stream endpoints validate IDs, range/row limits, size inputs and allowed local origins. `/health/live` reports process liveness; `/health/ready` reports required synchronized capabilities and recording status, not just HTTP success. Slow-client resync uses a fresh validated snapshot plus revision; UI never reconstructs authoritative exchange books from coalesced display frames. Accessible tables use exact formatted values; chart floats are disposable projections.

## Native replacement boundary

Only `BookReducer.apply/checkpoint/restore` and measured pure numeric kernels (`walk_book`, band-depth accumulation) may later cross PyO3. Batch exact scaled integers plus explicit scale/protocol IDs; return the same immutable Book/Reduction semantics. No networking, async scheduling, persistence, credential handling or frontend state crosses this boundary. Parity fixtures must match Python state hashes, invalidation reasons, checksum behavior and deterministic replay before selecting a native path. Until profiling demonstrates a failing budget, Python is the sole implementation.

## Synthetic fixture constructors

`synthetic_instrument(*, venue="synthetic", base="BTC", quote="USD", tick_size="0.01", lot_size="0.001", **overrides)` provides explicit synthetic metadata with available data access and unknown execution access. `SubscriptionKey.for_instrument(instrument, **options)` derives venue/API/ID without invented product mapping. `synthetic_frame(payload=b"{}", *, ordinal=1, epoch=0, mono_ns=1, **overrides)` computes and validates SHA256. `synthetic_envelope(instrument, payload, *, ordinal=1, epoch=0, mono_ns=None, **overrides)` uses demo mode, `synthetic-session`, `synthetic-stream`, `synthetic-clock`, and `synthetic-segment`; default monotonic time is the supplied ordinal. A matching fixture reducer therefore uses `segment_id="synthetic-segment"`. These helpers never participate in live discovery.

Offline consumer regression coverage is in `tests/test_workstation_core.py`: malformed precision/JSON, metadata version collisions, complete-message corruption, ordered repeated-price changes, initial/protocol synchronization, checksum/sequence hooks, depth scope, epoch/order fencing, heartbeat-versus-observation freshness, exact identity/cohorts, string-safe wire roundtrips and complete checkpoint restore. Check execution/results are recorded by the integration owner, not asserted here.
