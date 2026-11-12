"""Exact native schemas, calendar semantics, and bounded candle admission."""

import asyncio
from dataclasses import replace
from decimal import Decimal, localcontext
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from src.workstation import candles as module
from src.workstation.analytics import AnalyticsEngine
from src.workstation.candles import (
    CandleService,
    FAMILIES,
    SECOND,
    bucket_end,
    native_request,
    parse_native,
    request_start,
    session_candles,
    supported_intervals,
)
from src.workstation.events import (
    Control,
    Gap,
    SubscriptionKey,
    Trade,
    synthetic_envelope,
)
from src.workstation.instruments import AssetIdentity, Instrument, OrderConstraints
from src.workstation.market_presentation import AdmittedTradeHistory
from src.workstation.recording import RecordedBatch, StreamSpec
from src.workstation.replay import ReplayClock
from src.workstation.transport import PublicTransportError

HOUR = 3600 * SECOND
START = 1700002800 * SECOND  # 2023-11-14 23:00 UTC
PRICE = "100.12345678901234567890123456789012345"


def instrument(venue="hyperliquid", **options):
    return Instrument(
        instrument_id=f"{venue}:{FAMILIES[venue]}:spot:BTC",
        metadata_version="native-fixture",
        venue=venue,
        api_family=FAMILIES[venue],
        native_symbol="BTC",
        base=AssetIdentity("BTC", "BTC"),
        quote=AssetIdentity("USD", "USD"),
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.001"),
        native_asset_id="XXBTZUSD",
        **options,
    )


def payload(venue, starts=(START,)):
    rows = []
    for start in starts:
        seconds, ms = start // SECOND, start // 1_000_000
        if venue.startswith("binance-"):
            rows.append(
                [
                    ms,
                    PRICE,
                    "101",
                    "99",
                    "100.5",
                    "0.00000000000000000000000000000000000001",
                    ms + 3599999,
                    "0",
                    3,
                    "0",
                    "0",
                    "0",
                ]
            )
        elif venue == "coinbase":
            rows.append(
                dict(
                    start=str(seconds),
                    open=PRICE,
                    high="101",
                    low="99",
                    close="100.5",
                    volume="0.00000000000000000000000000000000000001",
                )
            )
        elif venue == "kraken":
            rows.append(
                [
                    seconds,
                    PRICE,
                    "101",
                    "99",
                    "100.5",
                    "100",
                    "0.00000000000000000000000000000000000001",
                    3,
                ]
            )
        elif venue == "bybit":
            rows.append(
                [
                    str(ms),
                    PRICE,
                    "101",
                    "99",
                    "100.5",
                    "0.00000000000000000000000000000000000001",
                    "0",
                ]
            )
        elif venue == "kucoin":
            rows.append(
                [
                    str(seconds),
                    PRICE,
                    "100.5",
                    "101",
                    "99",
                    "0.00000000000000000000000000000000000001",
                    "0",
                ]
            )
        else:
            rows.append(
                dict(
                    t=ms,
                    T=ms + 3599999,
                    s="BTC",
                    i="1h",
                    o=PRICE,
                    h="101",
                    l="99",
                    c="100.5",
                    v="0.00000000000000000000000000000000000001",
                )
            )
    if venue == "coinbase":
        root = {"candles": rows}
    elif venue == "kraken":
        root = {"error": [], "result": {"XXBTZUSD": rows, "last": starts[-1] // SECOND}}
    elif venue == "bybit":
        root = {
            "retCode": 0,
            "result": {"symbol": "BTC", "category": "spot", "list": rows},
        }
    elif venue == "kucoin":
        root = {"code": "200000", "data": rows}
    else:
        root = rows
    return json.dumps(root).encode()


@pytest.mark.parametrize("venue", tuple(FAMILIES))
def test_native_hour_schema_exact_values_gaps_and_closed(venue):
    product = instrument(venue)
    request = native_request(product, "1h", 300, START + HOUR // 2)
    assert request.endpoint_id.startswith("https://")
    result = parse_native(
        payload(venue, (START, START - 2 * HOUR)), product, "1h", 300, START + HOUR // 2
    )
    assert [item.start_ns for item in result] == [START - 2 * HOUR, START]
    assert all(item.end_ns - item.start_ns == HOUR for item in result)
    assert result[-1].wire(START + HOUR // 2)["open"] == PRICE
    assert result[-1].volume == Decimal("1e-38")
    assert result[0].wire(START)["closed"] is True
    assert result[-1].wire(START + HOUR // 2)["closed"] is False
    assert result[-1].high == Decimal("101") and result[-1].low == Decimal("99")


@pytest.mark.parametrize("venue", tuple(FAMILIES))
def test_native_duplicate_and_future_rows_are_rejected(venue):
    with pytest.raises(ValueError, match="duplicate"):
        parse_native(
            payload(venue, (START, START)), instrument(venue), "1h", 300, START + HOUR
        )
    with pytest.raises(ValueError, match="future"):
        parse_native(payload(venue), instrument(venue), "1h", 300, START - 1)


def test_calendar_month_not_thirty_days_and_native_week_alignment():
    february = 1706745600 * SECOND
    march = 1709251200 * SECOND
    assert bucket_end(february, "1M") == march  # leap-year February
    assert request_start(march + HOUR, "1M", 2) == february
    assert bucket_end(1700006400 * SECOND, "1w") == (1700006400 + 604800) * SECOND
    with pytest.raises(ValueError, match="calendar"):
        bucket_end(february + 86400 * SECOND, "1M")
    with pytest.raises(ValueError, match="unaligned"):
        bucket_end(START + SECOND, "1h")
    assert "1M" not in supported_intervals(instrument("kraken"))


def test_native_identity_parameters_and_no_cross_family_substitution():
    request = native_request(instrument("coinbase"), "1h", 500, START)
    assert urlsplit(request.endpoint_id).netloc == "api.coinbase.com"
    assert parse_qs(urlsplit(request.endpoint_id).query)["limit"] == ["350"]
    assert "/brokerage/market/products/BTC/candles" in request.endpoint_id
    product = replace(
        instrument(),
        native_symbol="xyz:XYZ100",
        dex_namespace="xyz",
        market_type="perpetual",
    )
    assert (
        json.loads(native_request(product, "1h", 300, START).body)["req"]["coin"]
        == "xyz:XYZ100"
    )
    assert not supported_intervals(replace(product, api_family="other"))
    wrong = json.loads(payload("hyperliquid"))
    wrong[0]["s"] = "OTHER"
    with pytest.raises(ValueError, match="identity"):
        parse_native(json.dumps(wrong).encode(), instrument(), "1h", 300, START)


def test_invalid_envelope_end_precision_and_json_float_exactness():
    rows = json.loads(payload("hyperliquid"))
    rows[0]["h"] = "99"
    with pytest.raises(ValueError, match="envelope"):
        parse_native(json.dumps(rows).encode(), instrument(), "1h", 300, START)
    rows[0]["h"] = "101"
    rows[0]["T"] -= 1
    with pytest.raises(ValueError, match="end"):
        parse_native(json.dumps(rows).encode(), instrument(), "1h", 300, START)
    raw = payload("hyperliquid").replace(('"' + PRICE + '"').encode(), PRICE.encode())
    assert parse_native(raw, instrument(), "1h", 300, START)[0].open == Decimal(PRICE)


def test_bounded_cache_concurrency_cancellation_and_access_errors(monkeypatch):
    async def scenario():
        service = CandleService()
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def fetch(request, **options):
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return payload("hyperliquid")

        monkeypatch.setattr(module, "fetch_public", fetch)
        tasks = [
            asyncio.create_task(service.query(instrument(), "1h", limit))
            for limit in range(1, 5)
        ]
        await entered.wait()
        # All four requests enter without a queue or detached task.
        await asyncio.sleep(0)
        assert service.active == 4
        overloaded = await service.query(instrument(), "1h", 5)
        assert overloaded["reasons"] == ["candle_capacity"]
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        assert service.active == 3 and not service.cache
        release.set()
        await asyncio.gather(*tasks[1:])
        assert service.active == 0
        before = calls
        assert (await service.query(instrument(), "1h", 2))["status"] == "ready"
        assert calls == before
        for count in range(5, 50):
            await service.query(instrument(), "1h", count)
        assert len(service.cache) == module.MAX_CACHE
        assert (await service.query(instrument("coinbase"), "1M"))[
            "status"
        ] == "unavailable"
        assert calls == before + 45

        async def blocked(request, **options):
            raise PublicTransportError("blocked", status=451)

        monkeypatch.setattr(module, "fetch_public", blocked)
        result = await service.query(instrument(), "1h", 500)
        assert result["reasons"] == ["blocked"] and result["candles"] == []
        assert service.active == 0

    asyncio.run(scenario())


class NativeFeed:
    def __init__(self, *products):
        self.products = products or (instrument(),)
        self.streams = tuple(
            StreamSpec(
                f"{index}-{channel}",
                product,
                SubscriptionKey.for_instrument(product, channel=channel),
                "test-v1",
                "test-config",
            )
            for index, product in enumerate(self.products)
            for channel in ("book", "trades")
        )
        self.engine = AnalyticsEngine(self.streams)
        self.history = AdmittedTradeHistory(self.streams, self.engine)
        self.clock = ReplayClock()
        self.batches = []

    def event(
        self,
        mono,
        price="100",
        quantity="1",
        *,
        product=0,
        wall=None,
        historical=False,
        dedup=None,
        payload=None,
        clock_id="recorded-clock",
        epoch=0,
        segment_id="synthetic-segment",
    ):
        spec = self.streams[product * 2 + 1]
        ordinal = len(self.batches) + 1
        return synthetic_envelope(
            spec.instrument,
            payload
            or Trade(
                str(ordinal),
                dedup or str(ordinal),
                Decimal(price),
                Decimal(quantity),
                quantity_unit=spec.instrument.quantity_unit,
                is_subscription_history=historical,
            ),
            ordinal=ordinal,
            mono_ns=mono,
            receive_wall_ns=START + mono if wall is None else wall,
            epoch=epoch,
            segment_id=segment_id,
            clock_id=clock_id,
            key=spec.key,
            stream_id=spec.stream_id,
        )

    def deliver(self, *args, **kwargs):
        event = self.event(*args, **kwargs)
        batch = RecordedBatch(len(self.batches) + 1, (event,))
        self.batches.append(batch)
        self.clock.advance(event)
        self.engine.on_batch(batch, (), self.clock)
        self.history.on_batch(batch, (), self.clock)
        return event


def test_session_aggregation_exact_partial_coverage_and_no_fabricated_gaps():
    feed = NativeFeed()
    feed.deliver(0, PRICE, "0.00000000000000000000000000000000000001")
    feed.deliver(120 * SECOND, "101", "0.2")
    feed.deliver(150 * SECOND, "99", "0.3")
    feed.deliver(160 * SECOND, "400", "10", historical=True)
    with localcontext() as context:
        context.prec = 6
        result = session_candles(feed.history, instrument(), "1h", 300)
    bar = result["candles"][0]
    assert (bar["open"], bar["high"], bar["low"], bar["close"]) == (
        PRICE,
        "101",
        "99",
        "99",
    )
    assert Decimal(bar["volume"]) == Decimal("0.50000000000000000000000000000000000001")
    assert bar["closed"] is False
    assert result["time_basis"] == "receive_utc"
    assert "subscription_history_excluded" in result["reasons"]
    assert "session_history_partial" in result["reasons"]
    assert len(session_candles(feed.history, instrument(), "1m", 300)["candles"]) == 2
    limited = session_candles(feed.history, instrument(), "1m", 1)
    assert int(limited["coverage_start_ns"]) == START + 120 * SECOND
    assert (
        session_candles(feed.history, instrument(), "7m", 300)["status"]
        == "unavailable"
    )


@pytest.mark.parametrize("venue", tuple(FAMILIES))
def test_three_hundred_hour_native_history_is_not_short_session_rebucketing(venue):
    starts = tuple(START - offset * HOUR for offset in range(300))
    result = parse_native(
        payload(venue, starts), instrument(venue), "1h", 300, START + HOUR
    )
    assert len(result) == 300
    assert result[-1].end_ns - result[0].start_ns == 300 * HOUR
    assert all(
        item.open == Decimal(PRICE) and item.close == Decimal("100.5")
        for item in result
    )


def test_cache_ttl_and_exact_metadata_identity(monkeypatch):
    async def scenario():
        service = CandleService()
        clock = [100.0]
        calls = []

        async def fetch(request, **options):
            calls.append(request)
            return payload("hyperliquid")

        monkeypatch.setattr(module, "fetch_public", fetch)
        monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
        product = instrument()
        await service.query(product, "1h")
        clock[0] += 4
        await service.query(product, "1h")
        assert len(calls) == 1
        clock[0] += 1
        await service.query(product, "1h")
        assert len(calls) == 2
        await service.query(replace(product, metadata_version="replacement"), "1h")
        assert len(calls) == 3

    asyncio.run(scenario())


def test_receive_projection_anchors_clock_corrections_and_resets_unrelated_clocks():
    feed = NativeFeed()
    feed.deliver(0)
    event = feed.deliver(10 * SECOND, wall=START + 2 * HOUR)
    assert feed.history.display_time(event) == START + 10 * SECOND
    assert feed.history.now_ns == START + 10 * SECOND
    assert "receive_clock_discontinuity" in feed.history.reasons
    feed.deliver(5 * SECOND, "500", wall=START + 3 * HOUR)
    result = session_candles(feed.history, instrument(), "1h", 300)
    assert "nonadvancing_receive_pair" in result["reasons"]
    assert result["candles"][0]["high"] == "100"  # analytics rejects clock regression
    feed.deliver(0, "200", clock_id="replacement-clock", wall=START + 4 * HOUR)
    result = session_candles(feed.history, instrument(), "1h", 300)
    assert "source_clock_changed" in result["reasons"]
    assert len(result["candles"]) == 1 and result["candles"][0]["open"] == "200"
    feed.history.reset()
    assert session_candles(feed.history, instrument(), "1h", 300)["candles"] == []


@pytest.mark.parametrize("interval", module.LOCAL_INTERVALS)
def test_all_shared_timeframes_use_exact_utc_source_boundaries(interval):
    feed = NativeFeed()
    feed.deliver(0)
    feed.deliver(2 * HOUR, "101")
    result = session_candles(feed.history, instrument(), interval, 300)
    assert result["status"] == "ready"
    assert result["supported_intervals"] == list(module.LOCAL_INTERVALS)
    for bar in result["candles"]:
        start, end = module.session_bucket(int(bar["start_ns"]), interval)
        assert (str(start), str(end)) == (bar["start_ns"], bar["end_ns"])
        assert int(bar["start_ns"]) <= feed.history.now_ns
        assert bar["closed"] == (end <= feed.history.now_ns)
    assert module.session_bucket(0, "1w") == (0, 604800 * SECOND)
    assert module.session_bucket(1709164800 * SECOND, "1M") == (
        1706745600 * SECOND,
        1709251200 * SECOND,
    )


def perpetual(venue, family, quote="USDT", *, streaming=False):
    changes = dict(
        instrument_id=f"{venue}:{family}:perpetual:BTC{quote}",
        api_family=family,
        native_symbol="BTC" + quote,
        market_type="perpetual",
        quote=AssetIdentity(quote, quote),
        settlement=AssetIdentity(quote, quote),
    )
    if streaming:
        changes.update(
            metadata_status="streaming_only",
            tick_size=None,
            lot_size=None,
            constraints=OrderConstraints(
                price_rule="unconstrained", size_rule="unconstrained"
            ),
        )
    return replace(instrument(venue), **changes)


@pytest.mark.parametrize(
    "venue,family,host,path,category",
    (
        ("binance-global", "usdm-v1", "fapi.binance.com", "/fapi/v1/klines", None),
        ("bybit", "v5-linear", "api.bytick.com", "/v5/market/kline", ["linear"]),
    ),
)
def test_optional_perpetual_history_uses_real_native_family(
    venue, family, host, path, category
):
    product = perpetual(venue, family)
    request = native_request(product, "1h", 20, START)
    url = urlsplit(request.endpoint_id)
    assert (url.netloc, url.path) == (host, path)
    assert parse_qs(url.query).get("category") == category
    raw = payload(venue)
    if venue == "bybit":
        root = json.loads(raw)
        root["result"]["category"] = "linear"
        root["result"]["symbol"] = product.native_symbol
        raw = json.dumps(root).encode()
        with pytest.raises(ValueError, match="identity"):
            parse_native(payload(venue), product, "1h", 20, START)
    assert parse_native(raw, product, "1h", 20, START)[0].open == Decimal(PRICE)


def test_admission_dedup_gaps_unknown_quantity_and_retention(monkeypatch):
    import src.workstation.market_presentation as presentation

    product = replace(instrument(), quantity_unit="contracts")
    feed = NativeFeed(product)
    feed.deliver(0)
    result = session_candles(feed.history, product, "1m", 300)
    assert not result["candles"] and "unknown_trade_quantity" in result["reasons"]
    feed = NativeFeed()
    monkeypatch.setattr(presentation, "MAX_TRADE_ROWS", 2)
    feed.deliver(0, dedup="same")
    feed.deliver(1, "200", dedup="same")
    assert len(feed.history.trades) == 1
    feed.deliver(2, "250")
    feed.deliver(2, "275")
    assert feed.history.floor_cursor == (1, 0)
    feed.deliver(2, payload=Control("disconnect", "test_disconnect"))
    feed.deliver(2, payload=Gap("test_gap"))
    feed.deliver(3, "300")
    feed.deliver(4, "400")
    result = session_candles(feed.history, instrument(), "1m", 300)
    assert result["retention"]["retained_trades"] == 2
    assert result["retention"]["truncated"] is True
    assert result["candles"][0]["open"] == "300"
    assert "session_discontinuities" in result["reasons"]
    assert "retained_history_truncated" in result["reasons"]
    assert feed.history.floor_cursor == (1, 0)
    assert feed.history.stream_floors["0-trades"] == (6, 0)
    monkeypatch.setattr(presentation, "MAX_TRADE_BYTES", 1)
    feed.deliver(5)
    assert feed.history.bytes == 0 and not feed.history.trades


def test_live_streaming_perpetual_api_is_immediate_exact_and_rest_independent(
    tmp_path, monkeypatch
):
    from httpx import ASGITransport, AsyncClient
    from src.workstation.api import create_app
    from src.workstation.runtime import ClientSession, TerminalRuntime

    primary = perpetual("binance-global", "usdm-v1", streaming=True)
    peer = perpetual("bybit", "v5-linear", quote="USDC", streaming=True)
    wrong_base = replace(
        peer, instrument_id="bybit:wrong-base", base=AssetIdentity("ETH", "ETH")
    )
    spot = instrument("coinbase")
    feed = NativeFeed(primary, peer, wrong_base, spot)
    feed.deliver(0, PRICE, "0.1")
    feed.deliver(SECOND, "101", "0.2", product=1, wall=START + 8 * HOUR)
    feed.deliver(2 * SECOND, "99", "0.3")
    calls = []

    async def denied(request, **kwargs):
        calls.append(request)
        raise PublicTransportError("blocked", status=451)

    monkeypatch.setattr(module, "fetch_public", denied)

    async def scenario():
        runtime = TerminalRuntime(state_dir=tmp_path)
        session = ClientSession(runtime, "live", "live")
        runtime.sessions[session.id] = session
        try:
            await session.work(
                session._initialize_live,
                feed.streams,
                str(primary.instrument_id),
                (),
                "recorded-clock",
            )
            session.live_clock = ReplayClock()
            session.status = "ready"
            for batch in feed.batches:
                await session.work(session._apply_live, batch.events, (), 1000)
            request = dict(session_id=session.id, generation="1", interval="1h")
            async with AsyncClient(
                transport=ASGITransport(app=create_app(runtime)),
                base_url="http://127.0.0.1",
            ) as client:

                async def query(**options):
                    return await client.post(
                        "/api/v1/candles", json={**request, **options}
                    )

                response = await asyncio.wait_for(query(), 2)
                assert response.status_code == 200, response.text
                primary_result = response.json()
                assert primary_result["source"] == "session_trades" and not calls
                assert primary_result["instrument_ids"] == [str(primary.instrument_id)]
                assert primary_result["candles"][0]["volume"] == "0.4"
                assert primary_result["candles"][0]["high"] == PRICE
                result = (
                    await query(
                        instrument_ids=[
                            str(peer.instrument_id),
                            str(primary.instrument_id),
                        ]
                    )
                ).json()
                assert result["scope"] == "aggregate" and result["venue"] == "aggregate"
                assert result["instrument_ids"] == [
                    str(primary.instrument_id),
                    str(peer.instrument_id),
                ]
                assert result["quote_asset_ids"] == ["USDC", "USDT"]
                assert result["candles"][0]["volume"] == "0.6"
                assert result["candles"][0]["high"] == "101"
                assert result["candles"][0]["close"] == "99"
                assert result["source_identity"] == session.source_identity
                tape = (
                    await client.post(
                        "/api/v1/market-data",
                        json={"session_id": session.id, "generation": "1"},
                    )
                ).json()
                assert [item["display_time_ns"] for item in tape["trades"]] == [
                    str(START),
                    str(START + SECOND),
                    str(START + 2 * SECOND),
                ]
                for ids in (
                    [str(peer.instrument_id)],
                    [str(primary.instrument_id)] * 2,
                    [str(primary.instrument_id), "unknown"],
                    [str(primary.instrument_id), str(wrong_base.instrument_id)],
                    [str(primary.instrument_id), str(spot.instrument_id)],
                ):
                    assert (await query(instrument_ids=ids)).status_code == 422
                assert (
                    await query(
                        source="venue_ohlcv",
                        instrument_ids=[
                            str(primary.instrument_id),
                            str(peer.instrument_id),
                        ],
                    )
                ).status_code == 422
                historical = (await query(source="venue_ohlcv")).json()
                assert historical["source"] == "venue_ohlcv"
                assert historical["time_basis"] == "exchange_utc"
                assert historical["status"] == "unavailable" and len(calls) == 1
                assert (await query()).json()["candles"] == primary_result["candles"]
                gap_event = feed.deliver(
                    3 * SECOND,
                    payload=Gap("missing", affected_streams=("1-trades",)),
                    wall=START + 9 * HOUR,
                )
                next_event = feed.deliver(4 * SECOND, "90", "0.5")
                for event in (gap_event, next_event):
                    await session.work(session._apply_live, (event,), (), 1000)
                after_gap = (
                    await query(
                        instrument_ids=[
                            str(primary.instrument_id),
                            str(peer.instrument_id),
                        ]
                    )
                ).json()
                assert after_gap["candles"][0]["high"] == "90"
                assert after_gap["candles"][0]["volume"] == "0.5"
                tape = (
                    await client.post(
                        "/api/v1/market-data",
                        json={"session_id": session.id, "generation": "1"},
                    )
                ).json()
                assert [item["trade"]["price"] for item in tape["trades"]] == ["90"]
                assert tape["trades"][0]["display_time_ns"] == str(START + 4 * SECOND)
                assert {item["stream_id"] for item in tape["boundaries"]} == {
                    "0-trades",
                    "1-trades",
                }
                assert {item["display_time_ns"] for item in tape["boundaries"]} == {
                    str(START + 3 * SECOND)
                }
                assert tape["source_identity"] == after_gap["source_identity"]
        finally:
            await runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ("demo", "replay"))
def test_replay_seek_rebuilds_admitted_candle_history_without_future(
    mode, tmp_path, monkeypatch
):
    from src.workstation.api import CandleRequest
    from src.workstation.demo import create_demo
    from src.workstation.demo_memory import MemoryReplaySource
    from src.workstation.runtime import ClientSession, TerminalRuntime, capture_identity

    async def forbidden(*args, **kwargs):
        raise AssertionError("session candles cannot request REST")

    monkeypatch.setattr(module, "fetch_public", forbidden)

    async def scenario():
        runtime = TerminalRuntime(state_dir=tmp_path)
        session = ClientSession(runtime, mode, "fixture")
        runtime.sessions[session.id] = session
        capture = await create_demo(tmp_path / "capture") if mode == "replay" else None
        try:
            if capture is not None:
                await session.work(
                    session._initialize_replay,
                    capture.reader.path,
                    (capture.reader.manifest, capture_identity(capture.reader.path)),
                )
            else:
                await session.work(
                    session._initialize_replay, MemoryReplaySource.demo()
                )
            selected = next(
                spec.instrument
                for spec in session.streams
                if spec.key.channel == "book"
            )
            await session.work(session.choose, str(selected.instrument_id))
            request = CandleRequest(
                session_id=session.id, generation="1", interval="1m"
            )
            initial = await runtime.candles(request)
            initial_cursor = session.replay.cursor.delivery_index
            await session.work(session.replay.seek_delivery, 1200)
            if capture is not None:
                checkpoints = tuple(
                    capture.reader.iter_checkpoints(before_delivery=1200)
                )
                assert (
                    checkpoints
                    and max(item.delivery_index for item in checkpoints) >= 1000
                )
                assert session.replay.checkpoint_status == "observer_history_recomputed"
            saved = await session.work(session.trade_history.checkpoint)
            prefix = await runtime.candles(request)
            await session.work(session.replay.step, 100)
            later = await runtime.candles(request)
            assert int(later["highwater"]) > int(initial["highwater"])
            await session.work(session.replay.seek_delivery, 1200)
            assert (await runtime.candles(request))["candles"] == prefix["candles"]
            assert await session.work(session.trade_history.checkpoint) == saved
            await session.work(session.trade_history.reset)
            await session.work(session.trade_history.restore, saved)
            assert (await runtime.candles(request))["candles"] == prefix["candles"]
            await session.work(session.replay.seek_delivery, initial_cursor)
            rewound = await runtime.candles(request)
            assert rewound["candles"] == initial["candles"]
            assert rewound["coverage_end_ns"] == initial["coverage_end_ns"]
            await session.work(session.replay.seek_delivery, 0)
            empty = await runtime.candles(request)
            assert empty["candles"] == [] and empty["coverage_end_ns"] is None
        finally:
            await runtime.close()
            if capture is not None:
                capture.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "boundary",
    ("gap", "fanout", "reset", "disconnect", "source_change", "segment", "epoch"),
)
def test_boundaries_remove_old_extremes_and_preserve_unaffected_cohort(boundary):
    primary, peer, other = instrument(), instrument("bybit"), instrument("coinbase")
    feed = NativeFeed(primary, peer, other)
    feed.deliver(0, "99999.00")
    feed.deliver(SECOND, "1.00", product=1)
    feed.deliver(2 * SECOND, "120.00", product=2)
    identity = {}
    if boundary in ("segment", "epoch"):
        identity = (
            {"segment_id": "next-segment"} if boundary == "segment" else {"epoch": 1}
        )
        feed.deliver(3 * SECOND, "100.00", **identity)
    else:
        payload = (
            Gap("missing", affected_streams=("1-trades",))
            if boundary == "fanout"
            else Gap("missing")
            if boundary == "gap"
            else Control(boundary, "boundary")
        )
        event = feed.deliver(3 * SECOND, payload=payload, wall=START + HOUR)
        assert feed.history.display_time(event) == START + 3 * SECOND
        feed.deliver(4 * SECOND, "100.00")
    selected = (primary, peer, other)
    result = session_candles(feed.history, primary, "1m", 300, cohort=selected)
    bar = result["candles"][0]
    assert (bar["open"], bar["high"], bar["low"], bar["close"]) == (
        ("120", "120", "100", "100")
        if boundary == "fanout"
        else ("1", "120", "1", "100")
    )
    assert bar["volume"] == ("2" if boundary == "fanout" else "3")
    assert all(
        item.event.payload.price != Decimal("99999") for item in feed.history.trades
    )
    assert set(feed.history.admitted) == {
        (item.delivery, item.event_index) for item in feed.history.trades
    }
    assert feed.history.bytes == sum(feed.history.sizes)
    assert "session_discontinuities" in result["reasons"]

    saved = feed.history.checkpoint()
    restored = AdmittedTradeHistory(feed.streams, feed.engine)
    restored.restore(saved)
    assert restored.checkpoint() == saved
    assert session_candles(restored, primary, "1m", 300, cohort=selected) == result
    assert restored.origins == feed.history.origins
    assert restored.stream_sources == feed.history.stream_sources
    assert restored.stream_floors == feed.history.stream_floors
    feed.deliver(5 * SECOND, "110.00", **identity)
    restored.on_batch(feed.batches[-1], (), feed.clock)
    assert restored.checkpoint() == feed.history.checkpoint()


def test_history_checkpoint_restores_retention_and_exact_clock_state(monkeypatch):
    import src.workstation.market_presentation as presentation

    monkeypatch.setattr(presentation, "MAX_TRADE_ROWS", 2)
    feed = NativeFeed()
    empty = feed.history.checkpoint()
    feed.deliver(0, "1.00")
    feed.deliver(SECOND, "2.00", wall=START + HOUR)
    feed.deliver(2 * SECOND, "3.00")
    saved = feed.history.checkpoint()
    expected = session_candles(feed.history, instrument(), "1m", 300)
    feed.deliver(3 * SECOND, "99999")
    feed.history.restore(saved)
    assert feed.history.checkpoint() == saved
    assert session_candles(feed.history, instrument(), "1m", 300) == expected
    assert feed.history.floor_cursor == (1, 0)
    assert feed.history.truncated
    assert feed.history.now_ns == START + 2 * SECOND
    feed.history.on_batch(feed.batches[-1], (), feed.clock)
    assert feed.history.highwater == 4
    assert feed.history.floor_cursor == (2, 0)
    feed.history.restore(empty)
    assert feed.history.checkpoint() == empty
    assert not feed.history.trades and not feed.history.admitted
