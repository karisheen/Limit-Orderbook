"""Exact native schemas, calendar semantics, and bounded candle admission."""

import asyncio
from dataclasses import replace
from decimal import Decimal, localcontext
import json
from types import SimpleNamespace
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
from src.workstation.events import EventEnvelopeRef
from src.workstation.instruments import AssetIdentity, Instrument
from src.workstation.quality import ClockReading
from src.workstation.transport import PublicTransportError
from src.workstation.visuals import VisualHistory

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


def test_session_aggregation_exact_partial_coverage_and_no_fabricated_gaps():
    def trade(stamp, price, quantity, *, history=False):
        return SimpleNamespace(
            print=SimpleNamespace(
                asof=EventEnvelopeRef(
                    "recorded-session",
                    1,
                    0,
                    None,
                    stamp,
                    stamp - START,
                    "recorded-clock",
                ),
                trade=SimpleNamespace(
                    price=Decimal(price), is_subscription_history=history
                ),
                base_quantity=Decimal(quantity),
            )
        )

    history = VisualHistory(AnalyticsEngine(()), source_identity="recorded-source")
    history.clock = ClockReading("recorded-clock", 180 * SECOND)
    history._observe_receive_clock(
        EventEnvelopeRef("recorded-session", 1, 0, None, START, 0, "recorded-clock"),
        ClockReading("recorded-clock", 0),
    )
    history._observe_receive_clock(
        EventEnvelopeRef(
            "recorded-session",
            2,
            0,
            None,
            START + 180 * SECOND,
            180 * SECOND,
            "recorded-clock",
        ),
        history.clock,
    )
    assert history.clock.wall_ns is None
    history.trades.extend(
        [
            trade(START + 1, PRICE, "0.00000000000000000000000000000000000001"),
            trade(START + 120 * SECOND, "101", "0.2"),
            trade(START + 150 * SECOND, "99", "0.3"),
            trade(START + 160 * SECOND, "400", "10", history=True),
            trade(START + 181 * SECOND, "500", "10"),
        ]
    )
    with localcontext() as context:
        context.prec = 6  # Aggregation must not use the caller's small context.
        result = session_candles(history, instrument(), "1h", 300)
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
    assert (
        int(result["coverage_end_ns"]) - int(result["coverage_start_ns"]) < 180 * SECOND
    )
    assert "session_history_partial" in result["reasons"]
    minute = session_candles(history, instrument(), "1m", 300)
    assert len(minute["candles"]) == 2  # no fabricated minute 1
    limited = session_candles(history, instrument(), "1m", 1)
    assert int(limited["coverage_start_ns"]) == START + 120 * SECOND
    assert session_candles(history, instrument(), "1M", 300)["status"] == "unavailable"


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


def test_replay_utc_frontier_uses_admitted_projection_not_unapplied_pair():
    history = VisualHistory(AnalyticsEngine(()), source_identity="recorded-source")
    history.clock = ClockReading("recorded-clock", 10 * SECOND)
    reference = EventEnvelopeRef(
        "recorded-session", 1, 0, None, START, 0, "recorded-clock"
    )
    history._observe_receive_clock(reference, ClockReading("recorded-clock", 0))
    history._observe_receive_clock(
        replace(
            reference, receive_mono_ns=10 * SECOND, receive_wall_ns=START + 10 * SECOND
        ),
        history.clock,
    )
    now, scope, reasons = module.receive_utc_frontier(history)
    assert now == START + 10 * SECOND
    assert scope == ("recorded-session", "recorded-clock", 10 * SECOND)
    assert not reasons
    # This later-delivered pair did not advance the admitted monotonic frontier.
    # Its large offset must be disclosed, not adopted as the source's current UTC.
    history._observe_receive_clock(
        replace(reference, receive_mono_ns=5 * SECOND, receive_wall_ns=START + HOUR),
        history.clock,
    )
    now, _, reasons = module.receive_utc_frontier(history)
    assert now == START + 10 * SECOND
    assert "nonadvancing_receive_pair" in reasons
    history.clock = ClockReading("recorded-clock", 11 * SECOND)
    history._observe_receive_clock(
        replace(
            reference, receive_mono_ns=11 * SECOND, receive_wall_ns=START + 2 * HOUR
        ),
        history.clock,
    )
    now, _, reasons = module.receive_utc_frontier(history)
    assert now == START + 2 * HOUR
    assert "receive_clock_discontinuity" in reasons
    history.clock = ClockReading("different-clock", 11 * SECOND, START + 10 * HOUR)
    assert module.receive_utc_frontier(history)[0] is None
    history.reset()
    assert session_candles(history, instrument(), "1h", 300)["candles"] == []
