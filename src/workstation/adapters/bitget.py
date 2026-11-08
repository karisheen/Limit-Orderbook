"""Bitget v2 public spot/USDT perpetual books5 snapshots and taker trades.

Protocol: https://www.bitget.com/docs/classic/websocket/spot/public/Depth-Channel
Contract sizes are base-coin amounts, not integer contracts. Bounded books5
messages replace the visible book; neither depth completeness nor event-by-event
continuity is promised. Original numeric JSON tokens are parsed as Decimal.
"""

from hashlib import sha256
import json
from time import time_ns
from urllib.parse import quote

from ..book import BookReducer
from ..events import BookUpdate, Control, RawFrame, SequenceInfo, SubscriptionKey, Trade
from ..instruments import Instrument, InstrumentId, MetadataSnapshot, OrderConstraints
from ..precision import (
    decimal,
    exact_json_loads,
    scaled_decimal,
    text,
    validate_increment,
)
from ..quality import Capabilities
from .base import DecodedEvent, DecodeResult, TransportRequest
from .binance import (
    array_field,
    asset_identity,
    decode_failure,
    integer_field,
    level_changes,
    metadata_snapshot,
    object_field,
    optional_decimal,
)


class BitgetAdapter:
    venue = "bitget"
    api_family = "v2-spot"
    perpetual_api_family = "v2-usdt-futures"
    rest_host = "https://api.bitget.com"
    stream = "wss://ws.bitget.com/v2/ws/public"

    def _metadata_endpoint(self, market_type):
        if market_type == "spot":
            return f"{self.rest_host}/api/v2/spot/public/symbols"
        if market_type == "perpetual":
            return (
                f"{self.rest_host}/api/v2/mix/market/contracts?productType=USDT-FUTURES"
            )
        raise ValueError("unsupported Bitget market")

    async def discover(self) -> MetadataSnapshot:
        from ..transport import fetch_public

        payloads: list[bytes] = []
        instruments: list[Instrument] = []
        for market in ("spot", "perpetual"):
            raw = await fetch_public(
                TransportRequest("GET", self._metadata_endpoint(market))
            )
            snapshot = self.parse_metadata(
                raw, market_type=market, fetch_wall_ns=time_ns()
            )
            payloads.append(raw)
            instruments.extend(snapshot.instruments)
        return metadata_snapshot(
            "|".join(
                self._metadata_endpoint(market) for market in ("spot", "perpetual")
            ),
            b"\n".join(payloads),
            snapshot.fetch_wall_ns,
            instruments,
        )

    @staticmethod
    def _success(root):
        if root["code"] != "00000":
            raise ValueError(f"endpoint_error:{root['code']}")

    def parse_metadata(
        self, raw: bytes, *, market_type: str = "spot", fetch_wall_ns: int = 0
    ) -> MetadataSnapshot:
        source = self._metadata_endpoint(market_type)
        root = object_field(exact_json_loads(raw))
        self._success(root)
        perpetual = market_type == "perpetual"
        family = self.perpetual_api_family if perpetual else self.api_family
        version = f"{sha256(raw).hexdigest()}:{fetch_wall_ns}"
        instruments = []
        for value in array_field(root["data"]):
            row = object_field(value)
            # RWA futures share the perpetual product type with crypto contracts.
            if perpetual and (
                row.get("isRwa") == "YES" or row["symbolType"] != "perpetual"
            ):
                continue
            symbol = text(row["symbol"])
            base = asset_identity(self.venue, row["baseCoin"])
            currency = asset_identity(self.venue, row["quoteCoin"])
            if perpetual and (
                currency.ticker != "USDT"
                or "USDT" not in array_field(row["supportMarginCoins"])
            ):
                raise ValueError("unsupported USDT contract settlement")
            if perpetual:
                tick = scaled_decimal(
                    integer_field(row["priceEndStep"]), integer_field(row["pricePlace"])
                )
                lot = decimal(row["sizeMultiplier"], positive=True)
                validate_increment(
                    lot, scaled_decimal(1, integer_field(row["volumePlace"]))
                )
                native_status = text(row["symbolStatus"])
                status = {
                    "normal": "active",
                    "restrictedAPI": "active",
                    "maintain": "suspended",
                    "limit_open": "suspended",
                    "off": "delisted",
                    "listed": "unavailable",
                }.get(native_status, "unknown")
            else:
                tick = scaled_decimal(1, integer_field(row["pricePrecision"]))
                lot = scaled_decimal(1, integer_field(row["quantityPrecision"]))
                native_status = text(row["status"])
                status = {
                    "online": "active",
                    "halt": "suspended",
                    "offline": "delisted",
                    "gray": "unavailable",
                }.get(native_status, "unknown")
            instruments.append(
                Instrument(
                    InstrumentId(f"{self.venue}:{family}:{market_type}:{symbol}"),
                    version,
                    self.venue,
                    family,
                    symbol,
                    base,
                    currency,
                    tick,
                    lot,
                    settlement=currency if perpetual else None,
                    market_type=market_type,
                    quantity_unit="base",
                    contract_multiplier=decimal("1"),
                    contract_value_currency=base.asset_id if perpetual else None,
                    inverse=False,
                    settlement_conversion="none" if perpetual else "unknown",
                    contract_specification_source=source if perpetual else None,
                    constraints=OrderConstraints(
                        min_base=optional_decimal(
                            row, "minTradeNum" if perpetual else "minTradeAmount"
                        ),
                        max_base=None
                        if perpetual
                        else optional_decimal(row, "maxTradeAmount"),
                        min_notional=optional_decimal(row, "minTradeUSDT"),
                        min_notional_currency=asset_identity(
                            self.venue, "USDT"
                        ).asset_id,
                        max_limit_base=optional_decimal(row, "maxOrderQty")
                        if perpetual
                        else None,
                        max_market_base=optional_decimal(row, "maxMarketOrderQty")
                        if perpetual
                        else None,
                    ),
                    status=status,
                    execution_access="blocked"
                    if native_status == "restrictedAPI"
                    else "unknown",
                    metadata_asof_wall_ns=fetch_wall_ns,
                    metadata_source=source,
                )
            )
        return metadata_snapshot(source, raw, fetch_wall_ns, instruments)

    def _validate(self, instrument, key):
        family = {
            "spot": self.api_family,
            "perpetual": self.perpetual_api_family,
        }.get(instrument.market_type)
        if (
            family is None
            or (instrument.venue, instrument.api_family, instrument.instrument_id)
            != (key.venue, key.api_family, key.instrument_id)
            or (key.venue, key.api_family) != (self.venue, family)
        ):
            raise ValueError("wrong_subscription")
        if key.channel not in ("book", "trades") or key.aggregation:
            raise ValueError("unsupported_subscription")
        if key.channel == "book" and key.depth != 5:
            raise ValueError("Bitget books5 requires depth 5")
        if instrument.status != "active":
            raise ValueError(f"product_{instrument.status}")

    def _arg(self, instrument, key):
        return {
            "instType": "SPOT" if instrument.market_type == "spot" else "USDT-FUTURES",
            "channel": "books5" if key.channel == "book" else "trade",
            "instId": instrument.native_symbol,
        }

    def capabilities(
        self, instrument: Instrument, key: SubscriptionKey
    ) -> Capabilities:
        self._validate(instrument, key)
        return Capabilities(
            snapshot=key.channel == "book", trades=key.channel == "trades"
        )

    def ws_endpoint(self, instrument: Instrument, key: SubscriptionKey) -> str:
        self._validate(instrument, key)
        return self.stream

    def subscription_requests(
        self, instrument: Instrument, key: SubscriptionKey
    ) -> tuple[TransportRequest, ...]:
        endpoint = self.ws_endpoint(instrument, key)
        body = {"op": "subscribe", "args": [self._arg(instrument, key)]}
        return (
            TransportRequest(
                "WS", endpoint, json.dumps(body, separators=(",", ":")).encode()
            ),
        )

    def snapshot_request(
        self, instrument: Instrument, key: SubscriptionKey
    ) -> TransportRequest:
        self._validate(instrument, key)
        symbol = quote(instrument.native_symbol, safe="")
        if instrument.market_type == "spot":
            suffix = (
                f"fills?symbol={symbol}&limit=100"
                if key.channel == "trades"
                else f"orderbook?symbol={symbol}&type=step0&limit=5"
            )
            path = f"spot/market/{suffix}"
        else:
            suffix = (
                f"fills?symbol={symbol}&productType=USDT-FUTURES&limit=100"
                if key.channel == "trades"
                else f"merge-depth?symbol={symbol}&productType=USDT-FUTURES&precision=scale0&limit=5"
            )
            path = f"mix/market/{suffix}"
        return TransportRequest("GET", f"{self.rest_host}/api/v2/{path}")

    def decode(
        self, frame: RawFrame, instrument: Instrument, key: SubscriptionKey
    ) -> DecodeResult:
        try:
            self._validate(instrument, key)
            rest = (
                frame.endpoint_id == self.snapshot_request(instrument, key).endpoint_id
            )
            if frame.transport != ("rest" if rest else "ws"):
                raise ValueError("wrong_transport")
            if not rest and frame.endpoint_id != self.stream:
                raise ValueError("wrong_endpoint")
            if not rest and frame.payload == b"pong":
                return DecodeResult(controls=(Control("heartbeat"),))
            root = object_field(exact_json_loads(frame.payload))
            if rest:
                self._success(root)
                rows = root["data"]
                history = True
                if key.channel == "book":
                    data = object_field(rows)
                    if instrument.market_type == "perpetual" and (
                        data["precision"] != "scale0"
                        or decimal(data["scale"], positive=True) != instrument.tick_size
                    ):
                        raise ValueError("unexpected depth aggregation")
                    rows = [data]
            else:
                if "arg" in root and object_field(root["arg"]) != self._arg(
                    instrument, key
                ):
                    raise ValueError("wrong_subscription")
                if root.get("event") == "error":
                    return DecodeResult(
                        controls=(
                            Control(
                                "rejection",
                                text(root["code"]),
                                str(root.get("msg", ""))[:4096],
                            ),
                        )
                    )
                if root.get("event") == "subscribe":
                    if "arg" not in root or "code" in root:
                        raise ValueError("invalid subscription acknowledgement")
                    return DecodeResult(controls=(Control("ack"),))
                if root.get("arg") != self._arg(instrument, key):
                    raise ValueError("wrong_subscription")
                action = root["action"]
                if action not in ("snapshot", "update") or (
                    key.channel == "book" and action != "snapshot"
                ):
                    raise ValueError("invalid snapshot action")
                history = action == "snapshot"
                rows = root["data"]
            rows = array_field(rows)
            if key.channel == "book" and len(rows) != 1:
                raise ValueError("expected one book snapshot")
            events = tuple(
                self._book(object_field(row), instrument, key, rest=rest)
                if key.channel == "book"
                else self._trade(object_field(row), instrument, history=history)
                for row in rows
            )
            return DecodeResult(events)
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            return decode_failure(frame, exc)

    def _book(self, row, instrument, key, *, rest):
        if "instId" in row and row["instId"] != instrument.native_symbol:
            raise ValueError("wrong_symbol")
        changes = level_changes(row["bids"], row["asks"], instrument)
        for side in ("bid", "ask"):
            levels = [change for change in changes if change.side == side]
            if len(levels) > 5 or len({change.price for change in levels}) != len(
                levels
            ):
                raise ValueError("invalid snapshot depth/duplicate price")
            if any(change.quantity == 0 for change in levels):
                raise ValueError("zero snapshot quantity")
        return DecodedEvent(
            BookUpdate("snapshot", changes, 5, "new_snapshot"),
            sequence=SequenceInfo(
                f"{instrument.instrument_id}:books5",
                last=str(integer_field(row["seq"])),
            )
            if "seq" in row
            else None,
            exchange_event_wall_ns=integer_field(row["ts"]) * 1_000_000,
            exchange_time_kind="system" if rest else "matching_engine",
        )

    def _trade(self, row, instrument, *, history):
        for field in ("symbol", "instId"):
            if field in row and row[field] != instrument.native_symbol:
                raise ValueError("wrong_symbol")
        trade_id = text(row["tradeId"])
        price = decimal(row["price"], positive=True)
        size = decimal(row["size"], positive=True)
        instrument.validate_price(price)
        instrument.validate_quantity(size)
        side = row.get("side")
        if side is not None and not isinstance(side, str):
            raise ValueError("invalid trade side")
        if side is None or side not in ("buy", "sell"):
            side = "unknown"
        known = side != "unknown"
        return DecodedEvent(
            Trade(
                trade_id,
                f"{instrument.instrument_id}:trade:{trade_id}",
                price,
                size,
                taker_side=side,
                side_provenance="documented_taker" if known else "unknown",
                is_subscription_history=history,
            ),
            exchange_event_wall_ns=integer_field(row["ts"]) * 1_000_000,
            exchange_time_kind="trade",
        )

    def new_reducer(self, instrument: Instrument, key: SubscriptionKey) -> BookReducer:
        return BookReducer(
            instrument,
            key,
            capabilities=self.capabilities(instrument, key),
            strategy_id=f"bitget-{instrument.api_family}-books5-v1",
            adapter_revision="bitget-v1",
        )
