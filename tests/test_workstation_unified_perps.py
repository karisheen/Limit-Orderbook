"""Deterministic public-protocol fixtures; no captured-live validation claims."""

import asyncio
from dataclasses import replace
from decimal import Decimal
import json

import pytest

from src.workstation.adapters.base import wrap_decoded
from src.workstation.adapters.binance import BinanceGlobalAdapter, BinanceUSAdapter
from src.workstation.adapters.bybit import BybitAdapter
from src.workstation.events import RawFrame, SegmentId, SubscriptionKey
from src.workstation.transport import PublicTransportError


def encoded(value):
    return json.dumps(value, separators=(",", ":")).encode()


def binance_metadata(*, perpetual=True, base="BTC"):
    row = {
        "symbol": base + "USDT",
        "baseAsset": base,
        "quoteAsset": "USDT",
        "status": "TRADING",
        # Precision is deliberately not the tick/step specification.
        "pricePrecision": 8,
        "quantityPrecision": 8,
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.25"},
            {
                "filterType": "LOT_SIZE",
                "stepSize": "0.005",
                "minQty": "0.010",
                "maxQty": "100",
            },
            {
                "filterType": "MARKET_LOT_SIZE",
                "stepSize": "0.005",
                "minQty": "0.010",
                "maxQty": "50",
            },
            {
                "filterType": "MIN_NOTIONAL",
                "notional" if perpetual else "minNotional": "5",
            },
        ],
    }
    if perpetual:
        row.update(contractType="PERPETUAL", marginAsset="USDT")
    else:
        row["isSpotTradingAllowed"] = True
    return {"symbols": [row]}


def bybit_metadata(*, perpetual=True, base="BTC", cursor=""):
    row = {
        "symbol": base + "USDT",
        "baseCoin": base,
        "quoteCoin": "USDT",
        "status": "Trading",
        "priceScale": "8",
        "priceFilter": {"tickSize": "0.25"},
        "lotSizeFilter": {
            "qtyStep": "0.005",
            "minOrderQty": "0.010",
            "maxOrderQty": "100",
            "maxMktOrderQty": "50",
            "minNotionalValue": "5",
        }
        if perpetual
        else {
            "basePrecision": "0.005",
            "minOrderAmt": "5",
            "maxLimitOrderQty": "100",
            "maxMarketOrderQty": "50",
        },
    }
    if perpetual:
        row.update(contractType="LinearPerpetual", settleCoin="USDT")
    return {
        "retCode": 0,
        "result": {
            "category": "linear" if perpetual else "spot",
            "list": [row],
            "nextPageCursor": cursor,
        },
    }


def setup(adapter, *, channel="book", base="BTC"):
    if isinstance(adapter, BinanceGlobalAdapter):
        instrument = adapter.parse_metadata(
            encoded(binance_metadata(base=base)), api_family="usdm-v1"
        ).instruments[0]
        depth = 20
    else:
        instrument = adapter.parse_metadata(
            encoded(bybit_metadata(base=base))
        ).instruments[0]
        depth = 50
    return instrument, SubscriptionKey.for_instrument(
        instrument, channel=channel, depth=depth if channel == "book" else None
    )


def frame(adapter, instrument, key, payload, ordinal=1, *, rest=False):
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
        adapter.snapshot_request(instrument, key).endpoint_id
        if rest
        else adapter.ws_endpoint(instrument, key),
        encoded(payload),
    )


def events(adapter, instrument, key, payload, ordinal=1):
    raw = frame(adapter, instrument, key, payload, ordinal)
    decoded = adapter.decode(raw, instrument, key)
    assert decoded.error is None, decoded.error
    mode = "ws_delta" if adapter.capabilities(instrument, key).deltas else "ws_snapshot"
    return wrap_decoded(raw, instrument, key, decoded, SegmentId("initial"), mode)


def binance_book(*, update=100, price="99.00", quantity="1.005"):
    return {
        "stream": "btcusdt@depth20@100ms",
        "data": {
            "e": "depthUpdate",
            "s": "BTCUSDT",
            "E": 1700000000002,
            "T": 1700000000001,
            "U": update - 10,
            "u": update,
            "pu": update - 20,
            "b": [[price, quantity]],
            "a": [["101.00", "2.000"]],
            "st": 1,
        },
    }


def bybit_book(
    *, update=100, seq=500, kind="snapshot", price="99.00", quantity="1.005"
):
    return {
        "topic": "orderbook.50.BTCUSDT",
        "type": kind,
        "cts": 1700000000001,
        "data": {
            "s": "BTCUSDT",
            "u": update,
            "seq": seq,
            "b": [[price, quantity]],
            "a": [["101.00", "2.000"]] if kind == "snapshot" else [],
        },
    }


@pytest.mark.parametrize("factory", [BinanceGlobalAdapter, BybitAdapter])
def test_linear_units_settlement_and_actual_filters(factory):
    adapter = factory()
    instrument, _ = setup(adapter)
    assert instrument.market_type == "perpetual"
    assert instrument.settlement.asset_id == "USDT"
    assert instrument.quote.asset_id == "USDT"
    assert instrument.quantity_unit == "base"
    assert instrument.contract_multiplier == Decimal("1")
    assert instrument.contract_value_currency == instrument.base.asset_id
    assert instrument.settlement_conversion == "none"
    assert not instrument.inverse
    assert instrument.expiry_wall_ns is None
    assert instrument.tick_size == Decimal("0.25")
    assert instrument.lot_size == Decimal("0.005")
    assert instrument.constraints.min_base == Decimal("0.010")
    assert instrument.constraints.max_limit_base == Decimal("100")
    assert instrument.constraints.max_market_base == Decimal("50")
    instrument.validate_order(Decimal("100"), Decimal("0.050"), order_type="limit")
    with pytest.raises(ValueError):
        instrument.validate_price(Decimal("100.01"))
    with pytest.raises(ValueError):
        instrument.validate_quantity(Decimal("0.001"))
    with pytest.raises(ValueError):
        instrument.validate_order(
            Decimal("100"), Decimal("50.005"), order_type="market"
        )
    # A prefixed underlying is a native asset, not an invented 1000x BTC contract.
    bundled, _ = setup(adapter, base="1000BTC")
    assert bundled.base.ticker == "1000BTC"
    assert bundled.base.asset_id != "BTC"
    assert bundled.contract_multiplier == Decimal("1")


def test_binance_current_routes_and_spot_us_separation():
    adapter = BinanceGlobalAdapter()
    instrument, key = setup(adapter)
    assert (
        adapter.ws_endpoint(instrument, key)
        == "wss://fstream.binance.com/public/stream"
    )
    assert json.loads(adapter.subscription_requests(instrument, key)[0].body)[
        "params"
    ] == ["btcusdt@depth20@100ms"]
    assert (
        adapter.snapshot_request(instrument, key).endpoint_id
        == "https://fapi.binance.com/fapi/v1/depth?symbol=BTCUSDT&limit=20"
    )
    trades = replace(key, channel="trades", depth=None)
    assert (
        adapter.ws_endpoint(instrument, trades)
        == "wss://fstream.binance.com/market/stream"
    )
    assert json.loads(adapter.subscription_requests(instrument, trades)[0].body)[
        "params"
    ] == ["btcusdt@aggTrade"]
    assert (
        "/fapi/v1/aggTrades?"
        in adapter.snapshot_request(instrument, trades).endpoint_id
    )
    caps = adapter.capabilities(instrument, key)
    assert caps.snapshot and not caps.deltas and not caps.exact_event_flow
    spot = adapter.parse_metadata(
        encoded(binance_metadata(perpetual=False))
    ).instruments[0]
    spot_key = SubscriptionKey.for_instrument(spot, depth=5000)
    assert adapter.capabilities(spot, spot_key).deltas
    assert "data-stream.binance.vision" in adapter.ws_endpoint(spot, spot_key)
    assert spot.instrument_id != instrument.instrument_id
    with pytest.raises(ValueError):
        BinanceUSAdapter().ws_endpoint(instrument, key)
    with pytest.raises(ValueError):
        adapter.ws_endpoint(instrument, replace(key, api_family="spot-v3"))


def test_binance_partial_books_replace_without_contiguous_id_claim():
    adapter = BinanceGlobalAdapter()
    instrument, key = setup(adapter)
    reducer = adapter.new_reducer(instrument, key)
    reducer.apply(events(adapter, instrument, key, binance_book()))
    result = reducer.apply(
        events(
            adapter,
            instrument,
            key,
            binance_book(update=900, price="98.00", quantity="0.015"),
            2,
        )
    )
    assert result.recovery is None
    assert [(level.price, level.quantity) for level in result.book.bids] == [
        (Decimal("98.00"), Decimal("0.015"))
    ]
    assert not result.book.quality.coverage.full_depth
    assert not result.book.quality.capabilities.exact_event_flow
    assert result.book.quality.source_mode == "ws_snapshot"
    restored = adapter.new_reducer(instrument, key)
    restored.restore(reducer.checkpoint())
    assert restored.book == reducer.book
    raw = frame(
        adapter,
        instrument,
        key,
        {"lastUpdateId": 901, "bids": [["98", "1.005"]], "asks": [["101", "2"]]},
        rest=True,
    )
    decoded = adapter.decode(raw, instrument, key)
    assert decoded.error is None
    assert decoded.events[0].payload.kind == "snapshot"


@pytest.mark.parametrize(
    "maker,side", [(True, "sell"), (False, "buy"), (None, "unknown")]
)
def test_binance_aggregate_trades_preserve_side_id_quantity_and_history(maker, side):
    adapter = BinanceGlobalAdapter()
    instrument, key = setup(adapter, channel="trades")
    row = {
        "e": "aggTrade",
        "s": "BTCUSDT",
        "a": 9007199254740993,
        "p": "99.25",
        "q": "0.015",
        "f": 10,
        "l": 15,
        "T": 1700000000001,
        "m": maker,
        "st": 1,
    }
    payload = {"stream": "btcusdt@aggTrade", "data": row}
    event = events(adapter, instrument, key, payload)[0]
    assert event.payload.trade_id == "9007199254740993"
    assert event.payload.quantity == Decimal("0.015")
    assert event.payload.taker_side == side
    assert event.exchange_event_wall_ns == 1700000000001000000
    history = adapter.decode(
        frame(adapter, instrument, key, [row], rest=True), instrument, key
    )
    assert history.error is None
    assert history.events[0].payload.dedup_key == event.payload.dedup_key
    assert history.events[0].payload.is_subscription_history
    row["m"] = "true"
    rejected = adapter.decode(frame(adapter, instrument, key, payload), instrument, key)
    assert rejected.error and not rejected.events


def test_bybit_perp_noncontiguous_delta_reset_and_endpoint_identity():
    adapter = BybitAdapter()
    instrument, key = setup(adapter)
    assert (
        adapter.ws_endpoint(instrument, key)
        == "wss://stream.bybit.com/v5/public/linear"
    )
    assert "category=linear" in adapter.snapshot_request(instrument, key).endpoint_id
    reducer = adapter.new_reducer(instrument, key)
    reducer.apply(events(adapter, instrument, key, bybit_book()))
    result = reducer.apply(
        events(
            adapter,
            instrument,
            key,
            bybit_book(update=900, seq=8000, kind="delta", quantity="0.015"),
            2,
        )
    )
    assert result.recovery is None
    assert result.book.bids[0].quantity == Decimal("0.015")
    stale = reducer.apply(
        events(
            adapter,
            instrument,
            key,
            bybit_book(update=899, seq=8001, kind="delta", quantity="5"),
            3,
        )
    )
    assert not stale.changed
    assert stale.book.bids[0].quantity == Decimal("0.015")
    reset = reducer.apply(
        events(adapter, instrument, key, bybit_book(update=1, seq=1, price="98.00"), 4)
    )
    assert reset.recovery is None
    assert [level.price for level in reset.book.bids] == [Decimal("98.00")]
    restored = adapter.new_reducer(instrument, key)
    restored.restore(reducer.checkpoint())
    assert restored.book == reducer.book
    wrong = replace(
        frame(adapter, instrument, key, bybit_book()), endpoint_id=adapter.stream
    )
    assert adapter.decode(wrong, instrument, key).error


@pytest.mark.parametrize(
    "side,expected", [("Buy", "buy"), ("Sell", "sell"), ("Other", "unknown")]
)
def test_bybit_linear_trade_units_and_side(side, expected):
    adapter = BybitAdapter()
    instrument, key = setup(adapter, channel="trades")
    row = {
        "T": 1700000000001,
        "s": "BTCUSDT",
        "S": side,
        "v": "0.015",
        "p": "99.25",
        "i": "perp-1",
    }
    payload = {"topic": "publicTrade.BTCUSDT", "type": "snapshot", "data": [row]}
    trade = events(adapter, instrument, key, payload)[0].payload
    assert trade.quantity == Decimal("0.015") and trade.taker_side == expected
    payload["data"].append({**row, "s": "ETHUSDT"})
    rejected = adapter.decode(frame(adapter, instrument, key, payload), instrument, key)
    assert rejected.error and not rejected.events


@pytest.mark.parametrize(
    "factory,builder",
    [(BinanceGlobalAdapter, binance_book), (BybitAdapter, bybit_book)],
)
def test_perpetual_invalid_lot_rejected_atomically(factory, builder):
    adapter = factory()
    instrument, key = setup(adapter)
    bad = adapter.decode(
        frame(adapter, instrument, key, builder(quantity="0.001")), instrument, key
    )
    assert bad.error and not bad.events


def test_binance_discovery_returns_both_native_products_and_surfaces_failure(
    monkeypatch,
):
    adapter = BinanceGlobalAdapter()
    requests = []

    async def fetch(request):
        requests.append(request.endpoint_id)
        return encoded(binance_metadata(perpetual="/fapi/" in request.endpoint_id))

    monkeypatch.setattr("src.workstation.transport.fetch_public", fetch)
    catalog = asyncio.run(adapter.discover())
    assert {item.api_family for item in catalog.instruments} == {"spot-v3", "usdm-v1"}
    assert len(requests) == 2
    for item in catalog.instruments:
        key = SubscriptionKey.for_instrument(
            item, depth=20 if item.market_type == "perpetual" else 5000
        )
        assert adapter.subscription_requests(item, key)

    async def failed(request):
        if "/fapi/" in request.endpoint_id:
            raise OSError("unavailable")
        return encoded(binance_metadata(perpetual=False))

    monkeypatch.setattr("src.workstation.transport.fetch_public", failed)
    partial = asyncio.run(adapter.discover())
    assert {item.api_family for item in partial.instruments} == {"spot-v3"}
    assert dict((family, state) for family, state, _ in adapter.discovery_evidence) == {
        "spot-v3": "available",
        "usdm-v1": "unavailable",
    }
    assert "OSError: unavailable" in adapter.discovery_evidence[1][2]
    assert partial.instruments[0].metadata_source == (
        "https://data-api.binance.vision/api/v3/exchangeInfo"
    )


@pytest.mark.parametrize("factory", [BinanceGlobalAdapter, BybitAdapter])
@pytest.mark.parametrize("failed_market", ["spot", "perpetual"])
@pytest.mark.parametrize("failure", ["blocked", "malformed"])
def test_family_discovery_preserves_exact_reachable_specs(
    monkeypatch, factory, failed_market, failure
):
    adapter = factory()
    builder = binance_metadata if factory is BinanceGlobalAdapter else bybit_metadata

    async def fetch(request):
        perpetual = (
            "/fapi/" in request.endpoint_id or "category=linear" in request.endpoint_id
        )
        if ("perpetual" if perpetual else "spot") == failed_market:
            if failure == "blocked":
                raise PublicTransportError("blocked", status=451)
            return b'{"invalid":true}'
        return encoded(builder(perpetual=perpetual))

    monkeypatch.setattr("src.workstation.transport.fetch_public", fetch)
    snapshot = asyncio.run(adapter.discover())
    assert len(snapshot.instruments) == 1
    instrument = snapshot.instruments[0]
    assert instrument.market_type != failed_market
    assert instrument.tick_size == Decimal("0.25")
    assert instrument.lot_size == Decimal("0.005")
    assert instrument.metadata_source == snapshot.source
    assert instrument.metadata_version
    expected = "blocked" if failure == "blocked" else "unavailable"
    assert {state for _, state, _ in adapter.discovery_evidence} == {
        "available",
        expected,
    }
    if factory is BybitAdapter:
        assert snapshot.source.startswith("https://api.bytick.com/")
        key = SubscriptionKey.for_instrument(instrument, depth=50)
        assert adapter.snapshot_request(instrument, key).endpoint_id.startswith(
            "https://api.bytick.com/"
        )


@pytest.mark.parametrize("factory", [BinanceGlobalAdapter, BybitAdapter])
def test_family_discovery_total_failure_raises_and_resets_evidence(
    monkeypatch, factory
):
    adapter = factory()

    async def failed(request):
        raise PublicTransportError("blocked", status=403)

    monkeypatch.setattr("src.workstation.transport.fetch_public", failed)
    with pytest.raises(PublicTransportError):
        asyncio.run(adapter.discover())
    assert len(adapter.discovery_evidence) == 2
    assert all(state == "blocked" for _, state, _ in adapter.discovery_evidence)
    assert all("403" in detail for _, _, detail in adapter.discovery_evidence)

    async def healthy(request):
        perpetual = (
            "/fapi/" in request.endpoint_id or "category=linear" in request.endpoint_id
        )
        builder = (
            binance_metadata if factory is BinanceGlobalAdapter else bybit_metadata
        )
        return encoded(builder(perpetual=perpetual))

    monkeypatch.setattr("src.workstation.transport.fetch_public", healthy)
    assert len(asyncio.run(adapter.discover()).instruments) == 2
    assert all(state == "available" for _, state, _ in adapter.discovery_evidence)


def test_bybit_discovery_paginates_and_retains_spot(monkeypatch):
    adapter = BybitAdapter()
    requests = []

    async def fetch(request):
        requests.append(request.endpoint_id)
        if "category=spot" in request.endpoint_id:
            return encoded(bybit_metadata(perpetual=False))
        if "cursor=" in request.endpoint_id:
            assert "cursor=next%2Fpage%3F%26" in request.endpoint_id
            return encoded(bybit_metadata(base="ETH"))
        return encoded(bybit_metadata(cursor="next/page?&"))

    monkeypatch.setattr("src.workstation.transport.fetch_public", fetch)
    catalog = asyncio.run(adapter.discover())
    assert len(requests) == 3
    assert {(item.api_family, item.base.ticker) for item in catalog.instruments} == {
        ("v5-spot", "BTC"),
        ("v5-linear", "BTC"),
        ("v5-linear", "ETH"),
    }
    for item in catalog.instruments:
        key = SubscriptionKey.for_instrument(item, depth=50)
        assert adapter.subscription_requests(item, key)


def test_bybit_mixed_discovery_excludes_tradfi_but_preserves_crypto(monkeypatch):
    adapter = BybitAdapter()
    linear = bybit_metadata()
    rows = linear["result"]["list"]
    rows.append(
        {**bybit_metadata(base="ETH")["result"]["list"][0], "symbolType": "normal"}
    )
    for base, symbol_type in (
        ("AAPL", "stock"),
        ("SPY", "ETF"),
        ("XAU", "commodity"),
        ("EUR", "forex"),
    ):
        rows.append(
            {
                **bybit_metadata(base=base)["result"]["list"][0],
                "symbolType": symbol_type,
            }
        )

    async def fetch(request):
        return encoded(
            bybit_metadata(perpetual=False)
            if "category=spot" in request.endpoint_id
            else linear
        )

    monkeypatch.setattr("src.workstation.transport.fetch_public", fetch)
    catalog = asyncio.run(adapter.discover())
    assert {(item.market_type, item.native_symbol) for item in catalog.instruments} == {
        ("spot", "BTCUSDT"),
        ("perpetual", "BTCUSDT"),
        ("perpetual", "ETHUSDT"),
    }
    for item in catalog.instruments:
        assert item.tick_size == Decimal("0.25")
        assert item.lot_size == Decimal("0.005")
        assert item.metadata_version and item.metadata_source
        assert adapter.subscription_requests(
            item, SubscriptionKey.for_instrument(item, depth=50)
        )
        if item.market_type == "perpetual":
            assert item.settlement.asset_id == "USDT"
            assert item.contract_multiplier == Decimal("1")
            assert item.constraints.min_base == Decimal("0.010")
            assert item.constraints.max_market_base == Decimal("50")


@pytest.mark.parametrize(
    "failure", ["repeat", "bound", "wrong_category", "endpoint_error", "duplicate"]
)
def test_bybit_discovery_retains_spot_but_never_incomplete_linear(monkeypatch, failure):
    adapter = BybitAdapter()
    adapter.MAX_METADATA_PAGES = 2
    calls = 0

    async def fetch(request):
        nonlocal calls
        calls += 1
        if "category=spot" in request.endpoint_id:
            return encoded(bybit_metadata(perpetual=False))
        if failure == "endpoint_error":
            return encoded({"retCode": 10001})
        if failure == "wrong_category":
            return encoded(bybit_metadata(perpetual=False))
        cursor = "same" if failure == "repeat" else str(calls)
        if failure == "duplicate" and calls == 3:
            cursor = ""
        return encoded(bybit_metadata(cursor=cursor))

    monkeypatch.setattr("src.workstation.transport.fetch_public", fetch)
    catalog = asyncio.run(adapter.discover())
    assert {item.api_family for item in catalog.instruments} == {"v5-spot"}
    assert adapter.discovery_evidence[1][0:2] == ("v5-linear", "unavailable")
    assert "ValueError:" in adapter.discovery_evidence[1][2]
    assert calls <= 3


@pytest.mark.parametrize("factory", [BinanceGlobalAdapter, BybitAdapter])
def test_metadata_filters_contract_types_and_rejects_wrong_settlement(factory):
    adapter = factory()
    if isinstance(adapter, BinanceGlobalAdapter):
        value = binance_metadata()
        rows = value["symbols"]
        rows.append({**rows[0], "contractType": "CURRENT_QUARTER"})

        def parse():
            return adapter.parse_metadata(encoded(value), api_family="usdm-v1")

        settlement = "marginAsset"
    else:
        value = bybit_metadata()
        rows = value["result"]["list"]
        rows.append({**rows[0], "contractType": "LinearFutures"})

        def parse():
            return adapter.parse_metadata(encoded(value))

        settlement = "settleCoin"
    assert len(parse().instruments) == 1
    rows[0][settlement] = "USDC"
    with pytest.raises(ValueError, match="settlement"):
        parse()


def test_missing_increment_metadata_is_not_inferred_from_precision():
    binance = binance_metadata()
    del binance["symbols"][0]["filters"][0]["tickSize"]
    with pytest.raises(KeyError):
        BinanceGlobalAdapter().parse_metadata(encoded(binance), api_family="usdm-v1")
    bybit = bybit_metadata()
    del bybit["result"]["list"][0]["lotSizeFilter"]["qtyStep"]
    with pytest.raises(KeyError):
        BybitAdapter().parse_metadata(encoded(bybit))
    with pytest.raises(ValueError, match="incomplete"):
        BybitAdapter().parse_metadata(encoded(bybit_metadata(cursor="next")))


def test_binance_futures_supervisor_publishes_ws_book_without_rest_bootstrap():
    from src.workstation.ingestion import FeedCallbacks, FeedSupervisor

    async def scenario():
        adapter = BinanceGlobalAdapter()
        instrument, key = setup(adapter)
        valid = asyncio.Event()
        raw_frames, fetches = [], []

        class Socket:
            def __init__(self):
                self.sent = []
                self.closed = False

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                self.closed = True

            async def __aiter__(self):
                yield encoded({"result": None, "id": 1})
                yield encoded(binance_book())
                await asyncio.Event().wait()

            async def send(self, body):
                self.sent.append(body)

        socket = Socket()

        def connect(endpoint, **kwargs):
            assert endpoint == "wss://fstream.binance.com/public/stream"
            return socket

        async def fetch(request):
            fetches.append(request)
            raise AssertionError("partial snapshots must not bootstrap with REST")

        def publish(book):
            if book.quality.integrity == "valid":
                valid.set()

        feed = FeedSupervisor(
            adapter,
            instrument,
            key,
            callbacks=FeedCallbacks(raw=raw_frames.append, book=publish),
            ws_connect=connect,
            http_fetch=fetch,
        )
        task = asyncio.create_task(feed._ws_session())
        valid_task = asyncio.create_task(valid.wait())
        try:
            done, _ = await asyncio.wait(
                (task, valid_task), timeout=2, return_when=asyncio.FIRST_COMPLETED
            )
            if task in done:
                await task
            assert valid_task in done
            assert feed.book.quality.integrity == "valid"
            assert feed.book.quality.source_mode == "ws_snapshot"
            assert not feed.book.quality.coverage.full_depth
            assert feed.book.bids[0].quantity == Decimal("1.005")
            assert feed.book.asks[0].price == Decimal("101.00")
            assert json.loads(socket.sent[0])["params"] == ["btcusdt@depth20@100ms"]
            assert raw_frames and all(raw.transport == "ws" for raw in raw_frames)
            assert not fetches
        finally:
            task.cancel()
            valid_task.cancel()
            await asyncio.gather(task, valid_task, return_exceptions=True)
        assert socket.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("established", [False, True])
@pytest.mark.parametrize("acknowledged", [False, True])
def test_deribit_quiet_trades_require_observed_reducer_owned_pongs(
    established, acknowledged
):
    from src.workstation.adapters.deribit import DeribitAdapter
    from src.workstation.events import Control, Trade
    from src.workstation.ingestion import (
        FeedCallbacks,
        FeedSupervisor,
        SupervisorConfig,
    )

    async def scenario():
        adapter = DeribitAdapter()
        instrument = adapter.parse_metadata(
            encoded(
                {
                    "jsonrpc": "2.0",
                    "result": [
                        {
                            "instrument_name": "BTC-PERPETUAL",
                            "kind": "future",
                            "settlement_period": "perpetual",
                            "instrument_type": "reversed",
                            "base_currency": "BTC",
                            "quote_currency": "USD",
                            "settlement_currency": "BTC",
                            "contract_size": 10,
                            "min_trade_amount": 10,
                            "tick_size": "0.5",
                            "is_active": True,
                        }
                    ],
                }
            )
        ).instruments[0]
        key = SubscriptionKey.for_instrument(instrument, channel="trades")
        raw, delivered = [], []
        ping_sent, received = asyncio.Event(), asyncio.Event()
        pong = asyncio.get_running_loop().create_future()

        class PingSocket:
            async def ping(self):
                ping_sent.set()
                return pong

        def captured(frame):
            raw.append(frame)
            received.set()

        assert SupervisorConfig().heartbeat_interval == 20
        feed = FeedSupervisor(
            adapter,
            instrument,
            key,
            callbacks=FeedCallbacks(raw=captured, events=delivered.extend),
            config=SupervisorConfig(heartbeat_interval=0.001, silence_timeout=0.05),
        )
        feed._start_epoch(False)
        if established:
            feed._receive(
                encoded(
                    {
                        "jsonrpc": "2.0",
                        "method": "subscription",
                        "params": {
                            "channel": "trades.BTC-PERPETUAL.100ms",
                            "data": [
                                {
                                    "instrument_name": "BTC-PERPETUAL",
                                    "trade_id": "first",
                                    "trade_seq": 1,
                                    "timestamp": 1700000000123,
                                    "price": "50000",
                                    "amount": "100",
                                    "direction": "buy",
                                }
                            ],
                        },
                    }
                ),
                "ws",
                adapter.ws_endpoint(instrument, key),
                feed.epoch,
            )
            await feed._drain_queued()
            assert feed._valid
            raw.clear()
            received.clear()
        # A quiet established stream needs transport evidence, not invented trades.
        feed.last_valid = feed.last_transport = 0
        delivered_before = len(delivered)
        task = asyncio.create_task(feed._heartbeat(PingSocket()))
        try:
            await asyncio.wait_for(ping_sent.wait(), 1)
            assert not raw and feed.queue.empty()
            assert feed.last_valid == feed.last_transport == 0
            assert len(delivered) == delivered_before
            if acknowledged:
                pong.set_result(0.001)
                await asyncio.wait_for(received.wait(), 1)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                assert len(raw) == 1
                assert raw[0].transport == "local"
                assert raw[0].endpoint_id == "ws_pong"
                assert feed.last_transport > 0
                # Receiving a pong enqueues evidence; only the consumer reduces it.
                assert feed.last_valid == 0
                assert len(delivered) == delivered_before
                assert feed.queue.qsize() == 1
                await feed._drain_queued()
                heartbeat = delivered[-1]
                assert heartbeat.payload == Control("heartbeat", "ws_pong")
                assert heartbeat.ingest_ordinal == raw[0].ingest_ordinal
                assert heartbeat.receive_mono_ns == raw[0].receive_mono_ns
                assert heartbeat.exchange_event_wall_ns is None
                assert feed._valid is established
                assert (feed.last_valid > 0) is established
                assert not feed._fault.is_set()
                if established:
                    assert feed.lifecycle == "live"
            else:
                await asyncio.wait_for(task, 1)
                assert feed._fault.is_set()
                assert feed._fault_reason == "heartbeat_send_failed"
                assert not raw and feed.queue.empty()
                assert feed.last_valid == feed.last_transport == 0
            trades = [event for event in delivered if isinstance(event.payload, Trade)]
            assert len(trades) == int(established)
            if established:
                assert trades[0].payload.trade_id == "first"
                assert trades[0].exchange_event_wall_ns == 1700000000123000000
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
