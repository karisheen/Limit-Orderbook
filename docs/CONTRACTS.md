# Workstation contracts (schema version 1)

The exact instrument/event/quality/book/wire contracts, venue adapters, transport/supervision, recording/replay, analytics/visualization and native alerts/context/paper/journal/research consumers are implemented in `src/workstation`. React and the supported thin Streamlit observation client consume the same native API/core; duplicate `src/data` and `src/domain` compatibility engines are retired. Implementation is not certification of acceptance: executed evidence belongs in `OMP_UPGRADE_HANDOFF.md`. Core instrument/event/book/quality models have no UI, network or database imports; transport/ingestion own network I/O and consumer stores own their SQLite connections.

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
| `analytics.py`, `costs.py`, `visuals.py` | Metrics, depth walks, costs/cohort/conversion policy and bounded visual history |
| `recording.py`, `replay.py` | Capture, manifests, recovery, deterministic clock/order |
| `market_presentation.py`, `candles.py` | Bounded admitted-trade history, projected source clock, cohort books/trades/candles, retention and delivery cursors; explicit separate venue OHLCV |
| `capture_service.py` | Session-owned managed capture lifecycle, bounds and restart discovery |
| `alerts.py`, `context.py` | Quality-gated local alert evidence and non-executable references |
| `positioning.py` | Bounded minute history of observed perpetual open interest/funding on the admitted candle clock |
| `paper.py`, `perpetual.py`, `journal.py` | Spot IOC and modeled isolated perpetual accounting, durable command/archive state and manual rationale |
| `research.py` | Causal offline experiments, durable attempts/evidence/all trial outcomes |
| `api.py`, `runtime.py`, `wire.py` | Versioned routes, owned sessions/consumers, input/origin limits and exact codecs |

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
| `tick_size`, `lot_size` | Optional[Decimal], Optional[Decimal]; positive when known; null lots are allowed only for streaming-only metadata with an unconstrained size rule |
| `constraints` | OrderConstraints |
| `contract_multiplier`, `quantity_unit` | Decimal; `base` or `contracts`; multiplier unit is explicit in `contract_value_currency` |
| `contract_value_currency`, `inverse` | Optional[AssetId], bool; inverse contract conversion is explicit, never base-size multiplication by assumption |
| `reference_multiplier` | Optional positive Decimal; separate underlying-reference ratio, not a contract multiplier or token-book conversion |
| `settlement_conversion` | `unknown` (default), `none`, or `quanto_no_fx`; `quanto_no_fx` requires non-spot and a settlement asset |
| `contract_specification_source` | Optional nonempty text; adapter-provided provenance for contract/settlement classification |
| `status` | `active`, `suspended`, `delisted`, `unavailable`, `unknown` |
| `data_access`, `execution_access` | Each `available`, `blocked`, `unsupported`, `unknown`; public readability does not prove trading eligibility |
| `metadata_asof_wall_ns`, `metadata_source` | WallNs, source endpoint/revision string |
| `metadata_status` | `verified` (default) or `streaming_only`; independent of listing/access status |

`InstrumentCatalog.resolve(venue: str, api_family: str, native_symbol: str, metadata_version: Optional[str] = None) -> Instrument` raises `UnknownInstrument`, never USD fallback. `get(instrument_id, metadata_version=None) -> Instrument`; `list(query=InstrumentQuery()) -> Tuple[Instrument, ...]`; `apply_metadata(snapshot: MetadataSnapshot) -> Tuple[Instrument, ...]` is transactional and retains old immutable versions for replay. `InstrumentQuery(base_id=None, venue=None, market_type=None, quote_id=None, status=None)` filters latest versions. `MetadataSnapshot(source, fetch_wall_ns, payload_hash, instruments)` is immutable. Content changes under an existing version, stable identity changes and native-symbol collisions fail. Latest selection uses metadata as-of time; discovery remains outside the pure catalog.

`streamable` permits active listings and unknown listings explicitly marked
`streaming_only`, never suspended/delisted/unavailable listings. Streaming candidates
retain null increments and validate finite positive prices/nonnegative quantities
without invented exchange precision. Financial comparison, depth walks, paper fills,
order validation and proposed risk reject `instrument_metadata_unverified`.
Enrichment may supply a previously absent native asset ID only when transitioning
from streaming-only to verified; prior immutable versions remain addressable.

Bybit xstock `reference_multiplier=m` describes reference stock quantity `token_quantity*m` and stock price `token_price/m`; it never changes native token quantities, `contract_multiplier`, or order notional. A `settlement_conversion="none"` classification does not require matching quote/settlement asset IDs: native collateral may have a distinct identity. Unknown conversion remains explicit, and no FX conversion is invented. Source provenance is optional at construction but supplied by adapters when sourced classifications are known.

Tagged Instrument wire encoding omits default `metadata_status="verified"` to preserve existing capture bytes and identities; decoding its absence supplies that default. Streaming-only records retain the explicit status and null increments. Other required Instrument/OrderConstraints fields remain strict. HTTP DTOs expose metadata status explicitly.

Default executable-comparison candidate cohort is Coinbase/Kraken BTC/ETH/SOL spot USD subject to discovery/access. USDT and USDC are separate cohorts. Hyperliquid BTC/ETH/SOL perpetuals, HYPE where listed, and metadata-discovered spot tokens stay separate throughout capture, UI and replay.

Live terminal selection retains the exact selected instrument and fills the existing
16-product bound with streamable same-base peers, one per venue/market group.
Selected market type takes priority; quote matching and the indicative USD/USDT/USDC
display family determine eligible peers. Suspended, delisted and unknown implicit
peers without explicit streaming-only provenance cannot block a streamable product.
Explicit selection still passes subscription validation. Collection membership is
not executable comparability; quote identities and native book depths stay intact.

Public crypto adapters include Binance global spot/USDT USDⓈ-M perpetuals, Bybit
spot/USDT linear crypto perpetuals, OKX spot/linear swaps, Bitget spot/USDT perpetuals,
Deribit BTC/ETH inverse perpetuals, Hyperliquid, Coinbase, Kraken, KuCoin and
Binance.US. Discovery excludes Bybit linear TradFi `symbolType` values `stock`,
`ETF`, `commodity` and `forex`, and Bitget perpetual rows marked `isRwa: "YES"`
or not classified as `symbolType: "perpetual"`. This does not rewrite native
crypto contract metadata, or erase reference metadata from existing captures.
Provider presence is not certification of every symbol, channel or region.

Binance `spot-v3` / `usdm-v1` and Bybit `v5-spot` / `v5-linear` metadata
discovery runs per family with independent deadlines. Publish only complete
family snapshots: a sibling failure does not discard successful native metadata,
and an incomplete Bybit linear cursor walk never becomes a partial product list.
Per-family `discovery_evidence` retains state plus endpoint or failure reason.
`VenueStatus.dependencies` includes `metadata:<api_family>` and aggregate
`metadata`. Mixed success yields venue `degraded`, aggregate `partial`, and
`metadata_partial` detail. When every family fails, preserve the common failure
state or aggregate differing states to `unavailable`, with `metadata_unavailable`
detail. No replacement products, invented constraints or hidden exchange fallback
are allowed. Metadata discovery alone leaves data/execution access unknown.

The live terminal explicitly seeds 116 bundled subscription candidates (29 crypto
assets across four Binance/Bybit families) before discovery. This is protocol
subscription knowledge, not synthetic market data or confirmed listing metadata.
REST failure retains candidates; an authoritative successful family marks missing
products unavailable and prevents later bootstrap resurrection. Existing shared
streams and captures pin their original versions; observers resolve metadata by
stream/book identity rather than overwriting it with a same-instrument trade version.

Exact-product probes retain `probed_instrument_id` and derive `metadata:product`
from its family. `fully_usable` requires that product's metadata plus available
snapshot, trades and WebSocket evidence; a successful probe may coexist with
aggregate partial metadata. Its `available` state applies to the probed product,
not all venue families. Bybit uses `api.bytick.com`, an
[official equivalent mainnet REST host](https://bybit-exchange.github.io/docs/v5/guide),
without bypassing provider restrictions.

At live ownership cutover, paper observation removes departed book keys **before**
awaiting collector replacement. Pending arrivals cannot consume departed books
during network cleanup/bootstrap. Retained keys keep their causal evidence, and
run identity, balances, prior fills and alert rules survive the switch.

The analytical queue remains fail-closed at 256 deliveries / 128 MiB. Each atomic
delivery retains only its affected books, charged conservatively at 512 bytes per
level plus fixed overhead. Event representation size is computed once and reused
for bounded history accounting. Trade-only deliveries reuse the immutable current
book tuple; paper skips an identical already-scheduled book object. Analytics
indexes immutable instrument-to-trade-stream relationships instead of validating
identifiers and scanning all streams for each lookup.
The owner takes at most32 already-admitted deliveries per executor call, preserving
every atomic batch and its original order. Detached in-flight batches remain charged
to both count and byte bounds until completion or cancellation has joined the worker;
there is no second unaccounted queue. This is scheduling, not analytical coalescing.
Run activation first clears the scheduled-source identity cache, then reschedules
its observed baseline under the configured feed latency. The steady-state object
identity optimization cannot skip reconstruction after rewind or durable resume.

Analytics caches only book-derived depth totals and frozen standing evidence within
the owning book window. New immutable books invalidate depth totals; book observations
invalidate standing evidence. Reset/restore discards both caches. Freshness, clock,
rolling windows, trade evidence and quality eligibility are still evaluated per query.
The caches do not change canonical checkpoints, source depth or metric formulas.

`evidence/m5-lifecycle-offline-summary.json` records complete state/history parity
before and after this service correction. `evidence/m5-lifecycle-capacity-summary.json`
preserves both the misleading same-event-loop raw-decoding stress and its corrected
dedicated-producer experiment. The latter applied all 703 retained deliveries at
the unchanged 8 MiB bound, with cold/steady highwaters of 6,626,908 / 5,131,912 bytes.
Its diagnostic 256 MiB comparison did not increase those highwaters. This serialized
12-stream experiment does **not** certify concurrent 16-product live bootstrap:
two large atomic snapshots can exceed 8 MiB, and no retained Coinbase 40k-level
payload was available. Original public overload evidence remains a failed run.

The full16-product native run subsequently exceeded the original8MiB budget.
After removing repeated immutable full-depth analytics work, a diagnostic64MiB
run completed all three BTC/ETH/SOL paper/alert/context workflows, but reached
64,597,682 accounted bytes for BTC. One further approximately5MiB full-depth
book would exceed that allowance. The production128MiB limit supplies bounded
burst headroom without reducing depth, dropping events or changing the256-item
bound. This is admission accounting, not a claimed RSS limit or sustained-load pass;
the independent live-history retention budget remains8MiB.

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

Protocol obligations: Kraken v2 applies all changes, truncates to subscription depth, checks top-10 CRC32 on every update; Coinbase Advanced Trade `level2` receives `l2_data` with absolute `new_quantity` and per-connection sequence handling; Binance global spot buffers diff events while fetching REST snapshot, bridges update IDs according to documented spot rules and resyncs gaps; Binance USDⓈ-M and US partial top-N replace bounded snapshots without exact-flow claims; Bybit spot/linear new snapshots and service restart `u=1` replace state without invented `u+1` continuity; Hyperliquid l2Book replaces bounded snapshots and records actual aggregation/cadence with no fictional checksum/sequence. Missing sequence support is a capability limitation, not a pass.

OKX public WebSocket is `wss://ws.okx.com:443/ws/v5/public`. Connection-wide
`notice` envelopes become informational `Control("clock_tick", "venue_notice:<code>", detail)`,
not errors, acknowledgments, heartbeats or book/trade observations. Notices cannot
establish synchronization or healthy market-data evidence.

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

This lease independence is not a managed-capture lifetime promise: the terminal's
`CaptureService` explicitly finalizes captures when their owning session closes.
Unrelated browser leases remain independent.

`AdapterCatalog.canonical_key(key)` supplies omitted bounded book depths before registry acquire/replace: Binance global spot 5000, Binance global `usdm-v1` 20, Binance.US 20, Kraken 25, Bybit 50, KuCoin 20, and Hyperliquid 20 (5 with `fast=true`). Explicit depth and aggregation are preserved; non-book keys are unchanged. Omitted and explicit default depths therefore share the same lease/physical owner, including same-generation replacement; different reconstruction settings remain different keys.

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

Terminal JSON uses `version:'1'`, session ID and generation, exact decimal/domain-integer strings, quality and reasons. Implemented routes and bounded request/response fields are specified in [API](API.md), including alerts, context, paper, journal and read-only research queries. `/health/live` reports process liveness; `/health/ready` reports synchronized active-session eligibility, not just HTTP success. UI never reconstructs authoritative exchange books from coalesced display frames. Accessible tables retain exact values; chart floats are disposable projections.

### Native market presentation

`POST /api/v1/market-data` is an owner-serialized read of completed canonical
deliveries, not a second exchange feed or an authoritative browser reducer.
Generation is checked before and under session ownership fences. The exclusive
cursor is `(delivery:string,event:number)`, initially `('0',-1)`; `event` is a
bounded array offset, unlike string-valued domain ordinals. It advances across
nontrade/out-of-cohort events as well as returned trades and can resume inside
one atomic delivery. Consumers must use the response cursor, not infer it from
the last trade.

`highwater` is the completed live delivery index or current replay cursor.
Replay never exposes later recorded events. `gap` marks a cursor behind live
retention or ahead of the current frontier and restarts scanning at the
available boundary; it does not repair missing history. `has_more` requires
another sequential page, even when the current page has no trades. Scan bounds
are 10,000 events / 10,000 batches, a combined trade/boundary limit of 1–2000
(default 1000), and 2 MiB estimated trade/boundary representation. A canonical
event's fan-out boundary group is never split: it can exceed the count/size
bound when admitted first on a page. Live history remains independently bounded
to 4096 batches / 8 MiB. This is not a total response-size or unlimited
historical coverage guarantee.

The display cohort is the selected instrument plus active same-base,
same-market session peers, bounded to 16 with venue-fair selection. It is not
an executable comparison cohort: quote and settlement identities remain native.
Books are current frontier snapshots, not historical snapshots at the page
cursor. They retain native quality/provenance/coverage, expose at most 100
levels per side with omission counts, and add display `eligibility` evaluated
at source `now` (nullable before clock evidence). Diagnostic ineligible books
may remain visible.

Book levels and `TapePrint` records add exact-string-or-null `base_quantity`
and `quote_notional`, alongside unchanged native price/quantity. Valuation uses
native contract metadata: base quantity times multiplier for base units or
documented non-inverse base-valued contracts; quantity times multiplier divided
by price for documented inverse quote-valued contracts. Inverse quote notional
is directly `quantity * contract_multiplier`, not the rounded base quotient
multiplied back by price. Other supported valuations use base quantity times
price under the native Decimal context, never a cross-quote conversion.
Unsupported metadata or trade-unit mismatch leaves both derived values null;
reference multipliers never substitute for contract units.
Trade records additionally carry `instrument_id`, delivery/event `cursor` and
`source_identity:{session_id,segment_id,clock_id,connection_epoch}` (all strings),
preserving original event reference, aggressor provenance and subscription-history
flag. They are not individually eligibility-certified.
`display_time_ns` additionally carries the exact source receive-clock projection.
Its origin is the first receive monotonic/wall pair for `(session_id,clock_id)`;
the timestamp is `origin_wall + receive_mono - origin_mono`. Wall-clock
corrections do not reanchor already admitted trades. Incomparable source clocks
cannot share history. `display_frontier_ns` is the same projection of the source
monotonic frontier (nullable without an origin), not the last trade or page
cursor. It can close existing buckets on a clock-only delivery without synthetic
empty bars. Late admitted prints can revise a time-closed bucket. Convert to
plotting milliseconds by exact integer division before Number conversion and
reject unsafe plotting ranges, rather than rounding epoch nanoseconds.

`AdmittedTradeHistory` retains at most 10,000 rows / 8 MiB of accounted trade
representation, independently of live raw history. Live raw-prefix trimming
also calls `prune_through(delivery,event_index)` so candles cannot outlive
obtainable market-feed rows. Every market response carries
`retention:{floor:{delivery,event},stream_floors:{[stream_id]:{delivery,event}}}`.
These floors are inclusive retirements; consumers prune through them even when
`gap:false`, including on empty/caught-up pages. Global rollover preserves
still-retained rows; stream retirement preserves unaffected peers and depth.
`source_identity`, `time_basis:"receive_utc"` and `display_time_reasons` expose
the owning source and clock limitations.

The additive `boundaries` array carries `{instrument_id,stream_id,cursor,asof,
kind,reason}` for cohort trade-stream gaps, resets, disconnects, source changes
and rejections. Gaps fan out only to affected trade streams; ordinary heartbeats
and book-only controls do not reset trade studies. Consumers merge boundaries
and trades by cursor and compare source identity per stream across pages.
Either a boundary or identity transition restarts that stream's trade continuity.
The browser removes its obsolete retained trades and rebuilds studies from the
remaining evidence, preserving independent depth/OBI history and other streams.

Source `now` and event receive/exchange clocks stay distinct; replay uses its
recorded monotonic frontier, never today's browser wall time. Native ownership
(session/generation/source/instrument) changes reset all display caches; a cursor
retention gap clears obsolete trade history without inventing replacement prints.
Display interval, tick/indicator and venue changes recompute retained trade
inputs without creating a native session. A changed depth cohort separately
resets depth history; ordinary trade-study rebuilds preserve it. Busy mutations
pause bridge polling without clearing same-owner caches/cursor.

Status lag subtracts exact exchange-event wall nanoseconds from receive-wall
nanoseconds before converting to display milliseconds. Missing exchange time,
subscription-history prints, negative differences or nonfinite projections are
unavailable, not zero. Positive differences still include source clock skew.
Replay labels this **Recorded lag** (capture-time receipt, not playback latency);
demo labels it **Synthetic lag**. Display floats/studies cannot become financial
inputs. Full request and response contracts are in
[API](API.md#native-market-data-delivery).

### Unified native candle contract

Strict `CandleRequest` selects `source:"session_trades"` by default in every
mode, optionally explicit `venue_ohlcv`. Its optional bounded `instrument_ids`
is primary-only when empty; an explicit cohort must contain the primary,
have no duplicates and belong to active pinned same-base/same-market streams.
At most 16 IDs are accepted; canonical output is primary first, then sorted peers.
Aggregate `instrument_ids`, `scope`, `venue_label` and `quote_asset_ids` describe
the actual requested cohort, not a single-venue label. Cross-quote aggregation
is indicative, with no conversion or financial use.

Native admitted trades drive both Price Action and Footprint/studies at the
same selected cohort/global interval. The fifteen intervals are
`1m,3m,5m,15m,30m,1h,2h,4h,6h,8h,12h,1d,3d,1w,1M`.
Fixed buckets floor projected UTC nanoseconds against Unix epoch (including
Thursday-origin weeks); calendar months run UTC first-to-next-first.
Delivery order determines open/close; unknown base sizes, subscription history,
unadmitted/future/unrelated-clock prints cannot supply volume. No empty candles
are invented. `closed` is only the source frontier passing exclusive `end_ns`,
not a completeness guarantee.

Native results expose source/session/generation, `highwater`, exact decimal
OHLC/base volume, `bucket_alignment`, `missing_instrument_ids`, and
`retention:{max_trades,max_bytes,retained_trades,retained_bytes,truncated}`.
They always report partial retained-session coverage; first/last contributing
trade times of returned bars define coverage, not bucket edges. Boundaries,
source/clock changes, retention and seek reconstruction fence obsolete data.
Frontend source/generation/cohort/interval changes fence obsolete responses.

Venue OHLCV is an explicitly separate primary-only exchange-UTC query, not
startup input, a replay-prefix substitute or automatic fallback. Exact native
family/market/interval support and access failures stay visible; provider bucket
alignment may differ from native epoch flooring. Binance spot/USD-M and Bybit
spot/linear select their own real native families, not a spot substitution.
All fields, bounds and optional-history limitations are in
[API](API.md#unified-native-candles-and-explicit-venue-history).


### Managed capture lifecycle

`CaptureService` exposes `/api/v1/recordings` (GET) and
`/api/v1/recordings/start` / `/stop` (POST). Start accepts exactly two scopes:
`instrument` (default), all currently owned primary-instrument keys including
reference/context channels; or `cohort`, all currently owned native session
keys. Default capture does not require unrelated cohort channels.
`RecorderBinding` retains independent leases and its fresh bootstrap/epoch
contract. Scope/key membership is frozen at start; later display selection or
session-cohort changes do not retarget capture.

Start/stop require current session/generation; stop additionally takes the
opaque `id` and requires its active owner. A failed feed remains stoppable.
Responses list rows `{id,label,status,source_id,reasons,session_id,scope,
instruments,channels}`; mutations also return the affected `recording_id`.
`instruments` contains native IDs; `channels` contains
`{venue,instrument_id,stream}` summaries, with `stream` naming the channel.
Scope/instruments/channels persist in managed metadata across restart; the
native manifest retains full key/evidence detail. Metadata is bounded to
24 instruments, 48 channels/keys and 64 KiB.
Row `session_id` is the active terminal owner (null after finalization/restart),
not the native manifest session ID. Active incomplete/error rows can still
have an owner. Stop finalizes and verifies; only complete usable captures
receive replay sources immediately. Scope metadata is not evidence of receipt:
missing channels or incomplete captures are never made complete by scope choice,
successful stop or restart.

Bounds are one active capture per session, two active per server, 64 retained
catalog entries and a 128 MiB managed budget per capture. Capacity fails with
429 rather than evicting an older row; no HTTP deletion is exposed. Ownership
cannot transfer between sessions. Session close, including stream disconnect,
finalizes owned captures; server shutdown finalizes all before registry close.
Cancellation-shielded mutations may commit even if their HTTP caller leaves,
so clients inspect the list before retrying.

Opaque managed paths live under `--state-dir/recordings`; clients submit IDs,
never paths. Startup recovers/verifies these directories, re-registers complete
replay sources and exposes interrupted/corrupt entries with incomplete/error
reasons, never healthy fallback data. Embedded temporary state is not durable.
See [API](API.md#managed-recordings) for exact fields and rejection semantics.

## Native M5 consumer contracts

### Alerts: intent, qualification and evidence

`AlertEngine` shares admitted `AnalyticsEngine` evidence, not a second feed or
browser-calculated signal. Rule commands apply after their recorded delivery
cutoff; rules, hysteresis latches, cooldown and fires are a replay-prefix
projection. Seek/reset reconstructs them without future evidence. Rules are
session-local, not a durable notification service.

Supported kinds are spread shock, depth shock, large executed print, event OFI,
best-quote imbalance, persistent displayed liquidity and net cost. Thresholds
are absolute levels: a depth shock is a low-depth condition, not an inferred
percentage change. Units are native base/instrument units, ratio, bps or native
quote as appropriate. Inclusive threshold crossing fires only while armed and
outside cooldown; rearm must lie beyond the nontrigger side. OFI and persistent
display rules require a positive rolling window, other kinds zero.

`AlertEvaluation` retains the rule, delivery/clock, value or null, eligibility,
window, latch/cooldown, condition and reasons, plus applicable metric references,
executed prints, displayed evidence, pair estimates and book/trade evidence.
Trades are not inferred from depth disappearance; snapshot liquidity is not
exact event OFI, passive fills, spoofing or predictive intent. Unsupported flow,
incomplete windows, stale/gapped sources, incompatible units and unavailable
fees exclude the affected calculation rather than producing a zero signal.
Net-cost alerts use shared paired size/cost assumptions, not account execution.

Native bounds are 64 rules, 256 retained fires, 4096 commands and 200 supporting
evidence items per bounded collection; overflow/unavailable reasons remain
visible. HTTP summaries omit heavy evidence and expose omission counts; explicit
evidence queries retrieve retained current-rule or historical-fire detail.

### Hyperliquid context and reference basis

`ContextEngine` observes reference and context streams independently of the book.
Hyperliquid mids, mark/oracle, funding rate/interval and open interest retain
instrument identity, source as-of and field-specific missing/eligibility reasons.
Funding is a fraction per funding interval, not an annualized return; open
interest units are adapter-declared, not independently verified. Reference-only
prices cannot repair missing depth, trigger fabricated trades or establish
executable liquidity.

Related spot/perpetual rows compare eligible spot mid with `mark`, `oracle` or
reference `mid`. A ticker can discover a candidate explanation but canonical
underlying identity, unit/multiplier compatibility, independent channel gates,
clock/freshness/skew and exact quote conversion determine availability. Wrapped
or otherwise distinct assets never become fungible because their tickers match.
USD/USDT/USDC quote and settlement identities remain separate. Without required
fresh, side-appropriate FX and conversion fees, cross-quote basis is unknown.

`ReferenceBasis` retains spot/reference as-of, native amounts, both FX conversions,
proceeds/funding basis and reasons under
`reference_basis_not_executable_arbitrage`. Here “funding cost” means FX input
required to acquire the reference amount; it is not perpetual funding P&L.
Cost scope is explicit FX spread/conversion fees only, excluding trading fees,
funding P&L, inventory, borrow/margin or a realizable arbitrage trade.

### Observed positioning history

`PositioningHistory(streams, clock)` is a replay observer over perpetual
`context` streams (at most 16). `ContextEngine` keeps only the latest
observation; this keeps a bounded series. It runs immediately after
`AdmittedTradeHistory` in the observer order and requires that history to have
consumed the same delivery, because the trade history's first receive pair per
source clock is the single projection origin for candles, tape and positioning.

Each admitted `AssetContext` carrying open interest or funding folds into a
`PositioningBar` keyed by the UTC minute of its projected receive time:
open-interest open/high/low/close, last funding rate with its interval, last
mark/oracle, first/last observation time and sample count. At most 10,080
minutes are retained per stream; eviction sets `truncated`. A funding interval
is stored only with a funding rate. Out-of-order minutes are rejected with
`nonadvancing_context_clock`, an unprojectable event with
`unprojectable_context_clock`.

Gap/reset/disconnect/source-change/rejection controls after a first observation
add `context_discontinuities` but retain observed bars. A different
`(session_id, clock_id)` clears all bars; a changed open-interest unit clears
that stream. `reset`, `checkpoint` and `restore` follow the observer protocol;
restore rejects another stream set, unordered or misaligned minutes, invalid
envelopes and unitless open interest.

`positioning_series` aggregates minutes with `session_bucket`, the same function
as session candles, for every stream whose canonical base asset matches the
selection. It adds no value that was not observed and reports the admitted
clock's own discontinuity reasons. It does not feed paper accounting:
`PaperFundingModel` remains an explicit user assumption.

### Paper funds, fees, time and consumed liquidity

`PaperEngine` implements spot and supported isolated one-way perpetual
market/marketable-limit IOC using exact native books and consumed-depth evidence.
`PaperConfig` contains `balances:tuple[InventoryBalance,...]`,
`fees:tuple[PaperFee,...]`, `feed_latency_ns`, `decision_latency_ns`,
`order_latency_ns`, `policy:EligibilityPolicy`,
`risk_models:tuple[PaperRiskModel,...]` and
`funding_models:tuple[PaperFundingModel,...]`. Core collections are bounded to
256 each (HTTP 64); balances are unique by venue/asset and each fee/model list
by instrument. Latencies are nonnegative and at most one day. Feed delay gates
book availability, including the initial baseline; decision plus order delay
fixes arrival. Pending commands compete for funds at arrival, not submission.
No cross-margin, cross-venue transfer, spot borrowing, passive queue priority
or market impact is modeled.

`PaperOrderRequest(instrument_id,side,quantity,order_type="market",
limit_price=None,reduce_only=False)` retains native quantity units. Fill
eligibility includes exact product constraints, verified metadata, source clock,
quality, available collateral, dated known fees and observed unconsumed depth.
Unknown/future-dated fees or missing arrival wall time exclude fills. Spot fees
use base/quote; perpetual fees use settlement, never an unmodeled third token.
Partial fills cancel the IOC remainder; pending cancellation is a durable
command, not an exchange message.

The active API `capability` describes only the exact pinned primary's
`instrument_id,metadata_version,market_type,quantity_unit,settlement_asset_id,
supported,reasons,native_order_constraints`. Support is intrinsic economics,
not book readiness, model completeness, collateral or venue execution access.
Clients fence both ID/version and must not substitute latest discovery metadata.
`native_order_constraints(instrument)` supplies the financial interpretation of
`OrderConstraints`: `min_base,max_base,max_limit_base,max_market_base` are native
quantity sizes despite their historical names; `min_notional,max_notional` keep
`min_notional_currency`, with `price_rule,size_rule` explicit. Null is unknown.
Only tightly identified historical OKX `okx-v1` SWAP descriptors have their
erroneously contract-value-scaled size limits reversed by this helper; raw
captured descriptors, hashes, as-of and metadata versions are unchanged.

Reservations are keyed by native instrument, side and price and survive
unsubscription. Healthy displayed decreases retire consumed quantity down to
the new size; only observed increases replenish availability. Disappearance
inside observed coverage followed by reappearance is replenishment; disappearance
outside truncated coverage is not. Heartbeats, unchanged snapshots, revision
changes and cohort replacement do not create reusable liquidity.

Spot balances, orders, fills and positions retain exact native assets/venues,
source clocks/deliveries and book key/revision/as-of. Spot accounting remains
average-cost; initial inventory basis is unknown, so affected basis/P&L is null.
Spot marks are gross best bid, not guaranteed liquidation proceeds.

#### Supported perpetual economics and isolated accounting

Supported verified contracts need interpretable native valuation and fixed-lot
sizes. Ordinary linear contracts settle in quote; inverse quote-face contracts
settle in base with `settlement_conversion="none"`. Default-namespace Hyperliquid
linear `quanto_no_fx` contracts are supported only with the recognized official
contract-specification provenance: the documented native payout denomination is
used, not an invented USDT/USDC FX parity. Unknown/builder economics and unsupported
settlement remain rejected. Native notional is not available cash or margin.

Positions are keyed by exact `(venue,instrument_id)` and retain their captured
`Instrument`. Free balances are keyed `(venue,settlement_asset_id)`, separate from
spot base inventory. Signed native quantity is one-way: buys open longs or close
shorts; sells do the converse. Opposite orders close before any eligible opening
remainder. `reduce_only` clamps to existing exposure and cannot increase or flip
it. Opening size is capped to free collateral for initial margin plus fees,
rounded down to native lots and still subject to native order/depth bounds.

For native signed quantity `q`, multiplier `m`, entry `E`, exit/mark `P`:

- Linear exposure is `q*m`; gross P&L is `q*m*(P-E)` and entry margin is
  `abs(q)*m*E/leverage`. Additional fills use quantity-weighted entry.
- Inverse face is `q*m`; gross base-settled P&L is
  `q*m*(1/E-1/P)` and entry margin is `abs(q)*m/(E*leverage)`.
  Additional fills use harmonic entry.

All arithmetic uses the declared Decimal context, not plotting floats.
Opening removes margin plus trading fees from free settlement collateral.
Closing releases proportional original `initial_margin`, realizes gross P&L
and charges fees/any protective penalty against isolated collateral; only the
available funded share returns to free collateral. A flat negative remainder
is retained as `debt`, not debited from another position or silently wiped.
`isolated_collateral`, `initial_margin`, cumulative gross `realized_pnl`,
`fees_paid`, `funding_paid` and `liquidation_fees_paid` remain distinct.

`PaperRiskModel(instrument_id,leverage,maintenance_margin_rate,
liquidation_fee_rate,provenance,asof_wall_ns)` is an explicit dated assumption:
leverage 1–1000, nonnegative maintenance below `1/leverage`, penalty below one.
It is not a venue risk-tier schedule. Missing/future models or unavailable model
date clocks reject exposure. An eligible executable-side book supplies the
adverse proxy mark (long bid/short ask), explicitly
`eligible_executable_side_book_proxy_not_exchange_mark`.
`unrealized_pnl` uses that mark; `equity = isolated_collateral + unrealized_pnl`;
maintenance is marked settlement value times the modeled maintenance rate.
Missing/stale/incompatible marks leave values null with reasons, not zero.

Equity at or below maintenance triggers protective reduce-only IOC through real
remaining eligible depth, with ordinary trading fees and a separate modeled
liquidation penalty. User orders and protective fills share consumed-depth
reservations. Partial/missing depth does not invent closure: `liquidating`,
remaining quantity, shortfall/debt and reasons remain visible. Risk cannot
increase for a troubled position or another position sharing its venue and
settlement asset while debt/liquidation/unresolved reasons persist. The bounded
10,000 liquidation-record capacity leaves risk frozen with
`liquidation_record_limit`, not an invented settlement.

#### Funding, clocks and bounded freeze

`PaperFundingModel(instrument_id,mode,provenance,asof_wall_ns,rate=None,
interval_ns=None,first_wall_ns=None,events=())` is explicit:
`none` declares no funding; `periodic` requires signed rate, positive interval
and first boundary; `events` contains chronological
`PaperFundingEvent(wall_ns,rate)` records. Periodic inputs and events cannot be
mixed. Rates have magnitude below one; boundaries cannot predate the model.
Missing funding is unavailable, never implicit zero.

Funding uses the run's same-clock wall/monotonic anchor to schedule deterministic
boundaries, not browser/request time or an unrelated process clock. When a replay
frontier omits wall time, command dates project already received same-clock
evidence; no today's-wall-time substitution is allowed. Per-instrument
`PaperFundingCursor.through_wall_ns` prevents duplicate settlement across
checkpoint/restore; full reset reconstructs from commands and delivered history.
At a boundary, payment is marked settlement value times signed funding rate
times position direction. Positive `amount` debits isolated collateral;
negative credits it. A missing eligible mark records null amount and
`funding_mark_unavailable_payment_unresolved`, not zero payment or guessed mark.

Due catch-up is preflighted against the 10,000 funding-record bound **before**
monetary projection mutation. Overflow raises `funding_catchup_limit`; runtime
and replay catch that specific error, freeze only paper, and continue non-paper
observations. Frozen snapshots retain the last valid accounting delivery/clock,
with `paper_frozen` and the cause; queries do not advance them. Orders, time and
observer mutation cannot silently skip funding. Checkpoints preserve
`frozen_reason`. At capacity, `none` and exhausted finite schedules remain usable;
only exposure needing further funding records is fenced.

`PaperSnapshot` adds `perpetual_positions`, `funding_payments`, `liquidations`
alongside spot positions, balances, orders, fills, reasons and assumptions.
`PaperPerpetualPosition`, `PaperFundingPayment` and `PaperLiquidation` retain
the exact fields listed in [API](API.md#paper-and-journal-semantics), with
settlement units and nullable unresolved valuation. Liquidation `fill_id` links
ordinary fee evidence; its `fee_amount` is only the protective penalty.
HTTP independently slices newest orders/fills/payments/liquidations before
serialization (default 20, maximum 100 each), reporting every limit/total/omitted
count. Each fill carries at most 20 consumed levels plus omissions. Full bounded
accounting remains durable; presentation truncation does not erase it.

### Durable state, archive, resume and manual provenance

`serve --state-dir` defaults to `data/workstation-state`, holding
`paper.sqlite3`, `journal.sqlite3`, `research.sqlite3` and managed `recordings/`.
SQLite connections belong to their dedicated worker; serialized immutable
records preserve exact values. The embedded runtime without a state directory
instead uses temporary storage. These databases supplement immutable recording
files, not replace them.

Paper persists full run configuration/captured metadata, ordered commands and
the latest genuine transition archive per run. Archive queries are read-only
historical accounting (`archived_not_live_projection`); advancing genuine
transitions may replace the stored latest snapshot, but rewinds, failed resume,
fresh child configuration and later chart frontiers cannot rewrite that historical
accounting into an earlier/invented state. Stale command revisions are disclosed
as `archive_command_revision_superseded`. No archive seeds active replay state.

Explicit configuration always creates a new run, linked by `parent_run_id`
when replacing one, with supplied new funds/models rather than inherited
accounting. Runtime passes **current** streams, pinned descriptors, books,
source clock and delivery, including after a cohort switch or funding freeze.
A frozen engine requires current books and a non-backward delivery. Reset clears
the freeze for genuine reconstruction, not automatic continuation after skipped
input. Old commands and historical accounting remain separate.

New clients do not auto-adopt runs. Replay/demo resume checks native source
identity and independently rebuilds a candidate through the current cursor.
`future_commands_fork_required` is a **mutation fence, not incomplete accounting**:
a valid historical prefix can resume, while order/cancel commands stay blocked
until the source catches up or configuration creates a fresh child.
Frozen, unconfigured, inactive, stale-revision, wrong-clock or delivery-incomplete
candidates reject atomically with `paper_resume_incomplete_accounting_prefix`.
Failed staging/durable publication leaves the active projection unchanged.
Successful cutover pauses playback and increments generation. Archives do not
supply future fills; pending future commands prevent backward archive publication.

Tagged durable wire stays strict. Only exact recognized pre-perpetual shapes
receive defaults: `PaperConfig` missing both model collections;
`PaperOrderRequest` missing `reduce_only`; `PaperSnapshot` missing all three
perpetual collections; `PaperCheckpoint` missing `perpetual_positions`,
`funding_payments`, `funding_cursors`, `liquidations`, `frozen_reason`.
Partially migrated shapes, other missing fields, unknown fields and numeric
decimal fields still fail. Native Instrument identity compatibility remains
separate. SQLite accepts schema versions 0/1/2 and retains version 2; derived
accounting/checkpoint values round-trip under `DERIVED_DECIMAL_POLICY`.

Recording identity includes session ID and immutable manifest hash; demo
identity hashes its source descriptor. Live identity includes clock and client
session, so live history cannot resume under a new owner. Source or generation
changes fence requests and obsolete presentation. A cancelled HTTP mutation may
already be committed; clients inspect state rather than automatically retry.

`JournalEntry` is manually authored evidence: entry ID, source/session,
delivery/clock, instrument/venue, rationale, optional entry/stop/context and
optional run/order links. New links must refer to the active run and a visible
matching-instrument order. Editing text/prices cannot reassign original
provenance or links. Source-scoped/all-source query is explicit. Journal notes
remain historical authorship, not observations available to an earlier replay
prefix and not research features or alerts.

### Offline research and applicability

`research run` accepts a verified capture or explicitly synthetic demo source;
`research list` retains attempts including failures; `research show` pages
results by attempt ID. See [DEMO](DEMO.md) for configuration/CLI usage. The
terminal's Research tab is read-only and uses the same state directory.

`ResearchStore` persists a begun attempt before opening/scanning its source.
Running, completed and failed attempts remain inspectable; a killed worker can
leave `running`, never fabricated success. Invalid configuration rejected before
an attempt begins is not an experiment. Source/config hashes, observation/label
evidence and all candidate trial outcomes are retained; publication of the
finished report is atomic. Query pagination does not discard nonwinning trials.

Hypotheses are depth-band imbalance, exact event OFI and known-side executed
trade flow. Features use only admitted prefixes. Outcomes use the last available
book at exact receive-clock deadlines, not a later book borrowed backward.
Default horizons are 1/5/30/60 seconds; default scenarios are base, extra
fee/slippage and one-second latency. Unknown fees exclude net economics, not
silently become zero. Gaps/continuity changes, unavailable depth, partial fills,
unknown flow, incomplete capture and censored outcomes retain exclusion reasons.

Chronological partitions default to 50% train, 25% validation and the remainder
test. All variants share splits: a closed outcome interval including the maximum
horizon plus maximum scenario latency that touches a boundary is purged; later
partitions embargo the configured interval (default 60 seconds). Causal feature
warmup from earlier partitions is allowed, future-label fitting is not.
Selection uses supported positive train payoff and validation ranking, never
test fitting. Every hypothesis/threshold/horizon/scenario/split and all/venue/
fixed-spread-regime group's statistics remain reported, including exclusions,
support/nonoverlap counts, no-trade and matched directional baselines.

These are independent unfunded directional taker markouts, not sequential funded
portfolio returns or paper fills. A short markout does not establish borrow or
margin access. Turnover/drawdown remain null with
`no_sequential_funded_portfolio_model`; no impact/shared inventory model is
invented. Outcomes are `insufficient_evidence`, `no_reliable_edge` or
`unvalidated_positive_markout`. Synthetic data always yields insufficient
evidence of edge, regardless of positive fixture results.

## Native replacement boundary

Only `BookReducer.apply/checkpoint/restore` and measured pure numeric kernels (`walk_book`, band-depth accumulation) may later cross PyO3. Batch exact scaled integers plus explicit scale/protocol IDs; return the same immutable Book/Reduction semantics. No networking, async scheduling, persistence, credential handling or frontend state crosses this boundary. Parity fixtures must match Python state hashes, invalidation reasons, checksum behavior and deterministic replay before selecting a native path. Until profiling demonstrates a failing budget, Python is the sole implementation.

## Synthetic fixture constructors

`synthetic_instrument(*, venue="synthetic", base="BTC", quote="USD", tick_size="0.01", lot_size="0.001", **overrides)` provides explicit synthetic metadata with available data access and unknown execution access. `SubscriptionKey.for_instrument(instrument, **options)` derives venue/API/ID without invented product mapping. `synthetic_frame(payload=b"{}", *, ordinal=1, epoch=0, mono_ns=1, **overrides)` computes and validates SHA256. `synthetic_envelope(instrument, payload, *, ordinal=1, epoch=0, mono_ns=None, **overrides)` uses demo mode, `synthetic-session`, `synthetic-stream`, `synthetic-clock`, and `synthetic-segment`; default monotonic time is the supplied ordinal. A matching fixture reducer therefore uses `segment_id="synthetic-segment"`. These helpers never participate in live discovery.

Offline consumer regression coverage is in `tests/test_workstation_core.py`: malformed precision/JSON, metadata version collisions, complete-message corruption, ordered repeated-price changes, initial/protocol synchronization, checksum/sequence hooks, depth scope, epoch/order fencing, heartbeat-versus-observation freshness, exact identity/cohorts, string-safe wire roundtrips and complete checkpoint restore. Check execution/results are recorded by the integration owner, not asserted here.
