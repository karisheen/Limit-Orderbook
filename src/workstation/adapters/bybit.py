"""Bybit v5 spot and linear USDT perpetual books; IDs are not contiguous."""

from hashlib import sha256
import json
from time import time_ns
from urllib.parse import quote

from ..book import BookReducer
from ..events import BookUpdate, Control, RawFrame, SequenceInfo, SubscriptionKey, Trade
from ..instruments import Instrument, InstrumentId, MetadataSnapshot, OrderConstraints
from ..precision import decimal, exact_json_loads, text
from ..quality import Capabilities
from .base import DecodedEvent, DecodeResult, TransportRequest
from .binance import (
    array_field,
    asset_identity,
    decode_failure,
    discover_metadata_families,
    integer_field,
    level_changes,
    metadata_snapshot,
    object_field,
    optional_decimal,
    validate_key,
)


def bybit_sequence(previous, incoming, snapshot):
    if incoming is None:
        return "accept"
    if incoming.last is None or incoming.first is None:
        return "gap"
    if snapshot or previous is None:
        return "accept"
    if (
        incoming.scope != previous.scope
        or previous.last is None
        or previous.first is None
    ):
        return "gap"
    # Cross sequence is a generation-order check, not a contiguous counter.
    return (
        "accept"
        if int(incoming.last) > int(previous.last)
        and int(incoming.first) > int(previous.first)
        else "ignore"
    )


class BybitAdapter:
    venue = "bybit"
    api_family = "v5-spot"
    # Both mainnet hosts are official; neither bypasses regional restrictions.
    # https://bybit-exchange.github.io/docs/v5/guide
    rest_host = "https://api.bytick.com"
    stream = "wss://stream.bybit.com/v5/public/spot"
    linear_api_family = "v5-linear"
    linear_stream = "wss://stream.bybit.com/v5/public/linear"
    MAX_METADATA_PAGES = 32
    metadata_families = ("v5-spot", "v5-linear")
    discovery_evidence: tuple[tuple[str, str, str], ...] = ()

    async def discover(self) -> MetadataSnapshot:
        return await self.discover_metadata()

    async def discover_metadata(self, *, timeout=30) -> MetadataSnapshot:
        return await discover_metadata_families(self, timeout=timeout)

    async def discover_family(self, family) -> MetadataSnapshot:
        from ..transport import fetch_public

        if family == self.api_family:
            raw = await fetch_public(
                TransportRequest(
                    "GET", f"{self.rest_host}/v5/market/instruments-info?category=spot"
                )
            )
            snapshot, _ = self._metadata_page(
                raw, fetch_wall_ns=time_ns(), expected_category="spot"
            )
            return snapshot
        if family != self.linear_api_family:
            raise ValueError("unsupported metadata API family")
        fetched = time_ns()
        raws, instruments = [], []
        cursor = ""
        seen = set()
        for _ in range(self.MAX_METADATA_PAGES):
            endpoint = f"{self.rest_host}/v5/market/instruments-info?category=linear&limit=1000"
            if cursor:
                endpoint += f"&cursor={quote(cursor, safe='')}"
            raw = await fetch_public(TransportRequest("GET", endpoint))
            page, cursor = self._metadata_page(
                raw, fetch_wall_ns=fetched, expected_category="linear"
            )
            raws.append(raw)
            instruments.extend(page.instruments)
            if not cursor:
                return metadata_snapshot(
                    page.source,
                    b"\n".join(raws),
                    fetched,
                    instruments,
                )
            if cursor in seen:
                raise ValueError("repeated metadata pagination cursor")
            seen.add(cursor)
        raise ValueError("metadata pagination limit exceeded")

    def parse_metadata(self, raw: bytes, *, fetch_wall_ns: int = 0) -> MetadataSnapshot:
        snapshot, cursor = self._metadata_page(raw, fetch_wall_ns=fetch_wall_ns)
        if cursor:
            raise ValueError("incomplete paginated metadata")
        return snapshot

    def _metadata_page(
        self, raw: bytes, *, fetch_wall_ns: int, expected_category: str | None = None
    ):
        root = object_field(exact_json_loads(raw))
        if integer_field(root["retCode"]) != 0:
            raise ValueError(f"metadata endpoint error: {root['retCode']}")
        result = object_field(root["result"])
        category = result["category"]
        if category not in ("spot", "linear"):
            raise ValueError("unexpected metadata category")
        if expected_category is not None and category != expected_category:
            raise ValueError("unexpected metadata category")
        cursor = result.get("nextPageCursor", "")
        if not isinstance(cursor, str) or len(cursor) > 4096:
            raise ValueError("invalid metadata pagination cursor")
        if category == "spot" and cursor:
            raise ValueError("unexpected spot metadata category/pagination")
        source = f"{self.rest_host}/v5/market/instruments-info?category={category}"
        linear = category == "linear"
        family = self.linear_api_family if linear else self.api_family
        market_type = "perpetual" if linear else "spot"
        version = f"{sha256(raw).hexdigest()}:{fetch_wall_ns}"
        instruments = []
        for value in array_field(result["list"]):
            row = object_field(value)
            if linear:
                # Linear also lists TradFi perpetuals, not just crypto contracts.
                if row.get("symbolType") in ("stock", "ETF", "commodity", "forex"):
                    continue
                if (
                    row["contractType"] != "LinearPerpetual"
                    or row["quoteCoin"] != "USDT"
                ):
                    continue
                if row["settleCoin"] != "USDT":
                    raise ValueError("unexpected linear settlement asset")
            symbol = text(row["symbol"])
            base, currency = (
                asset_identity(self.venue, row["baseCoin"]),
                asset_identity(self.venue, row["quoteCoin"]),
            )
            price, lot = (
                object_field(row["priceFilter"]),
                object_field(row["lotSizeFilter"]),
            )
            status = {
                "Trading": "active",
                "Halt": "suspended",
                "Suspended": "suspended",
                "Delisted": "delisted",
                "Closed": "delisted",
                "PreLaunch": "unavailable",
                "PendingOpen": "unavailable",
            }.get(text(row["status"]), "unknown")
            # Spot's deprecated quantity fields are not order constraints.
            # Derivative qtyStep and order-type limits remain authoritative.
            instruments.append(
                Instrument(
                    InstrumentId(f"{self.venue}:{family}:{market_type}:{symbol}"),
                    version,
                    self.venue,
                    family,
                    symbol,
                    base,
                    currency,
                    decimal(price["tickSize"], positive=True),
                    decimal(
                        lot["qtyStep" if linear else "basePrecision"], positive=True
                    ),
                    settlement=asset_identity(self.venue, row["settleCoin"])
                    if linear
                    else None,
                    market_type=market_type,
                    quantity_unit="base",
                    contract_multiplier=decimal("1"),
                    contract_value_currency=base.asset_id if linear else None,
                    settlement_conversion="none" if linear else "unknown",
                    contract_specification_source=source if linear else None,
                    native_asset_id=str(integer_field(row["symbolId"]))
                    if "symbolId" in row
                    else None,
                    reference_multiplier=decimal(
                        row.get("xstockMultiplier", "1"), positive=True
                    )
                    if not linear and row.get("symbolType") == "xstocks"
                    else None,
                    constraints=OrderConstraints(
                        min_base=optional_decimal(lot, "minOrderQty")
                        if linear
                        else None,
                        max_limit_base=optional_decimal(
                            lot, "maxOrderQty" if linear else "maxLimitOrderQty"
                        ),
                        max_market_base=optional_decimal(
                            lot, "maxMktOrderQty" if linear else "maxMarketOrderQty"
                        ),
                        min_notional=decimal(
                            lot["minNotionalValue" if linear else "minOrderAmt"],
                            nonnegative=True,
                        ),
                        min_notional_currency=currency.asset_id,
                    ),
                    status=status,
                    metadata_asof_wall_ns=fetch_wall_ns,
                    metadata_source=source,
                )
            )
        return metadata_snapshot(source, raw, fetch_wall_ns, instruments), cursor

    def _validate(self, instrument, key):
        linear = instrument.api_family == self.linear_api_family
        validate_key(
            instrument,
            key,
            self.venue,
            self.linear_api_family if linear else self.api_family,
            market_type="perpetual" if linear else "spot",
        )
        if key.channel == "book" and key.depth not in (1, 50, 200, 1000):
            raise ValueError("unsupported Bybit depth")

    def _topic(self, instrument, key):
        return (
            f"publicTrade.{instrument.native_symbol}"
            if key.channel == "trades"
            else f"orderbook.{key.depth}.{instrument.native_symbol}"
        )

    def capabilities(
        self, instrument: Instrument, key: SubscriptionKey
    ) -> Capabilities:
        self._validate(instrument, key)
        book = key.channel == "book"
        return Capabilities(
            snapshot=book,
            deltas=book and key.depth != 1,
            trades=not book,
            sequence_scope=self._topic(instrument, key) if book else None,
            exact_event_flow=False,
        )

    def ws_endpoint(self, instrument: Instrument, key: SubscriptionKey) -> str:
        self._validate(instrument, key)
        return (
            self.linear_stream
            if instrument.api_family == self.linear_api_family
            else self.stream
        )

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
                        "op": "subscribe",
                        "args": [self._topic(instrument, key)],
                        "req_id": "market",
                    },
                    separators=(",", ":"),
                ).encode(),
            ),
        )

    def snapshot_request(
        self, instrument: Instrument, key: SubscriptionKey
    ) -> TransportRequest:
        self._validate(instrument, key)
        symbol = quote(instrument.native_symbol, safe="")
        category = (
            "linear" if instrument.api_family == self.linear_api_family else "spot"
        )
        suffix = (
            f"recent-trade?category={category}&symbol={symbol}&limit=60"
            if key.channel == "trades"
            else f"orderbook?category={category}&symbol={symbol}&limit={key.depth}"
        )
        return TransportRequest("GET", f"{self.rest_host}/v5/market/{suffix}")

    def decode(
        self, frame: RawFrame, instrument: Instrument, key: SubscriptionKey
    ) -> DecodeResult:
        try:
            self._validate(instrument, key)
            root = object_field(exact_json_loads(frame.payload))
            rest = (
                frame.endpoint_id == self.snapshot_request(instrument, key).endpoint_id
            )
            if frame.transport != ("rest" if rest else "ws"):
                raise ValueError("wrong_transport")
            if not rest and frame.endpoint_id != self.ws_endpoint(instrument, key):
                raise ValueError("wrong_endpoint")
            if rest:
                if integer_field(root["retCode"]) != 0:
                    raise ValueError(f"endpoint_error:{root['retCode']}")
                data = object_field(root["result"])
                if key.channel == "trades":
                    category = (
                        "linear"
                        if instrument.api_family == self.linear_api_family
                        else "spot"
                    )
                    if data["category"] != category:
                        raise ValueError("wrong_category")
                    return DecodeResult(
                        tuple(
                            self._trade(object_field(row), instrument, rest=True)
                            for row in array_field(data["list"])
                        )
                    )
                kind = "snapshot"
                timestamp = data.get("cts", data.get("ts"))
            else:
                if root.get("op") in ("pong", "ping"):
                    if root.get("op") == "ping" and root.get("ret_msg") != "pong":
                        raise ValueError("invalid heartbeat")
                    return DecodeResult(controls=(Control("heartbeat"),))
                if root.get("op") == "subscribe":
                    if type(root.get("success")) is not bool:
                        raise ValueError("invalid subscription acknowledgement")
                    return DecodeResult(
                        controls=(
                            Control("ack")
                            if root["success"]
                            else Control(
                                "rejection",
                                "subscription_rejected",
                                str(root.get("ret_msg", ""))[:4096],
                            ),
                        )
                    )
                if root.get("topic") != self._topic(instrument, key):
                    raise ValueError("wrong_topic")
                if key.channel == "trades":
                    if root.get("type") != "snapshot":
                        raise ValueError("invalid trade message type")
                    return DecodeResult(
                        tuple(
                            self._trade(object_field(row), instrument)
                            for row in array_field(root["data"])
                        )
                    )
                data = object_field(root["data"])
                kind = root["type"]
                if kind not in ("snapshot", "delta"):
                    raise ValueError("invalid book message type")
                timestamp = root.get("cts", root.get("ts"))
            if data["s"] != instrument.native_symbol:
                raise ValueError("wrong_symbol")
            update_id, cross_sequence = (
                integer_field(data["u"]),
                integer_field(data["seq"]),
            )
            if update_id == 1:
                kind = "snapshot"
            if key.depth == 1 and kind != "snapshot":
                raise ValueError("level1_requires_snapshot")
            changes = level_changes(data["b"], data["a"], instrument)
            if key.depth is None:
                raise ValueError("missing subscription depth")
            if kind == "snapshot" and any(
                sum(change.side == side for change in changes) > key.depth
                for side in ("bid", "ask")
            ):
                raise ValueError("snapshot_depth_exceeded")
            event = DecodedEvent(
                BookUpdate(
                    kind,
                    changes,
                    key.depth,
                    "service_reset"
                    if update_id == 1
                    else "new_snapshot"
                    if kind == "snapshot"
                    else None,
                ),
                SequenceInfo(
                    self._topic(instrument, key),
                    first=str(cross_sequence),
                    last=str(update_id),
                ),
                exchange_event_wall_ns=integer_field(timestamp) * 1_000_000
                if timestamp is not None
                else None,
                exchange_time_kind=(
                    "matching_engine"
                    if ("cts" in data if rest else "cts" in root)
                    else "system"
                )
                if timestamp is not None
                else None,
            )
            return DecodeResult((event,))
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            return decode_failure(frame, exc)

    def _trade(self, row, instrument, *, rest=False):
        if row["symbol" if rest else "s"] != instrument.native_symbol:
            raise ValueError("wrong_symbol")
        trade_id = text(row["execId" if rest else "i"])
        side = row.get("side" if rest else "S")
        if side is not None and not isinstance(side, str):
            raise ValueError("invalid trade side")
        price, size = (
            decimal(row["price" if rest else "p"], positive=True),
            decimal(row["size" if rest else "v"], positive=True),
        )
        instrument.validate_price(price)
        instrument.validate_quantity(size)
        known = side in ("Buy", "Sell")
        return DecodedEvent(
            Trade(
                trade_id,
                f"{instrument.instrument_id}:trade:{trade_id}",
                price,
                size,
                taker_side=side.lower()
                if isinstance(side, str) and known
                else "unknown",
                side_provenance="documented_taker" if known else "unknown",
                is_subscription_history=rest,
            ),
            exchange_event_wall_ns=integer_field(row["time" if rest else "T"])
            * 1_000_000,
            exchange_time_kind="trade",
        )

    def new_reducer(self, instrument: Instrument, key: SubscriptionKey) -> BookReducer:
        return BookReducer(
            instrument,
            key,
            capabilities=self.capabilities(instrument, key),
            truncate_depth=True,
            sequence_validator=bybit_sequence if key.channel == "book" else None,
            strategy_id="bybit-v5-linear-v1"
            if instrument.api_family == self.linear_api_family
            else "bybit-v5-spot-v1",
            adapter_revision="bybit-linear-v1"
            if instrument.api_family == self.linear_api_family
            else "bybit-v1",
        )
