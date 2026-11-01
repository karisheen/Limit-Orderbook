"""Synthetic protocol fixtures; no live-access or synchronization claim."""

from dataclasses import replace
from decimal import Decimal, localcontext
import json

import pytest

from src.workstation.adapters.base import wrap_decoded
from src.workstation.adapters.hyperliquid import (
    BUILDER_SPECIFICATION_SOURCE,
    CONTRACT_SPECIFICATION_SOURCE,
    FUNDING_INTERVAL_NS,
    HyperliquidAdapter,
    INFO_URL,
    MONETARY_RULE_REVISION,
    TRADE_SIDE_SOURCE,
    validate_price,
)
from src.workstation.book import BookReducer, ReducerState
from src.workstation.events import (
    AssetContext,
    BookUpdate,
    Control,
    LevelChange,
    ReferencePrice,
    SegmentId,
    SubscriptionKey,
    Trade,
    synthetic_envelope,
    synthetic_frame,
)
from src.workstation.quality import ClockReading, evaluate
from src.workstation.wire import dumps, loads

D = Decimal


def metadata():
    spot = {
        "tokens": [
            {"name": "USDC", "index": 0, "tokenId": "0xusdc", "szDecimals": 8},
            {"name": "PURR", "index": 1, "tokenId": "0xpurr", "szDecimals": 0},
            {"name": "UBTC", "index": 9, "tokenId": "0xubtc", "szDecimals": 5},
            {"name": "HYPE", "index": 150, "tokenId": "0xhype", "szDecimals": 2},
            {"name": "ALTUSD", "index": 200, "tokenId": "0xalt", "szDecimals": 6},
        ],
        "universe": [
            {"name": "PURR/USDC", "index": 0, "tokens": [1, 0]},
            {"name": "@12", "index": 12, "tokens": [9, 0]},
            {"name": "@107", "index": 107, "tokens": [150, 0]},
        ],
    }
    meta = {
        "collateralToken": 0,
        "universe": [
            {"name": name, "szDecimals": size}
            for name, size in (
                ("BTC", 5),
                ("ETH", 4),
                ("SOL", 2),
                ("HYPE", 2),
                ("PURR", 0),
            )
        ],
    }
    return meta, spot


def instruments(adapter=None, **options):
    adapter = adapter or HyperliquidAdapter()
    meta, spot = metadata()
    return adapter.parse_metadata(
        meta, spot, fetch_wall_ns=123, payload_hash="synthetic", **options
    ).instruments


def setup(symbol="BTC", channel="book", **options):
    adapter = HyperliquidAdapter()
    instrument = next(
        item for item in instruments(adapter) if item.native_symbol == symbol
    )
    instrument = replace(instrument, data_access="available")
    key = SubscriptionKey.for_instrument(instrument, channel=channel, **options)
    return adapter, instrument, key


def frame(message, **options):
    return synthetic_frame(
        json.dumps(message, separators=(",", ":")).encode(), **options
    )


def book_message(coin="BTC", bids=None, asks=None):
    return {
        "channel": "l2Book",
        "data": {
            "coin": coin,
            "time": 1700000000000,
            "levels": [
                bids
                if bids is not None
                else [{"px": "60000.0", "sz": "1.00000", "n": 1}],
                asks
                if asks is not None
                else [{"px": "60001.0", "sz": "2.00000", "n": 2}],
            ],
        },
    }


def apply_message(adapter, instrument, key, reducer, message, **options):
    raw = frame(message, **options)
    decoded = adapter.decode(raw, instrument, key)
    assert decoded.error is None
    events = wrap_decoded(
        raw, instrument, key, decoded, reducer.segment_id, "ws_snapshot"
    )
    return reducer.apply(events)


def trade_message(*, side="A", coin="BTC", time=100, tid=77, size="0.00001"):
    return {
        "channel": "trades",
        "data": [
            {
                "coin": coin,
                "side": side,
                "px": "60000.0",
                "sz": size,
                "time": time,
                "tid": tid,
                "hash": "0x0",
                "users": ["buyer", "seller"],
            }
        ],
    }


def test_metadata_preserves_native_wrapped_spot_and_perpetual_identity():
    items = {item.native_symbol: item for item in instruments()}
    assert {"BTC", "ETH", "SOL", "HYPE", "PURR"} <= items.keys()
    btc, ubtc, hype = items["BTC"], items["@12"], items["@107"]
    assert btc.base.asset_id == "BTC"
    assert btc.native_asset_id == "0" and btc.dex_namespace == "default"
    assert ubtc.base.ticker == "UBTC" and ubtc.base.asset_id != "BTC"
    assert ubtc.base.native_token_id == "9"
    assert ubtc.native_asset_id == "10012"
    assert ubtc.base.reference_asset_id is None
    assert ubtc.base.asset_id == "hyperliquid:hypercore:token:0xubtc"
    assert hype.base.native_token_id == "150" and hype.native_asset_id == "10107"
    assert items["HYPE"].instrument_id != hype.instrument_id
    assert btc.settlement == ubtc.quote
    assert btc.quote.asset_id == "USDT" and btc.quote != btc.settlement
    assert items["HYPE"].quote.asset_id == items["PURR"].quote.asset_id == "USDC"
    assert items["HYPE"].quote != hype.quote
    assert btc.tick_size is None and btc.lot_size == D("0.00001")
    assert btc.execution_access == "unknown"


@pytest.mark.parametrize(
    "symbol,currency,conversion",
    [
        ("BTC", "USDT", "quanto_no_fx"),
        ("ETH", "USDT", "quanto_no_fx"),
        ("SOL", "USDT", "quanto_no_fx"),
        ("HYPE", "USDC", "none"),
        ("PURR", "USDC", "none"),
    ],
)
def test_default_contract_denomination_provenance_and_wire_identity(
    symbol, currency, conversion
):
    adapter, instrument, key = setup(symbol, channel="reference")
    assert instrument.quote.asset_id == instrument.quote.ticker == currency
    assert instrument.quote.native_token_id is None
    assert instrument.settlement.asset_id == "hyperliquid:hypercore:token:0xusdc"
    assert instrument.settlement.native_token_id == "0"
    assert instrument.contract_multiplier == D("1")
    assert instrument.contract_value_currency == instrument.base.asset_id
    assert instrument.quantity_unit == "base" and not instrument.inverse
    assert instrument.settlement_conversion == conversion
    assert instrument.contract_specification_source == CONTRACT_SPECIFICATION_SOURCE
    assert instrument.metadata_source == INFO_URL
    assert instrument.metadata_version == f"synthetic:123:{MONETARY_RULE_REVISION}"
    assert loads(dumps(instrument), type(instrument)) == instrument

    raw = frame({"channel": "allMids", "data": {"mids": {symbol: "123.456789"}}})
    decoded = adapter.decode(raw, instrument, key)
    assert decoded.error is None
    reference = decoded.events[0].payload
    assert reference.currency == currency and reference.price == D("123.456789")
    assert loads(dumps(reference), ReferencePrice) == reference

    context_key = SubscriptionKey.for_instrument(instrument, channel="context")
    raw = frame(
        {
            "channel": "activeAssetCtx",
            "data": {
                "coin": symbol,
                "ctx": {
                    "markPx": "123.456789",
                    "oraclePx": "122.123456",
                    "funding": "-0.0000125",
                    "openInterest": "2.125",
                },
            },
        }
    )
    decoded = adapter.decode(raw, instrument, context_key)
    assert decoded.error is None
    context = decoded.events[0].payload
    assert context.mark == D("123.456789") and context.oracle == D("122.123456")
    assert context.open_interest == D("2.125") and context.open_interest_unit == "base"
    assert context.funding_rate == D("-0.0000125")
    envelope = wrap_decoded(
        raw, instrument, context_key, decoded, SegmentId("denomination"), "ws_snapshot"
    )[0]
    assert envelope.instrument_metadata_version == instrument.metadata_version
    assert loads(dumps(envelope), type(envelope)) == envelope


def test_size_and_minimum_constraints_preserve_sourced_units():
    for instrument in instruments():
        constraints = instrument.constraints
        assert constraints.min_base == instrument.lot_size
        assert constraints.size_rule == "fixed_lot"
        assert constraints.max_base is constraints.max_notional is None
        if instrument.market_type == "spot":
            assert constraints.min_notional == D("10")
            assert constraints.min_notional_currency == instrument.quote.asset_id
        else:
            # Published dollar-symbol limits do not establish a currency identity.
            assert constraints.min_notional is constraints.min_notional_currency is None


def test_spot_minimum_uses_native_quote_and_settlement_keeps_token_provenance():
    meta, spot = metadata()
    spot["tokens"][0].update(
        {
            "tokenId": "0xdiscovered",
            "evmContract": {"address": "0xsettlement"},
        }
    )
    spot["universe"].append({"name": "@200", "index": 200, "tokens": [150, 200]})
    snapshot = HyperliquidAdapter().parse_metadata(
        meta, spot, fetch_wall_ns=123, payload_hash="native-identities"
    )
    items = {item.native_symbol: item for item in snapshot.instruments}
    for symbol in ("BTC", "ETH", "SOL", "HYPE", "PURR"):
        item = items[symbol]
        assert item.instrument_id == f"hyperliquid:info:perpetual:{symbol}"
        assert item.settlement.asset_id == "hyperliquid:hypercore:token:0xdiscovered"
        assert item.settlement.native_token_id == "0"
        assert item.settlement.network == "hypercore"
        assert item.settlement.contract_address == "0xsettlement"
    native_quote = items["@200"].quote
    assert native_quote.asset_id == "hyperliquid:hypercore:token:0xalt"
    assert native_quote.ticker == "ALTUSD"
    assert items["@200"].constraints.min_notional == D("10")
    assert items["@200"].constraints.min_notional_currency == native_quote.asset_id
    assert items["@12"].quote == items["BTC"].settlement
    assert items["@12"].base.asset_id == "hyperliquid:hypercore:token:0xubtc"
    meta["collateralToken"] = 999
    with pytest.raises(KeyError):
        HyperliquidAdapter().parse_metadata(
            meta, spot, fetch_wall_ns=123, payload_hash="unknown-collateral"
        )


@pytest.mark.parametrize("symbol", ["BTC", "HYPE", "PURR"])
def test_collateral_and_dex_index_are_resolved_not_assumed_usdc(symbol):
    adapter = HyperliquidAdapter(dex="xyz")
    meta, spot = metadata()
    meta["collateralToken"] = 200
    meta["universe"] = [{"name": f"xyz:{symbol}", "szDecimals": 3}]
    result = adapter.parse_metadata(
        meta, spot, fetch_wall_ns=1, payload_hash="dex", dex_index=2
    )
    item = result.instruments[0]
    assert item.native_asset_id == "120000" and item.dex_namespace == "xyz"
    assert item.settlement.ticker == "ALTUSD"
    assert item.settlement.native_token_id == "200"
    assert item.quote.asset_id == f"hyperliquid:unknown-quote:xyz:{symbol}"
    assert item.quote.ticker == "UNKNOWN" and item.quote.native_token_id is None
    assert item.data_access == item.execution_access == "unsupported"
    assert item.settlement_conversion == "unknown"
    assert item.contract_value_currency is None
    assert item.contract_specification_source == BUILDER_SPECIFICATION_SOURCE
    assert loads(dumps(item), type(item)) == item
    for channel in ("book", "trades", "reference", "context"):
        key = SubscriptionKey.for_instrument(item, channel=channel)
        for operation in (
            adapter.capabilities,
            adapter.subscription_requests,
            adapter.snapshot_request,
            adapter.ws_endpoint,
            adapter.new_reducer,
        ):
            with pytest.raises(ValueError, match="builder contract denomination"):
                operation(item, key)
        decoded = adapter.decode(frame({}), item, key)
        assert decoded.error is not None and not decoded.events
    spot_item = next(i for i in result.instruments if i.market_type == "spot")
    assert adapter.subscription_requests(
        spot_item, SubscriptionKey.for_instrument(spot_item)
    )
    assert item.base.asset_id == f"hyperliquid:perpetual-reference:xyz:{symbol}"
    with pytest.raises(ValueError):
        adapter.parse_metadata(meta, spot, fetch_wall_ns=1, payload_hash="bad")
    meta["universe"][0]["name"] = "BTC"
    with pytest.raises(ValueError):
        adapter.parse_metadata(
            meta, spot, fetch_wall_ns=1, payload_hash="bad", dex_index=2
        )


@pytest.mark.asyncio
async def test_discovery_uses_shared_public_fetch_and_exact_metadata(monkeypatch):
    meta, spot = metadata()
    requests = []

    async def fetch(request):
        assert request.method == "POST" and request.endpoint_id == INFO_URL
        kind = json.loads(request.body)["type"]
        requests.append(kind)
        return json.dumps({"meta": meta, "spotMeta": spot}[kind]).encode()

    monkeypatch.setattr("src.workstation.adapters.hyperliquid.fetch_public", fetch)
    snapshot = await HyperliquidAdapter().discover()
    assert requests == ["spotMeta", "meta"]
    assert len(snapshot.instruments) == 8
    assert len(snapshot.payload_hash) == 64
    assert snapshot.fetch_wall_ns > 0


@pytest.mark.asyncio
async def test_discovery_fetches_missing_collateral_metadata(monkeypatch):
    meta, spot = metadata()
    old_meta = {"universe": meta["universe"]}
    calls = []

    async def fetch(request):
        kind = json.loads(request.body)["type"]
        calls.append(kind)
        return json.dumps(
            {"meta": old_meta, "spotMeta": spot, "metaAndAssetCtxs": [meta, []]}[kind]
        ).encode()

    monkeypatch.setattr("src.workstation.adapters.hyperliquid.fetch_public", fetch)
    result = await HyperliquidAdapter().discover()
    assert "metaAndAssetCtxs" in calls
    assert result.instruments[0].settlement.native_token_id == "0"


@pytest.mark.parametrize(
    "symbol,valid,invalid",
    [
        ("BTC", "123456", "12345.6"),
        ("BTC", "1234.5", "1234.56"),
        ("BTC", "0.1", "0.01"),
        ("SOL", "0.1234", "0.12345"),
        ("@107", "0.001234", "0.0012345"),
        ("PURR/USDC", "0.0001234", "0.000000001"),
    ],
)
def test_variable_tick_and_integer_exception(symbol, valid, invalid):
    _, instrument, _ = setup(symbol)
    with localcontext() as context:
        context.prec = 2
        validate_price(instrument, D(valid))
        validate_price(
            instrument, D(valid + "0") if "." in valid else D(valid + ".000")
        )
        with pytest.raises(ValueError):
            validate_price(instrument, D(invalid))


def test_default_depth_cadence_exact_strings_and_replace_not_merge():
    adapter, instrument, key = setup()
    reducer = adapter.new_reducer(instrument, key)
    raw = frame(book_message())
    decoded = adapter.decode(raw, instrument, key)
    event = decoded.events[0]
    assert event.sequence is event.checksum is None
    assert event.exchange_event_wall_ns == 1700000000000000000
    assert str(event.payload.changes[0].price) == "60000.0"
    assert str(event.payload.changes[0].quantity) == "1.00000"
    first = apply_message(adapter, instrument, key, reducer, book_message())
    assert first.book.quality.coverage.subscribed_depth == 20
    assert first.book.quality.coverage.expected_cadence_ns == 500_000_000
    assert not first.book.quality.coverage.full_depth
    second = apply_message(
        adapter,
        instrument,
        key,
        reducer,
        book_message(
            bids=[{"px": "59999", "sz": "3", "n": 1}],
        ),
        ordinal=2,
    )
    assert [level.price for level in second.book.bids] == [D("59999")]
    assert second.book.quality.coverage.source_aggregation == (("fast", "false"),)
    capabilities = second.book.quality.capabilities
    assert not capabilities.deltas and not capabilities.exact_event_flow
    assert capabilities.sequence_scope is capabilities.checksum_scope is None


@pytest.mark.parametrize(
    "depth,aggregation,effective_depth",
    [
        (None, (), 20),
        (20, (), 20),
        (None, (("fast", "true"),), 5),
        (5, (("fast", "true"),), 5),
    ],
)
def test_effective_book_depth_preserves_physical_identity_and_checkpoint(
    depth, aggregation, effective_depth
):
    adapter, instrument, key = setup(depth=depth, aggregation=aggregation)
    reducer = adapter.new_reducer(instrument, key)
    raw = frame(book_message())
    decoded = adapter.decode(raw, instrument, key)
    assert decoded.error is None
    assert decoded.events[0].payload.subscribed_depth == effective_depth
    events = wrap_decoded(
        raw, instrument, key, decoded, reducer.segment_id, "ws_snapshot"
    )
    result = reducer.apply(events)
    assert result.recovery is None
    assert result.book is not None and reducer.synchronized
    assert reducer.key == result.book.key == key
    assert all(event.key == key for event in result.events)
    assert result.book.quality.coverage.subscribed_depth == effective_depth
    assert reducer.effective_depth == effective_depth
    checkpoint = reducer.checkpoint()
    state = loads(checkpoint.reducer_state, ReducerState)
    assert state.key == state.last_event.key == checkpoint.book.key == key
    assert state.effective_depth == effective_depth
    restored = adapter.new_reducer(instrument, key)
    restored.restore(checkpoint)
    assert restored.book == result.book and restored.synchronized
    assert restored.key == key
    other_key = replace(key, depth=effective_depth if depth is None else None)
    with pytest.raises(ValueError, match="incompatible reducer state"):
        adapter.new_reducer(instrument, other_key).restore(checkpoint)
    wrong_identity = adapter.new_reducer(instrument, other_key).apply(events)
    assert wrong_identity.recovery.reason == "wrong_subscription"
    assert wrong_identity.book is None
    restored.reset(1, SegmentId("reconnect"))
    next_result = apply_message(
        adapter, instrument, key, restored, book_message(), epoch=1, ordinal=2
    )
    assert next_result.recovery is None
    assert next_result.book.key == key
    assert next_result.book.quality.coverage.subscribed_depth == effective_depth


@pytest.mark.parametrize(
    "aggregation,wrong_depth", [((), 5), ((("fast", "true"),), 20)]
)
def test_implicit_depth_rejects_mismatched_normalized_book_depth(
    aggregation, wrong_depth
):
    adapter, instrument, key = setup(aggregation=aggregation)
    reducer = adapter.new_reducer(instrument, key)
    raw = frame(book_message())
    events = wrap_decoded(
        raw,
        instrument,
        key,
        adapter.decode(raw, instrument, key),
        reducer.segment_id,
        "ws_snapshot",
    )
    event = events[0]
    wrong = replace(event, payload=replace(event.payload, subscribed_depth=wrong_depth))
    result = reducer.apply((wrong,))
    assert result.recovery.reason == "subscription_depth_mismatch"
    assert result.book is None and not reducer.synchronized


def test_default_effective_depth_does_not_weaken_generic_reducer_validation():
    adapter, instrument, key = setup()
    raw = frame(book_message())
    generic = BookReducer(instrument, key, price_validator=validate_price)
    events = wrap_decoded(
        raw,
        instrument,
        key,
        adapter.decode(raw, instrument, key),
        generic.segment_id,
        "ws_snapshot",
    )
    result = generic.apply(events)
    assert generic.effective_depth is None
    assert result.recovery.reason == "subscription_depth_mismatch"
    assert result.book is None
    with pytest.raises(ValueError, match="conflicts with subscription depth"):
        BookReducer(instrument, replace(key, depth=20), effective_depth=5)


def test_checkpoint_rejects_different_effective_depth_for_same_physical_key():
    adapter, instrument, key = setup()
    reducer = adapter.new_reducer(instrument, key)
    apply_message(adapter, instrument, key, reducer, book_message())
    wrong = BookReducer(
        instrument,
        key,
        capabilities=reducer.capabilities,
        max_levels=reducer.max_levels,
        effective_depth=5,
        price_validator=validate_price,
        strategy_id=reducer.strategy_id,
        adapter_revision=reducer.adapter_revision,
    )
    with pytest.raises(ValueError, match="incompatible reducer state"):
        wrong.restore(reducer.checkpoint())
    assert wrong.book is None and not wrong.synchronized
    fast_key = replace(key, aggregation=(("fast", "true"),))
    with pytest.raises(ValueError, match="incompatible reducer state"):
        adapter.new_reducer(instrument, fast_key).restore(reducer.checkpoint())


@pytest.mark.parametrize("aggregation,depth", [((), 20), ((("fast", "true"),), 5)])
def test_effective_depth_boundary_rejects_oversize_without_truncation(
    aggregation, depth
):
    adapter, instrument, key = setup(aggregation=aggregation)
    reducer = adapter.new_reducer(instrument, key)
    bids = [{"px": str(60000 - i), "sz": "1", "n": 1} for i in range(depth)]
    result = apply_message(adapter, instrument, key, reducer, book_message(bids=bids))
    assert result.recovery is None
    assert len(result.book.bids) == reducer.max_levels == depth
    assert not reducer.truncate_depth
    bids.append({"px": str(60000 - depth), "sz": "1", "n": 1})
    decoded = adapter.decode(frame(book_message(bids=bids)), instrument, key)
    assert decoded.error is not None and not decoded.events
    # Normalized replay also enforces capacity; it cannot silently trim a snapshot.
    changes = tuple(
        LevelChange("bid", D(level["px"]), D(level["sz"])) for level in bids
    ) + (LevelChange("ask", D("60001"), D("1")),)
    fresh = adapter.new_reducer(instrument, key)
    event = synthetic_envelope(
        instrument,
        BookUpdate("snapshot", changes, depth),
        key=key,
        segment_id=fresh.segment_id,
    )
    rejected = fresh.apply((event,))
    assert rejected.recovery.reason == "book_capacity_exceeded"
    assert rejected.book is None and not fresh.synchronized


def test_fast_five_requires_explicit_source_selection_and_no_fake_rest_fast():
    adapter, instrument, key = setup(depth=5, aggregation=(("fast", "true"),))
    subscription = json.loads(adapter.subscription_requests(instrument, key)[0].body)
    assert subscription["subscription"]["fast"] is True
    assert adapter.snapshot_request(instrument, key) is None
    assert adapter.new_reducer(instrument, key).quality.coverage.subscribed_depth == 5
    assert (
        adapter.new_reducer(instrument, key).quality.coverage.expected_cadence_ns
        is None
    )
    levels = [{"px": str(60000 - i), "sz": "1", "n": 1} for i in range(6)]
    assert adapter.decode(frame(book_message(bids=levels)), instrument, key).error
    with pytest.raises(ValueError):
        adapter.new_reducer(instrument, replace(key, aggregation=()))


def test_selected_aggregation_is_in_subscription_and_coverage():
    adapter, instrument, key = setup(aggregation=(("nSigFigs", "5"), ("mantissa", "2")))
    sub = json.loads(adapter.subscription_requests(instrument, key)[0].body)[
        "subscription"
    ]
    assert sub["nSigFigs"] == 5 and sub["mantissa"] == 2
    assert ("mantissa", "2") in adapter.new_reducer(
        instrument, key
    ).quality.coverage.source_aggregation
    assert "fast" not in json.loads(adapter.snapshot_request(instrument, key).body)


@pytest.mark.parametrize(
    "message",
    [
        book_message(coin="ETH"),
        book_message(bids=[{"px": "60000", "sz": "0.000001", "n": 1}]),
        book_message(bids=[{"px": "60000.01", "sz": "1", "n": 1}]),
        book_message(bids=[{"px": "NaN", "sz": "1", "n": 1}]),
        book_message(bids=[{"px": "60000", "sz": "0", "n": 1}]),
        book_message(bids=[{"px": "60000", "sz": "1", "n": -1}]),
    ],
)
def test_invalid_book_schema_precision_and_wrong_coin_fail_atomically(message):
    adapter, instrument, key = setup()
    raw = frame(message)
    decoded = adapter.decode(raw, instrument, key)
    assert decoded.error.raw_hash == raw.payload_sha256
    assert not decoded.events and not decoded.controls


@pytest.mark.parametrize(
    "bids",
    [
        [],
        [{"px": "60001", "sz": "1", "n": 1}],
        [{"px": "60000", "sz": "1", "n": 1}, {"px": "60000.0", "sz": "2", "n": 1}],
    ],
)
def test_empty_locked_duplicate_snapshot_rejected_by_reducer(bids):
    adapter, instrument, key = setup()
    reducer = adapter.new_reducer(instrument, key)
    result = apply_message(adapter, instrument, key, reducer, book_message(bids=bids))
    assert result.recovery is not None
    assert not reducer.synchronized


def test_trade_side_unique_tuple_and_reconnect_duplicates_preserved():
    adapter, instrument, key = setup(channel="trades")
    keys = []
    for side, expected in (("A", "sell"), ("B", "buy"), ("unexpected", "unknown")):
        decoded = adapter.decode(frame(trade_message(side=side)), instrument, key)
        trade = decoded.events[0].payload
        assert isinstance(trade, Trade) and trade.taker_side == expected
        assert TRADE_SIDE_SOURCE in trade.side_provenance
        assert not trade.is_subscription_history
        keys.append(trade.dedup_key)
    assert keys == ['[100,"BTC",77]'] * 3
    # Decoder is deliberately stateless; one consumer scope spans connection epochs.
    first = adapter.decode(frame(trade_message()), instrument, key).events[0].payload
    replayed = (
        adapter.decode(frame(trade_message(), epoch=1), instrument, key)
        .events[0]
        .payload
    )
    assert first == replayed
    seen = {first.dedup_key}
    assert replayed.dedup_key in seen
    later = (
        adapter.decode(frame(trade_message(time=101)), instrument, key)
        .events[0]
        .payload
    )
    assert later.dedup_key not in seen
    _, eth, eth_key = setup("ETH", channel="trades")
    decoded = adapter.decode(
        frame(trade_message(coin="ETH", size=str(eth.lot_size))), eth, eth_key
    )
    assert decoded.error is None
    other = decoded.events[0].payload
    assert other.quantity == eth.lot_size == D("0.0001")
    assert other.dedup_key != first.dedup_key
    assert adapter.decode(frame(trade_message(coin="ETH")), instrument, key).error


def test_trade_batch_failure_does_not_return_partial_events():
    adapter, instrument, key = setup(channel="trades")
    message = trade_message()
    message["data"].append(trade_message(coin="ETH")["data"][0])
    result = adapter.decode(frame(message), instrument, key)
    assert result.error and not result.events


@pytest.mark.parametrize(
    "symbol,size",
    [("BTC", "0.000001"), ("ETH", "0.00001"), ("ETH", "1.00001")],
)
def test_trade_off_lot_precision_is_rejected_without_rounding(symbol, size):
    adapter, instrument, key = setup(symbol, channel="trades")
    raw = frame(trade_message(coin=symbol, size=size))
    decoded = adapter.decode(raw, instrument, key)
    assert decoded.error is not None
    assert decoded.error.raw_hash == raw.payload_sha256
    assert not decoded.events and not decoded.controls


def test_reference_is_not_an_executable_book_and_has_no_invented_time():
    adapter, instrument, key = setup(channel="reference")
    message = {
        "channel": "allMids",
        "data": {"mids": {"BTC": "60000.123456789", "ETH": "2"}},
    }
    result = adapter.decode(frame(message), instrument, key)
    event = result.events[0]
    assert isinstance(event.payload, ReferencePrice)
    assert event.payload.kind == "mid" and event.payload.price == D("60000.123456789")
    assert event.exchange_event_wall_ns is None
    assert event.payload.currency == "USDT"
    reducer = adapter.new_reducer(instrument, key)
    reduced = apply_message(adapter, instrument, key, reducer, message)
    assert reduced.book is None and not reducer.synchronized
    _, _, book_key = setup()
    assert adapter.decode(frame(message), instrument, book_key).error


def test_perpetual_context_units_funding_interval_and_spot_separation():
    adapter, instrument, key = setup(channel="context")
    ctx = {
        "markPx": "60000.1234567",
        "oraclePx": "60002",
        "funding": "-0.000001",
        "openInterest": "123.12345",
    }
    result = adapter.decode(
        frame({"channel": "activeAssetCtx", "data": {"coin": "BTC", "ctx": ctx}}),
        instrument,
        key,
    )
    payload = result.events[0].payload
    assert isinstance(payload, AssetContext)
    assert instrument.quote.asset_id == "USDT"
    assert payload.funding_interval_ns == FUNDING_INTERVAL_NS
    assert payload.open_interest_unit == "base" and payload.open_interest == D(
        "123.12345"
    )
    assert payload.funding_rate == D("-0.000001")
    assert payload.mark == D(
        "60000.1234567"
    )  # Context is not constrained to book ticks.
    _, spot, spot_key = setup("@12", channel="context")
    message = {
        "channel": "activeSpotAssetCtx",
        "data": {"coin": "@12", "ctx": {"markPx": "60001"}},
    }
    spot_payload = adapter.decode(frame(message), spot, spot_key).events[0].payload
    assert (
        spot_payload.funding_rate
        is spot_payload.oracle
        is spot_payload.open_interest
        is None
    )
    message["data"]["ctx"] = ctx
    assert adapter.decode(frame(message), spot, spot_key).error
    assert adapter.capabilities(spot, spot_key).asset_context == frozenset({"mark"})


def test_spot_context_ack_and_data_use_distinct_native_channel_names():
    adapter, spot, key = setup("@107", channel="context")
    subscription = json.loads(adapter.subscription_requests(spot, key)[0].body)
    assert subscription["subscription"] == {"type": "activeAssetCtx", "coin": "@107"}
    ack = adapter.decode(
        frame({"channel": "subscriptionResponse", "data": subscription}), spot, key
    )
    assert ack.error is None and ack.controls[0].kind == "ack"
    message = {
        "channel": "activeSpotAssetCtx",
        "data": {
            "coin": "@107",
            "ctx": {
                "prevDayPx": "86.766",
                "dayNtlVlm": "25303124.0211699829",
                "markPx": "88.006",
                "midPx": "88.0125",
                "circulatingSupply": "301920297.2344350219",
                "coin": "@107",
                "totalSupply": "998806575.6361390352",
                "dayBaseVlm": "288202.01",
            },
        },
    }
    decoded = adapter.decode(frame(message), spot, key)
    assert decoded.error is None
    assert decoded.events[0].payload == AssetContext(mark=D("88.006"))
    message["channel"] = "activeAssetCtx"
    assert adapter.decode(frame(message), spot, key).error
    _, perp, perp_key = setup("HYPE", channel="context")
    message["channel"] = "activeSpotAssetCtx"
    message["data"]["coin"] = "HYPE"
    assert adapter.decode(frame(message), perp, perp_key).error


@pytest.mark.parametrize("symbol", ["BTC", "@12"])
def test_info_contexts_use_metadata_mapping_not_positional_guess(symbol):
    adapter, instrument, key = setup(symbol, channel="context")
    meta, spot = metadata()
    chosen = meta if symbol == "BTC" else spot
    ctx = {"markPx": "60000"}
    if symbol == "BTC":
        ctx.update({"oraclePx": "60000", "funding": "0", "openInterest": "2"})
    message = [chosen, [ctx] * len(chosen["universe"])]
    result = adapter.decode(frame(message, transport="rest"), instrument, key)
    assert result.error is None and isinstance(result.events[0].payload, AssetContext)
    message[1].pop()
    assert adapter.decode(frame(message), instrument, key).error


def test_checkpoint_restores_variable_tick_validator_and_source_reset_is_boundary():
    adapter, instrument, key = setup()
    reducer = adapter.new_reducer(instrument, key)
    first = apply_message(adapter, instrument, key, reducer, book_message())
    checkpoint = reducer.checkpoint()
    restored = adapter.new_reducer(instrument, key)
    restored.restore(checkpoint)
    assert restored.book == first.book and restored.synchronized
    assert restored.quality.coverage == reducer.quality.coverage
    event = synthetic_envelope(
        instrument,
        Control("source_change", "rest_fallback"),
        key=key,
        segment_id=reducer.segment_id,
        ordinal=2,
        source_mode="rest_snapshot",
    )
    reducer.apply((event,))
    assert not reducer.synchronized
    reducer.reset(1, SegmentId("reconnect"))
    assert reducer.book is None and not reducer.synchronized
    assert reducer.quality.coverage.subscribed_depth == 20
    stale = wrap_decoded(
        frame(book_message()),
        instrument,
        key,
        adapter.decode(frame(book_message()), instrument, key),
        reducer.segment_id,
        "ws_snapshot",
    )
    assert reducer.apply(stale).recovery is not None
    current = apply_message(
        adapter, instrument, key, reducer, book_message(), epoch=1, ordinal=3
    )
    assert current.recovery is None and reducer.synchronized


def test_snapshot_book_can_quote_but_exact_event_flow_gate_rejects():
    adapter, instrument, key = setup()
    reducer = adapter.new_reducer(instrument, key)
    book = apply_message(adapter, instrument, key, reducer, book_message()).book
    now = ClockReading("synthetic-clock", 1)
    assert evaluate(book, instrument, "comparison", now).eligible
    eligibility = evaluate(book, instrument, "event_ofi", now)
    assert not eligibility.eligible
    assert {"missing_exact_event_flow", "no_event_continuity"} <= set(
        eligibility.reasons
    )


def test_subscription_ack_pong_and_rejection_are_not_book_health():
    adapter, instrument, key = setup()
    sub = json.loads(adapter.subscription_requests(instrument, key)[0].body)
    for message, kind in [
        ({"channel": "subscriptionResponse", "data": sub}, "ack"),
        ({"channel": "pong"}, "heartbeat"),
        ({"channel": "error", "data": "not supported"}, "rejection"),
    ]:
        decoded = adapter.decode(frame(message), instrument, key)
        assert decoded.controls[0].kind == kind
        assert not decoded.events
    sub["subscription"]["coin"] = "ETH"
    assert adapter.decode(
        frame({"channel": "subscriptionResponse", "data": sub}), instrument, key
    ).error


@pytest.mark.parametrize(
    "aggregation,ack_options",
    [
        ((), {"nSigFigs": None, "mantissa": None, "fast": False}),
        ((("fast", "false"),), {}),
        ((("fast", "true"),), {"nSigFigs": None, "mantissa": None, "fast": True}),
        (
            (("nSigFigs", "5"), ("mantissa", "2")),
            {"nSigFigs": 5, "mantissa": 2, "fast": False},
        ),
    ],
)
def test_book_ack_accepts_semantically_absent_null_and_default_options(
    aggregation, ack_options
):
    adapter, instrument, key = setup(aggregation=aggregation)
    message = {
        "channel": "subscriptionResponse",
        "data": {
            "method": "subscribe",
            "subscription": {"type": "l2Book", "coin": "BTC", **ack_options},
        },
    }
    decoded = adapter.decode(frame(message), instrument, key)
    assert decoded.error is None
    assert decoded.controls == (Control("ack", "hyperliquid_subscribed"),)
    assert not decoded.events
    reducer = adapter.new_reducer(instrument, key)
    result = apply_message(adapter, instrument, key, reducer, message)
    assert result.book is None and not reducer.synchronized
    assert reducer.quality.last_healthy_observation_mono_ns is None


@pytest.mark.parametrize(
    "aggregation,ack_options",
    [
        ((), {"coin": "ETH"}),
        ((), {"type": "trades"}),
        ((), {"fast": True}),
        ((), {"fast": None}),
        ((), {"fast": 0}),
        ((), {"nSigFigs": 5}),
        ((), {"unexpected": None}),
        ((("fast", "true"),), {}),
        ((("fast", "true"),), {"fast": 1}),
        ((("nSigFigs", "5"),), {"nSigFigs": None}),
        ((("nSigFigs", "5"),), {"nSigFigs": 4}),
        ((("nSigFigs", "5"),), {"nSigFigs": "5"}),
        ((("nSigFigs", "5"), ("mantissa", "2")), {"nSigFigs": 5, "mantissa": None}),
        ((("nSigFigs", "5"), ("mantissa", "2")), {"nSigFigs": 5, "mantissa": 1}),
        ((("nSigFigs", "5"), ("mantissa", "1")), {"nSigFigs": 5, "mantissa": True}),
    ],
)
def test_book_ack_rejects_wrong_identity_or_nondefault_aggregation(
    aggregation, ack_options
):
    adapter, instrument, key = setup(aggregation=aggregation)
    message = {
        "channel": "subscriptionResponse",
        "data": {
            "method": "subscribe",
            "subscription": {"type": "l2Book", "coin": "BTC", **ack_options},
        },
    }
    decoded = adapter.decode(frame(message), instrument, key)
    assert decoded.error is not None
    assert not decoded.events and not decoded.controls
