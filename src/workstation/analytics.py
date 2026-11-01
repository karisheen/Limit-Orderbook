"""Bounded, receive-time public-market analytics shared by live and replay."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, replace
from decimal import Decimal, localcontext
from hashlib import sha256
from typing import TYPE_CHECKING

from .book import Book
from .events import (
    BookUpdate,
    Control,
    EventEnvelope,
    EventEnvelopeRef,
    Gap,
    StreamId,
    SubscriptionKey,
    Trade,
)
from .instruments import Instrument, InstrumentId
from .precision import (
    DECIMAL_CONTEXT,
    DERIVED_DECIMAL_POLICY,
    decimal,
    nonnegative_int,
    text,
)
from .quality import (
    ClockReading,
    Eligibility,
    EligibilityPolicy,
    Quality,
    TradeEvidence,
    evaluate,
    evaluate_trades,
)
from .wire import dumps, loads

if TYPE_CHECKING:
    from .recording import RecordedBatch, StreamSpec
    from .replay import ReplayClock

ZERO = Decimal(0)


@dataclass(frozen=True)
class AnalyticsConfig:
    window_ns: int = 60_000_000_000
    retention_ns: int = 300_000_000_000
    max_trades: int = 20_000
    max_book_samples: int = 20_000
    max_display_events: int = 20_000
    max_display_levels: int = 100
    max_references: int = 32
    tape_limit: int = 100
    depth_bps: tuple[Decimal, ...] = (Decimal(5), Decimal(10), Decimal(25), Decimal(50))
    persistence_ns: int = 5_000_000_000
    evidence_min_base: Decimal = Decimal("0.00000001")
    policy: EligibilityPolicy = EligibilityPolicy()

    def __post_init__(self):
        for name in (
            "window_ns",
            "retention_ns",
            "max_trades",
            "max_book_samples",
            "max_display_events",
            "max_display_levels",
            "max_references",
            "tape_limit",
            "persistence_ns",
        ):
            if nonnegative_int(getattr(self, name)) == 0:
                raise ValueError("analytics bounds must be positive")
        if self.retention_ns < self.window_ns or self.tape_limit > self.max_trades:
            raise ValueError("insufficient analytics retention")
        bands = tuple(decimal(v, positive=True) for v in self.depth_bps)
        if (
            not bands
            or len(bands) > 32
            or len(set(bands)) != len(bands)
            or max(bands) >= 10000
        ):
            raise ValueError("invalid depth bands")
        object.__setattr__(self, "depth_bps", bands)
        object.__setattr__(
            self, "evidence_min_base", decimal(self.evidence_min_base, positive=True)
        )


@dataclass(frozen=True)
class MetricWindow:
    start_mono_ns: int
    end_mono_ns: int
    clock_id: str
    definition: str = "receive_time_(start,end]"

    def __post_init__(self):
        nonnegative_int(self.start_mono_ns)
        nonnegative_int(self.end_mono_ns)
        if self.start_mono_ns > self.end_mono_ns:
            raise ValueError("inverted metric window")
        text(self.clock_id)
        text(self.definition)


@dataclass(frozen=True)
class MetricResult:
    name: str
    value: Decimal | None
    unit: str
    instrument_id: str
    venue: str
    asof: EventEnvelopeRef | None
    window: MetricWindow
    quality: Quality | TradeEvidence | None
    eligibility: Eligibility
    formula_version: str
    config_hash: str
    references: tuple[EventEnvelopeRef, ...] = ()
    trade_evidence: TradeEvidence | None = None
    trade_eligibility: Eligibility | None = None
    trade_references: tuple[EventEnvelopeRef, ...] = ()

    def __post_init__(self):
        if self.value is not None:
            object.__setattr__(
                self, "value", decimal(self.value, policy=DERIVED_DECIMAL_POLICY)
            )
        if not self.eligibility.eligible and self.value is not None:
            raise ValueError("ineligible metric cannot carry a value")
        object.__setattr__(self, "references", tuple(self.references))
        object.__setattr__(self, "trade_references", tuple(self.trade_references))

    @property
    def reasons(self) -> tuple[str, ...]:
        return self.eligibility.reasons


@dataclass(frozen=True)
class TapePrint:
    trade: Trade
    asof: EventEnvelopeRef
    stream_id: str
    base_quantity: Decimal | None
    quote_notional: Decimal | None

    def __post_init__(self):
        for name in ("base_quantity", "quote_notional"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(
                    self,
                    name,
                    decimal(value, nonnegative=True, policy=DERIVED_DECIMAL_POLICY),
                )


@dataclass(frozen=True)
class ProfileLevel:
    price: Decimal
    buy_base: Decimal
    sell_base: Decimal
    unknown_base: Decimal
    quote_notional: Decimal

    def __post_init__(self):
        for name in (
            "price",
            "buy_base",
            "sell_base",
            "unknown_base",
            "quote_notional",
        ):
            object.__setattr__(
                self,
                name,
                decimal(
                    getattr(self, name), nonnegative=True, policy=DERIVED_DECIMAL_POLICY
                ),
            )


@dataclass(frozen=True)
class DisplayEvidence:
    side: str
    price: Decimal
    kind: str
    quantity: Decimal
    start_mono_ns: int
    end_mono_ns: int
    references: tuple[EventEnvelopeRef, ...]

    def __post_init__(self):
        for name in ("price", "quantity"):
            object.__setattr__(
                self,
                name,
                decimal(
                    getattr(self, name), nonnegative=True, policy=DERIVED_DECIMAL_POLICY
                ),
            )
        nonnegative_int(self.start_mono_ns)
        nonnegative_int(self.end_mono_ns)
        if self.start_mono_ns > self.end_mono_ns:
            raise ValueError("inverted displayed evidence interval")
        if self.side not in ("bid", "ask"):
            raise ValueError("invalid displayed side")
        if self.kind not in (
            "displayed_persistence",
            "displayed_depletion",
            "displayed_replenishment",
        ):
            raise ValueError("invalid displayed evidence kind")
        object.__setattr__(self, "references", tuple(self.references))


@dataclass(frozen=True)
class AnalyticsSnapshot:
    instrument_id: str
    key: SubscriptionKey | None
    asof: EventEnvelopeRef | None
    metrics: tuple[MetricResult, ...]
    tape: tuple[TapePrint, ...]
    profile: tuple[ProfileLevel, ...]
    display_evidence: tuple[DisplayEvidence, ...]
    reasons: tuple[str, ...]

    def __post_init__(self):
        for name in ("metrics", "tape", "profile", "display_evidence", "reasons"):
            object.__setattr__(self, name, tuple(getattr(self, name)))


@dataclass(frozen=True)
class BookSample:
    asof: EventEnvelopeRef
    value: Decimal
    kind: str

    def __post_init__(self):
        object.__setattr__(
            self, "value", decimal(self.value, policy=DERIVED_DECIMAL_POLICY)
        )


@dataclass(frozen=True)
class BookAnalyticsState:
    key: SubscriptionKey
    book: Book | None
    anchor: int | None
    samples: tuple[BookSample, ...]
    display: tuple[DisplayEvidence, ...]
    standing: tuple[DisplayEvidence, ...]
    overflow_until: int
    display_overflow_until: int

    def __post_init__(self):
        if self.anchor is not None:
            nonnegative_int(self.anchor)
        nonnegative_int(self.overflow_until)
        nonnegative_int(self.display_overflow_until)
        for name in ("samples", "display", "standing"):
            object.__setattr__(self, name, tuple(getattr(self, name)))


@dataclass(frozen=True)
class TradeAnalyticsState:
    stream_id: str
    evidence: TradeEvidence | None
    asof: EventEnvelopeRef | None
    tape: tuple[TapePrint, ...]
    overflow_until: int
    source_identity: tuple[str, str, str, int] | None

    def __post_init__(self):
        nonnegative_int(self.overflow_until)
        object.__setattr__(self, "tape", tuple(self.tape))
        if self.source_identity is not None:
            object.__setattr__(self, "source_identity", tuple(self.source_identity))


@dataclass(frozen=True)
class AnalyticsCheckpoint:
    version: str
    config_hash: str
    streams_hash: str
    delivery_index: int
    books: tuple[BookAnalyticsState, ...]
    trades: tuple[TradeAnalyticsState, ...]

    def __post_init__(self):
        nonnegative_int(self.delivery_index)
        object.__setattr__(self, "books", tuple(self.books))
        object.__setattr__(self, "trades", tuple(self.trades))
        if len({s.key for s in self.books}) != len(self.books) or len(
            {s.stream_id for s in self.trades}
        ) != len(self.trades):
            raise ValueError("duplicate analytics checkpoint streams")


class _StandingEvidence:
    """Owner-only evolving evidence; public snapshots/checkpoints remain immutable."""

    __slots__ = (
        "side",
        "price",
        "quantity",
        "start_mono_ns",
        "end_mono_ns",
        "references",
    )

    def __init__(
        self,
        side: str,
        price: Decimal,
        quantity: Decimal,
        start_mono_ns: int,
        end_mono_ns: int,
        references: tuple[EventEnvelopeRef, ...],
    ):
        self.side, self.price, self.quantity = side, price, quantity
        self.start_mono_ns, self.end_mono_ns = start_mono_ns, end_mono_ns
        self.references = references

    def freeze(self) -> DisplayEvidence:
        return DisplayEvidence(
            self.side,
            self.price,
            "displayed_persistence",
            self.quantity,
            self.start_mono_ns,
            self.end_mono_ns,
            self.references,
        )


def _contains_price(book: Book, side: str, price: Decimal) -> bool:
    """Search validated sorted native levels without copying full-depth keys."""
    levels = book.bids if side == "bid" else book.asks
    low, high = 0, len(levels)
    while low < high:
        middle = (low + high) // 2
        candidate = levels[middle].price
        if candidate == price:
            return True
        if (candidate > price) == (side == "bid"):
            low = middle + 1
        else:
            high = middle
    return False


class _BookWindow:
    def __init__(self, key: SubscriptionKey) -> None:
        self.key = key
        self.book: Book | None = None
        self.anchor: int | None = None
        self.samples: deque[BookSample] = deque()
        self.display: deque[DisplayEvidence] = deque()
        self.standing: dict[tuple[str, Decimal], _StandingEvidence] = {}
        self.overflow_until = self.display_overflow_until = 0


class _TradeWindow:
    def __init__(self, stream_id: str) -> None:
        self.stream_id = stream_id
        self.evidence: TradeEvidence | None = None
        self.asof: EventEnvelopeRef | None = None
        self.tape: deque[TapePrint] = deque()
        self.seen: set[str] = set()
        self.overflow_until = 0
        self.source_identity: tuple[str, str, str, int] | None = None


def _valuation(
    instrument: Instrument, price: Decimal, quantity: Decimal
) -> tuple[Decimal | None, Decimal | None]:
    # Contract metadata is explicit; reference_multiplier is never valuation.
    if instrument.quantity_unit == "base" and not instrument.inverse:
        base = quantity * instrument.contract_multiplier
    elif (
        instrument.contract_specification_source
        and instrument.contract_value_currency == instrument.base.asset_id
        and not instrument.inverse
    ):
        base = quantity * instrument.contract_multiplier
    elif (
        instrument.contract_specification_source
        and instrument.contract_value_currency == instrument.quote.asset_id
        and instrument.inverse
    ):
        base = quantity * instrument.contract_multiplier / price
    else:
        return None, None
    return base, base * price


def _ofi(previous: Book, current: Book) -> Decimal:
    b0, a0, b, a = previous.bids[0], previous.asks[0], current.bids[0], current.asks[0]
    return (
        (b.quantity if b.price >= b0.price else ZERO)
        - (b0.quantity if b.price <= b0.price else ZERO)
        - (a.quantity if a.price <= a0.price else ZERO)
        + (a0.quantity if a.price >= a0.price else ZERO)
    )


class AnalyticsEngine:
    observer_id = "analytics"
    version = "1"

    def __init__(
        self,
        streams: tuple[StreamSpec, ...],
        config: AnalyticsConfig = AnalyticsConfig(),
    ):
        self.streams, self.config = tuple(streams), config
        if len({s.stream_id for s in self.streams}) != len(self.streams):
            raise ValueError("duplicate analytics stream")
        self._specs = {s.stream_id: s for s in self.streams}
        self._instruments = {
            s.instrument.instrument_id: s.instrument for s in self.streams
        }
        if any(
            self._instruments[s.instrument.instrument_id] != s.instrument
            for s in self.streams
        ):
            raise ValueError("ambiguous instrument metadata")
        self.config_hash = sha256(
            dumps(config, decimal_policy=DERIVED_DECIMAL_POLICY)
        ).hexdigest()
        self.streams_hash = sha256(
            dumps(
                tuple(
                    (
                        s.stream_id,
                        s.instrument,
                        s.key,
                        s.adapter_revision,
                        s.config_hash,
                        s.adapter_config,
                    )
                    for s in self.streams
                )
            )
        ).hexdigest()
        self.reset()

    def reset(self) -> None:
        self._books: dict[SubscriptionKey, _BookWindow] = {
            s.key: _BookWindow(s.key) for s in self.streams if s.key.channel == "book"
        }
        self._trades: dict[str, _TradeWindow] = {
            s.stream_id: _TradeWindow(s.stream_id)
            for s in self.streams
            if s.key.channel in ("trade", "trades")
        }
        self.delivery_index = 0
        self._admitted_batch: RecordedBatch | None = None
        self._admitted: deque[TapePrint] = deque(maxlen=self.config.max_trades)
        self._admitted_complete = True

    def admitted_tape(self, batch: RecordedBatch) -> tuple[TapePrint, ...]:
        """Already normalized/deduplicated prints admitted by this exact batch.

        This ephemeral owner-only view never reruns normalization and cannot
        return a previous delivery's prints after seek, restore, or replacement.
        The bound is the existing per-stream tape bound; consumers must inspect
        admitted_tape_complete before claiming full batch coverage.
        """
        if batch is not self._admitted_batch:
            return ()
        return tuple(self._admitted)

    def admitted_tape_complete(self, batch: RecordedBatch) -> bool:
        return batch is self._admitted_batch and self._admitted_complete

    def initialize_books(self, books: tuple[Book, ...]) -> None:
        """Install a live subscription baseline without inventing observed history.

        Call synchronously after subscribing to processed deliveries and before
        consuming their buffered batches. Unconfigured keys are ignored, as in
        on_batch; configured books retain their exact source quality/provenance.
        """
        if (
            self.delivery_index != 0
            or any(state.book is not None for state in self._books.values())
            or any(state.evidence is not None for state in self._trades.values())
        ):
            raise ValueError("book initialization requires a pristine observer")
        selected = tuple(book for book in books if book.key in self._books)
        if len({book.key for book in selected}) != len(selected):
            raise ValueError("duplicate initial book key")
        for book in selected:
            instrument = self._instruments[book.instrument_id]
            if book.metadata_version != instrument.metadata_version:
                raise ValueError("initial book metadata mismatch")
        for book in selected:
            self._books[book.key].book = book

    def _trim(self, state: _BookWindow | _TradeWindow, now: int) -> None:
        cutoff = now - self.config.retention_ns
        if isinstance(state, _TradeWindow):
            while state.tape and state.tape[0].asof.receive_mono_ns <= cutoff:
                state.seen.discard(state.tape.popleft().trade.dedup_key)
            while len(state.tape) > self.config.max_trades:
                removed = state.tape.popleft()
                state.seen.discard(removed.trade.dedup_key)
                if not removed.trade.is_subscription_history:
                    state.overflow_until = max(
                        state.overflow_until,
                        removed.asof.receive_mono_ns + self.config.window_ns,
                    )
        else:

            def stamp(item: BookSample | DisplayEvidence) -> int:
                return (
                    item.asof.receive_mono_ns
                    if isinstance(item, BookSample)
                    else item.end_mono_ns
                )

            queues: tuple[
                tuple[deque[BookSample] | deque[DisplayEvidence], int, str], ...
            ] = (
                (state.samples, self.config.max_book_samples, "overflow_until"),
                (
                    state.display,
                    self.config.max_display_events,
                    "display_overflow_until",
                ),
            )
            for queue, maximum, attribute in queues:
                while queue and stamp(queue[0]) <= cutoff:
                    queue.popleft()
                while len(queue) > maximum:
                    removed_sample = queue.popleft()
                    setattr(
                        state,
                        attribute,
                        max(
                            getattr(state, attribute),
                            stamp(removed_sample) + self.config.window_ns,
                        ),
                    )

    def on_batch(
        self, batch: RecordedBatch, books: tuple[Book, ...], clock: ReplayClock
    ) -> None:
        if batch.delivery_index <= self.delivery_index:
            raise ValueError("analytics delivery must advance; reset before seek")
        self._admitted_batch = batch
        self._admitted.clear()
        self._admitted_complete = True
        with localcontext(DECIMAL_CONTEXT):
            present_book_keys = {book.key for book in books}
            for event in batch.events:
                spec = self._specs.get(event.stream_id)
                if spec is None:
                    continue
                if (
                    event.key != spec.key
                    or event.instrument_metadata_version
                    != spec.instrument.metadata_version
                ):
                    raise ValueError("analytics event identity mismatch")
                if isinstance(event.payload, Gap) or (
                    isinstance(event.payload, Control)
                    and event.payload.kind
                    in ("reset", "source_change", "rejection", "disconnect")
                ):
                    affected_keys = {event.key}
                    if isinstance(event.payload, Gap):
                        affected_keys.update(
                            self._specs[target].key
                            for target in event.payload.affected_streams
                            if target in self._specs
                        )
                    for affected_key in affected_keys:
                        if (
                            affected_key in self._books
                            and affected_key not in present_book_keys
                        ):
                            self._books[affected_key] = _BookWindow(affected_key)
                if event.stream_id in self._trades:
                    self._trade_event(event, spec)
                if isinstance(event.payload, Gap):
                    for target in event.payload.affected_streams:
                        affected = self._trades.get(target)
                        if affected is not None and target != event.stream_id:
                            if affected.evidence is not None:
                                affected.evidence = replace(
                                    affected.evidence,
                                    healthy=False,
                                    established_mono_ns=None,
                                    reason_codes=("trade_continuity_reset",),
                                )
                            affected.tape.clear()
                            affected.seen.clear()
                            affected.asof = event.ref()
            # Process once per complete feed-message delivery, never once per level.
            for book in books:
                state = self._books.get(book.key)
                relevant = tuple(e for e in batch.events if e.key == book.key)
                if (
                    state is not None
                    and not relevant
                    and state.book is not None
                    and state.book.quality != book.quality
                ):
                    relevant = batch.events
                if state is not None and relevant:
                    self._book_event(state, book, relevant)
            windows: tuple[_BookWindow | _TradeWindow, ...] = (
                *self._books.values(),
                *self._trades.values(),
            )
            for window_state in windows:
                reading = clock.reading
                state_clock = (
                    window_state.book.asof.clock_id
                    if isinstance(window_state, _BookWindow) and window_state.book
                    else window_state.evidence.clock_id
                    if isinstance(window_state, _TradeWindow) and window_state.evidence
                    else None
                )
                if reading is not None and reading.clock_id == state_clock:
                    self._trim(window_state, reading.mono_ns)
        self.delivery_index = batch.delivery_index

    def _trade_event(self, event: EventEnvelope, spec: StreamSpec) -> None:
        state = self._trades[event.stream_id]
        identity = (
            event.session_id,
            event.segment_id,
            event.clock_id,
            event.connection_epoch,
        )
        if state.source_identity != identity:
            state = self._trades[event.stream_id] = _TradeWindow(event.stream_id)
            state.source_identity = identity
        previous = state.evidence
        if previous is None:
            previous = TradeEvidence(
                str(spec.instrument.instrument_id),
                spec.instrument.metadata_version,
                str(event.stream_id),
                event.clock_id,
                event.connection_epoch,
            )
        if previous.last_validated_mono_ns is not None:
            age = event.receive_mono_ns - previous.last_validated_mono_ns
            if age < 0:
                state.evidence = replace(
                    previous,
                    healthy=False,
                    established_mono_ns=None,
                    reason_codes=("trade_clock_regression",),
                )
                state.tape.clear()
                state.seen.clear()
                state.asof = event.ref()
                return
            if age > self.config.policy.max_age_ns:
                previous = replace(
                    previous,
                    healthy=False,
                    established_mono_ns=None,
                    reason_codes=("trade_continuity_expired",),
                )
                state.tape.clear()
                state.seen.clear()
                state.overflow_until = 0
        payload = event.payload
        if (
            isinstance(payload, Gap)
            or isinstance(payload, Control)
            and payload.kind in ("reset", "disconnect", "source_change", "rejection")
        ):
            state.evidence = replace(
                previous,
                healthy=False,
                established_mono_ns=None,
                reason_codes=("trade_continuity_reset",),
            )
            state.tape.clear()
            state.seen.clear()
            state.asof = event.ref()
            return
        if isinstance(payload, Trade):
            if payload.quantity_unit != spec.instrument.quantity_unit:
                state.evidence = replace(
                    previous,
                    healthy=False,
                    established_mono_ns=None,
                    reason_codes=("trade_quantity_unit_mismatch",),
                )
                state.tape.clear()
                state.seen.clear()
                state.overflow_until = 0
                state.asof = event.ref()
                return
            if not payload.is_subscription_history:
                previous = replace(
                    previous,
                    healthy=True,
                    established_mono_ns=previous.established_mono_ns
                    if previous.established_mono_ns is not None
                    else event.receive_mono_ns,
                    last_validated_mono_ns=event.receive_mono_ns,
                    reason_codes=(),
                )
                state.asof = event.ref()
            if payload.dedup_key not in state.seen:
                base, quote = _valuation(
                    spec.instrument, payload.price, payload.quantity
                )
                item = TapePrint(
                    payload, event.ref(), str(event.stream_id), base, quote
                )
                state.tape.append(item)
                if len(self._admitted) == self.config.max_trades:
                    self._admitted_complete = False
                self._admitted.append(item)
                state.seen.add(payload.dedup_key)
        elif (
            isinstance(payload, Control)
            and payload.kind == "heartbeat"
            and payload.reason_code == "trade_channel_healthy"
            and previous.healthy
        ):
            previous = replace(previous, last_validated_mono_ns=event.receive_mono_ns)
            state.asof = event.ref()
        state.evidence = previous
        self._trim(state, event.receive_mono_ns)

    def _book_event(
        self, state: _BookWindow, book: Book, events: tuple[EventEnvelope, ...]
    ) -> None:
        old = state.book
        now = ClockReading(book.asof.clock_id, max(e.receive_mono_ns for e in events))
        eligible = evaluate(
            book,
            self._instruments[book.instrument_id],
            "display",
            now,
            self.config.policy,
        ).eligible
        reset = (
            old is None
            or state.anchor is None
            or not eligible
            or old.segment_id != book.segment_id
            or old.connection_epoch != book.connection_epoch
            or old.asof.clock_id != book.asof.clock_id
            or old.quality.continuous_since_ordinal
            != book.quality.continuous_since_ordinal
            or any(
                isinstance(e.payload, Gap)
                or isinstance(e.payload, Control)
                and e.payload.kind in ("reset", "disconnect", "source_change")
                for e in events
            )
        )
        changed = any(isinstance(e.payload, BookUpdate) for e in events) and (
            old is None or old.revision != book.revision
        )
        if reset:
            state.samples.clear()
            state.display.clear()
            state.standing.clear()
            state.anchor = book.asof.receive_mono_ns if eligible and changed else None
            state.overflow_until = state.display_overflow_until = 0
        elif changed and old is not None:
            event_flow = evaluate(
                book,
                self._instruments[book.instrument_id],
                "event_ofi",
                now,
                self.config.policy,
            ).eligible and all(
                not isinstance(e.payload, BookUpdate) or e.payload.kind == "delta"
                for e in events
            )
            state.samples.append(
                BookSample(
                    book.asof,
                    _ofi(old, book),
                    "event_ofi" if event_flow else "snapshot_best_quote_flow",
                )
            )
        if eligible and changed:
            levels = {
                (side, level.price): level
                for side, values in (("bid", book.bids), ("ask", book.asks))
                for level in values[: self.config.max_display_levels]
            }
            for identity, prior in tuple(state.standing.items()):
                current = levels.get(identity)
                # Only infer removal inside retained observed coverage, not a truncation edge.
                visible = (
                    current is not None
                    or not _contains_price(book, identity[0], identity[1])
                    and (
                        book.quality.coverage.full_depth
                        or identity[0] == "bid"
                        and book.bids[-1].price <= identity[1]
                        or identity[0] == "ask"
                        and book.asks[-1].price >= identity[1]
                    )
                )
                if visible:
                    quantity = current.quantity if current else ZERO
                    difference = quantity - prior.quantity
                    if difference:
                        state.display.append(
                            DisplayEvidence(
                                identity[0],
                                identity[1],
                                "displayed_replenishment"
                                if difference > 0
                                else "displayed_depletion",
                                abs(difference),
                                prior.end_mono_ns,
                                book.asof.receive_mono_ns,
                                (prior.references[-1], book.asof),
                            )
                        )
                if current is None:
                    del state.standing[identity]
            for identity, level in levels.items():
                standing_prior = state.standing.get(identity)
                if standing_prior is not None:
                    standing_prior.quantity = level.quantity
                    standing_prior.end_mono_ns = book.asof.receive_mono_ns
                    standing_prior.references = (
                        standing_prior.references[0],
                        book.asof,
                    )
                else:
                    state.standing[identity] = _StandingEvidence(
                        identity[0],
                        identity[1],
                        level.quantity,
                        book.asof.receive_mono_ns,
                        book.asof.receive_mono_ns,
                        (book.asof,),
                    )
        state.book = book
        self._trim(state, now.mono_ns)

    def _trade_source(
        self, instrument_id: str, now: ClockReading
    ) -> tuple[_TradeWindow | None, Eligibility]:
        instrument = self._instruments[InstrumentId(instrument_id)]
        candidates = [
            state
            for sid, state in self._trades.items()
            if self._specs[StreamId(sid)].instrument.instrument_id == instrument_id
        ]
        state = candidates[0] if len(candidates) == 1 else None
        eligibility = evaluate_trades(
            state.evidence if state else None, instrument, now, self.config.policy
        )
        if len(candidates) > 1:
            eligibility = replace(
                eligibility,
                eligible=False,
                reasons=(*eligibility.reasons, "ambiguous_trade_stream"),
            )
        return state, eligibility

    def trade_evidence(
        self,
        instrument_id: str,
        now: ClockReading,
        *,
        key: SubscriptionKey | None = None,
    ) -> tuple[TradeEvidence | None, Eligibility]:
        """Current public-channel quality, independent of book and rolling windows.

        A selected book key cannot disambiguate multiple matching trade streams.
        This read-only gate does not establish evidence or compute metric totals.
        """
        if instrument_id not in self._instruments:
            raise ValueError("unknown analytics instrument")
        if key is not None and key.instrument_id != instrument_id:
            raise ValueError("analytics key/instrument mismatch")
        state, eligibility = self._trade_source(instrument_id, now)
        return state.evidence if state else None, eligibility

    def snapshot(
        self,
        instrument_id: str,
        now: ClockReading,
        *,
        key: SubscriptionKey | None = None,
    ) -> AnalyticsSnapshot:
        if instrument_id not in self._instruments:
            raise ValueError("unknown analytics instrument")
        with localcontext(DECIMAL_CONTEXT):
            return self._snapshot(instrument_id, now, key)

    def _snapshot(
        self, instrument_id: str, now: ClockReading, key: SubscriptionKey | None
    ) -> AnalyticsSnapshot:
        instrument = self._instruments[InstrumentId(instrument_id)]
        candidates = [
            s
            for k, s in self._books.items()
            if k.instrument_id == instrument_id and (key is None or key == k)
        ]
        if key is not None and key.instrument_id != instrument_id:
            raise ValueError("analytics key/instrument mismatch")
        state = candidates[0] if len(candidates) == 1 else None
        book = state.book if state else None
        book_reasons = (
            ("ambiguous_book_key",)
            if len(candidates) > 1
            else ("no_book",)
            if book is None
            else evaluate(book, instrument, "display", now, self.config.policy).reasons
        )
        start = max(0, now.mono_ns - self.config.window_ns)
        window = MetricWindow(start, now.mono_ns, now.clock_id)
        instant = MetricWindow(
            now.mono_ns, now.mono_ns, now.clock_id, "latest_observation"
        )
        metrics: list[MetricResult] = []

        def metric(
            name: str,
            value: Decimal | None,
            unit: str,
            reasons: Iterable[str] = (),
            *,
            trade_state: _TradeWindow | None = None,
            trade_metric: bool = False,
            rolling: bool = False,
            observed: bool = False,
            refs: Iterable[EventEnvelopeRef] = (),
            formula: str = "analytics-v1",
            supporting_trade_evidence: TradeEvidence | None = None,
            supporting_trade_eligibility: Eligibility | None = None,
            trade_refs: Iterable[EventEnvelopeRef] = (),
        ) -> None:
            quality = (
                trade_state.evidence
                if trade_state
                else None
                if trade_metric
                else book.quality
                if book
                else None
            )
            asof = (
                trade_state.asof
                if trade_state
                else None
                if trade_metric
                else book.asof
                if book
                else None
            )
            reason_codes = tuple(dict.fromkeys(reasons))
            result_window = (
                replace(
                    window,
                    definition="observed_receive_time_(start,end];not_complete_history",
                )
                if observed
                else window
                if rolling
                else instant
            )
            references = tuple(refs)[-self.config.max_references :] or (
                (asof,) if asof else ()
            )
            metrics.append(
                MetricResult(
                    name,
                    None if reason_codes else value,
                    unit,
                    str(instrument_id),
                    instrument.venue,
                    asof,
                    result_window,
                    quality,
                    Eligibility(
                        not reason_codes,
                        reason_codes,
                        now.mono_ns,
                        self.config.policy.version,
                    ),
                    formula,
                    self.config_hash,
                    references,
                    supporting_trade_evidence,
                    supporting_trade_eligibility,
                    tuple(trade_refs)[-self.config.max_references :],
                )
            )

        price_unit = (
            str(instrument.quote.asset_id) + "/" + str(instrument.base.asset_id)
        )
        native_unit = instrument.quantity_unit + ":" + str(instrument_id)
        if book is not None and not book_reasons:
            bid, ask = book.bids[0], book.asks[0]
            mid = (bid.price + ask.price) / 2
            total = bid.quantity + ask.quantity
            values = {
                "best_bid": bid.price,
                "best_ask": ask.price,
                "mid": mid,
                "spread_native": ask.price - bid.price,
                "spread_bps": (ask.price - bid.price) / mid * 10000,
                "weighted_mid": (ask.price * bid.quantity + bid.price * ask.quantity)
                / total
                if total
                else None,
                "signed_imbalance": (bid.quantity - ask.quantity) / total
                if total
                else None,
                "bid_share": bid.quantity / total if total else None,
            }
        else:
            values = dict.fromkeys(
                (
                    "best_bid",
                    "best_ask",
                    "mid",
                    "spread_native",
                    "spread_bps",
                    "weighted_mid",
                    "signed_imbalance",
                    "bid_share",
                )
            )
        for name, value in values.items():
            unit = (
                "bps"
                if name == "spread_bps"
                else "ratio"
                if name in ("signed_imbalance", "bid_share")
                else price_unit
            )
            metric(
                name,
                value,
                unit,
                book_reasons or (("zero_denominator",) if value is None else ()),
            )
        for band in self.config.depth_bps:
            for side in ("bid", "ask"):
                reasons = list(book_reasons)
                base = quote = ZERO
                if book is not None and not reasons:
                    bound = mid * (
                        1 - band / 10000 if side == "bid" else 1 + band / 10000
                    )
                    levels = book.bids if side == "bid" else book.asks
                    covered = book.quality.coverage.full_depth or (
                        levels[-1].price <= bound
                        if side == "bid"
                        else levels[-1].price >= bound
                    )
                    if not covered:
                        reasons.append("incomplete_depth_band")
                    for level in levels:
                        if (
                            level.price >= bound
                            if side == "bid"
                            else level.price <= bound
                        ):
                            b, q = _valuation(instrument, level.price, level.quantity)
                            if b is None or q is None:
                                reasons.append("unknown_contract_valuation")
                                break
                            base += b
                            quote += q
                for suffix, value, unit in (
                    ("base", base, str(instrument.base.asset_id)),
                    ("quote", quote, str(instrument.quote.asset_id)),
                ):
                    metric(
                        f"depth_{side}_{format(band, 'f')}bps_{suffix}",
                        value,
                        unit,
                        reasons,
                    )
        history_reasons = list(book_reasons)
        if book is not None and not book.quality.capture_complete:
            history_reasons.append("capture_incomplete")
        if state is not None:
            if (
                state.anchor is None
                or state.anchor > start
                or now.mono_ns < self.config.window_ns
            ):
                history_reasons.append("book_window_warming")
            if now.mono_ns < state.overflow_until:
                history_reasons.append("book_history_overflow")
        for kind in ("event_ofi", "snapshot_best_quote_flow"):
            reasons = list(history_reasons)
            samples = (
                tuple(
                    s
                    for s in state.samples
                    if start < s.asof.receive_mono_ns <= now.mono_ns
                )
                if state
                else ()
            )
            if kind == "event_ofi" and book:
                reasons.extend(
                    evaluate(
                        book, instrument, "event_ofi", now, self.config.policy
                    ).reasons
                )
                if any(s.kind != kind for s in samples):
                    reasons.append("snapshot_transition_in_window")
            elif (
                kind == "snapshot_best_quote_flow"
                and book
                and book.quality.capabilities.exact_event_flow
            ):
                reasons.append("event_flow_source_use_event_ofi")
            metric(
                kind,
                sum((s.value for s in samples if s.kind == kind), ZERO),
                native_unit,
                reasons,
                rolling=True,
                refs=(s.asof for s in samples),
                formula="cont-best-quote-indicators-v1"
                if kind == "event_ofi"
                else "snapshot-best-quote-difference-v1",
            )
        trade_state, trade_eligibility = self._trade_source(instrument_id, now)
        trade_reasons = list(trade_eligibility.reasons)
        tape = (
            tuple(
                t
                for t in trade_state.tape
                if t.asof.clock_id == now.clock_id
                and t.asof.receive_mono_ns <= now.mono_ns
            )
            if trade_state
            else ()
        )
        flow = tuple(
            t
            for t in tape
            if start < t.asof.receive_mono_ns and not t.trade.is_subscription_history
        )
        if trade_state:
            anchor = (
                trade_state.evidence.established_mono_ns
                if trade_state.evidence
                else None
            )
            if anchor is None or anchor > start or now.mono_ns < self.config.window_ns:
                trade_reasons.append("trade_window_warming")
            if now.mono_ns < trade_state.overflow_until:
                trade_reasons.append("trade_history_overflow")
        if any(t.base_quantity is None for t in flow):
            trade_reasons.append("unknown_contract_valuation")
        if not flow and (
            trade_state is None
            or trade_state.evidence is None
            or trade_state.evidence.last_validated_mono_ns != now.mono_ns
        ):
            trade_reasons.append("quiet_trade_interval_unproven")
        observed_reasons = [r for r in trade_reasons if r != "trade_window_warming"]
        if not flow:
            observed_reasons.append("no_observed_trades")
        totals = {
            side: sum(
                (
                    t.quote_notional
                    for t in flow
                    if t.trade.taker_side == side and t.quote_notional is not None
                ),
                ZERO,
            )
            for side in ("buy", "sell", "unknown")
        }
        for side in totals:
            metric(
                f"trade_{side}_notional",
                totals[side],
                str(instrument.quote.asset_id),
                trade_reasons,
                trade_state=trade_state,
                trade_metric=True,
                rolling=True,
                refs=(t.asof for t in flow),
            )
            metric(
                f"observed_trade_{side}_notional",
                totals[side],
                str(instrument.quote.asset_id),
                observed_reasons,
                trade_state=trade_state,
                trade_metric=True,
                rolling=True,
                observed=True,
                refs=(t.asof for t in flow),
            )
        cvd = sum(
            (
                t.base_quantity * (1 if t.trade.taker_side == "buy" else -1)
                for t in flow
                if t.trade.taker_side != "unknown" and t.base_quantity is not None
            ),
            ZERO,
        )
        metric(
            "cvd_base",
            cvd,
            str(instrument.base.asset_id),
            trade_reasons,
            trade_state=trade_state,
            trade_metric=True,
            rolling=True,
            refs=(t.asof for t in flow),
            formula="receive-window-known-aggressor-buy-minus-sell-v1",
        )
        metric(
            "cvd_quote",
            totals["buy"] - totals["sell"],
            str(instrument.quote.asset_id),
            trade_reasons,
            trade_state=trade_state,
            trade_metric=True,
            rolling=True,
            refs=(t.asof for t in flow),
        )
        metric(
            "observed_cvd_base",
            cvd,
            str(instrument.base.asset_id),
            observed_reasons,
            trade_state=trade_state,
            trade_metric=True,
            rolling=True,
            observed=True,
            refs=(t.asof for t in flow),
        )
        profile: dict[Decimal, list[Decimal]] = {}
        if not observed_reasons:
            for trade in flow:
                assert trade.base_quantity is not None
                assert trade.quote_notional is not None
                row = profile.setdefault(trade.trade.price, [ZERO, ZERO, ZERO, ZERO])
                row[("buy", "sell", "unknown").index(trade.trade.taker_side)] += (
                    trade.base_quantity
                )
                row[3] += trade.quote_notional
        metric(
            "executed_profile_base",
            sum((t.base_quantity for t in flow if t.base_quantity is not None), ZERO),
            str(instrument.base.asset_id),
            observed_reasons,
            trade_state=trade_state,
            trade_metric=True,
            rolling=True,
            observed=True,
            refs=(t.asof for t in flow),
        )
        display = (
            tuple(d for d in state.display if start < d.end_mono_ns <= now.mono_ns)
            if state
            else ()
        )
        standing = (
            tuple(
                d.freeze()
                for d in state.standing.values()
                if d.end_mono_ns <= now.mono_ns
                and d.end_mono_ns - d.start_mono_ns >= self.config.persistence_ns
            )
            if state
            else ()
        )
        display_reasons = history_reasons + (
            ["display_history_overflow"]
            if state and now.mono_ns < state.display_overflow_until
            else []
        )
        for kind in (
            "displayed_depletion",
            "displayed_replenishment",
            "displayed_persistence",
        ):
            rows = (
                standing
                if kind == "displayed_persistence"
                else tuple(d for d in display if d.kind == kind)
            )
            metric(
                kind,
                sum((d.quantity for d in rows), ZERO),
                native_unit,
                display_reasons,
                rolling=True,
                refs=(r for d in rows for r in d.references),
                formula="observed-displayed-level-changes-v1",
            )
        heuristics_reasons = list(dict.fromkeys(display_reasons + trade_reasons))
        if instrument.quantity_unit != "base" or instrument.contract_multiplier != 1:
            heuristics_reasons.append("heuristic_requires_native_base_quantity")
        # Match public execution evidence only at the observed price and interval.
        absorption = pulling = ZERO
        absorption_refs: deque[EventEnvelopeRef] = deque(
            maxlen=self.config.max_references
        )
        pulling_refs: deque[EventEnvelopeRef] = deque(maxlen=self.config.max_references)
        absorption_book_refs: deque[EventEnvelopeRef] = deque(
            maxlen=self.config.max_references
        )
        pulling_book_refs: deque[EventEnvelopeRef] = deque(
            maxlen=self.config.max_references
        )
        flow_by_price_side: dict[tuple[Decimal, str], list[TapePrint]] = {}
        for trade in flow:
            if trade.base_quantity is not None:
                flow_by_price_side.setdefault(
                    (trade.trade.price, trade.trade.taker_side), []
                ).append(trade)
        for item in display:
            matched = tuple(
                t
                for t in flow_by_price_side.get(
                    (item.price, "sell" if item.side == "bid" else "buy"), ()
                )
                if item.start_mono_ns < t.asof.receive_mono_ns <= item.end_mono_ns
            )
            executed = sum(
                (t.base_quantity for t in matched if t.base_quantity is not None), ZERO
            )
            if (
                item.kind == "displayed_replenishment"
                and executed >= self.config.evidence_min_base
            ):
                absorption += min(item.quantity, executed)
                absorption_refs.extend(t.asof for t in matched)
                absorption_book_refs.extend(item.references)
            if item.kind == "displayed_depletion":
                pulling += max(ZERO, item.quantity - executed)
                pulling_refs.extend(t.asof for t in matched)
                pulling_book_refs.extend(item.references)
        trade_gate_reasons = tuple(dict.fromkeys(trade_reasons))
        trade_gate = Eligibility(
            not trade_gate_reasons,
            trade_gate_reasons,
            now.mono_ns,
            self.config.policy.version,
        )
        for name, value, matched_refs, book_refs in (
            (
                "absorption_evidence_heuristic",
                absorption,
                absorption_refs,
                absorption_book_refs,
            ),
            ("pulling_evidence_heuristic", pulling, pulling_refs, pulling_book_refs),
        ):
            supporting_refs = tuple(matched_refs) or (
                (trade_state.asof,) if trade_state and trade_state.asof else ()
            )
            metric(
                name,
                value,
                str(instrument.base.asset_id),
                heuristics_reasons,
                rolling=True,
                refs=book_refs,
                formula="public-price-interval-coincidence-not-intent-v1",
                supporting_trade_evidence=trade_state.evidence if trade_state else None,
                supporting_trade_eligibility=trade_gate,
                trade_refs=supporting_refs,
            )
        snapshot_reasons = tuple(dict.fromkeys(book_reasons + tuple(trade_reasons)))
        return AnalyticsSnapshot(
            str(instrument_id),
            state.key if state else key,
            book.asof if book else trade_state.asof if trade_state else None,
            tuple(metrics),
            tape[-self.config.tape_limit :],
            tuple(ProfileLevel(price, *row) for price, row in sorted(profile.items())),
            (display + standing)[-self.config.max_display_events :],
            snapshot_reasons,
        )

    def checkpoint(self) -> bytes:
        books = tuple(
            BookAnalyticsState(
                s.key,
                s.book,
                s.anchor,
                tuple(s.samples),
                tuple(s.display),
                tuple(d.freeze() for d in s.standing.values()),
                s.overflow_until,
                s.display_overflow_until,
            )
            for s in self._books.values()
        )
        trades = tuple(
            TradeAnalyticsState(
                s.stream_id,
                s.evidence,
                s.asof,
                tuple(s.tape),
                s.overflow_until,
                s.source_identity,
            )
            for s in self._trades.values()
        )
        return dumps(
            AnalyticsCheckpoint(
                self.version,
                self.config_hash,
                self.streams_hash,
                self.delivery_index,
                books,
                trades,
            ),
            decimal_policy=DERIVED_DECIMAL_POLICY,
        )

    def restore(self, state: bytes) -> None:
        checkpoint: AnalyticsCheckpoint = loads(
            state, AnalyticsCheckpoint, decimal_policy=DERIVED_DECIMAL_POLICY
        )
        if (checkpoint.version, checkpoint.config_hash, checkpoint.streams_hash) != (
            self.version,
            self.config_hash,
            self.streams_hash,
        ):
            raise ValueError("incompatible analytics checkpoint")
        if {s.key for s in checkpoint.books} != set(self._books) or {
            s.stream_id for s in checkpoint.trades
        } != set(self._trades):
            raise ValueError("analytics checkpoint stream mismatch")
        for s in checkpoint.books:
            if (
                len(s.samples) > self.config.max_book_samples
                or len(s.display) > self.config.max_display_events
                or len(s.standing) > 2 * self.config.max_display_levels
            ):
                raise ValueError("analytics checkpoint book bounds")
        if any(len(s.tape) > self.config.max_trades for s in checkpoint.trades):
            raise ValueError("analytics checkpoint trade bounds")
        self.reset()
        self.delivery_index = checkpoint.delivery_index
        for saved in checkpoint.books:
            current = self._books[saved.key]
            current.book, current.anchor = saved.book, saved.anchor
            current.samples, current.display = (
                deque(saved.samples),
                deque(saved.display),
            )
            current.standing = {
                (d.side, d.price): _StandingEvidence(
                    d.side,
                    d.price,
                    d.quantity,
                    d.start_mono_ns,
                    d.end_mono_ns,
                    d.references,
                )
                for d in saved.standing
            }
            current.overflow_until, current.display_overflow_until = (
                saved.overflow_until,
                saved.display_overflow_until,
            )
        for saved_trade in checkpoint.trades:
            current_trade = self._trades[saved_trade.stream_id]
            current_trade.evidence, current_trade.asof = (
                saved_trade.evidence,
                saved_trade.asof,
            )
            current_trade.tape = deque(saved_trade.tape)
            current_trade.seen = {t.trade.dedup_key for t in saved_trade.tape}
            current_trade.overflow_until, current_trade.source_identity = (
                saved_trade.overflow_until,
                saved_trade.source_identity,
            )

    @property
    def state_hash(self) -> str:
        return sha256(self.checkpoint()).hexdigest()
