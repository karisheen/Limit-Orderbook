"""Bounded display transport over owner-completed canonical deliveries.

Trades and trade-stream boundaries share an exclusive delivery/event cursor.
The page limit counts both; one fan-out boundary group remains atomic even when
it exceeds that limit. Consumers merge the arrays by cursor and compare each
trade's source_identity per stream to detect native source-epoch transitions.
The response gap flag only describes cursor retention loss, not stream health.
"""

from decimal import localcontext

from .analytics import TapePrint, _valuation
from .events import Control, Gap, Trade
from .precision import DECIMAL_CONTEXT
from .quality import EligibilityPolicy, evaluate


MAX_SCAN_EVENTS = 10_000
MAX_SCAN_BATCHES = 10_000


def market_data(session, request):
    # Called only on the session owner, with its lifecycle locks held.
    from .runtime import _book_presentation, _live_cohort, dto

    highwater = (
        session.replay.cursor.delivery_index if session.replay else session.delivery
    )
    floor = 1 if session.replay else session.history_floor
    delivery, offset = int(request.after_delivery), request.after_event
    floor_event = -1 if session.replay else session.history_floor_event
    gap = delivery > highwater or (delivery, offset) < (floor - 1, floor_event)
    if gap:
        delivery, offset = floor - 1, floor_event
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
                                    "kind": (
                                        "gap"
                                        if isinstance(payload, Gap)
                                        else payload.kind
                                    ),
                                    "reason": payload.reason_code,
                                }
                            )
                    presented = None
                    if (
                        isinstance(payload, Trade)
                        and str(event.key.instrument_id) in instruments
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
    }
