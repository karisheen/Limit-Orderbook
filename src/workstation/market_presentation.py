"""Bounded display transport over owner-completed canonical deliveries.

Trades and trade-stream boundaries share an exclusive delivery/event cursor.
The page limit counts both; one fan-out boundary group remains atomic even when
it exceeds that limit. Consumers merge the arrays by cursor and compare each
trade's source_identity per stream to detect native source-epoch transitions.
The response gap flag only describes cursor retention loss, not stream health.
"""

from collections import OrderedDict, deque
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import TypeAlias

from .analytics import AnalyticsEngine, TapePrint, _valuation
from .book import Book
from .events import Control, EventEnvelope, Gap, Trade
from .precision import DECIMAL_CONTEXT, DERIVED_DECIMAL_POLICY
from .quality import EligibilityPolicy, evaluate
from .recording import RecordedBatch, StreamSpec
from .replay import ReplayClock
from .wire import dumps, loads


MAX_SCAN_EVENTS = 10_000
MAX_SCAN_BATCHES = 10_000
MAX_TRADE_ROWS = 10_000
MAX_TRADE_BYTES = 8 * 1024 * 1024
MAX_CLOCK_ORIGINS = 256

ClockScope: TypeAlias = tuple[str, str]
SourceIdentity: TypeAlias = tuple[str, str, str, int]
TradeCursor: TypeAlias = tuple[int, int]
TradeHistoryState: TypeAlias = tuple[
    str,
    tuple[StreamSpec, ...],
    tuple[tuple[EventEnvelope, Decimal | None, int, int, int, int], ...],
    int,
    tuple[tuple[ClockScope, tuple[int, int]], ...],
    ClockScope | None,
    int | None,
    int,
    tuple[str, ...],
    bool,
    TradeCursor,
    tuple[tuple[str, SourceIdentity], ...],
    tuple[tuple[str, TradeCursor], ...],
]


@dataclass(frozen=True)
class AdmittedTrade:
    event: EventEnvelope
    base_quantity: Decimal | None
    display_time_ns: int
    delivery: int
    event_index: int


class AdmittedTradeHistory:
    """Owner-only native trade retention and shared receive-clock projection.

    The first admitted receive pair anchors each source clock. Later wall-clock
    corrections are disclosed, not used to move already published trades.
    Replay resets/rebuilds this observer with exactly the admitted prefix.
    """

    observer_id = "admitted_market_trades"
    version = "1"

    def __init__(
        self, streams: tuple[StreamSpec, ...], engine: AnalyticsEngine | None = None
    ) -> None:
        self.engine = engine
        self.specs = {spec.stream_id: spec for spec in streams}
        self.reset()

    def reset(self) -> None:
        self.trades: deque[AdmittedTrade] = deque()
        self.sizes: deque[int] = deque()
        self.bytes = 0
        self.origins: OrderedDict[ClockScope, tuple[int, int]] = OrderedDict()
        self.scope: ClockScope | None = None
        self.frontier: int | None = None
        self.highwater = 0
        self.reasons: set[str] = set()
        self.truncated = False
        self.admitted: dict[TradeCursor, AdmittedTrade] = {}
        self.floor_cursor: TradeCursor = (0, -1)
        self.stream_sources: dict[str, SourceIdentity] = {}
        self.stream_floors: dict[str, TradeCursor] = {}

    def checkpoint(self) -> bytes:
        state: TradeHistoryState = (
            self.version,
            tuple(self.specs.values()),
            tuple(
                (
                    item.event,
                    item.base_quantity,
                    item.display_time_ns,
                    item.delivery,
                    item.event_index,
                    size,
                )
                for item, size in zip(self.trades, self.sizes)
            ),
            self.bytes,
            tuple(self.origins.items()),
            self.scope,
            self.frontier,
            self.highwater,
            tuple(sorted(self.reasons)),
            self.truncated,
            self.floor_cursor,
            tuple(sorted(self.stream_sources.items())),
            tuple(sorted(self.stream_floors.items())),
        )
        # Preserve Decimal spelling: retention sizes include the exact event repr.
        return dumps(state, canonical=False, decimal_policy=DERIVED_DECIMAL_POLICY)

    def restore(self, state: bytes) -> None:
        checkpoint: TradeHistoryState = loads(
            state, TradeHistoryState, decimal_policy=DERIVED_DECIMAL_POLICY
        )
        (
            version,
            specs,
            rows,
            byte_count,
            origins,
            scope,
            frontier,
            highwater,
            reasons,
            truncated,
            floor,
            sources,
            stream_floors,
        ) = checkpoint
        origin_map = OrderedDict(origins)
        if (
            version != self.version
            or specs != tuple(self.specs.values())
            or len(rows) > MAX_TRADE_ROWS
            or not 0 <= byte_count <= MAX_TRADE_BYTES
            or len(origins) > MAX_CLOCK_ORIGINS
            or len(origin_map) != len(origins)
            or any(min(origin) < 0 for _, origin in origins)
            or (scope is not None and scope not in origin_map)
            or (frontier is not None and (frontier < 0 or scope is None))
            or highwater < 0
            or not 0 <= floor[0] <= highwater
            or floor[1] < -1
            or len(dict(sources)) != len(sources)
            or len(dict(stream_floors)) != len(stream_floors)
            or any(stream not in self.specs for stream, _ in sources)
            or any(
                stream not in self.specs
                or not 0 <= cursor[0] <= highwater
                or cursor[1] < -1
                for stream, cursor in stream_floors
            )
        ):
            raise ValueError("incompatible admitted trade checkpoint")
        trades: deque[AdmittedTrade] = deque()
        sizes: deque[int] = deque()
        admitted: dict[TradeCursor, AdmittedTrade] = {}
        previous = floor
        for event, base, stamp, delivery, index, size in rows:
            item = AdmittedTrade(event, base, stamp, delivery, index)
            cursor = (delivery, index)
            spec = self.specs.get(event.stream_id)
            origin = origin_map.get((event.session_id, event.clock_id))
            if (
                spec is None
                or spec.key != event.key
                or not isinstance(event.payload, Trade)
                or not previous < cursor
                or not 1 <= delivery <= highwater
                or index < 0
                or stamp < 0
                or origin is None
                or event.receive_mono_ns < origin[0]
                or stamp != origin[1] + event.receive_mono_ns - origin[0]
                or size != len(repr(item).encode("utf-8"))
                or (base is not None and (not base.is_finite() or base <= 0))
            ):
                raise ValueError("invalid admitted trade checkpoint row")
            trades.append(item)
            sizes.append(size)
            admitted[cursor] = item
            previous = cursor
        if sum(sizes) != byte_count:
            raise ValueError("invalid admitted trade checkpoint accounting")
        self.trades, self.sizes, self.admitted = trades, sizes, admitted
        self.bytes = byte_count
        self.origins = origin_map
        self.scope, self.frontier, self.highwater = scope, frontier, highwater
        self.reasons, self.truncated = set(reasons), truncated
        self.floor_cursor = floor
        self.stream_sources = dict(sources)
        self.stream_floors = dict(stream_floors)

    def _clear_streams(self, streams: tuple[str, ...], cursor: TradeCursor) -> None:
        targets = set(streams)
        for stream in targets:
            if stream in self.specs:
                self.stream_floors[stream] = cursor
        retained: deque[AdmittedTrade] = deque()
        sizes: deque[int] = deque()
        for item, size in zip(self.trades, self.sizes):
            if item.event.stream_id in targets:
                self.admitted.pop((item.delivery, item.event_index))
                self.bytes -= size
            else:
                retained.append(item)
                sizes.append(size)
        self.trades, self.sizes = retained, sizes

    def display_time(self, event: EventEnvelope) -> int | None:
        origin = self.origins.get((event.session_id, event.clock_id))
        if origin is None or event.receive_mono_ns < origin[0]:
            return None
        return origin[1] + event.receive_mono_ns - origin[0]

    @property
    def now_ns(self) -> int | None:
        origin = self.origins.get(self.scope) if self.scope is not None else None
        if origin is None or self.frontier is None or self.frontier < origin[0]:
            return None
        return origin[1] + self.frontier - origin[0]

    def on_batch(
        self, batch: RecordedBatch, books: tuple[Book, ...], clock: ReplayClock
    ) -> None:
        if batch.delivery_index <= self.highwater:
            raise ValueError("native trade delivery must advance; reset before seek")
        self.highwater = batch.delivery_index
        admitted = (
            {
                (item.stream_id, item.asof): item
                for item in self.engine.admitted_tape(batch)
            }
            if self.engine is not None
            else {}
        )
        if self.engine is not None and not self.engine.admitted_tape_complete(batch):
            self.reasons.add("admitted_batch_truncated")
        with localcontext(DECIMAL_CONTEXT):
            for index, event in enumerate(batch.events):
                spec = self.specs.get(event.stream_id)
                if spec is None:
                    continue
                identity = (
                    event.session_id,
                    event.segment_id,
                    event.clock_id,
                    event.connection_epoch,
                )
                previous = self.stream_sources.get(event.stream_id)
                if previous is not None and previous != identity:
                    self.reasons.update(
                        ("source_epoch_changed", "session_discontinuities")
                    )
                    self._clear_streams(
                        (str(event.stream_id),), (batch.delivery_index, index)
                    )
                self.stream_sources[event.stream_id] = identity
                scope = (event.session_id, event.clock_id)
                if scope != self.scope:
                    if self.scope is not None:
                        self.reasons.add("source_clock_changed")
                        # Incomparable source clocks must never share candles.
                        self.trades.clear()
                        self.sizes.clear()
                        self.bytes = 0
                        self.admitted.clear()
                        self.floor_cursor = (batch.delivery_index, index - 1)
                    self.scope = scope
                    self.frontier = None
                if scope not in self.origins:
                    self.origins[scope] = (event.receive_mono_ns, event.receive_wall_ns)
                    while len(self.origins) > MAX_CLOCK_ORIGINS:
                        self.origins.popitem(last=False)
                        self.reasons.add("receive_clock_history_truncated")
                stamp = self.display_time(event)
                if stamp != event.receive_wall_ns:
                    self.reasons.add("receive_clock_discontinuity")
                if self.frontier is not None and event.receive_mono_ns < self.frontier:
                    self.reasons.add("nonadvancing_receive_pair")
                self.frontier = max(self.frontier or 0, event.receive_mono_ns)
                payload = event.payload
                if isinstance(payload, Gap) or (
                    isinstance(payload, Control)
                    and payload.kind
                    in ("reset", "disconnect", "source_change", "rejection")
                ):
                    self.reasons.add("session_discontinuities")
                    self._clear_streams(
                        (str(event.stream_id), *payload.affected_streams)
                        if isinstance(payload, Gap)
                        else (str(event.stream_id),),
                        (batch.delivery_index, index),
                    )
                if not isinstance(payload, Trade):
                    continue
                spec = self.specs.get(event.stream_id)
                if spec is None or spec.key != event.key:
                    self.reasons.add("unknown_trade_stream")
                    continue
                if stamp is None or stamp < 0:
                    self.reasons.add("unprojectable_trade_clock")
                    continue
                item_print = admitted.get((str(event.stream_id), event.ref()))
                if item_print is None:
                    self.reasons.add("unadmitted_trades_excluded")
                    continue
                base = item_print.base_quantity
                item = AdmittedTrade(event, base, stamp, batch.delivery_index, index)
                size = len(repr(item).encode("utf-8"))
                while self.trades and (
                    len(self.trades) >= MAX_TRADE_ROWS
                    or self.bytes + size > MAX_TRADE_BYTES
                ):
                    removed = self.trades.popleft()
                    self.admitted.pop((removed.delivery, removed.event_index), None)
                    self.floor_cursor = (removed.delivery, removed.event_index)
                    self.bytes -= self.sizes.popleft()
                    self.truncated = True
                if size <= MAX_TRADE_BYTES:
                    self.trades.append(item)
                    self.sizes.append(size)
                    self.bytes += size
                    self.admitted[(batch.delivery_index, index)] = item
                else:
                    self.truncated = True


def market_data(session, request):
    # Called only on the session owner, with its lifecycle locks held.
    from .runtime import _book_presentation, _live_cohort, dto

    highwater = (
        session.replay.cursor.delivery_index if session.replay else session.delivery
    )
    floor = 1 if session.replay else session.history_floor
    delivery, offset = int(request.after_delivery), request.after_event
    floor_event = -1 if session.replay else session.history_floor_event
    floor_cursor = max((floor - 1, floor_event), session.trade_history.floor_cursor)
    gap = delivery > highwater or (delivery, offset) < floor_cursor
    if gap:
        delivery, offset = floor_cursor
    cursor = {"delivery": str(delivery), "event": offset}
    selected = next(
        (spec.instrument for spec in session.streams if spec.key == session.key), None
    )
    instruments = {}
    for spec in session.streams:
        instrument = spec.instrument
        if (
            selected is None
            or instrument.base.asset_id != selected.base.asset_id
            or instrument.market_type != selected.market_type
        ):
            continue
        identity = str(instrument.instrument_id)
        # The display catalogue describes books; trade leases can retain an
        # older pinned version and must not replace the book's descriptor.
        if spec.key.channel == "book" or identity not in instruments:
            instruments[identity] = instrument
    if selected is not None:
        instruments = {
            str(instrument.instrument_id): instrument
            for instrument in _live_cohort(selected, tuple(instruments.values()))
        }
    specs = {spec.stream_id: spec for spec in session.streams}
    book_instruments = {
        (spec.key, spec.instrument.metadata_version): spec.instrument
        for spec in session.streams
        if spec.key.channel == "book"
    }
    now = session._now()
    books = []
    with localcontext(DECIMAL_CONTEXT):
        for book in session.replay.books() if session.replay else session.live_books:
            if str(book.key.instrument_id) not in instruments:
                continue
            instrument = book_instruments.get((book.key, book.metadata_version))
            if instrument is None:
                continue
            presented = _book_presentation(book)
            for side in ("bids", "asks"):
                for level, native in zip(presented[side], getattr(book, side)):
                    base, quote = _valuation(instrument, native.price, native.quantity)
                    level.update(base_quantity=dto(base), quote_notional=dto(quote))
            presented["eligibility"] = (
                dto(evaluate(book, instrument, "display", now, EligibilityPolicy()))
                if now is not None
                else None
            )
            books.append(presented)

        start = max(floor, delivery, 1)
        batches = (
            session.reader.iter_batches(start_delivery=start, stop_delivery=highwater)
            if session.replay and start <= highwater
            else (
                batch
                for batch in session.history
                if start <= batch.delivery_index <= highwater
            )
        )
        trades: list[dict[str, object]] = []
        boundaries: list[dict[str, object]] = []
        scanned = 0
        batch_count = 0
        response_bytes = 0
        has_more = False
        try:
            for batch in batches:
                batch_count += 1
                if batch_count > MAX_SCAN_BATCHES:
                    has_more = True
                    break
                if batch.delivery_index == delivery and offset >= len(batch.events):
                    raise ValueError("market cursor event is outside its delivery")
                for index, event in enumerate(batch.events):
                    if batch.delivery_index == delivery and index <= offset:
                        continue
                    if (
                        scanned == MAX_SCAN_EVENTS
                        or len(trades) + len(boundaries) >= request.limit
                    ):
                        has_more = True
                        break
                    scanned += 1
                    next_cursor = {
                        "delivery": str(batch.delivery_index),
                        "event": index,
                    }
                    spec = specs.get(event.stream_id)
                    if spec is None:
                        cursor = next_cursor
                        continue
                    payload = event.payload
                    event_boundaries = []
                    if isinstance(payload, Gap) or (
                        isinstance(payload, Control)
                        and payload.kind
                        in ("reset", "disconnect", "source_change", "rejection")
                    ):
                        targets = (
                            dict.fromkeys((event.stream_id, *payload.affected_streams))
                            if isinstance(payload, Gap)
                            else (event.stream_id,)
                        )
                        for target in targets:
                            affected = specs.get(target)
                            if (
                                affected is None
                                or affected.key.channel not in ("trade", "trades")
                                or str(affected.instrument.instrument_id)
                                not in instruments
                            ):
                                continue
                            event_boundaries.append(
                                {
                                    "instrument_id": str(
                                        affected.instrument.instrument_id
                                    ),
                                    "stream_id": str(target),
                                    "cursor": next_cursor,
                                    "asof": dto(event.ref()),
                                    "display_time_ns": (
                                        str(stamp)
                                        if (
                                            stamp := session.trade_history.display_time(
                                                event
                                            )
                                        )
                                        is not None
                                        else None
                                    ),
                                    "kind": (
                                        "gap"
                                        if isinstance(payload, Gap)
                                        else payload.kind
                                    ),
                                    "reason": payload.reason_code,
                                }
                            )
                    presented = None
                    admitted = session.trade_history.admitted.get(
                        (batch.delivery_index, index)
                    )
                    if (
                        isinstance(payload, Trade)
                        and str(event.key.instrument_id) in instruments
                        and admitted is not None
                    ):
                        base, quote = (
                            _valuation(spec.instrument, payload.price, payload.quantity)
                            if payload.quantity_unit == spec.instrument.quantity_unit
                            else (None, None)
                        )
                        presented = {
                            **dto(
                                TapePrint(
                                    payload,
                                    event.ref(),
                                    str(event.stream_id),
                                    base,
                                    quote,
                                )
                            ),
                            "instrument_id": str(event.key.instrument_id),
                            "display_time_ns": str(admitted.display_time_ns),
                            "cursor": next_cursor,
                            # Exactly the native analytics continuity tuple. Consumers
                            # compare it per stream across pages; heartbeats are not
                            # discontinuities and need no display record.
                            "source_identity": {
                                "session_id": str(event.session_id),
                                "segment_id": str(event.segment_id),
                                "clock_id": event.clock_id,
                                "connection_epoch": str(event.connection_epoch),
                            },
                        }
                    size = sum(len(repr(item)) for item in event_boundaries)
                    if presented is not None:
                        size += len(repr(presented))
                    # A fan-out gap is atomic at one canonical cursor. Never split
                    # its affected streams across pages and silently lose siblings.
                    count = len(event_boundaries) + (presented is not None)
                    if (trades or boundaries) and (
                        len(trades) + len(boundaries) + count > request.limit
                        or response_bytes + size > 2 * 1024 * 1024
                    ):
                        has_more = True
                        break
                    response_bytes += size
                    cursor = next_cursor
                    boundaries.extend(event_boundaries)
                    if presented is not None:
                        trades.append(presented)
                if has_more:
                    break
                cursor = {
                    "delivery": str(batch.delivery_index),
                    "event": len(batch.events) - 1,
                }
        finally:
            close = getattr(batches, "close", None)
            if close is not None:
                close()
    return {
        "version": "1",
        "session_id": session.id,
        "generation": str(session.generation),
        "cursor": cursor,
        "highwater": str(highwater),
        "gap": gap,
        "has_more": has_more,
        "books": books,
        "instruments": dto(tuple(instruments.values())),
        "trades": trades,
        "boundaries": boundaries,
        "now": dto(now),
        "source_identity": session.source_identity,
        "time_basis": "receive_utc",
        "display_time_reasons": sorted(session.trade_history.reasons),
    }
