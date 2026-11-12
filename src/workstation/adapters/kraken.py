"""Kraken spot WebSocket v2: ordered absolute levels and mandatory top-ten CRC."""

from decimal import Decimal
from hashlib import sha256
import json
import time
from urllib.parse import urlencode
import zlib

from ..book import BookReducer, Level
from ..events import (
    BookUpdate,
    ChecksumInfo,
    Control,
    LevelChange,
    RawFrame,
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
from ..precision import decimal, exact_json_loads, nonnegative_int, scaled_decimal, text
from ..quality import Capabilities
from ..transport import fetch_public
from .base import (
    DecodeError,
    DecodedEvent,
    DecodeResult,
    TransportRequest,
    parse_rfc3339_ns,
)

_ALIASES = {"XBT": "BTC", "XDG": "DOGE"}


def kraken_checksum(
    bids: tuple[Level, ...], asks: tuple[Level, ...], checksum: ChecksumInfo
) -> str:
    """Preserve trailing decimal zeros; asks ascending then bids descending."""
    if checksum.algorithm != "crc32" or checksum.scope != "top10":
        raise ValueError("unsupported Kraken checksum")

    def digits(value: Decimal) -> str:
        return format(value, "f").replace(".", "").lstrip("0")

    wire = "".join(
        digits(level.price) + digits(level.quantity)
        for side in (asks, bids)
        for level in side[:10]
    )
    return str(zlib.crc32(wire.encode("ascii")) & 0xFFFFFFFF)


class KrakenAdapter:
    venue = "kraken"
    api_family = "spot-v2"
    websocket_url = "wss://ws.kraken.com/v2"
    metadata_url = "https://api.kraken.com/0/public/AssetPairs"
    book_url = "https://api.kraken.com/0/public/Depth"

    async def discover(self) -> MetadataSnapshot:
        raw = await fetch_public(TransportRequest("GET", self.metadata_url))
        data = exact_json_loads(raw)
        if data["error"]:
            raise ValueError("Kraken metadata error: " + str(data["error"]))
        digest, now = sha256(raw).hexdigest(), time.time_ns()
        instruments = []
        for native_id, row in data["result"].items():
            # Dark pools and non-WebSocket products have no public v2 symbol.
            if not row.get("wsname"):
                continue
            raw_base, raw_quote = text(row["wsname"]).split("/")
            base_name, quote_name = (
                _ALIASES.get(raw_base, raw_base),
                _ALIASES.get(raw_quote, raw_quote),
            )
            symbol = f"{base_name}/{quote_name}"
            base = AssetIdentity(
                canonical_asset_id("kraken", base_name),
                base_name,
                native_token_id=text(row["base"]),
            )
            quote = AssetIdentity(
                canonical_asset_id("kraken", quote_name),
                quote_name,
                native_token_id=text(row["quote"]),
            )
            lot = scaled_decimal(1, nonnegative_int(row["lot_decimals"]))
            tick = (
                decimal(row["tick_size"], positive=True)
                if "tick_size" in row
                else scaled_decimal(1, nonnegative_int(row["pair_decimals"]))
            )
            status = {
                "online": "active",
                "cancel_only": "suspended",
                "post_only": "active",
                "limit_only": "active",
                "maintenance": "suspended",
                "reduce_only": "suspended",
                "delisted": "delisted",
                "offline": "suspended",
            }.get(row.get("status"), "unknown")
            instruments.append(
                Instrument(
                    InstrumentId(f"{self.venue}:{self.api_family}:spot:{symbol}"),
                    asset_identity_metadata_version(f"{digest}:{now}", base, quote),
                    self.venue,
                    self.api_family,
                    symbol,
                    base,
                    quote,
                    tick,
                    lot,
                    native_asset_id=text(native_id),
                    constraints=OrderConstraints(
                        min_base=decimal(row["ordermin"], nonnegative=True)
                        if "ordermin" in row
                        else None,
                        min_notional=decimal(row["costmin"], nonnegative=True)
                        if "costmin" in row
                        else None,
                        min_notional_currency=quote.asset_id
                        if "costmin" in row
                        else None,
                    ),
                    contract_multiplier=decimal(
                        row.get("lot_multiplier", 1), positive=True
                    ),
                    status=status,
                    data_access="unknown",
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
            raise ValueError("unsupported Kraken instrument")
        if (key.venue, key.api_family, key.instrument_id) != (
            instrument.venue,
            instrument.api_family,
            instrument.instrument_id,
        ):
            raise ValueError("wrong Kraken subscription")
        if key.aggregation or key.channel not in ("book", "trades"):
            raise ValueError("unsupported Kraken channel/aggregation")
        if key.channel == "book" and key.depth not in (10, 25, 100, 500, 1000):
            raise ValueError(
                "Kraken requires explicit supported reconstruction depth (default 25)"
            )
        if key.channel == "trades" and key.depth is not None:
            raise ValueError("trade subscription has no depth")

    def capabilities(
        self, instrument: Instrument, key: SubscriptionKey
    ) -> Capabilities:
        self._validate(instrument, key)
        return Capabilities(
            snapshot=key.channel == "book",
            deltas=key.channel == "book",
            trades=key.channel == "trades",
            checksum_scope="top10" if key.channel == "book" else None,
            exact_event_flow=key.channel == "book",
        )

    def ws_endpoint(self, instrument: Instrument, key: SubscriptionKey) -> str:
        self._validate(instrument, key)
        return self.websocket_url

    def subscription_requests(
        self, instrument: Instrument, key: SubscriptionKey
    ) -> tuple[TransportRequest, ...]:
        self._validate(instrument, key)
        params: dict[str, object] = {
            "channel": "book" if key.channel == "book" else "trade",
            "symbol": [instrument.native_symbol],
            "snapshot": True,
        }
        if key.channel == "book":
            params["depth"] = key.depth
        return (
            TransportRequest(
                "WS",
                self.websocket_url,
                json.dumps(
                    {"method": "subscribe", "params": params}, separators=(",", ":")
                ).encode(),
            ),
        )

    def snapshot_request(
        self, instrument: Instrument, key: SubscriptionKey
    ) -> TransportRequest | None:
        self._validate(instrument, key)
        if key.channel != "book":
            return None
        native_id = instrument.native_asset_id
        if native_id is None:
            raise ValueError("Kraken REST depth requires native pair identity")
        return TransportRequest(
            "GET",
            self.book_url
            + "?"
            + urlencode({"pair": text(native_id), "count": key.depth}),
        )

    def new_reducer(self, instrument: Instrument, key: SubscriptionKey) -> BookReducer:
        return BookReducer(
            instrument,
            key,
            capabilities=self.capabilities(instrument, key),
            truncate_depth=True,
            checksum_calculator=kraken_checksum,
            strategy_id="kraken-v2-crc-top10-v1",
            adapter_revision="kraken-v1",
        )

    def decode(
        self, frame: RawFrame, instrument: Instrument, key: SubscriptionKey
    ) -> DecodeResult:
        try:
            self._validate(instrument, key)
            data = exact_json_loads(frame.payload)
            if not isinstance(data, dict):
                raise ValueError("expected Kraken object")
            if frame.transport == "rest" or frame.endpoint_id.startswith(self.book_url):
                if frame.transport != "rest":
                    raise ValueError("wrong_transport")
                request = self.snapshot_request(instrument, key)
                if request is None or frame.endpoint_id != request.endpoint_id:
                    raise ValueError("wrong_endpoint")
                if data["error"] != []:
                    raise ValueError("Kraken depth error")
                result = data["result"]
                if not isinstance(result, dict) or set(result) != {
                    instrument.native_asset_id
                }:
                    raise ValueError("wrong_symbol")
                book = result[instrument.native_asset_id]
                if not isinstance(book, dict):
                    raise ValueError("invalid Kraken depth book")
                rest_changes = []
                for side, field in (("bid", "bids"), ("ask", "asks")):
                    rows = book[field]
                    if not isinstance(rows, list) or not rows:
                        raise ValueError("invalid Kraken depth side")
                    for row in rows:
                        if not isinstance(row, list) or len(row) != 3:
                            raise ValueError("invalid Kraken depth level")
                        # REST timestamps describe individual levels, not the
                        # snapshot as a whole. Keep their original values in raw.
                        decimal(row[2], nonnegative=True)
                        rest_changes.append(
                            LevelChange(
                                side,
                                decimal(text(row[0])),
                                decimal(text(row[1])),
                            )
                        )
                return DecodeResult(
                    (
                        DecodedEvent(
                            BookUpdate(
                                "snapshot",
                                tuple(rest_changes),
                                subscribed_depth=key.depth,
                            )
                        ),
                    )
                )
            if frame.transport != "ws":
                raise ValueError("wrong_transport")
            channel = data.get("channel")
            if data.get("method") in ("subscribe", "unsubscribe"):
                if data.get("success") is not True:
                    return DecodeResult(
                        controls=(
                            Control(
                                "rejection",
                                "subscription_rejected",
                                str(data.get("error", ""))[:4096],
                            ),
                        )
                    )
                result = data["result"]
                if result["symbol"] != instrument.native_symbol:
                    raise ValueError("wrong_symbol")
                expected_channel = "book" if key.channel == "book" else "trade"
                if result["channel"] != expected_channel:
                    raise ValueError("wrong_channel")
                if key.channel == "book" and result["depth"] != key.depth:
                    raise ValueError("wrong_depth")
                return DecodeResult(controls=(Control("ack"),))
            if channel == "heartbeat" or data.get("method") == "pong":
                return DecodeResult(controls=(Control("heartbeat"),))
            if channel == "status":
                if any(row["system"] != "online" for row in data["data"]):
                    return DecodeResult(
                        controls=(Control("rejection", "venue_not_online"),)
                    )
                return DecodeResult(controls=(Control("heartbeat"),))
            expected_channel = "book" if key.channel == "book" else "trade"
            if channel != expected_channel:
                raise ValueError("wrong_channel")
            kind = {"snapshot": "snapshot", "update": "delta"}[data["type"]]
            rows = data["data"]
            if not isinstance(rows, list) or not rows:
                raise ValueError("empty Kraken message")
            if channel == "book":
                # One book per v2 message. Repeated changes within each side retain wire order.
                if key.channel != "book" or len(rows) != 1:
                    raise ValueError("invalid Kraken book batch")
                row = rows[0]
                if row["symbol"] != instrument.native_symbol:
                    raise ValueError("wrong_symbol")
                checksum = nonnegative_int(row["checksum"])
                if checksum > 0xFFFFFFFF:
                    raise ValueError("invalid CRC32")
                changes = tuple(
                    LevelChange(side, decimal(level["price"]), decimal(level["qty"]))
                    for side, field in (("bid", "bids"), ("ask", "asks"))
                    for level in row.get(field, [])
                )
                if not changes:
                    raise ValueError("empty Kraken book changes")
                stamp = parse_rfc3339_ns(row.get("timestamp"))
                return DecodeResult(
                    (
                        DecodedEvent(
                            BookUpdate(kind, changes, subscribed_depth=key.depth),
                            checksum=ChecksumInfo(
                                "crc32", str(checksum), scope="top10"
                            ),
                            exchange_event_wall_ns=stamp,
                            exchange_time_kind="book" if stamp is not None else None,
                        ),
                    )
                )
            if channel == "trade":
                events = []
                for row in rows:
                    if row["symbol"] != instrument.native_symbol:
                        raise ValueError("wrong_symbol")
                    trade_id = str(nonnegative_int(row["trade_id"]))
                    side = row.get("side")
                    taker = side if side in ("buy", "sell") else "unknown"
                    stamp = parse_rfc3339_ns(row.get("timestamp"))
                    price = decimal(row["price"], positive=True)
                    quantity = decimal(row["qty"], positive=True)
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
                                side_provenance="kraken_taker_side"
                                if taker != "unknown"
                                else "unknown",
                                is_subscription_history=kind == "snapshot",
                            ),
                            exchange_event_wall_ns=stamp,
                            exchange_time_kind="trade" if stamp is not None else None,
                        )
                    )
                return DecodeResult(tuple(events))
            raise ValueError("unsupported Kraken channel")
        except (
            ValueError,
            TypeError,
            KeyError,
            ArithmeticError,
            AttributeError,
        ) as exc:
            return DecodeResult(
                error=DecodeError(
                    "invalid_kraken_message", frame.payload_sha256, str(exc)[:4096]
                )
            )
