"""Offline discovery scheduling, exact-data parity and owned cancellation regressions."""

import asyncio
from decimal import Decimal
from hashlib import sha256
import json
import threading

import pytest

from src.workstation import transport
from src.workstation.adapters import (
    binance,
    bitget,
    bybit,
    coinbase,
    deribit,
    hyperliquid,
    kraken,
    kucoin,
    okx,
)
from src.workstation.precision import exact_json_loads


ADAPTERS = [
    (binance, binance.BinanceGlobalAdapter),
    (binance, binance.BinanceUSAdapter),
    (bitget, bitget.BitgetAdapter),
    (bybit, bybit.BybitAdapter),
    (coinbase, coinbase.CoinbaseAdapter),
    (deribit, deribit.DeribitAdapter),
    (hyperliquid, hyperliquid.HyperliquidAdapter),
    (kraken, kraken.KrakenAdapter),
    (kucoin, kucoin.KuCoinAdapter),
    (okx, okx.OKXAdapter),
]


def empty_payload(module, request):
    if module is binance:
        value = {"symbols": []}
    elif module is bitget:
        value = {"code": "00000", "data": []}
    elif module is bybit:
        value = {
            "retCode": 0,
            "result": {
                "category": "linear" if "linear" in request.endpoint_id else "spot",
                "nextPageCursor": "",
                "list": [],
            },
        }
    elif module is coinbase:
        value = {"products": []}
    elif module is deribit:
        value = {"jsonrpc": "2.0", "result": []}
    elif module is hyperliquid:
        if json.loads(request.body)["type"] == "spotMeta":
            value = {
                "tokens": [
                    {"name": "USDC", "index": 0, "tokenId": "0xusdc", "szDecimals": 8}
                ],
                "universe": [],
            }
        else:
            value = {"collateralToken": 0, "universe": []}
    elif module is kraken:
        value = {"error": [], "result": {}}
    elif module is kucoin:
        value = {"code": "200000", "data": []}
    else:
        value = {"code": "0", "data": []}
    return json.dumps(value, separators=(",", ":")).encode()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module,factory", ADAPTERS, ids=[cls.__name__ for _, cls in ADAPTERS]
)
@pytest.mark.parametrize("malformed", [False, True], ids=["success", "error"])
async def test_discovery_keeps_transport_on_loop_and_parsing_offloop(
    monkeypatch, module, factory, malformed
):
    loop = asyncio.get_running_loop()
    owner = threading.get_ident()
    entered = asyncio.Event()
    release = threading.Event()
    fetched = []
    parsed = []

    async def fetch(request):
        assert asyncio.get_running_loop() is loop
        assert threading.get_ident() == owner
        raw = b"{" if malformed else empty_payload(module, request)
        fetched.append(raw)
        return raw

    def parse(raw):
        assert threading.get_ident() != owner
        parsed.append(raw)
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5), "discovery starved the consumer heartbeat"
        return exact_json_loads(raw)

    monkeypatch.setattr(transport, "fetch_public", fetch)
    if hasattr(module, "fetch_public"):
        monkeypatch.setattr(module, "fetch_public", fetch)
    monkeypatch.setattr(module, "exact_json_loads", parse)
    discovery = asyncio.create_task(factory().discover())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        # A consumer remains schedulable while the metadata parser is busy.
        for _ in range(5):
            await asyncio.sleep(0)
            assert not discovery.done()
        release.set()
        if malformed:
            with pytest.raises(ValueError) as expected:
                exact_json_loads(b"{")
            with pytest.raises(type(expected.value)) as actual:
                await discovery
            assert str(actual.value) == str(expected.value)
            assert parsed and all(raw == b"{" for raw in parsed)
            assert len(parsed) <= len(fetched)
        else:
            snapshot = await discovery
            assert snapshot.instruments == ()
            assert snapshot.fetch_wall_ns > 0
            assert len(snapshot.payload_hash) == 64
            assert sorted(parsed) == sorted(fetched)
    finally:
        release.set()
        await asyncio.gather(discovery, return_exceptions=True)


RAW_BINANCE = b"""{"symbols":[{"symbol":"BTCUSDT","baseAsset":"BTC",
"quoteAsset":"USDT","status":"TRADING","isSpotTradingAllowed":true,
"contractType":"PERPETUAL","marginAsset":"USDT","filters":[
{"filterType":"PRICE_FILTER","tickSize":0.00000001},
{"filterType":"LOT_SIZE","stepSize":"0.00100000"}]}]}"""


@pytest.mark.asyncio
@pytest.mark.parametrize("family", ["spot-v3", "usdm-v1"])
async def test_binance_discovery_preserves_exact_bytes_decimals_and_fetch_clock(
    monkeypatch, family
):
    adapter = binance.BinanceGlobalAdapter()
    expected = adapter.parse_metadata(RAW_BINANCE, fetch_wall_ns=123, api_family=family)

    async def fetch(request):
        return RAW_BINANCE

    monkeypatch.setattr(transport, "fetch_public", fetch)
    monkeypatch.setattr(binance, "time_ns", lambda: 123)
    actual = await adapter.discover_family(family)
    assert actual == expected
    assert actual.payload_hash == sha256(RAW_BINANCE).hexdigest()
    assert actual.instruments[0].tick_size == Decimal("0.00000001")
    assert actual.instruments[0].lot_size.as_tuple() == Decimal("0.00100000").as_tuple()
    assert actual.fetch_wall_ns == actual.instruments[0].metadata_asof_wall_ns == 123


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True], ids=["success", "parse-error"])
async def test_cancelled_discovery_joins_parser_without_publishing(
    monkeypatch, failure
):
    adapter = binance.BinanceGlobalAdapter()
    adapter.metadata_families = ("spot-v3",)
    parser = adapter.parse_metadata
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    finished = threading.Event()
    release = threading.Event()
    error = ValueError("malformed metadata after cancellation")

    async def fetch(request):
        return RAW_BINANCE

    def blocked(raw, **kwargs):
        loop.call_soon_threadsafe(entered.set)
        try:
            assert release.wait(5)
            if failure:
                raise error
            return parser(raw, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(transport, "fetch_public", fetch)
    monkeypatch.setattr(adapter, "parse_metadata", blocked)
    discovery = asyncio.create_task(adapter.discover())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        for _ in range(3):
            discovery.cancel()
            await asyncio.sleep(0)
            assert not discovery.done()
        assert adapter.discovery_snapshots == ()
        assert adapter.discovery_evidence == ()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await discovery
        assert finished.is_set()
        assert adapter.discovery_snapshots == ()
        assert adapter.discovery_evidence == ()
    finally:
        release.set()
        await asyncio.gather(discovery, return_exceptions=True)


@pytest.mark.asyncio
async def test_family_deadline_discards_late_parse_but_retains_complete_peer(
    monkeypatch,
):
    adapter = binance.BinanceGlobalAdapter()
    parser = adapter.parse_metadata
    expected = parser(RAW_BINANCE, fetch_wall_ns=123)
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    finished = threading.Event()

    async def fetch(request):
        return RAW_BINANCE

    def blocked(raw, **kwargs):
        loop.call_soon_threadsafe(entered.set)
        try:
            assert release.wait(5)
            return parser(raw, **kwargs)
        finally:
            finished.set()

    discover_family = adapter.discover_family

    async def family(name):
        if name == "spot-v3":
            return expected
        return await discover_family(name)

    monkeypatch.setattr(transport, "fetch_public", fetch)
    monkeypatch.setattr(adapter, "parse_metadata", blocked)
    monkeypatch.setattr(adapter, "discover_family", family)
    discovery = asyncio.create_task(adapter.discover_metadata(timeout=0.05))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await asyncio.sleep(0.1)
        assert not discovery.done(), "deadline must retain ownership of the parse"
        assert adapter.discovery_snapshots == ()
        release.set()
        assert await discovery == expected
        assert finished.is_set()
        assert adapter.discovery_snapshots == (expected,)
        assert adapter.discovery_evidence == (
            ("spot-v3", "available", expected.source),
            ("usdm-v1", "unavailable", "metadata_timeout"),
        )
    finally:
        release.set()
        await asyncio.gather(discovery, return_exceptions=True)
