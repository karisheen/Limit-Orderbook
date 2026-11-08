"""OKX v5 public spot/linear swaps; bounded books5 snapshots, not full depth.

Protocol: https://www.okx.com/docs-v5/en/#order-book-trading-market-data
books5 replaces the complete observed five-level window on every message. It
has no incremental continuity/checksum guarantee. Native SWAP quantities remain
contracts; ctVal supplies the explicit base-currency valuation per contract.

Public WebSocket uses TLS port 443. Connection-wide notice envelopes are
informational controls, not subscription acknowledgements or market observations.
Notice protocol: https://www.okx.com/docs-v5/en/#overview-websocket-notification
"""

from decimal import Decimal, localcontext
from hashlib import sha256
import json
from time import time_ns
from urllib.parse import quote

from ..book import BookReducer
from ..events import (
    BookUpdate,
    Control,
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


CONTRACT_SPECIFICATION_SOURCE = (
    "https://www.okx.com/docs-v5/en/#public-data-rest-api-get-instruments"
)


def _snapshot_sequence(previous, incoming, snapshot):
    # Repeated IDs are idle snapshots; maintenance may reset IDs. A complete
    # snapshot needs no preceding sequence and can recover an invalidated book.
    return "accept" if incoming is None or snapshot else "gap"


def _snapshot_candidate(bids, asks, events):
    for event in events:
        if isinstance(event.payload, BookUpdate):
            if event.payload.kind != "snapshot":
                raise ValueError("books5_requires_snapshot")
            if event.sequence is None or event.sequence.last is None:
                raise ValueError("snapshot_sequence_missing")
    if len(bids) > 5 or len(asks) > 5:
        raise ValueError("snapshot_depth_exceeded")


class OKXAdapter:
    venue = "okx"
    api_family = "v5-spot"
    swap_api_family = "v5-swap"
    rest_host = "https://www.okx.com"
    stream = "wss://ws.okx.com:443/ws/v5/public"

    def _metadata_endpoint(self, inst_type):
        if inst_type not in ("SPOT", "SWAP"):
            raise ValueError("unsupported OKX instrument type")
        return f"{self.rest_host}/api/v5/public/instruments?instType={inst_type}"

    async def discover(self) -> MetadataSnapshot:
        from ..transport import fetch_public

        parts: list[bytes] = []
        instruments: list[Instrument] = []
        for inst_type in ("SPOT", "SWAP"):
            raw = await fetch_public(
                TransportRequest("GET", self._metadata_endpoint(inst_type))
            )
            parts.append(raw)
            instruments.extend(
                self.parse_metadata(
                    raw, inst_type=inst_type, fetch_wall_ns=time_ns()
                ).instruments
            )
        return metadata_snapshot(
            f"{self.rest_host}/api/v5/public/instruments",
            b"\x00".join(parts),
            time_ns(),
            instruments,
        )

    def parse_metadata(
        self, raw: bytes, *, inst_type: str = "SPOT", fetch_wall_ns: int = 0
    ) -> MetadataSnapshot:
        source = self._metadata_endpoint(inst_type)
        root = object_field(exact_json_loads(raw))
        if integer_field(root["code"]) != 0:
            raise ValueError(f"metadata endpoint error: {root['code']}")
        version = f"{sha256(raw).hexdigest()}:{fetch_wall_ns}:okx-v1"
        instruments = []
        for value in array_field(root["data"]):
            row = object_field(value)
            if row["instType"] != inst_type:
                raise ValueError("unexpected metadata instrument type")
            # RWA stock/commodity/FX contracts are not crypto instruments.
            if row.get("instCategory", "") not in ("", "1"):
                continue
            swap = inst_type == "SWAP"
            if swap and row["ctType"] == "inverse":
                continue
            symbol = text(row["instId"])
            multiplier = Decimal("1")
            if swap:
                if row["ctType"] != "linear":
                    raise ValueError("unsupported OKX contract type")
                family = text(row["instFamily"])
                currencies = family.split("-")
                if len(currencies) != 2 or symbol != f"{family}-SWAP":
                    raise ValueError("invalid linear contract identity")
                base_symbol, quote_symbol = currencies
                if row["ctValCcy"] != base_symbol or row["settleCcy"] != quote_symbol:
                    raise ValueError("unsupported linear contract denomination")
                # The documented linear notional is sz * ctVal * price:
                # ctVal already gives the complete base face value per contract.
                multiplier = decimal(row["ctVal"], positive=True)
            else:
                base_symbol, quote_symbol = text(row["baseCcy"]), text(row["quoteCcy"])
                if symbol != f"{base_symbol}-{quote_symbol}":
                    raise ValueError("invalid spot instrument identity")
            base = asset_identity(self.venue, base_symbol)
            currency = asset_identity(self.venue, quote_symbol)
            decimal(row["minSz"], positive=True)

            def base_constraint(field):
                if row.get(field) in (None, ""):
                    return None
                with localcontext(DECIMAL_CONTEXT):
                    return decimal(
                        decimal(row[field], nonnegative=True) * multiplier,
                        nonnegative=True,
                    )

            status = {
                "live": "active",
                "suspend": "suspended",
                "rebase": "suspended",
                "post_only": "unavailable",
                "preopen": "unavailable",
                "test": "unavailable",
            }.get(text(row["state"]), "unknown")
            family = self.swap_api_family if swap else self.api_family
            market = "perpetual" if swap else "spot"
            instruments.append(
                Instrument(
                    InstrumentId(f"{self.venue}:{family}:{market}:{symbol}"),
                    version,
                    self.venue,
                    family,
                    symbol,
                    base,
                    currency,
                    decimal(row["tickSz"], positive=True),
                    decimal(row["lotSz"], positive=True),
                    market_type=market,
                    settlement=currency if swap else None,
                    contract_multiplier=multiplier,
                    quantity_unit="contracts" if swap else "base",
                    contract_value_currency=base.asset_id if swap else None,
                    settlement_conversion="none" if swap else "unknown",
                    contract_specification_source=CONTRACT_SPECIFICATION_SOURCE
                    if swap
                    else None,
                    constraints=OrderConstraints(
                        min_base=base_constraint("minSz"),
                        max_limit_base=base_constraint("maxLmtSz"),
                        # Spot maxMktSz is quote-denominated, not base size.
                        max_market_base=base_constraint("maxMktSz") if swap else None,
                    ),
                    status=status,
                    metadata_asof_wall_ns=fetch_wall_ns,
                    metadata_source=source,
                )
            )
        return metadata_snapshot(source, raw, fetch_wall_ns, instruments)

    def _validate(self, instrument, key):
        expected_market = {
            self.api_family: "spot",
            self.swap_api_family: "perpetual",
        }.get(instrument.api_family)
        if (key.venue, key.api_family, key.instrument_id) != (
            self.venue,
            instrument.api_family,
            instrument.instrument_id,
        ) or instrument.venue != self.venue:
            raise ValueError("wrong_subscription")
        if expected_market is None or instrument.market_type != expected_market:
            raise ValueError("unsupported_market")
        if instrument.inverse or (
            expected_market == "perpetual"
            and (
                instrument.quantity_unit != "contracts"
                or instrument.contract_value_currency != instrument.base.asset_id
                or instrument.settlement != instrument.quote
                or not instrument.contract_specification_source
            )
        ):
            raise ValueError("unsupported_contract")
        if instrument.status != "active":
            raise ValueError(f"product_{instrument.status}")
        if key.channel not in ("book", "trades") or key.aggregation:
            raise ValueError("unsupported_subscription")
        if key.channel == "book" and key.depth != 5:
            raise ValueError("OKX books5 requires depth 5")
        if key.channel == "trades" and key.depth is not None:
            raise ValueError("trade subscription cannot request depth")

    def _argument(self, instrument, key):
        return {
            "channel": "books5" if key.channel == "book" else "trades",
            "instId": instrument.native_symbol,
        }

    def capabilities(
        self, instrument: Instrument, key: SubscriptionKey
    ) -> Capabilities:
        self._validate(instrument, key)
        book = key.channel == "book"
        return Capabilities(
            snapshot=book,
            trades=not book,
            sequence_scope=f"books5:{instrument.native_symbol}" if book else None,
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
                    {"op": "subscribe", "args": [self._argument(instrument, key)]},
                    separators=(",", ":"),
                ).encode(),
            ),
        )

    def snapshot_request(
        self, instrument: Instrument, key: SubscriptionKey
    ) -> TransportRequest:
        self._validate(instrument, key)
        symbol = quote(instrument.native_symbol, safe="")
        suffix = (
            f"books?instId={symbol}&sz=5"
            if key.channel == "book"
            else f"trades?instId={symbol}&limit=100"
        )
        return TransportRequest("GET", f"{self.rest_host}/api/v5/market/{suffix}")

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
                if integer_field(root["code"]) != 0:
                    raise ValueError(f"endpoint_error:{root['code']}")
            else:
                if root.get("event") == "error":
                    code = text(root["code"])
                    detail = root.get("msg", "")
                    if not isinstance(detail, str):
                        raise ValueError("invalid rejection detail")
                    return DecodeResult(
                        controls=(
                            Control(
                                "rejection",
                                f"subscription_rejected:{code}",
                                detail[:4096],
                            ),
                        )
                    )
                if root.get("event") == "notice":
                    code = text(root["code"])
                    detail = root["msg"]
                    if not isinstance(detail, str):
                        raise ValueError("invalid notice detail")
                    # clock_tick is the existing informational-only control:
                    # no acknowledgement, heartbeat, or market observation.
                    return DecodeResult(
                        controls=(
                            Control(
                                "clock_tick", f"venue_notice:{code}", detail[:4096]
                            ),
                        )
                    )
                argument = object_field(root["arg"])
                if any(
                    argument.get(name) != value
                    for name, value in self._argument(instrument, key).items()
                ):
                    raise ValueError("wrong_topic")
                if "event" in root:
                    if root["event"] != "subscribe" or root.get("code", "0") != "0":
                        raise ValueError("invalid subscription acknowledgement")
                    return DecodeResult(controls=(Control("ack"),))
            rows = array_field(root["data"])
            if key.channel == "book":
                if len(rows) != 1:
                    raise ValueError("invalid snapshot count")
                if root.get("action", "snapshot") != "snapshot":
                    raise ValueError("books5_requires_snapshot")
                return DecodeResult((self._book(object_field(rows[0]), instrument),))
            if rest and len(rows) > 100:
                raise ValueError("trade_batch_exceeded")
            return DecodeResult(
                tuple(
                    self._trade(object_field(row), instrument, rest=rest)
                    for row in rows
                )
            )
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            return decode_failure(frame, exc)

    def _book(self, row, instrument):
        if "instId" in row and row["instId"] != instrument.native_symbol:
            raise ValueError("wrong_symbol")
        changes = []
        for side, field in (("bid", "bids"), ("ask", "asks")):
            levels = array_field(row[field])
            if len(levels) > 5:
                raise ValueError("snapshot_depth_exceeded")
            for level in levels:
                if not isinstance(level, list) or len(level) != 4:
                    raise ValueError("invalid book level")
                price, quantity = (
                    decimal(level[0], positive=True),
                    decimal(level[1], positive=True),
                )
                # The third field is deprecated; fourth is the order count.
                integer_field(level[2])
                integer_field(level[3])
                instrument.validate_price(price)
                instrument.validate_quantity(quantity)
                changes.append(LevelChange(side, price, quantity))
        return DecodedEvent(
            BookUpdate("snapshot", tuple(changes), 5, "new_snapshot"),
            SequenceInfo(
                f"books5:{instrument.native_symbol}",
                last=str(integer_field(row["seqId"])),
            ),
            exchange_event_wall_ns=integer_field(row["ts"]) * 1_000_000,
            exchange_time_kind="book_generation",
        )

    def _trade(self, row, instrument, *, rest):
        if row["instId"] != instrument.native_symbol:
            raise ValueError("wrong_symbol")
        trade_id = text(row["tradeId"])
        price, quantity = (
            decimal(row["px"], positive=True),
            decimal(row["sz"], positive=True),
        )
        instrument.validate_price(price)
        instrument.validate_quantity(quantity)
        side = row.get("side")
        if side is not None and not isinstance(side, str):
            raise ValueError("invalid trade side")
        if side is None or side not in ("buy", "sell"):
            side = "unknown"
        known = side != "unknown"
        if "count" in row and integer_field(row["count"]) == 0:
            raise ValueError("invalid aggregate trade count")
        return DecodedEvent(
            Trade(
                trade_id,
                f"{instrument.instrument_id}:trade:{trade_id}",
                price,
                quantity,
                quantity_unit=instrument.quantity_unit,
                taker_side=side,
                side_provenance="documented_taker" if known else "unknown",
                is_subscription_history=rest,
            ),
            sequence=SequenceInfo(
                f"trades:{instrument.native_symbol}",
                last=str(integer_field(row["seqId"])),
            )
            if "seqId" in row
            else None,
            exchange_event_wall_ns=integer_field(row["ts"]) * 1_000_000,
            exchange_time_kind="trade",
        )

    def new_reducer(self, instrument: Instrument, key: SubscriptionKey) -> BookReducer:
        return BookReducer(
            instrument,
            key,
            capabilities=self.capabilities(instrument, key),
            max_levels=5,
            sequence_validator=_snapshot_sequence if key.channel == "book" else None,
            candidate_validator=_snapshot_candidate if key.channel == "book" else None,
            strategy_id="okx-v5-books5-v1",
            adapter_revision="okx-v1",
        )
