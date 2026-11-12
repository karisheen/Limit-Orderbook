"""Bounded open-interest and funding history over admitted asset-context events.

Positioning is venue-reported perpetual context, never an executable price,
exchange guarantee or account state. Observations coalesce into UTC-minute
bars on the admitted source receive-clock projection shared with session
candles, so every candle interval aggregates them exactly on the same axis.
Fields merge only inside an observed minute: nothing is carried across missing
minutes, no bar is synthesized and no data beyond the source frontier is used.
"""

from collections import OrderedDict
from dataclasses import astuple, dataclass, replace
from decimal import Decimal, localcontext
from typing import TypeAlias

from .book import Book
from .candles import LOCAL_INTERVALS, MAX_LIMIT, SECOND, session_bucket
from .events import AssetContext, Control, Gap
from .instruments import Instrument
from .market_presentation import AdmittedTradeHistory, ClockScope
from .precision import DECIMAL_CONTEXT, DERIVED_DECIMAL_POLICY, canonical_decimal
from .recording import RecordedBatch, StreamSpec
from .replay import ReplayClock
from .wire import dumps, loads

MINUTE = 60 * SECOND
MAX_MINUTES = 10_080
MAX_STREAMS = 16
_CLOCK_REASONS = frozenset(
    (
        "receive_clock_discontinuity",
        "receive_clock_history_truncated",
        "nonadvancing_receive_pair",
        "source_clock_changed",
    )
)


@dataclass(frozen=True)
class PositioningBar:
    """Open-interest envelope and last funding/reference values of one bucket."""

    start_ns: int
    first_ns: int
    last_ns: int
    samples: int
    open_interest_open: Decimal | None = None
    open_interest_high: Decimal | None = None
    open_interest_low: Decimal | None = None
    open_interest_close: Decimal | None = None
    funding_rate: Decimal | None = None
    funding_interval_ns: int | None = None
    mark: Decimal | None = None
    oracle: Decimal | None = None

    def merged(self, later: "PositioningBar", start_ns: int) -> "PositioningBar":
        """Fold a later bar of the same bucket; absent fields never erase values."""

        def last(name):
            value = getattr(later, name)
            return value if value is not None else getattr(self, name)

        def extreme(name, choose):
            values = [
                value
                for value in (getattr(self, name), getattr(later, name))
                if value is not None
            ]
            return choose(values) if values else None

        funded = later.funding_rate is not None
        return PositioningBar(
            start_ns,
            self.first_ns,
            later.last_ns,
            self.samples + later.samples,
            self.open_interest_open
            if self.open_interest_open is not None
            else later.open_interest_open,
            extreme("open_interest_high", max),
            extreme("open_interest_low", min),
            last("open_interest_close"),
            later.funding_rate if funded else self.funding_rate,
            later.funding_interval_ns if funded else self.funding_interval_ns,
            last("mark"),
            last("oracle"),
        )

    def valid(self, width_ns: int) -> bool:
        interest = (
            self.open_interest_open,
            self.open_interest_high,
            self.open_interest_low,
            self.open_interest_close,
        )
        observed = [value for value in interest if value is not None]
        if observed and (
            len(observed) != 4
            or any(not value.is_finite() or value < 0 for value in observed)
            or not observed[2] <= observed[0] <= observed[1]
            or not observed[2] <= observed[3] <= observed[1]
        ):
            return False
        return (
            self.start_ns >= 0
            and self.start_ns <= self.first_ns <= self.last_ns
            and self.last_ns < self.start_ns + width_ns
            and self.samples >= 1
            and (self.funding_rate is None or self.funding_rate.is_finite())
            and (self.funding_interval_ns is None or self.funding_interval_ns > 0)
            and (self.funding_rate is not None or self.funding_interval_ns is None)
            and all(
                value is None or (value.is_finite() and value > 0)
                for value in (self.mark, self.oracle)
            )
        )

    def wire(self, end_ns: int, now_ns: int) -> dict:
        def exact(value):
            return (
                None
                if value is None
                else canonical_decimal(value, policy=DERIVED_DECIMAL_POLICY)
            )

        interest = None
        if self.open_interest_close is not None:
            assert self.open_interest_open is not None
            with localcontext(DECIMAL_CONTEXT):
                change = self.open_interest_close - self.open_interest_open
            interest = {
                "open": exact(self.open_interest_open),
                "high": exact(self.open_interest_high),
                "low": exact(self.open_interest_low),
                "close": exact(self.open_interest_close),
                "change": exact(change),
            }
        return {
            "start_ns": str(self.start_ns),
            "end_ns": str(end_ns),
            "first_ns": str(self.first_ns),
            "last_ns": str(self.last_ns),
            "samples": self.samples,
            "closed": end_ns <= now_ns,
            "open_interest": interest,
            "funding_rate": exact(self.funding_rate),
            "funding_interval_ns": None
            if self.funding_interval_ns is None
            else str(self.funding_interval_ns),
            "mark": exact(self.mark),
            "oracle": exact(self.oracle),
        }


BarState: TypeAlias = tuple[
    int,
    int,
    int,
    int,
    Decimal | None,
    Decimal | None,
    Decimal | None,
    Decimal | None,
    Decimal | None,
    int | None,
    Decimal | None,
    Decimal | None,
]
PositioningState: TypeAlias = tuple[
    str,
    tuple[str, ...],
    int,
    ClockScope | None,
    tuple[tuple[str, str | None, tuple[BarState, ...]], ...],
    tuple[str, ...],
    bool,
]


class PositioningHistory:
    """Owner-only minute bars per perpetual context stream.

    Runs after the admitted trade history of the same delivery, whose first
    receive pair per source clock is the single projection origin. Continuity
    controls are disclosed but keep earlier observations: they were observed.
    An incomparable source clock or a changed open-interest unit clears bars.
    """

    observer_id = "positioning_context_history"
    version = "1"

    def __init__(
        self, streams: tuple[StreamSpec, ...], clock: AdmittedTradeHistory
    ) -> None:
        self.clock = clock
        self.specs = {
            str(spec.stream_id): spec
            for spec in streams
            if spec.key.channel == "context"
            and spec.instrument.market_type == "perpetual"
        }
        if len(self.specs) > MAX_STREAMS:
            raise ValueError("positioning stream bound exceeded")
        self.reset()

    def reset(self) -> None:
        self.delivery = 0
        self.scope: ClockScope | None = None
        self.bars: dict[str, OrderedDict[int, PositioningBar]] = {
            key: OrderedDict() for key in self.specs
        }
        self.units: dict[str, str] = {}
        self.reasons: set[str] = set()
        self.truncated = False

    def on_batch(
        self, batch: RecordedBatch, books: tuple[Book, ...], clock: ReplayClock
    ) -> None:
        if batch.delivery_index <= self.delivery:
            raise ValueError("positioning delivery must advance; reset before seek")
        if self.clock.highwater != batch.delivery_index:
            raise ValueError("positioning requires this delivery's admitted clock")
        self.delivery = batch.delivery_index
        for event in batch.events:
            payload = event.payload
            key = str(event.stream_id)
            if event.stream_id in self.clock.specs:
                scope = (event.session_id, event.clock_id)
                if scope != self.scope:
                    if self.scope is not None:
                        # All admitted streams share this axis, even when a
                        # clock transition contains no positioning sample.
                        self.reasons.add("source_clock_changed")
                        for bars in self.bars.values():
                            bars.clear()
                        self.units.clear()
                    self.scope = scope
            if isinstance(payload, Gap) and any(
                self.bars.get(str(target)) for target in payload.affected_streams
            ):
                self.reasons.add("context_discontinuities")
            spec = self.specs.get(key)
            if spec is None:
                continue
            if (
                event.key != spec.key
                or event.instrument_metadata_version != spec.instrument.metadata_version
            ):
                raise ValueError("positioning event identity mismatch")
            if isinstance(payload, Gap) or (
                isinstance(payload, Control)
                and payload.kind
                in ("reset", "disconnect", "source_change", "rejection")
            ):
                # A boundary before the first observation interrupts nothing.
                if self.bars[key]:
                    self.reasons.add("context_discontinuities")
                continue
            if not isinstance(payload, AssetContext) or (
                payload.open_interest is None and payload.funding_rate is None
            ):
                continue
            stamp = self.clock.display_time(event)
            if stamp is None or stamp < 0:
                self.reasons.add("unprojectable_context_clock")
                continue
            bars = self.bars[key]
            if bars and stamp < next(reversed(bars.values())).last_ns:
                self.reasons.add("nonadvancing_context_clock")
                continue
            if payload.open_interest is not None:
                unit = payload.open_interest_unit
                assert unit is not None
                if self.units.setdefault(key, unit) != unit:
                    self.reasons.add("open_interest_unit_changed")
                    bars.clear()
                    self.units[key] = unit
            start = stamp // MINUTE * MINUTE
            sample = PositioningBar(
                start,
                stamp,
                stamp,
                1,
                *(payload.open_interest,) * 4,
                payload.funding_rate,
                payload.funding_interval_ns
                if payload.funding_rate is not None
                else None,
                payload.mark,
                payload.oracle,
            )
            previous = bars.get(start)
            bars[start] = sample if previous is None else previous.merged(sample, start)
            while len(bars) > MAX_MINUTES:
                bars.popitem(last=False)
                self.truncated = True

    def checkpoint(self) -> bytes:
        state: PositioningState = (
            self.version,
            tuple(self.specs),
            self.delivery,
            self.scope,
            tuple(
                (key, self.units.get(key), tuple(astuple(bar) for bar in bars.values()))
                for key, bars in self.bars.items()
            ),
            tuple(sorted(self.reasons)),
            self.truncated,
        )
        # Preserve the observed Decimal spelling of venue-reported values.
        return dumps(state, canonical=False, decimal_policy=DERIVED_DECIMAL_POLICY)

    def restore(self, state: bytes) -> None:
        checkpoint: PositioningState = loads(
            state, PositioningState, decimal_policy=DERIVED_DECIMAL_POLICY
        )
        version, streams, delivery, scope, rows, reasons, truncated = checkpoint
        if (
            version != self.version
            or streams != tuple(self.specs)
            or delivery < 0
            or tuple(key for key, _, _ in rows) != streams
        ):
            raise ValueError("incompatible positioning checkpoint")
        restored: dict[str, OrderedDict[int, PositioningBar]] = {}
        units: dict[str, str] = {}
        for key, unit, states in rows:
            bars = tuple(PositioningBar(*state) for state in states)
            starts = [bar.start_ns for bar in bars]
            if (
                len(bars) > MAX_MINUTES
                or starts != sorted(set(starts))
                or any(start % MINUTE for start in starts)
                or any(not bar.valid(MINUTE) for bar in bars)
                or (bars and scope is None)
                or (
                    unit is None
                    and any(bar.open_interest_close is not None for bar in bars)
                )
            ):
                raise ValueError("invalid positioning checkpoint bars")
            restored[key] = OrderedDict((bar.start_ns, bar) for bar in bars)
            if unit is not None:
                units[key] = unit
        self.delivery, self.scope = delivery, scope
        self.bars, self.units = restored, units
        self.reasons, self.truncated = set(reasons), truncated


def positioning_series(
    history: PositioningHistory, selected: Instrument, interval: str, limit: int
) -> dict:
    """Aggregate observed minute bars of the selected underlying's perpetuals.

    Buckets match `session_candles` exactly. A perpetual of another venue with
    the same canonical base asset is disclosed as related, never relabeled.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise ValueError("invalid positioning limit")
    clock = history.clock
    now = clock.now_ns
    reasons = {
        "observed_session_context_only",
        "session_history_partial",
        "receive_time_buckets",
        "venue_reported_not_executable",
        "closed_is_time_boundary_not_completeness",
        *history.reasons,
        *(_CLOCK_REASONS & clock.reasons),
    }
    if history.truncated:
        reasons.add("retained_history_truncated")
    if now is None:
        reasons.add("missing_receive_utc")
    if interval not in LOCAL_INTERVALS:
        reasons.add("unsupported_interval")
    usable = now is not None and interval in LOCAL_INTERVALS
    comparable = history.scope is None or history.scope == clock.scope
    if not comparable:
        reasons.add("unrelated_source_clock")
    series = []
    for key, spec in history.specs.items():
        item = spec.instrument
        if item.base.asset_id != selected.base.asset_id:
            continue
        buckets: OrderedDict[int, tuple[PositioningBar, int]] = OrderedDict()
        if usable and comparable:
            assert now is not None
            for bar in history.bars[key].values():
                if bar.last_ns > now:
                    reasons.add("future_context_excluded")
                    continue
                start, end = session_bucket(bar.start_ns, interval)
                previous = buckets.get(start)
                buckets[start] = (
                    (bar if bar.start_ns == start else replace(bar, start_ns=start))
                    if previous is None
                    else previous[0].merged(bar, start),
                    end,
                )
        rows = list(buckets.values())[-limit:]
        series.append(
            {
                "stream_id": key,
                "instrument_id": str(item.instrument_id),
                "venue": item.venue,
                "api_family": item.api_family,
                "native_symbol": item.native_symbol,
                "base_asset_id": item.base.asset_id,
                "quote_asset_id": item.quote.asset_id,
                "relation": "selected"
                if item.instrument_id == selected.instrument_id
                else "related_underlying",
                "open_interest_unit": history.units.get(key),
                "open_interest_unit_provenance": (
                    "adapter_declared_not_independently_verified"
                ),
                "funding_rate_unit": "fraction_per_funding_interval",
                "bars": [bar.wire(end, now or 0) for bar, end in rows],
                "coverage_start_ns": str(rows[0][0].first_ns) if rows else None,
                "coverage_end_ns": str(rows[-1][0].last_ns) if rows else None,
                "retention": {
                    "base_interval": "1m",
                    "max_minutes": MAX_MINUTES,
                    "retained_minutes": len(history.bars[key]),
                },
            }
        )
    series.sort(
        key=lambda row: (
            row["relation"] != "selected",
            row["venue"],
            row["instrument_id"],
        )
    )
    if not series:
        reasons.add("context_channel_not_subscribed")
    return {
        "instrument_id": str(selected.instrument_id),
        "interval": interval,
        "status": "unavailable"
        if not usable or not comparable
        else "ready"
        if any(row["bars"] for row in series)
        else "empty",
        "source": "session_context",
        "time_basis": "receive_utc",
        "fetched_at_ns": str(now or 0),
        "supported_intervals": list(LOCAL_INTERVALS),
        "bucket_alignment": "utc_calendar_month"
        if interval == "1M"
        else "utc_epoch_floor",
        "series": series,
        "reasons": sorted(reasons),
        "partial": True,
        "highwater": str(history.delivery),
        "label": "venue_reported_positioning_not_executable",
    }
