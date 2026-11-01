"""Distinct Binance spot protocols and shared strict spot-field parsing helpers."""

from dataclasses import replace
from hashlib import sha256
import json
from time import time_ns
from urllib.parse import quote

from ..book import BookReducer, Reduction, contiguous_sequence
from ..events import (
    BookUpdate,
    Control,
    EventEnvelope,
    LevelChange,
    RawFrame,
    SequenceInfo,
    SubscriptionKey,
    Trade,
)
from ..instruments import (
    AssetId,
    AssetIdentity,
    Instrument,
    InstrumentId,
    MetadataSnapshot,
    OrderConstraints,
)
from ..precision import decimal, exact_json_loads, nonnegative_int, text
from ..quality import Capabilities
from .base import DecodeError, DecodedEvent, DecodeResult, TransportRequest

CANONICAL_ASSETS = (
    "BTC",
    "ETH",
    "SOL",
    "XRP",
    "DOGE",
    "ADA",
    "LTC",
    "LINK",
    "AVAX",
    "DOT",
)


def object_field(value):
    if not isinstance(value, dict):
        raise ValueError("expected object")
    return value


def array_field(value):
    if not isinstance(value, list):
        raise ValueError("expected array")
    return value


def integer_field(value):
    if isinstance(value, str):
        if not value.isascii() or not value.isdigit() or len(value) > 38:
            raise ValueError("expected unsigned integer text")
        value = int(value)
    return nonnegative_int(value)


def asset_identity(venue, symbol):
    symbol = text(symbol)
    canonical = symbol in CANONICAL_ASSETS + ("USD", "USDT", "USDC")
    return AssetIdentity(
        AssetId(symbol if canonical else f"{venue}:{symbol}"),
        symbol,
        native_token_id=symbol,
    )


def metadata_snapshot(source, raw, fetched, instruments):
    instruments = tuple(instruments)
    if len({item.instrument_id for item in instruments}) != len(instruments):
        raise ValueError("duplicate metadata instrument")
    priority = {asset: index for index, asset in enumerate(CANONICAL_ASSETS)}
    return MetadataSnapshot(
        source,
        fetched,
        sha256(raw).hexdigest(),
        tuple(
            sorted(
                instruments,
                key=lambda item: (
                    priority.get(item.base.ticker, 99),
                    item.native_symbol,
                ),
            )
        ),
    )


def validate_key(instrument, key, venue, api_family):
    if (instrument.venue, instrument.api_family, instrument.instrument_id) != (
        key.venue,
        key.api_family,
        key.instrument_id,
    ) or (key.venue, key.api_family) != (venue, api_family):
        raise ValueError("wrong_subscription")
    if (
        instrument.market_type != "spot"
        or key.channel not in ("book", "trades")
        or key.aggregation
    ):
        raise ValueError("unsupported_subscription")
    if instrument.status != "active":
        raise ValueError(f"product_{instrument.status}")


def level_changes(bids, asks, instrument):
    changes = []
    for side, rows in (("bid", bids), ("ask", asks)):
        for row in array_field(rows):
            if not isinstance(row, list) or len(row) != 2:
                raise ValueError("invalid price/quantity pair")
            change = LevelChange(
                side, decimal(row[0], positive=True), decimal(row[1], nonnegative=True)
            )
            instrument.validate_price(change.price)
            instrument.validate_quantity(change.quantity)
            changes.append(change)
    return tuple(changes)


def decode_failure(frame, exc):
    return DecodeResult(
        error=DecodeError("invalid_message", frame.payload_sha256, str(exc)[:4096])
    )


def optional_decimal(row, field):
    return (
        decimal(row[field], nonnegative=True)
        if field in row and row[field] is not None
        else None
    )


def book_sequence(previous, incoming, snapshot):
    # Acks and unrelated unsequenced controls do not consume book update IDs.
    return (
        "accept"
        if incoming is None
        else contiguous_sequence(previous, incoming, snapshot)
    )


class _BinanceDepthReducer(BookReducer):
    """Keep only the price region completely observed by the limited bootstrap.

    A deeper changed level does not reveal unchanged levels beyond the initial
    REST boundary. Raw/normalized events retain it, but a usable Book must not
    imply that the intervening unknown region contains zero liquidity.
    """

    def _bounds(self):
        if not self.protocol_state:
            return None, None
        state = exact_json_loads(self.protocol_state, max_bytes=4096)
        if (
            not isinstance(state, dict)
            or set(state) != {"version", "bid_floor", "ask_ceiling"}
            or type(state["version"]) is not int
            or state["version"] != 1
        ):
            raise ValueError("invalid Binance depth boundary state")
        values = tuple(
            None if state[name] is None else decimal(state[name], positive=True)
            for name in ("bid_floor", "ask_ceiling")
        )
        for value in values:
            if value is not None:
                self.instrument.validate_price(value)
        return values

    def _levels(self, bids, asks):
        bid_levels, ask_levels = super()._levels(bids, asks)
        floor, ceiling = self._bounds()
        if floor is not None and bid_levels and bid_levels[-1].price < floor:
            bid_levels = tuple(level for level in bid_levels if level.price >= floor)
        if ceiling is not None and ask_levels and ask_levels[-1].price > ceiling:
            ask_levels = tuple(level for level in ask_levels if level.price <= ceiling)
        if (floor is not None and not bid_levels) or (
            ceiling is not None and not ask_levels
        ):
            raise ValueError("known_depth_exhausted")
        return bid_levels, ask_levels

    def restore(self, checkpoint):
        # Core restores new maps, never mutates the previous ones. Retain those
        # references so adapter-state rejection remains atomic without copying L2.
        previous = self.__dict__.copy()
        try:
            super().restore(checkpoint)
            floor, ceiling = self._bounds()
            if self.initialized and self.quality.source_mode == "ws_delta":
                if not self.protocol_state:
                    raise ValueError("missing Binance depth boundary state")
                if (
                    floor is not None
                    and self._bid_levels
                    and self._bid_levels[-1].price < floor
                    or ceiling is not None
                    and self._ask_levels
                    and self._ask_levels[-1].price > ceiling
                    or self.quality.coverage.full_depth
                    != (floor is None and ceiling is None)
                ):
                    raise ValueError("inconsistent Binance depth boundary state")
        except Exception:
            self.__dict__.clear()
            self.__dict__.update(previous)
            raise


class BinanceGlobalReducer(_BinanceDepthReducer):
    """Receive-order journal with an atomic snapshot + buffered-diff bootstrap.

    The private candidate replays exchange order; original envelopes are never
    renumbered. The committed receive cursor remains the snapshot's later ordinal.
    """

    MAX_BUFFER_EVENTS = 4096
    MAX_BUFFER_CHANGES = 200_000

    def apply(
        self, events: tuple[EventEnvelope, ...], *, synchronized: bool = True
    ) -> Reduction:
        events = tuple(events)
        if not events:
            return Reduction(self.book)
        books = [event for event in events if isinstance(event.payload, BookUpdate)]
        if not books or self.key.channel != "book":
            return super().apply(events, synchronized=synchronized)
        if len(events) != 1:
            return self._reject(events, "invalid_binance_transaction", invalidate=True)
        event = events[0]
        # Let core validate all identity, epoch, stream and receive-order fences
        # using an unsequenced control, without applying a premature delta.
        probe = replace(event, payload=Control("heartbeat"), sequence=None)
        fence = super().apply((probe,), synchronized=False)
        if fence.recovery is not None:
            return replace(fence, events=events)
        self._last_event = event
        payload = event.payload
        assert isinstance(payload, BookUpdate)
        if payload.kind == "delta" and not self.initialized:
            if (
                len(self.buffered_events) >= self.MAX_BUFFER_EVENTS
                or sum(
                    len(item.payload.changes)
                    for item in self.buffered_events
                    if isinstance(item.payload, BookUpdate)
                )
                + len(payload.changes)
                > self.MAX_BUFFER_CHANGES
            ):
                self.buffered_events = ()
                return self._reject(
                    events, "bootstrap_buffer_overflow", invalidate=True
                )
            self.buffered_events += events
            self._last_event = event
            return Reduction(None, events)
        # The probe already advanced the cursor; actual core application starts
        # from the previous receive position, not from a fabricated ordinal.
        self._last_order = None
        self._last_event = None
        if payload.kind == "delta":
            result = super().apply(events, synchronized=synchronized)
            if result.recovery is not None:
                self.buffered_events = ()
            return result
        if event.source_mode == "rest_snapshot":
            self.buffered_events = ()
            return super().apply(events, synchronized=synchronized)
        candidate = _BinanceDepthReducer(
            self.instrument,
            self.key,
            capabilities=self.capabilities,
            epoch=self.epoch,
            segment_id=self.segment_id,
            max_levels=self.max_levels,
            sequence_validator=book_sequence,
            strategy_id=self.strategy_id,
            adapter_revision=self.adapter_revision,
            config_hash=self.config_hash,
        )
        prices = {
            side: tuple(
                change.price for change in payload.changes if change.side == side
            )
            for side in ("bid", "ask")
        }
        boundaries = {
            "version": 1,
            "bid_floor": str(min(prices["bid"]))
            if len(prices["bid"]) == 5000
            else None,
            "ask_ceiling": str(max(prices["ask"]))
            if len(prices["ask"]) == 5000
            else None,
        }
        candidate.protocol_state = json.dumps(
            boundaries, separators=(",", ":")
        ).encode()
        candidate.quality = replace(
            candidate.quality,
            coverage=replace(
                candidate.quality.coverage,
                full_depth=boundaries["bid_floor"] is None
                and boundaries["ask_ceiling"] is None,
            ),
        )
        result = candidate.apply(events, synchronized=False)
        if result.recovery is not None:
            self._last_event, self._last_order = event, event.order_key
            return self._reject(events, result.recovery.reason, invalidate=True)
        if self.buffered_events:
            first_sequence = self.buffered_events[0].sequence
            if (
                first_sequence is None
                or first_sequence.first is None
                or candidate.sequence is None
                or candidate.sequence.last is None
            ):
                self._last_event, self._last_order = event, event.order_key
                return self._reject(events, "sequence_missing", invalidate=True)
            # Current spot bootstrap step 4 requires retry when the snapshot
            # predates the first buffered U, even if it is only one ID behind.
            if int(candidate.sequence.last) < int(first_sequence.first):
                self._last_event, self._last_order = event, event.order_key
                return self._reject(events, "sequence_gap", invalidate=True)
        bridged = False
        for buffered in self.buffered_events:
            if (
                buffered.sequence is None
                or buffered.sequence.last is None
                or candidate.sequence is None
                or candidate.sequence.last is None
            ):
                self._last_event, self._last_order = event, event.order_key
                return self._reject(events, "sequence_missing", invalidate=True)
            if int(buffered.sequence.last) <= int(candidate.sequence.last):
                continue
            candidate._last_event = None
            candidate._last_order = None
            result = candidate.apply((buffered,), synchronized=True)
            if result.recovery is not None:
                # Retain buffer: a newer snapshot may bridge it on retry.
                self._last_event, self._last_order = event, event.order_key
                return self._reject(events, result.recovery.reason, invalidate=True)
            bridged = True
        candidate._last_event, candidate._last_order = event, event.order_key
        candidate.quality = replace(
            candidate.quality,
            last_transport_mono_ns=event.receive_mono_ns,
            last_book_observation_mono_ns=event.receive_mono_ns,
            last_healthy_observation_mono_ns=event.receive_mono_ns if bridged else None,
            continuous_since_ordinal=event.ingest_ordinal if bridged else None,
        )
        if candidate.book is not None:
            candidate.book = replace(
                candidate.book, asof=event.ref(), quality=candidate.quality
            )
        self.restore(candidate.checkpoint())
        return Reduction(self.book if self.synchronized else None, events, changed=True)


class _BinanceAdapter:
    venue = ""
    api_family = "spot-v3"
    rest_host = ""
    stream_host = ""
    partial = False

    async def discover(self) -> MetadataSnapshot:
        from ..transport import fetch_public

        request = TransportRequest("GET", f"{self.rest_host}/api/v3/exchangeInfo")
        raw = await fetch_public(request)
        return self.parse_metadata(raw, fetch_wall_ns=time_ns())

    def parse_metadata(self, raw: bytes, *, fetch_wall_ns: int = 0) -> MetadataSnapshot:
        data = object_field(exact_json_loads(raw))
        if "code" in data:
            raise ValueError(f"metadata endpoint error: {data['code']}")
        version = f"{sha256(raw).hexdigest()}:{fetch_wall_ns}"
        source = f"{self.rest_host}/api/v3/exchangeInfo"
        instruments = []
        for value in array_field(data["symbols"]):
            row = object_field(value)
            if row.get("isSpotTradingAllowed") is False:
                continue
            filters = {}
            for value in array_field(row["filters"]):
                item = object_field(value)
                name = text(item["filterType"])
                if name in filters:
                    raise ValueError("duplicate metadata filter")
                filters[name] = item
            price, lot = filters["PRICE_FILTER"], filters["LOT_SIZE"]
            notional = filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {}))
            base, currency = (
                asset_identity(self.venue, row["baseAsset"]),
                asset_identity(self.venue, row["quoteAsset"]),
            )
            symbol = text(row["symbol"])
            status = {
                "TRADING": "active",
                "BREAK": "suspended",
                "HALT": "suspended",
                "END_OF_DAY": "suspended",
                "PRE_TRADING": "unavailable",
                "POST_TRADING": "unavailable",
            }.get(text(row["status"]), "unknown")
            instruments.append(
                Instrument(
                    InstrumentId(f"{self.venue}:{self.api_family}:spot:{symbol}"),
                    version,
                    self.venue,
                    self.api_family,
                    symbol,
                    base,
                    currency,
                    decimal(price["tickSize"], positive=True),
                    decimal(lot["stepSize"], positive=True),
                    constraints=OrderConstraints(
                        min_base=optional_decimal(lot, "minQty"),
                        max_base=optional_decimal(lot, "maxQty"),
                        min_notional=optional_decimal(notional, "minNotional"),
                        max_notional=optional_decimal(notional, "maxNotional"),
                        min_notional_currency=currency.asset_id,
                    ),
                    status=status,
                    metadata_asof_wall_ns=fetch_wall_ns,
                    metadata_source=source,
                )
            )
        return metadata_snapshot(source, raw, fetch_wall_ns, instruments)

    def _validate(self, instrument, key):
        validate_key(instrument, key, self.venue, self.api_family)
        if key.channel == "book" and (
            key.depth not in (5, 10, 20)
            if self.partial
            else key.depth not in (None, 5000)
        ):
            raise ValueError("unsupported reconstruction depth")

    def capabilities(
        self, instrument: Instrument, key: SubscriptionKey
    ) -> Capabilities:
        self._validate(instrument, key)
        book = key.channel == "book"
        return Capabilities(
            snapshot=book,
            deltas=book and not self.partial,
            trades=not book,
            sequence_scope=f"{instrument.instrument_id}:depth"
            if book and not self.partial
            else None,
            exact_event_flow=book and not self.partial,
        )

    def _stream(self, instrument, key):
        suffix = (
            "trade"
            if key.channel == "trades"
            else f"depth{key.depth}@100ms"
            if self.partial
            else "depth@100ms"
        )
        return f"{instrument.native_symbol.lower()}@{suffix}"

    def ws_endpoint(self, instrument: Instrument, key: SubscriptionKey) -> str:
        self._validate(instrument, key)
        # Combined stream supplies a symbol even on US partial snapshots.
        return f"{self.stream_host}/stream"

    def subscription_requests(
        self, instrument: Instrument, key: SubscriptionKey
    ) -> tuple[TransportRequest, ...]:
        endpoint = self.ws_endpoint(instrument, key)
        body = json.dumps(
            {"method": "SUBSCRIBE", "params": [self._stream(instrument, key)], "id": 1},
            separators=(",", ":"),
        ).encode()
        return (TransportRequest("WS", endpoint, body),)

    def snapshot_request(
        self, instrument: Instrument, key: SubscriptionKey
    ) -> TransportRequest:
        self._validate(instrument, key)
        symbol = quote(instrument.native_symbol, safe="")
        path = (
            f"trades?symbol={symbol}&limit=1000"
            if key.channel == "trades"
            else f"depth?symbol={symbol}&limit={key.depth if self.partial else 5000}"
        )
        return TransportRequest("GET", f"{self.rest_host}/api/v3/{path}")

    def decode(
        self, frame: RawFrame, instrument: Instrument, key: SubscriptionKey
    ) -> DecodeResult:
        try:
            self._validate(instrument, key)
            data = exact_json_loads(frame.payload)
            rest = (
                frame.endpoint_id == self.snapshot_request(instrument, key).endpoint_id
            )
            if frame.transport != ("rest" if rest else "ws"):
                raise ValueError("wrong_transport")
            if not rest and frame.endpoint_id != self.ws_endpoint(instrument, key):
                raise ValueError("wrong_endpoint")
            if rest and key.channel == "trades":
                events = []
                for value in array_field(data):
                    row = object_field(value)
                    events.append(self._trade(row, instrument, history=True))
                return DecodeResult(tuple(events))
            data = object_field(data)
            if "code" in data:
                raise ValueError(f"endpoint_error:{data['code']}")
            if not rest and "result" in data:
                if data["result"] is not None or data.get("id") != 1:
                    raise ValueError("invalid subscription acknowledgement")
                return DecodeResult(controls=(Control("ack"),))
            if not rest:
                if data.get("stream") == "!serverShutdown":
                    return DecodeResult(
                        controls=(Control("disconnect", "server_shutdown"),)
                    )
                if data.get("stream") != self._stream(instrument, key):
                    raise ValueError("wrong_stream")
                data = object_field(data["data"])
            if "s" in data and data["s"] != instrument.native_symbol:
                raise ValueError("wrong_symbol")
            if key.channel == "trades":
                if (
                    data.get("e") != "trade"
                    or data.get("s") != instrument.native_symbol
                ):
                    raise ValueError("wrong_trade_schema")
                return DecodeResult((self._trade(data, instrument),))
            snapshot = rest or self.partial
            if snapshot:
                update_id = integer_field(data["lastUpdateId"])
                sequence = SequenceInfo(
                    f"{instrument.instrument_id}:depth", last=str(update_id)
                )
                changes = level_changes(data["bids"], data["asks"], instrument)
                limit = key.depth if self.partial else 5000
                if limit is None:
                    raise ValueError("missing subscription depth")
                if any(
                    sum(change.side == side for change in changes) > limit
                    for side in ("bid", "ask")
                ):
                    raise ValueError("snapshot_depth_exceeded")
                event = DecodedEvent(
                    BookUpdate("snapshot", changes, key.depth), sequence
                )
            else:
                if (
                    data.get("e") != "depthUpdate"
                    or data.get("s") != instrument.native_symbol
                ):
                    raise ValueError("wrong_depth_schema")
                first, last = integer_field(data["U"]), integer_field(data["u"])
                if first > last:
                    raise ValueError("inverted_update_range")
                event = DecodedEvent(
                    BookUpdate(
                        "delta",
                        level_changes(data["b"], data["a"], instrument),
                        key.depth,
                    ),
                    SequenceInfo(
                        f"{instrument.instrument_id}:depth", str(first), str(last)
                    ),
                    exchange_event_wall_ns=integer_field(data["E"]) * 1_000_000,
                    exchange_time_kind="event",
                )
            return DecodeResult((event,))
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            return decode_failure(frame, exc)

    def _trade(self, row, instrument, *, history=False):
        trade_id = str(integer_field(row["id" if history else "t"]))
        maker = row.get("isBuyerMaker" if history else "m")
        if maker is not None and type(maker) is not bool:
            raise ValueError("invalid maker flag")
        price, size = (
            decimal(row["price" if history else "p"], positive=True),
            decimal(row["qty" if history else "q"], positive=True),
        )
        instrument.validate_price(price)
        instrument.validate_quantity(size)
        trade = Trade(
            trade_id,
            f"{instrument.instrument_id}:trade:{trade_id}",
            price,
            size,
            taker_side="unknown" if maker is None else "sell" if maker else "buy",
            side_provenance="unknown" if maker is None else "buyer_is_maker",
            is_subscription_history=history,
        )
        return DecodedEvent(
            trade,
            exchange_event_wall_ns=integer_field(row["time" if history else "T"])
            * 1_000_000,
            exchange_time_kind="trade",
        )

    def new_reducer(self, instrument: Instrument, key: SubscriptionKey) -> BookReducer:
        caps = self.capabilities(instrument, key)
        if not self.partial and key.channel == "book":
            return BinanceGlobalReducer(
                instrument,
                key,
                capabilities=caps,
                sequence_validator=book_sequence,
                strategy_id="binance-global-bridge-frontier-v2",
                adapter_revision="binance-v2",
            )
        return BookReducer(
            instrument,
            key,
            capabilities=caps,
            strategy_id=f"{self.venue}-snapshot-v1",
            adapter_revision="binance-v1",
        )


class BinanceGlobalAdapter(_BinanceAdapter):
    venue = "binance-global"
    rest_host = "https://data-api.binance.vision"
    stream_host = "wss://data-stream.binance.vision:443"


class BinanceUSAdapter(_BinanceAdapter):
    venue = "binance-us"
    rest_host = "https://api.binance.us"
    stream_host = "wss://stream.binance.us:9443"
    partial = True
