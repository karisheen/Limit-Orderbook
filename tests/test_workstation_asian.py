"""Synthetic offline protocol fixtures, not captured-live validation evidence."""

from dataclasses import replace
from decimal import Decimal
import json

import pytest

from src.workstation.adapters.base import wrap_decoded
from src.workstation.adapters.binance import BinanceGlobalAdapter, BinanceUSAdapter
from src.workstation.adapters.bybit import BybitAdapter
from src.workstation.adapters.kucoin import KuCoinAdapter
from src.workstation.events import RawFrame, SegmentId, SubscriptionKey, Trade
from src.workstation.quality import ClockReading, EligibilityPolicy, evaluate_cohort


def encoded(value):
    return json.dumps(value, separators=(",", ":")).encode()


def metadata(adapter, *, base="BTC", currency="USDT", status=True):
    symbol = (
        f"{base}-{currency}" if isinstance(adapter, KuCoinAdapter) else base + currency
    )
    if isinstance(adapter, (BinanceGlobalAdapter, BinanceUSAdapter)):
        return {
            "symbols": [
                {
                    "symbol": symbol,
                    "baseAsset": base,
                    "quoteAsset": currency,
                    "status": "TRADING" if status else "HALT",
                    "isSpotTradingAllowed": True,
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                        {
                            "filterType": "LOT_SIZE",
                            "stepSize": "0.001",
                            "minQty": "0.001",
                            "maxQty": "1000",
                        },
                        {"filterType": "MIN_NOTIONAL", "minNotional": "5"},
                    ],
                }
            ]
        }
    if isinstance(adapter, BybitAdapter):
        return {
            "retCode": 0,
            "result": {
                "category": "spot",
                "list": [
                    {
                        "symbol": symbol,
                        "baseCoin": base,
                        "quoteCoin": currency,
                        "status": "Trading" if status else "Halt",
                        "priceFilter": {"tickSize": "0.01"},
                        "lotSizeFilter": {
                            "basePrecision": "0.001",
                            "minOrderAmt": "5",
                            "maxMarketOrderQty": "1000",
                            "maxLimitOrderQty": "2000",
                        },
                    }
                ],
            },
        }
    return {
        "code": "200000",
        "data": [
            {
                "symbol": symbol,
                "baseCurrency": base,
                "quoteCurrency": currency,
                "enableTrading": status,
                "priceIncrement": "0.01",
                "baseIncrement": "0.001",
                "baseMinSize": "0.001",
                "baseMaxSize": "1000",
                "minFunds": "5",
            }
        ],
    }


def setup(adapter, *, channel="book", currency="USDT"):
    instrument = adapter.parse_metadata(
        encoded(metadata(adapter, currency=currency))
    ).instruments[0]
    depth = (
        20
        if isinstance(adapter, (BinanceUSAdapter, KuCoinAdapter))
        else 50
        if isinstance(adapter, BybitAdapter)
        else 5000
    )
    key = SubscriptionKey.for_instrument(
        instrument, channel=channel, depth=depth if channel == "book" else None
    )
    return instrument, key


def frame(adapter, instrument, key, value, ordinal=1, *, rest=False, epoch=0):
    endpoint = (
        adapter.snapshot_request(instrument, key).endpoint_id
        if rest
        else adapter.ws_endpoint(instrument, key)
    )
    return RawFrame(
        "session",
        "stream",
        "connection",
        epoch,
        "clock",
        ordinal,
        ordinal * 1_000_000,
        ordinal * 1000,
        "rest" if rest else "ws",
        endpoint,
        value if isinstance(value, bytes) else encoded(value),
    )


def events(adapter, instrument, key, value, ordinal=1, *, rest=False, epoch=0):
    raw = frame(adapter, instrument, key, value, ordinal, rest=rest, epoch=epoch)
    result = adapter.decode(raw, instrument, key)
    assert result.error is None, result.error
    mode = (
        "rest_snapshot"
        if isinstance(adapter, KuCoinAdapter)
        else "ws_snapshot"
        if isinstance(adapter, BinanceUSAdapter)
        else "ws_delta"
    )
    return wrap_decoded(raw, instrument, key, result, SegmentId("initial"), mode)


def diff(first, last, bids=None, asks=None, symbol="BTCUSDT"):
    return {
        "stream": f"{symbol.lower()}@depth@100ms",
        "data": {
            "e": "depthUpdate",
            "s": symbol,
            "E": 1234567890123,
            "U": first,
            "u": last,
            "b": bids or [],
            "a": asks or [],
        },
    }


def snapshot(last=100, bids=None, asks=None):
    return {
        "lastUpdateId": last,
        "bids": [["99.00", "1.000"], ["98.00", "2.000"]] if bids is None else bids,
        "asks": [["101.00", "1.000"]] if asks is None else asks,
    }


def bybook(
    update=100,
    seq=500,
    *,
    kind="snapshot",
    bids=None,
    asks=None,
    symbol="BTCUSDT",
    depth=50,
):
    return {
        "topic": f"orderbook.{depth}.{symbol}",
        "type": kind,
        "ts": 1234567890123,
        "data": {
            "s": symbol,
            "u": update,
            "seq": seq,
            "b": [["99.00", "1.000"]] if bids is None else bids,
            "a": [["101.00", "1.000"]] if asks is None else asks,
        },
    }


def test_bybit_order_type_caps_ignore_deprecated_constraints():
    adapter = BybitAdapter()
    payload = metadata(adapter)
    lot = payload["result"]["list"][0]["lotSizeFilter"]
    lot.update(minOrderQty="9000", maxOrderQty="0.001", maxOrderAmt="0.01")
    item = adapter.parse_metadata(encoded(payload)).instruments[0]
    assert item.constraints.min_base is None
    assert item.constraints.max_base is None
    assert item.constraints.max_notional is None
    assert item.constraints.max_limit_base == Decimal("2000")
    assert item.constraints.max_market_base == Decimal("1000")
    item.validate_order(Decimal("100"), Decimal("2000"), order_type="limit")
    item.validate_order(Decimal("100"), Decimal("1000"), order_type="market")
    for order_type, quantity in (("limit", "2000.001"), ("market", "1000.001")):
        with pytest.raises(ValueError, match="outside instrument constraints"):
            item.validate_order(
                Decimal("100"), Decimal(quantity), order_type=order_type
            )
    for order_type in ("limit", "market"):
        with pytest.raises(ValueError, match="outside instrument constraints"):
            item.validate_order(Decimal("100"), Decimal("0.049"), order_type=order_type)
    del lot["maxLimitOrderQty"]
    item = adapter.parse_metadata(encoded(payload)).instruments[0]
    assert item.constraints.max_limit_base is None
    item.validate_order(Decimal("100"), Decimal("2001"), order_type="limit")


def test_bybit_xstock_reference_does_not_change_token_book_economics():
    adapter = BybitAdapter()
    payload = metadata(adapter, base="AAPLX")
    row = payload["result"]["list"][0]
    row.update(
        symbolType="xstocks", xstockMultiplier="0.001", symbolId=9007199254740993
    )
    item = adapter.parse_metadata(encoded(payload)).instruments[0]
    assert item.reference_multiplier == Decimal("0.001")
    assert item.native_asset_id == "9007199254740993"
    assert item.base.asset_id == "bybit:AAPLX"
    assert item.base.reference_asset_id is None
    assert item.contract_multiplier == Decimal("1")
    assert item.quantity_unit == "base"
    key = SubscriptionKey.for_instrument(item, depth=50)
    reducer = adapter.new_reducer(item, key)
    book = reducer.apply(events(adapter, item, key, bybook(symbol="AAPLXUSDT"))).book
    assert book.bids[0].price == Decimal("99")
    assert book.bids[0].quantity == Decimal("1")
    # Minimum amount is token price * token quantity, not reference quantity.
    item.validate_order(Decimal("100"), Decimal("0.05"), order_type="limit")
    row["symbolType"] = "normal"
    assert (
        adapter.parse_metadata(encoded(payload)).instruments[0].reference_multiplier
        is None
    )
    row["symbolType"] = "xstocks"
    del row["xstockMultiplier"]
    assert adapter.parse_metadata(encoded(payload)).instruments[
        0
    ].reference_multiplier == Decimal("1")
    for invalid in ("0", "-1", "NaN"):
        row["xstockMultiplier"] = invalid
        with pytest.raises(ValueError):
            adapter.parse_metadata(encoded(payload))


@pytest.mark.parametrize(
    "factory", [BinanceGlobalAdapter, BinanceUSAdapter, BybitAdapter, KuCoinAdapter]
)
def test_metadata_exact_identity_constraints_status(factory):
    adapter = factory()
    instrument, key = setup(adapter)
    assert instrument.base.asset_id == "BTC"
    assert instrument.quote.asset_id == "USDT"
    assert instrument.tick_size == Decimal("0.01")
    assert instrument.lot_size == Decimal("0.001")
    assert instrument.constraints.min_notional == Decimal("5")
    assert instrument.constraints.min_notional_currency == "USDT"
    assert instrument.venue in instrument.instrument_id
    assert instrument.api_family in instrument.instrument_id
    assert instrument.native_symbol in instrument.instrument_id
    assert instrument.data_access == instrument.execution_access == "unknown"
    suspended = adapter.parse_metadata(
        encoded(metadata(adapter, status=False))
    ).instruments[0]
    assert suspended.status == "suspended"
    with pytest.raises(ValueError, match="product_suspended"):
        adapter.snapshot_request(
            suspended, SubscriptionKey.for_instrument(suspended, depth=key.depth)
        )
    with pytest.raises(ValueError):
        adapter.snapshot_request(instrument, replace(key, instrument_id="unknown"))


@pytest.mark.parametrize(
    "factory", [BinanceGlobalAdapter, BinanceUSAdapter, BybitAdapter, KuCoinAdapter]
)
def test_metadata_priority_and_extensibility(factory):
    adapter = factory()
    payloads = [
        metadata(adapter, base=base) for base in ("DOT", "ETH", "BTC", "SOL", "NEWCOIN")
    ]
    if isinstance(adapter, (BinanceGlobalAdapter, BinanceUSAdapter)):
        data = {"symbols": [payload["symbols"][0] for payload in payloads]}
    elif isinstance(adapter, BybitAdapter):
        data = {
            "retCode": 0,
            "result": {
                "category": "spot",
                "list": [payload["result"]["list"][0] for payload in payloads],
            },
        }
    else:
        data = {"code": "200000", "data": [payload["data"][0] for payload in payloads]}
    products = adapter.parse_metadata(encoded(data)).instruments
    assert [item.base.ticker for item in products] == [
        "BTC",
        "ETH",
        "SOL",
        "DOT",
        "NEWCOIN",
    ]
    assert products[-1].base.asset_id == f"{adapter.venue}:NEWCOIN"


def test_global_hosts_and_independent_us_snapshot_capability():
    global_adapter, us = BinanceGlobalAdapter(), BinanceUSAdapter()
    instrument, key = setup(global_adapter)
    other, other_key = setup(us)
    assert global_adapter.snapshot_request(instrument, key).endpoint_id.startswith(
        "https://data-api.binance.vision/"
    )
    assert (
        global_adapter.ws_endpoint(instrument, key)
        == "wss://data-stream.binance.vision:443/stream"
    )
    assert (
        b"depth@100ms" in global_adapter.subscription_requests(instrument, key)[0].body
    )
    assert b"depth20@100ms" in us.subscription_requests(other, other_key)[0].body
    assert instrument.instrument_id != other.instrument_id
    assert global_adapter.capabilities(instrument, key).deltas
    assert not us.capabilities(other, other_key).deltas
    assert not us.capabilities(other, other_key).exact_event_flow
    assert us.decode(
        frame(global_adapter, instrument, key, snapshot(), rest=True), other, key
    ).error


def test_global_buffer_bridge_preserves_receive_provenance_and_checkpoint():
    adapter = BinanceGlobalAdapter()
    instrument, key = setup(adapter)
    reducer = adapter.new_reducer(instrument, key)
    old = events(adapter, instrument, key, diff(90, 99), 1)
    bridge = events(adapter, instrument, key, diff(100, 102, [["99.00", "3.000"]]), 2)
    assert reducer.apply(old).book is None
    assert reducer.apply(bridge).book is None
    restored = adapter.new_reducer(instrument, key)
    restored.restore(reducer.checkpoint())
    assert restored.buffered_events == old + bridge
    snap = events(adapter, instrument, key, snapshot(), 3, rest=True)
    result = restored.apply(snap)
    assert result.recovery is None
    assert result.book.bids[0].quantity == Decimal("3.000")
    assert result.book.asof.ingest_ordinal == 3
    assert bridge[0].ingest_ordinal == 2
    assert restored.sequence.last == "102"
    assert restored.buffered_events == ()
    assert restored.apply(bridge).recovery.reason == "obsolete_ordinal"
    original = reducer.apply(snap)
    assert original.book == result.book
    assert reducer.checkpoint().state_hash == restored.checkpoint().state_hash


def test_global_snapshot_waits_for_bridge_duplicate_and_deletion():
    adapter = BinanceGlobalAdapter()
    instrument, key = setup(adapter)
    reducer = adapter.new_reducer(instrument, key)
    assert (
        reducer.apply(events(adapter, instrument, key, snapshot(), 1, rest=True)).book
        is None
    )
    assert reducer.initialized and not reducer.synchronized
    assert (
        reducer.apply(events(adapter, instrument, key, diff(90, 100), 2)).book is None
    )
    result = reducer.apply(
        events(
            adapter,
            instrument,
            key,
            diff(101, 105, [["99", "0"], ["98", "3"], ["98", "4"]]),
            3,
        )
    )
    assert result.book.bids[0].price == Decimal("98")
    assert result.book.bids[0].quantity == Decimal("4")
    assert reducer.sequence.last == "105"
    duplicate = reducer.apply(
        events(adapter, instrument, key, diff(103, 105, [["98", "9"]]), 4)
    )
    assert not duplicate.changed
    assert duplicate.book.bids[0].quantity == Decimal("4")


def test_global_gap_requires_new_snapshot_and_retains_bootstrap_buffer():
    adapter = BinanceGlobalAdapter()
    instrument, key = setup(adapter)
    reducer = adapter.new_reducer(instrument, key)
    reducer.apply(events(adapter, instrument, key, diff(105, 106, [["99", "2"]]), 1))
    failure = reducer.apply(
        events(adapter, instrument, key, snapshot(100), 2, rest=True)
    )
    assert failure.recovery.reason == "sequence_gap"
    assert not reducer.synchronized
    assert len(reducer.buffered_events) == 1
    recovered = reducer.apply(
        events(adapter, instrument, key, snapshot(105), 3, rest=True)
    )
    assert recovered.book.quality.integrity == "valid"
    assert recovered.book.bids[0].quantity == Decimal("2")
    failed = reducer.apply(events(adapter, instrument, key, diff(108, 109), 4))
    assert failed.recovery.reason == "sequence_gap"
    assert failed.book.quality.integrity == "invalid"


def test_global_epoch_reset_and_buffer_overflow():
    adapter = BinanceGlobalAdapter()
    instrument, key = setup(adapter)
    reducer = adapter.new_reducer(instrument, key)
    reducer.MAX_BUFFER_EVENTS = 1
    reducer.apply(events(adapter, instrument, key, diff(1, 2), 1))
    failed = reducer.apply(events(adapter, instrument, key, diff(3, 4), 2))
    assert failed.recovery.reason == "bootstrap_buffer_overflow"
    reducer.reset(1, SegmentId("initial"))
    assert not reducer.buffered_events
    assert (
        reducer.apply(events(adapter, instrument, key, diff(5, 6), 3)).recovery.reason
        == "obsolete_epoch"
    )


def test_us_partial_snapshots_replace_instead_of_merge():
    adapter = BinanceUSAdapter()
    instrument, key = setup(adapter)
    reducer = adapter.new_reducer(instrument, key)
    payload = {"stream": "btcusdt@depth20@100ms", "data": snapshot()}
    assert reducer.apply(events(adapter, instrument, key, payload)).book is not None
    payload["data"] = snapshot(200, bids=[["97", "2"]])
    book = reducer.apply(events(adapter, instrument, key, payload, 2)).book
    assert [level.price for level in book.bids] == [Decimal("97")]
    assert not book.quality.capabilities.deltas
    assert not book.quality.capabilities.exact_event_flow
    payload["stream"] = "ethusdt@depth20@100ms"
    assert adapter.decode(
        frame(adapter, instrument, key, payload, 3), instrument, key
    ).error


@pytest.mark.parametrize("update,seq,quantity", [(200, 900, "2"), (201, 901, "3")])
def test_bybit_noncontiguous_ids_are_valid(update, seq, quantity):
    adapter = BybitAdapter()
    instrument, key = setup(adapter)
    reducer = adapter.new_reducer(instrument, key)
    reducer.apply(events(adapter, instrument, key, bybook()))
    result = reducer.apply(
        events(
            adapter,
            instrument,
            key,
            bybook(update, seq, kind="delta", bids=[["99", quantity]], asks=[]),
            2,
        )
    )
    assert result.recovery is None
    assert result.book.bids[0].quantity == Decimal(quantity)


@pytest.mark.parametrize("update,seq", [(99, 501), (101, 499), (100, 500)])
def test_bybit_stale_or_duplicate_update_ignored(update, seq):
    adapter = BybitAdapter()
    instrument, key = setup(adapter)
    reducer = adapter.new_reducer(instrument, key)
    reducer.apply(events(adapter, instrument, key, bybook()))
    result = reducer.apply(
        events(
            adapter,
            instrument,
            key,
            bybook(update, seq, kind="delta", bids=[["99", "8"]], asks=[]),
            2,
        )
    )
    assert result.book.bids[0].quantity == Decimal("1")
    assert not result.changed


@pytest.mark.parametrize("kind,update", [("delta", 1), ("snapshot", 30)])
def test_bybit_service_reset_or_new_snapshot_replaces(kind, update):
    adapter = BybitAdapter()
    instrument, key = setup(adapter)
    reducer = adapter.new_reducer(instrument, key)
    reducer.apply(events(adapter, instrument, key, bybook()))
    result = reducer.apply(
        events(
            adapter,
            instrument,
            key,
            bybook(update, 4, kind=kind, bids=[["98", "2"]]),
            2,
        )
    )
    assert result.book.bids[0].price == Decimal("98")
    assert len(result.book.bids) == 1
    assert reducer.sequence.last == str(update)
    restored = adapter.new_reducer(instrument, key)
    restored.restore(reducer.checkpoint())
    assert restored.sequence == reducer.sequence


def test_bybit_delta_without_snapshot_and_exact_topic():
    adapter = BybitAdapter()
    instrument, key = setup(adapter)
    reducer = adapter.new_reducer(instrument, key)
    assert (
        reducer.apply(
            events(adapter, instrument, key, bybook(kind="delta"))
        ).recovery.reason
        == "snapshot_required"
    )
    for payload in (bybook(symbol="ETHUSDT"), bybook(depth=200)):
        assert adapter.decode(
            frame(adapter, instrument, key, payload), instrument, key
        ).error


def test_bybit_level1_snapshot_only_and_depth_bound():
    adapter = BybitAdapter()
    instrument, key = setup(adapter)
    key = replace(key, depth=1)
    assert not adapter.capabilities(instrument, key).deltas
    result = adapter.decode(
        frame(adapter, instrument, key, bybook(kind="delta", depth=1)), instrument, key
    )
    assert result.error
    result = adapter.decode(
        frame(
            adapter, instrument, key, bybook(depth=1, bids=[["99", "1"], ["98", "1"]])
        ),
        instrument,
        key,
    )
    assert result.error


def test_kucoin_no_invented_ws_or_continuity():
    adapter = KuCoinAdapter()
    instrument, key = setup(adapter)
    assert adapter.ws_endpoint(instrument, key) is None
    assert adapter.subscription_requests(instrument, key) == ()
    caps = adapter.capabilities(instrument, key)
    assert caps.sequence_scope is None and not caps.deltas and not caps.exact_event_flow
    reducer = adapter.new_reducer(instrument, key)
    payload = {
        "code": "200000",
        "data": {
            "sequence": "100",
            "time": 1234567890123,
            "bids": [["99", "1"]],
            "asks": [["101", "1"]],
        },
    }
    result = reducer.apply(events(adapter, instrument, key, payload, rest=True))
    assert result.book.quality.lifecycle == "degraded"
    payload["data"]["bids"] = [["98", "2"]]
    payload["data"]["sequence"] = "10000"
    result = reducer.apply(events(adapter, instrument, key, payload, 2, rest=True))
    assert result.book.bids[0].price == Decimal("98")
    assert result.recovery is None


@pytest.mark.parametrize(
    "factory", [BinanceGlobalAdapter, BinanceUSAdapter, BybitAdapter, KuCoinAdapter]
)
@pytest.mark.parametrize("bad", [b"{", b'{"x":1,"x":2}', b'{"x":NaN}', b"[]"])
def test_raw_schema_failures_are_atomic(factory, bad):
    adapter = factory()
    instrument, key = setup(adapter)
    raw = frame(adapter, instrument, key, bad, rest=True)
    result = adapter.decode(raw, instrument, key)
    assert result.error and not result.events and not result.controls
    assert result.error.raw_hash == raw.payload_sha256


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-1", "1e100", "0.0001"])
def test_invalid_level_fails_entire_message(bad):
    adapter = BybitAdapter()
    instrument, key = setup(adapter)
    payload = bybook(bids=[["99", "1"], ["98", bad]])
    result = adapter.decode(frame(adapter, instrument, key, payload), instrument, key)
    assert result.error and not result.events


@pytest.mark.parametrize(
    "bids,reason",
    [
        ([], "empty_side"),
        ([["102", "1"]], "crossed_or_locked"),
        ([["99", "1"], ["99", "2"]], "duplicate_snapshot_price"),
        ([["99", "0"]], "invalid_snapshot_quantity"),
    ],
)
def test_invalid_snapshot_never_becomes_usable(bids, reason):
    adapter = BybitAdapter()
    instrument, key = setup(adapter)
    reducer = adapter.new_reducer(instrument, key)
    result = reducer.apply(events(adapter, instrument, key, bybook(bids=bids)))
    assert result.recovery.reason == reason
    assert not reducer.synchronized


def test_trade_side_id_exactness_and_reconnect_dedup_key():
    adapter = BinanceGlobalAdapter()
    instrument, key = setup(adapter, channel="trades")
    payload = {
        "stream": "btcusdt@trade",
        "data": {
            "e": "trade",
            "s": "BTCUSDT",
            "t": 9007199254740993,
            "p": "99.00",
            "q": "0.001",
            "T": 1700000000001,
            "m": True,
        },
    }
    first = events(adapter, instrument, key, payload)[0]
    second = events(adapter, instrument, key, payload, 2, epoch=1)[0]
    assert first.payload.trade_id == "9007199254740993"
    assert first.payload.taker_side == "sell"
    assert first.payload.dedup_key == second.payload.dedup_key
    assert first.exchange_event_wall_ns == 1700000000001000000
    assert str(first.payload.price) == "99.00"
    del payload["data"]["m"]
    assert events(adapter, instrument, key, payload)[0].payload.taker_side == "unknown"


def test_bybit_trade_batch_wrong_symbol_is_atomic_and_unknown_side_retained():
    adapter = BybitAdapter()
    instrument, key = setup(adapter, channel="trades")
    row = {
        "T": 123,
        "s": "BTCUSDT",
        "S": "Buy",
        "v": "1.000",
        "p": "99.00",
        "i": "trade-1",
    }
    payload = {"topic": "publicTrade.BTCUSDT", "type": "snapshot", "data": [row]}
    assert events(adapter, instrument, key, payload)[0].payload.taker_side == "buy"
    row["S"] = "Unspecified"
    assert events(adapter, instrument, key, payload)[0].payload.taker_side == "unknown"
    payload["data"].append({**row, "s": "ETHUSDT"})
    result = adapter.decode(frame(adapter, instrument, key, payload), instrument, key)
    assert result.error and not result.events


def test_kucoin_trade_history_missing_continuity_and_unknown_aggressor():
    adapter = KuCoinAdapter()
    instrument, key = setup(adapter, channel="trades")
    payload = {
        "code": "200000",
        "data": [
            {
                "sequence": "9007199254740993",
                "price": "99.00",
                "size": "1.000",
                "side": "buy",
                "time": 1700000000000000001,
            }
        ],
    }
    first = events(adapter, instrument, key, payload, rest=True)[0]
    assert isinstance(first.payload, Trade)
    assert first.payload.taker_side == "unknown"
    assert first.payload.trade_id is None
    assert first.payload.is_subscription_history
    assert first.exchange_event_wall_ns == 1700000000000000001
    assert (
        first.payload.dedup_key
        == events(adapter, instrument, key, payload, 2, rest=True)[0].payload.dedup_key
    )


@pytest.mark.parametrize(
    "factory,error",
    [
        (BinanceGlobalAdapter, {"code": -1121, "msg": "Invalid symbol"}),
        (BybitAdapter, {"retCode": 10001, "retMsg": "bad symbol"}),
        (KuCoinAdapter, {"code": "400100", "msg": "bad symbol"}),
    ],
)
def test_endpoint_errors_explicit(factory, error):
    adapter = factory()
    instrument, key = setup(adapter)
    with pytest.raises(ValueError, match="endpoint error"):
        adapter.parse_metadata(encoded(error))
    result = adapter.decode(
        frame(adapter, instrument, key, error, rest=True), instrument, key
    )
    assert result.error


def test_subscriptions_controls_do_not_initialize_or_repair_books():
    adapter = BybitAdapter()
    instrument, key = setup(adapter)
    reducer = adapter.new_reducer(instrument, key)
    for index, payload in enumerate(
        ({"op": "subscribe", "success": True}, {"op": "pong"}), 1
    ):
        result = reducer.apply(events(adapter, instrument, key, payload, index))
        assert result.book is None and not reducer.initialized
    rejection = events(
        adapter,
        instrument,
        key,
        {"op": "subscribe", "success": False, "ret_msg": "unsupported"},
        3,
    )
    assert reducer.apply(rejection).recovery.reason == "subscription_rejected"


def test_us_fallback_keeps_quote_cohorts_and_execution_access_separate():
    adapter = BinanceUSAdapter()
    instruments, books = [], []
    for currency in ("USD", "USDT"):
        instrument, key = setup(adapter, currency=currency)
        instrument = replace(instrument, data_access="available")
        payload = {"stream": f"btc{currency.lower()}@depth20@100ms", "data": snapshot()}
        book = (
            adapter.new_reducer(instrument, key)
            .apply(events(adapter, instrument, key, payload))
            .book
        )
        instruments.append(instrument)
        books.append(book)
    cohort = evaluate_cohort(
        tuple(books), tuple(instruments), ClockReading("clock", 1000)
    )
    assert not cohort.eligible and "incompatible_instruments" in cohort.reasons
    informational = evaluate_cohort(
        (books[0],), (instruments[0],), ClockReading("clock", 1000)
    )
    assert informational.eligible
    execution = evaluate_cohort(
        (books[0],),
        (instruments[0],),
        ClockReading("clock", 1000),
        EligibilityPolicy(require_execution_access=True),
    )
    assert not execution.eligible


@pytest.mark.parametrize(
    "factory", [BinanceGlobalAdapter, BinanceUSAdapter, BybitAdapter, KuCoinAdapter]
)
def test_discovery_uses_shared_transport_exact_bytes(monkeypatch, factory):
    import asyncio
    from src.workstation import transport

    adapter = factory()
    raw = encoded(metadata(adapter))
    requests = []

    async def fetch(request):
        requests.append(request)
        return raw

    monkeypatch.setattr(transport, "fetch_public", fetch)
    discovered = asyncio.run(adapter.discover())
    assert len(requests) == 1 and requests[0].method == "GET"
    assert discovered.instruments[0].base.asset_id == "BTC"
    assert discovered.fetch_wall_ns > 0
    assert discovered.instruments[0].metadata_version.endswith(
        f":{discovered.fetch_wall_ns}"
    )


def test_bootstrap_snapshot_one_id_before_first_buffer_is_retried():
    adapter = BinanceGlobalAdapter()
    instrument, key = setup(adapter)
    reducer = adapter.new_reducer(instrument, key)
    reducer.apply(events(adapter, instrument, key, diff(101, 102), 1))
    assert (
        reducer.apply(
            events(adapter, instrument, key, snapshot(100), 2, rest=True)
        ).recovery.reason
        == "sequence_gap"
    )
    assert len(reducer.buffered_events) == 1
    assert (
        reducer.apply(
            events(adapter, instrument, key, snapshot(101), 3, rest=True)
        ).book
        is not None
    )


def test_bybit_thousand_level_fallback_uses_actual_requested_depth():
    adapter = BybitAdapter()
    instrument, key = setup(adapter)
    key = replace(key, depth=1000)
    assert adapter.snapshot_request(instrument, key).endpoint_id.endswith("limit=1000")
    assert (
        b"orderbook.1000.BTCUSDT"
        in adapter.subscription_requests(instrument, key)[0].body
    )


def bounded_global_book():
    adapter = BinanceGlobalAdapter()
    instrument, key = setup(adapter)
    reducer = adapter.new_reducer(instrument, key)
    bids = [[str(Decimal("10000") - Decimal(n) / 100), "1"] for n in range(5000)]
    asks = [[str(Decimal("10001") + Decimal(n) / 100), "1"] for n in range(5000)]
    reducer.apply(
        events(adapter, instrument, key, snapshot(bids=bids, asks=asks), 1, rest=True)
    )
    reducer.apply(events(adapter, instrument, key, diff(101, 101), 2))
    return adapter, instrument, key, reducer


@pytest.mark.parametrize("side,far_price", [("bid", "8000"), ("ask", "12000")])
def test_global_deep_changes_do_not_invent_bootstrap_coverage(side, far_price):
    adapter, instrument, key, reducer = bounded_global_book()
    before = reducer.book
    assert not before.quality.coverage.full_depth
    source = events(
        adapter,
        instrument,
        key,
        diff(102, 102, **{"bids" if side == "bid" else "asks": [[far_price, "1"]]}),
        3,
    )
    result = reducer.apply(source)
    assert result.recovery is None
    assert result.events == source
    assert reducer.sequence.last == "102"
    assert result.book.bids == before.bids and result.book.asks == before.asks
    assert result.book.quality.coverage == before.quality.coverage
    restored = adapter.new_reducer(instrument, key)
    restored.restore(reducer.checkpoint())
    follow = events(
        adapter, instrument, key, diff(103, 103, [["10000", "3"]], [["12001", "1"]]), 4
    )
    assert restored.apply(follow).book == reducer.apply(follow).book
    assert restored.checkpoint().state_hash == reducer.checkpoint().state_hash


def test_global_underfilled_bootstrap_knows_the_initial_complete_book():
    adapter = BinanceGlobalAdapter()
    instrument, key = setup(adapter)
    reducer = adapter.new_reducer(instrument, key)
    reducer.apply(events(adapter, instrument, key, snapshot(), 1, rest=True))
    result = reducer.apply(
        events(adapter, instrument, key, diff(101, 101, [["80", "1"]]), 2)
    )
    assert result.book.bids[-1].price == Decimal("80")
    assert result.book.quality.coverage.full_depth


def test_global_exhausted_known_depth_requires_fresh_snapshot():
    adapter, instrument, key, reducer = bounded_global_book()
    deletions = [[str(level.price), "0"] for level in reducer.book.bids]
    result = reducer.apply(
        events(
            adapter,
            instrument,
            key,
            diff(102, 102, deletions + [["8000", "1"]]),
            3,
        )
    )
    assert result.recovery.reason == "known_depth_exhausted"
    assert result.book.quality.integrity == "invalid"
    assert not reducer.synchronized


def test_global_checkpoint_rejects_inconsistent_boundary_without_mutation():
    from hashlib import sha256
    from src.workstation.wire import dumps, loads

    adapter, instrument, key, reducer = bounded_global_book()
    checkpoint = reducer.checkpoint()
    state = loads(checkpoint.reducer_state)
    bad_state = dumps(
        replace(
            state,
            protocol_state=encoded(
                {"version": 1, "bid_floor": "10000", "ask_ceiling": "10001"}
            ),
        ),
        canonical=False,
    )
    corrupt = replace(
        checkpoint, reducer_state=bad_state, state_hash=sha256(bad_state).hexdigest()
    )
    with pytest.raises(ValueError):
        reducer.restore(corrupt)
    assert reducer.checkpoint() == checkpoint


def test_global_native_candidate_rejects_inconsistent_boundary_without_mutation():
    adapter, instrument, key, reducer = bounded_global_book()
    checkpoint = reducer.checkpoint()
    candidate = adapter.new_reducer(instrument, key)
    candidate.restore(checkpoint)
    candidate.protocol_state = encoded(
        {"version": 1, "bid_floor": "10000", "ask_ceiling": "10001"}
    )
    with pytest.raises(ValueError):
        reducer._adopt_candidate(candidate)
    assert reducer.checkpoint() == checkpoint
