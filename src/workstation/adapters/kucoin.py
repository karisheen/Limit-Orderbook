"""KuCoin classic public REST: bounded snapshots and recent trade observations."""

from hashlib import sha256
from time import time_ns
from urllib.parse import quote

from ..book import BookReducer
from ..events import BookUpdate, RawFrame, SequenceInfo, SubscriptionKey, Trade
from ..instruments import Instrument, InstrumentId, MetadataSnapshot, OrderConstraints
from ..precision import decimal, exact_json_loads, text
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
    validate_key,
)


class KuCoinAdapter:
    venue = "kucoin"
    api_family = "classic-spot"
    rest_host = "https://api.kucoin.com"

    async def discover(self) -> MetadataSnapshot:
        from ..transport import fetch_public

        raw = await fetch_public(
            TransportRequest("GET", f"{self.rest_host}/api/v2/symbols")
        )
        return self.parse_metadata(raw, fetch_wall_ns=time_ns())

    def parse_metadata(self, raw: bytes, *, fetch_wall_ns: int = 0) -> MetadataSnapshot:
        root = object_field(exact_json_loads(raw))
        if root["code"] != "200000":
            raise ValueError(f"metadata endpoint error: {root['code']}")
        source = f"{self.rest_host}/api/v2/symbols"
        version = f"{sha256(raw).hexdigest()}:{fetch_wall_ns}"
        instruments = []
        for value in array_field(root["data"]):
            row = object_field(value)
            symbol = text(row["symbol"])
            base, currency = (
                asset_identity(self.venue, row["baseCurrency"]),
                asset_identity(self.venue, row["quoteCurrency"]),
            )
            enabled = row["enableTrading"]
            if type(enabled) is not bool:
                raise ValueError("invalid trading status")
            instruments.append(
                Instrument(
                    InstrumentId(f"{self.venue}:{self.api_family}:spot:{symbol}"),
                    version,
                    self.venue,
                    self.api_family,
                    symbol,
                    base,
                    currency,
                    decimal(row["priceIncrement"], positive=True),
                    decimal(row["baseIncrement"], positive=True),
                    constraints=OrderConstraints(
                        min_base=optional_decimal(row, "baseMinSize"),
                        max_base=optional_decimal(row, "baseMaxSize"),
                        min_notional=optional_decimal(row, "minFunds"),
                        min_notional_currency=currency.asset_id,
                    ),
                    status="active" if enabled else "suspended",
                    metadata_asof_wall_ns=fetch_wall_ns,
                    metadata_source=source,
                )
            )
        return metadata_snapshot(source, raw, fetch_wall_ns, instruments)

    def _validate(self, instrument, key):
        validate_key(instrument, key, self.venue, self.api_family)
        if key.channel == "book" and key.depth not in (20, 100):
            raise ValueError("unsupported KuCoin public snapshot depth")

    def capabilities(
        self, instrument: Instrument, key: SubscriptionKey
    ) -> Capabilities:
        self._validate(instrument, key)
        # A response sequence is not proof of continuity between polls.
        return Capabilities(
            snapshot=key.channel == "book", trades=key.channel == "trades"
        )

    def ws_endpoint(self, instrument: Instrument, key: SubscriptionKey) -> None:
        self._validate(instrument, key)
        return None

    def subscription_requests(
        self, instrument: Instrument, key: SubscriptionKey
    ) -> tuple[TransportRequest, ...]:
        self._validate(instrument, key)
        return ()

    def snapshot_request(
        self, instrument: Instrument, key: SubscriptionKey
    ) -> TransportRequest:
        self._validate(instrument, key)
        symbol = quote(instrument.native_symbol, safe="")
        path = (
            "histories" if key.channel == "trades" else f"orderbook/level2_{key.depth}"
        )
        return TransportRequest(
            "GET", f"{self.rest_host}/api/v1/market/{path}?symbol={symbol}"
        )

    def decode(
        self, frame: RawFrame, instrument: Instrument, key: SubscriptionKey
    ) -> DecodeResult:
        try:
            self._validate(instrument, key)
            if frame.transport != "rest":
                raise ValueError("wrong_transport")
            if frame.endpoint_id != self.snapshot_request(instrument, key).endpoint_id:
                raise ValueError("wrong_endpoint")
            root = object_field(exact_json_loads(frame.payload))
            if root["code"] != "200000":
                raise ValueError(f"endpoint_error:{root['code']}")
            if key.channel == "trades":
                events = []
                for value in array_field(root["data"]):
                    row = object_field(value)
                    if "symbol" in row and row["symbol"] != instrument.native_symbol:
                        raise ValueError("wrong_symbol")
                    sequence = str(integer_field(row["sequence"]))
                    price, size = (
                        decimal(row["price"], positive=True),
                        decimal(row["size"], positive=True),
                    )
                    instrument.validate_price(price)
                    instrument.validate_quantity(size)
                    # Classic REST documents a side but does not establish that
                    # it is aggressor side. Do not infer from price or maker data.
                    if "side" in row and row["side"] not in (
                        "buy",
                        "sell",
                        "unknown",
                        None,
                    ):
                        raise ValueError("invalid trade side")
                    events.append(
                        DecodedEvent(
                            Trade(
                                None,
                                f"{instrument.instrument_id}:trade-sequence:{sequence}",
                                price,
                                size,
                                taker_side="unknown",
                                side_provenance="rest_side_semantics_unverified",
                                is_subscription_history=True,
                            ),
                            exchange_event_wall_ns=integer_field(row["time"]),
                            exchange_time_kind="trade",
                        )
                    )
                return DecodeResult(tuple(events))
            data = object_field(root["data"])
            if "symbol" in data and data["symbol"] != instrument.native_symbol:
                raise ValueError("wrong_symbol")
            changes = level_changes(data["bids"], data["asks"], instrument)
            if key.depth is None:
                raise ValueError("missing subscription depth")
            if any(
                sum(change.side == side for change in changes) > key.depth
                for side in ("bid", "ask")
            ):
                raise ValueError("snapshot_depth_exceeded")
            timestamp = (
                integer_field(data["time"]) * 1_000_000 if "time" in data else None
            )
            return DecodeResult(
                (
                    DecodedEvent(
                        BookUpdate("snapshot", changes, key.depth),
                        SequenceInfo(
                            f"{instrument.instrument_id}:snapshot",
                            last=str(integer_field(data["sequence"])),
                        ),
                        exchange_event_wall_ns=timestamp,
                        exchange_time_kind="snapshot"
                        if timestamp is not None
                        else None,
                    ),
                )
            )
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            return decode_failure(frame, exc)

    def new_reducer(self, instrument: Instrument, key: SubscriptionKey) -> BookReducer:
        return BookReducer(
            instrument,
            key,
            capabilities=self.capabilities(instrument, key),
            strategy_id="kucoin-rest-snapshot-v1",
            adapter_revision="kucoin-v1",
        )
