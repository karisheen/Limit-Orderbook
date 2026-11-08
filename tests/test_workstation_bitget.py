"""Offline Bitget v2 fixtures; these tests do not claim captured-live coverage."""

import asyncio
from dataclasses import replace
from decimal import Decimal, localcontext
import json

import pytest

from src.workstation.adapters.base import wrap_decoded
from src.workstation.adapters.bitget import BitgetAdapter
from src.workstation.events import RawFrame, SegmentId, SubscriptionKey
from src.workstation.ingestion import make_snapshot_reducer


def encoded(value):
    return json.dumps(value, separators=(",", ":")).encode()


def metadata(market="spot", **overrides):
    row = {
        "symbol": "BTCUSDT",
        "baseCoin": "BTC",
        "quoteCoin": "USDT",
        "minTradeUSDT": "5",
    }
    row.update(
        {
            "pricePrecision": "2",
            "quantityPrecision": "6",
            "minTradeAmount": "0.000001",
            "maxTradeAmount": "900000000000000000000",
            "status": "online",
        }
        if market == "spot"
        else {
            "symbolType": "perpetual",
            "symbolStatus": "normal",
            "supportMarginCoins": ["USDT"],
            "pricePlace": "2",
            "priceEndStep": "5",
            "volumePlace": "4",
            "sizeMultiplier": "0.0005",
            "minTradeNum": "0.001",
            "maxOrderQty": "1200",
            "maxMarketOrderQty": "220",
        }
    )
    row.update(overrides)
    return {"code": "00000", "data": [row]}


def setup(market="spot", channel="book"):
    adapter = BitgetAdapter()
    instrument = adapter.parse_metadata(
        encoded(metadata(market)), market_type=market
    ).instruments[0]
    key = SubscriptionKey.for_instrument(
        instrument, channel=channel, depth=5 if channel == "book" else None
    )
    return adapter, instrument, key


def arg(market="spot", channel="books5"):
    return {
        "instType": "SPOT" if market == "spot" else "USDT-FUTURES",
        "channel": channel,
        "instId": "BTCUSDT",
    }


def book(market="spot", *, bids=None, asks=None, seq=9007199254740993):
    return {
        "action": "snapshot",
        "arg": arg(market),
        "data": [
            {
                "bids": [["100.00", "1.0005"], ["99.95", "0.0010"]]
                if bids is None
                else bids,
                "asks": [["100.05", "2.0010"]] if asks is None else asks,
                "seq": seq,
                "ts": "1695716059516",
            }
        ],
        "ts": 1695716059999,
    }


def trade(trade_id="9007199254740993", **overrides):
    row = {
        "tradeId": trade_id,
        "price": "100.05",
        "size": "0.0010",
        "side": "buy",
        "ts": "1695716059516",
    }
    row.update(overrides)
    return row


def frame(adapter, instrument, key, value, ordinal=1, *, rest=False):
    return RawFrame(
        "session",
        "stream",
        "connection",
        0,
        "clock",
        ordinal,
        ordinal * 1_000_000,
        ordinal * 1000,
        "rest" if rest else "ws",
        (
            adapter.snapshot_request(instrument, key).endpoint_id
            if rest
            else adapter.ws_endpoint(instrument, key)
        ),
        value if isinstance(value, bytes) else encoded(value),
    )


@pytest.mark.parametrize("market", ["spot", "perpetual"])
def test_metadata_exact_units_constraints_and_provenance(market):
    adapter = BitgetAdapter()
    raw = encoded(metadata(market))
    # Native metadata derivation cannot inherit a low precision caller context.
    with localcontext() as context:
        context.prec = 2
        snapshot = adapter.parse_metadata(raw, market_type=market, fetch_wall_ns=123)
    instrument = snapshot.instruments[0]
    assert instrument.tick_size == Decimal("0.01" if market == "spot" else "0.05")
    assert instrument.lot_size == Decimal("0.000001" if market == "spot" else "0.0005")
    assert instrument.quantity_unit == "base"
    assert instrument.contract_multiplier == 1 and not instrument.inverse
    assert instrument.market_type == market
    assert instrument.metadata_asof_wall_ns == 123
    assert instrument.metadata_source == snapshot.source
    assert instrument.metadata_version == f"{snapshot.payload_hash}:123"
    assert instrument.constraints.min_notional == Decimal("5")
    assert instrument.constraints.min_notional_currency == "USDT"
    if market == "perpetual":
        assert instrument.settlement.asset_id == "USDT"
        assert instrument.contract_value_currency == "BTC"
        assert instrument.settlement_conversion == "none"
        assert instrument.constraints.min_base == Decimal("0.001")
        assert instrument.constraints.max_limit_base == Decimal("1200")
        assert instrument.constraints.max_market_base == Decimal("220")
        with pytest.raises(ValueError, match="increment"):
            instrument.validate_price(Decimal("100.01"))
        with pytest.raises(ValueError, match="increment"):
            instrument.validate_quantity(Decimal("0.0001"))
    else:
        assert instrument.settlement is None
        assert instrument.constraints.max_base == Decimal("900000000000000000000")


def test_discover_combines_distinct_markets(monkeypatch):
    from src.workstation import transport

    requests = []

    async def fetch(request):
        requests.append(request)
        return encoded(
            metadata("perpetual" if "contracts" in request.endpoint_id else "spot")
        )

    monkeypatch.setattr(transport, "fetch_public", fetch)
    snapshot = asyncio.run(BitgetAdapter().discover())
    assert len(requests) == 2 and all(request.method == "GET" for request in requests)
    assert {instrument.market_type for instrument in snapshot.instruments} == {
        "spot",
        "perpetual",
    }
    assert len({instrument.instrument_id for instrument in snapshot.instruments}) == 2
    assert {instrument.api_family for instrument in snapshot.instruments} == {
        "v2-spot",
        "v2-usdt-futures",
    }


def test_mixed_discovery_excludes_rwa_but_preserves_crypto(monkeypatch):
    perpetual = metadata("perpetual")
    perpetual["data"].extend(
        [
            metadata("perpetual", symbol="ETHUSDT", baseCoin="ETH", isRwa="NO")["data"][
                0
            ],
            metadata("perpetual", symbol="AAPLUSDT", baseCoin="AAPL", isRwa="YES")[
                "data"
            ][0],
        ]
    )

    async def fetch(request):
        return encoded(
            perpetual if "contracts" in request.endpoint_id else metadata("spot")
        )

    monkeypatch.setattr("src.workstation.transport.fetch_public", fetch)
    adapter = BitgetAdapter()
    catalog = asyncio.run(adapter.discover())
    assert {(item.market_type, item.native_symbol) for item in catalog.instruments} == {
        ("spot", "BTCUSDT"),
        ("perpetual", "BTCUSDT"),
        ("perpetual", "ETHUSDT"),
    }
    for item in catalog.instruments:
        assert item.metadata_version and item.metadata_source
        assert adapter.subscription_requests(
            item, SubscriptionKey.for_instrument(item, depth=5)
        )
        if item.market_type == "perpetual":
            assert item.tick_size == Decimal("0.05")
            assert item.lot_size == Decimal("0.0005")
            assert item.settlement.asset_id == "USDT"
            assert item.contract_multiplier == Decimal("1")
            assert item.constraints.min_base == Decimal("0.001")
            assert item.constraints.max_market_base == Decimal("220")


@pytest.mark.parametrize(
    "market,status,expected",
    [
        ("spot", "halt", "suspended"),
        ("spot", "offline", "delisted"),
        ("spot", "gray", "unavailable"),
        ("spot", "new-status", "unknown"),
        ("perpetual", "maintain", "suspended"),
        ("perpetual", "limit_open", "suspended"),
        ("perpetual", "off", "delisted"),
        ("perpetual", "listed", "unavailable"),
    ],
)
def test_unavailable_status_is_preserved_and_not_subscribed(market, status, expected):
    adapter = BitgetAdapter()
    fields = {"status" if market == "spot" else "symbolStatus": status}
    instrument = adapter.parse_metadata(
        encoded(metadata(market, **fields)), market_type=market
    ).instruments[0]
    assert instrument.status == expected
    key = SubscriptionKey.for_instrument(instrument, channel="book", depth=5)
    with pytest.raises(ValueError, match=f"product_{expected}"):
        adapter.subscription_requests(instrument, key)


def test_restricted_execution_does_not_mislabel_public_data():
    adapter = BitgetAdapter()
    instrument = adapter.parse_metadata(
        encoded(metadata("perpetual", symbolStatus="restrictedAPI")),
        market_type="perpetual",
    ).instruments[0]
    assert instrument.status == "active" and instrument.execution_access == "blocked"
    key = SubscriptionKey.for_instrument(instrument, channel="book", depth=5)
    assert adapter.subscription_requests(instrument, key)


@pytest.mark.parametrize(
    "market,fields",
    [
        ("spot", {"pricePrecision": "39"}),
        ("spot", {"quantityPrecision": True}),
        ("perpetual", {"priceEndStep": "0"}),
        ("perpetual", {"sizeMultiplier": "0"}),
        ("perpetual", {"sizeMultiplier": "0.00001"}),
        ("perpetual", {"supportMarginCoins": ["USDC"]}),
    ],
)
def test_invalid_metadata_is_not_rounded_or_invented(market, fields):
    with pytest.raises(ValueError):
        BitgetAdapter().parse_metadata(
            encoded(metadata(market, **fields)), market_type=market
        )


def test_delivery_contracts_are_not_misclassified_as_perpetuals():
    adapter = BitgetAdapter()
    assert not adapter.parse_metadata(
        encoded(metadata("perpetual", symbolType="delivery")), market_type="perpetual"
    ).instruments
    invalid = metadata()
    invalid["code"] = "40001"
    with pytest.raises(ValueError, match="endpoint_error"):
        adapter.parse_metadata(encoded(invalid))
    duplicate = metadata()
    duplicate["data"] *= 2
    with pytest.raises(ValueError, match="duplicate metadata"):
        adapter.parse_metadata(encoded(duplicate))


@pytest.mark.parametrize("market", ["spot", "perpetual"])
def test_requests_are_public_and_match_declared_depth(market):
    adapter, instrument, key = setup(market)
    (request,) = adapter.subscription_requests(instrument, key)
    assert request.endpoint_id == "wss://ws.bitget.com/v2/ws/public"
    assert json.loads(request.body) == {"op": "subscribe", "args": [arg(market)]}
    assert adapter.snapshot_request(instrument, key).method == "GET"
    with pytest.raises(ValueError, match="depth 5"):
        adapter.subscription_requests(instrument, replace(key, depth=50))
    with pytest.raises(ValueError, match="wrong_subscription"):
        adapter.capabilities(instrument, replace(key, api_family="other"))
    assert not adapter.capabilities(instrument, key).exact_event_flow


@pytest.mark.parametrize("market", ["spot", "perpetual"])
@pytest.mark.parametrize("rest", [False, True])
def test_bounded_snapshots_replace_book_and_replay_from_checkpoint(market, rest):
    adapter, instrument, key = setup(market)
    reducer = (
        make_snapshot_reducer(adapter, instrument, key, poll_interval=2)
        if rest
        else adapter.new_reducer(instrument, key)
    )
    for ordinal in (1, 2):
        payload = (
            book(market)
            if ordinal == 1
            else book(
                market, bids=[["99.90", "0.0020"]], asks=[["100.10", "0.0030"]], seq=1
            )
        )
        if rest:
            data = payload["data"][0]
            data.update(precision="scale0", scale="0.05")
            payload = {"code": "00000", "data": data}
        raw = frame(adapter, instrument, key, payload, ordinal, rest=rest)
        decoded = adapter.decode(raw, instrument, key)
        assert decoded.error is None
        (event,) = decoded.events
        assert event.exchange_event_wall_ns == 1695716059516000000
        assert event.exchange_time_kind == ("system" if rest else "matching_engine")
        assert event.sequence.last == ("9007199254740993" if ordinal == 1 else "1")
        envelopes = wrap_decoded(
            raw,
            instrument,
            key,
            decoded,
            SegmentId("initial"),
            "rest_snapshot" if rest else "ws_snapshot",
        )
        if ordinal == 2:
            clone = (
                make_snapshot_reducer(adapter, instrument, key, poll_interval=2)
                if rest
                else adapter.new_reducer(instrument, key)
            )
            clone.restore(reducer.checkpoint())
            replayed = clone.apply(envelopes)
        result = reducer.apply(envelopes)
        assert result.recovery is None
        assert result.book.quality.integrity == "valid"
        assert not result.book.quality.coverage.full_depth
        assert result.book.quality.coverage.subscribed_depth == 5
        assert not result.book.quality.capabilities.deltas
        assert not result.book.quality.capabilities.exact_event_flow
        if ordinal == 2:
            assert result.book == replayed.book
            assert [(level.price, level.quantity) for level in result.book.bids] == [
                (Decimal("99.90"), Decimal("0.0020"))
            ]
            assert result.book.quality.coverage.retained_bid_levels == 1


@pytest.mark.parametrize("market", ["spot", "perpetual"])
def test_object_trades_have_exact_scoped_identity_taker_side_and_history(market):
    adapter, instrument, key = setup(market, "trades")
    payload = {
        "arg": arg(market, "trade"),
        "action": "update",
        "data": [trade(), trade("9007199254740994", side="sell")],
    }
    decoded = adapter.decode(frame(adapter, instrument, key, payload), instrument, key)
    assert decoded.error is None
    first, second = decoded.events
    assert first.payload.trade_id == "9007199254740993"
    assert first.payload.dedup_key != second.payload.dedup_key
    assert str(instrument.instrument_id) in first.payload.dedup_key
    assert first.payload.price == Decimal("100.05")
    assert first.payload.quantity == Decimal("0.0010")
    assert first.payload.taker_side == "buy" and second.payload.taker_side == "sell"
    assert first.payload.side_provenance == "documented_taker"
    assert first.exchange_event_wall_ns == 1695716059516000000
    assert not first.payload.is_subscription_history
    payload["action"] = "snapshot"
    payload["data"] = [trade(side="new-side")]
    history = (
        adapter.decode(frame(adapter, instrument, key, payload), instrument, key)
        .events[0]
        .payload
    )
    assert history.is_subscription_history and history.taker_side == "unknown"
    rest = adapter.decode(
        frame(
            adapter, instrument, key, {"code": "00000", "data": [trade()]}, rest=True
        ),
        instrument,
        key,
    )
    assert rest.events[0].payload.is_subscription_history
    assert rest.events[0].payload.dedup_key == first.payload.dedup_key


def test_numeric_source_tokens_are_not_binary_float_rounded():
    adapter, instrument, key = setup("spot", "trades")
    raw = b'{"arg":{"instType":"SPOT","channel":"trade","instId":"BTCUSDT"},"action":"update","data":[{"tradeId":"1","price":9007199254740993.01,"size":0.000001,"side":"buy","ts":"1695716059516"}]}'
    result = adapter.decode(frame(adapter, instrument, key, raw), instrument, key)
    assert result.error is None
    assert result.events[0].payload.price == Decimal("9007199254740993.01")
    assert result.events[0].payload.quantity == Decimal("0.000001")


@pytest.mark.parametrize("market", ["spot", "perpetual"])
def test_control_messages_do_not_publish_market_evidence(market):
    adapter, instrument, key = setup(market)
    for payload, kind in (
        (b"pong", "heartbeat"),
        ({"event": "subscribe", "arg": arg(market)}, "ack"),
        (
            {"event": "error", "code": "30001", "msg": "subscription rejected"},
            "rejection",
        ),
    ):
        raw = frame(adapter, instrument, key, payload)
        decoded = adapter.decode(raw, instrument, key)
        assert decoded.error is None and not decoded.events
        assert decoded.controls[0].kind == kind
        reducer = adapter.new_reducer(instrument, key)
        result = reducer.apply(
            wrap_decoded(
                raw, instrument, key, decoded, SegmentId("initial"), "ws_snapshot"
            )
        )
        assert result.book is None
        assert reducer.quality.last_book_observation_mono_ns is None


@pytest.mark.parametrize(
    "change",
    [
        {"action": "update"},
        {"arg": arg("perpetual")},
        {"data": []},
        {"data": [{"bids": [["100.00", "0"]], "asks": [["100.05", "1"]], "ts": "1"}]},
        {
            "data": [
                {"bids": [["100.00", "1"]] * 2, "asks": [["100.05", "1"]], "ts": "1"}
            ]
        },
        {
            "data": [
                {
                    "bids": [[str(90 + i), "1"] for i in range(6)],
                    "asks": [["100.05", "1"]],
                    "ts": "1",
                }
            ]
        },
        {"data": [{"bids": [["100.001", "1"]], "asks": [["100.05", "1"]], "ts": "1"}]},
    ],
)
def test_malformed_snapshot_is_atomic_and_preserves_raw_hash(change):
    adapter, instrument, key = setup()
    payload = book()
    payload.update(change)
    raw = frame(adapter, instrument, key, payload)
    decoded = adapter.decode(raw, instrument, key)
    assert decoded.error is not None and decoded.error.raw_hash == raw.payload_sha256
    assert not decoded.events and not decoded.controls


@pytest.mark.parametrize(
    "row",
    [
        trade(trade_id=""),
        trade(side=1),
        trade(ts=True),
        trade(size="0"),
        trade(price="NaN"),
        trade(size="0.0000001"),
        ["1695716059516", "100.05", "1", "buy"],
    ],
)
def test_malformed_trade_batch_never_partially_publishes(row):
    adapter, instrument, key = setup("spot", "trades")
    raw = frame(
        adapter,
        instrument,
        key,
        {"arg": arg(channel="trade"), "action": "update", "data": [trade(), row]},
    )
    decoded = adapter.decode(raw, instrument, key)
    assert decoded.error is not None and not decoded.events
    assert decoded.error.raw_hash == raw.payload_sha256


def test_wrong_transport_endpoint_and_merged_rest_depth_are_rejected():
    adapter, instrument, key = setup("perpetual")
    raw = frame(adapter, instrument, key, book("perpetual"))
    assert adapter.decode(replace(raw, transport="rest"), instrument, key).error
    assert adapter.decode(
        replace(raw, endpoint_id="wss://ws.bitget.com/v2/ws/private"), instrument, key
    ).error
    data = book("perpetual")["data"][0]
    data.update(precision="scale1", scale="0.5")
    raw = frame(adapter, instrument, key, {"code": "00000", "data": data}, rest=True)
    assert adapter.decode(raw, instrument, key).error


@pytest.mark.parametrize(
    "bids,asks",
    [
        ([["100.10", "1"]], [["100.05", "1"]]),
        ([], [["100.05", "1"]]),
    ],
)
def test_invalid_book_shape_never_becomes_usable(bids, asks):
    adapter, instrument, key = setup()
    reducer = adapter.new_reducer(instrument, key)
    raw = frame(adapter, instrument, key, book(bids=bids, asks=asks))
    decoded = adapter.decode(raw, instrument, key)
    assert decoded.error is None
    envelopes = wrap_decoded(
        raw, instrument, key, decoded, SegmentId("initial"), "ws_snapshot"
    )
    result = reducer.apply(envelopes)
    assert result.recovery is not None
    assert result.book is None
    assert reducer.quality.integrity != "valid"


def test_trade_identity_does_not_collide_between_spot_and_perpetual():
    identities = []
    for market in ("spot", "perpetual"):
        adapter, instrument, key = setup(market, "trades")
        payload = {"arg": arg(market, "trade"), "action": "update", "data": [trade()]}
        result = adapter.decode(
            frame(adapter, instrument, key, payload), instrument, key
        )
        assert result.error is None
        identities.append(result.events[0].payload.dedup_key)
    assert identities[0] != identities[1]
