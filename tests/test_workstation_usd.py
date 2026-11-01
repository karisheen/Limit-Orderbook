"""Synthetic offline fixtures, plus Kraken's published CRC example (linked in docs)."""

import asyncio
from dataclasses import replace
from decimal import Decimal
import json
import zlib

import pytest

from src.workstation.adapters.base import parse_rfc3339_ns, wrap_decoded
from src.workstation.adapters.coinbase import CoinbaseAdapter
from src.workstation.adapters.kraken import KrakenAdapter, kraken_checksum
from src.workstation.book import BookReducer, Level
from src.workstation.events import (
    BookUpdate,
    ChecksumInfo,
    Control,
    RawFrame,
    SegmentId,
    SessionId,
    StreamId,
    SubscriptionKey,
    Trade,
)
from src.workstation.ingestion import make_snapshot_reducer
from src.workstation.instruments import (
    AssetId,
    AssetIdentity,
    Instrument,
    InstrumentCatalog,
    InstrumentId,
)
from src.workstation.precision import exact_json_loads


def instrument(adapter):
    symbol = "BTC/USD" if adapter.venue == "kraken" else "BTC-USD"
    return Instrument(
        InstrumentId(f"{adapter.venue}:{adapter.api_family}:spot:{symbol}"),
        "fixture-v1",
        adapter.venue,
        adapter.api_family,
        symbol,
        AssetIdentity(AssetId("BTC"), "BTC"),
        AssetIdentity(AssetId("USD"), "USD"),
        Decimal("0.1"),
        Decimal("0.00000001"),
        native_asset_id="XXBTZUSD" if adapter.venue == "kraken" else symbol,
    )


def setup(adapter, channel="book"):
    item = instrument(adapter)
    key = SubscriptionKey.for_instrument(
        item,
        channel=channel,
        depth=25 if adapter.venue == "kraken" and channel == "book" else None,
    )
    return item, key, adapter.new_reducer(item, key)


def frame(payload, ordinal=0, transport="ws", epoch=0):
    raw = (
        payload
        if isinstance(payload, bytes)
        else json.dumps(payload, separators=(",", ":")).encode()
    )
    return RawFrame(
        SessionId("fixture"),
        StreamId("stream"),
        "connection",
        epoch,
        "clock",
        ordinal,
        1_000_000_000 + ordinal,
        100 + ordinal,
        transport,
        "fixture",
        raw,
    )


def apply(adapter, item, key, reducer, payload, ordinal=0, epoch=0):
    raw = frame(payload, ordinal, epoch=epoch)
    result = adapter.decode(raw, item, key)
    assert result.error is None, result.error
    envelopes = wrap_decoded(raw, item, key, result, SegmentId("initial"), "ws_delta")
    return reducer.apply(envelopes)


def crc(bids, asks):
    # Independent fixture oracle: incoming arrays are already sorted final levels.
    digits = "".join(
        str(value).replace(".", "").lstrip("0")
        for rows in (asks[:10], bids[:10])
        for row in rows
        for value in row
    )
    return zlib.crc32(digits.encode()) & 0xFFFFFFFF


def kraken_message(bids, asks, kind="snapshot", checksum=None, symbol="BTC/USD"):
    return {
        "channel": "book",
        "type": kind,
        "data": [
            {
                "symbol": symbol,
                "bids": [{"price": price, "qty": size} for price, size in bids],
                "asks": [{"price": price, "qty": size} for price, size in asks],
                "checksum": crc(bids, asks) if checksum is None else checksum,
            }
        ],
    }


def kraken_rest_message():
    return {
        "error": [],
        "result": {
            "XXBTZUSD": {
                "bids": [["100.1000", "0.12345678", 1793502000]],
                "asks": [["102.2000", "2.00000000", 1793501990]],
            }
        },
    }


def test_kraken_rest_snapshot_preserves_precision_and_bounded_degraded_coverage():
    adapter = KrakenAdapter()
    item, key, ws_reducer = setup(adapter)
    request = adapter.snapshot_request(item, key)
    assert request.method == "GET"
    assert request.endpoint_id == (
        "https://api.kraken.com/0/public/Depth?pair=XXBTZUSD&count=25"
    )
    trade_key = SubscriptionKey.for_instrument(item, channel="trades")
    assert adapter.snapshot_request(item, trade_key) is None
    reducer = make_snapshot_reducer(adapter, item, key, poll_interval=2)
    payload = kraken_rest_message()
    payload["result"]["XXBTZUSD"]["bids"][0][0] = "9007199254740993.1000"
    payload["result"]["XXBTZUSD"]["asks"][0][0] = "9007199254740995.2000"
    raw = replace(frame(payload, transport="rest"), endpoint_id=request.endpoint_id)
    decoded = adapter.decode(raw, item, key)
    assert decoded.error is None
    event = decoded.events[0]
    assert event.payload.subscribed_depth == 25
    assert event.sequence is None and event.checksum is None
    assert event.exchange_event_wall_ns is None and event.exchange_time_kind is None
    events = wrap_decoded(
        raw, item, key, decoded, SegmentId("initial"), "rest_snapshot"
    )
    reduced = reducer.apply(events)
    assert reduced.recovery is None
    assert str(reduced.book.bids[0].price) == "9007199254740993.1000"
    assert str(reduced.book.bids[0].quantity) == "0.12345678"
    assert str(reduced.book.asks[0].price) == "9007199254740995.2000"
    assert str(reduced.book.asks[0].quantity) == "2.00000000"
    quality = reduced.book.quality
    assert quality.lifecycle == "degraded" and quality.integrity == "valid"
    assert quality.source_mode == "rest_snapshot"
    assert quality.coverage.subscribed_depth == 25
    assert (
        quality.coverage.retained_bid_levels
        == quality.coverage.retained_ask_levels
        == 1
    )
    assert quality.coverage.expected_cadence_ns == 2_000_000_000
    assert not quality.coverage.full_depth
    assert not quality.capabilities.deltas and not quality.capabilities.exact_event_flow
    assert quality.capabilities.checksum_scope is None
    assert quality.capabilities.sequence_scope is None

    # REST observations replace, never incrementally merge or bootstrap WS CRC.
    raw = replace(
        frame(kraken_rest_message(), 1, transport="rest"),
        endpoint_id=request.endpoint_id,
    )
    decoded = adapter.decode(raw, item, key)
    assert decoded.error is None
    events = wrap_decoded(
        raw, item, key, decoded, SegmentId("initial"), "rest_snapshot"
    )
    reduced = reducer.apply(events)
    assert reduced.recovery is None
    assert len(reduced.book.bids) == 1
    assert reduced.book.bids[0].price == Decimal("100.1000")
    assert ws_reducer.apply(events).recovery.reason == "checksum_missing"
    assert ws_reducer.book is None


@pytest.mark.parametrize(
    "fault",
    [
        "endpoint",
        "pair",
        "count",
        "query",
        "ws",
        "http",
        "trade_key",
        "wrong_symbol",
        "extra_symbol",
        "error",
        "error_type",
        "missing_error",
        "missing_side",
        "side_type",
        "short_level",
        "long_level",
        "numeric_price",
        "negative_quantity",
        "nonfinite_price",
        "bad_timestamp",
    ],
)
def test_kraken_rest_rejects_wrong_identity_transport_and_schema(fault):
    adapter = KrakenAdapter()
    item, key, _ = setup(adapter)
    request = adapter.snapshot_request(item, key)
    endpoint, transport = request.endpoint_id, "rest"
    payload = kraken_rest_message()
    book = payload["result"]["XXBTZUSD"]
    if fault == "endpoint":
        endpoint = endpoint.replace("/Depth", "/Trades")
    elif fault == "pair":
        endpoint = endpoint.replace("XXBTZUSD", "XBTUSD")
    elif fault == "count":
        endpoint = endpoint.replace("count=25", "count=10")
    elif fault == "query":
        endpoint = adapter.book_url
    elif fault in ("ws", "http"):
        transport = fault
    elif fault == "trade_key":
        key = SubscriptionKey.for_instrument(item, channel="trades")
    elif fault == "wrong_symbol":
        payload["result"] = {"XBTUSD": book}
    elif fault == "extra_symbol":
        payload["result"]["XETHZUSD"] = book
    elif fault == "error":
        payload["error"] = ["EQuery:Unknown asset pair"]
    elif fault == "error_type":
        payload["error"] = {}
    elif fault == "missing_error":
        del payload["error"]
    elif fault == "missing_side":
        del book["asks"]
    elif fault == "side_type":
        book["asks"] = {}
    elif fault == "short_level":
        book["bids"][0].pop()
    elif fault == "long_level":
        book["bids"][0].append("extra")
    elif fault == "numeric_price":
        book["bids"][0][0] = 100
    elif fault == "negative_quantity":
        book["bids"][0][1] = "-1"
    elif fault == "nonfinite_price":
        book["bids"][0][0] = "NaN"
    else:
        book["bids"][0][2] = -1
    raw = replace(frame(payload, transport=transport), endpoint_id=endpoint)
    decoded = adapter.decode(raw, item, key)
    assert decoded.error is not None
    assert not decoded.events and not decoded.controls


def test_kraken_rest_payload_cannot_masquerade_as_websocket_data():
    adapter = KrakenAdapter()
    item, key, _ = setup(adapter)
    decoded = adapter.decode(
        replace(frame(kraken_rest_message()), endpoint_id=adapter.websocket_url),
        item,
        key,
    )
    assert decoded.error is not None
    assert not decoded.events and not decoded.controls


def test_kraken_rest_requires_discovered_native_pair_identity():
    adapter = KrakenAdapter()
    item, key, _ = setup(adapter)
    with pytest.raises(ValueError, match="native pair identity"):
        adapter.snapshot_request(replace(item, native_asset_id=None), key)


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("tick", "price is off increment"),
        ("crossed", "crossed_or_locked"),
        ("zero_size", "invalid_snapshot_quantity"),
        ("duplicate", "duplicate_snapshot_price"),
    ],
)
def test_kraken_rest_snapshot_reducer_retains_book_validation(fault, reason):
    adapter = KrakenAdapter()
    item, key, _ = setup(adapter)
    payload = kraken_rest_message()
    book = payload["result"]["XXBTZUSD"]
    if fault == "tick":
        book["bids"][0][0] = "100.01"
    elif fault == "crossed":
        book["bids"][0][0] = "103.0"
    elif fault == "zero_size":
        book["bids"][0][1] = "0"
    else:
        book["bids"] *= 2
    raw = replace(
        frame(payload, transport="rest"),
        endpoint_id=adapter.snapshot_request(item, key).endpoint_id,
    )
    decoded = adapter.decode(raw, item, key)
    assert decoded.error is None
    reducer = make_snapshot_reducer(adapter, item, key)
    events = wrap_decoded(
        raw, item, key, decoded, SegmentId("initial"), "rest_snapshot"
    )
    result = reducer.apply(events)
    assert result.recovery.reason == reason
    assert result.book is None


def coinbase_message(seq, kind="snapshot", updates=None, symbol="BTC-USD"):
    if updates is None:
        updates = [
            {"side": "bid", "price_level": "100.0", "new_quantity": "1.00000000"},
            {"side": "offer", "price_level": "102.0", "new_quantity": "2.00000000"},
        ]
    return {
        "channel": "l2_data",
        "sequence_num": seq,
        "timestamp": "2026-10-31T10:00:00.123456789Z",
        "events": [{"type": kind, "product_id": symbol, "updates": updates}],
    }


def trade_message(adapter, seq=0):
    if adapter.venue == "coinbase":
        return {
            "channel": "market_trades",
            "sequence_num": seq,
            "events": [
                {
                    "type": "snapshot",
                    "trades": [
                        {
                            "product_id": "BTC-USD",
                            "trade_id": "9007199254740993",
                            "price": "100.1",
                            "size": "0.1",
                            "side": "SELL",
                            "time": "2026-10-31T10:00:00.000000001Z",
                        }
                    ],
                }
            ],
        }
    return {
        "channel": "trade",
        "type": "snapshot",
        "data": [
            {
                "symbol": "BTC/USD",
                "trade_id": 9007199254740993,
                "price": "100.1",
                "qty": "0.1",
                "side": "buy",
            }
        ],
    }


def test_kraken_truncates_25_after_ordered_whole_message():
    adapter = KrakenAdapter()
    item, key, reducer = setup(adapter)
    bids = [(f"{100 - i}.0", "1.00000000") for i in range(25)]
    asks = [("103.0", "1.00000000")]
    initial = apply(adapter, item, key, reducer, kraken_message(bids, asks))
    assert initial.book.quality.integrity == "valid"
    final_bids = [("101.00", "3.00000000"), *bids[:24]]
    changes = [("101.0", "1.00000000"), ("101.00", "3.00000000")]
    result = apply(
        adapter,
        item,
        key,
        reducer,
        kraken_message(changes, [], "update", crc(final_bids, asks)),
        1,
    )
    assert result.recovery is None
    assert len(result.book.bids) == 25
    assert result.book.bids[-1].price == Decimal("77")
    assert str(result.book.bids[0].price) == "101.00"
    assert str(result.book.bids[0].quantity) == "3.00000000"
    assert result.events[0].checksum.result == "match"
    assert not result.book.quality.coverage.full_depth


def test_kraken_validates_only_final_message_not_transient_cross():
    adapter = KrakenAdapter()
    item, key, reducer = setup(adapter)
    apply(
        adapter, item, key, reducer, kraken_message([("100.0", "1")], [("102.0", "1")])
    )
    # Intermediate insertion crosses; the later same-price deletion repairs it inside this one message.
    result = apply(
        adapter,
        item,
        key,
        reducer,
        kraken_message(
            [("103.0", "1"), ("103.0", "0"), ("100.0", "2")],
            [],
            "update",
            crc([("100.0", "2")], [("102.0", "1")]),
        ),
        1,
    )
    assert result.recovery is None
    assert result.book.bids[0].quantity == 2


def test_kraken_lexical_numeric_precision_and_published_crc():
    bid_rows = [
        ("45283.5", "0.10000000"),
        ("45283.4", "1.54582015"),
        ("45282.1", "0.10000000"),
        ("45281.0", "0.10000000"),
        ("45280.3", "1.54592586"),
        ("45279.0", "0.07990000"),
        ("45277.6", "0.03310103"),
        ("45277.5", "0.30000000"),
        ("45277.3", "1.54602737"),
        ("45276.6", "0.15445238"),
    ]
    ask_rows = [
        ("45285.2", "0.00100000"),
        ("45286.4", "1.54571953"),
        ("45286.6", "1.54571109"),
        ("45289.6", "1.54560911"),
        ("45290.2", "0.15890660"),
        ("45291.8", "1.54553491"),
        ("45294.7", "0.04454749"),
        ("45296.1", "0.35380000"),
        ("45297.5", "0.09945542"),
        ("45299.5", "0.18772827"),
    ]

    def levels(rows):
        return tuple(Level(Decimal(price), Decimal(size)) for price, size in rows)

    assert (
        kraken_checksum(
            levels(bid_rows),
            levels(ask_rows),
            ChecksumInfo("crc32", "3310070434", scope="top10"),
        )
        == "3310070434"
    )
    adapter = KrakenAdapter()
    item, key, reducer = setup(adapter)
    # JSON numbers, not pre-rounded floats or strings: trailing zeros are checksum-significant.
    raw = b'{"channel":"book","type":"snapshot","data":[{"symbol":"BTC/USD","bids":[{"price":100.00,"qty":0.10000000}],"asks":[{"price":102.00,"qty":1.00000000}],"checksum":CHECK}]}'
    raw = raw.replace(
        b"CHECK",
        str(crc([("100.00", "0.10000000")], [("102.00", "1.00000000")])).encode(),
    )
    result = apply(adapter, item, key, reducer, raw)
    assert result.recovery is None
    assert str(result.book.bids[0].quantity) == "0.10000000"


def test_kraken_requires_snapshot_and_checksum_failure_invalidates():
    adapter = KrakenAdapter()
    item, key, reducer = setup(adapter)
    payload = kraken_message([("100.0", "1")], [("102.0", "1")], "update")
    result = apply(adapter, item, key, reducer, payload)
    assert result.book is None
    assert result.recovery.reason == "snapshot_required"
    payload["type"] = "snapshot"
    assert (
        apply(adapter, item, key, reducer, payload, 1).book.quality.integrity == "valid"
    )
    broken = kraken_message([("100.0", "2")], [], "update", checksum=0)
    result = apply(adapter, item, key, reducer, broken, 2)
    assert result.recovery.reason == "checksum_mismatch"
    assert result.book.quality.integrity == "invalid"
    result = apply(adapter, item, key, reducer, {"channel": "heartbeat"}, 3)
    assert reducer.quality.integrity == "invalid"
    assert result.book is None


@pytest.mark.parametrize(
    "fault",
    [
        "wrong_symbol",
        "missing_crc",
        "negative",
        "nonfinite",
        "precision",
        "unknown_channel",
    ],
)
def test_kraken_malformed_frames_never_emit_partial_events(fault):
    adapter = KrakenAdapter()
    item, key, _ = setup(adapter)
    payload = kraken_message([("100.0", "1")], [("102.0", "1")])
    row = payload["data"][0]
    if fault == "wrong_symbol":
        row["symbol"] = "ETH/USD"
    elif fault == "missing_crc":
        del row["checksum"]
    elif fault == "unknown_channel":
        payload["channel"] = "ticker"
    else:
        row["bids"][0]["qty"] = {
            "negative": "-1",
            "nonfinite": "NaN",
            "precision": "0." + "1" * 39,
        }[fault]
    result = adapter.decode(frame(payload), item, key)
    assert result.error is not None
    assert not result.events and not result.controls


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("duplicate", "duplicate_snapshot_price"),
        ("empty", "empty_side"),
        ("crossed", "crossed_or_locked"),
        ("tick", "price is off increment"),
    ],
)
def test_kraken_invalid_snapshot_reducer(fault, reason):
    adapter = KrakenAdapter()
    item, key, reducer = setup(adapter)
    bids = [("100.0", "1")]
    asks = [("102.0", "1")]
    if fault == "duplicate":
        bids *= 2
    elif fault == "empty":
        asks = []
    elif fault == "crossed":
        bids = [("103.0", "1")]
    else:
        bids = [("100.01", "1")]
    result = apply(adapter, item, key, reducer, kraken_message(bids, asks))
    assert result.recovery.reason == reason
    assert result.book is None


def test_coinbase_independent_book_and_trade_connections_consume_control_sequences():
    adapter = CoinbaseAdapter()
    item, book_key, book = setup(adapter)
    _, trade_key, trades = setup(adapter, "trades")
    for key, reducer, channel in (
        (book_key, book, "level2"),
        (trade_key, trades, "market_trades"),
    ):
        ack = {
            "channel": "subscriptions",
            "sequence_num": 0,
            "events": [
                {"subscriptions": {channel: ["BTC-USD"], "heartbeats": ["heartbeats"]}}
            ],
        }
        result = apply(adapter, item, key, reducer, ack)
        assert result.recovery is None
        assert result.events[0].payload == Control("ack")
        heartbeat = {"channel": "heartbeats", "sequence_num": 1, "events": [{}]}
        result = apply(adapter, item, key, reducer, heartbeat, 1)
        assert result.recovery is None
        assert result.events[0].payload == Control("heartbeat")
        assert reducer.sequence.last == "1"

    result = apply(adapter, item, book_key, book, coinbase_message(2), 2)
    assert result.recovery is None
    assert result.book.quality.coverage.full_depth
    payload = trade_message(adapter, 2)
    payload["events"][0]["trades"][0]["side"] = "BUY"
    result = apply(adapter, item, trade_key, trades, payload, 2)
    assert result.recovery is None and result.book is None
    trade = result.events[0].payload
    assert isinstance(trade, Trade)
    assert trade.taker_side == "sell"
    assert trade.side_provenance == "coinbase_maker_side_inverted"
    assert trade.is_subscription_history
    assert trade.trade_id == "9007199254740993"

    result = apply(
        adapter,
        item,
        book_key,
        book,
        coinbase_message(
            3, "update", [{"side": "bid", "price_level": "100.0", "new_quantity": "3"}]
        ),
        3,
    )
    assert result.recovery is None
    assert result.book.bids[0].quantity == 3  # absolute, not previous + 3
    assert book.sequence.last == "3" and trades.sequence.last == "2"
    empty = {
        "channel": "market_trades",
        "sequence_num": 3,
        "events": [{"type": "update", "trades": []}],
    }
    assert apply(adapter, item, trade_key, trades, empty, 3).recovery is None
    assert trades.sequence.last == "3"

    gap = {"channel": "heartbeats", "sequence_num": 5, "events": [{}]}
    assert (
        apply(adapter, item, book_key, book, gap, 4).recovery.reason == "sequence_gap"
    )
    heartbeat = {"channel": "heartbeats", "sequence_num": 4, "events": [{}]}
    assert apply(adapter, item, trade_key, trades, heartbeat, 4).recovery is None
    assert trades.sequence.last == "4"
    gap["sequence_num"] = 6
    assert (
        apply(adapter, item, trade_key, trades, gap, 5).recovery.reason
        == "sequence_gap"
    )


@pytest.mark.parametrize(
    "channel,wire_channel,ack_sequence",
    [("book", "level2", 3), ("trades", "market_trades", 2)],
)
def test_coinbase_connection_scoped_heartbeat_ack_preserves_sequence(
    channel, wire_channel, ack_sequence
):
    adapter = CoinbaseAdapter()
    item, key, reducer = setup(adapter, channel)
    initial = coinbase_message(0) if channel == "book" else trade_message(adapter)
    assert apply(adapter, item, key, reducer, initial).recovery is None
    heartbeat = {"channel": "heartbeats", "sequence_num": 1, "events": [{}]}
    assert apply(adapter, item, key, reducer, heartbeat, 1).recovery is None
    if channel == "book":
        assert (
            apply(
                adapter, item, key, reducer, coinbase_message(2, "update"), 2
            ).recovery
            is None
        )
    # Captured public acknowledgments identify the heartbeat connection by
    # ["heartbeats"], while the data subscription retains its product identity.
    subscriptions = {"heartbeats": ["heartbeats"], wire_channel: ["BTC-USD"]}
    ack = {
        "channel": "subscriptions",
        "timestamp": "2026-11-01T02:56:25.094520362Z",
        "sequence_num": ack_sequence,
        "events": [{"subscriptions": subscriptions}],
    }
    result = apply(adapter, item, key, reducer, ack, ack_sequence)
    assert result.recovery is None
    assert result.events[0].payload == Control("ack")
    assert reducer.sequence.last == str(ack_sequence)
    heartbeat["sequence_num"] = ack_sequence + 1
    assert (
        apply(adapter, item, key, reducer, heartbeat, ack_sequence + 1).recovery is None
    )
    assert reducer.sequence.last == str(ack_sequence + 1)

    for field, wrong_identity in (
        (wire_channel, ["ETH-USD"]),
        (wire_channel, ["heartbeats"]),
        ("heartbeats", ["BTC-USD"]),
        ("heartbeats", "heartbeats"),
    ):
        malformed = {
            **ack,
            "events": [{"subscriptions": {**subscriptions, field: wrong_identity}}],
        }
        decoded = adapter.decode(frame(malformed), item, key)
        assert decoded.error is not None
        assert not decoded.events and not decoded.controls


def test_coinbase_duplicates_ignored_gaps_and_obsolete_epochs_rejected():
    adapter = CoinbaseAdapter()
    item, key, reducer = setup(adapter)
    apply(adapter, item, key, reducer, coinbase_message(0))
    duplicate = coinbase_message(
        0, "update", [{"side": "bid", "price_level": "100.0", "new_quantity": "9"}]
    )
    result = apply(adapter, item, key, reducer, duplicate, 1)
    assert result.events == () and result.book.bids[0].quantity == 1
    assert (
        apply(
            adapter, item, key, reducer, coinbase_message(2, "update"), 2
        ).recovery.reason
        == "sequence_gap"
    )
    reducer.reset(1, SegmentId("initial"))
    result = apply(adapter, item, key, reducer, coinbase_message(0), 3, epoch=0)
    assert result.recovery.reason == "obsolete_epoch"
    assert reducer.book is None


def test_coinbase_snapshot_cannot_hide_missing_initial_or_later_frame():
    adapter = CoinbaseAdapter()
    item, key, reducer = setup(adapter)
    assert (
        apply(adapter, item, key, reducer, coinbase_message(1)).recovery.reason
        == "sequence_gap"
    )
    reducer.reset(0, SegmentId("initial"))
    apply(adapter, item, key, reducer, coinbase_message(0))
    assert (
        apply(adapter, item, key, reducer, coinbase_message(2), 1).recovery.reason
        == "sequence_gap"
    )


def test_coinbase_delta_and_heartbeat_before_snapshot_never_valid():
    adapter = CoinbaseAdapter()
    item, key, reducer = setup(adapter)
    heartbeat = {"channel": "heartbeats", "sequence_num": 0, "events": [{}]}
    assert apply(adapter, item, key, reducer, heartbeat).book is None
    assert (
        apply(
            adapter, item, key, reducer, coinbase_message(1, "update"), 1
        ).recovery.reason
        == "snapshot_required"
    )


def test_coinbase_full_depth_beyond_observed_40000_levels_and_four_megabytes():
    adapter = CoinbaseAdapter()
    item, key, reducer = setup(adapter)
    updates = [
        {
            "side": "bid",
            "price_level": f"{50000 - index}.0",
            "new_quantity": "1.00000000",
            "event_time": "2026-10-31T10:00:00.123456789Z",
        }
        for index in range(40405)
    ]
    updates.append({"side": "offer", "price_level": "50001.0", "new_quantity": "1"})
    payload = coinbase_message(0, updates=updates)
    raw = frame(payload)
    assert len(raw.payload) > 4 * 1024 * 1024
    result = apply(adapter, item, key, reducer, payload)
    assert result.recovery is None
    assert len(result.book.bids) == 40405
    assert result.book.quality.coverage.full_depth
    assert reducer.max_levels == 100000 and not reducer.truncate_depth


def test_coinbase_capacity_exhaustion_invalidates_instead_of_truncating():
    adapter = CoinbaseAdapter()
    item, key, reducer = setup(adapter)
    reducer.max_levels = 1
    updates = [
        {"side": "bid", "price_level": "100.0", "new_quantity": "1"},
        {"side": "bid", "price_level": "99.0", "new_quantity": "1"},
        {"side": "offer", "price_level": "102.0", "new_quantity": "1"},
    ]
    assert (
        apply(
            adapter, item, key, reducer, coinbase_message(0, updates=updates)
        ).recovery.reason
        == "book_capacity_exceeded"
    )


@pytest.mark.parametrize(
    "fault",
    [
        "wrong_symbol",
        "side",
        "sequence",
        "negative",
        "nonfinite",
        "bad_time",
        "exchange_schema",
    ],
)
def test_coinbase_rejects_invalid_payloads(fault):
    adapter = CoinbaseAdapter()
    item, key, _ = setup(adapter)
    payload = coinbase_message(0)
    if fault == "wrong_symbol":
        payload["events"][0]["product_id"] = "ETH-USD"
    elif fault == "side":
        payload["events"][0]["updates"][0]["side"] = "buy"
    elif fault == "sequence":
        payload["sequence_num"] = True
    elif fault in ("negative", "nonfinite"):
        payload["events"][0]["updates"][0]["new_quantity"] = (
            "-1" if fault == "negative" else "Infinity"
        )
    elif fault == "bad_time":
        payload["timestamp"] = "2026-10-31T10:00:00.1234567891Z"
    else:
        payload = {
            "type": "snapshot",
            "product_id": "BTC-USD",
            "bids": [["100", "1"]],
            "asks": [["102", "1"]],
        }
    result = adapter.decode(frame(payload), item, key)
    assert result.error is not None and not result.events


def test_coinbase_native_rest_snapshot_is_not_ws_bootstrap():
    adapter = CoinbaseAdapter()
    item, key, _ = setup(adapter)
    payload = {
        "pricebook": {
            "product_id": "BTC-USD",
            "bids": [{"price": "100.0", "size": "1"}],
            "asks": [{"price": "102.0", "size": "2"}],
            "time": "2026-10-31T10:00:00Z",
        }
    }
    raw = frame(payload, transport="rest")
    decoded = adapter.decode(raw, item, key)
    assert decoded.error is None and decoded.events[0].sequence is None
    reducer = BookReducer(item, key)
    events = wrap_decoded(
        raw, item, key, decoded, SegmentId("initial"), "rest_snapshot"
    )
    result = reducer.apply(events)
    assert result.book.quality.lifecycle == "degraded"
    assert not result.book.quality.coverage.full_depth
    assert adapter.decode(frame(payload), item, key).error is not None
    request = adapter.snapshot_request(item, key)
    assert "/api/v3/brokerage/market/product_book?" in request.endpoint_id
    assert "api.exchange.coinbase.com" not in request.endpoint_id


@pytest.mark.parametrize("adapter", [KrakenAdapter(), CoinbaseAdapter()])
def test_channel_capabilities_and_immutable_trade_dedup(adapter):
    item, book_key, _ = setup(adapter)
    _, key, _ = setup(adapter, "trades")
    book_caps = adapter.capabilities(item, book_key)
    caps = adapter.capabilities(item, key)
    assert book_caps.snapshot and book_caps.deltas and book_caps.exact_event_flow
    assert not book_caps.trades
    assert caps.trades and not caps.snapshot and not caps.deltas
    assert not caps.exact_event_flow
    if adapter.venue == "kraken":
        assert book_caps.checksum_scope == "top10"
        assert caps.checksum_scope is None and caps.sequence_scope is None
    else:
        assert caps.sequence_scope == book_caps.sequence_scope == "connection"
    payload = trade_message(adapter)
    first = adapter.decode(frame(payload), item, key).events[0].payload
    second = adapter.decode(frame(payload, 10, epoch=2), item, key).events[0].payload
    assert first.dedup_key == second.dedup_key and first.taker_side == "buy"
    with pytest.raises((AttributeError, TypeError)):
        first.dedup_key = "changed"
    with pytest.raises(ValueError):
        adapter.capabilities(item, replace(key, depth=7))
    with pytest.raises(ValueError):
        adapter.capabilities(item, replace(key, aggregation=(("display_depth", "10"),)))


@pytest.mark.parametrize("adapter", [KrakenAdapter(), CoinbaseAdapter()])
def test_trade_increments_preserve_exact_good_values(adapter):
    item, key, _ = setup(adapter, "trades")
    payload = trade_message(adapter)
    row = (
        payload["data"][0]
        if adapter.venue == "kraken"
        else payload["events"][0]["trades"][0]
    )
    row["price"] = "100.1000"
    row["qty" if adapter.venue == "kraken" else "size"] = "0.12345678"
    result = adapter.decode(frame(payload), item, key)
    assert result.error is None
    trade = result.events[0].payload
    assert str(trade.price) == "100.1000"
    assert str(trade.quantity) == "0.12345678"
    assert trade.taker_side == "buy" and trade.is_subscription_history
    assert trade.trade_id == "9007199254740993"


@pytest.mark.parametrize("adapter", [KrakenAdapter(), CoinbaseAdapter()])
@pytest.mark.parametrize(
    "field,value", [("price", "100.01"), ("quantity", "0.000000001")]
)
def test_trade_off_increment_rejects_entire_batch(adapter, field, value):
    item, key, _ = setup(adapter, "trades")
    payload = trade_message(adapter)
    rows = (
        payload["data"] if adapter.venue == "kraken" else payload["events"][0]["trades"]
    )
    bad = dict(rows[0])
    bad[
        "price" if field == "price" else "qty" if adapter.venue == "kraken" else "size"
    ] = value
    rows.append(bad)
    raw = frame(payload)
    result = adapter.decode(raw, item, key)
    assert result.error is not None
    assert result.error.raw_hash == raw.payload_sha256
    assert not result.events and not result.controls


@pytest.mark.parametrize("adapter", [KrakenAdapter(), CoinbaseAdapter()])
@pytest.mark.parametrize("channel", ["book", "trades"])
def test_data_channel_cannot_cross_subscription_keys(adapter, channel):
    item, key, _ = setup(adapter, channel)
    payload = (
        trade_message(adapter)
        if channel == "book"
        else coinbase_message(0)
        if adapter.venue == "coinbase"
        else kraken_message([("100.0", "1")], [("102.0", "1")])
    )
    result = adapter.decode(frame(payload), item, key)
    assert result.error is not None
    assert not result.events and not result.controls


@pytest.mark.parametrize("events", [[], [{"type": "update", "trades": []}]])
def test_coinbase_empty_trade_batches_belong_only_to_trade_connection(events):
    adapter = CoinbaseAdapter()
    item, book_key, _ = setup(adapter)
    _, trade_key, reducer = setup(adapter, "trades")
    payload = {"channel": "market_trades", "sequence_num": 0, "events": events}
    result = adapter.decode(frame(payload), item, book_key)
    assert result.error is not None
    assert not result.events and not result.controls
    result = apply(adapter, item, trade_key, reducer, payload)
    assert result.recovery is None
    assert result.events[0].payload == Control("heartbeat")
    assert reducer.sequence.last == "0"
    heartbeat = {"channel": "heartbeats", "sequence_num": 1, "events": [{}]}
    assert apply(adapter, item, trade_key, reducer, heartbeat, 1).recovery is None
    assert reducer.sequence.last == "1"


@pytest.mark.parametrize(
    "channel,fault",
    [
        ("book", None),
        ("book", "channel"),
        ("book", "depth"),
        ("book", "symbol"),
        ("trades", None),
        ("trades", "channel"),
        ("trades", "symbol"),
    ],
)
def test_kraken_subscription_ack_matches_connection(channel, fault):
    adapter = KrakenAdapter()
    item, key, reducer = setup(adapter, channel)
    result = {
        "channel": "book" if channel == "book" else "trade",
        "symbol": item.native_symbol,
    }
    if channel == "book":
        result["depth"] = key.depth
    if fault == "channel":
        result["channel"] = "trade" if channel == "book" else "book"
        result["depth"] = 25
    elif fault == "depth":
        result["depth"] = 10
    elif fault == "symbol":
        result["symbol"] = "ETH/USD"
    payload = {"method": "subscribe", "success": True, "result": result}
    if fault:
        decoded = adapter.decode(frame(payload), item, key)
        assert decoded.error is not None
        assert not decoded.events and not decoded.controls
    else:
        reduced = apply(adapter, item, key, reducer, payload)
        assert reduced.recovery is None
        assert reduced.events[0].payload == Control("ack")
        heartbeat = apply(adapter, item, key, reducer, {"channel": "heartbeat"}, 1)
        assert heartbeat.recovery is None
        assert heartbeat.events[0].payload == Control("heartbeat")


@pytest.mark.parametrize("adapter", [KrakenAdapter(), CoinbaseAdapter()])
def test_physical_subscriptions_deliver_each_feed_on_only_its_own_connection(adapter):
    def exchange_messages(requests):
        # A subscription-driven exchange simulator: every requested data feed
        # produces a frame, including accidental duplicate physical trade feeds.
        for request in requests:
            body = exact_json_loads(request.body)
            if adapter.venue == "coinbase":
                channel = body["channel"]
                symbol = body["product_ids"][0]
                if channel not in ("level2", "market_trades", "heartbeats"):
                    yield {"type": "error", "message": "unsupported channel"}
                    continue
                yield {
                    "channel": "subscriptions",
                    "events": [
                        {
                            "subscriptions": {
                                channel: ["heartbeats"]
                                if channel == "heartbeats"
                                else [symbol]
                            }
                        }
                    ],
                }
                if channel == "level2":
                    yield coinbase_message(0, symbol=symbol)
                elif channel == "market_trades":
                    payload = trade_message(adapter)
                    payload["events"][0]["trades"][0]["product_id"] = symbol
                    yield payload
                else:
                    yield {"channel": "heartbeats", "events": [{}]}
            else:
                params = body["params"]
                channel = params["channel"]
                symbol = params["symbol"][0]
                if channel not in ("book", "trade"):
                    # Kraken heartbeat is automatic, not a subscribable channel.
                    yield {
                        "method": "subscribe",
                        "success": False,
                        "error": "unsupported channel",
                    }
                    continue
                result = {"channel": channel, "symbol": symbol}
                if channel == "book":
                    result["depth"] = params["depth"]
                yield {"method": "subscribe", "success": True, "result": result}
                if params.get("snapshot"):
                    if channel == "book":
                        yield kraken_message(
                            [("100.0", "1")], [("102.0", "1")], symbol=symbol
                        )
                    else:
                        payload = trade_message(adapter)
                        payload["data"][0]["symbol"] = symbol
                        yield payload
        if adapter.venue == "kraken":
            yield {"channel": "heartbeat"}

    delivered = {"book": [], "trades": []}
    for channel in delivered:
        item, key, reducer = setup(adapter, channel)
        requests = adapter.subscription_requests(item, key)
        controls = []
        for ordinal, payload in enumerate(exchange_messages(requests)):
            if adapter.venue == "coinbase" and payload.get("type") != "error":
                payload["sequence_num"] = ordinal
            result = apply(adapter, item, key, reducer, payload, ordinal)
            assert result.recovery is None
            for event in result.events:
                if isinstance(event.payload, Control):
                    controls.append(event.payload.kind)
                else:
                    delivered[channel].append(event.payload)
        assert "ack" in controls and "heartbeat" in controls
        if channel == "book":
            assert reducer.book.quality.integrity == "valid"
            assert reducer.book.bids[0].price == Decimal("100.0")
        else:
            assert reducer.book is None
    assert len(delivered["book"]) == len(delivered["trades"]) == 1
    assert isinstance(delivered["book"][0], BookUpdate)
    assert isinstance(delivered["trades"][0], Trade)
    assert delivered["trades"][0].trade_id == "9007199254740993"


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        ("1970-01-01T00:00:00Z", 0),
        ("1970-01-01T01:00:00.123456789+01:00", 123456789),
        ("1970-01-01T00:00:00.1Z", 100000000),
    ],
)
def test_exact_rfc3339_nanoseconds(value, expected):
    assert parse_rfc3339_ns(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "1970-01-01T00:00:00.1234567890Z",
        "2026-02-30T00:00:00Z",
        "1970-01-01T00:00:00",
        "1970-01-01T00:00:00+01:99",
        "1969-01-01T00:00:00Z",
        1,
    ],
)
def test_bad_rfc3339_rejected(value):
    with pytest.raises(ValueError):
        parse_rfc3339_ns(value)


def test_kraken_discovery_exact_native_identity_and_status(monkeypatch):
    from src.workstation.adapters import kraken

    payload = {
        "error": [],
        "result": {
            "XXBTZUSD": {
                "wsname": "XBT/USD",
                "base": "XXBT",
                "quote": "ZUSD",
                "lot_decimals": 8,
                "pair_decimals": 1,
                "tick_size": "0.1",
                "ordermin": "0.0001",
                "costmin": "0.5",
                "lot_multiplier": 1,
                "status": "online",
            },
            "XDGUSD": {
                "wsname": "XDG/USD",
                "base": "XXDG",
                "quote": "ZUSD",
                "lot_decimals": 8,
                "pair_decimals": 5,
                "status": "cancel_only",
            },
        },
    }

    async def fetch(request):
        assert (
            request.method == "GET"
            and request.endpoint_id == KrakenAdapter.metadata_url
        )
        return json.dumps(payload).encode()

    monkeypatch.setattr(kraken, "fetch_public", fetch)
    snapshot = asyncio.run(KrakenAdapter().discover())
    btc, doge = snapshot.instruments
    assert btc.native_symbol == "BTC/USD" and btc.native_asset_id == "XXBTZUSD"
    assert btc.base.asset_id == "BTC" and btc.base.native_token_id == "XXBT"
    assert btc.quote.asset_id == "USD" and btc.lot_size == Decimal("0.00000001")
    assert btc.constraints.min_notional == Decimal("0.5")
    assert doge.base.asset_id == "DOGE" and doge.status == "suspended"
    assert btc.execution_access == "unknown"
    catalog = InstrumentCatalog()
    catalog.apply_metadata(snapshot)
    catalog.apply_metadata(asyncio.run(KrakenAdapter().discover()))


def test_coinbase_discovery_native_advanced_metadata_and_pagination(monkeypatch):
    from src.workstation.adapters import coinbase

    base = {
        "product_id": "BTC-USD",
        "product_type": "SPOT",
        "base_currency_id": "BTC",
        "quote_currency_id": "USD",
        "price_increment": "0.1",
        "quote_increment": "0.00000001",
        "base_increment": "0.00000001",
        "base_min_size": "0.00001",
        "base_max_size": "1000",
        "quote_min_size": "1",
        "quote_max_size": "10000000",
        "status": "online",
    }
    requests = []

    async def fetch(request):
        requests.append(request.endpoint_id)
        if "cursor=" in request.endpoint_id:
            return json.dumps(
                {
                    "products": [
                        {
                            **base,
                            "product_id": "ETH-USD",
                            "base_currency_id": "ETH",
                            "trading_disabled": True,
                        },
                        {
                            **base,
                            "product_id": "BTC-USDC",
                            "quote_currency_id": "USDC",
                            "alias": "BTC-USD",
                        },
                    ],
                    "pagination": {"has_next": False},
                }
            ).encode()
        return json.dumps(
            {
                "products": [base],
                "pagination": {"has_next": True, "next_cursor": "next-page"},
            }
        ).encode()

    monkeypatch.setattr(coinbase, "fetch_public", fetch)
    snapshot = asyncio.run(CoinbaseAdapter().discover())
    btc, eth, alias = snapshot.instruments
    assert len(requests) == 2 and "cursor=next-page" in requests[1]
    assert all("/api/v3/brokerage/market/products?" in request for request in requests)
    assert btc.tick_size == Decimal("0.1")  # NOT quote_increment
    assert btc.base.asset_id == "BTC" and btc.quote.asset_id == "USD"
    assert eth.status == "suspended" and btc.execution_access == "unknown"
    assert alias.quote.asset_id == "USDC"
    assert alias.status == "unavailable" and alias.data_access == "unsupported"
    with pytest.raises(ValueError, match="unsupported"):
        CoinbaseAdapter().subscription_requests(
            alias, SubscriptionKey.for_instrument(alias)
        )
    catalog = InstrumentCatalog()
    catalog.apply_metadata(snapshot)
    catalog.apply_metadata(asyncio.run(CoinbaseAdapter().discover()))


@pytest.mark.parametrize("adapter", [KrakenAdapter(), CoinbaseAdapter()])
def test_subscription_rejection_is_explicit(adapter):
    item, key, reducer = setup(adapter)
    payload = (
        {"method": "subscribe", "success": False, "error": "unsupported"}
        if adapter.venue == "kraken"
        else {"type": "error", "message": "unsupported"}
    )
    result = apply(adapter, item, key, reducer, payload)
    assert result.recovery.reason == "subscription_rejected"
    assert reducer.quality.integrity == "invalid"


def test_coinbase_zero_deletion_and_repeated_updates_are_absolute():
    adapter = CoinbaseAdapter()
    item, key, reducer = setup(adapter)
    initial = coinbase_message(
        0,
        updates=[
            {"side": "bid", "price_level": "100.0", "new_quantity": "1"},
            {"side": "bid", "price_level": "99.0", "new_quantity": "1"},
            {"side": "offer", "price_level": "102.0", "new_quantity": "2"},
        ],
    )
    apply(adapter, item, key, reducer, initial)
    result = apply(
        adapter,
        item,
        key,
        reducer,
        coinbase_message(
            1,
            "update",
            [
                {"side": "bid", "price_level": "100.0", "new_quantity": "0"},
                {"side": "bid", "price_level": "99.0", "new_quantity": "4"},
                {"side": "bid", "price_level": "99.0", "new_quantity": "3"},
            ],
        ),
        1,
    )
    assert result.recovery is None
    assert len(result.book.bids) == 1
    assert result.book.bids[0] == Level(Decimal("99.0"), Decimal("3"))


@pytest.mark.parametrize("adapter", [KrakenAdapter(), CoinbaseAdapter()])
def test_unknown_trade_side_retained_and_wrong_trade_symbol_rejected(adapter):
    item, key, _ = setup(adapter, "trades")
    if adapter.venue == "kraken":
        row = {
            "symbol": "BTC/USD",
            "trade_id": 1,
            "price": "100",
            "qty": "1",
            "side": "unknown",
        }
        payload = {"channel": "trade", "type": "update", "data": [row]}
        symbol_field = "symbol"
    else:
        row = {"product_id": "BTC-USD", "trade_id": "1", "price": "100", "size": "1"}
        payload = {
            "channel": "market_trades",
            "sequence_num": 0,
            "events": [{"type": "update", "trades": [row]}],
        }
        symbol_field = "product_id"
    result = adapter.decode(frame(payload), item, key)
    assert result.error is None and result.events[0].payload.taker_side == "unknown"
    row[symbol_field] = "ETH-USD"
    result = adapter.decode(frame(payload), item, key)
    assert result.error is not None and not result.events


def test_kraken_checksum_recovery_requires_fresh_snapshot():
    adapter = KrakenAdapter()
    item, key, reducer = setup(adapter)
    snapshot = kraken_message([("100.0", "1")], [("102.0", "1")])
    apply(adapter, item, key, reducer, snapshot)
    broken = kraken_message([("100.0", "2")], [], "update", checksum=0)
    assert apply(adapter, item, key, reducer, broken, 1).recovery is not None
    assert (
        apply(
            adapter,
            item,
            key,
            reducer,
            kraken_message(
                [("100.0", "1")], [], "update", crc([("100.0", "1")], [("102.0", "1")])
            ),
            2,
        ).recovery.reason
        == "snapshot_required"
    )
    result = apply(adapter, item, key, reducer, snapshot, 3)
    assert result.recovery is None and result.book.quality.integrity == "valid"


def test_coinbase_heartbeat_gap_invalidates_and_heartbeat_cannot_repair():
    adapter = CoinbaseAdapter()
    item, key, reducer = setup(adapter)
    apply(adapter, item, key, reducer, coinbase_message(0))
    result = apply(
        adapter,
        item,
        key,
        reducer,
        {"channel": "heartbeats", "sequence_num": 2, "events": [{}]},
        1,
    )
    assert (
        result.recovery.reason == "sequence_gap"
        and reducer.quality.integrity == "invalid"
    )
    apply(
        adapter,
        item,
        key,
        reducer,
        {"channel": "heartbeats", "sequence_num": 1, "events": [{}]},
        2,
    )
    assert reducer.quality.integrity == "invalid" and not reducer.synchronized
