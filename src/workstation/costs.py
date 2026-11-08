"""Conditional, exact known-depth execution and prefunded venue comparisons.

Money uses the versioned p80 Decimal context. Integer arithmetic is used only
for discrete lot-grid intersections; native market constructors remain strict.
No function submits orders, obtains balances, invents fees, or transfers assets.
"""

from collections.abc import Iterable
from dataclasses import dataclass, fields, is_dataclass, replace
from decimal import Decimal, ROUND_FLOOR, localcontext
from fractions import Fraction
from math import gcd, lcm
from typing import get_args, get_origin

from .book import Book
from .events import EventEnvelopeRef
from .instruments import AssetId, Instrument, InstrumentId
from .precision import (
    DECIMAL_CONTEXT,
    DERIVED_DECIMAL_POLICY,
    choice,
    decimal,
    nonnegative_int,
    text,
)
from .quality import (
    ClockReading,
    Coverage,
    Eligibility,
    EligibilityPolicy,
    evaluate,
    evaluate_cohort,
)

ZERO = Decimal("0")
ONE = Decimal("1")
BPS = Decimal("10000")
FORMULA_VERSION = "costs-v1-p80"
MAX_PAIR_GRID_LEVELS = 256
MAX_LOT_GRID_BITS = 4096
_CohortKey = tuple[
    AssetId,
    AssetId,
    str,
    AssetId | None,
    int | None,
    str,
    Decimal,
    AssetId | None,
    bool,
    str,
]
_NONNEGATIVE_RESULTS = frozenset(
    {
        "requested_base_quantity",
        "quote_budget",
        "filled_base_quantity",
        "unfilled_base_quantity",
        "requested_native_quantity",
        "filled_native_quantity",
        "unfilled_native_quantity",
        "unfilled_quote_budget",
        "notional_quote",
        "native_quantity",
        "base_quantity",
        "fee_amount",
        "fee_quote_equivalent",
        "feasible_base_quantity",
        "buy_fee",
        "sell_fee",
        "rebalance_cost",
        "latency_stress",
        "settlement_notional",
        "source_amount",
        "target_amount",
        "mid_equivalent",
    }
)


class _Derived:
    """Validate derived decimals without relaxing nested native model bounds."""

    def __post_init__(self):
        if not is_dataclass(self):
            raise TypeError("derived validation requires a dataclass instance")
        for item in fields(self):
            value = getattr(self, item.name)
            if item.type is Decimal or Decimal in get_args(item.type):
                if value is not None:
                    object.__setattr__(
                        self,
                        item.name,
                        decimal(
                            value,
                            policy=DERIVED_DECIMAL_POLICY,
                            nonnegative=item.name in _NONNEGATIVE_RESULTS,
                            positive=item.name
                            in {
                                "price",
                                "vwap",
                                "best_price",
                                "effective_price",
                                "bid",
                                "ask",
                            },
                        ),
                    )
                elif item.type is Decimal:
                    raise ValueError("missing derived decimal: " + item.name)
            elif get_origin(item.type) is tuple:
                object.__setattr__(self, item.name, tuple(value))
            elif item.type is bool and type(value) is not bool:
                raise ValueError("result flag must be boolean: " + item.name)


@dataclass(frozen=True)
class FeeSchedule:
    rate: Decimal | None = None
    asset_id: AssetId | None = None
    provenance: str = "unknown"
    asof_wall_ns: int | None = None
    assumed: bool = True

    def __post_init__(self):
        text(self.provenance)
        if type(self.assumed) is not bool:
            raise ValueError("fee assumed must be boolean")
        if self.asset_id is not None:
            object.__setattr__(self, "asset_id", AssetId(self.asset_id))
        if self.asof_wall_ns is not None:
            nonnegative_int(self.asof_wall_ns)
        if self.rate is not None:
            rate = decimal(self.rate, nonnegative=True)
            if rate >= ONE:
                raise ValueError("fee rate must be below one")
            if self.provenance == "unknown" or self.asof_wall_ns is None:
                raise ValueError("known fees require provenance and date")
            object.__setattr__(self, "rate", rate)


@dataclass(frozen=True)
class FXRate:
    base_asset_id: AssetId
    quote_asset_id: AssetId
    bid: Decimal
    ask: Decimal
    asof: ClockReading
    source: str
    conversion_fee_rate: Decimal | None = None
    assumed: bool = False

    def __post_init__(self):
        for name in ("base_asset_id", "quote_asset_id"):
            object.__setattr__(self, name, AssetId(getattr(self, name)))
        if self.base_asset_id == self.quote_asset_id:
            raise ValueError("FX requires distinct assets")
        for name in ("bid", "ask"):
            object.__setattr__(self, name, decimal(getattr(self, name), positive=True))
        rate = self.conversion_fee_rate
        if rate is not None:
            rate = decimal(rate, nonnegative=True)
        if self.bid > self.ask or rate is not None and rate >= ONE:
            raise ValueError("invalid FX spread or conversion fee")
        object.__setattr__(self, "conversion_fee_rate", rate)
        if not isinstance(self.asof, ClockReading) or type(self.assumed) is not bool:
            raise ValueError("invalid FX evidence")
        text(self.source)


@dataclass(frozen=True)
class FXConversion(_Derived):
    source_asset_id: AssetId
    target_asset_id: AssetId
    source_amount: Decimal | None
    target_amount: Decimal | None
    mid_equivalent: Decimal | None
    cost: Decimal | None
    required_input: bool
    rate: FXRate | None
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class InventoryBalance:
    venue: str
    asset_id: AssetId
    available: Decimal
    provenance: str = "user supplied prefunded balance"
    asof: ClockReading | None = None

    def __post_init__(self):
        text(self.venue)
        text(self.provenance)
        object.__setattr__(self, "asset_id", AssetId(self.asset_id))
        object.__setattr__(self, "available", decimal(self.available, nonnegative=True))
        if self.asof is not None and not isinstance(self.asof, ClockReading):
            raise ValueError("invalid inventory clock")


@dataclass(frozen=True)
class StressAssumptions:
    latency_bps: Decimal = ZERO
    rebalance_cost: Decimal = ZERO
    provenance: str = "user assumption"

    def __post_init__(self):
        for name in ("latency_bps", "rebalance_cost"):
            object.__setattr__(
                self, name, decimal(getattr(self, name), nonnegative=True)
            )
        text(self.provenance)


@dataclass(frozen=True)
class ConsumedLevel(_Derived):
    price: Decimal
    native_quantity: Decimal
    base_quantity: Decimal
    notional_quote: Decimal


@dataclass(frozen=True)
class ExecutionEstimate(_Derived):
    instrument_id: InstrumentId
    venue: str
    side: str
    requested_base_quantity: Decimal | None
    quote_budget: Decimal | None
    filled_base_quantity: Decimal
    unfilled_base_quantity: Decimal | None
    requested_native_quantity: Decimal | None
    filled_native_quantity: Decimal
    unfilled_native_quantity: Decimal | None
    unfilled_quote_budget: Decimal | None
    notional_quote: Decimal
    quote_asset_id: AssetId
    vwap: Decimal | None
    best_price: Decimal | None
    slippage_quote: Decimal | None
    slippage_bps: Decimal | None
    fee_amount: Decimal | None
    fee_asset_id: AssetId
    fee_quote_equivalent: Decimal | None
    fees: FeeSchedule
    effective_price: Decimal | None
    consumed_levels: tuple[ConsumedLevel, ...]
    coverage: Coverage
    eligibility: Eligibility
    reasons: tuple[str, ...]
    partial: bool
    assumptions: tuple[str, ...]
    asof: EventEnvelopeRef
    quantity_unit: str
    order_type: str
    settlement_asset_id: AssetId | None
    settlement_notional: Decimal | None
    formula_version: str = FORMULA_VERSION


@dataclass(frozen=True)
class PairEstimate(_Derived):
    buy_instrument_id: InstrumentId
    sell_instrument_id: InstrumentId
    requested_base_quantity: Decimal
    feasible_base_quantity: Decimal
    unfilled_base_quantity: Decimal
    numeraire: AssetId
    buy: ExecutionEstimate
    sell: ExecutionEstimate
    gross_reference: Decimal | None
    depth_slippage: Decimal | None
    buy_fee: Decimal | None
    sell_fee: Decimal | None
    fx_cost: Decimal | None
    rebalance_cost: Decimal
    latency_stress: Decimal | None
    net: Decimal | None
    net_bps: Decimal | None
    eligible: bool
    execution_eligible: bool
    reasons: tuple[str, ...]
    assumptions: tuple[str, ...]
    fx_conversions: tuple[FXConversion, ...]
    inventory: tuple[InventoryBalance, ...]
    stress: StressAssumptions
    feasibility_reasons: tuple[str, ...]
    formula_version: str = FORMULA_VERSION


@dataclass(frozen=True)
class ComparisonEntry:
    book: Book
    instrument: Instrument
    fees: FeeSchedule

    def __post_init__(self):
        if not isinstance(self.book, Book) or not isinstance(
            self.instrument, Instrument
        ):
            raise ValueError("comparison needs native book and metadata")
        if not isinstance(self.fees, FeeSchedule):
            raise ValueError("comparison needs explicit fees")


@dataclass(frozen=True)
class ComparisonExclusion:
    instrument_id: InstrumentId
    reasons: tuple[str, ...]

    def __post_init__(self):
        object.__setattr__(self, "instrument_id", InstrumentId(self.instrument_id))
        object.__setattr__(self, "reasons", tuple(self.reasons))


@dataclass(frozen=True)
class BestObserved(_Derived):
    base_asset_id: AssetId
    quote_asset_id: AssetId
    market_type: str
    settlement_asset_id: AssetId | None
    bid: Decimal | None
    ask: Decimal | None
    bid_instrument_id: InstrumentId | None
    ask_instrument_id: InstrumentId | None
    bid_asof: EventEnvelopeRef | None
    ask_asof: EventEnvelopeRef | None
    members: tuple[InstrumentId, ...]
    exclusions: tuple[ComparisonExclusion, ...]
    label: str = "best observed comparable bid/ask"


@dataclass(frozen=True)
class ComparisonMatrix:
    pairs: tuple[PairEstimate, ...]
    best_observed: tuple[BestObserved, ...]
    exclusions: tuple[ComparisonExclusion, ...]

    def __post_init__(self):
        for name in ("pairs", "best_observed", "exclusions"):
            object.__setattr__(self, name, tuple(getattr(self, name)))


def _unique(reasons: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(reasons))


def _floor(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def _valuation_reason(instrument: Instrument) -> str | None:
    if instrument.metadata_status != "verified":
        return "instrument_metadata_unverified"
    if instrument.quantity_unit == "base":
        if instrument.inverse or instrument.contract_multiplier != ONE:
            return "ambiguous_base_quantity_contract"
    elif instrument.inverse:
        if instrument.contract_value_currency != instrument.quote.asset_id:
            return "unknown_inverse_contract_value"
    elif instrument.contract_value_currency != instrument.base.asset_id:
        return "unknown_linear_contract_value"
    return None


def _amounts(
    instrument: Instrument, price: Decimal, native: Decimal
) -> tuple[Decimal, Decimal]:
    if instrument.quantity_unit == "base":
        return native, native * price
    value = native * instrument.contract_multiplier
    return (value / price, value) if instrument.inverse else (value, value * price)


def _native_for_base(instrument: Instrument, price: Decimal, base: Decimal):
    if instrument.quantity_unit == "base":
        return base
    if instrument.inverse:
        return base * price / instrument.contract_multiplier
    return base / instrument.contract_multiplier


def _validate_price(instrument: Instrument, price: Decimal):
    if instrument.constraints.price_rule == "hyperliquid_significant_digits":
        from .adapters.hyperliquid import validate_price

        validate_price(instrument, price)
    else:
        instrument.validate_price(price)


def walk_book(
    book: Book,
    instrument: Instrument,
    side: str,
    *,
    base_quantity: Decimal | None = None,
    quote_budget: Decimal | None = None,
    fees: FeeSchedule,
    policy: EligibilityPolicy,
    now: ClockReading,
    order_type: str = "market",
    limit_price: Decimal | None = None,
) -> ExecutionEstimate:
    """Walk asks for buy, bids for sell; budget caps gross native quote notional.

    Lot remainders and type-specific maxima reduce the fill, never round up.
    A partial fill below a native minimum is not an executable proposed order.
    A sell quote budget caps gross proceeds; neither budget silently includes fees.
    """
    choice(side, ("buy", "sell"), "side")
    choice(order_type, ("market", "limit"), "order type")
    if (base_quantity is None) == (quote_budget is None):
        raise ValueError("specify exactly one base quantity or native quote budget")
    if not isinstance(fees, FeeSchedule):
        raise ValueError("explicit FeeSchedule required, including unknown fees")
    # Derived requests are needed when a pair reconciles contract quantities.
    if base_quantity is not None:
        base_quantity = decimal(
            base_quantity, nonnegative=True, policy=DERIVED_DECIMAL_POLICY
        )
    if quote_budget is not None:
        quote_budget = decimal(
            quote_budget, nonnegative=True, policy=DERIVED_DECIMAL_POLICY
        )
    if (order_type == "limit") != (limit_price is not None):
        raise ValueError("limit orders require a limit price; market orders do not")
    if limit_price is not None:
        limit_price = decimal(limit_price, positive=True)
        _validate_price(instrument, limit_price)
    with localcontext(DECIMAL_CONTEXT):
        return _walk(
            book,
            instrument,
            side,
            base_quantity,
            quote_budget,
            fees,
            policy,
            now,
            order_type,
            limit_price,
        )


def _walk(
    book: Book,
    instrument: Instrument,
    side: str,
    base_quantity: Decimal | None,
    quote_budget: Decimal | None,
    fees: FeeSchedule,
    policy: EligibilityPolicy,
    now: ClockReading,
    order_type: str,
    limit_price: Decimal | None,
) -> ExecutionEstimate:
    gate = evaluate(book, instrument, "depth_walk", now, policy)
    reasons = list(gate.reasons)
    assumptions = [
        "conditional_on_observed_depth",
        "no_market_impact",
        "no_queue_or_fill_guarantee",
    ]
    if fees.assumed:
        assumptions.append("user_assumed_fees")
    if quote_budget is not None:
        assumptions.append(
            "quote_budget_excludes_fees"
            if side == "buy"
            else "quote_budget_caps_gross_proceeds"
        )
    if instrument.execution_access != "available":
        assumptions.append("execution_access_" + instrument.execution_access)
    if instrument.market_type != "spot":
        assumptions.append("derivative_notional_not_cash_or_margin")
    valuation = _valuation_reason(instrument)
    if valuation:
        reasons.append(valuation)
    constraints = instrument.constraints
    lot = instrument.lot_size
    if constraints.size_rule != "fixed_lot" or lot is None:
        reasons.append("unsupported_size_rule")
    if (
        constraints.min_notional is not None or constraints.max_notional is not None
    ) and constraints.min_notional_currency != instrument.quote.asset_id:
        reasons.append("unknown_notional_constraint_currency")
    levels = book.asks if side == "buy" else book.bids
    best = levels[0].price if levels else None
    native_maxima = [
        item
        for item in (
            constraints.max_base,
            constraints.max_market_base
            if order_type == "market"
            else constraints.max_limit_base,
        )
        if item is not None
    ]
    native_cap = min(native_maxima) if native_maxima else None
    notional_caps = [
        item for item in (quote_budget, constraints.max_notional) if item is not None
    ]
    notional_cap = min(notional_caps) if notional_caps else None
    consumed: list[ConsumedLevel] = []
    filled = native_total = notional = ZERO
    if not reasons:
        assert lot is not None  # Fixed-lot metadata is required above.
        for level in levels:
            if limit_price is not None and (
                side == "buy"
                and level.price > limit_price
                or side == "sell"
                and level.price < limit_price
            ):
                reasons.append("limit_price_reached")
                break
            try:
                _validate_price(instrument, level.price)
                instrument.validate_quantity(level.quantity)
            except ValueError:
                reasons.append("invalid_native_level")
                consumed = []
                filled = native_total = notional = ZERO
                break
            take = level.quantity
            if base_quantity is not None:
                take = min(
                    take,
                    _native_for_base(
                        instrument, level.price, max(ZERO, base_quantity - filled)
                    ),
                )
            if native_cap is not None:
                take = min(take, max(ZERO, native_cap - native_total))
            if notional_cap is not None:
                _, unit_notional = _amounts(instrument, level.price, ONE)
                take = min(take, max(ZERO, notional_cap - notional) / unit_notional)
            take = _floor(take, lot)
            # A recurring inverse conversion may round just below an exact lot
            # when inverted again. Reconcile one neighboring lot against the
            # forward-valued caps; never use an epsilon or round quantity up.
            for candidate in (
                take + lot,
                take,
                max(ZERO, take - lot),
            ):
                candidate_base, candidate_cash = _amounts(
                    instrument, level.price, candidate
                )
                if (
                    candidate <= level.quantity
                    and (
                        base_quantity is None
                        or filled + candidate_base <= base_quantity
                    )
                    and (native_cap is None or native_total + candidate <= native_cap)
                    and (
                        notional_cap is None
                        or notional + candidate_cash <= notional_cap
                    )
                ):
                    take = candidate
                    break
            if take <= ZERO:
                break
            base, cash = _amounts(instrument, level.price, take)
            # The summed cash is authoritative; never reconstruct it from VWAP.
            consumed.append(ConsumedLevel(level.price, take, base, cash))
            filled += base
            native_total += take
            notional += cash
            if take < level.quantity:
                break
        if native_total and (
            constraints.min_base is not None
            and native_total < constraints.min_base
            or constraints.min_notional is not None
            and notional < constraints.min_notional
        ):
            reasons.append("below_order_minimum")
            consumed = []
            filled = native_total = notional = ZERO
    requested_native = None
    if base_quantity is not None and not valuation and not instrument.inverse:
        requested_native = _native_for_base(instrument, best or ONE, base_quantity)
    unfilled = max(ZERO, base_quantity - filled) if base_quantity is not None else None
    quote_remaining = (
        max(ZERO, quote_budget - notional) if quote_budget is not None else None
    )
    partial = bool(unfilled if base_quantity is not None else quote_remaining)
    if partial and gate.eligible and not valuation:
        assert lot is not None  # Eligible instruments have verified lot metadata.
        if native_cap is not None and native_total >= _floor(native_cap, lot):
            reasons.append("native_order_maximum")
        if (
            constraints.max_notional is not None
            and notional_cap == constraints.max_notional
        ):
            reasons.append("notional_order_maximum")
        if (
            consumed
            and len(consumed) == len(levels)
            and consumed[-1].native_quantity == levels[-1].quantity
        ):
            reasons.append("known_depth_exhausted")
        elif not reasons:
            reasons.append("lot_remainder")
    fee_asset = fees.asset_id or instrument.quote.asset_id
    fee_amount = fee_quote = effective = None
    if fees.rate is None:
        reasons.append("unknown_fees")
    elif (
        fees.asof_wall_ns is not None
        and now.wall_ns is not None
        and fees.asof_wall_ns > now.wall_ns
    ):
        reasons.append("fee_date_in_future")
    elif fee_asset == instrument.quote.asset_id:
        fee_amount = fee_quote = notional * fees.rate
        if filled:
            effective = (
                notional + fee_amount if side == "buy" else notional - fee_amount
            ) / filled
    elif fee_asset == instrument.base.asset_id:
        fee_amount = filled * fees.rate
        fee_quote = notional * fees.rate
        if filled:
            effective = notional / (
                filled - fee_amount if side == "buy" else filled + fee_amount
            )
        assumptions.append("base_fee_valued_at_execution_vwap")
    else:
        reasons.append("unsupported_fee_asset")
    vwap = notional / filled if filled else None
    slippage = (
        ((notional - filled * best) if side == "buy" else (filled * best - notional))
        if filled and best is not None
        else None
    )
    slippage_bps = None
    if slippage is not None:
        assert best is not None
        slippage_bps = slippage / (filled * best) * BPS
    settlement_id = instrument.settlement.asset_id if instrument.settlement else None
    settlement_notional = None
    if instrument.market_type == "spot" and settlement_id in (
        None,
        instrument.quote.asset_id,
    ):
        settlement_notional = notional
    elif instrument.settlement_conversion == "quanto_no_fx":
        # Contract payout denomination, not an exchangeable quote/settlement FX rate.
        settlement_notional = notional
        assumptions.append("quanto_contract_rule_not_fx")
    elif (
        settlement_id == instrument.quote.asset_id
        and instrument.settlement_conversion == "none"
    ):
        settlement_notional = notional
    elif (
        instrument.inverse
        and settlement_id == instrument.base.asset_id
        and instrument.settlement_conversion == "none"
    ):
        settlement_notional = filled
    else:
        assumptions.append("unknown_settlement_conversion")
    return ExecutionEstimate(
        instrument.instrument_id,
        instrument.venue,
        side,
        base_quantity,
        quote_budget,
        filled,
        unfilled,
        requested_native,
        native_total,
        max(ZERO, requested_native - native_total)
        if requested_native is not None
        else None,
        quote_remaining,
        notional,
        instrument.quote.asset_id,
        vwap,
        best,
        slippage,
        slippage_bps,
        fee_amount,
        fee_asset,
        fee_quote,
        fees,
        effective,
        tuple(consumed),
        book.quality.coverage,
        gate,
        _unique(reasons),
        partial,
        _unique(assumptions),
        book.asof,
        instrument.quantity_unit,
        order_type,
        settlement_id,
        settlement_notional,
    )


def _fx_rate(source, target, rates, now, policy, reference_mono_ns):
    if source == target:
        return None, ()
    candidates = [
        rate
        for rate in rates
        if {rate.base_asset_id, rate.quote_asset_id} == {source, target}
    ]
    if not candidates:
        return None, ("missing_fx:" + source + ":" + target,)
    if len(candidates) != 1:
        return None, ("ambiguous_fx:" + source + ":" + target,)
    rate = candidates[0]
    reasons = []
    if rate.conversion_fee_rate is None:
        reasons.append("unknown_fx_fees")
    if rate.asof.clock_id != now.clock_id:
        reasons.append("fx_clock_mismatch")
    elif rate.asof.mono_ns > now.mono_ns:
        reasons.append("fx_clock_regression")
    elif now.mono_ns - rate.asof.mono_ns > policy.max_age_ns:
        reasons.append("stale_fx")
    if (
        reference_mono_ns is not None
        and abs(rate.asof.mono_ns - reference_mono_ns) > policy.max_asof_skew_ns
    ):
        reasons.append("fx_asof_skew")
    return rate, tuple(reasons)


def convert_fx(
    amount: Decimal,
    source_asset_id: AssetId,
    target_asset_id: AssetId,
    *,
    rates: tuple[FXRate, ...],
    now: ClockReading,
    policy: EligibilityPolicy,
    required_input: bool = False,
    reference_mono_ns: int | None = None,
) -> FXConversion:
    """Convert source proceeds, or fund an exact target amount from source.

    With required_input=True, amount is TARGET currency and the returned source
    amount is what must be provided. Costs use source units in that case, target
    units otherwise. This is not the reciprocal of a proceeds conversion in the
    opposite direction: bid/ask and conversion fees must retain their side.
    """
    amount = decimal(amount, nonnegative=True, policy=DERIVED_DECIMAL_POLICY)
    source, target = AssetId(source_asset_id), AssetId(target_asset_id)
    if type(required_input) is not bool:
        raise ValueError("required_input must be boolean")
    if reference_mono_ns is not None:
        nonnegative_int(reference_mono_ns)
    rate, reasons = _fx_rate(source, target, rates, now, policy, reference_mono_ns)
    if reasons and reasons != ("unknown_fx_fees",):
        return FXConversion(
            source, target, None, None, None, None, required_input, rate, reasons
        )
    with localcontext(DECIMAL_CONTEXT):
        if rate is None:
            return FXConversion(
                source, target, amount, amount, amount, ZERO, required_input, None, ()
            )
        mid = (rate.bid + rate.ask) / Decimal("2")
        selling_fx_base = source == rate.base_asset_id
        if reasons:
            mid_equivalent = (
                (amount / mid if selling_fx_base else amount * mid)
                if required_input
                else (amount * mid if selling_fx_base else amount / mid)
            )
            return FXConversion(
                source,
                target,
                None if required_input else amount,
                amount if required_input else None,
                mid_equivalent,
                None,
                required_input,
                rate,
                reasons,
            )
        retained = ONE - rate.conversion_fee_rate
        if required_input:
            source_amount = (
                amount / rate.bid if selling_fx_base else amount * rate.ask
            ) / retained
            mid_equivalent = amount / mid if selling_fx_base else amount * mid
            return FXConversion(
                source,
                target,
                source_amount,
                amount,
                mid_equivalent,
                source_amount - mid_equivalent,
                True,
                rate,
                (),
            )
        target_amount = (
            amount * rate.bid if selling_fx_base else amount / rate.ask
        ) * retained
        mid_equivalent = amount * mid if selling_fx_base else amount / mid
        return FXConversion(
            source,
            target,
            amount,
            target_amount,
            mid_equivalent,
            mid_equivalent - target_amount,
            False,
            rate,
            (),
        )


def _cohort_key(instrument: Instrument) -> _CohortKey:
    return (
        instrument.base.asset_id,
        instrument.quote.asset_id,
        instrument.market_type,
        instrument.settlement.asset_id if instrument.settlement else None,
        instrument.expiry_wall_ns,
        instrument.quantity_unit,
        instrument.contract_multiplier,
        instrument.contract_value_currency,
        instrument.inverse,
        instrument.settlement_conversion,
    )


def _pair_reasons(buy_book, buy_instrument, sell_book, sell_instrument, now, policy):
    cohort = evaluate_cohort(
        (buy_book, sell_book), (buy_instrument, sell_instrument), now, policy
    )
    reasons = [
        reason for reason in cohort.reasons if reason != "incompatible_instruments"
    ]
    for name, gate in cohort.members:
        reasons.extend(str(name) + ":" + reason for reason in gate.reasons)
    a, b = buy_instrument, sell_instrument
    if a.venue == b.venue:
        reasons.append("same_venue")
    if (
        a.base.asset_id != b.base.asset_id
        or a.market_type != b.market_type
        or a.expiry_wall_ns != b.expiry_wall_ns
    ):
        reasons.append("incompatible_instruments")
    # Cash spot quote currencies may differ only through explicit FX below.
    # Derivatives additionally require identical economic/settlement definitions.
    if a.market_type != "spot" and _cohort_key(a) != _cohort_key(b):
        reasons.append("incompatible_contracts")
    if a.market_type != "spot" and (
        a.settlement_conversion == "unknown" or b.settlement_conversion == "unknown"
    ):
        reasons.append("unknown_settlement_conversion")
    if any(
        item.market_type == "spot"
        and item.settlement is not None
        and item.settlement.asset_id != item.quote.asset_id
        for item in (a, b)
    ):
        reasons.append("unknown_spot_settlement_conversion")
    return reasons


def _inventory_at(inventory, instrument, asset, now, policy):
    venue_items = [item for item in inventory if item.venue == instrument.venue]
    matching = [item for item in venue_items if item.asset_id == asset]
    if len(matching) > 1:
        raise ValueError("duplicate inventory asset at venue")
    if not venue_items:
        return None, ()
    if not matching:
        return ZERO, ()
    item = matching[0]
    if item.asof is not None:
        if item.asof.clock_id != now.clock_id or item.asof.mono_ns > now.mono_ns:
            return ZERO, ("inventory_clock_mismatch",)
        if now.mono_ns - item.asof.mono_ns > policy.max_age_ns:
            return ZERO, ("stale_inventory",)
    return item.available, ()


class _GridComplexity(ValueError):
    pass


def _bounded_grid(value):
    if isinstance(value, Fraction):
        bits = max(abs(value.numerator).bit_length(), value.denominator.bit_length())
    else:
        bits = abs(value).bit_length()
    if bits > MAX_LOT_GRID_BITS:
        raise _GridComplexity("lot_grid_complexity_limit")
    return value


def _scaled_integer(value, exponent):
    parts = value.as_tuple()
    coefficient = 0
    for digit in parts.digits:
        coefficient = coefficient * 10 + digit
    return (-coefficient if parts.sign else coefficient) * 10 ** (
        int(parts.exponent) - exponent
    )


def _constant_grid_quantity(buy, bi, sell, si):
    """Spot/linear fast path: two finite native lot increments, no rational book."""
    assert bi.lot_size is not None and si.lot_size is not None
    limit = min(buy.filled_base_quantity, sell.filled_base_quantity)
    step_a, _ = _amounts(bi, ONE, bi.lot_size)
    step_b, _ = _amounts(si, ONE, si.lot_size)
    exponent = min(int(value.as_tuple().exponent) for value in (limit, step_a, step_b))
    cap, a, b = (_scaled_integer(value, exponent) for value in (limit, step_a, step_b))
    step = lcm(a, b)
    point = cap // step * step
    return +Decimal((0, tuple(int(digit) for digit in str(point)), exponent))


def _segments(estimate, instrument):
    """Exact rational lot indices, not money or rounded inverse-base increments.

    A three-contract fill at price three is base one, not three times the
    rounded Decimal(1/3). Fractions here only select discrete native lot counts;
    both resulting legs are independently valued in the declared Decimal context.
    """
    if len(estimate.consumed_levels) > MAX_PAIR_GRID_LEVELS:
        raise _GridComplexity("lot_grid_complexity_limit")
    prefix = Fraction(0)
    assert instrument.lot_size is not None
    lot = Fraction(instrument.lot_size)
    for level in estimate.consumed_levels:
        step = lot
        if instrument.quantity_unit == "contracts":
            step *= Fraction(instrument.contract_multiplier)
            if instrument.inverse:
                step /= Fraction(level.price)
        _bounded_grid(step)
        end = _bounded_grid(prefix + step * (Fraction(level.native_quantity) / lot))
        yield prefix, end, step
        prefix = end


def _grid_intersection(a, step_a, b, step_b, lower, upper):
    """Largest shared rational grid point via exact integer lot congruences."""
    values = (a, step_a, b, step_b, lower, upper)
    denominator = 1
    for value in values:
        denominator = _bounded_grid(lcm(denominator, value.denominator))
    ai, si, bi, ti, lo, hi = (
        _bounded_grid(value.numerator * (denominator // value.denominator))
        for value in values
    )
    divisor = gcd(si, ti)
    if (bi - ai) % divisor:
        return None
    modulus = ti // divisor
    k = (
        ((bi - ai) // divisor * pow(si // divisor, -1, modulus)) % modulus
        if modulus > 1
        else 0
    )
    period = _bounded_grid(si * modulus)
    first = _bounded_grid(ai + si * k)
    point = _bounded_grid(first + ((hi - first) // period) * period)
    return _bounded_grid(Fraction(point, denominator)) if point >= lo else None


def _rational_common_quantity(buy, buy_instrument, sell, sell_instrument):
    a, b = (
        tuple(_segments(buy, buy_instrument)),
        tuple(_segments(sell, sell_instrument)),
    )
    i = j = 0
    best = Fraction(0)
    while i < len(a) and j < len(b):
        start_a, end_a, step_a = a[i]
        start_b, end_b, step_b = b[j]
        lower, upper = max(start_a, start_b), min(end_a, end_b)
        if lower <= upper:
            common = _grid_intersection(start_a, step_a, start_b, step_b, lower, upper)
            if common is not None:
                best = max(best, common)
        if end_a <= end_b:
            i += 1
        if end_b <= end_a:
            j += 1
    return Decimal(best.numerator) / Decimal(best.denominator)


def _common_quantity(buy, bi, sell, si):
    if not buy.filled_base_quantity or not sell.filled_base_quantity:
        return ZERO, ()
    if not bi.inverse and not si.inverse:
        return _constant_grid_quantity(buy, bi, sell, si), ()
    try:
        return _rational_common_quantity(buy, bi, sell, si), ()
    except _GridComplexity:
        return ZERO, ("lot_grid_complexity_limit",)


def compare_pair(
    buy_book: Book,
    buy_instrument: Instrument,
    sell_book: Book,
    sell_instrument: Instrument,
    *,
    base_quantity: Decimal,
    buy_fees: FeeSchedule,
    sell_fees: FeeSchedule,
    policy: EligibilityPolicy,
    now: ClockReading,
    numeraire: AssetId | None = None,
    fx_rates: tuple[FXRate, ...] = (),
    inventory: tuple[InventoryBalance, ...] = (),
    stress: StressAssumptions = StressAssumptions(),
) -> PairEstimate:
    """Buy A and sell B over the same feasible base quantity, not two VWAP sizes."""
    quantity = decimal(base_quantity, nonnegative=True)
    currency = AssetId(numeraire or buy_instrument.quote.asset_id)
    with localcontext(DECIMAL_CONTEXT):
        return _compare_pair(
            buy_book,
            buy_instrument,
            sell_book,
            sell_instrument,
            quantity,
            buy_fees,
            sell_fees,
            policy,
            now,
            currency,
            tuple(fx_rates),
            tuple(inventory),
            stress,
        )


def _compare_pair(
    buy_book: Book,
    bi: Instrument,
    sell_book: Book,
    si: Instrument,
    quantity: Decimal,
    buy_fees: FeeSchedule,
    sell_fees: FeeSchedule,
    policy: EligibilityPolicy,
    now: ClockReading,
    currency: AssetId,
    rates: tuple[FXRate, ...],
    inventory: tuple[InventoryBalance, ...],
    stress: StressAssumptions,
) -> PairEstimate:
    reasons = _pair_reasons(buy_book, bi, sell_book, si, now, policy)
    assumptions = [
        "conditional_on_observed_books",
        "non_atomic_legs",
        "no_instantaneous_transfers",
        "no_market_impact",
        "rebalance_and_latency_are_user_scenarios",
    ]
    spot_pair = bi.market_type == si.market_type == "spot"
    if not spot_pair:
        assumptions.extend(
            (
                "derivative_notional_no_margin_model",
                "derivative_collateral_margin_unavailable",
                "opening_price_difference_not_realized_pnl",
            )
        )
    if (
        bi.settlement_conversion == "quanto_no_fx"
        or si.settlement_conversion == "quanto_no_fx"
    ):
        assumptions.append("quanto_contract_rule_not_fx")
        # A quote-denominated reference spread cannot become settled P&L via FX.
        reasons.append("quanto_pair_requires_settlement_pnl_model")
    buy = walk_book(
        buy_book,
        bi,
        "buy",
        base_quantity=quantity,
        fees=buy_fees,
        policy=policy,
        now=now,
    )
    sell = walk_book(
        sell_book,
        si,
        "sell",
        base_quantity=quantity,
        fees=sell_fees,
        policy=policy,
        now=now,
    )
    feasibility = [
        prefix + ":" + reason
        for prefix, estimate in (("buy", buy), ("sell", sell))
        for reason in estimate.reasons
    ]
    cash = base = None
    if spot_pair:
        cash, cash_reasons = _inventory_at(
            inventory, bi, bi.quote.asset_id, now, policy
        )
        base, base_reasons = _inventory_at(inventory, si, si.base.asset_id, now, policy)
        reasons.extend(cash_reasons + base_reasons)
        if cash is None or base is None:
            assumptions.append("unknown_inventory_assumed_prefunded")
    if bi.execution_access != "available" or si.execution_access != "available":
        assumptions.append("execution_access_not_established")
    if cash is not None:
        if buy_fees.rate is None:
            reasons.append("unknown_fees_prevent_cash_feasibility")
        factor = (
            ONE + buy_fees.rate
            if buy_fees.rate is not None
            and (buy_fees.asset_id or bi.quote.asset_id) == bi.quote.asset_id
            else ONE
        )
        limited = walk_book(
            buy_book,
            bi,
            "buy",
            quote_budget=cash / factor,
            fees=buy_fees,
            policy=policy,
            now=now,
        )
        if limited.filled_base_quantity < buy.filled_base_quantity:
            buy = limited
            feasibility.extend("buy:" + reason for reason in limited.reasons)
            assumptions.append("buy_prefunded_cash_limited")
    if base is not None:
        factor = (
            ONE + sell_fees.rate
            if sell_fees.rate is not None and sell_fees.asset_id == si.base.asset_id
            else ONE
        )
        limited = walk_book(
            sell_book,
            si,
            "sell",
            base_quantity=min(quantity, base / factor),
            fees=sell_fees,
            policy=policy,
            now=now,
        )
        if limited.filled_base_quantity < sell.filled_base_quantity:
            sell = limited
            feasibility.extend("sell:" + reason for reason in limited.reasons)
            assumptions.append("sell_prefunded_base_limited")
    common, grid_reasons = _common_quantity(buy, bi, sell, si)
    reasons.extend(grid_reasons)
    buy = walk_book(
        buy_book, bi, "buy", base_quantity=common, fees=buy_fees, policy=policy, now=now
    )
    sell = walk_book(
        sell_book,
        si,
        "sell",
        base_quantity=common,
        fees=sell_fees,
        policy=policy,
        now=now,
    )
    if (
        buy.filled_base_quantity != sell.filled_base_quantity
        or buy.filled_base_quantity != common
    ):
        # Recurring inverse divisions are bounded p80 values, never fuzzy equality.
        reasons.append("no_exact_common_quantity")
        common = ZERO
        buy = walk_book(
            buy_book,
            bi,
            "buy",
            base_quantity=ZERO,
            fees=buy_fees,
            policy=policy,
            now=now,
        )
        sell = walk_book(
            sell_book,
            si,
            "sell",
            base_quantity=ZERO,
            fees=sell_fees,
            policy=policy,
            now=now,
        )
    if not common:
        reasons.append("no_feasible_quantity")
        reasons.extend(feasibility)
    elif common < quantity:
        assumptions.append("requested_size_reduced_to_feasible_quantity")
    for prefix, estimate in (("buy", buy), ("sell", sell)):
        reasons.extend(prefix + ":" + reason for reason in estimate.reasons)
    gross: Decimal | None = None
    depth: Decimal | None = None
    buy_fee: Decimal | None = None
    sell_fee: Decimal | None = None
    fx_cost: Decimal | None = None
    latency: Decimal | None = None
    net: Decimal | None = None
    net_bps: Decimal | None = None
    conversions: list[FXConversion] = []
    # Mid conversion is only the decomposition reference, never executable FX.
    buy_mid = convert_fx(
        ONE,
        bi.quote.asset_id,
        currency,
        rates=rates,
        now=now,
        policy=policy,
        reference_mono_ns=buy_book.asof.receive_mono_ns,
    )
    sell_mid = convert_fx(
        ONE,
        si.quote.asset_id,
        currency,
        rates=rates,
        now=now,
        policy=policy,
        reference_mono_ns=sell_book.asof.receive_mono_ns,
    )
    reasons.extend(buy_mid.reasons + sell_mid.reasons)
    if (
        common
        and buy_mid.mid_equivalent is not None
        and sell_mid.mid_equivalent is not None
    ):
        bm, sm = buy_mid.mid_equivalent, sell_mid.mid_equivalent
        # Positive matched fills always carry their native price/slippage evidence.
        assert buy.best_price is not None and sell.best_price is not None
        assert buy.slippage_quote is not None and sell.slippage_quote is not None
        gross = common * (sell.best_price * sm - buy.best_price * bm)
        depth = buy.slippage_quote * bm + sell.slippage_quote * sm
        if buy.fee_quote_equivalent is not None:
            buy_fee = buy.fee_quote_equivalent * bm
        if sell.fee_quote_equivalent is not None:
            sell_fee = sell.fee_quote_equivalent * sm
        latency = (
            (buy.notional_quote * bm + sell.notional_quote * sm)
            * stress.latency_bps
            / BPS
        )
        if (
            buy.fee_quote_equivalent is not None
            and sell.fee_quote_equivalent is not None
        ):
            if (
                buy.fee_asset_id != bi.quote.asset_id
                or sell.fee_asset_id != si.quote.asset_id
            ):
                # Base-denominated fees alter the acquired/delivered asset balance.
                # A cash-only pair cannot hide that exposure behind a VWAP mark.
                reasons.append("non_quote_fee_requires_rebalance_conversion")
            funding = convert_fx(
                buy.notional_quote + buy.fee_quote_equivalent,
                currency,
                bi.quote.asset_id,
                rates=rates,
                now=now,
                policy=policy,
                required_input=True,
                reference_mono_ns=buy_book.asof.receive_mono_ns,
            )
            proceeds = convert_fx(
                sell.notional_quote - sell.fee_quote_equivalent,
                si.quote.asset_id,
                currency,
                rates=rates,
                now=now,
                policy=policy,
                reference_mono_ns=sell_book.asof.receive_mono_ns,
            )
            conversions.extend((funding, proceeds))
            reasons.extend(funding.reasons + proceeds.reasons)
            if funding.cost is not None and proceeds.cost is not None:
                fx_cost = funding.cost + proceeds.cost
                if not reasons:
                    assert funding.source_amount is not None
                    assert proceeds.target_amount is not None
                    # Ledger inputs are accumulated notionals, NOT rounded VWAP*q.
                    net = (
                        proceeds.target_amount
                        - funding.source_amount
                        - stress.rebalance_cost
                        - latency
                    )
                    if funding.source_amount:
                        net_bps = net / funding.source_amount * BPS
    if any(rate.assumed for rate in rates):
        assumptions.append("assumed_fx_rates")
    if rates:
        assumptions.append("fx_rate_capacity_assumed")
    if buy_fees.assumed or sell_fees.assumed:
        assumptions.append("user_assumed_fees")
    eligible = not reasons and net is not None
    executable = (
        eligible
        and cash is not None
        and base is not None
        and bi.execution_access == si.execution_access == "available"
        and bi.market_type == "spot"
    )
    return PairEstimate(
        bi.instrument_id,
        si.instrument_id,
        quantity,
        common,
        max(ZERO, quantity - common),
        currency,
        buy,
        sell,
        gross,
        depth,
        buy_fee,
        sell_fee,
        fx_cost,
        stress.rebalance_cost,
        latency,
        net,
        net_bps,
        eligible,
        executable,
        _unique(reasons),
        _unique(assumptions),
        tuple(conversions),
        inventory,
        stress,
        _unique(feasibility),
    )


def best_observed(
    entries: tuple[ComparisonEntry, ...],
    *,
    now: ClockReading,
    policy: EligibilityPolicy,
) -> tuple[BestObserved, ...]:
    """Separate exact native cohorts; align survivors to their newest receive as-of."""
    groups: dict[_CohortKey, list[ComparisonEntry]] = {}
    counts: dict[InstrumentId, int] = {}
    for entry in entries:
        groups.setdefault(_cohort_key(entry.instrument), []).append(entry)
        counts[entry.instrument.instrument_id] = (
            counts.get(entry.instrument.instrument_id, 0) + 1
        )
    results: list[BestObserved] = []
    for key, members in groups.items():
        valid: list[ComparisonEntry] = []
        excluded: list[ComparisonExclusion] = []
        for entry in members:
            gate = evaluate(entry.book, entry.instrument, "comparison", now, policy)
            reasons = list(gate.reasons)
            if counts[entry.instrument.instrument_id] > 1:
                reasons.append("ambiguous_book_key")
            if reasons:
                excluded.append(
                    ComparisonExclusion(
                        entry.instrument.instrument_id, _unique(reasons)
                    )
                )
            else:
                valid.append(entry)
        newest = max((entry.book.asof.receive_mono_ns for entry in valid), default=0)
        aligned: list[ComparisonEntry] = []
        for entry in valid:
            if newest - entry.book.asof.receive_mono_ns > policy.max_asof_skew_ns:
                excluded.append(
                    ComparisonExclusion(entry.instrument.instrument_id, ("asof_skew",))
                )
            else:
                aligned.append(entry)
        bid = max(aligned, key=lambda item: item.book.bids[0].price, default=None)
        ask = min(aligned, key=lambda item: item.book.asks[0].price, default=None)
        results.append(
            BestObserved(
                key[0],
                key[1],
                key[2],
                key[3],
                bid.book.bids[0].price if bid else None,
                ask.book.asks[0].price if ask else None,
                bid.instrument.instrument_id if bid else None,
                ask.instrument.instrument_id if ask else None,
                bid.book.asof if bid else None,
                ask.book.asof if ask else None,
                tuple(entry.instrument.instrument_id for entry in aligned),
                tuple(excluded),
            )
        )
    return tuple(results)


def compare_matrix(
    entries: tuple[ComparisonEntry, ...],
    *,
    base_quantity: Decimal,
    policy: EligibilityPolicy,
    now: ClockReading,
    numeraire: AssetId | None = None,
    fx_rates: tuple[FXRate, ...] = (),
    inventory: tuple[InventoryBalance, ...] = (),
    stress: StressAssumptions = StressAssumptions(),
) -> ComparisonMatrix:
    """Full directed N*N matrix, including explicit unavailable diagonal cells."""
    entries = tuple(entries)
    observed = best_observed(entries, now=now, policy=policy)
    exclusions = tuple(item for cohort in observed for item in cohort.exclusions)
    ambiguous = {
        item.instrument_id
        for item in exclusions
        if "ambiguous_book_key" in item.reasons
    }
    pairs = []
    for buy in entries:
        for sell in entries:
            pair = compare_pair(
                buy.book,
                buy.instrument,
                sell.book,
                sell.instrument,
                base_quantity=base_quantity,
                buy_fees=buy.fees,
                sell_fees=sell.fees,
                policy=policy,
                now=now,
                numeraire=numeraire,
                fx_rates=fx_rates,
                inventory=inventory,
                stress=stress,
            )
            if (
                buy.instrument.instrument_id in ambiguous
                or sell.instrument.instrument_id in ambiguous
            ):
                pair = replace(
                    pair,
                    eligible=False,
                    execution_eligible=False,
                    net=None,
                    net_bps=None,
                    reasons=_unique(pair.reasons + ("ambiguous_book_key",)),
                )
            pairs.append(pair)
    return ComparisonMatrix(tuple(pairs), observed, exclusions)
