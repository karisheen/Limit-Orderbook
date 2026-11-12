"""Observed open interest and funding share the admitted candle time axis."""

import asyncio
from dataclasses import replace
from decimal import Decimal as D

import pytest

from src.workstation.analytics import AnalyticsEngine
from src.workstation.candles import session_candles
from src.workstation.events import (
    AssetContext,
    Control,
    SubscriptionKey,
    Trade,
    synthetic_envelope,
)
from src.workstation.instruments import AssetIdentity, Instrument
from src.workstation.market_presentation import AdmittedTradeHistory
from src.workstation.positioning import (
    MINUTE,
    PositioningHistory,
    positioning_series,
)
from src.workstation.recording import RecordedBatch, StreamSpec
from src.workstation.replay import ReplayClock

SECOND = 1_000_000_000
HOUR = 3600 * SECOND
START = 1700002800 * SECOND  # 2023-11-14 23:00 UTC, aligned to every interval <= 1h


def product(venue, market, base="BTC"):
    return Instrument(
        instrument_id=f"{venue}:test:{market}:{base}",
        metadata_version="positioning-fixture",
        venue=venue,
        api_family="test",
        native_symbol=base,
        base=AssetIdentity(base, base),
        quote=AssetIdentity("USD", "USD"),
        tick_size=D("0.01"),
        lot_size=D("0.001"),
        market_type=market,
        settlement=AssetIdentity("USD", "USD") if market == "perpetual" else None,
    )


class PositioningFeed:
    def __init__(self):
        self.perp = product("venue-a", "perpetual")
        self.spot = product("venue-b", "spot")
        self.other = product("venue-a", "perpetual", "ETH")
        self.streams = tuple(
            StreamSpec(
                f"{item.instrument_id}-{channel}",
                item,
                SubscriptionKey.for_instrument(item, channel=channel),
                "test-v1",
                "test-config",
            )
            for item, channels in (
                (self.perp, ("book", "trades", "context")),
                (self.spot, ("book", "trades")),
                (self.other, ("context",)),
            )
            for channel in channels
        )
        self.engine = AnalyticsEngine(self.streams)
        self.trades = AdmittedTradeHistory(self.streams, self.engine)
        self.history = PositioningHistory(self.streams, self.trades)
        self.clock = ReplayClock()
        self.delivery = 0
        self.batches = []

    def deliver(self, mono, payload, *, item=None, channel="context", clock_id="clock"):
        item = item or self.perp
        spec = next(
            spec
            for spec in self.streams
            if spec.instrument == item and spec.key.channel == channel
        )
        self.delivery += 1
        event = synthetic_envelope(
            item,
            payload,
            ordinal=self.delivery,
            mono_ns=mono,
            receive_wall_ns=START + mono,
            clock_id=clock_id,
            key=spec.key,
            stream_id=spec.stream_id,
        )
        batch = RecordedBatch(self.delivery, (event,))
        self.batches.append(batch)
        self.clock.advance(event)
        for observer in (self.engine, self.trades, self.history):
            observer.on_batch(batch, (), self.clock)

    def context(self, mono, interest=None, funding=None, *, unit="base", **options):
        self.deliver(
            mono,
            AssetContext(
                mark=D("100"),
                oracle=D("100"),
                funding_rate=None if funding is None else D(funding),
                funding_interval_ns=None if funding is None else HOUR,
                open_interest=None if interest is None else D(interest),
                open_interest_unit=None if interest is None else unit,
            ),
            **options,
        )

    def series(self, interval="1m", *, selected=None, limit=300):
        return positioning_series(self.history, selected or self.perp, interval, limit)


def test_minutes_aggregate_exactly_on_the_candle_axis_without_carry_forward():
    feed = PositioningFeed()
    feed.context(0, "100", "0.0001")
    feed.deliver(
        20 * SECOND,
        Trade("1", "1", D("100"), D("1"), quantity_unit="base"),
        channel="trades",
    )
    feed.context(30 * SECOND, "120", "0.0001")
    feed.context(45 * SECOND, "90", "0.0001")
    # A funding-only observation must not erase or invent open interest.
    feed.context(90 * SECOND, None, "-0.0002")
    # Minutes two through four are never observed.
    feed.context(310 * SECOND, "150", "0.0003")

    minutes = feed.series()
    assert minutes["status"] == "ready" and minutes["source"] == "session_context"
    (row,) = minutes["series"]
    assert row["instrument_id"] == str(feed.perp.instrument_id)
    assert row["relation"] == "selected" and row["open_interest_unit"] == "base"
    assert [int(bar["start_ns"]) - START for bar in row["bars"]] == [
        0,
        MINUTE,
        5 * MINUTE,
    ]
    first, funding_only, last = row["bars"]
    assert first["open_interest"] == {
        "open": "100",
        "high": "120",
        "low": "90",
        "close": "90",
        "change": "-10",
    }
    assert first["funding_rate"] == "0.0001" and first["samples"] == 3
    assert first["funding_interval_ns"] == str(HOUR) and first["closed"] is True
    assert funding_only["open_interest"] is None
    assert funding_only["funding_rate"] == "-0.0002"
    assert last["open_interest"]["close"] == "150" and last["closed"] is False

    five = feed.series("5m")["series"][0]["bars"]
    candle = session_candles(feed.trades, feed.perp, "5m", 300)["candles"][0]
    assert (five[0]["start_ns"], five[0]["end_ns"]) == (
        candle["start_ns"],
        candle["end_ns"],
    )
    assert five[0]["open_interest"] == first["open_interest"]
    assert five[0]["funding_rate"] == "-0.0002" and five[0]["samples"] == 4
    assert five[0]["first_ns"] == str(START)
    assert five[0]["last_ns"] == str(START + 90 * SECOND)
    assert five[1]["open_interest"]["change"] == "0"
    hour = feed.series("1h")["series"][0]["bars"]
    assert len(hour) == 1 and hour[0]["open_interest"] == {
        "open": "100",
        "high": "150",
        "low": "90",
        "close": "150",
        "change": "50",
    }
    assert [bar["start_ns"] for bar in feed.series(limit=1)["series"][0]["bars"]] == [
        str(START + 5 * MINUTE)
    ]


@pytest.mark.parametrize("interval", ["5m", "1h", "1d", "1w", "1M"])
def test_sparse_bucket_starts_at_canonical_boundary(interval):
    feed = PositioningFeed()
    feed.context(2 * MINUTE + SECOND, "100", "0.0001")
    feed.deliver(
        2 * MINUTE + 2 * SECOND,
        Trade("1", "1", D("100"), D("1"), quantity_unit="base"),
        channel="trades",
    )
    minute = feed.series()["series"][0]["bars"][0]
    (bar,) = feed.series(interval)["series"][0]["bars"]
    (candle,) = session_candles(feed.trades, feed.perp, interval, 300)["candles"]
    assert (bar["start_ns"], bar["end_ns"]) == (
        candle["start_ns"],
        candle["end_ns"],
    )
    assert bar["first_ns"] == bar["last_ns"] == str(START + 2 * MINUTE + SECOND)
    assert bar["samples"] == 1
    # Aggregating must not mutate the retained minute's own boundary.
    assert feed.series()["series"][0]["bars"][0] == minute


def test_related_underlying_is_disclosed_and_other_assets_never_leak():
    feed = PositioningFeed()
    feed.context(0, "100", "0.0001")
    spot = feed.series(selected=feed.spot)
    assert [(row["instrument_id"], row["relation"]) for row in spot["series"]] == [
        (str(feed.perp.instrument_id), "related_underlying")
    ]
    assert spot["instrument_id"] == str(feed.spot.instrument_id)
    other = feed.series(selected=feed.other)
    assert other["status"] == "empty"
    assert [(row["instrument_id"], row["bars"]) for row in other["series"]] == [
        (str(feed.other.instrument_id), [])
    ]
    unsubscribed = feed.series(selected=product("venue-c", "perpetual", "SOL"))
    assert unsubscribed["series"] == [] and unsubscribed["status"] == "empty"
    assert "context_channel_not_subscribed" in unsubscribed["reasons"]
    assert feed.series("7m")["status"] == "unavailable"


def test_boundaries_keep_observed_bars_but_unit_and_clock_changes_clear_them():
    feed = PositioningFeed()
    feed.deliver(0, Control("source_change", "ws_connect"))
    feed.context(SECOND, "100", "0.0001")
    assert "context_discontinuities" not in feed.series()["reasons"]
    feed.deliver(2 * SECOND, Control("disconnect", "test_disconnect"))
    feed.context(3 * SECOND, "110", "0.0001")
    kept = feed.series()
    assert "context_discontinuities" in kept["reasons"]
    assert kept["series"][0]["bars"][0]["open_interest"]["open"] == "100"

    feed.context(70 * SECOND, "5000", "0.0001", unit="quote")
    changed = feed.series()
    assert "open_interest_unit_changed" in changed["reasons"]
    assert changed["series"][0]["open_interest_unit"] == "quote"
    assert [bar["open_interest"]["open"] for bar in changed["series"][0]["bars"]] == [
        "5000"
    ]

    feed.context(SECOND, "7", "0.0002", unit="quote", clock_id="replacement")
    replaced = feed.series()
    assert "source_clock_changed" in replaced["reasons"]
    assert [
        (bar["start_ns"], bar["open_interest"]["close"])
        for bar in replaced["series"][0]["bars"]
    ] == [(str(START), "7")]


@pytest.mark.parametrize("late_stamp", [30 * SECOND, 70 * SECOND])
def test_late_unit_change_cannot_discard_observed_history(late_stamp):
    feed = PositioningFeed()
    feed.context(0, "100", "0.0001")
    feed.context(90 * SECOND, "110", "-0.0001")
    expected = feed.series()["series"]
    feed.context(late_stamp, "9999", "0.5", unit="quote")
    result = feed.series()
    assert result["series"] == expected
    assert "nonadvancing_context_clock" in result["reasons"]
    assert "open_interest_unit_changed" not in result["reasons"]


def test_noncontext_clock_transition_cannot_resurrect_old_context():
    feed = PositioningFeed()
    feed.context(0, "100", "0.0001")
    feed.deliver(
        SECOND,
        Control("heartbeat"),
        channel="trades",
        clock_id="replacement",
    )
    assert feed.series()["series"][0]["bars"] == []
    feed.deliver(2 * SECOND, Control("heartbeat"), channel="trades")
    assert feed.series()["series"][0]["bars"] == []
    feed.context(3 * SECOND, "120", "-0.0001")
    (bar,) = feed.series()["series"][0]["bars"]
    assert bar["samples"] == 1
    assert bar["open_interest"]["open"] == "120"
    assert "source_clock_changed" in feed.series()["reasons"]


def test_live_owner_delivers_context_and_trades_through_one_clock(tmp_path):
    from src.workstation.api import CandleRequest, PositioningRequest
    from src.workstation.runtime import ClientSession, TerminalRuntime

    feed = PositioningFeed()
    feed.context(0, "100", "0.0001")
    feed.deliver(
        20 * SECOND,
        Trade("1", "1", D("100"), D("1"), quantity_unit="base"),
        channel="trades",
    )
    feed.context(70 * SECOND, "130", "-0.0001")

    async def scenario():
        runtime = TerminalRuntime(state_dir=tmp_path)
        session = ClientSession(runtime, "live", "live")
        runtime.sessions[session.id] = session
        try:
            await session.work(
                session._initialize_live,
                feed.streams,
                str(feed.perp.instrument_id),
                (),
                "clock",
            )
            session.live_clock = ReplayClock()
            session.status = "ready"
            for batch in feed.batches:
                await session.work(session._apply_live, batch.events, (), 1000)
            identity = dict(session_id=session.id, generation="1", interval="1m")
            live = await session.work(
                session._positioning_query, PositioningRequest(**identity)
            )
            assert live["series"] == feed.series()["series"]
            assert live["status"] == "ready"
            candles = await runtime.candles(CandleRequest(**identity))
            assert candles["fetched_at_ns"] == live["fetched_at_ns"]
            assert (
                candles["candles"][0]["start_ns"]
                == (live["series"][0]["bars"][0]["start_ns"])
            )
            # Reselection replaces the owner's histories while deliveries continue.
            await session.work(
                session._initialize_live,
                feed.streams,
                str(feed.perp.instrument_id),
                (),
                "clock",
            )
            session.live_clock = ReplayClock()
            await session.work(session._apply_live, feed.batches[-1].events, (), 1000)
            (row,) = (
                await session.work(
                    session._positioning_query, PositioningRequest(**identity)
                )
            )["series"]
            assert [bar["open_interest"]["close"] for bar in row["bars"]] == ["130"]
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_checkpoint_restores_exact_series_and_rejects_foreign_or_invalid_state():
    feed = PositioningFeed()
    feed.context(0, "100.500", "0.0001")
    feed.context(61 * SECOND, "99.25", "-0.00005")
    expected = feed.series("5m")
    saved = feed.history.checkpoint()
    feed.history.reset()
    assert feed.series("5m")["series"][0]["bars"] == []
    feed.history.restore(saved)
    assert feed.series("5m") == expected
    assert feed.history.checkpoint() == saved

    foreign = PositioningHistory(feed.streams[:3], feed.trades)
    with pytest.raises(ValueError, match="incompatible positioning checkpoint"):
        foreign.restore(saved)
    key = next(iter(feed.history.bars))
    start, bar = next(iter(feed.history.bars[key].items()))
    feed.history.bars[key][start] = replace(bar, open_interest_low=D("101"))
    with pytest.raises(ValueError, match="invalid positioning checkpoint bars"):
        feed.history.restore(feed.history.checkpoint())


def test_replay_api_rebuilds_positioning_prefix_without_future_context(tmp_path):
    from httpx import ASGITransport, AsyncClient

    from src.workstation.api import create_app
    from src.workstation.demo_memory import MemoryReplaySource
    from src.workstation.runtime import ClientSession, TerminalRuntime

    async def scenario():
        runtime = TerminalRuntime(state_dir=tmp_path)
        session = ClientSession(runtime, "demo", "fixture")
        runtime.sessions[session.id] = session
        try:
            await session.work(session._initialize_replay, MemoryReplaySource.demo())
            perpetual = next(
                spec.instrument
                for spec in session.streams
                if spec.key.channel == "book"
                and spec.instrument.venue == "hyperliquid"
                and spec.instrument.market_type == "perpetual"
                and spec.instrument.base.ticker == "BTC"
            )
            await session.work(session.choose, str(perpetual.instrument_id))
            async with AsyncClient(
                transport=ASGITransport(app=create_app(runtime)),
                base_url="http://127.0.0.1",
            ) as client:

                async def query(interval="1m"):
                    response = await client.post(
                        "/api/v1/positioning",
                        json={
                            "session_id": session.id,
                            "generation": str(session.generation),
                            "interval": interval,
                        },
                    )
                    assert response.status_code == 200, response.text
                    return response.json()

                await session.work(session.replay.seek_delivery, 1200)
                prefix = await query()
                (row,) = prefix["series"]
                assert row["instrument_id"] == str(perpetual.instrument_id)
                assert row["relation"] == "selected" and row["bars"]
                assert prefix["source_identity"] == session.source_identity
                candles = await client.post(
                    "/api/v1/candles",
                    json={
                        "session_id": session.id,
                        "generation": str(session.generation),
                        "interval": "1m",
                    },
                )
                starts = {bar["start_ns"] for bar in candles.json()["candles"]}
                assert starts and starts <= {bar["start_ns"] for bar in row["bars"]}
                assert prefix["fetched_at_ns"] == candles.json()["fetched_at_ns"]

                await session.work(session.replay.seek_delivery, 3000)
                later = (await query())["series"][0]["bars"]
                assert int(later[-1]["last_ns"]) > int(row["bars"][-1]["last_ns"])
                interest = [D(bar["open_interest"]["close"]) for bar in later]
                assert len(set(interest)) > 1
                assert {bar["funding_interval_ns"] for bar in later} == {str(HOUR)}

                await session.work(session.replay.seek_delivery, 1200)
                assert (await query())["series"] == prefix["series"]
                await session.work(session.replay.seek_delivery, 0)
                empty = await query()
                assert empty["status"] == "unavailable"
                assert [item["bars"] for item in empty["series"]] == [[]]
                stale = await client.post(
                    "/api/v1/positioning",
                    json={
                        "session_id": session.id,
                        "generation": str(session.generation + 1),
                        "interval": "1m",
                    },
                )
                assert stale.status_code == 409
        finally:
            await runtime.close()

    asyncio.run(scenario())
