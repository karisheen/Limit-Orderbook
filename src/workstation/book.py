"""Single-owner exact reducer. Protocol callbacks inspect candidates before commit."""

from dataclasses import dataclass, replace
from decimal import Decimal, localcontext
from hashlib import sha256
from typing import Callable

from .events import (
    SCHEMA_VERSION,
    BookUpdate,
    ChecksumInfo,
    Control,
    EventEnvelope,
    EventEnvelopeRef,
    Gap,
    SegmentId,
    SequenceInfo,
    SessionId,
    StreamId,
    SubscriptionKey,
)
from .instruments import Instrument, InstrumentId
from .precision import DECIMAL_CONTEXT, decimal, nonnegative_int, text
from .quality import Capabilities, Coverage, Quality


@dataclass(frozen=True)
class Level:
    price: Decimal
    quantity: Decimal

    def __post_init__(self):
        object.__setattr__(self, "price", decimal(self.price, positive=True))
        object.__setattr__(self, "quantity", decimal(self.quantity, positive=True))

    @classmethod
    def _from_validated(cls, price: Decimal, quantity: Decimal) -> "Level":
        """Reducer-only: bounded positive price and final quantity already checked."""
        level = object.__new__(cls)
        object.__setattr__(level, "price", price)
        object.__setattr__(level, "quantity", quantity)
        return level


@dataclass(frozen=True)
class Book:
    instrument_id: InstrumentId
    metadata_version: str
    key: SubscriptionKey
    segment_id: SegmentId
    connection_epoch: int
    revision: int
    asof: EventEnvelopeRef
    bids: tuple[Level, ...]
    asks: tuple[Level, ...]
    quality: Quality

    def __post_init__(self):
        object.__setattr__(self, "instrument_id", InstrumentId(self.instrument_id))
        object.__setattr__(self, "segment_id", SegmentId(self.segment_id))
        text(self.metadata_version)
        nonnegative_int(self.connection_epoch)
        nonnegative_int(self.revision)
        if (
            not isinstance(self.key, SubscriptionKey)
            or self.key.instrument_id != self.instrument_id
        ):
            raise ValueError("book subscription identity mismatch")
        if not isinstance(self.asof, EventEnvelopeRef) or not isinstance(
            self.quality, Quality
        ):
            raise ValueError("invalid book provenance/quality")
        for name, reverse in (("bids", True), ("asks", False)):
            levels = tuple(getattr(self, name))
            object.__setattr__(self, name, levels)
            if any(not isinstance(level, Level) for level in levels):
                raise ValueError("invalid level")
            for left, right in zip(levels, levels[1:]):
                if (
                    (left.price <= right.price)
                    if reverse
                    else (left.price >= right.price)
                ):
                    raise ValueError("book levels must be strictly ordered and unique")
        if self.quality.integrity == "valid" and (
            not self.bids or not self.asks or self.bids[0].price >= self.asks[0].price
        ):
            raise ValueError("valid book cannot be empty, locked, or crossed")

    @classmethod
    def _from_validated(
        cls,
        instrument_id: InstrumentId,
        metadata_version: str,
        key: SubscriptionKey,
        segment_id: SegmentId,
        connection_epoch: int,
        revision: int,
        asof: EventEnvelopeRef,
        bids: tuple[Level, ...],
        asks: tuple[Level, ...],
        quality: Quality,
    ) -> "Book":
        """Reducer-only publication of a fully checked, immutable candidate.

        Identity comes from validated reducer configuration; provenance from the
        accepted envelope. Sides contain only validated Levels, sorted by unique
        Decimal map keys, and apply checks nonempty/noncrossing before publication.
        No mutable reducer dictionary is retained by the published value.
        """
        book = object.__new__(cls)
        object.__setattr__(book, "instrument_id", instrument_id)
        object.__setattr__(book, "metadata_version", metadata_version)
        object.__setattr__(book, "key", key)
        object.__setattr__(book, "segment_id", segment_id)
        object.__setattr__(book, "connection_epoch", connection_epoch)
        object.__setattr__(book, "revision", revision)
        object.__setattr__(book, "asof", asof)
        object.__setattr__(book, "bids", bids)
        object.__setattr__(book, "asks", asks)
        object.__setattr__(book, "quality", quality)
        return book

    def _with_quality(self, quality: Quality) -> "Book":
        """Keep previously checked economics/provenance when updating quality."""
        return self._from_validated(
            self.instrument_id,
            self.metadata_version,
            self.key,
            self.segment_id,
            self.connection_epoch,
            self.revision,
            self.asof,
            self.bids,
            self.asks,
            quality,
        )


@dataclass(frozen=True)
class RecoveryRequest:
    reason: str
    action: str = "request_snapshot"

    def __post_init__(self):
        text(self.reason)
        if self.action not in (
            "request_snapshot",
            "resubscribe",
            "reconnect",
            "stop_unsupported",
        ):
            raise ValueError("invalid recovery action")


@dataclass(frozen=True)
class Reduction:
    book: Book | None
    events: tuple[EventEnvelope, ...] = ()
    recovery: RecoveryRequest | None = None
    changed: bool = False

    def __post_init__(self):
        object.__setattr__(self, "events", tuple(self.events))


@dataclass(frozen=True)
class ReducerState:
    instrument_id: InstrumentId
    metadata_version: str
    key: SubscriptionKey
    strategy_id: str
    truncate_depth: bool
    max_levels: int
    effective_depth: int | None
    epoch: int
    segment_id: SegmentId
    initialized: bool
    synchronized: bool
    revision: int
    bids: tuple[Level, ...]
    asks: tuple[Level, ...]
    sequence: SequenceInfo | None
    last_order: tuple[int, int] | None
    last_event: EventEnvelope | None
    quality: Quality
    book: Book | None
    buffered_events: tuple[EventEnvelope, ...]
    protocol_state: bytes

    def __post_init__(self):
        object.__setattr__(self, "instrument_id", InstrumentId(self.instrument_id))
        object.__setattr__(self, "segment_id", SegmentId(self.segment_id))
        text(self.metadata_version)
        text(self.strategy_id)
        for value in (self.epoch, self.revision, self.max_levels):
            nonnegative_int(value)
        if self.max_levels == 0:
            raise ValueError("invalid reducer capacity")
        if self.effective_depth is not None and not (
            1 <= nonnegative_int(self.effective_depth) <= 100_000
        ):
            raise ValueError("invalid effective depth")
        for value in (self.initialized, self.synchronized, self.truncate_depth):
            if type(value) is not bool:
                raise ValueError("invalid reducer state flag")
        for name, reverse in (("bids", True), ("asks", False)):
            levels = tuple(getattr(self, name))
            object.__setattr__(self, name, levels)
            if any(not isinstance(level, Level) for level in levels):
                raise ValueError("invalid checkpoint level")
            for left, right in zip(levels, levels[1:]):
                if (
                    (left.price <= right.price)
                    if reverse
                    else (left.price >= right.price)
                ):
                    raise ValueError("checkpoint levels must be ordered and unique")
        if self.last_order is not None:
            object.__setattr__(self, "last_order", tuple(self.last_order))
            if len(self.last_order) != 2:
                raise ValueError("invalid checkpoint order")
            for value in self.last_order:
                nonnegative_int(value)
        object.__setattr__(self, "buffered_events", tuple(self.buffered_events))
        if len(self.buffered_events) > 100_000 or any(
            not isinstance(event, EventEnvelope) for event in self.buffered_events
        ):
            raise ValueError("invalid protocol buffer")
        if (
            not isinstance(self.protocol_state, bytes)
            or len(self.protocol_state) > 4_194_304
        ):
            raise ValueError("invalid protocol state")
        if self.book is None and (
            self.bids or self.asks or self.initialized or self.synchronized
        ):
            raise ValueError("checkpoint state missing book")


@dataclass(frozen=True)
class BookCheckpoint:
    schema_version: int
    session_id: SessionId | None
    stream_id: StreamId | None
    segment_id: SegmentId
    ordinal: int | None
    event_index: int | None
    metadata_version: str
    adapter_revision: str
    config_hash: str
    book: Book | None
    reducer_state: bytes
    reducer_state_version: int
    state_hash: str

    def __post_init__(self):
        if self.schema_version != SCHEMA_VERSION or self.reducer_state_version != 1:
            raise ValueError("unsupported checkpoint schema")
        text(self.metadata_version)
        text(self.adapter_revision)
        text(self.config_hash)
        object.__setattr__(self, "segment_id", SegmentId(self.segment_id))
        if self.session_id is not None:
            object.__setattr__(self, "session_id", SessionId(self.session_id))
        if self.stream_id is not None:
            object.__setattr__(self, "stream_id", StreamId(self.stream_id))
        for value in (self.ordinal, self.event_index):
            if value is not None:
                nonnegative_int(value)
        if (
            not isinstance(self.reducer_state, bytes)
            or sha256(self.reducer_state).hexdigest() != self.state_hash
        ):
            raise ValueError("checkpoint state hash mismatch")


# Sequence callbacks are pure: accept, ignore (duplicate/obsolete), or gap.
SequenceValidator = Callable[[SequenceInfo | None, SequenceInfo | None, bool], str]
ChecksumCalculator = Callable[[tuple[Level, ...], tuple[Level, ...], ChecksumInfo], str]
PriceValidator = Callable[[Instrument, Decimal], None]
CandidateValidator = Callable[
    [tuple[Level, ...], tuple[Level, ...], tuple[EventEnvelope, ...]], None
]


def monotonic_sequence(
    previous: SequenceInfo | None, incoming: SequenceInfo | None, snapshot: bool
) -> str:
    """Numeric monotonic IDs only; makes no contiguous-ID claim."""
    if incoming is None or incoming.last is None:
        return "gap"
    if snapshot or previous is None:
        return "accept"
    if incoming.scope != previous.scope or previous.last is None:
        return "gap"
    try:
        return "accept" if int(incoming.last) > int(previous.last) else "ignore"
    except ValueError:
        return "gap"


def contiguous_sequence(
    previous: SequenceInfo | None, incoming: SequenceInfo | None, snapshot: bool
) -> str:
    """Use only where protocol guarantees numeric contiguous IDs in this exact scope."""
    decision = monotonic_sequence(previous, incoming, snapshot)
    if decision != "accept" or snapshot or previous is None:
        return decision
    try:
        if incoming is None or incoming.last is None or previous.last is None:
            return "gap"
        first = int(incoming.first or incoming.last)
        return (
            "accept" if first <= int(previous.last) + 1 <= int(incoming.last) else "gap"
        )
    except ValueError:
        return "gap"


class BookReducer:
    epoch: int

    def __init__(
        self,
        instrument: Instrument,
        key: SubscriptionKey,
        *,
        capabilities: Capabilities = Capabilities(),
        epoch: int = 0,
        segment_id: str = "initial",
        truncate_depth: bool = False,
        max_levels: int = 100_000,
        effective_depth: int | None = None,
        sequence_validator: SequenceValidator | None = None,
        checksum_calculator: ChecksumCalculator | None = None,
        price_validator: PriceValidator | None = None,
        candidate_validator: CandidateValidator | None = None,
        strategy_id: str = "generic-v1",
        adapter_revision: str = "core-v1",
        config_hash: str = "default",
    ):
        if (key.instrument_id, key.venue, key.api_family) != (
            instrument.instrument_id,
            instrument.venue,
            instrument.api_family,
        ):
            raise ValueError("reducer subscription identity mismatch")
        if nonnegative_int(max_levels) == 0:
            raise ValueError("max_levels must be positive")
        effective_depth = key.depth if effective_depth is None else effective_depth
        if effective_depth is not None and not (
            1 <= nonnegative_int(effective_depth) <= 100_000
        ):
            raise ValueError("invalid effective depth")
        if key.depth is not None and effective_depth != key.depth:
            raise ValueError("effective depth conflicts with subscription depth")
        for value in (strategy_id, adapter_revision, config_hash):
            text(value)
        self.instrument, self.key = instrument, key
        self.effective_depth = effective_depth
        self.capabilities = capabilities
        self.truncate_depth, self.max_levels = truncate_depth, max_levels
        self.sequence_validator, self.checksum_calculator = (
            sequence_validator,
            checksum_calculator,
        )
        self.price_validator, self.candidate_validator = (
            price_validator,
            candidate_validator,
        )
        self.strategy_id, self.adapter_revision, self.config_hash = (
            strategy_id,
            adapter_revision,
            config_hash,
        )
        self.reset(epoch, SegmentId(segment_id))

    def reset(self, epoch: int, segment_id: SegmentId) -> None:
        nonnegative_int(epoch)
        if hasattr(self, "epoch") and epoch < self.epoch:
            raise ValueError("cannot regress connection epoch")
        self.epoch, self.segment_id = epoch, SegmentId(segment_id)
        self._bids: dict[Decimal, Level] = {}
        self._asks: dict[Decimal, Level] = {}
        self._bid_levels: tuple[Level, ...] = ()
        self._ask_levels: tuple[Level, ...] = ()
        self.initialized = self.synchronized = False
        self.revision = 0
        self.sequence: SequenceInfo | None = None
        self._last_order: tuple[int, int] | None = None
        self._last_event: EventEnvelope | None = None
        self.book: Book | None = None
        self.quality = Quality(
            capabilities=self.capabilities,
            coverage=Coverage(
                subscribed_depth=self.effective_depth,
                source_aggregation=self.key.aggregation,
            ),
        )
        self.buffered_events: tuple[EventEnvelope, ...] = ()
        self.protocol_state: bytes = b""

    def _levels(self, bids, asks):
        # Cached tuples correspond exactly to the committed maps. Restore uses
        # the validated state tuples, preserving their lexical Decimal values.
        bid_levels = (
            self._bid_levels
            if bids is self._bids
            else tuple(
                sorted(bids.values(), key=lambda level: level.price, reverse=True)
            )
        )
        ask_levels = (
            self._ask_levels
            if asks is self._asks
            else tuple(sorted(asks.values(), key=lambda level: level.price))
        )
        if self.truncate_depth and self.effective_depth is not None:
            bid_levels, ask_levels = (
                bid_levels[: self.effective_depth],
                ask_levels[: self.effective_depth],
            )
        return bid_levels, ask_levels

    def _reject(self, events, reason, *, invalidate=False):
        if invalidate:
            self.initialized = self.synchronized = False
            self.quality = replace(
                self.quality,
                lifecycle="resynchronizing",
                integrity="invalid",
                continuous_since_ordinal=None,
                reason_codes=(reason,),
            )
            if self.book is not None:
                self.book = self.book._with_quality(self.quality)
        return Reduction(
            self.book if invalidate else None, events, RecoveryRequest(reason), False
        )

    def apply(
        self, events: tuple[EventEnvelope, ...], *, synchronized: bool = True
    ) -> Reduction:
        """Apply one whole source message atomically; synchronized=False stages bootstrap state.

        Hooks are pure and called before commit. Explicit depth truncation is venue opt-in.
        Wrappers may keep bounded initialization events in buffered_events and immutable
        protocol_state bytes; both are included in checkpoints.
        """
        events = tuple(events)
        if not events:
            return Reduction(self.book)
        if any(not isinstance(event, EventEnvelope) for event in events):
            raise ValueError("invalid reducer event")
        first, last = events[0], events[-1]
        for event in events:
            if (
                event.key != self.key
                or event.instrument_metadata_version != self.instrument.metadata_version
            ):
                return self._reject(events, "wrong_subscription")
            if event.connection_epoch != self.epoch:
                return self._reject(
                    events,
                    "obsolete_epoch"
                    if event.connection_epoch < self.epoch
                    else "unannounced_epoch",
                )
            if event.segment_id != self.segment_id:
                return self._reject(events, "wrong_segment")
            if (
                event.session_id,
                event.stream_id,
                event.connection_id,
                event.ingest_ordinal,
                event.clock_id,
                event.source_mode,
            ) != (
                first.session_id,
                first.stream_id,
                first.connection_id,
                first.ingest_ordinal,
                first.clock_id,
                first.source_mode,
            ):
                return self._reject(events, "mixed_transaction")
        if any(
            left.order_key >= right.order_key for left, right in zip(events, events[1:])
        ):
            return self._reject(events, "unordered_transaction")
        if self._last_order is not None and first.order_key <= self._last_order:
            return self._reject(events, "obsolete_ordinal")
        if self._last_event is not None:
            old = self._last_event
            if (
                first.session_id,
                first.stream_id,
                first.connection_id,
                first.clock_id,
            ) != (old.session_id, old.stream_id, old.connection_id, old.clock_id):
                return self._reject(
                    events, "unannounced_stream_change", invalidate=True
                )
            if first.receive_mono_ns < old.receive_mono_ns:
                return self._reject(events, "clock_regression", invalidate=True)
            if first.source_mode != old.source_mode:
                return self._reject(
                    events, "unannounced_source_change", invalidate=True
                )
        # Copy a side only on its first write. All candidate mutations remain
        # private until every event and protocol hook has accepted the message.
        bids, asks = self._bids, self._asks
        initialized, sequence = self.initialized, self.sequence
        has_book, accepted_book = False, False
        checksum = None
        snapshot_seen = False
        accepted_events = []
        previous_in_message = object()
        try:
            for event in events:
                payload = event.payload
                if isinstance(payload, Gap):
                    self._last_event, self._last_order = last, last.order_key
                    self.quality = replace(
                        self.quality,
                        capture_complete=self.quality.capture_complete
                        and not payload.capture_lost,
                    )
                    if payload.continuity_lost:
                        return self._reject(
                            events, payload.reason_code, invalidate=True
                        )
                    continue
                if isinstance(payload, Control) and payload.kind in (
                    "reset",
                    "disconnect",
                    "source_change",
                    "rejection",
                ):
                    self._last_event, self._last_order = last, last.order_key
                    result = self._reject(events, payload.reason_code, invalidate=True)
                    if payload.kind == "disconnect":
                        self.quality = replace(
                            self.quality,
                            lifecycle="disconnected",
                            transport_alive=False,
                        )
                        if self.book is not None:
                            self.book = self.book._with_quality(self.quality)
                        return replace(result, book=self.book)
                    return result
                snapshot = (
                    isinstance(payload, BookUpdate) and payload.kind == "snapshot"
                )
                if (
                    self.sequence_validator is not None
                    and event.sequence != previous_in_message
                ):
                    decision = self.sequence_validator(
                        sequence, event.sequence, snapshot
                    )
                    if decision == "ignore":
                        continue
                    if decision != "accept":
                        raise ValueError("sequence_gap")
                elif (
                    self.capabilities.sequence_scope is not None
                    and self.sequence_validator is None
                ):
                    raise ValueError("sequence_validator_missing")
                previous_in_message = event.sequence
                if event.sequence is not None:
                    sequence = event.sequence
                accepted_events.append(event)
                if not isinstance(payload, BookUpdate):
                    continue
                has_book = True
                if (
                    payload.subscribed_depth is not None
                    and payload.subscribed_depth != self.effective_depth
                ):
                    raise ValueError("subscription_depth_mismatch")
                if snapshot:
                    bids, asks = {}, {}
                    initialized = True
                    snapshot_seen = True
                elif not initialized:
                    raise ValueError("snapshot_required")
                seen = set()
                for change in payload.changes:
                    if self.price_validator is None:
                        self.instrument.validate_price(change.price)
                    else:
                        self.price_validator(self.instrument, change.price)
                    self.instrument.validate_quantity(
                        change.quantity,
                        incremental=change.quantity_semantics == "incremental",
                    )
                    if change.side == "bid":
                        if bids is self._bids:
                            bids = bids.copy()
                        side = bids
                    else:
                        if asks is self._asks:
                            asks = asks.copy()
                        side = asks
                    if snapshot:
                        identity = change.side, change.price
                        if identity in seen:
                            raise ValueError("duplicate_snapshot_price")
                        seen.add(identity)
                        if (
                            change.quantity_semantics != "absolute"
                            or change.quantity <= 0
                        ):
                            raise ValueError("invalid_snapshot_quantity")
                    quantity = change.quantity
                    if change.quantity_semantics == "incremental":
                        with localcontext(DECIMAL_CONTEXT):
                            quantity += (
                                side[change.price].quantity
                                if change.price in side
                                else Decimal(0)
                            )
                        decimal(quantity, nonnegative=True)
                    if quantity == 0:
                        side.pop(change.price, None)
                    else:
                        # Store the latest lexical price and quantity, not the old equal dict key.
                        # LevelChange bounds/validates price and absolute quantity;
                        # incremental sums are revalidated above. Instrument and
                        # protocol validators still run on every incoming change.
                        side[change.price] = Level._from_validated(
                            change.price, quantity
                        )
                if len(bids) > self.max_levels or len(asks) > self.max_levels:
                    raise ValueError("book_capacity_exceeded")
                accepted_book = True
                if event.checksum is not None:
                    checksum = event.checksum
            if has_book:
                bid_levels, ask_levels = self._levels(bids, asks)
                if not bid_levels or not ask_levels:
                    raise ValueError("empty_side")
                if bid_levels[0].price >= ask_levels[0].price:
                    raise ValueError("crossed_or_locked")
                if self.capabilities.checksum_scope is not None and checksum is None:
                    raise ValueError("checksum_missing")
                if checksum is not None:
                    if self.checksum_calculator is None:
                        raise ValueError("checksum_calculator_missing")
                    calculated = self.checksum_calculator(
                        bid_levels, ask_levels, checksum
                    )
                    if calculated != checksum.supplied:
                        raise ValueError("checksum_mismatch")
                    checked = replace(checksum, calculated=calculated, result="match")
                    accepted_events = [
                        replace(event, checksum=checked)
                        if event.checksum is not None
                        else event
                        for event in accepted_events
                    ]
                if self.candidate_validator is not None:
                    self.candidate_validator(
                        bid_levels, ask_levels, tuple(accepted_events)
                    )
        except (ValueError, ArithmeticError) as exc:
            self._last_event, self._last_order = last, last.order_key
            return self._reject(events, str(exc) or "invalid_book", invalidate=True)
        self._last_event, self._last_order = last, last.order_key
        self.sequence = sequence
        received = any(
            not isinstance(event.payload, (Gap, Control))
            or isinstance(event.payload, Control)
            and event.payload.kind != "clock_tick"
            for event in events
        )
        quality = replace(
            self.quality,
            transport_alive=True if received else self.quality.transport_alive,
            last_transport_mono_ns=last.receive_mono_ns
            if received
            else self.quality.last_transport_mono_ns,
            source_mode=last.source_mode,
            clock_id=last.clock_id,
        )
        if (
            synchronized
            and self.synchronized
            and self.book is not None
            and quality.integrity == "valid"
            and quality.source_mode == "ws_delta"
            and quality.capabilities.deltas
            and quality.capabilities.exact_event_flow
            and quality.continuous_since_ordinal is not None
            and any(
                isinstance(event.payload, Control) and event.payload.kind == "heartbeat"
                for event in accepted_events
            )
        ):
            quality = replace(
                quality, last_healthy_observation_mono_ns=last.receive_mono_ns
            )
        changed = False
        if accepted_book:
            # Reuse the exact candidate checked by checksum/candidate hooks.
            changed = bid_levels != self._bid_levels or ask_levels != self._ask_levels
            # Only venue-opt-in truncation needs to rebuild a candidate map.
            self._bids = (
                bids
                if len(bids) == len(bid_levels)
                else {level.price: level for level in bid_levels}
            )
            self._asks = (
                asks
                if len(asks) == len(ask_levels)
                else {level.price: level for level in ask_levels}
            )
            self._bid_levels, self._ask_levels = bid_levels, ask_levels
            self.initialized, self.synchronized = initialized, synchronized
            self.revision += 1
            coverage = replace(
                quality.coverage,
                retained_bid_levels=len(bid_levels),
                retained_ask_levels=len(ask_levels),
                worst_bid=bid_levels[-1].price,
                worst_ask=ask_levels[-1].price,
            )
            quality = replace(
                quality,
                lifecycle=(
                    "degraded" if last.source_mode == "rest_snapshot" else "live"
                )
                if synchronized
                else "synchronizing",
                integrity="valid" if synchronized else "unknown",
                coverage=coverage,
                last_book_observation_mono_ns=last.receive_mono_ns,
                last_book_change_mono_ns=last.receive_mono_ns
                if changed
                else quality.last_book_change_mono_ns,
                last_healthy_observation_mono_ns=last.receive_mono_ns
                if synchronized
                else quality.last_healthy_observation_mono_ns,
                continuous_since_ordinal=(
                    first.ingest_ordinal
                    if snapshot_seen or quality.continuous_since_ordinal is None
                    else quality.continuous_since_ordinal
                )
                if synchronized
                else None,
                reason_codes=() if synchronized else ("awaiting_protocol_sync",),
            )
            self.book = Book._from_validated(
                self.instrument.instrument_id,
                self.instrument.metadata_version,
                self.key,
                self.segment_id,
                self.epoch,
                self.revision,
                last.ref(),
                bid_levels,
                ask_levels,
                quality,
            )
        elif self.book is not None:
            self.book = self.book._with_quality(quality)
        self.quality = quality
        return Reduction(
            self.book if self.synchronized else None,
            tuple(accepted_events),
            changed=changed,
        )

    def invalidate(self, gap: EventEnvelope) -> Reduction:
        if not isinstance(gap.payload, Gap):
            raise ValueError("invalidation requires a Gap payload")
        return self.apply((gap,))

    def _state(self) -> ReducerState:
        """Capture immutable native state without a persistence round trip."""
        bids, asks = self._levels(self._bids, self._asks)
        return ReducerState(
            self.instrument.instrument_id,
            self.instrument.metadata_version,
            self.key,
            self.strategy_id,
            self.truncate_depth,
            self.max_levels,
            self.effective_depth,
            self.epoch,
            self.segment_id,
            self.initialized,
            self.synchronized,
            self.revision,
            bids,
            asks,
            self.sequence,
            self._last_order,
            self._last_event,
            self.quality,
            self.book,
            tuple(self.buffered_events),
            self.protocol_state,
        )

    def checkpoint(self) -> BookCheckpoint:
        from .wire import dumps

        state = self._state()
        encoded = dumps(state, canonical=False)
        event = self._last_event
        return BookCheckpoint(
            SCHEMA_VERSION,
            event.session_id if event else None,
            event.stream_id if event else None,
            self.segment_id,
            event.ingest_ordinal if event else None,
            event.event_index if event else None,
            self.instrument.metadata_version,
            self.adapter_revision,
            self.config_hash,
            self.book,
            encoded,
            1,
            sha256(encoded).hexdigest(),
        )

    def restore(self, checkpoint: BookCheckpoint) -> None:
        from .wire import loads

        if (
            checkpoint.metadata_version != self.instrument.metadata_version
            or checkpoint.adapter_revision != self.adapter_revision
            or checkpoint.config_hash != self.config_hash
        ):
            raise ValueError("incompatible checkpoint configuration")
        if sha256(checkpoint.reducer_state).hexdigest() != checkpoint.state_hash:
            raise ValueError("checkpoint state hash mismatch")
        state = loads(checkpoint.reducer_state, ReducerState)
        if state.book != checkpoint.book or state.segment_id != checkpoint.segment_id:
            raise ValueError("inconsistent checkpoint book")
        event = state.last_event
        if (
            checkpoint.session_id,
            checkpoint.stream_id,
            checkpoint.ordinal,
            checkpoint.event_index,
        ) != (
            event.session_id if event else None,
            event.stream_id if event else None,
            event.ingest_ordinal if event else None,
            event.event_index if event else None,
        ):
            raise ValueError("inconsistent checkpoint position")
        self._restore_state(state)

    def _adopt_candidate(self, candidate: "BookReducer") -> None:
        """Commit a private reducer using the same native restore validation.

        Bootstrap candidates have already applied every native event validator.
        Avoid encoding and decoding their immutable state merely to transfer it;
        _restore_state still checks consistency and constructs independent maps.
        Adapter overrides retain their protocol validation and atomic rollback.
        """
        if (
            candidate.instrument.metadata_version != self.instrument.metadata_version
            or candidate.adapter_revision != self.adapter_revision
            or candidate.config_hash != self.config_hash
        ):
            raise ValueError("incompatible checkpoint configuration")
        self._restore_state(candidate._state())

    def _restore_state(self, state: ReducerState) -> None:
        if (
            state.instrument_id,
            state.metadata_version,
            state.key,
            state.strategy_id,
            state.truncate_depth,
            state.max_levels,
            state.effective_depth,
        ) != (
            self.instrument.instrument_id,
            self.instrument.metadata_version,
            self.key,
            self.strategy_id,
            self.truncate_depth,
            self.max_levels,
            self.effective_depth,
        ):
            raise ValueError("incompatible reducer state")
        if state.quality.capabilities != self.capabilities:
            raise ValueError("incompatible checkpoint capabilities")
        if state.synchronized and (
            not state.initialized
            or state.book is None
            or state.quality.integrity != "valid"
        ):
            raise ValueError("invalid checkpoint synchronization")
        if len(state.bids) > self.max_levels or len(state.asks) > self.max_levels:
            raise ValueError("checkpoint capacity exceeded")
        if state.book is not None and (
            state.bids != state.book.bids
            or state.asks != state.book.asks
            or state.book.quality != state.quality
        ):
            raise ValueError("inconsistent checkpoint levels")
        for level in state.bids + state.asks:
            if self.price_validator is not None:
                self.price_validator(self.instrument, level.price)
            else:
                self.instrument.validate_price(level.price)
            self.instrument.validate_quantity(level.quantity)
        event = state.last_event
        if event is not None and (
            state.last_order != event.order_key
            or event.key != self.key
            or event.connection_epoch != state.epoch
            or event.segment_id != state.segment_id
        ):
            raise ValueError("invalid checkpoint event")
        # All validation precedes mutation, including restore failures.
        self.epoch, self.segment_id = state.epoch, state.segment_id
        self.initialized, self.synchronized, self.revision = (
            state.initialized,
            state.synchronized,
            state.revision,
        )
        self._bids = {level.price: level for level in state.bids}
        self._asks = {level.price: level for level in state.asks}
        self._bid_levels, self._ask_levels = state.bids, state.asks
        self.sequence, self._last_order, self._last_event = (
            state.sequence,
            state.last_order,
            state.last_event,
        )
        self.quality, self.book = state.quality, state.book
        self.buffered_events, self.protocol_state = (
            state.buffered_events,
            state.protocol_state,
        )
