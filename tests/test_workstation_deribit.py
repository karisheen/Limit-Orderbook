"""Deterministic public Deribit contract quantities and consumer-visible books."""

import asyncio
from dataclasses import replace
from decimal import Decimal as D
from hashlib import sha256
import json

import pytest

from src.workstation.adapters.base import TransportRequest, wrap_decoded
from src.workstation.adapters.deribit import DeribitAdapter
from src.workstation.analytics import AnalyticsEngine
from src.workstation.costs import FeeSchedule, walk_book
from src.workstation.events import RawFrame, SegmentId, SubscriptionKey
from src.workstation.quality import ClockReading, EligibilityPolicy
from src.workstation.recording import RecordedBatch, StreamSpec
from src.workstation.replay import ReplayClock


def encoded(value):
    return json.dumps(value, separators=(",", ":")).encode()


def metadata_row(base="BTC", **overrides):
    return {
        "instrument_name": f"{base}-PERPETUAL",
        "instrument_id": 124972 if base == "BTC" else 124973,
        "kind": "future",
        "settlement_period": "perpetual",
        "instrument_type": "reversed",
        "base_currency": base,
        "quote_currency": "USD",
        "settlement_currency": base,
        "contract_size": 10 if base == "BTC" else 1,
        "min_trade_amount": 10 if base == "BTC" else 1,
        "lot_size": 1,
        "tick_size": "0.5" if base == "BTC" else "0.05",
        "tick_size_steps": [],
        "expiration_timestamp": 32503708800000,
        "is_active": True,
        "state": "open",
        **overrides,
    }


def metadata(*rows):
    return encoded({"jsonrpc": "2.0", "result": list(rows) or [metadata_row()]})


def setup(*, base="BTC", depth=20, channel="book"):
    adapter = DeribitAdapter()
    instrument = adapter.parse_metadata(metadata(metadata_row(base))).instruments[0]
    key = SubscriptionKey.for_instrument(
        instrument, channel=channel, depth=depth if channel == "book" else None
    )
    return adapter, instrument, key


def topic(instrument, key):
    if key.channel == "trades":
        return f"trades.{instrument.native_symbol}.100ms"
    if key.depth is None:
        return f"book.{instrument.native_symbol}.100ms"
    return f"book.{instrument.native_symbol}.none.{key.depth}.100ms"


def notification(instrument, key, data):
    return {
        "jsonrpc": "2.0",
        "method": "subscription",
        "params": {"channel": topic(instrument, key), "data": data},
    }


def book_data(instrument, key, *, change_id=100, bids=None, asks=None, **overrides):
    return {
        "instrument_name": instrument.native_symbol,
        "change_id": change_id,
        "timestamp": 1700000000123,
        "bids": bids
        if bids is not None
        else (
            [["50000", "100"]] if key.depth is not None else [["new", "50000", "100"]]
        ),
        "asks": asks
        if asks is not None
        else (
            [["50001", "200"]] if key.depth is not None else [["new", "50001", "200"]]
        ),
        **({"type": "snapshot"} if key.depth is None else {}),
        **overrides,
    }


def trade_data(instrument, **overrides):
    return {
        "instrument_name": instrument.native_symbol,
        "trade_id": "123456789012345678901",
        "trade_seq": 9007199254740993,
        "timestamp": 1700000000123,
        "price": "50000",
        "amount": "100",
        "direction": "buy",
        "tick_direction": 2,
        **overrides,
    }


def frame(adapter, value, ordinal=1):
    return RawFrame(
        "session",
        "stream",
        "connection",
        0,
        "clock",
        ordinal,
        ordinal * 1_000_000,
        ordinal * 1000,
        "ws",
        adapter.stream,
        value if isinstance(value, bytes) else encoded(value),
    )


def events(adapter, instrument, key, data, ordinal=1):
    raw = frame(adapter, notification(instrument, key, data), ordinal)
    result = adapter.decode(raw, instrument, key)
    assert result.error is None, result.error
    return wrap_decoded(
        raw,
        instrument,
        key,
        result,
        SegmentId("initial"),
        "ws_delta" if key.depth is None else "ws_snapshot",
    )


def test_metadata_retains_inverse_contract_economics_and_provenance():
    adapter = DeribitAdapter()
    raw = metadata(
        metadata_row("ETH"), metadata_row(), {"instrument_name": "BTC-25DEC26"}
    )
    snapshot = adapter.parse_metadata(raw, fetch_wall_ns=77)
    assert snapshot.payload_hash == sha256(raw).hexdigest()
    assert snapshot.fetch_wall_ns == 77
    assert [item.native_symbol for item in snapshot.instruments] == [
        "BTC-PERPETUAL",
        "ETH-PERPETUAL",
    ]
    for item, multiplier in zip(snapshot.instruments, (D(10), D(1))):
        assert item.market_type == "perpetual" and item.inverse
        assert item.quantity_unit == "contracts" and item.lot_size == D(1)
        assert item.contract_multiplier == multiplier
        assert item.contract_value_currency == item.quote.asset_id == "USD"
        assert item.settlement == item.base
        assert item.settlement_conversion == "none"
        assert item.expiry_wall_ns is None  # API's sentinel is not a dated future.
        assert item.contract_specification_source == adapter.specification
        assert item.metadata_source == snapshot.source == adapter.metadata_endpoint
        assert item.metadata_asof_wall_ns == 77
        assert item.metadata_version == f"{snapshot.payload_hash}:77"


def test_quantity_increment_is_not_contract_size_or_fee_lot_size():
    adapter = DeribitAdapter()
    item = adapter.parse_metadata(
        metadata(metadata_row(min_trade_amount="20", lot_size="999"))
    ).instruments[0]
    assert item.contract_multiplier == D(10)
    assert item.lot_size == item.constraints.min_base == D(2)
    assert item.constraints.min_notional == D(20)
    item.validate_quantity(D(4))
    with pytest.raises(ValueError, match="off increment"):
        item.validate_quantity(D(3))


@pytest.mark.parametrize(
    "changes",
    [
        {"contract_size": 0},
        {"min_trade_amount": -1},
        {"is_active": "true"},
        {"instrument_type": "linear"},
        {"settlement_currency": "USD"},
        {"quote_currency": "USDC"},
        {"base_currency": "ETH"},
        {"tick_size_steps": [{"above_price": 100, "tick_size": 1}]},
    ],
)
def test_malformed_reference_metadata_is_rejected(changes):
    with pytest.raises((ValueError, ArithmeticError)):
        DeribitAdapter().parse_metadata(metadata(metadata_row(**changes)))


def test_duplicate_metadata_and_inactive_subscription_are_rejected():
    adapter = DeribitAdapter()
    with pytest.raises(ValueError, match="duplicate"):
        adapter.parse_metadata(metadata(metadata_row(), metadata_row()))
    item = adapter.parse_metadata(metadata(metadata_row(is_active=False))).instruments[
        0
    ]
    assert item.status == "suspended"
    with pytest.raises(ValueError, match="product_suspended"):
        adapter.capabilities(item, SubscriptionKey.for_instrument(item, depth=20))


def test_discovery_uses_only_public_metadata_endpoint(monkeypatch):
    captured = []

    async def fetch(request):
        captured.append(request)
        return metadata(metadata_row(), metadata_row("ETH"))

    monkeypatch.setattr("src.workstation.transport.fetch_public", fetch)
    adapter = DeribitAdapter()
    result = asyncio.run(adapter.discover())
    assert len(result.instruments) == 2
    assert captured == [TransportRequest("GET", adapter.metadata_endpoint)]
    assert "currency=any&kind=future&expired=false" in captured[0].endpoint_id


@pytest.mark.parametrize("depth", [None, 1, 10, 20])
def test_public_subscription_selects_documented_book_semantics(depth):
    adapter, item, key = setup(depth=depth)
    (request,) = adapter.subscription_requests(item, key)
    assert request.method == "WS" and request.endpoint_id == adapter.stream
    assert json.loads(request.body) == {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "public/subscribe",
        "params": {"channels": [topic(item, key)]},
    }
    caps = adapter.capabilities(item, key)
    assert caps.snapshot and not caps.exact_event_flow
    assert caps.deltas == (depth is None)
    assert caps.sequence_scope == topic(item, key)
    assert adapter.snapshot_request(item, key) is None
    ack = adapter.decode(
        frame(adapter, {"jsonrpc": "2.0", "id": 1, "result": [topic(item, key)]}),
        item,
        key,
    )
    assert ack.error is None and ack.controls[0].kind == "ack"
    reject = adapter.decode(
        frame(
            adapter,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": 10000, "message": "rejected"},
            },
        ),
        item,
        key,
    )
    assert reject.error is None and reject.controls[0].kind == "rejection"


def test_private_rpc_and_unsupported_subscriptions_cannot_be_sent():
    adapter, item, key = setup()
    for invalid in (
        replace(key, depth=50),
        replace(key, aggregation=(("group", "5"),)),
    ):
        with pytest.raises(ValueError, match="unsupported_subscription"):
            adapter.subscription_requests(item, invalid)
    for method in ("private/subscribe", "private/buy", "public/auth"):
        with pytest.raises(ValueError):
            TransportRequest(
                "WS",
                adapter.stream,
                encoded(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": method,
                        "params": {"channels": [topic(item, key)]},
                    }
                ),
            )


def test_grouped_books_replace_instead_of_retaining_disappeared_levels():
    adapter, item, key = setup()
    reducer = adapter.new_reducer(item, key)
    first = events(
        adapter,
        item,
        key,
        book_data(item, key, bids=[["50000", "100"], ["49999", "20"]]),
    )
    first_book = reducer.apply(first).book
    assert [level.quantity for level in first_book.bids] == [D(10), D(2)]
    assert first[0].exchange_event_wall_ns == 1700000000123000000
    assert first[0].exchange_time_kind == "book_change"
    assert not first_book.quality.coverage.full_depth
    second = events(
        adapter,
        item,
        key,
        book_data(item, key, change_id=700, bids=[["49998", "30"]]),
        2,
    )
    result = reducer.apply(second)
    assert result.recovery is None
    assert [(level.price, level.quantity) for level in result.book.bids] == [
        (D(49998), D(3))
    ]
    assert result.book.quality.coverage.subscribed_depth == 20
    assert not result.book.quality.capabilities.deltas
    stale = reducer.apply(
        events(adapter, item, key, book_data(item, key, change_id=100), 3)
    )
    assert not stale.changed
    assert stale.book.bids == result.book.bids


def test_full_book_uses_predecessors_not_contiguous_arithmetic_and_restores_checkpoint():
    adapter, item, key = setup(depth=None)
    reducer = adapter.new_reducer(item, key)
    initial = reducer.apply(events(adapter, item, key, book_data(item, key)))
    assert initial.recovery is None and reducer.synchronized
    assert initial.book.quality.coverage.full_depth
    delta = book_data(
        item,
        key,
        change_id=900,
        type="change",
        prev_change_id=100,
        bids=[["delete", "50000", 0], ["new", "49999", "30"]],
        asks=[["change", "50001", "70"]],
    )
    accepted = reducer.apply(events(adapter, item, key, delta, 2))
    assert accepted.recovery is None
    assert accepted.book.bids[0].price == D(49999)
    assert accepted.book.bids[0].quantity == D(3)
    assert accepted.book.asks[0].quantity == D(7)
    assert accepted.book.quality.coverage.full_depth
    assert not accepted.book.quality.capabilities.exact_event_flow
    restored = adapter.new_reducer(item, key)
    restored.restore(reducer.checkpoint())
    assert restored.checkpoint().state_hash == reducer.checkpoint().state_hash
    assert restored.book.quality.coverage.full_depth
    duplicate = restored.apply(events(adapter, item, key, delta, 3))
    assert not duplicate.changed
    assert duplicate.book.bids == accepted.book.bids
    gap = book_data(
        item,
        key,
        change_id=902,
        type="change",
        prev_change_id=901,
        bids=[["change", "49999", "90"]],
        asks=[],
    )
    bad = restored.apply(events(adapter, item, key, gap, 4))
    assert bad.recovery is not None and not restored.synchronized
    assert restored.book.bids[0].quantity == D(3)  # Missing delta never commits.
    recovered = restored.apply(
        events(adapter, item, key, book_data(item, key, change_id=1000), 5)
    )
    assert recovered.recovery is None and restored.synchronized
    assert recovered.book.bids[0].quantity == D(10)
    assert recovered.book.quality.coverage.full_depth


def test_delta_before_snapshot_never_publishes_a_partial_book():
    adapter, item, key = setup(depth=None)
    data = book_data(
        item,
        key,
        change_id=200,
        type="change",
        prev_change_id=100,
        bids=[["change", "50000", "100"]],
        asks=[],
    )
    reducer = adapter.new_reducer(item, key)
    result = reducer.apply(events(adapter, item, key, data))
    assert result.book is None and result.recovery is not None


def test_inverse_book_walk_uses_price_dependent_base_not_usd_as_coin():
    adapter, item, key = setup()
    data = book_data(
        item, key, bids=[["19000", "100"]], asks=[["20000", "200"], ["40000", "800"]]
    )
    book = adapter.new_reducer(item, key).apply(events(adapter, item, key, data)).book
    result = walk_book(
        book,
        item,
        "buy",
        base_quantity=D("0.02"),
        fees=FeeSchedule(D(0), provenance="explicit fixture zero fee", asof_wall_ns=1),
        policy=EligibilityPolicy(max_age_ns=10000),
        now=ClockReading("clock", 1000),
    )
    assert result.filled_native_quantity == D(60)
    assert result.filled_base_quantity == D("0.02")
    assert result.notional_quote == D(600)
    assert result.settlement_notional == D("0.02")


@pytest.mark.parametrize(
    "base,price,amount,contracts,base_size",
    [
        ("BTC", "50000", "100", "10", "0.002"),
        ("ETH", "2500", "25", "25", "0.01"),
    ],
)
def test_trade_contracts_side_sequence_and_consumer_valuation(
    base, price, amount, contracts, base_size
):
    adapter, item, key = setup(base=base, channel="trades")
    (request,) = adapter.subscription_requests(item, key)
    assert json.loads(request.body)["params"]["channels"] == [topic(item, key)]
    rows = [trade_data(item, price=price, amount=amount, contracts=contracts)]
    envelopes = events(adapter, item, key, rows)
    (event,) = envelopes
    assert event.payload.quantity == D(contracts)
    assert event.payload.quantity_unit == "contracts"
    assert event.payload.taker_side == "buy"  # tick_direction=2 is not sell.
    assert event.payload.side_provenance == "documented_taker"
    assert not event.payload.is_subscription_history
    assert event.sequence.last == "9007199254740993"
    assert event.exchange_event_wall_ns == 1700000000123000000
    assert (
        event.raw_ref.payload_sha256
        == frame(adapter, notification(item, key, rows)).payload_sha256
    )
    engine = AnalyticsEngine((StreamSpec("stream", item, key, "deribit-v1", "test"),))
    clock = ReplayClock()
    clock.advance(event)
    engine.on_batch(RecordedBatch(1, envelopes), (), clock)
    tape = engine.snapshot(item.instrument_id, ClockReading("clock", 1000)).tape
    assert len(tape) == 1
    assert tape[0].base_quantity == D(base_size)
    assert tape[0].quote_notional == D(amount)
    assert tape[0].trade.quantity == D(contracts)


@pytest.mark.parametrize(
    "side,expected", [("sell", "sell"), (None, "unknown"), ("unrecognized", "unknown")]
)
def test_trade_direction_never_guesses_a_sell(side, expected):
    adapter, item, key = setup(channel="trades")
    (event,) = events(adapter, item, key, [trade_data(item, direction=side)])
    assert event.payload.taker_side == expected


@pytest.mark.parametrize(
    "changes",
    [
        {"amount": "15"},
        {"amount": 0},
        {"amount": -10},
        {"amount": "NaN"},
        {"contracts": "11"},
        {"instrument_name": "ETH-PERPETUAL"},
        {"price": "50000.1"},
        {"direction": True},
        {"timestamp": True},
        {"trade_seq": -1},
        {"trade_id": 123},
    ],
)
def test_malformed_trade_batch_fails_atomically_with_raw_evidence(changes):
    adapter, item, key = setup(channel="trades")
    raw = frame(
        adapter,
        notification(item, key, [trade_data(item), trade_data(item, **changes)]),
    )
    result = adapter.decode(raw, item, key)
    assert result.error is not None and not result.events
    assert result.error.raw_hash == raw.payload_sha256


def test_numeric_json_preserves_large_contract_counts_and_trade_order():
    adapter, item, key = setup(channel="trades")
    raw = frame(
        adapter,
        b'{"jsonrpc":"2.0","method":"subscription","params":'
        b'{"channel":"trades.BTC-PERPETUAL.100ms","data":['
        b'{"instrument_name":"BTC-PERPETUAL","trade_id":"exact",'
        b'"trade_seq":9007199254740993,"timestamp":1700000000123,'
        b'"direction":"buy","price":50000.5,"amount":90071992547409930}]}}',
    )
    result = adapter.decode(raw, item, key)
    assert result.error is None
    (event,) = result.events
    assert event.payload.price == D("50000.5")
    assert event.payload.quantity == D("9007199254740993")
    envelopes = wrap_decoded(raw, item, key, result, SegmentId("initial"), "ws_delta")
    reducer = adapter.new_reducer(item, key)
    admitted = reducer.apply(envelopes)
    assert admitted.events == envelopes
    duplicate = events(adapter, item, key, [trade_data(item)], 2)
    assert not reducer.apply(duplicate).events
    newer = events(
        adapter,
        item,
        key,
        [trade_data(item, trade_id="next", trade_seq=9007199254741093)],
        3,
    )
    assert reducer.apply(newer).events == newer


@pytest.mark.parametrize(
    "depth,changes",
    [
        (20, {"bids": [["new", "50000", "100"]]}),
        (20, {"bids": [["50000", "15"]]}),
        (20, {"bids": [["50000", "10"], ["50000", "20"]]}),
        (20, {"type": "change", "prev_change_id": 99}),
        (1, {"bids": [["50000", "10"], ["49999", "20"]]}),
        (None, {"type": "change"}),
        (None, {"type": "change", "prev_change_id": 100}),
        (None, {"bids": [["change", "50000", "10"]]}),
        (
            None,
            {
                "type": "change",
                "prev_change_id": 99,
                "bids": [["delete", "50000", "10"]],
            },
        ),
        (None, {"type": "change", "prev_change_id": 99, "bids": [["new", "50000", 0]]}),
        (None, {"bids": [["50000", "100"]]}),
    ],
)
def test_malformed_book_and_sequence_shapes_never_emit_updates(depth, changes):
    adapter, item, key = setup(depth=depth)
    raw = frame(adapter, notification(item, key, book_data(item, key, **changes)))
    result = adapter.decode(raw, item, key)
    assert result.error is not None and result.events == ()
    assert result.error.raw_hash == raw.payload_sha256


def test_wrong_channel_endpoint_version_and_ambiguous_json_are_rejected():
    adapter, item, key = setup()
    message = notification(item, key, book_data(item, key))
    raw = frame(adapter, message)
    wrong_channel = notification(item, key, book_data(item, key))
    wrong_channel["params"]["channel"] = "book.ETH-PERPETUAL.none.20.100ms"
    candidates = (
        replace(raw, endpoint_id="wss://test.deribit.com/ws/api/v2"),
        replace(raw, transport="rest"),
        frame(adapter, {**message, "jsonrpc": "1.0"}),
        frame(adapter, wrong_channel),
        frame(adapter, b'{"jsonrpc":"2.0","jsonrpc":"2.0"}'),
        frame(adapter, {"jsonrpc": "2.0", "id": True, "result": [topic(item, key)]}),
    )
    for candidate in candidates:
        result = adapter.decode(candidate, item, key)
        assert result.error is not None and not result.events and not result.controls
