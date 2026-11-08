"""Consumer-level isolated accounting and durable source-time regression cases."""

from dataclasses import replace
from decimal import Decimal

import pytest

from src.workstation.costs import FeeSchedule, InventoryBalance
from src.workstation.instruments import AssetIdentity, synthetic_instrument
from src.workstation.paper import (
    PaperConfig,
    PaperEngine,
    PaperError,
    PaperFee,
    PaperOrderRequest,
    PaperStore,
    PaperRiskModel,
    PaperFundingModel,
    PaperSnapshot,
    PaperCheckpoint,
)
from src.workstation.perpetual import PaperFundingEvent
from src.workstation.precision import DERIVED_DECIMAL_POLICY
from src.workstation.quality import ClockReading, EligibilityPolicy
from src.workstation.wire import to_wire, from_wire, loads
from test_workstation_paper import observation, deliver

D = Decimal
FREE = FeeSchedule(D(0), provenance="test explicit fee assumption", asof_wall_ns=1)


def instrument(kind="linear"):
    base = synthetic_instrument(
        market_type="perpetual",
        instrument_id="synthetic:perpetual:BTC-USD",
        settlement_conversion="none",
    )
    if kind == "inverse":
        return replace(
            base,
            settlement=base.base,
            inverse=True,
            quantity_unit="contracts",
            contract_multiplier=D(100),
            contract_value_currency=base.quote.asset_id,
            lot_size=D(1),
        )
    if kind == "quanto":
        return replace(
            base,
            venue="hyperliquid",
            instrument_id="hyperliquid:perpetual:BTC",
            dex_namespace="default",
            settlement=AssetIdentity("USDC", "USDC"),
            settlement_conversion="quanto_no_fx",
            contract_specification_source="https://hyperliquid.gitbook.io/hyperliquid-docs/trading/contract-specifications",
        )
    if kind == "contracts":
        return replace(
            base,
            settlement=base.quote,
            quantity_unit="contracts",
            contract_multiplier=D("0.1"),
            contract_value_currency=base.base.asset_id,
            lot_size=D(1),
        )
    return replace(base, settlement=base.quote)


def config(i, *, cash="1000", leverage="10", fee="0", funding=None, **kwargs):
    return PaperConfig(
        (InventoryBalance(i.venue, i.settlement.asset_id, D(cash)),),
        (PaperFee(i.instrument_id, replace(FREE, rate=D(fee))),),
        risk_models=(
            PaperRiskModel(
                i.instrument_id,
                D(leverage),
                D("0.005"),
                D("0.01"),
                "synthetic modeled risk, not venue tiers",
                1,
            ),
        ),
        funding_models=(
            funding
            or PaperFundingModel(
                i.instrument_id, "none", "explicit synthetic no funding", 1
            ),
        ),
        policy=EligibilityPolicy(max_age_ns=10_000),
        **kwargs,
    )


def setup(
    tmp_path, kind="linear", *, cfg=None, asks=(("100", "10"),), bids=(("99", "10"),)
):
    i = instrument(kind)
    stream, book, batch = observation(i, asks=asks, bids=bids)
    store = PaperStore(tmp_path / "perpetual.sqlite")
    engine = PaperEngine((stream,), store, "synthetic:perpetual")
    at = deliver(engine, book, batch)
    engine.configure(cfg or config(i), now=at, delivery_index=1)
    return store, engine, stream, book, at


def submit(engine, i, at, quantity="1", side="buy", **kwargs):
    return engine.submit(
        PaperOrderRequest(i.instrument_id, side, D(quantity), **kwargs),
        now=at,
        delivery_index=engine.snapshot(at).delivery_index,
    )


def position(engine, at):
    return engine.snapshot(at).perpetual_positions[0]


def free(engine, at):
    return engine.snapshot(at).balances[0].available


def move(
    engine,
    i,
    *,
    mono=200,
    delivery=2,
    bid="109",
    ask="110",
    bid_size="10",
    ask_size="10",
):
    _, book, batch = observation(
        i,
        mono=mono,
        delivery=delivery,
        bids=((bid, bid_size),),
        asks=((ask, ask_size),),
    )
    return deliver(engine, book, batch)


@pytest.mark.parametrize(
    "kind,quantity,margin",
    [
        ("linear", "2", "20"),
        ("contracts", "20", "20"),
        ("quanto", "2", "20"),
        ("inverse", "2", "0.2"),
    ],
)
def test_native_open_close_settlement_and_no_spot_inventory(
    tmp_path, kind, quantity, margin
):
    store, engine, stream, _, at = setup(tmp_path, kind, asks=(("100", "100"),))
    i = stream.instrument
    order = submit(engine, i, at, quantity)
    assert order.status == "filled"
    p = position(engine, at)
    assert p.quantity == D(quantity) and p.entry_price == 100
    assert p.initial_margin == p.isolated_collateral == D(margin)
    assert free(engine, at) == 1000 - D(margin)
    assert not engine.snapshot(at).positions
    assert engine.snapshot(at).fills[0].quantity == D(quantity)
    assert engine.snapshot(at).fills[0].fee_asset_id == i.settlement.asset_id
    at = move(engine, i, bid="125", ask="126", bid_size="100")
    submit(engine, i, at, quantity, "sell", reduce_only=True)
    p = position(engine, at)
    expected = D("0.4") if kind == "inverse" else D("50")
    assert p.quantity == p.initial_margin == p.isolated_collateral == 0
    assert p.realized_pnl == expected
    assert free(engine, at) == 1000 + expected
    store.close()


@pytest.mark.parametrize("kind", ["linear", "inverse", "quanto"])
def test_short_profit_and_settlement_fees(tmp_path, kind):
    i = instrument(kind)
    store, engine, _, _, at = setup(
        tmp_path,
        kind,
        cfg=config(i, fee="0.001"),
        bids=(("100", "10"),),
        asks=(("101", "10"),),
    )
    submit(engine, i, at, "2", "sell")
    at = move(engine, i, bid="79", ask="80")
    submit(engine, i, at, "2", "buy", reduce_only=True)
    p = position(engine, at)
    profit, fees = (D("0.5"), D("0.0045")) if kind == "inverse" else (D(40), D("0.36"))
    assert p.realized_pnl == profit and p.fees_paid == fees
    assert free(engine, at) == 1000 + profit - fees
    assert p.quantity == 0
    store.close()


@pytest.mark.parametrize("kind,entry", [("linear", D("125")), ("inverse", D("120"))])
def test_arithmetic_linear_harmonic_inverse_entry(tmp_path, kind, entry):
    store, engine, stream, _, at = setup(tmp_path, kind)
    submit(engine, stream.instrument, at)
    at = move(engine, stream.instrument, bid="149", ask="150")
    submit(engine, stream.instrument, at)
    assert abs(position(engine, at).entry_price - entry) < D("1e-75")
    store.close()


def test_margin_cap_lot_rounding_and_fee_reserve(tmp_path):
    i = instrument()
    store, engine, _, _, at = setup(tmp_path, cfg=config(i, cash="15", fee="0.01"))
    order = submit(engine, i, at, "2")
    assert order.status == "partial" and order.filled_quantity == D("1.363")
    assert "insufficient_free_collateral" in order.reasons
    assert free(engine, at) == D("0.007")
    assert position(engine, at).isolated_collateral == D("13.63")
    assert position(engine, at).fees_paid == D("1.363")
    store.close()


def test_partial_close_reversal_and_reduce_only(tmp_path):
    store, engine, stream, _, at = setup(tmp_path)
    i = stream.instrument
    submit(engine, i, at, "2")
    assert submit(engine, i, at, "1", reduce_only=True).filled_quantity == 0
    submit(engine, i, at, "0.5", "sell", reduce_only=True)
    p = position(engine, at)
    assert p.quantity == D("1.5") and p.initial_margin == 15
    assert p.realized_pnl == D("-0.5")
    submit(engine, i, at, "2", "sell")
    p = position(engine, at)
    assert p.quantity == D("-0.5") and p.entry_price == 99
    assert p.realized_pnl == -2
    assert p.initial_margin == D("4.95")
    closed = submit(engine, i, at, "3", "buy", reduce_only=True)
    assert closed.filled_quantity == D("0.5")
    assert position(engine, at).quantity == 0
    assert free(engine, at) == D("997.5")
    store.close()


@pytest.mark.parametrize(
    "side,rate,expected",
    [("buy", "0.01", "1"), ("sell", "0.01", "-1.01"), ("buy", "-0.01", "-1")],
)
def test_periodic_funding_direction_timing_and_checkpoint_idempotency(
    tmp_path, side, rate, expected
):
    i = instrument()
    _, _, batch = observation(i)
    wall = batch.events[0].receive_wall_ns
    funding = PaperFundingModel(
        i.instrument_id,
        "periodic",
        "explicit test funding",
        1,
        D(rate),
        100,
        wall + 100,
    )
    store, engine, stream, book, at = setup(
        tmp_path,
        cfg=config(i, funding=funding),
        bids=(("100", "10"),),
        asks=(("101", "10"),),
    )
    submit(engine, i, at, side=side)
    before = position(engine, at).isolated_collateral
    engine.advance_time(replace(at, mono_ns=199, wall_ns=wall + 99))
    assert not engine.snapshot(at).funding_payments
    end = replace(at, mono_ns=200, wall_ns=wall + 100)
    engine.advance_time(end)
    payment = engine.snapshot(end).funding_payments[0]
    assert payment.amount == D(expected)
    assert position(engine, end).isolated_collateral == before - D(expected)
    saved = engine.checkpoint()
    reopened = PaperEngine(
        (stream,), store, engine.source_id, run_id=engine.snapshot(end).run.run_id
    )
    reopened.restore(saved)
    reopened.advance_time(end)
    assert reopened.snapshot(end) == engine.snapshot(end)
    replay = PaperEngine(
        (stream,), store, engine.source_id, run_id=engine.snapshot(end).run.run_id
    )
    _, _, initial = observation(i, bids=(("100", "10"),), asks=(("101", "10"),))
    deliver(replay, book, initial)
    replay.advance_time(end)
    assert replay.snapshot(end) == engine.snapshot(end)
    store.close()


def test_dated_inverse_funding_and_unresolved_mark_are_not_zero_payments(tmp_path):
    i = instrument("inverse")
    _, _, batch = observation(i)
    wall = batch.events[0].receive_wall_ns
    model = PaperFundingModel(
        i.instrument_id,
        "events",
        "dated synthetic funding",
        1,
        events=(
            PaperFundingEvent(wall + 100, D("0.01")),
            PaperFundingEvent(wall + 20_000, D("0.02")),
        ),
    )
    store, engine, _, _, at = setup(
        tmp_path,
        "inverse",
        cfg=config(i, funding=model),
        bids=(("100", "10"),),
        asks=(("101", "10"),),
    )
    submit(engine, i, at)
    end = replace(at, mono_ns=200, wall_ns=wall + 100)
    engine.advance_time(end)
    assert engine.snapshot(end).funding_payments[0].amount == D("0.01")
    end = replace(at, mono_ns=20_100, wall_ns=wall + 20_000)
    engine.advance_time(end)
    payment = engine.snapshot(end).funding_payments[1]
    assert payment.amount is None and payment.mark_price is None
    assert (
        "funding_mark_unavailable_payment_unresolved" in position(engine, end).reasons
    )
    store.close()


def test_funding_boundary_precedes_same_time_new_order(tmp_path):
    i = instrument()
    _, _, batch = observation(i)
    wall = batch.events[0].receive_wall_ns
    model = PaperFundingModel(
        i.instrument_id, "periodic", "boundary test", 1, D("0.01"), 100, wall + 100
    )
    store, engine, _, _, at = setup(
        tmp_path, cfg=config(i, funding=model, order_latency_ns=100)
    )
    submit(engine, i, at)
    end = replace(at, mono_ns=200, wall_ns=wall + 100)
    engine.advance_time(end)
    assert position(engine, end).quantity == 1
    assert not engine.snapshot(end).funding_payments
    engine.advance_time(replace(end, mono_ns=300, wall_ns=wall + 200))
    assert len(engine.snapshot(end).funding_payments) == 1
    store.close()


@pytest.mark.parametrize("bid_size", ["1", "2"])
@pytest.mark.parametrize("arrival_tie", [False, True])
def test_delayed_boundary_book_funds_held_quantity_before_liquidation(
    tmp_path, bid_size, arrival_tie
):
    i = instrument()
    model = PaperFundingModel(
        i.instrument_id,
        "events",
        "delayed boundary model",
        1,
        events=(PaperFundingEvent(250, D(".01")),),
    )
    store, engine, stream, initial_book, at = setup(
        tmp_path,
        cfg=config(i, funding=model, feed_latency_ns=50, order_latency_ns=50),
    )
    submit(engine, i, at, "2")
    opened = ClockReading(at.clock_id, 150, 150)
    engine.advance_time(opened)
    assert position(engine, opened).quantity == 2
    observed = move(engine, i, mono=200, bid="80", ask="81", bid_size=bid_size)
    if arrival_tie:
        submit(engine, i, observed, "2", "sell", reduce_only=True)
    before = engine.checkpoint()
    boundary = ClockReading(at.clock_id, 250, 250)
    engine.advance_time(boundary)
    expected = engine.snapshot(boundary)
    assert len(expected.funding_payments) == len(expected.liquidations) == 1
    payment = expected.funding_payments[0]
    assert payment.quantity == 2 and payment.mark_price == 80
    assert payment.amount == D("1.6")
    liquidation = expected.liquidations[0]
    assert liquidation.filled_quantity == D(bid_size)
    p = expected.perpetual_positions[0]
    assert p.quantity == 2 - D(bid_size)
    if bid_size == "1":
        assert p.isolated_collateral == D("-2.4")
        assert p.shortfall == D("22.4") and p.debt == 0
    else:
        assert p.debt == D("23.2") and p.isolated_collateral == 0
    assert free(engine, boundary) == 980
    if arrival_tie:
        assert expected.orders[-1].filled_quantity == 0
    engine.advance_time(boundary)
    assert engine.snapshot(boundary) == expected
    # Both sides of the boundary checkpoint retain exactly-once accounting.
    for saved in (before, engine.checkpoint()):
        resumed = PaperEngine(
            (stream,), store, engine.source_id, run_id=expected.run.run_id
        )
        resumed.restore(saved)
        resumed.advance_time(boundary)
        assert resumed.snapshot(boundary) == expected
    replay = PaperEngine((stream,), store, engine.source_id, run_id=expected.run.run_id)
    _, _, initial = observation(i)
    deliver(replay, initial_book, initial)
    move(replay, i, mono=200, bid="80", ask="81", bid_size=bid_size)
    replay.advance_time(boundary)
    assert replay.snapshot(boundary) == expected
    archived = store.archived_snapshot(expected.run.run_id)
    assert archived is not None
    assert "archived_not_live_projection" in archived.reasons
    assert archived.balances == expected.balances
    assert archived.perpetual_positions == expected.perpetual_positions
    assert archived.funding_payments == expected.funding_payments
    assert archived.liquidations == expected.liquidations
    store.close()


def test_partial_liquidation_retries_on_funding_and_new_native_liquidity(tmp_path):
    i = instrument()
    model = PaperFundingModel(
        i.instrument_id,
        "events",
        "residual funding model",
        1,
        events=(PaperFundingEvent(300, D(".01")),),
    )
    store, engine, _, _, at = setup(tmp_path, cfg=config(i, funding=model))
    submit(engine, i, at, "2")
    at = move(engine, i, mono=200, bid="80", ask="81", bid_size="1")
    assert len(engine.snapshot(at).liquidations) == 1
    boundary = ClockReading(at.clock_id, 300, 300)
    engine.advance_time(boundary)
    funded = engine.snapshot(boundary)
    assert funded.funding_payments[0].quantity == 1
    assert funded.funding_payments[0].amount == D(".8")
    assert len(funded.liquidations) == 2
    assert funded.liquidations[-1].filled_quantity == 0
    assert funded.liquidations[-1].reasons == ("no_unreserved_depth",)
    assert position(engine, boundary).isolated_collateral == D("-1.6")
    engine.advance_time(boundary)
    assert engine.snapshot(boundary) == funded
    at = move(engine, i, mono=400, delivery=3, bid="80", ask="81", bid_size="2")
    closed = engine.snapshot(at)
    assert len(closed.liquidations) == 3
    assert closed.liquidations[-1].filled_quantity == 1
    assert closed.perpetual_positions[0].debt == D("22.4")
    assert closed.perpetual_positions[0].quantity == 0
    assert free(engine, at) == 980
    store.close()


def test_protective_liquidation_partial_consumed_depth_residual_and_debt(tmp_path):
    store, engine, stream, _, at = setup(tmp_path)
    i = stream.instrument
    submit(engine, i, at, "2")
    at = move(engine, i, bid="80", ask="81", bid_size="1")
    snap = engine.snapshot(at)
    assert len(snap.liquidations) == 1
    assert sum(x.filled_quantity for x in snap.liquidations) == 1
    p = position(engine, at)
    assert p.quantity == 1 and p.liquidating
    assert p.isolated_collateral == D("-0.8")
    assert p.shortfall == D("20.8")
    count = len(snap.liquidations)
    engine.advance_time(at)
    assert len(engine.snapshot(at).liquidations) == count
    saved = engine.checkpoint()
    resumed = PaperEngine((stream,), store, engine.source_id, run_id=snap.run.run_id)
    resumed.restore(saved)
    resumed.advance_time(at)
    assert resumed.snapshot(at) == engine.snapshot(at)
    # A new observation of identical native depth is not replenishment.
    same = move(engine, i, mono=201, delivery=3, bid="80", ask="81", bid_size="1")
    assert len(engine.snapshot(same).liquidations) == count
    at = same
    assert submit(engine, i, at).filled_quantity == 0
    at = move(engine, i, mono=300, delivery=4, bid="79", ask="80", bid_size="1")
    p = position(engine, at)
    assert p.quantity == 0 and p.debt == D("22.59")
    assert p.liquidation_fees_paid == D("1.59")
    assert (
        free(engine, at) == 980
    )  # Isolated losses never raid remaining free collateral.
    assert submit(engine, i, at).filled_quantity == 0
    store.close()


def test_funding_breach_without_eligible_depth_retains_exposure(tmp_path):
    i = instrument()
    _, _, batch = observation(i)
    wall = batch.events[0].receive_wall_ns
    model = PaperFundingModel(
        i.instrument_id,
        "events",
        "stress funding",
        1,
        events=(PaperFundingEvent(wall + 100, D("0.2")),),
    )
    store, engine, stream, book, at = setup(
        tmp_path, cfg=config(i, funding=model), asks=(("100", "11"),)
    )
    submit(engine, i, at)
    # Consume all bids with a real close, then reopen using remaining asks.
    submit(engine, i, at, "10", "sell")
    submit(engine, i, at, "10", "buy")
    end = replace(at, mono_ns=200, wall_ns=wall + 100)
    engine.advance_time(end)
    p = position(engine, end)
    assert p.quantity == 1 and p.liquidating
    assert p.isolated_collateral < 0
    assert engine.snapshot(end).liquidations[-1].filled_quantity == 0
    assert "no_unreserved_depth" in engine.snapshot(end).liquidations[-1].reasons
    store.close()


def test_feed_delay_prevents_future_book_from_filling_arrival(tmp_path):
    i = instrument()
    store, engine, _, _, at = setup(
        tmp_path, cfg=config(i, feed_latency_ns=200, order_latency_ns=100)
    )
    submit(engine, i, at)
    at = move(engine, i, mono=200, bid="109", ask="110")
    assert engine.snapshot(at).orders[0].filled_quantity == 0
    assert not engine.snapshot(at).perpetual_positions
    store.close()


def test_unknown_risk_funding_settlement_and_future_assumptions_fail_closed(tmp_path):
    i = instrument()
    variants = [
        (replace(config(i), risk_models=()), "missing_perpetual_risk_model"),
        (replace(config(i), funding_models=()), "missing_explicit_funding_model"),
        (
            replace(
                config(i),
                risk_models=(replace(config(i).risk_models[0], asof_wall_ns=10**30),),
            ),
            "model_date_in_future",
        ),
    ]
    for index, (cfg, reason) in enumerate(variants):
        root = tmp_path / str(index)
        root.mkdir()
        store, engine, _, _, at = setup(root, cfg=cfg)
        with pytest.raises(PaperError, match=reason):
            submit(engine, i, at)
        assert not engine.snapshot(at).orders
        store.close()


def test_unsupported_quanto_and_builder_contracts_are_not_fx(tmp_path):
    from src.workstation.perpetual import economics_reason

    i = instrument("quanto")
    assert economics_reason(i) is None
    assert economics_reason(replace(i, venue="unknown"))
    assert economics_reason(replace(i, dex_namespace="builder"))
    assert economics_reason(replace(i, contract_specification_source=None))
    assert economics_reason(replace(i, settlement_conversion="unknown"))


def test_strict_legacy_shapes_and_captured_descriptor_preservation(tmp_path):
    from test_workstation_paper import setup as spot_setup

    store, engine, stream, _, at = spot_setup(tmp_path)
    submit(engine, stream.instrument, at)
    snapshot = engine.snapshot(at)
    wire = to_wire(snapshot)
    wire.pop("perpetual_positions")
    wire.pop("funding_payments")
    wire.pop("liquidations")
    wire["run"]["config"].pop("risk_models")
    wire["run"]["config"].pop("funding_models")
    for order in wire["orders"]:
        order["request"].pop("reduce_only")
    decoded = from_wire(wire, PaperSnapshot, decimal_policy=DERIVED_DECIMAL_POLICY)
    assert decoded == snapshot
    assert decoded.run.instruments == snapshot.run.instruments
    bad = dict(wire, perpetual_positions=[])
    with pytest.raises(ValueError, match="missing/unknown"):
        from_wire(bad, PaperSnapshot)
    bad = dict(wire, arbitrary=[])
    with pytest.raises(ValueError, match="missing/unknown"):
        from_wire(bad, PaperSnapshot)
    checkpoint = to_wire(
        loads(
            engine.checkpoint(), PaperCheckpoint, decimal_policy=DERIVED_DECIMAL_POLICY
        ),
        decimal_policy=DERIVED_DECIMAL_POLICY,
    )
    for name in (
        "perpetual_positions",
        "funding_payments",
        "funding_cursors",
        "liquidations",
        "frozen_reason",
    ):
        checkpoint.pop(name)
    restored = from_wire(
        checkpoint, PaperCheckpoint, decimal_policy=DERIVED_DECIMAL_POLICY
    )
    assert not restored.perpetual_positions
    store.close()


def test_reopened_archive_and_new_run_do_not_adopt_old_margin(tmp_path):
    store, engine, stream, _, at = setup(tmp_path)
    submit(engine, stream.instrument, at, "2")
    snap = engine.snapshot(at)
    old_run = snap.run.run_id
    state = engine.checkpoint()
    store.close()
    store = PaperStore(tmp_path / "perpetual.sqlite")
    archived = store.archived_snapshot(old_run)
    assert archived.perpetual_positions == snap.perpetual_positions
    assert archived.run.config.risk_models == snap.run.config.risk_models
    resumed = PaperEngine((stream,), store, engine.source_id, run_id=old_run)
    resumed.restore(state)
    assert resumed.snapshot(at) == snap
    new_id = resumed.configure(
        config(stream.instrument, cash="500"), now=at, delivery_index=1
    )
    fresh = resumed.snapshot(at)
    assert new_id != old_run and fresh.run.parent_run_id == old_run
    assert not fresh.perpetual_positions and free(resumed, at) == 500
    assert (
        store.archived_snapshot(old_run).perpetual_positions == snap.perpetual_positions
    )
    store.close()


def test_native_minimum_atomicity_and_failed_candidate_does_not_consume_depth(tmp_path):
    from src.workstation.instruments import OrderConstraints

    i = instrument()
    i = replace(
        i,
        constraints=OrderConstraints(
            min_notional=D("150"), min_notional_currency=i.quote.asset_id
        ),
    )
    stream, book, batch = observation(i, asks=(("100", "1"),), bids=(("99", "1"),))
    store = PaperStore(tmp_path / "bounded.sqlite")
    engine = PaperEngine((stream,), store, "synthetic:minimum")
    at = deliver(engine, book, batch)
    engine.configure(config(i), now=at, delivery_index=1)
    order = submit(engine, i, at, "2")
    assert order.filled_quantity == 0 and "below_order_minimum" in order.reasons
    assert free(engine, at) == 1000 and not engine.snapshot(at).perpetual_positions
    assert not loads(
        engine.checkpoint(), PaperCheckpoint, decimal_policy=DERIVED_DECIMAL_POLICY
    ).reservations
    store.close()


@pytest.mark.parametrize(
    "rate,remaining_collateral,released",
    [("0.01", "9.01", "19.01"), ("-0.01", "10.99", "20.99")],
)
def test_partial_close_releases_proportional_funded_collateral(
    tmp_path, rate, remaining_collateral, released
):
    i = instrument()
    model = PaperFundingModel(
        i.instrument_id,
        "events",
        "funded close model",
        1,
        events=(PaperFundingEvent(200, D(rate)),),
    )
    store, engine, _, _, at = setup(tmp_path, cfg=config(i, funding=model))
    submit(engine, i, at, "2")
    engine.advance_time(ClockReading(at.clock_id, 200, 200))
    at = move(engine, i, mono=300, bid="110", ask="111")
    before = free(engine, at)
    submit(engine, i, at, "1", "sell", reduce_only=True)
    p = position(engine, at)
    assert p.quantity == 1 and p.initial_margin == 10
    assert p.isolated_collateral == D(remaining_collateral)
    assert free(engine, at) - before == D(released)
    store.close()


def test_documented_parent_smoke_exact_funding_fees_and_close(tmp_path):
    i = instrument()
    model = PaperFundingModel(
        i.instrument_id,
        "events",
        "explicit parent smoke funding",
        1,
        events=(PaperFundingEvent(200, D(".001")),),
    )
    store, engine, _, _, at = setup(tmp_path, cfg=config(i, fee=".001", funding=model))
    submit(engine, i, at, "2")
    engine.advance_time(ClockReading(at.clock_id, 200, 200))
    assert engine.snapshot(at).funding_payments[0].amount == D(".198")
    at = move(engine, i, mono=300, bid="110", ask="111")
    submit(engine, i, at, "2", "sell", reduce_only=True)
    assert free(engine, at) == D("1019.382")
    store.close()


def test_periodic_catchup_preflight_is_bounded_and_atomic(tmp_path):
    i = instrument()
    model = PaperFundingModel(
        i.instrument_id, "periodic", "small interval stress model", 1, D(".001"), 1, 101
    )
    store, engine, _, _, at = setup(tmp_path, cfg=config(i, funding=model))
    submit(engine, i, at)
    before = engine.checkpoint()
    with pytest.raises(PaperError, match="funding_catchup_limit"):
        engine.advance_time(ClockReading(at.clock_id, 10**18, 10**18))
    assert engine.checkpoint() == before
    assert not engine.snapshot(at).funding_payments
    revision = engine.snapshot(at).run.revision
    with pytest.raises(PaperError, match="funding_catchup_limit"):
        submit(
            engine,
            i,
            ClockReading(at.clock_id, 10**18, 10**18),
            side="sell",
            reduce_only=True,
        )
    assert engine.snapshot(at).run.revision == revision
    store.close()


@pytest.mark.parametrize("side,bid,ask", [("buy", "80", "81"), ("sell", "124", "125")])
def test_inverse_protective_liquidation_retains_base_settled_debt(
    tmp_path, side, bid, ask
):
    store, engine, stream, _, at = setup(
        tmp_path, "inverse", bids=(("100", "10"),), asks=(("100.01", "10"),)
    )
    i = stream.instrument
    submit(engine, i, at, "2", side)
    at = move(engine, i, bid=bid, ask=ask)
    p = position(engine, at)
    assert p.quantity == 0 and p.debt > 0
    assert p.settlement_asset_id == i.base.asset_id
    assert engine.snapshot(at).liquidations[-1].filled_quantity == 2
    assert p.liquidation_fees_paid > 0
    debt = p.debt
    engine.advance_time(at)
    assert position(engine, at).debt == debt
    store.close()


def test_real_default_hyperliquid_descriptor_uses_native_token_settlement(tmp_path):
    from test_workstation_hyperliquid import instruments

    i = next(item for item in instruments() if item.native_symbol == "BTC")
    stream, book, batch = observation(
        i, mono=200, asks=(("100", "2"),), bids=(("99", "2"),)
    )
    store = PaperStore(tmp_path / "native-hl.sqlite")
    engine = PaperEngine((stream,), store, "synthetic:documented-hl")
    at = deliver(engine, book, batch)
    engine.configure(config(i), now=at, delivery_index=1)
    assert submit(engine, i, at).filled_quantity == 1
    p = position(engine, at)
    assert i.quote.asset_id == "USDT"
    assert p.settlement_asset_id == "hyperliquid:hypercore:token:0xusdc"
    assert p.initial_margin == 10 and free(engine, at) == 990
    assert engine.snapshot(at).fills[0].fee_asset_id == p.settlement_asset_id
    store.close()


def test_legacy_database_payloads_resume_and_archive_without_descriptor_rewrite(
    tmp_path,
):
    import json
    import sqlite3
    from test_workstation_paper import setup as spot_setup

    store, engine, stream, book, at = spot_setup(tmp_path)
    submit(engine, stream.instrument, at)
    expected = engine.snapshot(at)
    checkpoint = engine.checkpoint()
    store.close()

    additions = {
        "PaperConfig": ("risk_models", "funding_models"),
        "PaperOrderRequest": ("reduce_only",),
        "PaperSnapshot": ("perpetual_positions", "funding_payments", "liquidations"),
        "PaperCheckpoint": (
            "perpetual_positions",
            "funding_payments",
            "funding_cursors",
            "liquidations",
            "frozen_reason",
        ),
    }

    def legacy(payload):
        def visit(value):
            if isinstance(value, dict):
                for key in additions.get(value.get("$type"), ()):
                    value.pop(key)
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        value = json.loads(payload)
        visit(value)
        return json.dumps(value).encode()

    with sqlite3.connect(tmp_path / "paper.sqlite") as db:
        for table in ("runs", "commands", "archives"):
            for rowid, payload in db.execute(
                f"SELECT rowid,payload FROM {table}"
            ).fetchall():
                db.execute(
                    f"UPDATE {table} SET payload=? WHERE rowid=?",
                    (legacy(payload), rowid),
                )
    store = PaperStore(tmp_path / "paper.sqlite")
    resumed = PaperEngine(
        (stream,), store, expected.run.source_id, run_id=expected.run.run_id
    )
    resumed.restore(legacy(checkpoint))
    assert resumed.snapshot(at) == expected
    archived = store.archived_snapshot(expected.run.run_id)
    assert archived.fills == expected.fills
    assert archived.run.instruments == expected.run.instruments
    assert not archived.perpetual_positions
    store.close()


def test_catchup_batch_is_atomic_and_freeze_requires_explicit_fresh_run(tmp_path):
    i = instrument()
    model = PaperFundingModel(
        i.instrument_id, "periodic", "bounded run", 1, D(".001"), 1, 101
    )
    store, engine, stream, _, at = setup(tmp_path, cfg=config(i, funding=model))
    submit(engine, i, at)
    previous = engine.checkpoint()
    _, current_book, batch = observation(i, mono=10**18, delivery=2)
    with pytest.raises(PaperError, match="funding_catchup_limit"):
        deliver(engine, current_book, batch)
    assert engine.checkpoint() == previous
    engine.freeze()
    assert engine.frozen_reason == "funding_catchup_limit"
    with pytest.raises(AttributeError):
        engine.frozen_reason = None
    later = ClockReading(at.clock_id, 10**18, 10**18)
    frozen = engine.snapshot(later)
    assert frozen.at == at and frozen.delivery_index == 1
    assert "paper_frozen" in frozen.reasons
    assert frozen.perpetual_positions[0].quantity == 1
    with pytest.raises(PaperError, match="funding_catchup_limit"):
        submit(engine, i, at)
    reopened = PaperEngine((stream,), store, engine.source_id, run_id=frozen.run.run_id)
    reopened.restore(engine.checkpoint())
    assert reopened.frozen_reason == "funding_catchup_limit"
    assert reopened.snapshot(later) == frozen
    with pytest.raises(PaperError, match="frozen_run_requires_current_books"):
        engine.configure(config(i), now=later, delivery_index=2)
    engine.configure(config(i), now=later, delivery_index=2, books=(current_book,))
    fresh = engine.snapshot(later)
    assert fresh.run.parent_run_id == frozen.run.run_id
    assert not fresh.reasons and not fresh.perpetual_positions
    assert engine.frozen_reason is None
    assert submit(engine, i, later).filled_quantity == 1
    store.close()


def test_perpetual_orders_share_nonreplenishing_native_depth(tmp_path):
    store, engine, stream, _, at = setup(
        tmp_path, asks=(("100", "1"),), bids=(("99", "1"),)
    )
    i = stream.instrument
    submit(engine, i, at)
    submit(engine, i, at, side="sell", reduce_only=True)
    before = free(engine, at)
    assert submit(engine, i, at).filled_quantity == 0
    at = move(engine, i, bid="99", ask="100", bid_size="1", ask_size="1")
    assert submit(engine, i, at).filled_quantity == 0
    assert free(engine, at) == before and position(engine, at).quantity == 0
    at = move(
        engine, i, mono=300, delivery=3, bid="99", ask="100", bid_size="1", ask_size="2"
    )
    assert submit(engine, i, at, "2").filled_quantity == 1
    assert position(engine, at).quantity == 1
    store.close()


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize(
    "order_type,quantity,maximum",
    [
        ("market", "101", "10000"),
        ("limit", "201", "20000"),
    ],
)
def test_parsed_okx_native_constraints_apply_to_commands_and_execution(
    tmp_path, legacy, order_type, quantity, maximum
):
    import json
    from src.workstation.adapters.okx import OKXAdapter

    payload = {
        "code": "0",
        "data": [
            {
                "instType": "SWAP",
                "instId": "BTC-USDT-SWAP",
                "instFamily": "BTC-USDT",
                "ctType": "linear",
                "ctVal": "0.01",
                "ctValCcy": "BTC",
                "settleCcy": "USDT",
                "tickSz": "0.1",
                "lotSz": "1",
                "minSz": "2",
                "maxLmtSz": "20000",
                "maxMktSz": "10000",
                "state": "live",
            }
        ],
    }
    i = (
        OKXAdapter()
        .parse_metadata(json.dumps(payload).encode(), inst_type="SWAP", fetch_wall_ns=1)
        .instruments[0]
    )
    if legacy:
        i = replace(
            i,
            metadata_version=i.metadata_version.replace("okx-v2-native-size", "okx-v1"),
            constraints=replace(
                i.constraints,
                min_base=D(".02"),
                max_limit_base=D("200"),
                max_market_base=D("100"),
            ),
        )
    original = to_wire(i)
    stream, book, batch = observation(i, asks=(("100", "1"),), bids=(("99", "1000"),))
    store = PaperStore(tmp_path / "native-okx.sqlite")
    engine = PaperEngine((stream,), store, "synthetic:parsed-okx")
    at = deliver(engine, book, batch)
    engine.configure(config(i), now=at, delivery_index=1)
    options: dict[str, str | Decimal] = {"order_type": order_type}
    if order_type == "limit":
        options["limit_price"] = D("100")
    for invalid in ("1", str(D(maximum) + 1)):
        with pytest.raises(PaperError, match="native_order_quantity_constraint"):
            submit(engine, i, at, invalid, **options)
    # The request meets minSz, but the observed partial fill does not. Neither
    # collateral nor depth may be committed under the legacy base-sized minimum.
    rejected = submit(engine, i, at, "2", **options)
    assert rejected.filled_quantity == 0
    assert "below_order_minimum" in rejected.reasons
    assert not engine.snapshot(at).perpetual_positions
    assert free(engine, at) == 1000
    at = move(
        engine,
        i,
        mono=200,
        bid="99",
        ask="100",
        bid_size="1000",
        ask_size=quantity,
    )
    filled = submit(engine, i, at, quantity, **options)
    assert filled.status == "filled" and filled.filled_quantity == D(quantity)
    p = position(engine, at)
    assert p.quantity == D(quantity)
    assert p.initial_margin == D(quantity) / 10
    assert p.settlement_asset_id == i.settlement.asset_id
    assert to_wire(p.instrument) == original
    saved = engine.checkpoint()
    resumed = PaperEngine(
        (stream,),
        store,
        engine.source_id,
        run_id=engine.snapshot(at).run.run_id,
    )
    resumed.restore(saved)
    assert resumed.snapshot(at) == engine.snapshot(at)
    assert to_wire(resumed.snapshot(at).run.instruments[0]) == original
    store.close()


def test_partial_liquidation_records_eligibility_transitions_without_clock_churn(
    tmp_path,
):
    store, engine, stream, _, at = setup(tmp_path)
    i = stream.instrument
    submit(engine, i, at, "2")
    at = move(engine, i, mono=200, bid="80", ask="81", bid_size="1")
    stale = ClockReading(at.clock_id, 10_201, 10_201)
    engine.advance_time(stale)
    unavailable = engine.snapshot(stale)
    assert len(unavailable.liquidations) == 2
    assert unavailable.liquidations[-1].filled_quantity == 0
    assert unavailable.liquidations[-1].reasons
    assert unavailable.liquidations[-1].reasons != ("no_unreserved_depth",)
    engine.advance_time(ClockReading(at.clock_id, 10_202, 10_202))
    assert engine.snapshot(stale).liquidations == unavailable.liquidations
    recovered = move(
        engine, i, mono=10_300, delivery=3, bid="80", ask="81", bid_size="1"
    )
    snap = engine.snapshot(recovered)
    assert len(snap.liquidations) == 3
    assert snap.liquidations[-1].reasons == ("no_unreserved_depth",)
    engine.advance_time(recovered)
    assert engine.snapshot(recovered).liquidations == snap.liquidations
    at = move(engine, i, mono=10_400, delivery=4, bid="80", ask="81", bid_size="2")
    assert position(engine, at).quantity == 0
    assert position(engine, at).debt == D("21.6")
    store.close()
