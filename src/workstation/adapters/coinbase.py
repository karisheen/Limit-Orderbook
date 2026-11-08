"""Public Coinbase Advanced Trade only; never Coinbase Exchange wire schemas."""

from dataclasses import replace
from hashlib import sha256
import json
import time
from urllib.parse import urlencode

from ..book import BookReducer, Reduction
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
    AssetIdentity,
    Instrument,
    InstrumentId,
    MetadataSnapshot,
    OrderConstraints,
    asset_identity_metadata_version,
    canonical_asset_id,
)
from ..precision import decimal, exact_json_loads, nonnegative_int, text
from ..quality import Capabilities
from ..transport import fetch_public
from .base import (
    DecodeError,
    DecodedEvent,
    DecodeResult,
    TransportRequest,
    parse_rfc3339_ns,
)


def _asset(ticker: str) -> AssetIdentity:
    text(ticker)
    return AssetIdentity(
        canonical_asset_id("coinbase", ticker),
        ticker,
        native_token_id=ticker,
    )


def connection_sequence(
    previous: SequenceInfo | None, incoming: SequenceInfo | None, snapshot: bool
) -> str:
    """Snapshots cannot conceal missing connection frames; controls consume IDs too."""
    if incoming is None or incoming.scope != "connection" or incoming.last is None:
        return "gap"
    try:
        current = int(incoming.last)
        if previous is None:
            return "accept" if current == 0 else "gap"
        if previous.scope != incoming.scope or previous.last is None:
            return "gap"
        old = int(previous.last)
        return "ignore" if current <= old else "accept" if current == old + 1 else "gap"
    except ValueError:
        return "gap"


class CoinbaseReducer(BookReducer):
    def apply(
        self, events: tuple[EventEnvelope, ...], *, synchronized: bool = True
    ) -> Reduction:
        result = super().apply(events, synchronized=synchronized)
        if result.book is not None and result.recovery is None and self.synchronized:
            # WS level2 is a full book. The capacity is a safety bound, not display depth.
            self.quality = replace(
                self.quality, coverage=replace(self.quality.coverage, full_depth=True)
            )
            self.book = replace(result.book, quality=self.quality)
            return replace(result, book=self.book)
        return result


class CoinbaseAdapter:
    venue = "coinbase"
    api_family = "advanced-trade"
    websocket_url = "wss://advanced-trade-ws.coinbase.com"
    metadata_url = "https://api.coinbase.com/api/v3/brokerage/market/products"
    book_url = "https://api.coinbase.com/api/v3/brokerage/market/product_book"

    async def discover(self) -> MetadataSnapshot:
        pages: list[bytes] = []
        products: list[dict] = []
        seen: set[str] = set()
        cursor: str | None = None
        for page in range(100):
            params: dict[str, str | int] = {"product_type": "SPOT", "limit": 1000}
            params["cursor" if cursor else "offset"] = cursor if cursor else page * 1000
            raw = await fetch_public(
                TransportRequest("GET", self.metadata_url + "?" + urlencode(params))
            )
            data = exact_json_loads(raw)
            rows = data["products"]
            if not isinstance(rows, list):
                raise ValueError("invalid Advanced products")
            pages.append(raw)
            for row in rows:
                symbol = text(row["product_id"])
                if symbol in seen:
                    raise ValueError("duplicate Advanced product/pagination loop")
                seen.add(symbol)
                products.append(row)
            pagination = data.get("pagination", {})
            if pagination.get("has_next") is False or (
                not pagination.get("has_next") and len(rows) < 1000
            ):
                break
            cursor = pagination.get("next_cursor")
            if cursor is not None:
                text(cursor)
        else:
            raise ValueError("Advanced product pagination limit exceeded")
        digest = sha256(
            b"".join(len(raw).to_bytes(8, "big") + raw for raw in pages)
        ).hexdigest()
        now = time.time_ns()
        instruments = []
        for row in products:
            if row.get("product_type", "SPOT") != "SPOT":
                continue
            symbol = text(row["product_id"])
            base, quote = (
                _asset(row["base_currency_id"]),
                _asset(row["quote_currency_id"]),
            )
            status = "active" if row.get("status") == "online" else "unknown"
            if (
                row.get("is_disabled")
                or row.get("trading_disabled")
                or row.get("cancel_only")
                or row.get("view_only")
            ):
                status = "suspended"
            elif row.get("status") in ("offline", "delisted"):
                status = "delisted" if row["status"] == "delisted" else "suspended"
            alias = row.get("alias")
            if alias:
                text(alias)
            is_alias = bool(alias and alias != symbol)
            if is_alias:
                # Unified USD/USDC aliases are not another executable liquidity pool.
                status = "unavailable"
            instruments.append(
                Instrument(
                    InstrumentId(f"{self.venue}:{self.api_family}:spot:{symbol}"),
                    asset_identity_metadata_version(f"{digest}:{now}", base, quote),
                    self.venue,
                    self.api_family,
                    symbol,
                    base,
                    quote,
                    decimal(row["price_increment"], positive=True),
                    decimal(row["base_increment"], positive=True),
                    native_asset_id=symbol,
                    constraints=OrderConstraints(
                        min_base=decimal(row["base_min_size"], nonnegative=True),
                        max_base=decimal(row["base_max_size"], nonnegative=True),
                        min_notional=decimal(row["quote_min_size"], nonnegative=True),
                        max_notional=decimal(row["quote_max_size"], nonnegative=True),
                        min_notional_currency=quote.asset_id,
                    ),
                    status=status,
                    data_access="unsupported" if is_alias else "unknown",
                    execution_access="unknown",
                    metadata_asof_wall_ns=now,
                    metadata_source=self.metadata_url,
                )
            )
        return MetadataSnapshot(self.metadata_url, now, digest, tuple(instruments))

    def _validate(self, instrument: Instrument, key: SubscriptionKey) -> None:
        if (instrument.venue, instrument.api_family, instrument.market_type) != (
            self.venue,
            self.api_family,
            "spot",
        ):
            raise ValueError("unsupported Coinbase instrument")
        if instrument.data_access == "unsupported":
            raise ValueError(
                "unsupported Coinbase product (including unified-book aliases)"
            )
        if key != SubscriptionKey.for_instrument(
            instrument, channel=key.channel
        ) or key.channel not in ("book", "trades"):
            raise ValueError(
                "Advanced feeds require unaggregated full-depth subscriptions"
            )

    def capabilities(
        self, instrument: Instrument, key: SubscriptionKey
    ) -> Capabilities:
        self._validate(instrument, key)
        return Capabilities(
            snapshot=key.channel == "book",
            deltas=key.channel == "book",
            trades=key.channel == "trades",
            sequence_scope="connection",
            exact_event_flow=key.channel == "book",
        )

    def ws_endpoint(self, instrument: Instrument, key: SubscriptionKey) -> str:
        self._validate(instrument, key)
        return self.websocket_url

    def subscription_requests(
        self, instrument: Instrument, key: SubscriptionKey
    ) -> tuple[TransportRequest, ...]:
        self._validate(instrument, key)
        channels = (
            ("level2", "heartbeats")
            if key.channel == "book"
            else ("market_trades", "heartbeats")
        )
        return tuple(
            TransportRequest(
                "WS",
                self.websocket_url,
                json.dumps(
                    {
                        "type": "subscribe",
                        "channel": channel,
                        "product_ids": [instrument.native_symbol],
                    },
                    separators=(",", ":"),
                ).encode(),
            )
            for channel in channels
        )

    def snapshot_request(
        self, instrument: Instrument, key: SubscriptionKey
    ) -> TransportRequest | None:
        self._validate(instrument, key)
        if key.channel != "book":
            return None
        # Fallback only: no Advanced REST sequence can bridge this to the WS stream.
        return TransportRequest(
            "GET",
            self.book_url
            + "?"
            + urlencode({"product_id": instrument.native_symbol, "limit": 1000}),
        )

    def new_reducer(self, instrument: Instrument, key: SubscriptionKey) -> BookReducer:
        return CoinbaseReducer(
            instrument,
            key,
            capabilities=self.capabilities(instrument, key),
            sequence_validator=connection_sequence,
            max_levels=100_000,
            strategy_id="coinbase-advanced-connection-v1",
            adapter_revision="coinbase-v1",
        )

    def decode(
        self, frame: RawFrame, instrument: Instrument, key: SubscriptionKey
    ) -> DecodeResult:
        try:
            self._validate(instrument, key)
            data = exact_json_loads(frame.payload)
            if not isinstance(data, dict):
                raise ValueError("expected Advanced object")
            if frame.transport.lower() in ("rest", "http", "https"):
                book = data["pricebook"]
                if (
                    key.channel != "book"
                    or book["product_id"] != instrument.native_symbol
                ):
                    raise ValueError("wrong_symbol")
                changes = tuple(
                    LevelChange(side, decimal(row["price"]), decimal(row["size"]))
                    for side, field in (("bid", "bids"), ("ask", "asks"))
                    for row in book[field]
                )
                stamp = parse_rfc3339_ns(book.get("time"))
                return DecodeResult(
                    (
                        DecodedEvent(
                            BookUpdate("snapshot", changes),
                            exchange_event_wall_ns=stamp,
                            exchange_time_kind="book" if stamp is not None else None,
                        ),
                    )
                )
            if data.get("type") == "error":
                return DecodeResult(
                    controls=(
                        Control(
                            "rejection",
                            "subscription_rejected",
                            str(data.get("message", ""))[:4096],
                        ),
                    )
                )
            sequence = SequenceInfo(
                "connection", last=str(nonnegative_int(data["sequence_num"]))
            )
            channel = data["channel"]
            if (
                channel in ("l2_data", "level2")
                and key.channel != "book"
                or channel == "market_trades"
                and key.channel != "trades"
            ):
                raise ValueError("wrong_channel")
            stamp = parse_rfc3339_ns(data.get("timestamp"))
            events = []
            for event in data["events"]:
                if channel in ("l2_data", "level2"):
                    if (
                        key.channel != "book"
                        or event["product_id"] != instrument.native_symbol
                    ):
                        raise ValueError("wrong_symbol")
                    kind = {"snapshot": "snapshot", "update": "delta"}[event["type"]]
                    changes = tuple(
                        LevelChange(
                            {"bid": "bid", "offer": "ask"}[row["side"]],
                            decimal(row["price_level"]),
                            decimal(row["new_quantity"]),
                            exchange_event_wall_ns=parse_rfc3339_ns(
                                row.get("event_time")
                            ),
                        )
                        for row in event["updates"]
                    )
                    events.append(
                        DecodedEvent(
                            BookUpdate(kind, changes),
                            sequence,
                            exchange_event_wall_ns=stamp,
                            exchange_time_kind="message" if stamp is not None else None,
                        )
                    )
                elif channel == "market_trades":
                    history = {"snapshot": True, "update": False}[event["type"]]
                    for row in event["trades"]:
                        if row["product_id"] != instrument.native_symbol:
                            raise ValueError("wrong_symbol")
                        trade_id = text(row["trade_id"])
                        side = row.get("side")
                        taker = {"BUY": "sell", "SELL": "buy"}.get(side, "unknown")
                        trade_time = parse_rfc3339_ns(row.get("time"))
                        price = decimal(row["price"], positive=True)
                        quantity = decimal(row["size"], positive=True)
                        instrument.validate_price(price)
                        instrument.validate_quantity(quantity)
                        events.append(
                            DecodedEvent(
                                Trade(
                                    trade_id,
                                    f"{instrument.instrument_id}:trade:{trade_id}",
                                    price,
                                    quantity,
                                    taker_side=taker,
                                    side_provenance="coinbase_maker_side_inverted"
                                    if taker != "unknown"
                                    else "unknown",
                                    is_subscription_history=history,
                                ),
                                sequence,
                                exchange_event_wall_ns=trade_time,
                                exchange_time_kind="trade"
                                if trade_time is not None
                                else None,
                            )
                        )
                elif channel == "subscriptions":
                    for subscription_channel, symbols in event["subscriptions"].items():
                        expected_symbol = (
                            "heartbeats"
                            if subscription_channel == "heartbeats"
                            else instrument.native_symbol
                        )
                        if not isinstance(symbols, list) or any(
                            symbol != expected_symbol for symbol in symbols
                        ):
                            raise ValueError("wrong_symbol")
                    events.append(DecodedEvent(Control("ack"), sequence))
                elif channel == "heartbeats":
                    events.append(
                        DecodedEvent(
                            Control("heartbeat"),
                            sequence,
                            exchange_event_wall_ns=stamp,
                            exchange_time_kind="message" if stamp is not None else None,
                        )
                    )
                else:
                    raise ValueError("unsupported Advanced channel")
            if not events:
                # Even a valid empty trade batch consumes one connection sequence number.
                if channel not in ("market_trades", "subscriptions", "heartbeats"):
                    raise ValueError("empty Advanced book message")
                events.append(DecodedEvent(Control("heartbeat"), sequence))
            return DecodeResult(tuple(events))
        except (
            ValueError,
            TypeError,
            KeyError,
            ArithmeticError,
            AttributeError,
        ) as exc:
            return DecodeResult(
                error=DecodeError(
                    "invalid_coinbase_message", frame.payload_sha256, str(exc)[:4096]
                )
            )
