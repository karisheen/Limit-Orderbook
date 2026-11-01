"""Public HyperCore metadata, bounded books, trades and non-executable context."""

from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
import json
from time import time_ns

from ..book import BookReducer
from ..events import (
    AssetContext,
    BookUpdate,
    Control,
    LevelChange,
    RawFrame,
    ReferencePrice,
    SegmentId,
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
from ..quality import Capabilities, Coverage
from ..transport import fetch_public
from .base import DecodeError, DecodedEvent, DecodeResult, TransportRequest

INFO_URL = "https://api.hyperliquid.xyz/info"
WS_URL = "wss://api.hyperliquid.xyz/ws"
TRADE_SIDE_SOURCE = "https://www.dwellir.com/docs/hyperliquid/trades#response-fields"
CONTRACT_SPECIFICATION_SOURCE = (
    "https://hyperliquid.gitbook.io/hyperliquid-docs/trading/contract-specifications"
)
BUILDER_SPECIFICATION_SOURCE = (
    "https://hyperliquid.gitbook.io/hyperliquid-docs/"
    "hyperliquid-improvement-proposals-hips/hip-3-builder-deployed-perpetuals"
)
MONETARY_RULE_REVISION = "hyperliquid-denomination-v2"
FUNDING_INTERVAL_NS = 3_600_000_000_000
_CHANNELS = {
    "book": "l2Book",
    "trades": "trades",
    "reference": "allMids",
    "context": "activeAssetCtx",
}


def _object(value):
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


def _array(value):
    if not isinstance(value, list):
        raise ValueError("expected JSON array")
    return value


def _request(kind: str, **options) -> TransportRequest:
    return TransportRequest(
        "POST",
        INFO_URL,
        json.dumps({"type": kind, **options}, separators=(",", ":")).encode(),
        (("Content-Type", "application/json"),),
    )


def _size_decimals(value, maximum: int) -> int:
    count = nonnegative_int(value, "szDecimals")
    if count > maximum:
        raise ValueError("szDecimals exceeds market precision")
    return count


def validate_price(instrument: Instrument, price: Decimal) -> None:
    """Validate without normalize()/rounding under the caller's Decimal context."""
    value = decimal(price, positive=True)
    parts = value.as_tuple()
    digits, exponent = list(parts.digits), int(parts.exponent)
    while digits[-1] == 0:
        digits.pop()
        exponent += 1
    if exponent >= 0:  # Integer exception, even above five significant digits.
        return
    size_exponent = int(instrument.lot_size.as_tuple().exponent)
    max_decimals = (8 if instrument.market_type == "spot" else 6) + size_exponent
    if len(digits) > 5 or -exponent > max_decimals:
        raise ValueError("hyperliquid_variable_price_rule")


def _book_options(key: SubscriptionKey) -> tuple[int, dict]:
    options = dict(key.aggregation)
    if options.keys() - {"fast", "nSigFigs", "mantissa"}:
        raise ValueError("unsupported Hyperliquid aggregation")
    fast = options.get("fast", "false")
    if fast not in ("true", "false"):
        raise ValueError("fast must be true or false")
    depth = 5 if fast == "true" else 20
    if key.depth not in (None, depth):
        raise ValueError(
            "Hyperliquid depth must match slow20 or explicitly selected fast5"
        )
    wire: dict = {}
    if "fast" in options:
        wire["fast"] = fast == "true"
    if "nSigFigs" in options:
        if options["nSigFigs"] not in ("2", "3", "4", "5"):
            raise ValueError("unsupported nSigFigs")
        wire["nSigFigs"] = int(options["nSigFigs"])
    if "mantissa" in options:
        if wire.get("nSigFigs") != 5 or options["mantissa"] not in ("1", "2", "5"):
            raise ValueError("mantissa requires nSigFigs=5 and 1,2,5")
        wire["mantissa"] = int(options["mantissa"])
    return depth, wire


class HyperliquidBookReducer(BookReducer):
    """Core checkpoint/recovery semantics with explicit bounded source coverage."""

    def reset(self, epoch: int, segment_id: SegmentId) -> None:
        super().reset(epoch, segment_id)
        depth, options = _book_options(self.key)
        aggregation = dict(self.key.aggregation)
        aggregation.setdefault("fast", "false")
        self.quality = replace(
            self.quality,
            coverage=Coverage(
                subscribed_depth=depth,
                source_aggregation=tuple(sorted(aggregation.items())),
                # Official slow feed is block-triggered, at least 0.5s apart, not an SLA.
                expected_cadence_ns=None if options.get("fast") else 500_000_000,
            ),
        )


class HyperliquidAdapter:
    def __init__(self, *, dex: str = ""):
        if dex:
            text(dex)
            if ":" in dex:
                raise ValueError("DEX name must not contain namespace separator")
        self.dex = dex

    async def discover(self) -> MetadataSnapshot:
        raw_spot = await fetch_public(_request("spotMeta"))
        raw_meta = await fetch_public(
            _request("meta", **({"dex": self.dex} if self.dex else {}))
        )
        raw_parts = [raw_spot, raw_meta]
        spot, meta = (
            _object(exact_json_loads(raw_spot)),
            _object(exact_json_loads(raw_meta)),
        )
        # Older meta responses omit collateralToken; obtain it, never guess collateral.
        if "collateralToken" not in meta:
            raw_context = await fetch_public(
                _request("metaAndAssetCtxs", **({"dex": self.dex} if self.dex else {}))
            )
            raw_parts.append(raw_context)
            contexts = _array(exact_json_loads(raw_context))
            if len(contexts) != 2:
                raise ValueError("invalid metadata/context response")
            meta = _object(contexts[0])
        dex_index = 0
        if self.dex:
            raw_dex = await fetch_public(_request("perpDexs"))
            raw_parts.append(raw_dex)
            matches = [
                i
                for i, item in enumerate(_array(exact_json_loads(raw_dex)))
                if isinstance(item, dict) and item.get("name") == self.dex
            ]
            if len(matches) != 1 or matches[0] == 0:
                raise ValueError("unresolved DEX index")
            dex_index = matches[0]
        digest = sha256(b"\x00".join(raw_parts)).hexdigest()
        return self.parse_metadata(
            meta,
            spot,
            fetch_wall_ns=time_ns(),
            payload_hash=digest,
            dex_index=dex_index,
        )

    def parse_metadata(
        self,
        meta: dict,
        spot: dict,
        *,
        fetch_wall_ns: int,
        payload_hash: str,
        dex_index: int = 0,
    ) -> MetadataSnapshot:
        """Normalize already exact-decoded Info metadata; useful for offline replay."""
        nonnegative_int(dex_index)
        if bool(self.dex) != bool(dex_index):
            raise ValueError("DEX namespace/index mismatch")
        tokens: dict[int, tuple[AssetIdentity, int]] = {}
        for raw_token in _array(spot["tokens"]):
            token = _object(raw_token)
            index = nonnegative_int(token["index"])
            token_id = text(token["tokenId"])
            if index in tokens:
                raise ValueError("duplicate native token index")
            contract = token.get("evmContract")
            address = _object(contract).get("address") if contract is not None else None
            identity = AssetIdentity(
                AssetId(f"hyperliquid:hypercore:token:{token_id}"),
                text(token["name"]),
                native_token_id=str(index),
                network="hypercore",
                contract_address=address,
            )
            tokens[index] = identity, _size_decimals(token["szDecimals"], 8)
        collateral = tokens[nonnegative_int(meta["collateralToken"])][0]
        instruments = []
        seen_symbols: set[str] = set()
        for index, raw_asset in enumerate(_array(meta["universe"])):
            asset = _object(raw_asset)
            name = text(asset["name"])
            namespace = name.split(":", 1)[0] if ":" in name else ""
            if namespace != self.dex or name in seen_symbols:
                raise ValueError("invalid perpetual namespace or duplicate symbol")
            seen_symbols.add(name)
            size = _size_decimals(asset["szDecimals"], 6)
            native_id = index if not self.dex else 100_000 + dex_index * 10_000 + index
            base_id = (
                name if not self.dex else f"hyperliquid:perpetual-reference:{name}"
            )
            base = AssetIdentity(AssetId(base_id), name)
            # HIP-3 leaves oracle/contract definition to its deployer. Collateral
            # metadata alone cannot establish a builder's price denomination.
            quote_ticker = "USDC" if name in {"HYPE", "PURR"} else "USDT"
            quote = (
                AssetIdentity(AssetId(f"hyperliquid:unknown-quote:{name}"), "UNKNOWN")
                if self.dex
                else AssetIdentity(AssetId(quote_ticker), quote_ticker)
            )
            lot = Decimal(f"1e-{size}")
            instruments.append(
                Instrument(
                    InstrumentId(f"hyperliquid:info:perpetual:{name}"),
                    f"{payload_hash}:{fetch_wall_ns}:{MONETARY_RULE_REVISION}",
                    "hyperliquid",
                    "info",
                    name,
                    base,
                    quote,
                    None,
                    lot,
                    native_asset_id=str(native_id),
                    dex_namespace=self.dex or "default",
                    settlement=collateral,
                    market_type="perpetual",
                    constraints=OrderConstraints(
                        min_base=lot,
                        price_rule="hyperliquid_significant_digits",
                    ),
                    contract_value_currency=None if self.dex else base.asset_id,
                    settlement_conversion=(
                        "unknown"
                        if self.dex
                        else "none"
                        if quote_ticker == "USDC"
                        else "quanto_no_fx"
                    ),
                    contract_specification_source=(
                        BUILDER_SPECIFICATION_SOURCE
                        if self.dex
                        else CONTRACT_SPECIFICATION_SOURCE
                    ),
                    status="delisted" if asset.get("isDelisted", False) else "active",
                    data_access="unsupported" if self.dex else "unknown",
                    execution_access="unsupported" if self.dex else "unknown",
                    metadata_asof_wall_ns=fetch_wall_ns,
                    metadata_source=INFO_URL,
                )
            )
        seen_indices: set[int] = set()
        for raw_pair in _array(spot["universe"]):
            pair = _object(raw_pair)
            index = nonnegative_int(pair["index"])
            indices = _array(pair["tokens"])
            if len(indices) != 2 or index in seen_indices:
                raise ValueError("invalid or duplicate spot pair")
            seen_indices.add(index)
            base, size = tokens[nonnegative_int(indices[0])]
            quote = tokens[nonnegative_int(indices[1])][0]
            name = text(pair["name"])
            if name != ("PURR/USDC" if index == 0 else f"@{index}"):
                raise ValueError("spot coin does not match native pair index")
            instruments.append(
                Instrument(
                    InstrumentId(f"hyperliquid:info:spot:{name}"),
                    f"{payload_hash}:{fetch_wall_ns}:{MONETARY_RULE_REVISION}",
                    "hyperliquid",
                    "info",
                    name,
                    base,
                    quote,
                    None,
                    Decimal(f"1e-{size}"),
                    native_asset_id=str(10_000 + index),
                    dex_namespace="spot",
                    market_type="spot",
                    constraints=OrderConstraints(
                        min_base=Decimal(f"1e-{size}"),
                        min_notional=Decimal("10"),
                        min_notional_currency=quote.asset_id,
                        price_rule="hyperliquid_significant_digits",
                    ),
                    metadata_asof_wall_ns=fetch_wall_ns,
                    metadata_source=INFO_URL,
                )
            )
        return MetadataSnapshot(
            INFO_URL, fetch_wall_ns, payload_hash, tuple(instruments)
        )

    def _check(self, instrument: Instrument, key: SubscriptionKey) -> None:
        if (instrument.venue, instrument.api_family) != ("hyperliquid", "info") or (
            key.venue,
            key.api_family,
            key.instrument_id,
        ) != (instrument.venue, instrument.api_family, instrument.instrument_id):
            raise ValueError("Hyperliquid subscription identity mismatch")
        if instrument.market_type == "perpetual" and instrument.dex_namespace != (
            self.dex or "default"
        ):
            raise ValueError("Hyperliquid DEX mismatch")
        if instrument.market_type == "perpetual" and self.dex:
            raise ValueError("unsupported Hyperliquid builder contract denomination")
        if key.channel not in _CHANNELS:
            raise ValueError("unsupported Hyperliquid channel")
        if key.channel == "book":
            _book_options(key)
        elif key.depth is not None or key.aggregation:
            raise ValueError("depth/aggregation only apply to book channel")

    def capabilities(
        self, instrument: Instrument, key: SubscriptionKey
    ) -> Capabilities:
        self._check(instrument, key)
        return Capabilities(
            snapshot=key.channel == "book",
            trades=key.channel == "trades",
            reference_prices=frozenset({"mid"})
            if key.channel == "reference"
            else frozenset(),
            asset_context=(
                frozenset({"mark", "oracle", "funding", "open_interest"})
                if instrument.market_type == "perpetual"
                else frozenset({"mark"})
            )
            if key.channel == "context"
            else frozenset(),
        )

    def _subscription(self, instrument: Instrument, key: SubscriptionKey) -> dict:
        self._check(instrument, key)
        subscription = {"type": _CHANNELS[key.channel]}
        if key.channel == "reference":
            if instrument.market_type == "perpetual" and self.dex:
                subscription["dex"] = self.dex
        else:
            subscription["coin"] = instrument.native_symbol
        if key.channel == "book":
            subscription.update(_book_options(key)[1])
        return subscription

    def subscription_requests(
        self, instrument: Instrument, key: SubscriptionKey
    ) -> tuple[TransportRequest, ...]:
        body = json.dumps(
            {
                "method": "subscribe",
                "subscription": self._subscription(instrument, key),
            },
            separators=(",", ":"),
        ).encode()
        return (TransportRequest("WS", WS_URL, body),)

    def ws_endpoint(self, instrument: Instrument, key: SubscriptionKey) -> str:
        self._check(instrument, key)
        return WS_URL

    def snapshot_request(
        self, instrument: Instrument, key: SubscriptionKey
    ) -> TransportRequest | None:
        self._check(instrument, key)
        if key.channel == "book":
            _, options = _book_options(key)
            if options.pop("fast", False):
                return (
                    None  # Info has no documented fast5 option; do not relabel slow20.
                )
            return _request("l2Book", coin=instrument.native_symbol, **options)
        if key.channel == "reference":
            return _request(
                "allMids",
                **(
                    {"dex": self.dex}
                    if self.dex and instrument.market_type == "perpetual"
                    else {}
                ),
            )
        if key.channel == "context":
            return (
                _request("spotMetaAndAssetCtxs")
                if instrument.market_type == "spot"
                else _request(
                    "metaAndAssetCtxs", **({"dex": self.dex} if self.dex else {})
                )
            )
        return None

    def new_reducer(self, instrument: Instrument, key: SubscriptionKey) -> BookReducer:
        capabilities = self.capabilities(instrument, key)
        if key.channel != "book":
            return BookReducer(
                instrument,
                key,
                capabilities=capabilities,
                price_validator=validate_price,
                strategy_id="hyperliquid-context-v1",
                adapter_revision="hyperliquid-v1",
            )
        depth, _ = _book_options(key)
        return HyperliquidBookReducer(
            instrument,
            key,
            capabilities=capabilities,
            max_levels=depth,
            effective_depth=depth,
            price_validator=validate_price,
            strategy_id="hyperliquid-snapshot-v1",
            adapter_revision="hyperliquid-v1",
        )

    def decode(
        self, frame: RawFrame, instrument: Instrument, key: SubscriptionKey
    ) -> DecodeResult:
        try:
            self._check(instrument, key)
            message = exact_json_loads(frame.payload)
            if isinstance(message, dict) and "channel" in message:
                channel, data = message["channel"], message.get("data")
                if channel == "pong":
                    return DecodeResult(
                        controls=(Control("heartbeat", "hyperliquid_pong"),)
                    )
                if channel == "error":
                    return DecodeResult(
                        controls=(
                            Control("rejection", "hyperliquid_error", str(data)[:4096]),
                        )
                    )
                if channel == "subscriptionResponse":
                    ack = _object(data)
                    received = _object(ack.get("subscription"))
                    expected = self._subscription(instrument, key)
                    if key.channel == "book":
                        # The server echoes absent aggregation as null and slow as false.
                        for option in ("nSigFigs", "mantissa"):
                            if option in received:
                                if received[option] is None:
                                    del received[option]
                                elif type(received[option]) is not int:
                                    raise ValueError(
                                        "invalid acknowledgement aggregation"
                                    )
                        received.setdefault("fast", False)
                        expected.setdefault("fast", False)
                        if type(received["fast"]) is not bool:
                            raise ValueError("invalid acknowledgement fast flag")
                    if ack.get("method") != "subscribe" or received != expected:
                        raise ValueError("subscription acknowledgement mismatch")
                    return DecodeResult(
                        controls=(Control("ack", "hyperliquid_subscribed"),)
                    )
                if channel != _CHANNELS[key.channel]:
                    raise ValueError("unexpected Hyperliquid channel")
            else:
                if frame.transport == "ws":
                    raise ValueError("WebSocket frame missing channel")
                data = message
                if key.channel == "trades":
                    raise ValueError("undocumented public trade snapshot")
                if key.channel == "reference":
                    data = {"mids": data}
                elif key.channel == "context":
                    pair = _array(data)
                    if len(pair) != 2:
                        raise ValueError("invalid Info contexts")
                    universe = _array(_object(pair[0])["universe"])
                    contexts = _array(pair[1])
                    if len(universe) != len(contexts):
                        raise ValueError("context universe length mismatch")
                    matches = [
                        i
                        for i, item in enumerate(universe)
                        if _object(item).get("name") == instrument.native_symbol
                    ]
                    if len(matches) != 1:
                        raise ValueError("context coin not found uniquely")
                    data = {
                        "coin": instrument.native_symbol,
                        "ctx": contexts[matches[0]],
                    }
            if key.channel == "trades":
                events = tuple(
                    self._trade(_object(item), instrument) for item in _array(data)
                )
                return DecodeResult(events=events)
            data = _object(data)
            if key.channel == "reference":
                mids = _object(data["mids"])
                if instrument.native_symbol not in mids:
                    raise ValueError("reference coin missing")
                return DecodeResult(
                    events=(
                        DecodedEvent(
                            ReferencePrice(
                                "mid",
                                decimal(mids[instrument.native_symbol], positive=True),
                                instrument.quote.asset_id,
                            )
                        ),
                    )
                )
            if data.get("coin") != instrument.native_symbol:
                raise ValueError("wrong Hyperliquid coin")
            if key.channel == "context":
                return DecodeResult(
                    events=(
                        DecodedEvent(self._context(_object(data["ctx"]), instrument)),
                    )
                )
            depth, _ = _book_options(key)
            sides = _array(data["levels"])
            if len(sides) != 2:
                raise ValueError("book requires two sides")
            stamp = nonnegative_int(data["time"]) * 1_000_000
            changes = []
            for side, raw_levels in zip(("bid", "ask"), sides):
                levels = _array(raw_levels)
                if len(levels) > depth:
                    raise ValueError("book exceeds selected depth")
                for raw_level in levels:
                    level = _object(raw_level)
                    price, size = (
                        decimal(level["px"], positive=True),
                        decimal(level["sz"], positive=True),
                    )
                    validate_price(instrument, price)
                    instrument.validate_quantity(size)
                    nonnegative_int(level["n"])
                    changes.append(LevelChange(side, price, size))
            return DecodeResult(
                events=(
                    DecodedEvent(
                        BookUpdate("snapshot", tuple(changes), depth),
                        exchange_event_wall_ns=stamp,
                        exchange_time_kind="book_snapshot",
                    ),
                )
            )
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            return DecodeResult(
                error=DecodeError(
                    "hyperliquid_decode", frame.payload_sha256, str(exc)[:4096]
                )
            )

    @staticmethod
    def _trade(data: dict, instrument: Instrument) -> DecodedEvent:
        if data.get("coin") != instrument.native_symbol:
            raise ValueError("wrong Hyperliquid trade coin")
        stamp, tid = nonnegative_int(data["time"]), nonnegative_int(data["tid"])
        side = text(data["side"])
        taker = {"A": "sell", "B": "buy"}.get(side, "unknown")
        price, size = (
            decimal(data["px"], positive=True),
            decimal(data["sz"], positive=True),
        )
        validate_price(instrument, price)
        instrument.validate_quantity(size)
        # Raw and normalized duplicates survive. Consumers dedup across reconnects.
        dedup = json.dumps(
            [stamp, instrument.native_symbol, tid], separators=(",", ":")
        )
        trade = Trade(
            str(tid),
            dedup,
            price,
            size,
            taker_side=taker,
            side_provenance=f"{TRADE_SIDE_SOURCE};side={side}",
        )
        return DecodedEvent(
            trade,
            exchange_event_wall_ns=stamp * 1_000_000,
            exchange_time_kind="trade_block_time",
        )

    @staticmethod
    def _context(ctx: dict, instrument: Instrument) -> AssetContext:
        mark = decimal(ctx["markPx"], positive=True)
        if instrument.market_type == "spot":
            if {"funding", "oraclePx", "openInterest"} & ctx.keys():
                raise ValueError("perpetual fields in spot context")
            return AssetContext(mark=mark)
        return AssetContext(
            mark=mark,
            oracle=decimal(ctx["oraclePx"], positive=True),
            funding_rate=decimal(ctx["funding"]),
            funding_interval_ns=FUNDING_INTERVAL_NS,
            open_interest=decimal(ctx["openInterest"], nonnegative=True),
            open_interest_unit="base",
        )
