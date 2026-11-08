"""Numerical consumer fixtures: synthetic books, never execution/live evidence."""

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal, localcontext

import pytest

from src.workstation.book import BookReducer
from src.workstation.costs import (
    ComparisonEntry,
    FeeSchedule,
    FXRate,
    InventoryBalance,
    StressAssumptions,
    best_observed,
    compare_matrix,
    compare_pair,
    convert_fx,
    walk_book,
)
from src.workstation.events import (
    BookUpdate,
    Control,
    LevelChange,
    SubscriptionKey,
    synthetic_envelope,
)
from src.workstation.instruments import (
    AssetIdentity,
    OrderConstraints,
    synthetic_instrument,
)
from src.workstation.precision import DECIMAL_CONTEXT, DERIVED_DECIMAL_POLICY
from src.workstation.quality import (
    Capabilities,
    ClockReading,
    EligibilityPolicy,
    evaluate,
)
from src.workstation.wire import dumps, loads

D = Decimal
POLICY = EligibilityPolicy(max_age_ns=1000, max_asof_skew_ns=50)
FREE = FeeSchedule(
    D("0"), provenance="synthetic explicit zero assumption", asof_wall_ns=1
)
FEE = FeeSchedule(D("0.01"), provenance="synthetic 1% assumption", asof_wall_ns=1)


def fixture(
    venue="a",
    *,
    bids=(("99", "2"), ("98", "3")),
    asks=(("100", "1"), ("102", "2")),
    mono=100,
    instrument=None,
    **metadata,
):
    instrument = instrument or synthetic_instrument(venue=venue, **metadata)
    key = SubscriptionKey.for_instrument(
        instrument, depth=max(10, len(bids), len(asks))
    )
    reducer = BookReducer(instrument, key, segment_id="synthetic-segment")
    changes = tuple(
        LevelChange(side, D(price), D(quantity))
        for side, levels in (("bid", bids), ("ask", asks))
        for price, quantity in levels
    )
    result = reducer.apply(
        (
            synthetic_envelope(
                instrument,
                BookUpdate("snapshot", changes, key.depth),
                key=key,
                mono_ns=mono,
            ),
        )
    )
    assert result.book is not None
    book = result.book
    now = ClockReading(book.asof.clock_id, 100, book.asof.receive_wall_ns)
    return instrument, book, now


def streaming_fixture(venue="a", **metadata):
    instrument = replace(
        synthetic_instrument(venue=venue, **metadata),
        metadata_version="synthetic-streaming-v1",
        metadata_status="streaming_only",
        status="unknown",
        tick_size=None,
        lot_size=None,
        constraints=OrderConstraints(
            price_rule="unconstrained", size_rule="unconstrained"
        ),
    )
    return fixture(instrument=instrument)


def walk(instrument, book, now, side="buy", **options):
    return walk_book(
        book,
        instrument,
        side,
        fees=options.pop("fees", FREE),
        policy=options.pop("policy", POLICY),
        now=now,
        **options,
    )


def pair(left, right, quantity="2", **options):
    bi, bb, now = left
    si, sb, _ = right
    return compare_pair(
        bb,
        bi,
        sb,
        si,
        base_quantity=D(quantity),
        buy_fees=options.pop("buy_fees", FREE),
        sell_fees=options.pop("sell_fees", FREE),
        policy=options.pop("policy", POLICY),
        now=options.pop("now", now),
        **options,
    )


def test_walk_uses_correct_sides_and_summed_cash_not_rounded_vwap():
    instrument, book, now = fixture()
    result = walk(instrument, book, now, base_quantity=D("3"), fees=FEE)
    assert result.filled_base_quantity == D("3")
    assert result.notional_quote == D("304")
    assert result.fee_amount == D("3.04")
    assert result.slippage_quote == D("4")
    assert [item.native_quantity for item in result.consumed_levels] == [D("1"), D("2")]
    with localcontext(DECIMAL_CONTEXT):
        assert result.vwap == D("304") / D("3")
        assert result.effective_price == D("307.04") / D("3")
    sell = walk(instrument, book, now, "sell", base_quantity=D("3"), fees=FEE)
    assert sell.notional_quote == D("296")
    assert sell.slippage_quote == D("1")
    assert sell.fee_amount == D("2.96")
    assert sell.asof == book.asof
    assert sell.coverage == book.quality.coverage


def test_partial_zero_budget_and_lot_remainder_are_explicit():
    instrument, book, now = fixture()
    partial = walk(instrument, book, now, base_quantity=D("5"))
    assert partial.filled_base_quantity == D("3")
    assert partial.unfilled_base_quantity == D("2")
    assert partial.partial and "known_depth_exhausted" in partial.reasons
    budget = walk(instrument, book, now, quote_budget=D("150"))
    assert budget.filled_base_quantity == D("1.490")
    assert budget.notional_quote == D("149.98")
    assert budget.unfilled_quote_budget == D("0.02")
    assert budget.requested_base_quantity is None
    assert budget.unfilled_base_quantity is None
    assert "quote_budget_excludes_fees" in budget.assumptions
    for size in ({"base_quantity": D("0")}, {"quote_budget": D("0")}):
        zero = walk(instrument, book, now, **size)
        assert zero.filled_base_quantity == zero.notional_quote == D("0")
        assert zero.vwap is zero.effective_price is None
        assert zero.fee_amount == D("0") and not zero.partial


def test_size_selection_is_exclusive_and_not_float_or_negative():
    instrument, book, now = fixture()
    for options in (
        {},
        {"base_quantity": D("1"), "quote_budget": D("100")},
        {"base_quantity": D("-1")},
        {"base_quantity": 1.5},
    ):
        with pytest.raises(ValueError):
            walk(instrument, book, now, **options)


def test_unknown_fee_retains_gross_but_never_zero_net():
    instrument, book, now = fixture()
    estimate = walk(instrument, book, now, base_quantity=D("1"), fees=FeeSchedule())
    assert estimate.notional_quote == D("100")
    assert estimate.fee_amount is estimate.effective_price is None
    assert "unknown_fees" in estimate.reasons
    result = pair(
        fixture(),
        fixture("b", bids=(("105", "5"),), asks=(("106", "5"),)),
        buy_fees=FeeSchedule(),
    )
    assert result.gross_reference == D("10")
    assert result.net is None and not result.eligible
    with pytest.raises(ValueError):
        FeeSchedule(D("0"))
    with pytest.raises(ValueError):
        FeeSchedule(D("1"), provenance="invalid", asof_wall_ns=1)


def test_base_fee_changes_effective_quantity_and_unknown_asset_is_unavailable():
    instrument, book, now = fixture()
    fee = FeeSchedule(
        D("0.01"), asset_id="BTC", provenance="user assumption", asof_wall_ns=1
    )
    result = walk(instrument, book, now, base_quantity=D("1"), fees=fee)
    assert result.fee_amount == D("0.01") and result.fee_quote_equivalent == D("1")
    with localcontext(DECIMAL_CONTEXT):
        assert result.effective_price == D("100") / D("0.99")
    other = walk(
        instrument, book, now, base_quantity=D("1"), fees=replace(fee, asset_id="BNB")
    )
    assert other.fee_amount is None and "unsupported_fee_asset" in other.reasons
    comparison = pair(fixture(), fixture("b"), buy_fees=fee)
    assert comparison.net is None
    assert "non_quote_fee_requires_rebalance_conversion" in comparison.reasons


def test_native_minima_and_order_type_maxima_intersect():
    constraints = OrderConstraints(
        min_base=D("0.2"),
        max_base=D("2"),
        min_notional=D("25"),
        min_notional_currency="USD",
        max_market_base=D("1.5"),
        max_limit_base=D("1"),
    )
    instrument, book, now = fixture(constraints=constraints)
    below = walk(instrument, book, now, base_quantity=D("0.2"))
    assert below.filled_base_quantity == D("0")
    assert "below_order_minimum" in below.reasons
    market = walk(instrument, book, now, base_quantity=D("3"))
    assert market.filled_native_quantity == D("1.5")
    assert market.unfilled_native_quantity == D("1.5")
    limit = walk(
        instrument,
        book,
        now,
        base_quantity=D("3"),
        order_type="limit",
        limit_price=D("102"),
    )
    assert limit.filled_native_quantity == D("1")
    with pytest.raises(ValueError):
        walk(
            instrument,
            book,
            now,
            base_quantity=D("1"),
            order_type="limit",
            limit_price=D("100.001"),
        )
    with pytest.raises(ValueError):
        walk(instrument, book, now, base_quantity=D("1"), order_type="limit")


def test_limit_price_and_notional_caps_do_not_extrapolate():
    instrument, book, now = fixture()
    limit = walk(
        instrument,
        book,
        now,
        base_quantity=D("3"),
        order_type="limit",
        limit_price=D("101"),
    )
    assert limit.filled_base_quantity == D("1")
    assert "limit_price_reached" in limit.reasons
    constraints = OrderConstraints(max_notional=D("150"), min_notional_currency="USD")
    instrument, book, now = fixture(constraints=constraints)
    capped = walk(instrument, book, now, base_quantity=D("3"))
    assert capped.notional_quote == D("149.98")
    assert "notional_order_maximum" in capped.reasons
    instrument, book, now = fixture(
        constraints=replace(constraints, min_notional_currency="USDT")
    )
    assert (
        "unknown_notional_constraint_currency"
        in walk(instrument, book, now, base_quantity=D("1")).reasons
    )


def test_reference_multiplier_never_changes_native_token_quantity():
    instrument, book, now = fixture(reference_multiplier=D("0.01"))
    result = walk(instrument, book, now, base_quantity=D("1"))
    assert result.notional_quote == D("100") and result.filled_native_quantity == D("1")
    instrument, book, now = fixture(contract_multiplier=D("10"))
    result = walk(instrument, book, now, base_quantity=D("1"))
    assert result.filled_base_quantity == D("0")
    assert "ambiguous_base_quantity_contract" in result.reasons


def test_linear_contracts_honor_native_contract_lots_and_maximum():
    instrument, book, now = fixture(
        lot_size="1",
        market_type="perpetual",
        quantity_unit="contracts",
        contract_multiplier=D("0.1"),
        contract_value_currency="BTC",
        settlement=AssetIdentity("USD", "USD"),
        settlement_conversion="none",
        constraints=OrderConstraints(max_market_base=D("2")),
    )
    result = walk(instrument, book, now, base_quantity=D("0.5"))
    assert result.filled_native_quantity == D("2")
    assert result.filled_base_quantity == D("0.2")
    assert result.notional_quote == D("20.2")
    assert result.unfilled_base_quantity == D("0.3")
    assert result.settlement_notional == D("20.2")


def inverse(venue="a", **options):
    return fixture(
        venue,
        lot_size="1",
        market_type="perpetual",
        quantity_unit="contracts",
        inverse=True,
        contract_multiplier=D("100"),
        contract_value_currency="USD",
        settlement=AssetIdentity("BTC", "BTC"),
        settlement_conversion="none",
        **options,
    )


def test_inverse_contract_cash_and_base_are_price_dependent():
    instrument, book, now = inverse(
        bids=(("190", "5"),), asks=(("200", "2"), ("400", "8"))
    )
    result = walk(instrument, book, now, base_quantity=D("2"))
    assert result.filled_native_quantity == D("6")
    assert result.filled_base_quantity == D("2")
    assert result.notional_quote == D("600") and result.vwap == D("300")
    assert result.settlement_notional == D("2")
    assert result.requested_native_quantity is result.unfilled_native_quantity is None
    budget = walk(instrument, book, now, quote_budget=D("550"))
    assert budget.filled_native_quantity == D("5")
    assert budget.filled_base_quantity == D("1.75")
    assert budget.unfilled_quote_budget == D("50")
    bad = replace(instrument, contract_value_currency="USDC")
    assert (
        "unknown_inverse_contract_value"
        in walk(bad, book, now, base_quantity=D("1")).reasons
    )


def test_inverse_same_quantity_uses_contract_lot_intersection():
    left = inverse(bids=(("190", "10"),), asks=(("200", "10"),))
    right = inverse("b", bids=(("250", "10"),), asks=(("260", "10"),))
    result = pair(left, right, "2.4")
    assert result.feasible_base_quantity == D("2")
    assert result.buy.filled_native_quantity == D("4")
    assert result.sell.filled_native_quantity == D("5")
    assert result.buy.filled_base_quantity == result.sell.filled_base_quantity
    assert not result.execution_eligible


def test_recurring_inverse_lots_do_not_lose_exact_full_contract_endpoint():
    metadata = dict(
        lot_size="1",
        market_type="perpetual",
        quantity_unit="contracts",
        inverse=True,
        contract_multiplier=D("1"),
        contract_value_currency="USD",
        settlement=AssetIdentity("BTC", "BTC"),
        settlement_conversion="none",
    )
    left = fixture(bids=(("2", "10"),), asks=(("3", "10"),), **metadata)
    right = fixture("b", bids=(("6", "10"),), asks=(("7", "10"),), **metadata)
    result = pair(left, right, "1")
    assert result.feasible_base_quantity == D("1")
    assert result.buy.filled_native_quantity == D("3")
    assert result.sell.filled_native_quantity == D("6")
    assert result.buy.notional_quote == D("3") and result.sell.notional_quote == D("6")


def test_quality_and_freshness_are_centralized_not_diagnostic_fills():
    instrument, book, now = fixture()
    stale = replace(now, mono_ns=1101)
    result = walk(instrument, book, stale, base_quantity=D("1"))
    assert not result.eligibility.eligible and result.filled_base_quantity == D("0")
    assert "stale" in result.reasons
    broken = replace(
        book,
        quality=replace(book.quality, integrity="invalid", lifecycle="resynchronizing"),
    )
    assert walk(instrument, broken, now, base_quantity=D("1")).notional_quote == D("0")
    policy = replace(POLICY, require_execution_access=True)
    assert (
        "execution_access_unknown"
        in walk(instrument, book, now, base_quantity=D("1"), policy=policy).reasons
    )


@pytest.mark.parametrize("side", ("buy", "sell"))
@pytest.mark.parametrize("sizing", ("base_quantity", "quote_budget"))
@pytest.mark.parametrize("order_type", ("market", "limit"))
@pytest.mark.parametrize("market_type", ("spot", "perpetual"))
def test_streaming_only_depth_walk_rejects_without_inventing_native_lots(
    side, sizing, order_type, market_type
):
    instrument, book, now = streaming_fixture(market_type=market_type)
    assert evaluate(book, instrument, "display", now, POLICY).eligible
    options = {sizing: D("1.23456789"), "order_type": order_type}
    if order_type == "limit":
        options["limit_price"] = D("100.123456789")
    estimate = walk(instrument, book, now, side=side, **options)
    assert "instrument_metadata_unverified" in estimate.reasons
    assert "instrument_metadata_unverified" in estimate.eligibility.reasons
    assert not estimate.eligibility.eligible
    assert estimate.filled_base_quantity == estimate.filled_native_quantity == 0
    assert estimate.notional_quote == 0
    assert estimate.requested_native_quantity is None
    assert estimate.vwap is None and estimate.effective_price is None
    assert not estimate.consumed_levels


@pytest.mark.parametrize("unverified_leg", ("buy", "sell", "both"))
def test_streaming_only_pair_has_no_feasible_quantity_or_profit(unverified_leg):
    left = streaming_fixture() if unverified_leg != "sell" else fixture()
    right = streaming_fixture("b") if unverified_leg != "buy" else fixture("b")
    result = pair(left, right)
    assert not result.eligible and not result.execution_eligible
    assert result.feasible_base_quantity == 0
    assert result.gross_reference is None and result.net is None
    assert result.net_bps is None
    for name in ("buy", "sell"):
        if unverified_leg in (name, "both"):
            assert "instrument_metadata_unverified" in getattr(result, name).reasons
            assert name + ":instrument_metadata_unverified" in result.reasons


def test_streaming_only_quotes_are_excluded_from_best_observed_and_matrix():
    verified, verified_book, now = fixture()
    streaming, streaming_book, _ = streaming_fixture("b")
    entries = (
        ComparisonEntry(verified_book, verified, FREE),
        ComparisonEntry(streaming_book, streaming, FREE),
    )
    observed = best_observed(entries, now=now, policy=POLICY)
    assert len(observed) == 1
    assert observed[0].bid_instrument_id == verified.instrument_id
    assert observed[0].ask_instrument_id == verified.instrument_id
    assert len(observed[0].exclusions) == 1
    assert observed[0].exclusions[0].instrument_id == streaming.instrument_id
    assert "instrument_metadata_unverified" in observed[0].exclusions[0].reasons
    matrix = compare_matrix(entries, base_quantity=D("1"), policy=POLICY, now=now)
    assert matrix.best_observed == observed
    assert matrix.exclusions == observed[0].exclusions
    for result in matrix.pairs:
        if streaming.instrument_id in (
            result.buy_instrument_id,
            result.sell_instrument_id,
        ):
            assert not result.eligible and not result.execution_eligible
            assert result.feasible_base_quantity == 0 and result.net is None
            assert any(
                reason.endswith("instrument_metadata_unverified")
                for reason in result.reasons
            )


def test_pair_rewalks_same_feasible_size_and_cash_accounting_without_double_count():
    left = fixture()
    right = fixture("b", bids=(("105", "1"), ("103", "2")), asks=(("106", "5"),))
    stress = StressAssumptions(
        latency_bps=D("10"), rebalance_cost=D("0.5"), provenance="synthetic scenario"
    )
    result = pair(left, right, "4", buy_fees=FEE, sell_fees=FEE, stress=stress)
    assert result.feasible_base_quantity == D(
        "3"
    ) and result.unfilled_base_quantity == D("1")
    assert result.buy.notional_quote == D("304") and result.sell.notional_quote == D(
        "311"
    )
    assert result.gross_reference == D("15")
    assert result.depth_slippage == D("8")
    assert result.buy_fee == D("3.04") and result.sell_fee == D("3.11")
    assert result.fx_cost == D("0") and result.latency_stress == D("0.615")
    assert result.net == D("-0.265")
    assert (
        result.net
        == result.gross_reference
        - result.depth_slippage
        - result.buy_fee
        - result.sell_fee
        - result.fx_cost
        - result.rebalance_cost
        - result.latency_stress
    )
    assert result.eligible and not result.execution_eligible
    assert "unknown_inventory_assumed_prefunded" in result.assumptions


def test_joint_lot_grid_not_merely_minimum_of_two_rounded_sizes():
    left = fixture(lot_size="0.03", bids=(("99", "0.12"),), asks=(("100", "0.12"),))
    right = fixture(
        "b", lot_size="0.02", bids=(("105", "0.12"),), asks=(("106", "0.12"),)
    )
    result = pair(left, right, "0.11")
    assert result.feasible_base_quantity == D("0.06")
    assert (
        result.buy.filled_base_quantity == result.sell.filled_base_quantity == D("0.06")
    )


def test_inventory_requires_cash_and_base_on_correct_prefunded_venues():
    left = fixture(execution_access="available")
    right = fixture(
        "b", bids=(("105", "5"),), asks=(("106", "5"),), execution_access="available"
    )
    inventory = (
        InventoryBalance("a", "USD", D("150")),
        InventoryBalance("b", "BTC", D("1.2")),
    )
    result = pair(left, right, "3", buy_fees=FEE, inventory=inventory)
    assert result.feasible_base_quantity == D("1.2")
    assert result.buy.notional_quote + result.buy.fee_amount <= D("150")
    assert result.sell.filled_base_quantity <= D("1.2")
    assert result.execution_eligible
    wrong_venues = (
        InventoryBalance("a", "BTC", D("10")),
        InventoryBalance("b", "USD", D("10000")),
    )
    result = pair(left, right, "1", inventory=wrong_venues)
    assert result.feasible_base_quantity == D("0") and result.net is None
    assert "no_instantaneous_transfers" in result.assumptions
    with pytest.raises(ValueError):
        pair(left, right, inventory=inventory + (inventory[0],))


def test_buy_fee_is_included_in_cash_cap_and_stale_balances_exclude():
    left, right = fixture(), fixture("b")
    inventory = (
        InventoryBalance("a", "USD", D("100")),
        InventoryBalance("b", "BTC", D("5")),
    )
    result = pair(left, right, "1", buy_fees=FEE, inventory=inventory)
    assert result.feasible_base_quantity == D("0.990")
    assert result.buy.notional_quote + result.buy.fee_amount == D("99.990")
    now = replace(left[2], mono_ns=1001)
    dated = tuple(replace(item, asof=replace(now, mono_ns=0)) for item in inventory)
    stale = pair(left, right, "1", inventory=dated, now=now)
    assert "stale_inventory" in stale.reasons and stale.net is None


def fx(now, **options):
    return FXRate(
        "USDT",
        "USD",
        D("0.90"),
        D("0.91"),
        now,
        "synthetic depeg book",
        conversion_fee_rate=options.pop("conversion_fee_rate", D("0")),
        **options,
    )


def test_fx_orientation_required_input_and_conversion_fees():
    _, _, now = fixture()
    rate = fx(now)
    sell = convert_fx(D("100"), "USDT", "USD", rates=(rate,), now=now, policy=POLICY)
    assert sell.target_amount == D("90")
    buy = convert_fx(D("91"), "USD", "USDT", rates=(rate,), now=now, policy=POLICY)
    assert buy.target_amount == D("100")
    funding = convert_fx(
        D("100"),
        "USD",
        "USDT",
        rates=(rate,),
        now=now,
        policy=POLICY,
        required_input=True,
    )
    assert funding.source_amount == D("91")
    reverse = convert_fx(
        D("90"),
        "USDT",
        "USD",
        rates=(rate,),
        now=now,
        policy=POLICY,
        required_input=True,
    )
    assert reverse.source_amount == D("100")
    charged = replace(rate, conversion_fee_rate=D("0.01"))
    proceeds = convert_fx(
        D("100"), "USDT", "USD", rates=(charged,), now=now, policy=POLICY
    )
    assert proceeds.target_amount == D("89.10")
    funding = convert_fx(
        D("99"),
        "USD",
        "USDT",
        rates=(charged,),
        now=now,
        policy=POLICY,
        required_input=True,
    )
    assert funding.source_amount == D("91")


def test_depeg_pair_uses_ask_to_fund_required_quote_not_reciprocal_proceeds():
    left = fixture(quote="USDT")
    right = fixture("b", bids=(("95", "5"),), asks=(("96", "5"),))
    rate = fx(left[2])
    result = pair(left, right, "1", numeraire="USD", fx_rates=(rate,))
    assert result.gross_reference == D("4.5")
    assert result.depth_slippage == D("0") and result.fx_cost == D("0.5")
    assert result.net == D("4")
    assert result.fx_conversions[0].source_amount == D("91")
    missing = pair(left, right, "1", numeraire="USD")
    assert missing.net is None and any(
        reason.startswith("missing_fx:") for reason in missing.reasons
    )
    usdc = fixture("c", quote="USDC")
    assert pair(left, usdc, "1").net is None


@pytest.mark.parametrize(
    "kind,reason",
    [
        ("stale", "stale_fx"),
        ("future", "fx_clock_regression"),
        ("clock", "fx_clock_mismatch"),
        ("skew", "fx_asof_skew"),
    ],
)
def test_fx_missing_stale_future_wrong_clock_and_skew_fail_explicitly(kind, reason):
    _, _, now = fixture()
    rate = fx(now)
    reference = 100
    if kind == "stale":
        now = replace(now, mono_ns=1200)
    elif kind == "future":
        rate = replace(rate, asof=replace(now, mono_ns=101))
    elif kind == "clock":
        rate = replace(rate, asof=replace(now, clock_id="other"))
    else:
        reference = 0
    result = convert_fx(
        D("100"),
        "USD",
        "USDT",
        rates=(rate,),
        now=now,
        policy=POLICY,
        reference_mono_ns=reference,
    )
    assert result.target_amount is None and reason in result.reasons


def test_matrix_preserves_quote_and_market_cohorts_exclusions_and_full_directions():
    left = fixture()
    right = fixture("b", bids=(("105", "5"),), asks=(("106", "5"),))
    stable = fixture("c", quote="USDT")
    entries = tuple(
        ComparisonEntry(book, instrument, FREE)
        for instrument, book, _ in (left, right, stable)
    )
    result = compare_matrix(entries, base_quantity=D("1"), policy=POLICY, now=left[2])
    assert len(result.pairs) == 9 and len(result.best_observed) == 2
    usd = next(item for item in result.best_observed if item.quote_asset_id == "USD")
    assert usd.bid == D("105") and usd.ask == D("100")
    assert usd.bid_instrument_id == right[0].instrument_id
    assert all(
        not item.eligible
        for item in result.pairs
        if item.buy_instrument_id == item.sell_instrument_id
    )
    assert sum(item.eligible for item in result.pairs) == 2
    old = fixture("d", mono=1)
    observed = best_observed(
        entries[:2] + (ComparisonEntry(old[1], old[0], FREE),),
        now=left[2],
        policy=POLICY,
    )
    assert observed[0].exclusions[0].reasons == ("asof_skew",)
    duplicate = compare_matrix(
        (entries[0], entries[0]), base_quantity=D("1"), policy=POLICY, now=left[2]
    )
    assert all("ambiguous_book_key" in item.reasons for item in duplicate.pairs)
    assert duplicate.best_observed[0].bid is None


@pytest.mark.parametrize("state", ["stale", "disconnected"])
def test_best_observed_selects_healthy_survivor_over_more_attractive_book(state):
    healthy_instrument, healthy_book, clock = fixture(mono=1100)
    instrument, book, _ = fixture(
        "b",
        bids=(("99.50", "1"),),
        asks=(("99.75", "1"),),
        mono=1 if state == "stale" else 1100,
    )
    if state == "disconnected":
        book = replace(
            book,
            quality=replace(
                book.quality, lifecycle="disconnected", transport_alive=False
            ),
        )
    (observed,) = best_observed(
        (
            ComparisonEntry(book, instrument, FREE),
            ComparisonEntry(healthy_book, healthy_instrument, FREE),
        ),
        now=replace(clock, mono_ns=1100),
        policy=POLICY,
    )
    assert observed.members == (healthy_instrument.instrument_id,)
    assert observed.bid == D("99") and observed.ask == D("100")
    assert observed.bid_instrument_id == healthy_instrument.instrument_id
    assert observed.ask_instrument_id == healthy_instrument.instrument_id
    assert observed.bid_asof == observed.ask_asof == healthy_book.asof
    (exclusion,) = observed.exclusions
    assert exclusion.instrument_id == instrument.instrument_id
    assert ("stale" if state == "stale" else "lifecycle_disconnected") in (
        exclusion.reasons
    )


def test_best_observed_keeps_btc_and_eth_in_separate_native_cohorts():
    btc = fixture(base="BTC")
    eth = fixture("b", base="ETH", bids=(("199", "1"),), asks=(("200", "1"),))
    observed = best_observed(
        tuple(
            ComparisonEntry(book, instrument, FREE)
            for instrument, book, _ in (btc, eth)
        ),
        now=btc[2],
        policy=POLICY,
    )
    assert len(observed) == 2
    cohorts = {item.base_asset_id: item for item in observed}
    assert set(cohorts) == {"BTC", "ETH"}
    for base, (instrument, book, _) in (("BTC", btc), ("ETH", eth)):
        cohort = cohorts[base]
        assert cohort.quote_asset_id == "USD" and cohort.market_type == "spot"
        assert cohort.members == (instrument.instrument_id,)
        assert cohort.bid == book.bids[0].price
        assert cohort.ask == book.asks[0].price
        assert (
            cohort.bid_instrument_id
            == cohort.ask_instrument_id
            == (instrument.instrument_id)
        )
        assert not cohort.exclusions


@pytest.mark.parametrize("max_skew", [0, 37, 50])
@pytest.mark.parametrize("beyond_boundary", [False, True])
def test_best_observed_accepts_exact_configured_skew_boundary(
    max_skew, beyond_boundary
):
    healthy_instrument, healthy_book, now = fixture()
    instrument, book, _ = fixture(
        "b",
        bids=(("99.50", "1"),),
        asks=(("99.75", "1"),),
        mono=100 - max_skew - int(beyond_boundary),
    )
    (observed,) = best_observed(
        (
            ComparisonEntry(healthy_book, healthy_instrument, FREE),
            ComparisonEntry(book, instrument, FREE),
        ),
        now=now,
        policy=replace(POLICY, max_asof_skew_ns=max_skew),
    )
    selected_instrument = healthy_instrument if beyond_boundary else instrument
    selected_book = healthy_book if beyond_boundary else book
    assert (
        observed.bid_instrument_id
        == observed.ask_instrument_id
        == (selected_instrument.instrument_id)
    )
    assert observed.bid == selected_book.bids[0].price
    assert observed.ask == selected_book.asks[0].price
    if beyond_boundary:
        assert observed.members == (healthy_instrument.instrument_id,)
        (exclusion,) = observed.exclusions
        assert exclusion.instrument_id == instrument.instrument_id
        assert exclusion.reasons == ("asof_skew",)
    else:
        assert observed.members == (
            healthy_instrument.instrument_id,
            instrument.instrument_id,
        )
        assert not observed.exclusions


@pytest.mark.parametrize("source_mode", ["rest_snapshot", "ws_delta"])
def test_best_observed_accepts_valid_rest_and_continuous_heartbeat_evidence(
    source_mode,
):
    instrument = synthetic_instrument()
    key = SubscriptionKey.for_instrument(instrument)
    reducer = BookReducer(
        instrument,
        key,
        segment_id="synthetic-segment",
        capabilities=Capabilities(
            deltas=source_mode == "ws_delta",
            exact_event_flow=source_mode == "ws_delta",
        ),
    )
    snapshot = synthetic_envelope(
        instrument,
        BookUpdate(
            "snapshot",
            (
                LevelChange("bid", D("99"), D("1")),
                LevelChange("ask", D("100"), D("1")),
            ),
        ),
        key=key,
        mono_ns=1,
        source_mode=source_mode,
    )
    book = reducer.apply((snapshot,)).book
    assert book is not None
    now = ClockReading(snapshot.clock_id, 1)
    if source_mode == "rest_snapshot":
        assert book.quality.lifecycle == "degraded"
        assert book.quality.integrity == "valid"
    else:
        now = replace(now, mono_ns=POLICY.max_age_ns + 2)
        (stale,) = best_observed(
            (ComparisonEntry(book, instrument, FREE),), now=now, policy=POLICY
        )
        assert stale.bid is stale.ask is None
        assert stale.exclusions[0].reasons == ("stale",)
        heartbeat = synthetic_envelope(
            instrument,
            Control("heartbeat"),
            key=key,
            ordinal=2,
            mono_ns=now.mono_ns,
            source_mode=source_mode,
        )
        book = reducer.apply((heartbeat,)).book
        assert book is not None and book.asof == snapshot.ref()
        assert book.quality.last_book_observation_mono_ns == 1
        assert book.quality.last_healthy_observation_mono_ns == now.mono_ns
    (observed,) = best_observed(
        (ComparisonEntry(book, instrument, FREE),), now=now, policy=POLICY
    )
    assert observed.members == (instrument.instrument_id,)
    assert observed.bid == D("99") and observed.ask == D("100")
    assert (
        observed.bid_instrument_id
        == observed.ask_instrument_id
        == (instrument.instrument_id)
    )
    assert not observed.exclusions


def test_spot_perpetual_and_quanto_are_not_executable_arbitrage_or_fx():
    spot = fixture()
    perp = fixture(
        "b",
        market_type="perpetual",
        settlement=AssetIdentity("USD", "USD"),
        settlement_conversion="none",
    )
    result = pair(spot, perp, "1")
    assert result.net is None and "incompatible_instruments" in result.reasons
    hl = fixture(
        "hyperliquid",
        quote="USDT",
        market_type="perpetual",
        settlement=AssetIdentity("USDC", "USDC"),
        settlement_conversion="quanto_no_fx",
    )
    estimate = walk(hl[0], hl[1], hl[2], base_quantity=D("1"))
    assert estimate.settlement_asset_id == "USDC" and estimate.settlement_notional == D(
        "100"
    )
    assert "quanto_contract_rule_not_fx" in estimate.assumptions
    result = pair(
        hl,
        fixture(
            "b",
            quote="USDT",
            market_type="perpetual",
            settlement=AssetIdentity("USDC", "USDC"),
            settlement_conversion="quanto_no_fx",
        ),
        "1",
        numeraire="USDC",
    )
    assert result.net is None
    assert "quanto_pair_requires_settlement_pnl_model" in result.reasons
    assert any(reason.startswith("missing_fx:") for reason in result.reasons)


def test_derived_results_roundtrip_without_relaxing_native_precision():
    instrument, book, now = fixture()
    with localcontext() as ambient:
        ambient.prec = 5
        estimate = walk(instrument, book, now, base_quantity=D("3"))
    assert len(estimate.vwap.as_tuple().digits) == 80
    payload = dumps(estimate, decimal_policy=DERIVED_DECIMAL_POLICY)
    assert loads(payload, decimal_policy=DERIVED_DECIMAL_POLICY) == estimate
    with pytest.raises(ValueError):
        dumps(estimate)
    with pytest.raises(FrozenInstanceError):
        estimate.notional_quote = D("0")
    with pytest.raises(ValueError):
        FeeSchedule(
            D("0." + "1" * 39), provenance="too precise native fee", asof_wall_ns=1
        )


def test_unknown_fx_cost_does_not_silently_become_free_conversion():
    _, _, now = fixture()
    rate = FXRate("USDT", "USD", D("0.90"), D("0.91"), now, "synthetic source")
    result = convert_fx(D("100"), "USDT", "USD", rates=(rate,), now=now, policy=POLICY)
    assert result.target_amount is None and "unknown_fx_fees" in result.reasons
    assert result.mid_equivalent == D("90.5")


def test_inverse_lot_solver_bounds_work_instead_of_approximating_a_pair(monkeypatch):
    import src.workstation.costs as costs

    left = inverse(bids=(("190", "10"),), asks=(("200", "2"), ("400", "8")))
    right = inverse("b", bids=(("500", "20"),), asks=(("510", "20"),))
    monkeypatch.setattr(costs, "MAX_PAIR_GRID_LEVELS", 1)
    result = pair(left, right, "2")
    assert result.feasible_base_quantity == D("0") and result.net is None
    assert "lot_grid_complexity_limit" in result.reasons
    # Ordinary native spot lots bypass rational machinery, including its bounds.
    spot = pair(fixture(), fixture("b"), "2")
    assert spot.feasible_base_quantity == D("2")
    monkeypatch.setattr(costs, "MAX_PAIR_GRID_LEVELS", 256)
    monkeypatch.setattr(costs, "MAX_LOT_GRID_BITS", 4)
    result = pair(left, right, "2")
    assert result.net is None and "lot_grid_complexity_limit" in result.reasons


def test_infeasible_minimum_survives_pair_zero_quantity_rewalk():
    left = fixture(constraints=OrderConstraints(min_base=D("2")))
    result = pair(left, fixture("b"), "1")
    assert result.feasible_base_quantity == D("0")
    assert "buy:below_order_minimum" in result.reasons
    assert "buy:below_order_minimum" in result.feasibility_reasons


def test_existing_hyperliquid_native_price_rule_is_used_for_limit_orders():
    instrument, book, now = fixture(market_type="perpetual")
    instrument = replace(
        instrument,
        metadata_version="synthetic-variable-price-v1",
        tick_size=None,
        constraints=OrderConstraints(price_rule="hyperliquid_significant_digits"),
    )
    book = replace(book, metadata_version=instrument.metadata_version)
    allowed = walk(
        instrument,
        book,
        now,
        base_quantity=D("1"),
        order_type="limit",
        limit_price=D("100.12"),
    )
    assert allowed.filled_base_quantity == D("1")
    with pytest.raises(ValueError):
        walk(
            instrument,
            book,
            now,
            base_quantity=D("1"),
            order_type="limit",
            limit_price=D("100.123"),
        )


def test_future_fee_date_cannot_create_available_net_cost():
    instrument, book, now = fixture()
    future = replace(FEE, asof_wall_ns=now.wall_ns + 1)
    estimate = walk(instrument, book, now, base_quantity=D("1"), fees=future)
    assert estimate.notional_quote == D("100")
    assert estimate.fee_amount is estimate.effective_price is None
    assert "fee_date_in_future" in estimate.reasons


@pytest.mark.parametrize(
    "generic,market,expected", [("2", "1.5", "1.5"), ("1.5", "2", "1.5")]
)
def test_generic_maximum_and_market_maximum_both_apply(generic, market, expected):
    instrument, book, now = fixture(
        constraints=OrderConstraints(max_base=D(generic), max_market_base=D(market))
    )
    estimate = walk(instrument, book, now, base_quantity=D("3"))
    assert estimate.filled_native_quantity == D(expected)


def test_reference_link_does_not_merge_distinct_native_base_identities():
    spot = fixture()
    wrapped = fixture("b", base="WBTC")
    wi, wb, now = wrapped
    wi = replace(wi, base=replace(wi.base, reference_asset_id="BTC"))
    comparison = pair(spot, (wi, wb, now), "1")
    assert comparison.net is None and "incompatible_instruments" in comparison.reasons


@pytest.mark.parametrize("market_type", ["perpetual", "future"])
def test_derivative_collateral_is_not_spot_principal_or_underlying_inventory(
    market_type,
):
    metadata = {
        "market_type": market_type,
        "settlement": AssetIdentity("USD", "USD"),
        "settlement_conversion": "none",
        "execution_access": "available",
    }
    if market_type == "future":
        metadata["expiry_wall_ns"] = 2_000_000_000_000_000_000
    left = fixture(**metadata)
    right = fixture("b", bids=(("105", "5"),), asks=(("106", "5"),), **metadata)
    # Collateral is below either leg's principal notional; neither account
    # holds spot BTC. Without a margin model neither fact sizes these contracts.
    inventory = (
        InventoryBalance("a", "USD", D("1")),
        InventoryBalance("b", "USD", D("1")),
    )
    without_inventory = pair(left, right, "1")
    with_inventory = pair(left, right, "1", inventory=inventory)
    assert with_inventory.feasible_base_quantity == D("1")
    assert (
        with_inventory.feasible_base_quantity
        == without_inventory.feasible_base_quantity
    )
    assert with_inventory.gross_reference == with_inventory.net == D("5")
    assert with_inventory.net == without_inventory.net
    assert with_inventory.eligible and not with_inventory.execution_eligible
    assert with_inventory.inventory == inventory
    assert "derivative_collateral_margin_unavailable" in with_inventory.assumptions
    assert "unknown_inventory_assumed_prefunded" not in with_inventory.assumptions
    assert "buy_prefunded_cash_limited" not in with_inventory.assumptions
    assert "sell_prefunded_base_limited" not in with_inventory.assumptions


def test_quanto_settlement_collateral_preserves_informational_depth_not_fx_or_margin():
    metadata = {
        "quote": "USDT",
        "market_type": "perpetual",
        "settlement": AssetIdentity("USDC", "USDC"),
        "settlement_conversion": "quanto_no_fx",
        "execution_access": "available",
    }
    left = fixture("hyperliquid", **metadata)
    right = fixture("b", bids=(("105", "5"),), asks=(("106", "5"),), **metadata)
    inventory = (
        InventoryBalance("hyperliquid", "USDC", D("10")),
        InventoryBalance("b", "USDC", D("10")),
    )
    result = pair(left, right, "1", inventory=inventory)
    assert result.feasible_base_quantity == D("1")
    assert result.buy.notional_quote == D("100")
    assert result.sell.notional_quote == D("105")
    assert result.gross_reference == D("5")
    assert result.net is None and not result.execution_eligible
    assert "quanto_pair_requires_settlement_pnl_model" in result.reasons
    assert "no_feasible_quantity" not in result.reasons
    assert "derivative_collateral_margin_unavailable" in result.assumptions
