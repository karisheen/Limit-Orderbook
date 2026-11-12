"""Ordered native trade continuity and exact contract valuation regressions."""

import asyncio
from dataclasses import replace
from decimal import Decimal, localcontext

import pytest

from src.workstation.analytics import AnalyticsEngine, _valuation
from src.workstation.api import MarketDataRequest
from src.workstation.events import (
    Control,
    Gap,
    SubscriptionKey,
    Trade,
    synthetic_envelope,
)
from src.workstation.instruments import AssetIdentity, Instrument
from src.workstation.market_presentation import AdmittedTradeHistory
from src.workstation.precision import DECIMAL_CONTEXT
from src.workstation.recording import RecordedBatch, StreamSpec
from src.workstation.runtime import ClientSession, TerminalRuntime


@pytest.fixture
def instrument():
    return Instrument(
        "test:BTCUSD",
        "1",
        "test",
        "public",
        "BTCUSD",
        AssetIdentity("BTC", "BTC"),
        AssetIdentity("USD", "USD"),
        Decimal("1"),
        Decimal("1"),
    )


def session_for(tmp_path, instrument, *peers):
    runtime = TerminalRuntime(state_dir=tmp_path)
    session = ClientSession(runtime, "live", "live")
    runtime.sessions[session.id] = session
    session.status = "live"
    session.streams = tuple(
        StreamSpec(
            f"{item.instrument_id}:{channel}",
            item,
            SubscriptionKey.for_instrument(item, channel=channel),
            "test",
            "test",
        )
        for item in (instrument, *peers)
        for channel in ("book", "trades")
    )
    session.engine = AnalyticsEngine(session.streams)
    session.trade_history = AdmittedTradeHistory(session.streams, session.engine)
    session.choose(str(instrument.instrument_id))
    return runtime, session


def event_for(session, payload, *, index=0, stream=None, **kwargs):
    spec = next(
        spec
        for spec in session.streams
        if spec.stream_id == (stream or f"{session.instrument_id}:trades")
    )
    kwargs.setdefault("ordinal", session.delivery + 1)
    return synthetic_envelope(
        spec.instrument,
        payload,
        stream_id=spec.stream_id,
        key=spec.key,
        event_index=index,
        **kwargs,
    )


def buy(key="buy", *, quantity_unit="base"):
    return Trade(key, key, Decimal("30000"), Decimal("1"), quantity_unit, "buy")


async def deliver(session, *events):
    def apply():
        batch = RecordedBatch(session.delivery + 1, events)
        session.live_clock.advance(events[0])
        session.engine.on_batch(batch, (), session.live_clock)
        session.trade_history.on_batch(batch, (), session.live_clock)
        session.history.append(batch)
        session.delivery = batch.delivery_index

    await session.work(apply)


def request(session, cursor=None, *, limit=1):
    cursor = cursor or {"delivery": "0", "event": -1}
    return MarketDataRequest(
        session_id=session.id,
        generation=str(session.generation),
        after_delivery=cursor["delivery"],
        after_event=cursor["event"],
        limit=limit,
    )


def test_buy_gap_buy_pages_preserve_order_without_retention_gap(tmp_path, instrument):
    async def scenario():
        runtime, session = session_for(tmp_path, instrument)
        try:
            await deliver(session, event_for(session, buy("before")))
            first = await runtime.market_data(request(session))
            assert not first["has_more"]
            boundary_event = event_for(session, Gap("missing_trades"))
            await deliver(
                session,
                boundary_event,
                event_for(session, buy("after"), index=1),
            )
            second = await runtime.market_data(request(session, first["cursor"]))
            third = await runtime.market_data(request(session, second["cursor"]))
            assert first["boundaries"] == third["boundaries"] == []
            assert second["trades"] == []
            assert first["trades"][0]["trade"]["trade_id"] == "before"
            assert third["trades"][0]["trade"]["trade_id"] == "after"
            (boundary,) = second["boundaries"]
            assert boundary["instrument_id"] == str(instrument.instrument_id)
            assert boundary["stream_id"] == str(boundary_event.stream_id)
            assert boundary["kind"] == "gap"
            assert boundary["reason"] == "missing_trades"
            assert boundary["asof"]["event_index"] == "0"
            assert boundary["cursor"] == second["cursor"]
            assert [page["cursor"] for page in (first, second, third)] == [
                {"delivery": "1", "event": 0},
                {"delivery": "2", "event": 0},
                {"delivery": "2", "event": 1},
            ]
            assert all(not page["gap"] for page in (first, second, third))
            assert second["has_more"] and not third["has_more"]
            assert second["retention"]["stream_floors"] == {
                str(boundary_event.stream_id): second["cursor"]
            }
            drained = await runtime.market_data(request(session, third["cursor"]))
            assert drained["trades"] == drained["boundaries"] == []
            # A retry is stateless and returns the same discontinuity, not a lost cache hit.
            retried = await runtime.market_data(request(session, first["cursor"]))
            assert retried["boundaries"] == second["boundaries"]
            assert retried["trades"] == second["trades"]
            assert retried["cursor"] == second["cursor"]
            # Raw deliveries still contain "before"; admission retirement wins.
            restarted = await runtime.market_data(request(session, limit=10))
            assert [item["trade"]["trade_id"] for item in restarted["trades"]] == [
                "after"
            ]
            assert restarted["boundaries"] == second["boundaries"]
        finally:
            await runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["reset", "disconnect", "source_change", "rejection"])
def test_invalidating_controls_are_boundaries_but_heartbeats_are_not(
    tmp_path, instrument, kind
):
    async def scenario():
        runtime, session = session_for(tmp_path, instrument)
        await deliver(
            session,
            event_for(session, buy("before")),
            event_for(session, Control("heartbeat"), index=1),
            event_for(session, Control(kind, "native_reason"), index=2),
            event_for(session, Control("ack"), index=3),
            event_for(session, buy(), index=4),
        )
        try:
            page = await runtime.market_data(request(session, limit=10))
            assert [
                (item["kind"], item["reason"], item["cursor"]["event"])
                for item in page["boundaries"]
            ] == [(kind, "native_reason", 2)]
            assert [item["cursor"]["event"] for item in page["trades"]] == [4]
            assert page["cursor"] == {"delivery": "1", "event": 4}
            assert page["retention"]["stream_floors"] == {
                f"{instrument.instrument_id}:trades": {"delivery": "1", "event": 2}
            }
            assert not page["gap"]
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_heartbeats_and_acknowledgements_do_not_retire_prints(tmp_path, instrument):
    async def scenario():
        runtime, session = session_for(tmp_path, instrument)
        try:
            await deliver(session, event_for(session, buy("before")))
            first = await runtime.market_data(request(session))
            await deliver(
                session,
                event_for(session, Control("heartbeat")),
                event_for(session, Control("ack"), index=1),
            )
            drained = await runtime.market_data(request(session, first["cursor"]))
            assert drained["trades"] == drained["boundaries"] == []
            assert drained["cursor"] == {"delivery": "2", "event": 1}
            assert drained["retention"]["stream_floors"] == {}
            assert not drained["gap"]
            restarted = await runtime.market_data(request(session, limit=10))
            assert restarted["trades"] == first["trades"]
            assert restarted["boundaries"] == []
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_gap_fanout_is_atomic_and_only_targets_selected_market_cohort(
    tmp_path, instrument
):
    async def scenario():
        peer = replace(instrument, instrument_id="peer:BTCUSD", venue="peer")
        perp = replace(
            instrument, instrument_id="test:BTC-PERP", market_type="perpetual"
        )
        other = replace(
            instrument, instrument_id="test:ETHUSD", base=AssetIdentity("ETH", "ETH")
        )
        unaffected = replace(instrument, instrument_id="third:BTCUSD", venue="third")
        runtime, session = session_for(
            tmp_path, instrument, peer, perp, other, unaffected
        )
        targets = tuple(
            spec.stream_id for spec in session.streams if spec.instrument != unaffected
        )
        book_stream = f"{instrument.instrument_id}:book"
        try:
            await deliver(
                session,
                event_for(session, buy("before")),
                event_for(
                    session,
                    buy("unrelated"),
                    index=1,
                    stream=f"{unaffected.instrument_id}:trades",
                ),
            )
            first = await runtime.market_data(request(session))
            assert first["trades"][0]["trade"]["trade_id"] == "before"
            await deliver(
                session,
                event_for(
                    session,
                    Gap("fanout", affected_streams=targets),
                    stream=book_stream,
                ),
                event_for(session, buy("after"), index=1),
            )
            # One unrelated print leaves too little space for the fan-out pair.
            unrelated = await runtime.market_data(
                request(session, first["cursor"], limit=2)
            )
            assert [item["trade"]["trade_id"] for item in unrelated["trades"]] == [
                "unrelated"
            ]
            assert unrelated["boundaries"] == []
            assert unrelated["cursor"] == {"delivery": "1", "event": 1}
            assert unrelated["has_more"]
            second = await runtime.market_data(request(session, unrelated["cursor"]))
            assert first["boundaries"] == []
            assert second["trades"] == []
            assert {item["stream_id"] for item in second["boundaries"]} == {
                f"{instrument.instrument_id}:trades",
                f"{peer.instrument_id}:trades",
            }
            assert all(
                item["cursor"] == second["cursor"] for item in second["boundaries"]
            )
            assert second["cursor"] == {"delivery": "2", "event": 0}
            assert second["has_more"]
            third = await runtime.market_data(request(session, second["cursor"]))
            assert third["boundaries"] == []
            assert third["trades"][0]["trade"]["trade_id"] == "after"
            assert {item["instrument_id"] for item in third["instruments"]} == {
                str(instrument.instrument_id),
                str(peer.instrument_id),
                str(unaffected.instrument_id),
            }
            assert (
                f"{unaffected.instrument_id}:trades"
                not in second["retention"]["stream_floors"]
            )
            restarted = await runtime.market_data(request(session, limit=10))
            assert [item["trade"]["trade_id"] for item in restarted["trades"]] == [
                "unrelated",
                "after",
            ]
            assert restarted["boundaries"] == second["boundaries"]
            assert not restarted["gap"]
        finally:
            await runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "field,value",
    [
        ("session_id", "next-session"),
        ("segment_id", "next-segment"),
        ("clock_id", "next-clock"),
        ("connection_epoch", 1),
    ],
)
def test_source_identity_survives_cursor_pages(tmp_path, instrument, field, value):
    async def scenario():
        runtime, session = session_for(tmp_path, instrument)
        original = event_for(session, buy("before"))
        try:
            await deliver(session, original)
            first = await runtime.market_data(request(session))
            changed = replace(event_for(session, buy("after")), **{field: value})
            await deliver(session, changed)
            second = await runtime.market_data(request(session, first["cursor"]))
            before = first["trades"][0]["source_identity"]
            after = second["trades"][0]["source_identity"]
            assert before == {
                "session_id": str(original.session_id),
                "segment_id": str(original.segment_id),
                "clock_id": original.clock_id,
                "connection_epoch": str(original.connection_epoch),
            }
            assert after == {**before, field: str(value)}
            assert first["boundaries"] == second["boundaries"] == []
            clock_changed = field in ("session_id", "clock_id")
            assert second["gap"] is clock_changed
            assert second["retention"]["stream_floors"] == {
                str(original.stream_id): {"delivery": "2", "event": -1}
            }
            assert second["retention"]["floor"] == (
                {"delivery": "2", "event": -1}
                if clock_changed
                else {"delivery": "0", "event": -1}
            )
            restarted = await runtime.market_data(request(session, limit=10))
            assert [item["trade"]["trade_id"] for item in restarted["trades"]] == [
                "after"
            ]
            assert restarted["trades"][0]["source_identity"] == after
            assert restarted["gap"] is clock_changed
        finally:
            await runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "metadata_policy", ("explicit", "missing_source", "wrong_currency")
)
def test_inverse_quote_keeps_exact_contract_product_and_metadata_policy(
    tmp_path, instrument, metadata_policy
):
    inverse = replace(
        instrument,
        market_type="perpetual",
        quantity_unit="contracts",
        inverse=True,
        contract_multiplier=Decimal("10"),
        contract_value_currency=instrument.quote.asset_id,
        contract_specification_source="explicit native contract specification",
        reference_multiplier=Decimal("999"),
    )
    with localcontext(DECIMAL_CONTEXT):
        expected_base = Decimal("10") / Decimal("30000")
        assert _valuation(inverse, Decimal("30000"), Decimal("1")) == (
            expected_base,
            Decimal("10"),
        )
        assert _valuation(
            replace(inverse, contract_specification_source=None),
            Decimal("30000"),
            Decimal("1"),
        ) == (None, None)
        assert _valuation(
            replace(inverse, contract_value_currency=inverse.base.asset_id),
            Decimal("30000"),
            Decimal("1"),
        ) == (None, None)

    if metadata_policy == "missing_source":
        inverse = replace(inverse, contract_specification_source=None)
    elif metadata_policy == "wrong_currency":
        inverse = replace(inverse, contract_value_currency=inverse.base.asset_id)

    async def scenario():
        runtime, session = session_for(tmp_path, inverse)
        await deliver(session, event_for(session, buy(quantity_unit="contracts")))
        try:
            page = await runtime.market_data(request(session))
            (trade,) = page["trades"]
            if metadata_policy == "explicit":
                assert trade["quote_notional"] == "10"
                assert Decimal(trade["base_quantity"]) == expected_base
            else:
                assert trade["quote_notional"] is None
                assert trade["base_quantity"] is None
            assert trade["trade"]["quantity"] == "1"
            assert trade["trade"]["quantity_unit"] == "contracts"
            assert (
                page["instruments"][0]["metadata_version"] == inverse.metadata_version
            )
            assert (
                page["instruments"][0]["contract_specification_source"]
                == inverse.contract_specification_source
            )
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_boundary_only_history_obeys_page_limits(tmp_path, instrument):
    async def scenario():
        runtime, session = session_for(tmp_path, instrument)
        await deliver(
            session,
            *(
                event_for(session, Gap(f"gap-{index}"), index=index)
                for index in range(7)
            ),
        )
        try:
            cursor = None
            seen = []
            while True:
                page = await runtime.market_data(request(session, cursor, limit=2))
                assert page["trades"] == []
                assert len(page["boundaries"]) <= 2
                seen.extend(item["cursor"]["event"] for item in page["boundaries"])
                cursor = page["cursor"]
                if not page["has_more"]:
                    break
            assert seen == list(range(7))
            assert cursor == {"delivery": "1", "event": 6}
        finally:
            await runtime.close()

    asyncio.run(scenario())
