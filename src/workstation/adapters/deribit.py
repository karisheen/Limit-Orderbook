"""Deribit public inverse perpetuals, with exact USD amount/contract conversion.

The grouped ``book.SYMBOL.none.DEPTH.100ms`` feed replaces the limited book on
EVERY notification; only ``book.SYMBOL.100ms`` sends an initial full snapshot
followed by action tuples linked by prev_change_id. Neither is raw event flow.
Full-depth coverage is asserted only after the ungrouped snapshot is validated;
predecessor-linked deltas and checkpoint restores preserve that evidence.

Protocol references:
https://docs.deribit.com/api-reference/market-data/public-get_instruments
https://docs.deribit.com/subscriptions/orderbook/bookinstrument_namegroupdepthinterval
https://docs.deribit.com/subscriptions/orderbook/bookinstrument_nameinterval
https://docs.deribit.com/subscriptions/trades/tradesinstrument_nameinterval
"""

from dataclasses import replace
from decimal import Inexact, localcontext
from hashlib import sha256
import json
from time import time_ns

from ..book import BookReducer, Reduction, monotonic_sequence
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
from ..instruments import Instrument, InstrumentId, MetadataSnapshot, OrderConstraints
from ..precision import DECIMAL_CONTEXT, decimal, exact_json_loads, text
from ..quality import Capabilities
from .base import DecodedEvent, DecodeResult, TransportRequest
from .binance import (
    array_field,
    asset_identity,
    decode_failure,
    integer_field,
    metadata_snapshot,
    object_field,
)


def _contracts(amount, multiplier):
    """USD API amounts are not base coins; divide only by documented USD/contract."""
    with localcontext(DECIMAL_CONTEXT) as context:
        context.traps[Inexact] = True
        return decimal(decimal(amount, nonnegative=True) / multiplier, nonnegative=True)


def deribit_book_sequence(previous, incoming, snapshot):
    if incoming is None:
        return "accept"  # Acknowledgements do not advance the book sequence.
    decision = monotonic_sequence(previous, incoming, False)
    if decision != "accept" or previous is None or snapshot:
        return decision
    # change_id is not contiguous: the explicit predecessor supplies continuity.
    return "accept" if incoming.previous == previous.last else "gap"


def _trade_sequence(previous, incoming, snapshot):
    return (
        "accept" if incoming is None else monotonic_sequence(previous, incoming, False)
    )


class DeribitReducer(BookReducer):
    def apply(
        self, events: tuple[EventEnvelope, ...], *, synchronized: bool = True
    ) -> Reduction:
        result = super().apply(events, synchronized=synchronized)
        if (
            self.key.channel == "book"
            and self.key.depth is None
            and result.book is not None
            and result.recovery is None
            and self.synchronized
        ):
            # The ungrouped stream snapshots every price level; accepted linked
            # deltas preserve that coverage. Grouped snapshots remain bounded.
            self.quality = replace(
                self.quality, coverage=replace(self.quality.coverage, full_depth=True)
            )
            self.book = replace(result.book, quality=self.quality)
            return replace(result, book=self.book)
        return result


class DeribitAdapter:
    venue = "deribit"
    api_family = "jsonrpc-v2"
    rest_host = "https://www.deribit.com"
    stream = "wss://www.deribit.com/ws/api/v2"
    symbols = ("BTC-PERPETUAL", "ETH-PERPETUAL")
    specification = (
        "https://docs.deribit.com/api-reference/market-data/public-get_instruments"
    )

    @property
    def metadata_endpoint(self):
        return (
            f"{self.rest_host}/api/v2/public/get_instruments"
            "?currency=any&kind=future&expired=false"
        )

    async def discover(self) -> MetadataSnapshot:
        from ..transport import fetch_public

        raw = await fetch_public(TransportRequest("GET", self.metadata_endpoint))
        return self.parse_metadata(raw, fetch_wall_ns=time_ns())

    def parse_metadata(self, raw: bytes, *, fetch_wall_ns: int = 0) -> MetadataSnapshot:
        root = object_field(exact_json_loads(raw))
        if root.get("jsonrpc") != "2.0" or "error" in root:
            raise ValueError("invalid Deribit metadata response")
        version = f"{sha256(raw).hexdigest()}:{fetch_wall_ns}"
        instruments = []
        for value in array_field(root["result"]):
            row = object_field(value)
            symbol = text(row["instrument_name"])
            if symbol not in self.symbols:
                continue
            base_name = symbol.split("-", 1)[0]
            if (
                row["kind"] != "future"
                or row["settlement_period"] != "perpetual"
                or row["instrument_type"] != "reversed"
                or row["base_currency"] != base_name
                or row["quote_currency"] != "USD"
                or row["settlement_currency"] != base_name
            ):
                raise ValueError("unexpected inverse perpetual specification")
            if type(row["is_active"]) is not bool:
                raise ValueError("invalid instrument activity")
            if array_field(row.get("tick_size_steps", [])):
                raise ValueError("unsupported variable perpetual tick size")
            base = asset_identity(self.venue, base_name)
            currency = asset_identity(self.venue, "USD")
            multiplier = decimal(row["contract_size"], positive=True)
            minimum = decimal(row["min_trade_amount"], positive=True)
            # min_trade_amount is the USD quantity increment. The separate API
            # lot_size is for fee lot counting, NOT the order quantity step.
            lot = _contracts(minimum, multiplier)
            state = row.get("state")
            status = (
                ("active" if row["is_active"] else "suspended")
                if state is None
                else {
                    "open": "active" if row["is_active"] else "suspended",
                    "locked": "suspended",
                    "halted": "suspended",
                    "settlement": "suspended",
                    "delivered": "delisted",
                    "inactive": "unavailable",
                }.get(text(state), "unknown")
            )
            instruments.append(
                Instrument(
                    InstrumentId(f"{self.venue}:{self.api_family}:perpetual:{symbol}"),
                    version,
                    self.venue,
                    self.api_family,
                    symbol,
                    base,
                    currency,
                    decimal(row["tick_size"], positive=True),
                    lot,
                    native_asset_id=str(integer_field(row["instrument_id"]))
                    if "instrument_id" in row
                    else None,
                    settlement=base,
                    market_type="perpetual",
                    constraints=OrderConstraints(
                        min_base=lot,
                        min_notional=minimum,
                        min_notional_currency=currency.asset_id,
                    ),
                    contract_multiplier=multiplier,
                    quantity_unit="contracts",
                    contract_value_currency=currency.asset_id,
                    inverse=True,
                    status=status,
                    metadata_asof_wall_ns=fetch_wall_ns,
                    metadata_source=self.metadata_endpoint,
                    settlement_conversion="none",
                    contract_specification_source=self.specification,
                )
            )
        return metadata_snapshot(
            self.metadata_endpoint, raw, fetch_wall_ns, instruments
        )

    def _validate(self, instrument, key):
        if (instrument.venue, instrument.api_family, instrument.instrument_id) != (
            key.venue,
            key.api_family,
            key.instrument_id,
        ) or (key.venue, key.api_family) != (self.venue, self.api_family):
            raise ValueError("wrong_subscription")
        if (
            instrument.native_symbol not in self.symbols
            or instrument.market_type != "perpetual"
            or not instrument.inverse
            or instrument.quantity_unit != "contracts"
            or instrument.contract_value_currency != instrument.quote.asset_id
            or instrument.quote.asset_id != "USD"
            or key.channel not in ("book", "trades")
            or key.aggregation
            or (key.channel == "book" and key.depth not in (None, 1, 10, 20))
            or (key.channel == "trades" and key.depth is not None)
        ):
            raise ValueError("unsupported_subscription")
        if instrument.status != "active":
            raise ValueError(f"product_{instrument.status}")

    def _topic(self, instrument, key):
        symbol = instrument.native_symbol
        if key.channel == "trades":
            return f"trades.{symbol}.100ms"
        return (
            f"book.{symbol}.100ms"
            if key.depth is None
            else f"book.{symbol}.none.{key.depth}.100ms"
        )

    def capabilities(
        self, instrument: Instrument, key: SubscriptionKey
    ) -> Capabilities:
        self._validate(instrument, key)
        book = key.channel == "book"
        return Capabilities(
            snapshot=book,
            deltas=book and key.depth is None,
            trades=not book,
            sequence_scope=self._topic(instrument, key),
            exact_event_flow=False,
        )

    def ws_endpoint(self, instrument: Instrument, key: SubscriptionKey) -> str:
        self._validate(instrument, key)
        return self.stream

    def subscription_requests(
        self, instrument: Instrument, key: SubscriptionKey
    ) -> tuple[TransportRequest, ...]:
        endpoint = self.ws_endpoint(instrument, key)
        return (
            TransportRequest(
                "WS",
                endpoint,
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "public/subscribe",
                        "params": {"channels": [self._topic(instrument, key)]},
                    },
                    separators=(",", ":"),
                ).encode(),
            ),
        )

    def snapshot_request(
        self, instrument: Instrument, key: SubscriptionKey
    ) -> TransportRequest | None:
        self._validate(instrument, key)
        # Both public book channels deliver their own snapshots. A limited REST
        # book must never bootstrap the unlimited delta stream as full depth.
        return None

    def decode(
        self, frame: RawFrame, instrument: Instrument, key: SubscriptionKey
    ) -> DecodeResult:
        try:
            self._validate(instrument, key)
            if frame.transport != "ws" or frame.endpoint_id != self.stream:
                raise ValueError("wrong_transport_or_endpoint")
            root = object_field(exact_json_loads(frame.payload))
            if root.get("jsonrpc") != "2.0":
                raise ValueError("invalid JSON-RPC version")
            if "id" in root:
                if type(root["id"]) is not int or root["id"] != 1 or "method" in root:
                    raise ValueError("invalid subscription acknowledgement")
                if "error" in root:
                    if "result" in root:
                        raise ValueError("conflicting RPC response")
                    error = object_field(root["error"])
                    message = text(error["message"])
                    return DecodeResult(
                        controls=(
                            Control("rejection", "subscription_rejected", message),
                        )
                    )
                if root.get("result") != [self._topic(instrument, key)]:
                    raise ValueError("wrong subscription acknowledgement")
                return DecodeResult(controls=(Control("ack"),))
            if root.get("method") != "subscription" or "error" in root:
                raise ValueError("unexpected RPC notification")
            params = object_field(root["params"])
            if params["channel"] != self._topic(instrument, key):
                raise ValueError("wrong_channel")
            if key.channel == "trades":
                return DecodeResult(
                    tuple(
                        self._trade(object_field(row), instrument, key)
                        for row in array_field(params["data"])
                    )
                )
            return DecodeResult(
                (self._book(object_field(params["data"]), instrument, key),)
            )
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            return decode_failure(frame, exc)

    def _book(self, data, instrument, key):
        if data["instrument_name"] != instrument.native_symbol:
            raise ValueError("wrong_symbol")
        change_id = integer_field(data["change_id"])
        previous = None
        if key.depth is not None:
            if data.get("type", "snapshot") != "snapshot" or "prev_change_id" in data:
                raise ValueError("grouped_book_requires_snapshot")
            kind = "snapshot"
        else:
            native_kind = data["type"]
            if native_kind not in ("snapshot", "change"):
                raise ValueError("invalid book message type")
            kind = "snapshot" if native_kind == "snapshot" else "delta"
            if kind == "delta":
                previous = integer_field(data["prev_change_id"])
                if previous >= change_id:
                    raise ValueError("invalid change predecessor")
            elif "prev_change_id" in data:
                raise ValueError("snapshot_has_predecessor")
        changes = []
        for side, name in (("bid", "bids"), ("ask", "asks")):
            rows = array_field(data[name])
            if key.depth is not None and len(rows) > key.depth:
                raise ValueError("snapshot_depth_exceeded")
            seen = set()
            for value in rows:
                row = array_field(value)
                if len(row) != (2 if key.depth is not None else 3):
                    raise ValueError("invalid book tuple")
                action = "new" if key.depth is not None else row[0]
                if action not in ("new", "change", "delete") or (
                    kind == "snapshot" and action != "new"
                ):
                    raise ValueError("invalid book action")
                price = decimal(row[-2], positive=True)
                amount = decimal(row[-1], nonnegative=True)
                if (action == "delete") != (amount == 0):
                    raise ValueError("invalid book action amount")
                quantity = _contracts(amount, instrument.contract_multiplier)
                instrument.validate_price(price)
                instrument.validate_quantity(quantity)
                if price in seen:
                    raise ValueError("duplicate book price")
                seen.add(price)
                changes.append(LevelChange(side, price, quantity))
        timestamp = data.get("timestamp")
        return DecodedEvent(
            BookUpdate(kind, tuple(changes), key.depth),
            SequenceInfo(
                self._topic(instrument, key),
                last=str(change_id),
                previous=str(previous) if previous is not None else None,
            ),
            exchange_event_wall_ns=integer_field(timestamp) * 1_000_000
            if timestamp is not None
            else None,
            exchange_time_kind="book_change" if timestamp is not None else None,
        )

    def _trade(self, row, instrument, key):
        if row["instrument_name"] != instrument.native_symbol:
            raise ValueError("wrong_symbol")
        trade_id = text(row["trade_id"])
        sequence = integer_field(row["trade_seq"])
        price = decimal(row["price"], positive=True)
        amount = decimal(row["amount"], positive=True)
        quantity = _contracts(amount, instrument.contract_multiplier)
        if "contracts" in row and decimal(row["contracts"], positive=True) != quantity:
            raise ValueError("inconsistent native contract quantity")
        instrument.validate_price(price)
        instrument.validate_quantity(quantity)
        side = row.get("direction")
        if side is not None and not isinstance(side, str):
            raise ValueError("invalid trade direction")
        if side is None or side not in ("buy", "sell"):
            side = "unknown"
        known = side != "unknown"
        return DecodedEvent(
            Trade(
                trade_id,
                f"{instrument.instrument_id}:trade:{trade_id}",
                price,
                quantity,
                quantity_unit="contracts",
                taker_side=side,
                side_provenance="documented_taker" if known else "unknown",
            ),
            SequenceInfo(self._topic(instrument, key), last=str(sequence)),
            exchange_event_wall_ns=integer_field(row["timestamp"]) * 1_000_000,
            exchange_time_kind="trade",
        )

    def new_reducer(self, instrument: Instrument, key: SubscriptionKey) -> BookReducer:
        return DeribitReducer(
            instrument,
            key,
            capabilities=self.capabilities(instrument, key),
            sequence_validator=deribit_book_sequence
            if key.channel == "book"
            else _trade_sequence,
            strategy_id="deribit-public-v1",
            adapter_revision="deribit-v1",
        )
