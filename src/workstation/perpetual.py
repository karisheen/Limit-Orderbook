"""Pure typed perpetual models and economics; never venue risk guarantees.

Native quantities, settlement-denominated money, conservative executable-side book
marks and source-clock funding are deliberately separate from spot inventory.
"""

from dataclasses import dataclass
from decimal import Decimal

from .costs import _amounts, _valuation_reason
from .instruments import AssetId, Instrument, InstrumentId
from .precision import decimal, nonnegative_int, text, choice
from .quality import ClockReading

ZERO = Decimal("0")
MAX_PERPETUAL_RECORDS = 10_000


def _provenance(provenance: str, asof: int) -> None:
    text(provenance)
    nonnegative_int(asof)
    if provenance == "unknown":
        raise ValueError("explicit modeled provenance required")


@dataclass(frozen=True)
class PaperRiskModel:
    instrument_id: InstrumentId
    leverage: Decimal
    maintenance_margin_rate: Decimal
    liquidation_fee_rate: Decimal
    provenance: str
    asof_wall_ns: int

    def __post_init__(self):
        object.__setattr__(self, "instrument_id", InstrumentId(self.instrument_id))
        _provenance(self.provenance, self.asof_wall_ns)
        leverage = decimal(self.leverage, positive=True)
        maintenance = decimal(self.maintenance_margin_rate, nonnegative=True)
        penalty = decimal(self.liquidation_fee_rate, nonnegative=True)
        if (
            leverage < 1
            or leverage > 1000
            or maintenance >= 1 / leverage
            or penalty >= 1
        ):
            raise ValueError("invalid explicit isolated risk assumptions")
        object.__setattr__(self, "leverage", leverage)
        object.__setattr__(self, "maintenance_margin_rate", maintenance)
        object.__setattr__(self, "liquidation_fee_rate", penalty)


@dataclass(frozen=True)
class PaperFundingEvent:
    wall_ns: int
    rate: Decimal

    def __post_init__(self):
        nonnegative_int(self.wall_ns)
        rate = decimal(self.rate)
        if abs(rate) >= 1:
            raise ValueError("modeled funding rate must have magnitude below one")
        object.__setattr__(self, "rate", rate)


@dataclass(frozen=True)
class PaperFundingModel:
    instrument_id: InstrumentId
    mode: str
    provenance: str
    asof_wall_ns: int
    rate: Decimal | None = None
    interval_ns: int | None = None
    first_wall_ns: int | None = None
    events: tuple[PaperFundingEvent, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "instrument_id", InstrumentId(self.instrument_id))
        choice(self.mode, ("none", "periodic", "events"), "funding model")
        _provenance(self.provenance, self.asof_wall_ns)
        events = tuple(self.events)
        if len(events) > MAX_PERPETUAL_RECORDS or any(
            not isinstance(e, PaperFundingEvent) for e in events
        ):
            raise ValueError("invalid bounded funding events")
        if any(a.wall_ns >= b.wall_ns for a, b in zip(events, events[1:])):
            raise ValueError("funding events must be strictly chronological")
        object.__setattr__(self, "events", events)
        if self.mode == "periodic":
            if (
                self.rate is None
                or self.interval_ns is None
                or self.first_wall_ns is None
                or events
            ):
                raise ValueError(
                    "periodic funding requires rate, interval and first boundary"
                )
            event = PaperFundingEvent(self.first_wall_ns, self.rate)
            if nonnegative_int(self.interval_ns) < 1:
                raise ValueError("funding interval must be positive")
            object.__setattr__(self, "rate", event.rate)
            if self.first_wall_ns < self.asof_wall_ns:
                raise ValueError("funding boundary predates model")
        elif any(
            v is not None for v in (self.rate, self.interval_ns, self.first_wall_ns)
        ):
            raise ValueError("nonperiodic model cannot contain periodic inputs")
        if self.mode == "none" and events:
            raise ValueError("explicit no-funding model cannot contain events")
        if any(e.wall_ns < self.asof_wall_ns for e in events):
            raise ValueError("funding event predates model")


@dataclass(frozen=True)
class PaperPerpetualPosition:
    """Native signed exposure; money is in settlement_asset_id.

    initial_margin is the unreleased entry requirement; isolated_collateral also
    includes settled funding and realized close flows. realized_pnl is gross;
    fees_paid and liquidation_fees_paid are separate cumulative charges. Debt is
    a retained isolated deficit, never a debit of another position's collateral.
    """

    venue: str
    instrument_id: InstrumentId
    settlement_asset_id: AssetId
    instrument: Instrument
    quantity: Decimal = ZERO
    entry_price: Decimal | None = None
    initial_margin: Decimal = ZERO
    isolated_collateral: Decimal = ZERO
    realized_pnl: Decimal = ZERO
    fees_paid: Decimal = ZERO
    funding_paid: Decimal = ZERO
    liquidation_fees_paid: Decimal = ZERO
    debt: Decimal = ZERO
    unrealized_pnl: Decimal | None = None
    mark_price: Decimal | None = None
    maintenance_margin: Decimal | None = None
    equity: Decimal | None = None
    shortfall: Decimal | None = None
    liquidating: bool = False
    reasons: tuple[str, ...] = ()
    mark_source: str = "eligible_executable_side_book_proxy_not_exchange_mark"


@dataclass(frozen=True)
class PaperFundingPayment:
    """Positive amount debits isolated collateral; None is an unresolved payment."""

    instrument_id: InstrumentId
    settlement_asset_id: AssetId
    at: ClockReading
    rate: Decimal
    quantity: Decimal
    mark_price: Decimal | None
    amount: Decimal | None
    provenance: str
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class PaperFundingCursor:
    instrument_id: InstrumentId
    through_wall_ns: int


@dataclass(frozen=True)
class PaperLiquidation:
    """fee_amount is the protective penalty; fill_id links ordinary trading fees."""

    instrument_id: InstrumentId
    settlement_asset_id: AssetId
    at: ClockReading
    requested_quantity: Decimal
    filled_quantity: Decimal
    remaining_quantity: Decimal
    fee_amount: Decimal
    debt: Decimal
    reasons: tuple[str, ...]
    fill_id: str | None = None


def economics_reason(instrument: Instrument) -> str | None:
    reason = _valuation_reason(instrument)
    if reason:
        return reason
    if instrument.market_type != "perpetual" or instrument.settlement is None:
        return "unsupported_perpetual_or_contract_economics"
    if instrument.venue == "hyperliquid" and instrument.dex_namespace != "default":
        return "unsupported_perpetual_or_contract_economics"
    if instrument.constraints.size_rule != "fixed_lot":
        return "unsupported_size_rule"
    if instrument.inverse:
        supported = (
            instrument.settlement_conversion == "none"
            and instrument.settlement.asset_id == instrument.base.asset_id
        )
    else:
        supported = (
            instrument.settlement_conversion == "none"
            and instrument.settlement.asset_id == instrument.quote.asset_id
        ) or (
            instrument.settlement_conversion == "quanto_no_fx"
            and instrument.venue == "hyperliquid"
            and instrument.dex_namespace == "default"
            and instrument.contract_specification_source
            == "https://hyperliquid.gitbook.io/hyperliquid-docs/trading/contract-specifications"
        )
    return None if supported else "unsupported_perpetual_or_contract_economics"


def settlement_value(
    instrument: Instrument, quantity: Decimal, price: Decimal
) -> Decimal:
    base, quote = _amounts(instrument, price, abs(quantity))
    return base if instrument.inverse else quote


def pnl(position: PaperPerpetualPosition, quantity: Decimal, price: Decimal) -> Decimal:
    if not quantity or position.entry_price is None:
        return ZERO
    signed = quantity if position.quantity > 0 else -quantity
    multiplier = position.instrument.contract_multiplier
    if position.instrument.inverse:
        return signed * multiplier * (1 / position.entry_price - 1 / price)
    return signed * multiplier * (price - position.entry_price)
