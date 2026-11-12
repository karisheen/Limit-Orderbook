"""Synthetic metadata fixtures; no claims about current venue listings or prices."""

import asyncio
from dataclasses import replace
from hashlib import sha256
import json

import pytest

from src.workstation.adapters import coinbase, kraken
from src.workstation.adapters.base import wrap_decoded
from src.workstation.adapters.binance import (
    BinanceGlobalAdapter,
    BinanceUSAdapter,
    asset_identity,
    discover_metadata_families,
    metadata_snapshot,
)
from src.workstation.adapters.bybit import BybitAdapter
from src.workstation.adapters.hyperliquid import HyperliquidAdapter
from src.workstation.events import SegmentId, StreamId, SubscriptionKey, synthetic_frame
from src.workstation.instruments import (
    ASSET_IDENTITY_POLICY_REVISION,
    CANONICAL_ASSETS,
    AssetIdentity,
    InstrumentCatalog,
    InstrumentQuery,
    MetadataSnapshot,
    asset_identity_metadata_version,
    canonical_asset_id,
)
from src.workstation.recording import Recorder, RecordingReader, StreamSpec
from src.workstation.streaming_bootstrap import streaming_snapshot
from src.workstation.wire import dumps, loads


BASES = tuple(
    "BTC ETH SOL XRP DOGE ADA LTC LINK AVAX DOT BNB TRX HYPE SUI BCH UNI NEAR "
    "APT AAVE ETC ENA ATOM ARB OP INJ FIL SEI TIA WIF".split()
)
ASOF = 123456789


def encoded(value):
    return json.dumps(value, separators=(",", ":")).encode()


def binance_payload(bases):
    return encoded(
        {
            "symbols": [
                {
                    "symbol": base + "USDT",
                    "baseAsset": base,
                    "quoteAsset": "USDT",
                    "status": "TRADING",
                    "isSpotTradingAllowed": True,
                    "contractType": "PERPETUAL",
                    "marginAsset": "USDT",
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                        {"filterType": "LOT_SIZE", "stepSize": "0.001"},
                    ],
                }
                for base in bases
            ]
        }
    )


def bybit_payload(bases, *, perpetual=False):
    return encoded(
        {
            "retCode": 0,
            "result": {
                "category": "linear" if perpetual else "spot",
                "nextPageCursor": "",
                "list": [
                    {
                        "symbol": base + "USDT",
                        "baseCoin": base,
                        "quoteCoin": "USDT",
                        "settleCoin": "USDT",
                        "contractType": "LinearPerpetual",
                        "status": "Trading",
                        "priceFilter": {"tickSize": "0.01"},
                        "lotSizeFilter": {
                            "qtyStep": "0.001",
                            "basePrecision": "0.001",
                            "minNotionalValue": "1",
                            "minOrderAmt": "1",
                        },
                    }
                    for base in bases
                ],
            },
        }
    )


def native_snapshots(monkeypatch, bases):
    """Exercise real metadata parsers without network I/O or ticker rewriting."""
    snapshots = []
    for adapter in (BinanceGlobalAdapter(), BinanceUSAdapter()):
        snapshots.append(
            adapter.parse_metadata(binance_payload(bases), fetch_wall_ns=ASOF)
        )
    snapshots.append(
        BinanceGlobalAdapter().parse_metadata(
            binance_payload(bases), fetch_wall_ns=ASOF, api_family="usdm-v1"
        )
    )
    for perpetual in (False, True):
        snapshots.append(
            BybitAdapter().parse_metadata(
                bybit_payload(bases, perpetual=perpetual), fetch_wall_ns=ASOF
            )
        )

    coinbase_raw = encoded(
        {
            "products": [
                {
                    "product_id": base + "-USD",
                    "base_currency_id": base,
                    "quote_currency_id": "USD",
                    "status": "online",
                    "price_increment": "0.01",
                    "base_increment": "0.001",
                    "base_min_size": "0.001",
                    "base_max_size": "1000",
                    "quote_min_size": "1",
                    "quote_max_size": "1000000",
                }
                for base in bases
            ],
            "pagination": {"has_next": False},
        }
    )
    aliases = {"BTC": "XBT", "DOGE": "XDG"}
    kraken_raw = encoded(
        {
            "error": [],
            "result": {
                base + "PAIR": {
                    "wsname": aliases.get(base, base) + "/USD",
                    "base": "native-" + aliases.get(base, base),
                    "quote": "ZUSD",
                    "lot_decimals": 3,
                    "pair_decimals": 2,
                    "status": "online",
                }
                for base in bases
            },
        }
    )

    async def fetch_coinbase(request):
        return coinbase_raw

    async def fetch_kraken(request):
        return kraken_raw

    monkeypatch.setattr(coinbase, "fetch_public", fetch_coinbase)
    monkeypatch.setattr(kraken, "fetch_public", fetch_kraken)
    monkeypatch.setattr(coinbase.time, "time_ns", lambda: ASOF)
    snapshots.append(asyncio.run(coinbase.CoinbaseAdapter().discover()))
    snapshots.append(asyncio.run(kraken.KrakenAdapter().discover()))
    return snapshots


def test_all_29_bases_share_identity_and_are_catalog_selectable(monkeypatch):
    assert CANONICAL_ASSETS == BASES
    snapshots = native_snapshots(monkeypatch, BASES)
    hyperliquid = HyperliquidAdapter().parse_metadata(
        {
            "collateralToken": 0,
            "universe": [{"name": base, "szDecimals": 3} for base in BASES],
        },
        {
            "tokens": [
                {"index": 0, "tokenId": "fixture-usdc", "name": "USDC", "szDecimals": 6}
            ],
            "universe": [],
        },
        fetch_wall_ns=ASOF,
        payload_hash="synthetic-hyperliquid-metadata",
    )
    catalog = InstrumentCatalog()
    for snapshot in (*snapshots, hyperliquid):
        catalog.apply_metadata(snapshot)
    for base in BASES:
        selected = catalog.list(InstrumentQuery(base_id=base))
        assert len(selected) == 8
        assert len(catalog.list(InstrumentQuery(base_id=base, market_type="spot"))) == 5
        assert (
            len(catalog.list(InstrumentQuery(base_id=base, market_type="perpetual")))
            == 3
        )
        for item in selected:
            assert item.base.asset_id == item.base.ticker == base
            assert item.metadata_asof_wall_ns == ASOF
            if item.venue == "kraken":
                assert item.native_symbol == base + "/USD"
                assert item.native_asset_id == base + "PAIR"
                assert item.base.native_token_id == "native-" + {
                    "BTC": "XBT",
                    "DOGE": "XDG",
                }.get(base, base)
                assert item.quote.native_token_id == "ZUSD"
            elif item.venue == "coinbase":
                assert item.native_symbol == base + "-USD"
            elif item.venue == "hyperliquid":
                assert item.native_symbol == base
            else:
                assert item.native_symbol == base + "USDT"
                assert item.base.native_token_id == base


@pytest.mark.parametrize(
    "symbol",
    [
        "WBTC",
        "WETH",
        "cbBTC",
        "BTC.B",
        "BTC-ERC20",
        "SUI.e",
        "sui",
        "1000SUI",
        "UNKNOWN",
        "builder:SUI",
        "XBT",
        "XDG",
    ],
)
def test_noncurated_and_wrapped_symbols_remain_scoped(symbol):
    for venue in ("binance-global", "binance-us", "bybit", "coinbase", "kraken"):
        assert canonical_asset_id(venue, symbol) == f"{venue}:{symbol}"
        identity = asset_identity(venue, symbol)
        assert identity.asset_id == f"{venue}:{symbol}"
        assert identity.native_token_id == identity.ticker == symbol
        assert identity.reference_asset_id is None


def test_native_parsers_preserve_unknown_and_wrapped_namespaces(monkeypatch):
    bases = ("WBTC", "WETH", "SUI.e", "UNKNOWN")
    for snapshot in native_snapshots(monkeypatch, bases):
        for item in snapshot.instruments:
            assert item.base.asset_id == f"{item.venue}:{item.base.ticker}"
            assert not item.metadata_version.endswith(ASSET_IDENTITY_POLICY_REVISION)
    explicit = AssetIdentity(
        "wrapped:sui",
        "SUI",
        native_token_id="0x123",
        network="other-chain",
        contract_address="0x123",
        reference_asset_id="SUI",
    )
    assert loads(dumps(explicit), AssetIdentity) == explicit
    assert explicit.asset_id != canonical_asset_id("bybit", explicit.ticker)


@pytest.mark.parametrize("symbol", ["USD", "USDT", "USDC"])
def test_quote_and_settlement_assets_keep_existing_identity(symbol):
    for venue in ("binance-global", "bybit", "coinbase", "kraken"):
        assert canonical_asset_id(venue, symbol) == symbol
    assert (
        asset_identity_metadata_version("old-version", asset_identity("bybit", symbol))
        == "old-version"
    )


def test_parser_revision_preserves_payload_hash_asof_and_unaffected_versions(
    monkeypatch,
):
    for snapshot in native_snapshots(monkeypatch, ("BTC", "SUI")):
        btc = next(item for item in snapshot.instruments if item.base.ticker == "BTC")
        sui = next(item for item in snapshot.instruments if item.base.ticker == "SUI")
        assert (
            sui.metadata_version
            == btc.metadata_version + ":" + ASSET_IDENTITY_POLICY_REVISION
        )
        assert (
            snapshot.fetch_wall_ns
            == btc.metadata_asof_wall_ns
            == sui.metadata_asof_wall_ns
            == ASOF
        )
        assert (
            asset_identity_metadata_version(sui.metadata_version, sui.base)
            == sui.metadata_version
        )
    raw = binance_payload(("SUI",))
    snapshot = BinanceGlobalAdapter().parse_metadata(raw, fetch_wall_ns=0)
    assert snapshot.payload_hash == sha256(raw).hexdigest()
    assert snapshot.fetch_wall_ns == snapshot.instruments[0].metadata_asof_wall_ns == 0
    combined = metadata_snapshot("synthetic-combination", raw, 0, snapshot.instruments)
    assert combined.instruments == snapshot.instruments
    # Settlement changes also affect parser provenance, even when the base did not.
    assert (
        asset_identity_metadata_version(
            "old", asset_identity("bybit", "BTC"), None, asset_identity("bybit", "SUI")
        )
        == "old:" + ASSET_IDENTITY_POLICY_REVISION
    )


def test_streaming_seeds_use_new_policy_without_fabricated_freshness():
    snapshot = streaming_snapshot()
    assert snapshot.fetch_wall_ns == 0
    for base in BASES:
        candidates = [item for item in snapshot.instruments if item.base.ticker == base]
        assert len(candidates) == 4
        for item in candidates:
            assert item.base.asset_id == base
            assert item.metadata_asof_wall_ns == 0
            assert item.metadata_status == "streaming_only"
            assert item.metadata_version.endswith(ASSET_IDENTITY_POLICY_REVISION) == (
                base in BASES[10:]
            )


def test_old_captured_scoped_identity_and_catalog_guards_remain_immutable(tmp_path):
    adapter = BybitAdapter()
    raw_metadata = bybit_payload(("SUI",))
    snapshot = adapter.parse_metadata(raw_metadata, fetch_wall_ns=ASOF)
    current = snapshot.instruments[0]
    old = replace(
        current,
        base=replace(current.base, asset_id="bybit:SUI"),
        metadata_version=f"{sha256(raw_metadata).hexdigest()}:{ASOF}",
    )
    historical = replace(snapshot, instruments=(old,))
    catalog = InstrumentCatalog()
    catalog.apply_metadata(historical)
    captured_bytes = dumps(old)
    assert loads(captured_bytes, type(old)) == old
    with pytest.raises(ValueError, match="stable instrument identity changed"):
        catalog.apply_metadata(snapshot)
    with pytest.raises(ValueError, match="metadata version is immutable"):
        catalog.apply_metadata(
            replace(
                snapshot,
                instruments=(replace(current, metadata_version=old.metadata_version),),
            )
        )
    assert catalog.get(old.instrument_id, old.metadata_version) == old
    assert dumps(catalog.get(old.instrument_id)) == captured_bytes

    key = SubscriptionKey.for_instrument(old, depth=50)
    reducer = adapter.new_reducer(old, key)
    spec = StreamSpec(
        StreamId("synthetic-stream"),
        old,
        key,
        reducer.adapter_revision,
        reducer.config_hash,
    )
    frame = synthetic_frame(
        encoded(
            {
                "topic": "orderbook.50.SUIUSDT",
                "type": "snapshot",
                "ts": 1700000000000,
                "data": {
                    "s": "SUIUSDT",
                    "u": 1,
                    "seq": 1,
                    "b": [["1.00", "2.000"]],
                    "a": [["1.01", "3.000"]],
                },
            }
        ),
        transport="ws",
        endpoint_id="wss://stream.bybit.com/v5/public/spot",
    )
    events = wrap_decoded(
        frame,
        old,
        key,
        adapter.decode(frame, old, key),
        SegmentId("fixture"),
        "ws_snapshot",
    )
    recorder = Recorder(
        tmp_path,
        session_id="synthetic-session",
        clock_id="synthetic-clock",
        streams=(spec,),
        provenance="generated identity regression fixture",
        license="CC0-1.0",
    )
    recorder.append_raw(frame)
    recorder.append_events(events)
    manifest = asyncio.run(recorder.finalize())
    assert manifest.complete, manifest.incomplete_reasons
    with RecordingReader(recorder.path) as reader:
        assert reader.manifest.streams[0].instrument == old
        assert dumps(reader.manifest.streams[0].instrument) == captured_bytes
        assert tuple(reader.iter_raw()) == (frame,)
        assert next(reader.iter_batches()).events == events


def test_family_snapshots_retain_empty_success_and_original_fetch_clock():
    empty = MetadataSnapshot("synthetic-empty-family", 10, "empty", ())
    populated = BinanceGlobalAdapter().parse_metadata(
        binance_payload(("SUI",)), fetch_wall_ns=20
    )

    class Adapter:
        metadata_families = ("empty", "populated", "failed")
        discovery_snapshots = ("stale",)

        async def discover_family(self, family):
            assert self.discovery_snapshots == ()
            if family == "failed":
                raise ValueError("synthetic failure")
            return empty if family == "empty" else populated

    adapter = Adapter()
    combined = asyncio.run(discover_metadata_families(adapter))
    assert adapter.discovery_snapshots == (empty, populated)
    assert [item.fetch_wall_ns for item in adapter.discovery_snapshots] == [10, 20]
    assert combined.instruments == populated.instruments
    assert adapter.discovery_evidence[-1][1] == "unavailable"
    adapter.metadata_families = ("failed",)
    with pytest.raises(ValueError, match="synthetic failure"):
        asyncio.run(discover_metadata_families(adapter))
    assert adapter.discovery_snapshots == ()
