"""Completed-delivery visualization evidence, never browser-frame history.

The observer owns analytics ordering: close old boundaries, apply the complete
message, then admit its shared normalized prints. Native Books are immutable and
shared; display bounds never clip the collector or reducer.
"""

from bisect import bisect_left, bisect_right
from collections import deque
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
from .events import Control, Gap, SubscriptionKey
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
        raise ValueError("unsupported display price quantum")
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
]


class VisualHistory:
    """One bounded selected-instrument observer, owned by the session worker."""

    observer_id = "visualization_with_analytics"
    version = "2"

    def __init__(self, engine: AnalyticsEngine, key: SubscriptionKey | None = None):
        self.engine, self.key = engine, key
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
        self.price_step = getattr(self, "price_step", None)
        self.bucket_ns = getattr(self, "bucket_ns", SECOND)
        self.accounted_bytes = 256 * 1024  # bounded query fingerprints/ownership
        self._books = {}
        self._last_query = None

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
        if sample.book and self.book_bytes(sample.book) + size + 256 * 1024 > MAX_BYTES:
            sample = replace(
                sample,
                book=None,
                reasons=sample.reasons + ("native_depth_exceeds_history_byte_limit",),
            )
            size += 512
        self.samples.append(sample)
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
        if sample.book:
            identity = id(sample.book)
            retained = self._books[identity]
            retained[1] -= 1
            if not retained[1]:
                self.accounted_bytes -= self.book_bytes(sample.book)
                del self._books[identity]
        self.truncated = True

    def _trim(self):
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
            self.samples or self.trades or self.flow
        ):
            oldest = min(
                (self.samples[0].time_ns if self.samples else float("inf"), "sample"),
                (self.trades[0].time_ns if self.trades else float("inf"), "trade"),
                (self.flow[0][0] if self.flow else float("inf"), "flow"),
            )[1]
            if oldest == "sample":
                self._drop_sample()
            elif oldest == "trade":
                self._drop_trade()
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
        )
        return dumps(state, decimal_policy=DERIVED_DECIMAL_POLICY)

    def restore(self, state):
        saved = loads(state, CheckpointState, decimal_policy=DERIVED_DECIMAL_POLICY)
        if saved[0] != self.version or saved[1] != self.key:
            raise ValueError("incompatible visualization checkpoint")
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
        ) = saved[5:-2]
        self.discontinuities.extend(saved[-2])
        for stamp, reasons, validated in saved[-1]:
            self.flow.append((stamp, reasons, validated))
            self.accounted_bytes += self._flow_bytes(reasons)
        for sample in saved[3]:
            self._retain(Sample(*sample))
        for trade in saved[4]:
            self._retain_trade(ObservedTrade(*trade))
        self._trim()

    def query(self, request, generation):
        with localcontext(DECIMAL_CONTEXT):
            return self._query(request, generation)

    def _query(self, request, generation):
        instrument = self.instrument
        if instrument is None:
            raise ValueError("no selected visualization instrument")
        step = display_step(instrument, request.price_step, self.price_step)
        bucket = (
            int(request.bucket_ns) if request.bucket_ns is not None else self.bucket_ns
        )
        if bucket not in (SECOND, 5 * SECOND, 10 * SECOND):
            raise ValueError("unsupported time quantum")
        now = self.clock.mono_ns if self.clock else 0
        end = min(int(request.end_ns), now) if request.end_ns is not None else now
        start = (
            int(request.start_ns)
            if request.start_ns is not None
            else max(0, end - 180 * SECOND)
        )
        if start > end or end - start > MAX_COLUMNS * SECOND:
            raise ValueError("visual range must be ordered and at most 1800 seconds")
        self.price_step, self.bucket_ns = step, bucket
        if request.recenter and self.book and self.book.bids and self.book.asks:
            self.anchor = (self.book.bids[0].price + self.book.asks[0].price) / 2
        anchor = self.anchor or ZERO
        low = max(
            ZERO,
            floor(anchor, step)
            + (request.price_offset_rows - request.rows // 2) * step,
        )
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
        retained = self.samples[0].time_ns if self.samples else None
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
        visible_trades = [item for item in self.trades if start <= item.time_ns < end]
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
                "cvd": exact(sample.cvd),
                "ofi": exact(sample.ofi),
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
        for column in reversed(columns):
            size = len(json.dumps(column, separators=(",", ":")).encode())
            if output_bytes + size > 4 * 1024 * 1024:
                reasons.append("column_byte_output_limit")
                break
            bounded_columns.append(column)
            output_bytes += size
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
        current = {c["id"]: digest(c) for c in columns}
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
            "step_options": list(map(exact, step_options(instrument))),
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
                    base, _ = _valuation(instrument, level.price, level.quantity)
                    assert base is not None  # Valuation capability gated above.
                    if not groups or groups[-1][0] != price:
                        groups.append([price, ZERO])
                    groups[-1][1] += base
                cumulative = ZERO
                for price, base in groups:
                    cumulative += base
                    result.append(
                        {
                            "side": side,
                            "price": exact(price),
                            "base_quantity": exact(base),
                            "cumulative_base": exact(cumulative),
                        }
                    )
            if not book.quality.coverage.full_depth:
                reasons.append("partial_depth_coverage")
        if instrument.tick_size is None:
            reasons.append("display_quantum_not_native_tick")
        return {
            "version": "1",
            "generation": str(generation),
            "instrument_id": str(instrument.instrument_id),
            "asof": exact(book.asof) if book else None,
            "step": exact(step),
            "unit": "unavailable"
            if "unknown_contract_valuation" in reasons
            else str(instrument.base.asset_id),
            "eligible": not any(
                reason
                not in ("partial_depth_coverage", "display_quantum_not_native_tick")
                for reason in reasons
            ),
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
