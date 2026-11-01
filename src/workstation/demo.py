"""Deterministic, explicitly synthetic public-source fixtures; never opens a socket."""

import asyncio
from contextlib import ExitStack
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from types import TracebackType
from typing import Iterator

from .adapter_catalog import AdapterCatalog, create_adapter
from .adapters.binance import BinanceGlobalAdapter
from .adapters.coinbase import CoinbaseAdapter
from .adapters.hyperliquid import HyperliquidAdapter
from .adapters.kraken import KrakenAdapter
from .book import Level
from .events import (
    ChecksumInfo,
    Control,
    EventEnvelope,
    Gap,
    RawFrame,
    SegmentId,
    SessionId,
    StreamId,
    SubscriptionKey,
    local_control_ref,
)
from .instruments import AssetId, AssetIdentity, Instrument, InstrumentId
from .quality import SourceMode
from .recording import Recorder, RecordingReader, StreamSpec


FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "tests/fixtures/workstation/demo-v2.json"
)
DEMO_SESSION = "synthetic-demo-v2-180s"
DEMO_CLOCK = "synthetic-clock-v2"


def demo_session_id(duration_seconds: int) -> str:
    return f"synthetic-demo-v2-{duration_seconds}s"


def source_tree_revision() -> str:
    """Hash exact workstation Python source bytes, including uncommitted changes."""
    root = Path(__file__).resolve().parent
    digest = sha256()
    for path in sorted(root.rglob("*.py")):
        name, content = path.relative_to(root).as_posix().encode(), path.read_bytes()
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return "workstation-source-sha256:" + digest.hexdigest()


def _json(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _expand(value: object, fields: dict[str, str]) -> object:
    if isinstance(value, str):
        return value.format_map(fields)
    if isinstance(value, list):
        return [_expand(item, fields) for item in value]
    if isinstance(value, dict):
        return {key: _expand(item, fields) for key, item in value.items()}
    return value


@dataclass(frozen=True)
class DemoSource:
    streams: tuple[StreamSpec, ...]
    fixture: dict
    fixture_hash: str
    duration_seconds: int = 180

    @property
    def session_id(self) -> str:
        return demo_session_id(self.duration_seconds)

    @property
    def provenance(self) -> str:
        return f"{self.fixture['provenance']} fixture_sha256={self.fixture_hash}"

    def _bootstrap_inputs(self) -> Iterator[RawFrame | tuple[EventEnvelope, ...]]:
        """Emit original source bytes or explicit local controls in receive order.

        Fixtures are deliberately tiny *synthetic* complete Binance books; they
        do not represent historical depth. Hyperliquid remains a bounded source.
        Gaps describe an unknowable network interval, not missing recorded bytes.
        """
        ordinal = 0
        wall, mono, step = (
            int(self.fixture[name]) for name in ("wall_ns", "mono_ns", "step_ns")
        )
        assets = {row["base"]: row for row in self.fixture["assets"]}
        for spec in self.streams:
            if spec.instrument.venue not in ("binance-global", "hyperliquid"):
                continue
            instrument, key = spec.instrument, spec.key
            adapter = create_adapter(instrument.venue)
            asset = assets[instrument.base.ticker]
            mid = Decimal(asset["mid"])
            fields = {
                "base": instrument.base.ticker,
                "symbol": instrument.native_symbol,
                "symbol_lower": instrument.native_symbol.lower(),
                "mid": str(mid),
                "bid": str(mid - 1),
                "bid2": str(mid - 2),
                "ask": str(mid + 1),
                "ask2": str(mid + 2),
            }
            epoch = 1
            segment = SegmentId(f"{spec.stream_id}-ws-1")
            mode: SourceMode = (
                "ws_delta"
                if adapter.capabilities(instrument, key).deltas
                else "ws_snapshot"
            )

            def local(payload: Control | Gap) -> tuple[EventEnvelope, ...]:
                nonlocal ordinal
                ordinal += 1
                return (
                    EventEnvelope(
                        session_id=SessionId(DEMO_SESSION),
                        segment_id=segment,
                        stream_id=spec.stream_id,
                        key=key,
                        instrument_metadata_version=instrument.metadata_version,
                        connection_id=f"{spec.stream_id}-{epoch}",
                        connection_epoch=epoch,
                        clock_id=DEMO_CLOCK,
                        ingest_ordinal=ordinal,
                        receive_wall_ns=wall + ordinal * step,
                        receive_mono_ns=mono + ordinal * step,
                        payload=payload,
                        source_mode=mode,
                        raw_ref=local_control_ref(spec.stream_id, ordinal),
                    ),
                )

            def frame(template: str, *, rest: bool = False) -> RawFrame:
                nonlocal ordinal
                ordinal += 1
                request = adapter.snapshot_request(instrument, key) if rest else None
                endpoint = (
                    request.endpoint_id
                    if request
                    else adapter.ws_endpoint(instrument, key)
                )
                if endpoint is None:
                    raise ValueError("fixture source has no public endpoint")
                return RawFrame(
                    SessionId(DEMO_SESSION),
                    spec.stream_id,
                    f"{spec.stream_id}-{epoch}",
                    epoch,
                    DEMO_CLOCK,
                    ordinal,
                    wall + ordinal * step,
                    mono + ordinal * step,
                    "rest" if rest else "ws",
                    endpoint,
                    _json(_expand(self.fixture[template], fields)),
                )

            yield local(
                Control("source_change", "ws_connect", "synthetic fixture source")
            )
            binance = instrument.venue == "binance-global"
            if key.channel == "trades":
                yield frame("binance_trade" if binance else "hyperliquid_trade")
                # Duplicate raw print: shared downstream dedup must exclude it.
                yield frame("binance_trade" if binance else "hyperliquid_trade")
                continue
            if binance:
                yield frame("binance_snapshot", rest=True)
                yield frame("binance_diff")
            else:
                yield frame("hyperliquid_book")
            yield local(
                Gap(
                    "synthetic_network_gap",
                    continuity_lost=True,
                    capture_lost=False,
                    affected_streams=(spec.stream_id,),
                )
            )
            epoch += 1
            segment = SegmentId(f"{spec.stream_id}-rest-{epoch}")
            mode = "rest_snapshot"
            yield local(
                Control("source_change", "rest_fallback", "synthetic source boundary")
            )
            if binance:
                yield frame("binance_snapshot", rest=True)
            else:
                # Hyperliquid's Info response is the l2Book data object, not WS framing.
                raw = frame("hyperliquid_book", rest=True)
                yield RawFrame(
                    raw.session_id,
                    raw.stream_id,
                    raw.connection_id,
                    raw.connection_epoch,
                    raw.clock_id,
                    raw.ingest_ordinal,
                    raw.receive_wall_ns,
                    raw.receive_mono_ns,
                    raw.transport,
                    raw.endpoint_id,
                    _json(json.loads(raw.payload)["data"]),
                )
            epoch += 1
            segment = SegmentId(f"{spec.stream_id}-ws-{epoch}")
            mode = "ws_delta" if binance else "ws_snapshot"
            yield local(Control("source_change", "ws_connect", "synthetic recovery"))
            if binance:
                yield frame("binance_snapshot", rest=True)
                yield frame("binance_diff")
            else:
                yield frame("hyperliquid_book")

    def inputs(self) -> Iterator[RawFrame | tuple[EventEnvelope, ...]]:
        """Interleave all four venues at 1 Hz under one deterministic receive clock."""
        ordinal = 0
        for item in self._bootstrap_inputs():
            if isinstance(item, RawFrame):
                ordinal = item.ingest_ordinal
                yield replace(item, session_id=SessionId(self.session_id))
            else:
                ordinal = item[-1].ingest_ordinal
                yield tuple(
                    replace(event, session_id=SessionId(self.session_id))
                    for event in item
                )
        wall, mono = int(self.fixture["wall_ns"]), int(self.fixture["mono_ns"])
        usd = tuple(
            spec
            for spec in self.streams
            if spec.instrument.venue in ("coinbase", "kraken")
        )
        for spec in usd:
            ordinal += 1
            mode: SourceMode = (
                "ws_delta" if spec.key.channel == "book" else "ws_snapshot"
            )
            yield (
                EventEnvelope(
                    session_id=SessionId(self.session_id),
                    segment_id=SegmentId(f"{spec.stream_id}-ws-1"),
                    stream_id=spec.stream_id,
                    key=spec.key,
                    instrument_metadata_version=spec.instrument.metadata_version,
                    connection_id=f"{spec.stream_id}-1",
                    connection_epoch=1,
                    clock_id=DEMO_CLOCK,
                    ingest_ordinal=ordinal,
                    receive_wall_ns=wall + ordinal * 1_000_000,
                    receive_mono_ns=mono + ordinal * 1_000_000,
                    payload=Control(
                        "source_change", "ws_connect", "synthetic USD spot cohort"
                    ),
                    source_mode=mode,
                    raw_ref=local_control_ref(spec.stream_id, ordinal),
                ),
            )
            ordinal += 1
            adapter = create_adapter(spec.instrument.venue)
            endpoint = adapter.ws_endpoint(spec.instrument, spec.key)
            if endpoint is None:
                raise ValueError("demo source requires a public WebSocket endpoint")
            yield RawFrame(
                SessionId(self.session_id),
                spec.stream_id,
                f"{spec.stream_id}-1",
                1,
                DEMO_CLOCK,
                ordinal,
                wall + ordinal * 1_000_000,
                mono + ordinal * 1_000_000,
                "ws",
                endpoint,
                self._message(spec, 0),
            )
        for second in range(1, self.duration_seconds + 1):
            for offset, spec in enumerate(self.streams):
                ordinal += 1
                epoch = (
                    3
                    if spec.instrument.venue in ("binance-global", "hyperliquid")
                    and spec.key.channel == "book"
                    else 1
                )
                received = second * 1_000_000_000 + offset * 1_000
                adapter = create_adapter(spec.instrument.venue)
                endpoint = adapter.ws_endpoint(spec.instrument, spec.key)
                if endpoint is None:
                    raise ValueError("demo source requires a public WebSocket endpoint")
                yield RawFrame(
                    SessionId(self.session_id),
                    spec.stream_id,
                    f"{spec.stream_id}-{epoch}",
                    epoch,
                    DEMO_CLOCK,
                    ordinal,
                    wall + received,
                    mono + received,
                    "ws",
                    endpoint,
                    self._message(spec, second),
                )

    def _message(self, spec: StreamSpec, tick: int) -> bytes:
        """Source-schema generation only; production adapters own all interpretation."""
        instrument, key = spec.instrument, spec.key
        asset = next(
            row
            for row in self.fixture["assets"]
            if row["base"] == instrument.base.ticker
        )
        mid = Decimal(asset["mid"]) + (1 if instrument.venue == "kraken" else 0)
        bid, ask = f"{mid - 1:.2f}", f"{mid + 1:.2f}"
        quantity = f"{2 + tick % 5}.000"
        stamp_ms = int(self.fixture["wall_ns"]) // 1_000_000 + tick * 1000
        stamp = (
            datetime.fromtimestamp(stamp_ms // 1000, timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        trade_id = 9007199254741000 + tick
        fields = {
            "base": instrument.base.ticker,
            "symbol": instrument.native_symbol,
            "symbol_lower": instrument.native_symbol.lower(),
            "mid": str(mid),
            "bid": bid,
            "bid2": f"{mid - 2:.2f}",
            "ask": ask,
            "ask2": f"{mid + 2:.2f}",
        }
        if instrument.venue == "binance-global":
            template = "binance_diff" if key.channel == "book" else "binance_trade"
            message = _expand(self.fixture[template], fields)
            if not isinstance(message, dict):
                raise ValueError("Binance fixture template must be an object")
            if key.channel == "book":
                message["data"].update(
                    U=101 + tick, u=101 + tick, E=stamp_ms, b=[[bid, quantity]]
                )
            else:
                message["data"].update(t=trade_id, T=stamp_ms, m=bool(tick % 2))
            return _json(message)
        if instrument.venue == "hyperliquid":
            template = (
                "hyperliquid_book" if key.channel == "book" else "hyperliquid_trade"
            )
            message = _expand(self.fixture[template], fields)
            if not isinstance(message, dict):
                raise ValueError("Hyperliquid fixture template must be an object")
            if key.channel == "book":
                message["data"]["time"] = stamp_ms
                message["data"]["levels"][0][0]["sz"] = quantity
            else:
                message["data"][0].update(
                    tid=trade_id, time=stamp_ms, side="A" if tick % 2 else "B"
                )
            return _json(message)
        if instrument.venue == "coinbase":
            if key.channel == "book":
                updates = [
                    {"side": "bid", "price_level": bid, "new_quantity": quantity}
                ]
                if tick == 0:
                    updates.append(
                        {"side": "offer", "price_level": ask, "new_quantity": "3.000"}
                    )
                event = {
                    "type": "snapshot" if tick == 0 else "update",
                    "product_id": instrument.native_symbol,
                    "updates": updates,
                }
                channel = "l2_data"
            else:
                event = {
                    "type": "update",
                    "trades": [
                        {
                            "product_id": instrument.native_symbol,
                            "trade_id": str(trade_id),
                            "price": str(mid),
                            "size": "0.010",
                            "time": stamp,
                            "side": "BUY" if tick % 2 else "SELL",
                        }
                    ],
                }
                channel = "market_trades"
            return _json(
                {
                    "channel": channel,
                    "timestamp": stamp,
                    "sequence_num": tick,
                    "events": [event],
                }
            )
        from .adapters.kraken import kraken_checksum

        if key.channel == "book":
            bids = (Level(Decimal(bid), Decimal(quantity)),)
            asks = (Level(Decimal(ask), Decimal("3.000")),)
            checksum = kraken_checksum(
                bids, asks, ChecksumInfo("crc32", "0", scope="top10")
            )
            row = {
                "symbol": instrument.native_symbol,
                "timestamp": stamp,
                "bids": [{"price": bid, "qty": quantity}],
                "checksum": int(checksum),
            }
            if tick == 0:
                row["asks"] = [{"price": ask, "qty": "3.000"}]
            return _json(
                {
                    "channel": "book",
                    "type": "snapshot" if tick == 0 else "update",
                    "data": [row],
                }
            )
        return _json(
            {
                "channel": "trade",
                "type": "update",
                "data": [
                    {
                        "symbol": instrument.native_symbol,
                        "trade_id": trade_id,
                        "side": "sell" if tick % 2 else "buy",
                        "price": str(mid),
                        "qty": "0.010",
                        "timestamp": stamp,
                    }
                ],
            }
        )


def demo_source(
    fixture_path: Path = FIXTURE_PATH, *, duration_seconds: int = 180
) -> DemoSource:
    """Parse illustrative metadata through the production venue parsers."""
    raw = fixture_path.read_bytes()
    fixture = json.loads(raw)
    if (
        fixture.get("schema_version") != 2
        or fixture.get("fixture_id") != "workstation-synthetic-v2"
    ):
        raise ValueError("unsupported demo fixture version")
    if type(duration_seconds) is not int or not 1 <= duration_seconds <= 86400:
        raise ValueError("demo duration must be between 1 and 86400 virtual seconds")
    digest = sha256(raw).hexdigest()
    wall = int(fixture["wall_ns"])
    symbols = []
    for row in fixture["assets"]:
        symbols.append(
            {
                "symbol": row["base"] + "USDT",
                "baseAsset": row["base"],
                "quoteAsset": "USDT",
                "status": "TRADING",
                "isSpotTradingAllowed": True,
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                    {
                        "filterType": "LOT_SIZE",
                        "stepSize": row["lot"],
                        "minQty": row["lot"],
                    },
                ],
            }
        )
    binance = BinanceGlobalAdapter()
    hyperliquid = HyperliquidAdapter()
    instruments: tuple[Instrument, ...] = (
        *binance.parse_metadata(
            _json({"symbols": symbols}), fetch_wall_ns=wall
        ).instruments,
        *(
            item
            for item in hyperliquid.parse_metadata(
                fixture["hyperliquid_meta"],
                fixture["hyperliquid_spot_meta"],
                fetch_wall_ns=wall,
                payload_hash=digest,
            ).instruments
            if item.base.ticker in {row["base"] for row in fixture["assets"]}
        ),
    )
    usd_instruments = []
    for venue in ("coinbase", "kraken"):
        usd_adapter: CoinbaseAdapter | KrakenAdapter = (
            CoinbaseAdapter() if venue == "coinbase" else KrakenAdapter()
        )
        for row in fixture["assets"]:
            base = row["base"]
            symbol = f"{base}-USD" if venue == "coinbase" else f"{base}/USD"
            native = (
                symbol if venue == "coinbase" else fixture["kraken_native_pairs"][base]
            )
            usd_instruments.append(
                Instrument(
                    InstrumentId(f"{venue}:{usd_adapter.api_family}:spot:{symbol}"),
                    f"synthetic-v2:{digest}",
                    venue,
                    usd_adapter.api_family,
                    symbol,
                    AssetIdentity(AssetId(base), base),
                    AssetIdentity(AssetId("USD"), "USD"),
                    Decimal("0.01"),
                    Decimal(row["lot"]),
                    native_asset_id=native,
                    status="active",
                    data_access="unknown",
                    execution_access="unknown",
                    metadata_asof_wall_ns=wall,
                    metadata_source=f"synthetic-fixture:{fixture['fixture_id']}",
                )
            )
    instruments += tuple(usd_instruments)
    streams = []
    for instrument in instruments:
        adapter = create_adapter(instrument.venue)
        for channel in ("book", "trades"):
            key = AdapterCatalog.canonical_key(
                SubscriptionKey.for_instrument(instrument, channel=channel)
            )
            reducer = adapter.new_reducer(instrument, key)
            streams.append(
                StreamSpec(
                    StreamId(
                        f"demo-{instrument.venue}-{instrument.base.ticker}-{channel}"
                    ),
                    instrument,
                    key,
                    reducer.adapter_revision,
                    reducer.config_hash,
                )
            )
    return DemoSource(tuple(streams), fixture, digest, duration_seconds)


@dataclass(frozen=True)
class DemoCapture:
    reader: RecordingReader
    state_hash: str
    book_hash: str

    def close(self) -> None:
        """Release the verified reader's caches, catalog and retention lease."""
        self.reader.close()

    def __enter__(self) -> "DemoCapture":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


async def create_demo(
    root: str | Path, *, fixture_path: Path = FIXTURE_PATH, duration_seconds: int = 180
) -> DemoCapture:
    """Return an owned verified reader and independent source-projection hashes.

    Use ``with await create_demo(...) as capture`` or explicitly call close().
    Fresh and cached captures are fully verified exactly once per call.
    """
    from .projection import RecordingProjection
    from .recording import RecordedBatch

    source = demo_source(fixture_path, duration_seconds=duration_seconds)
    projection = RecordingProjection(source.streams)
    path = Path(root) / source.session_id
    revision = source_tree_revision()
    config = (
        ("fixture_version", "2"),
        ("fixture_sha256", source.fixture_hash),
        ("duration_seconds", str(duration_seconds)),
        ("source_cadence_hz", "1"),
    )
    with ExitStack() as resources:
        recorder = None
        reader: RecordingReader | None = None
        if path.exists():
            reader = resources.enter_context(RecordingReader(path))
            existing = reader.manifest
            if (
                existing.provenance != source.provenance
                or existing.streams != source.streams
                or existing.license != source.fixture["license"]
                or existing.config != config
                or existing.code_revision != revision
            ):
                raise ValueError(
                    "existing demo capture belongs to a different fixture/configuration"
                )
        else:
            recorder = Recorder(
                root,
                session_id=source.session_id,
                clock_id=DEMO_CLOCK,
                streams=source.streams,
                config=config,
                code_revision=revision,
                provenance=source.provenance,
                license=source.fixture["license"],
            )
        positions: dict[StreamId, tuple[SegmentId, SourceMode]] = {}
        delivery = 0
        try:
            for item in source.inputs():
                if recorder is not None:
                    # Offline generation may pause its synthetic producer; live offers
                    # remain nonblocking and loss-intolerant in RecorderBinding.
                    async with asyncio.timeout(30):
                        while recorder.health.queue_count >= 128:
                            if recorder.health.incomplete_reasons:
                                raise RuntimeError(
                                    "demo writer failed: "
                                    + ",".join(recorder.health.incomplete_reasons)
                                )
                            await asyncio.sleep(0.001)
                if isinstance(item, RawFrame):
                    segment, mode = positions[item.stream_id]
                    if recorder is not None:
                        recorder.append_raw(item)
                    events = projection.process_raw(
                        item, segment_id=segment, source_mode=mode
                    )
                else:
                    events = item
                    first = events[0]
                    positions[first.stream_id] = (first.segment_id, first.source_mode)
                    events = projection.apply_batch(RecordedBatch(delivery + 1, events))
                if events:
                    delivery += 1
                    if recorder is not None:
                        recorder.append_events(events)
        except BaseException:
            if recorder is not None:
                recorder.mark_incomplete("synthetic_generation_failed")
            raise
        finally:
            if recorder is not None:
                await recorder.finalize()
        if reader is None:
            reader = resources.enter_context(RecordingReader(path))
        capture = DemoCapture(reader, projection.state_hash, projection.book_hash)
        resources.pop_all()
        return capture
