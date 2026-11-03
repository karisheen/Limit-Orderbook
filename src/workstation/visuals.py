"""Completed-delivery visualization evidence, never browser-frame history.

The observer owns analytics ordering: close old boundaries, apply the complete
message, then admit its shared normalized prints. Native Books are immutable and
shared; display bounds never clip the collector or reducer.
"""

from bisect import bisect_left, bisect_right
from collections import deque
from copy import copy
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, localcontext
from hashlib import sha256
import json

from .analytics import AnalyticsEngine, MetricResult, TapePrint, _valuation
from .book import Book
from .costs import (
    FeeSchedule,
    _amounts,
    _native_for_base,
    _validate_price,
    _valuation_reason,
)
from .events import Control, EventEnvelopeRef, Gap, SubscriptionKey
from .precision import (
    DECIMAL_CONTEXT,
    DERIVED_DECIMAL_POLICY,
    canonical_decimal,
    decimal,
)
from .quality import ClockReading, EligibilityPolicy, evaluate
from .wire import dumps, loads

SECOND = 1_000_000_000
ZERO = Decimal(0)
MAX_COLUMNS = 1800
MAX_TRADES = 4096
MAX_BYTES = 32 * 1024 * 1024
MAX_CELLS = 65536
MAX_QUERY_LEVELS = 500_000
SAMPLING = "last_completed_observation_at_boundary_v1"
WALL_OFFSET_CHANGE_THRESHOLD_NS = 1_000_000
MAX_TIME_AXIS_SEGMENTS = 1801
TIME_AXIS_PROJECTION = "piecewise_receive_pair_v1"


def exact(value):
    """HTTP scalars remain exact; no persisted wire-schema changes."""
    if isinstance(value, Decimal):
        return canonical_decimal(value, policy=DERIVED_DECIMAL_POLICY)
    if is_dataclass(value):
        return {
            field.name: exact(getattr(value, field.name)) for field in fields(value)
        }
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, dict):
        return {str(key): exact(item) for key, item in value.items()}
    if isinstance(value, frozenset):
        return [exact(item) for item in sorted(value)]
    if isinstance(value, (tuple, list)):
        return [exact(item) for item in value]
    raise TypeError(type(value).__name__)


def digest(value):
    return sha256(
        json.dumps(exact(value), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:32]


def floor(value, step):
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def step_options(instrument):
    # Unknown/variable native ticks are not fabricated. These are explicitly
    # display quanta, not claims that orders at those increments are valid.
    tick = instrument.tick_size
    if tick is None:
        return (
            Decimal("0.01"),
            Decimal("0.1"),
            Decimal("1"),
            Decimal("10"),
            Decimal("100"),
        )
    result = []
    with localcontext(DECIMAL_CONTEXT):
        for multiple in (1, 5, 10, 50, 100, 500, 1000):
            value = tick * multiple
            try:
                result.append(
                    decimal(
                        canonical_decimal(value, policy=DERIVED_DECIMAL_POLICY),
                        positive=True,
                    )
                )
            except ValueError:
                # Do not advertise a quantum the bounded HTTP decimal contract
                # cannot accept. Native precision remains unchanged.
                continue
    return tuple(result)


def display_step(instrument, requested, previous=None):
    options = step_options(instrument)
    step = (
        decimal(requested, positive=True)
        if requested is not None
        else previous or options[0]
    )
    if step not in options:
        # Auto-fit can need a coarser quantum than the menu's initial choices.
        # Accept the same 1/5 decade progression for explicit chart/ladder use.
        multiple = (step / options[0]).normalize()
        parts = multiple.as_tuple()
        if multiple < 1 or parts.exponent < 0 or parts.digits not in ((1,), (5,)):
            raise ValueError("unsupported display price quantum")
    return step


def fitted_step(instrument, book, trades, anchor, rows):
    """Fit actual near-market levels and prints, without clipping native depth."""
    count = max(1, rows // 4)
    lower = book.bids[min(count, len(book.bids)) - 1].price
    upper = book.asks[min(count, len(book.asks)) - 1].price
    for observed in trades:
        lower = min(lower, observed.print.trade.price)
        upper = max(upper, observed.print.trade.price)
    # Leave two cells of breathing room, including floor alignment at the top.
    required = max(
        (anchor - lower) / (rows // 2 - 2),
        (upper - anchor) / (rows - rows // 2 - 2),
    )
    minimum = step_options(instrument)[0]
    step = minimum
    while step < required:
        step *= 10
    if step / 2 >= max(required, minimum):
        step /= 2
    return step


@dataclass(frozen=True)
class Sample:
    time_ns: int
    clock_id: str
    segment_id: str
    book: Book | None
    reasons: tuple[str, ...]
    cvd: MetricResult | None
    ofi: MetricResult | None


@dataclass(frozen=True)
class ObservedTrade:
    time_ns: int
    segment_id: str
    print: TapePrint


@dataclass(frozen=True)
class ReceiveClockSegment:
    id: str
    start_ns: int
    reference: EventEnvelopeRef
    cause: str
    offset_change_ns: int | None


@dataclass(frozen=True)
class UnappliedReceivePair:
    admission_ns: int
    reference: EventEnvelopeRef
    offset_change_ns: int
    reason: str = "nonadvancing_receive_pair"


ClockSegmentState = tuple[str, int, EventEnvelopeRef, str, int | None]
UnappliedPairState = tuple[int, EventEnvelopeRef, int, str]
TimeAxisState = tuple[
    str,
    int,
    str,
    int,
    str | None,
    tuple[str, str] | None,
    int | None,
    int | None,
    bool,
    bool,
    tuple[ClockSegmentState, ...],
    UnappliedPairState | None,
]


SampleState = tuple[
    int,
    str,
    str,
    Book | None,
    tuple[str, ...],
    MetricResult | None,
    MetricResult | None,
]
TradeState = tuple[int, str, TapePrint]
FlowState = tuple[int, tuple[str, ...], int | None]
CheckpointState = tuple[
    str,
    SubscriptionKey | None,
    bytes,
    tuple[SampleState, ...],
    tuple[TradeState, ...],
    Book | None,
    ClockReading | None,
    int | None,
    str,
    tuple[str, ...] | None,
    int,
    int | None,
    bool,
    int | None,
    Decimal | None,
    Decimal | None,
    int,
    tuple[int, ...],
    tuple[FlowState, ...],
    TimeAxisState,
]


class VisualHistory:
    """One bounded selected-instrument observer, owned by the session worker."""

    observer_id = "visualization_with_analytics"
    version = "3"

    def __init__(
        self,
        engine: AnalyticsEngine,
        key: SubscriptionKey | None = None,
        *,
        source_identity: str | None = None,
    ):
        self.engine, self.key = engine, key
        self._configured_source_identity = source_identity
        self.instrument = next(
            (s.instrument for s in engine.streams if s.key == key), None
        )
        self.trade_streams = frozenset(
            str(s.stream_id)
            for s in engine.streams
            if key is not None
            and s.key.instrument_id == key.instrument_id
            and s.key.channel in ("trade", "trades")
        )
        self.selected_streams = frozenset(
            s.stream_id
            for s in engine.streams
            if key is not None
            and s.key.instrument_id == key.instrument_id
            and s.key.channel in ("book", "trade", "trades")
        )
        self.reset()

    def reset(self):
        self.engine.reset()
        self.samples: deque[Sample] = deque()
        self._sample_sizes: deque[int] = deque()
        self._sample_metrics: dict[int, bytes] = {}
        self.trades: deque[ObservedTrade] = deque()
        self._trade_sizes: deque[int] = deque()
        self.discontinuities: deque[int] = deque()
        self.flow: deque[FlowState] = deque()
        self.book = None
        self.clock: ClockReading | None = None
        self.next_boundary = None
        self.segment = "unobserved"
        self.identity = None
        self.segment_number = 0
        self.first_ns = None
        self.truncated = False
        self.trade_floor = None
        self.anchor: Decimal | None = None
        # Fitted scale is evidence-derived and must not survive a replay rewind.
        # Clients retaining an explicit quantum send it on the next query.
        self.price_step = None
        self.bucket_ns = getattr(self, "bucket_ns", SECOND)
        self.accounted_bytes = 256 * 1024  # bounded query fingerprints/ownership
        self._books = {}
        self._last_query = None
        self.source_identity = self._configured_source_identity or ""
        self.clock_segments: deque[ReceiveClockSegment] = deque()
        self._clock_segment_sizes: deque[int] = deque()
        self.clock_scope = None
        self._clock_epoch = 0
        self._clock_epoch_key = None
        self._clock_floor = None
        self._clock_frontier = None
        self._clock_truncated = False
        self._clock_changed = False
        self.last_unapplied_pair = None
        self._unapplied_pair_size = 0
        # Publication fence is private ownership, not part of deterministic IDs.
        self._query_epoch = object()

    def select(self, key):
        self.key = key
        self.instrument = next(
            s.instrument for s in self.engine.streams if s.key == key
        )
        self.trade_streams = frozenset(
            str(s.stream_id)
            for s in self.engine.streams
            if s.key.instrument_id == key.instrument_id
            and s.key.channel in ("trade", "trades")
        )
        self.selected_streams = frozenset(
            s.stream_id
            for s in self.engine.streams
            if s.key.instrument_id == key.instrument_id
            and s.key.channel in ("book", "trade", "trades")
        )
        self.anchor = None
        self.price_step = None
        self.bucket_ns = SECOND

    def initialize_books(self, books):
        self.engine.initialize_books(books)
        self.book = next((book for book in books if book.key == self.key), None)
        # No baseline history: first on_batch establishes subscription time.

    @staticmethod
    def book_bytes(book):
        return 32768 + 512 * (len(book.bids) + len(book.asks)) if book else 0

    def _retain(self, sample):
        size = 4096 + 4 * len(
            dumps(
                (sample.reasons, sample.cvd, sample.ofi),
                decimal_policy=DERIVED_DECIMAL_POLICY,
            )
        )
        # Closed metric provenance is immutable. Encode it once, charge the
        # retained bytes, and decode fresh HTTP containers for each query.
        metric_bytes = json.dumps(
            exact((sample.cvd, sample.ofi)), separators=(",", ":")
        ).encode()
        size += 256 + len(metric_bytes)
        if sample.book and self.book_bytes(sample.book) + size + 256 * 1024 > MAX_BYTES:
            sample = replace(
                sample,
                book=None,
                reasons=sample.reasons + ("native_depth_exceeds_history_byte_limit",),
            )
            size += 512
        self.samples.append(sample)
        self._sample_metrics[id(sample)] = metric_bytes
        self._sample_sizes.append(size)
        self.accounted_bytes += size
        if sample.book:
            identity = id(sample.book)
            if identity not in self._books:
                self._books[identity] = [sample.book, 0]
                self.accounted_bytes += self.book_bytes(sample.book)
            self._books[identity][1] += 1
        self._trim()

    def _drop_sample(self):
        sample = self.samples.popleft()
        self.accounted_bytes -= self._sample_sizes.popleft()
        del self._sample_metrics[id(sample)]
        if sample.book:
            identity = id(sample.book)
            retained = self._books[identity]
            retained[1] -= 1
            if not retained[1]:
                self.accounted_bytes -= self.book_bytes(sample.book)
                del self._books[identity]
        self.truncated = True

    @staticmethod
    def _clock_record_bytes(record):
        return 1024 + 4 * len(json.dumps(exact(record), separators=(",", ":")))

    def _drop_clock_segment(self):
        self.clock_segments.popleft()
        self.accounted_bytes -= self._clock_segment_sizes.popleft()
        self._clock_truncated = True
        assert self._clock_floor is not None
        self._clock_floor = (
            max(self._clock_floor, self.clock_segments[0].start_ns)
            if self.clock_segments
            else None
        )

    def _set_unapplied_pair(self, pair):
        self.accounted_bytes -= self._unapplied_pair_size
        self.last_unapplied_pair = pair
        self._unapplied_pair_size = self._clock_record_bytes(pair) if pair else 0
        self.accounted_bytes += self._unapplied_pair_size

    def _observe_receive_clock(self, reference, reading):
        epoch_key = (str(reference.session_id), reading.clock_id)
        if epoch_key != self._clock_epoch_key:
            changed = self._clock_epoch_key is not None
            while self.clock_segments:
                self._drop_clock_segment()
            self._set_unapplied_pair(None)
            self.source_identity = self._configured_source_identity or str(
                reference.session_id
            )
            self._clock_epoch += 1
            self._clock_epoch_key = epoch_key
            self.clock_scope = digest(
                (
                    TIME_AXIS_PROJECTION,
                    self.source_identity,
                    reading.clock_id,
                    self._clock_epoch,
                    reference,
                )
            )
            self._clock_floor = None
            self._clock_frontier = None
            self._clock_truncated = False
            self._clock_changed = changed
            self._query_epoch = object()
        frontier = reading.mono_ns
        previous = self._clock_frontier
        self._clock_frontier = frontier
        change = None
        if self.clock_segments:
            active = self.clock_segments[-1].reference
            change = (
                reference.receive_wall_ns
                - reference.receive_mono_ns
                - (active.receive_wall_ns - active.receive_mono_ns)
            )
            if abs(change) < WALL_OFFSET_CHANGE_THRESHOLD_NS:
                return
            if frontier <= previous or reference.receive_mono_ns != frontier:
                self._set_unapplied_pair(
                    UnappliedReceivePair(frontier, reference, change)
                )
                return
        segment = ReceiveClockSegment(
            digest((self.clock_scope, frontier, reference)),
            frontier,
            reference,
            "initial_pair" if not self.clock_segments else "wall_offset_change",
            change,
        )
        self.clock_segments.append(segment)
        size = self._clock_record_bytes(segment)
        self._clock_segment_sizes.append(size)
        self.accounted_bytes += size
        if self._clock_floor is None:
            self._clock_floor = frontier

    def _trim_receive_clock(self):
        if self.clock_segments:
            assert self._clock_floor is not None and self._clock_frontier is not None
            floor_ns = max(
                self._clock_floor, self._clock_frontier - MAX_COLUMNS * SECOND
            )
            if floor_ns > self._clock_floor:
                self._clock_truncated = True
                self._clock_floor = floor_ns
            # Preserve the original predecessor, not a viewport-relative anchor.
            while (
                len(self.clock_segments) > 1
                and self.clock_segments[1].start_ns <= floor_ns
            ):
                self._drop_clock_segment()
            while len(self.clock_segments) > MAX_TIME_AXIS_SEGMENTS:
                self._drop_clock_segment()
        if self.last_unapplied_pair and (
            self._clock_floor is None
            or self.last_unapplied_pair.admission_ns < self._clock_floor
        ):
            self._set_unapplied_pair(None)

    def _time_axis(self, start, end, clock_id):
        reasons = []
        segments = []
        lower = upper = None
        if self.clock_segments and end >= self._clock_floor:
            lower = max(start, self._clock_floor)
            upper = min(end, self._clock_frontier)
            if lower <= upper:
                for segment in self.clock_segments:
                    if segment.start_ns > upper:
                        break
                    if segment.start_ns <= lower:
                        segments = [segment]
                    else:
                        segments.append(segment)
            else:
                lower = upper = None
        if not segments:
            lower = upper = None
            reasons.append("receive_clock_unavailable")
        if self._clock_truncated:
            reasons.append("receive_clock_history_truncated")
        if self._clock_floor is not None and start < self._clock_floor:
            reasons.append("requested_receive_clock_history_unavailable")
        if any(segment.cause == "wall_offset_change" for segment in segments):
            reasons.append("wall_offset_change")
        pair = self.last_unapplied_pair
        if pair is not None and not (
            lower is not None and lower <= pair.admission_ns <= upper
        ):
            pair = None
        if pair:
            reasons.append("nonadvancing_receive_pair")
        if self._clock_changed and segments:
            reasons.append("source_clock_changed")
        return {
            "version": "1",
            "basis": "receive_mono_highwater",
            "projection": TIME_AXIS_PROJECTION,
            "source_identity": self.source_identity,
            "clock_id": clock_id,
            "scope_id": self.clock_scope,
            "available_start_ns": exact(lower),
            "available_end_ns": exact(upper),
            "offset_change_threshold_ns": str(WALL_OFFSET_CHANGE_THRESHOLD_NS),
            "segments": [exact(segment) for segment in segments],
            "last_unapplied_pair": exact(pair),
            "reasons": reasons,
        }

    def _trim(self):
        self._trim_receive_clock()
        while len(self.samples) > MAX_COLUMNS:
            self._drop_sample()
        while len(self.trades) > MAX_TRADES:
            self._drop_trade()
        while len(self.discontinuities) > MAX_COLUMNS:
            self.trade_floor = max(
                self.trade_floor or 0, self.discontinuities.popleft() + 1
            )
            self.truncated = True
        while len(self.flow) > MAX_COLUMNS:
            self._drop_flow()
        while self.accounted_bytes > MAX_BYTES and (
            self.samples
            or self.trades
            or self.flow
            or self.clock_segments
            or self.last_unapplied_pair
        ):
            oldest = min(
                (self.samples[0].time_ns if self.samples else float("inf"), "sample"),
                (self.trades[0].time_ns if self.trades else float("inf"), "trade"),
                (self.flow[0][0] if self.flow else float("inf"), "flow"),
                (
                    self.clock_segments[0].start_ns
                    if self.clock_segments
                    and (
                        len(self.clock_segments) > 1
                        or not (
                            self.samples
                            or self.trades
                            or self.flow
                            or self.last_unapplied_pair
                        )
                    )
                    else float("inf"),
                    "clock",
                ),
                (
                    self.last_unapplied_pair.admission_ns
                    if self.last_unapplied_pair
                    else float("inf"),
                    "pair",
                ),
            )[1]
            if oldest == "sample":
                self._drop_sample()
            elif oldest == "trade":
                self._drop_trade()
            elif oldest == "clock":
                self._drop_clock_segment()
            elif oldest == "pair":
                self._set_unapplied_pair(None)
            else:
                self._drop_flow()
        if self.samples:
            while self.trades and self.trades[0].time_ns < self.samples[0].time_ns:
                self._drop_trade()
            while (
                self.discontinuities
                and self.discontinuities[0] < self.samples[0].time_ns
            ):
                self.discontinuities.popleft()
            while self.flow and self.flow[0][0] + SECOND <= self.samples[0].time_ns:
                self._drop_flow()

    def _drop_trade(self):
        trade = self.trades.popleft()
        self.accounted_bytes -= self._trade_sizes.popleft()
        self.trade_floor = max(self.trade_floor or 0, trade.time_ns + 1)
        self.truncated = True

    def _retain_trade(self, trade):
        size = 4096 + 4 * len(dumps(trade.print, decimal_policy=DERIVED_DECIMAL_POLICY))
        self.trades.append(trade)
        self._trade_sizes.append(size)
        self.accounted_bytes += size
        self._trim()

    @staticmethod
    def _flow_bytes(reasons):
        return 4096 + 4 * len(dumps(reasons))

    def _drop_flow(self):
        stamp, reasons, _ = self.flow.popleft()
        self.accounted_bytes -= self._flow_bytes(reasons)
        self.trade_floor = max(self.trade_floor or 0, stamp + SECOND)
        self.truncated = True

    def _close_flow(self, reading):
        """Freeze elapsed half-open intervals before applying the next delivery.

        Equal-highwater deliveries settle together. Quality at both interval
        endpoints captures freshness expiry even without a trade-channel event.
        """
        clock = self.clock
        instrument = self.instrument
        assert clock is not None and instrument is not None
        start = clock.mono_ns
        cutoff = max(0, reading.mono_ns // SECOND - MAX_COLUMNS) * SECOND
        if start < cutoff:
            start = cutoff
            self.trade_floor = max(self.trade_floor or 0, cutoff)
            self.truncated = True
        while start < reading.mono_ns:
            stamp = start // SECOND * SECOND
            end = min(stamp + SECOND, reading.mono_ns)
            evidence, final_gate = self.engine.trade_evidence(
                str(instrument.instrument_id),
                ClockReading(reading.clock_id, end - 1),
                key=self.key,
            )
            _, initial_gate = self.engine.trade_evidence(
                str(instrument.instrument_id),
                ClockReading(reading.clock_id, start),
                key=self.key,
            )
            reasons = tuple(dict.fromkeys((*initial_gate.reasons, *final_gate.reasons)))
            validated = evidence.last_validated_mono_ns if evidence else None
            if self.flow and self.flow[-1][0] == stamp:
                previous = self.flow.pop()[1]
                self.accounted_bytes -= self._flow_bytes(previous)
                reasons = tuple(dict.fromkeys((*previous, *reasons)))
            self.flow.append((stamp, reasons, validated))
            self.accounted_bytes += self._flow_bytes(reasons)
            self._trim()
            start = end

    def _sample(self, stamp):
        clock = self.clock
        assert clock is not None
        reasons = []
        book = self.book
        if book is None:
            reasons.append("no_validated_book")
        elif self.book_bytes(book) + 256 * 1024 + 4096 > MAX_BYTES:
            reasons.append("native_depth_exceeds_history_byte_limit")
            book = None
        metrics = {}
        if self.instrument is not None:
            now = ClockReading(clock.clock_id, stamp)
            if book is not None:
                reasons.extend(
                    evaluate(
                        book, self.instrument, "display", now, EligibilityPolicy()
                    ).reasons
                )
            if stamp - clock.mono_ns <= self.engine.config.policy.max_age_ns:
                snapshot = self.engine.snapshot(
                    str(self.instrument.instrument_id), now, key=self.key
                )
                metrics = {metric.name: metric for metric in snapshot.metrics}
            else:
                reasons.append("metrics_unavailable_without_recent_observation")
        return Sample(
            stamp,
            clock.clock_id,
            self.segment,
            book,
            tuple(dict.fromkeys(reasons)),
            metrics.get("cvd_base"),
            metrics.get("event_ofi"),
        )

    def on_batch(self, batch, books, clock):
        reading = clock.reading
        if reading is None:
            return
        # The engine must still contain only PREVIOUS completed deliveries here.
        if self.clock is not None and reading.clock_id == self.clock.clock_id:
            if self.instrument is not None:
                self._close_flow(reading)
            if self.next_boundary is not None:
                if reading.mono_ns - self.next_boundary > MAX_COLUMNS * SECOND:
                    self.next_boundary = (
                        (reading.mono_ns // SECOND) - MAX_COLUMNS
                    ) * SECOND
                    self.truncated = True
                while self.next_boundary < reading.mono_ns:
                    self._retain(self._sample(self.next_boundary))
                    self.next_boundary += SECOND
        elif self.clock is not None:
            # Different monotonic clocks have no invented numeric alignment.
            while self.samples:
                self._drop_sample()
            while self.trades:
                self._drop_trade()
            while self.flow:
                self._drop_flow()
            self.first_ns = None
            self.next_boundary = None
            self.trade_floor = None
            self.discontinuities.clear()
        self.engine.on_batch(batch, books, clock)
        if batch.events:
            self._observe_receive_clock(batch.events[0].ref(), reading)
        self.clock = reading
        self.book = next((book for book in books if book.key == self.key), None)
        relevant = [
            event
            for event in batch.events
            if event.stream_id in self.selected_streams
            or (
                isinstance(event.payload, Gap)
                and self.selected_streams.intersection(event.payload.affected_streams)
            )
        ]
        if relevant:
            event = relevant[-1]
            identity = (
                (
                    str(self.book.asof.session_id),
                    str(self.book.segment_id),
                    self.book.asof.clock_id,
                    str(self.book.connection_epoch),
                )
                if self.book
                else (
                    str(event.session_id),
                    str(event.segment_id),
                    event.clock_id,
                    str(event.connection_epoch),
                )
            )
            broken = any(
                isinstance(item.payload, Gap)
                or isinstance(item.payload, Control)
                and item.payload.kind
                in ("reset", "disconnect", "source_change", "rejection")
                for item in relevant
            )
            if identity != self.identity or broken:
                if self.identity is not None:
                    self.discontinuities.append(reading.mono_ns)
                self.segment_number += 1
                self.identity = identity
                self.segment = digest((identity, self.segment_number))
        if self.first_ns is None:
            self.first_ns = reading.mono_ns
        if self.next_boundary is None:
            self.next_boundary = ((reading.mono_ns + SECOND - 1) // SECOND) * SECOND
        if self.anchor is None and self.book and self.book.bids and self.book.asks:
            with localcontext(DECIMAL_CONTEXT):
                self.anchor = (self.book.bids[0].price + self.book.asks[0].price) / 2
        flow_eligible = (
            self.instrument is not None
            and self.engine.trade_evidence(
                str(self.instrument.instrument_id), reading, key=self.key
            )[1].eligible
        )
        for item in self.engine.admitted_tape(batch):
            if item.stream_id in self.trade_streams and (
                flow_eligible
                or item.trade.is_subscription_history
                and len(self.trade_streams) == 1
            ):
                self._retain_trade(ObservedTrade(reading.mono_ns, self.segment, item))
        if not self.engine.admitted_tape_complete(batch):
            self.truncated = True
            self.trade_floor = reading.mono_ns + 1
        self._trim()

    def checkpoint(self):
        samples = tuple(
            tuple(getattr(sample, field.name) for field in fields(sample))
            for sample in self.samples
        )
        trades = tuple(
            (trade.time_ns, trade.segment_id, trade.print) for trade in self.trades
        )
        state = (
            self.version,
            self.key,
            self.engine.checkpoint(),
            samples,
            trades,
            self.book,
            self.clock,
            self.next_boundary,
            self.segment,
            self.identity,
            self.segment_number,
            self.first_ns,
            self.truncated,
            self.trade_floor,
            self.anchor,
            self.price_step,
            self.bucket_ns,
            tuple(self.discontinuities),
            tuple(self.flow),
            (
                TIME_AXIS_PROJECTION,
                WALL_OFFSET_CHANGE_THRESHOLD_NS,
                self.source_identity,
                self._clock_epoch,
                self.clock_scope,
                self._clock_epoch_key,
                self._clock_floor,
                self._clock_frontier,
                self._clock_truncated,
                self._clock_changed,
                tuple(
                    tuple(getattr(segment, field.name) for field in fields(segment))
                    for segment in self.clock_segments
                ),
                tuple(
                    getattr(self.last_unapplied_pair, field.name)
                    for field in fields(self.last_unapplied_pair)
                )
                if self.last_unapplied_pair
                else None,
            ),
        )
        return dumps(state, decimal_policy=DERIVED_DECIMAL_POLICY)

    def restore(self, state):
        saved = loads(state, CheckpointState, decimal_policy=DERIVED_DECIMAL_POLICY)
        if saved[0] != self.version or saved[1] != self.key:
            raise ValueError("incompatible visualization checkpoint")
        axis = saved[19]
        if (
            axis[:2] != (TIME_AXIS_PROJECTION, WALL_OFFSET_CHANGE_THRESHOLD_NS)
            or self._configured_source_identity is not None
            and axis[2] != self._configured_source_identity
            or self.source_identity
            and axis[2] != self.source_identity
        ):
            raise ValueError("incompatible visualization clock checkpoint")
        self.reset()
        self.engine.restore(saved[2])
        (
            self.book,
            self.clock,
            self.next_boundary,
            self.segment,
            self.identity,
            self.segment_number,
            self.first_ns,
            self.truncated,
            self.trade_floor,
            self.anchor,
            self.price_step,
            self.bucket_ns,
        ) = saved[5:17]
        (
            self.source_identity,
            self._clock_epoch,
            self.clock_scope,
            self._clock_epoch_key,
            self._clock_floor,
            self._clock_frontier,
            self._clock_truncated,
            self._clock_changed,
        ) = axis[2:10]
        for state in axis[10]:
            segment = ReceiveClockSegment(*state)
            self.clock_segments.append(segment)
            size = self._clock_record_bytes(segment)
            self._clock_segment_sizes.append(size)
            self.accounted_bytes += size
        self._set_unapplied_pair(UnappliedReceivePair(*axis[11]) if axis[11] else None)
        self.discontinuities.extend(saved[17])
        for stamp, reasons, validated in saved[18]:
            self.flow.append((stamp, reasons, validated))
            self.accounted_bytes += self._flow_bytes(reasons)
        for sample in saved[3]:
            self._retain(Sample(*sample))
        for trade in saved[4]:
            self._retain_trade(ObservedTrade(*trade))
        self._trim()

    def query_snapshot(self):
        """Query-only view of one completed prefix; no reader or engine work."""
        snapshot = copy(self)
        snapshot.samples = self.samples.copy()
        snapshot.trades = self.trades.copy()
        snapshot.flow = self.flow.copy()
        snapshot.discontinuities = self.discontinuities.copy()
        snapshot._sample_metrics = self._sample_metrics.copy()
        snapshot.clock_segments = self.clock_segments.copy()
        return snapshot

    def accept_query(self, snapshot):
        """Commit presentation state only, never overwrite advancing evidence."""
        current_clock = self.clock.clock_id if self.clock else None
        queried_clock = snapshot.clock.clock_id if snapshot.clock else None
        if (
            self.key != snapshot.key
            or current_clock != queried_clock
            or self.source_identity != snapshot.source_identity
            or self.clock_scope != snapshot.clock_scope
            or self._query_epoch is not snapshot._query_epoch
        ):
            return False
        if snapshot.anchor is not None:
            self.anchor = snapshot.anchor
        self.price_step = snapshot.price_step
        self.bucket_ns = snapshot.bucket_ns
        self._last_query = snapshot._last_query
        return True

    def query(self, request, generation):
        with localcontext(DECIMAL_CONTEXT):
            return self._query(request, generation)

    def _query(self, request, generation):
        instrument = self.instrument
        if instrument is None:
            raise ValueError("no selected visualization instrument")
        step = display_step(instrument, request.price_step, self.price_step)
        origin = getattr(request, "price_min", None)
        if origin is not None:
            if not isinstance(origin, str):
                raise ValueError("price_min must be an exact decimal string")
            origin = decimal(origin, nonnegative=True)
            if request.price_step is None:
                raise ValueError("price_min requires explicit price_step")
            if request.recenter:
                raise ValueError("price_min cannot be combined with recenter")
            if origin % step:
                raise ValueError("price_min must align to price_step")
        bucket = (
            int(request.bucket_ns) if request.bucket_ns is not None else self.bucket_ns
        )
        if bucket not in (SECOND, 5 * SECOND, 10 * SECOND):
            raise ValueError("unsupported time quantum")
        now = self.clock.mono_ns if self.clock else 0
        end = min(int(request.end_ns), now) if request.end_ns is not None else now
        retained = self.samples[0].time_ns if self.samples else None
        first = self.first_ns or 0
        first_boundary = ((first + SECOND - 1) // SECOND) * SECOND
        # Trade/flow trimming can mark history truncated before any sampled
        # depth is evicted. Keep that initial partial bucket visible.
        history_start = (
            retained if retained is not None and retained > first_boundary else first
        )
        start = (
            int(request.start_ns)
            if request.start_ns is not None
            else max(
                0,
                end - 180 * SECOND,
                min(end, history_start // bucket * bucket),
            )
        )
        if start > end or end - start > MAX_COLUMNS * SECOND:
            raise ValueError("visual range must be ordered and at most 1800 seconds")
        visible_trades = [item for item in self.trades if start <= item.time_ns < end]
        valid_book = (
            self.book
            if self.book
            and self.book.bids
            and self.book.asks
            and self.clock
            and evaluate(
                self.book, instrument, "display", self.clock, EligibilityPolicy()
            ).eligible
            else None
        )
        if origin is None:
            if valid_book and (
                request.recenter or self.anchor is None or self.price_step is None
            ):
                self.anchor = (valid_book.bids[0].price + valid_book.asks[0].price) / 2
            if (
                valid_book
                and request.price_step is None
                and (self.price_step is None or request.recenter)
            ):
                step = fitted_step(
                    instrument, valid_book, visible_trades, self.anchor, request.rows
                )
            # An unavailable bootstrap must not freeze the fallback quantum: the
            # first validated two-sided book still gets its initial fit.
            if valid_book or request.price_step is not None:
                self.price_step = step
        self.bucket_ns = bucket
        anchor = self.anchor if self.anchor is not None else ZERO
        # Pane-owned origins never mutate the shared legacy anchor/quantum.
        low = (
            max(ZERO, origin + request.price_offset_rows * step)
            if origin is not None
            else max(
                ZERO,
                floor(anchor, step)
                + (request.price_offset_rows - request.rows // 2) * step,
            )
        )
        # Returned origins are resubmitted by independent panes. Reject a pan
        # outside that same bounded input domain, before publishing geometry.
        low = decimal(exact(low), nonnegative=True)
        prices = tuple(low + i * step for i in range(request.rows))
        clock_id = self.clock.clock_id if self.clock else "unobserved"
        grid = digest(
            (
                str(instrument.instrument_id),
                instrument.metadata_version,
                clock_id,
                self.identity[0] if self.identity else None,
                step,
                bucket,
                SAMPLING,
                str(instrument.base.asset_id),
            )
        )
        reasons = []
        if instrument.tick_size is None:
            reasons.append("display_quantum_not_native_tick")
        if start % bucket:
            reasons.append("initial_partial_bucket")
        if not self.trade_streams:
            reasons.append("trade_channel_unavailable")
        if self.first_ns is None or start < self.first_ns:
            reasons.append("initial_partial_history")
        if retained is None or start < retained:
            reasons.append("requested_history_unavailable")
        if self.truncated:
            reasons.append("history_retention_truncated")
        if self.trade_floor is not None and start < self.trade_floor:
            reasons.append("trade_history_truncated")
        # Closed samples are immutable; no provisional current-book column is
        # promoted to historical evidence. The final unobserved boundary is absent.
        selected = [
            sample
            for sample in self.samples
            if start <= sample.time_ns < end and sample.time_ns % bucket == 0
        ]
        maximum = min(request.max_columns, MAX_CELLS // request.rows)
        if len(selected) > maximum:
            selected = selected[-maximum:]
            reasons.append("column_output_limit")
        if not selected or selected[-1].time_ns + bucket > end:
            reasons.append("trailing_partial_history")
        flow_reasons: dict[int, set[str]] = {}
        final_validation = None
        for stamp, failures, validated in self.flow:
            if stamp < end and stamp + SECOND > start:
                bucket_start = stamp // bucket * bucket
                flow_reasons.setdefault(bucket_start, set()).update(failures)
                if stamp <= end - 1 < stamp + SECOND:
                    final_validation = validated
        unavailable_flow = sorted(
            {reason for failures in flow_reasons.values() for reason in failures}
        )
        if (
            not any(
                not item.print.trade.is_subscription_history for item in visible_trades
            )
            and final_validation != end - 1
        ):
            unavailable_flow.append("quiet_trade_interval_unproven")
        reasons.extend(unavailable_flow)
        columns = []
        # A per-column quota depends only on explicit query controls, not how
        # many newer columns arrived. Work exhaustion cannot repaint old cells.
        quota = MAX_QUERY_LEVELS // maximum
        projected = {}
        for sample in selected:
            projection_key = (id(sample.book), sample.reasons)
            if projection_key not in projected:
                cells, extra, _ = self._cells(sample, prices, step, quota)
                projected[projection_key] = (cells, extra)
            else:
                cells, extra = projected[projection_key]
            sample_reasons = list(sample.reasons) + extra
            if sample.time_ns < (self.first_ns or 0):
                sample_reasons.append("initial_partial_history")
            column_id = digest(
                (grid, sample.clock_id, sample.segment_id, sample.time_ns)
            )
            cvd, ofi = json.loads(self._sample_metrics[id(sample)])
            column = {
                "id": column_id,
                "time_ns": str(sample.time_ns),
                "end_ns": str(sample.time_ns + bucket),
                "segment_id": sample.segment_id,
                "asof": exact(sample.book.asof) if sample.book else None,
                "eligible": not any(
                    reason != "partial_depth_coverage" for reason in sample_reasons
                ),
                "reasons": sample_reasons,
                "liquidity_hash": digest(
                    (column_id, low, cells, tuple(sample_reasons))
                ),
                "cells": cells,
                "best_bid": exact(sample.book.bids[0].price)
                if sample.book and sample.book.bids and not sample.reasons
                else None,
                "best_ask": exact(sample.book.asks[0].price)
                if sample.book and sample.book.asks and not sample.reasons
                else None,
                "cvd": cvd,
                "ofi": ofi,
                "candle": None,
            }
            columns.append(column)
        if any(not column["eligible"] for column in columns):
            reasons.append("ineligible_liquidity_samples")
        if any("partial_depth_coverage" in column["reasons"] for column in columns):
            reasons.append("partial_depth_coverage")
        # One bounded trade pass, independent of depth/column count.
        profile: dict[Decimal, list[Decimal]] = {}
        footprints: dict[tuple[int, Decimal], list[Decimal]] = {}
        candles: dict[tuple[int, str], list[Decimal]] = {}
        plotted = []
        bucket_segments: dict[int, set[str]] = {}
        for sample in self.samples:
            if start <= sample.time_ns < end:
                bucket_segments.setdefault(
                    sample.time_ns // bucket * bucket, set()
                ).add(sample.segment_id)
        unknown_trade_quantity = False
        output_trades = []
        overlay_bytes = 0
        for observed in reversed(visible_trades):
            item, trade = observed.print, observed.print.trade
            encoded = {
                "id": digest((item.stream_id, item.asof, trade.dedup_key)),
                "time_ns": str(observed.time_ns),
                "price": exact(trade.price),
                "base_quantity": exact(item.base_quantity),
                "taker_side": trade.taker_side,
                "asof": exact(item.asof),
                "segment_id": observed.segment_id,
                "is_subscription_history": trade.is_subscription_history,
            }
            size = len(json.dumps(encoded, separators=(",", ":")).encode())
            if overlay_bytes + size > 1024 * 1024:
                reasons.append("trade_output_limit")
                break
            overlay_bytes += size
            output_trades.append((observed, encoded))
        for observed, encoded in reversed(output_trades):
            item, trade = observed.print, observed.print.trade
            plotted.append(encoded)
            if trade.is_subscription_history:
                continue
            if item.base_quantity is None:
                unknown_trade_quantity = True
                continue
            price = floor(trade.price, step)
            stamp = observed.time_ns // bucket * bucket
            bucket_segments.setdefault(stamp, set()).add(observed.segment_id)
            side = (
                0
                if trade.taker_side == "buy"
                else 1
                if trade.taker_side == "sell"
                else 2
            )
            if low <= price < low + request.rows * step:
                profile.setdefault(price, [ZERO, ZERO, ZERO])[side] += (
                    item.base_quantity
                )
                footprints.setdefault((stamp, price), [ZERO, ZERO, ZERO])[side] += (
                    item.base_quantity
                )
            key = (stamp, observed.segment_id)
            candle = candles.get(key)
            if candle is None:
                candles[key] = [
                    trade.price,
                    trade.price,
                    trade.price,
                    trade.price,
                    item.base_quantity,
                ]
            else:
                candle[1] = max(candle[1], trade.price)
                candle[2] = min(candle[2], trade.price)
                candle[3] = trade.price
                candle[4] += item.base_quantity
        if unknown_trade_quantity:
            reasons.append("unknown_trade_quantity")
        discontinuous = {
            stamp for stamp, segments in bucket_segments.items() if len(segments) > 1
        }
        discontinuous.update(
            stamp // bucket * bucket
            for stamp in self.discontinuities
            if start <= stamp < end and stamp % bucket
        )
        if discontinuous:
            reasons.append("interval_segment_discontinuity")
            footprints = {
                key: value
                for key, value in footprints.items()
                if key[0] not in discontinuous
            }
        for column in columns:
            candle = candles.get((int(column["time_ns"]), column["segment_id"]))
            if int(column["time_ns"]) in discontinuous:
                candle = None
            if candle:
                incomplete = sorted(flow_reasons.get(int(column["time_ns"]), ()))
                if int(column["end_ns"]) > end:
                    incomplete.append("trailing_partial_history")
                if (
                    self.trade_floor is not None
                    and int(column["time_ns"]) < self.trade_floor
                ):
                    incomplete.append("trade_history_truncated")
                if unknown_trade_quantity:
                    incomplete.append("unknown_trade_quantity")
                if "trade_output_limit" in reasons:
                    incomplete.append("trade_output_limit")
                column["candle"] = dict(
                    zip(("open", "high", "low", "close", "volume"), map(exact, candle))
                )
                column["candle"].update(eligible=not incomplete, reasons=incomplete)
        if any("query_level_work_limit" in column["reasons"] for column in columns):
            reasons.append("query_level_work_limit")
        # Exact encoded column accounting caps response growth even for unusually
        # large provenance references. Overlay counts have a separate hard cap.
        output_bytes = 0
        bounded_columns = []
        current = {}
        for column in reversed(columns):
            # Columns already contain exact HTTP scalars. Reuse their canonical
            # bytes for the incremental hash instead of recursively copying and
            # serializing the entire provenance tree again on the owner thread.
            column_bytes = json.dumps(
                column, sort_keys=True, separators=(",", ":")
            ).encode()
            size = len(column_bytes)
            if output_bytes + size > 4 * 1024 * 1024:
                reasons.append("column_byte_output_limit")
                break
            bounded_columns.append(column)
            output_bytes += size
            current[column["id"]] = sha256(column_bytes).hexdigest()[:32]
        columns = list(reversed(bounded_columns))
        token = digest(
            (
                grid,
                generation,
                low,
                request.rows,
                start,
                end,
                tuple((c["id"], c["liquidity_hash"], c["candle"]) for c in columns),
            )
        )
        # A single bounded previous result identity supports incremental polls;
        # changed ranges/evictions always request replacement, never partial lies.
        identity = (grid, generation, low, request.rows)
        replace = True
        delivered = columns
        if (
            self._last_query
            and request.after_column == self._last_query[0]
            and identity == self._last_query[1]
        ):
            previous = self._last_query[2]
            if previous.keys() <= current.keys():
                replace = False
                delivered = [
                    c for c in columns if previous.get(c["id"]) != current[c["id"]]
                ]
        self._last_query = (token, identity, current)
        incomplete_reasons = {
            "initial_partial_history",
            "requested_history_unavailable",
            "trade_history_truncated",
            "column_output_limit",
            "trailing_partial_history",
            "query_level_work_limit",
            "unknown_trade_quantity",
            "ineligible_liquidity_samples",
            "partial_depth_coverage",
            "interval_segment_discontinuity",
            "column_byte_output_limit",
            "trade_output_limit",
            "initial_partial_bucket",
            "trade_channel_unavailable",
        }
        return {
            "version": "1",
            "generation": str(generation),
            "grid_id": grid,
            "instrument_id": str(instrument.instrument_id),
            "clock_id": clock_id,
            "time_axis": self._time_axis(start, end, clock_id),
            "quantity_unit": str(instrument.base.asset_id)
            if _valuation(instrument, Decimal(1), Decimal(1))[0] is not None
            else "unavailable",
            "sampling": SAMPLING,
            "price_step": exact(step),
            "price_min": exact(low),
            "prices": list(map(exact, prices)),
            "rows": request.rows,
            "bucket_ns": str(bucket),
            "start_ns": str(start),
            "end_ns": str(end),
            "now_ns": str(now),
            "retained_start_ns": str(retained) if retained is not None else None,
            "step_options": list(
                map(exact, sorted(set((*step_options(instrument), step))))
            ),
            "columns": delivered,
            "trades": plotted,
            "profile": [
                {
                    "price": exact(p),
                    **dict(
                        zip(
                            ("buy_base", "sell_base", "unknown_base"),
                            map(exact, amounts),
                        )
                    ),
                }
                for p, amounts in sorted(profile.items())
            ],
            "footprints": [
                {
                    "time_ns": str(t),
                    "price": exact(p),
                    **dict(
                        zip(
                            ("buy_base", "sell_base", "unknown_base"),
                            map(exact, amounts),
                        )
                    ),
                }
                for (t, p), amounts in sorted(footprints.items())
            ],
            "reasons": reasons,
            "truncated": self.truncated
            or any(
                reason in reasons
                for reason in (
                    "column_output_limit",
                    "query_level_work_limit",
                    "column_byte_output_limit",
                    "trade_output_limit",
                )
            ),
            "complete_window": not incomplete_reasons.intersection(reasons)
            and not unavailable_flow,
            "replace": replace,
            "next_after": token,
        }

    def _cells(self, sample, prices, step, remaining):
        empty = [[None, None] for _ in prices]
        book = sample.book
        if sample.reasons or book is None:
            return empty, [], 0
        instrument = self.instrument
        assert instrument is not None
        if _valuation(instrument, Decimal(1), Decimal(1))[0] is None:
            return empty, ["unknown_contract_valuation"], 0
        low, high = prices[0], prices[-1] + step
        ranges = []
        for levels, reverse in ((book.bids, True), (book.asks, False)):
            if reverse:
                begin = bisect_right(levels, -high, key=lambda level: -level.price)
                end = bisect_right(levels, -low, key=lambda level: -level.price)
            else:
                begin = bisect_left(levels, low, key=lambda level: level.price)
                end = bisect_left(levels, high, key=lambda level: level.price)
            ranges.append((levels, begin, end))
        count = sum(end - begin for _, begin, end in ranges)
        if count > remaining:
            return empty, ["query_level_work_limit"], max(0, remaining)
        cells: list[list[Decimal | None]] = [[ZERO, ZERO] for _ in prices]
        for side, (levels, begin, end) in enumerate(ranges):
            for index in range(begin, end):
                level = levels[index]
                base, _ = _valuation(instrument, level.price, level.quantity)
                if base is None:
                    return empty, ["unknown_contract_valuation"], count
                cell = cells[int((level.price - low) // step)]
                quantity = cell[side]
                assert quantity is not None
                cell[side] = quantity + base
        coverage = book.quality.coverage
        partial = False
        for index, price in enumerate(prices):
            if not coverage.full_depth:
                # A whole price cell must be covered, not just its populated edge.
                if not book.bids or price < book.bids[-1].price:
                    cells[index][0] = None
                    partial = True
                if not book.asks or price + step > book.asks[-1].price + (
                    instrument.tick_size or ZERO
                ):
                    cells[index][1] = None
                    partial = True
        return (
            [[exact(value) for value in pair] for pair in cells],
            ["partial_depth_coverage"] if partial else [],
            count,
        )


def grouped_ladder(book, instrument, now, request, generation):
    with localcontext(DECIMAL_CONTEXT):
        step = display_step(instrument, request.price_step)
        reasons = (
            list(
                evaluate(book, instrument, "display", now, EligibilityPolicy()).reasons
            )
            if book and now
            else ["no_validated_book"]
        )
        if _valuation(instrument, Decimal(1), Decimal(1))[0] is None:
            reasons.append("unknown_contract_valuation")
        result = []
        if book and not reasons:
            for side, levels in (("bid", book.bids), ("ask", book.asks)):
                groups: list[list[Decimal]] = []
                examined = 0
                for level in levels:
                    price = (level.price / step).to_integral_value(
                        rounding=ROUND_FLOOR if side == "bid" else ROUND_CEILING
                    ) * step
                    if (
                        groups
                        and groups[-1][0] != price
                        and len(groups) == request.rows
                    ):
                        break
                    examined += 1
                    if examined > MAX_QUERY_LEVELS:
                        reasons.append("query_level_work_limit")
                        groups = []
                        break
                    base, quote = _valuation(instrument, level.price, level.quantity)
                    # Value each native level before outward display grouping:
                    # the bucket price is not an execution or valuation price.
                    assert base is not None and quote is not None
                    if not groups or groups[-1][0] != price:
                        groups.append([price, ZERO, ZERO])
                    groups[-1][1] += base
                    groups[-1][2] += quote
                cumulative = cumulative_quote = ZERO
                for price, base, quote in groups:
                    cumulative += base
                    cumulative_quote += quote
                    result.append(
                        {
                            "side": side,
                            "price": exact(price),
                            "base_quantity": exact(base),
                            "cumulative_base": exact(cumulative),
                            "quote_notional": exact(quote),
                            "cumulative_quote": exact(cumulative_quote),
                        }
                    )
            if not book.quality.coverage.full_depth:
                reasons.append("partial_depth_coverage")
        if instrument.tick_size is None:
            reasons.append("display_quantum_not_native_tick")
        eligible = not any(
            reason not in ("partial_depth_coverage", "display_quantum_not_native_tick")
            for reason in reasons
        )
        summary = None
        if eligible and book and book.bids and book.asks:
            best_bid, best_ask = book.bids[0].price, book.asks[0].price
            midpoint = (best_bid + best_ask) / 2
            spread = best_ask - best_bid
            summary = {
                "best_bid": exact(best_bid),
                "best_ask": exact(best_ask),
                "midpoint": exact(midpoint),
                "spread": exact(spread),
                "spread_ticks": exact(spread / instrument.tick_size)
                if instrument.tick_size is not None
                else None,
                "spread_bps": exact(spread / midpoint * 10000),
            }
        coverage = book.quality.coverage if book else None
        return {
            "version": "1",
            "generation": str(generation),
            "instrument_id": str(instrument.instrument_id),
            "asof": exact(book.asof) if book else None,
            "step": exact(step),
            "tick_size": exact(instrument.tick_size),
            "step_options": exact(step_options(instrument)),
            "quote_unit": str(instrument.quote.asset_id),
            "coverage": {
                "subscribed_depth": exact(coverage.subscribed_depth),
                "retained_bid_levels": exact(coverage.retained_bid_levels),
                "retained_ask_levels": exact(coverage.retained_ask_levels),
                "full_depth": coverage.full_depth,
            }
            if coverage is not None
            else None,
            "source_mode": book.quality.source_mode if book else None,
            "summary": summary,
            "unit": "unavailable"
            if "unknown_contract_valuation" in reasons
            else str(instrument.base.asset_id),
            "eligible": eligible,
            "reasons": reasons,
            "rows": result,
        }


def proposal_risk(instrument, now, request, generation):
    """Hypothetical fixed-native-position loss, not simulated execution."""
    with localcontext(DECIMAL_CONTEXT):
        entry = decimal(request.entry_price, positive=True)
        stop = decimal(request.stop_price, positive=True)
        reasons = []
        assumptions = [
            "hypothetical_entry_and_stop_not_fills",
            "stop_liquidity_unknown",
            "gaps_and_slippage_can_exceed_estimate",
            "not_a_guaranteed_loss_bound",
            "fees_assumed_quote_denomination_both_legs",
        ]
        if (
            request.side == "buy"
            and stop >= entry
            or request.side == "sell"
            and stop <= entry
        ):
            reasons.append("stop_must_be_below_buy_or_above_sell_entry")
        for price in (entry, stop):
            try:
                _validate_price(instrument, price)
            except ValueError:
                reasons.append("unsupported_or_invalid_price_precision")
        valuation = _valuation_reason(instrument)
        if valuation:
            reasons.append(valuation)
        if (
            instrument.quantity_unit == "contracts"
            and not instrument.contract_specification_source
        ):
            reasons.append("unknown_contract_specification")
        constraints = instrument.constraints
        if constraints.size_rule != "fixed_lot":
            reasons.append("unsupported_size_rule")
        if (
            constraints.min_notional is not None or constraints.max_notional is not None
        ) and constraints.min_notional_currency != instrument.quote.asset_id:
            reasons.append("unknown_notional_constraint_currency")
        fees = FeeSchedule()
        if request.fee_bps is not None:
            date = datetime.strptime(request.fee_date, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
            fees = FeeSchedule(
                rate=decimal(request.fee_bps) / 10000,
                provenance=request.fee_provenance,
                asof_wall_ns=int(date.timestamp()) * SECOND,
                assumed=True,
            )
            assumptions.append(
                "user_assumed_fees:" + fees.provenance + ":" + request.fee_date
            )
        if fees.rate is None:
            reasons.append("unknown_fees")
        elif now and now.wall_ns is not None and fees.asof_wall_ns > now.wall_ns:
            reasons.append("fee_date_in_future")
        base = (
            decimal(request.base_quantity, positive=True)
            if request.base_quantity
            else None
        )
        suggested = gross = fee = loss = None
        blocking = [reason for reason in reasons if reason != "unknown_fees"]
        if not blocking:
            # Quote loss for inverse contracts values the native fixed position's
            # base-denominated P/L at the hypothetical stop (not entry-base * move).
            unit_entry_base, unit_entry_quote = _amounts(instrument, entry, Decimal(1))
            _, unit_stop_quote = _amounts(instrument, stop, Decimal(1))
            unit_gross = abs(unit_entry_base * (entry - stop))
            unit_fee = (
                (unit_entry_quote + unit_stop_quote) * fees.rate
                if fees.rate is not None
                else None
            )
            if request.risk_budget is not None and unit_fee is not None:
                budget = decimal(request.risk_budget, positive=True)
                native = floor(budget / (unit_gross + unit_fee), instrument.lot_size)
                caps = [
                    value
                    for value in (
                        constraints.max_base,
                        constraints.max_limit_base,
                        constraints.max_market_base,
                    )
                    if value is not None
                ]
                if constraints.max_notional is not None:
                    caps.append(
                        constraints.max_notional
                        / max(unit_entry_quote, unit_stop_quote)
                    )
                if caps:
                    native = min(native, floor(min(caps), instrument.lot_size))
                base = _amounts(instrument, entry, native)[0]
                suggested = base
            elif base is not None:
                native = _native_for_base(instrument, entry, base)
            else:
                native = None
            if native is not None:
                if native <= 0:
                    reasons.append("risk_budget_below_minimum_lot")
                try:
                    instrument.validate_quantity(native)
                except ValueError:
                    reasons.append("quantity_off_native_lot")
                for value, minimum, maximum in (
                    (native, constraints.min_base, constraints.max_base),
                    (native, None, constraints.max_limit_base),
                    (native, None, constraints.max_market_base),
                    (
                        native * unit_entry_quote,
                        constraints.min_notional,
                        constraints.max_notional,
                    ),
                    (
                        native * unit_stop_quote,
                        constraints.min_notional,
                        constraints.max_notional,
                    ),
                ):
                    if (
                        minimum is not None
                        and value < minimum
                        or maximum is not None
                        and value > maximum
                    ):
                        reasons.append("order_outside_instrument_constraints")
                gross = native * unit_gross
                fee = native * unit_fee if unit_fee is not None else None
                loss = gross + fee if fee is not None else None
                if (
                    request.risk_budget is not None
                    and loss is not None
                    and loss > decimal(request.risk_budget)
                ):
                    reasons.append("derived_risk_exceeds_budget")
        if reasons and request.risk_budget is not None:
            suggested = None
        return {
            "version": "1",
            "generation": str(generation),
            "instrument_id": str(instrument.instrument_id),
            "quote_asset_id": str(instrument.quote.asset_id),
            "base_quantity": exact(base),
            "suggested_base_quantity": exact(suggested),
            "gross_loss": exact(gross),
            "fee_cost": exact(fee),
            "estimated_loss": exact(loss),
            "eligible": not reasons,
            "reasons": list(dict.fromkeys(reasons)),
            "assumptions": assumptions,
        }
