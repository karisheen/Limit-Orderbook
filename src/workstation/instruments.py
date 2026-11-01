"""Immutable product metadata and explicit, version-preserving resolution."""

from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import ClassVar

from .precision import choice, decimal, nonnegative_int, text, validate_increment


class Identifier(str):
    label: ClassVar[str] = "identifier"

    def __new__(cls, value: str):
        return super().__new__(cls, text(value, cls.label))


class InstrumentId(Identifier):
    pass


class AssetId(Identifier):
    pass


@dataclass(frozen=True)
class AssetIdentity:
    asset_id: AssetId
    ticker: str
    native_token_id: str | None = None
    network: str | None = None
    contract_address: str | None = None
    reference_asset_id: AssetId | None = None

    def __post_init__(self):
        object.__setattr__(self, "asset_id", AssetId(self.asset_id))
        text(self.ticker)
        for value in (self.native_token_id, self.network, self.contract_address):
            if value is not None:
                text(value)
        if self.reference_asset_id is not None:
            object.__setattr__(
                self, "reference_asset_id", AssetId(self.reference_asset_id)
            )


@dataclass(frozen=True)
class OrderConstraints:
    min_base: Decimal | None = None
    max_base: Decimal | None = None
    min_notional: Decimal | None = None
    max_notional: Decimal | None = None
    min_notional_currency: AssetId | None = None
    price_rule: str = "fixed_tick"
    size_rule: str = "fixed_lot"
    max_limit_base: Decimal | None = None
    max_market_base: Decimal | None = None

    def __post_init__(self):
        for name in (
            "min_base",
            "max_base",
            "min_notional",
            "max_notional",
            "max_limit_base",
            "max_market_base",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, decimal(value, nonnegative=True))
        for low, high in (
            (self.min_base, self.max_base),
            (self.min_base, self.max_limit_base),
            (self.min_base, self.max_market_base),
            (self.min_notional, self.max_notional),
        ):
            if low is not None and high is not None and low > high:
                raise ValueError("inverted order constraints")
        text(self.price_rule)
        text(self.size_rule)
        if self.min_notional_currency is not None:
            object.__setattr__(
                self, "min_notional_currency", AssetId(self.min_notional_currency)
            )


@dataclass(frozen=True)
class Instrument:
    instrument_id: InstrumentId
    metadata_version: str
    venue: str
    api_family: str
    native_symbol: str
    base: AssetIdentity
    quote: AssetIdentity
    tick_size: Decimal | None
    lot_size: Decimal
    native_asset_id: str | None = None
    dex_namespace: str | None = None
    settlement: AssetIdentity | None = None
    market_type: str = "spot"
    expiry_wall_ns: int | None = None
    constraints: OrderConstraints = field(default_factory=OrderConstraints)
    contract_multiplier: Decimal = Decimal("1")
    quantity_unit: str = "base"
    contract_value_currency: AssetId | None = None
    inverse: bool = False
    status: str = "active"
    data_access: str = "unknown"
    execution_access: str = "unknown"
    metadata_asof_wall_ns: int = 0
    metadata_source: str = "unspecified"
    reference_multiplier: Decimal | None = None
    settlement_conversion: str = "unknown"
    contract_specification_source: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "instrument_id", InstrumentId(self.instrument_id))
        for required_text in (
            self.metadata_version,
            self.venue,
            self.api_family,
            self.native_symbol,
            self.metadata_source,
        ):
            text(required_text)
        for optional_text in (
            self.native_asset_id,
            self.dex_namespace,
            self.contract_specification_source,
        ):
            if optional_text is not None:
                text(optional_text)
        if not isinstance(self.base, AssetIdentity) or not isinstance(
            self.quote, AssetIdentity
        ):
            raise ValueError("instrument assets must be identities")
        if self.settlement is not None and not isinstance(
            self.settlement, AssetIdentity
        ):
            raise ValueError("invalid settlement identity")
        if not isinstance(self.constraints, OrderConstraints):
            raise ValueError("invalid order constraints")
        choice(self.market_type, ("spot", "perpetual", "future"), "market type")
        choice(self.quantity_unit, ("base", "contracts"), "quantity unit")
        choice(
            self.settlement_conversion,
            ("unknown", "none", "quanto_no_fx"),
            "settlement conversion",
        )
        if self.settlement_conversion == "quanto_no_fx" and (
            self.market_type == "spot" or self.settlement is None
        ):
            raise ValueError("quanto conversion requires a derivative settlement")
        choice(
            self.status,
            ("active", "suspended", "delisted", "unavailable", "unknown"),
            "status",
        )
        for value in (self.data_access, self.execution_access):
            choice(value, ("available", "blocked", "unsupported", "unknown"), "access")
        if type(self.inverse) is not bool:
            raise ValueError("inverse must be boolean")
        if self.contract_value_currency is not None:
            object.__setattr__(
                self, "contract_value_currency", AssetId(self.contract_value_currency)
            )
        if self.tick_size is not None:
            object.__setattr__(
                self, "tick_size", decimal(self.tick_size, positive=True)
            )
        elif self.constraints.price_rule == "fixed_tick":
            raise ValueError("fixed_tick requires a known tick size")
        object.__setattr__(self, "lot_size", decimal(self.lot_size, positive=True))
        object.__setattr__(
            self,
            "contract_multiplier",
            decimal(self.contract_multiplier, positive=True),
        )
        if self.reference_multiplier is not None:
            object.__setattr__(
                self,
                "reference_multiplier",
                decimal(self.reference_multiplier, positive=True),
            )
        nonnegative_int(self.metadata_asof_wall_ns)
        if self.expiry_wall_ns is not None:
            nonnegative_int(self.expiry_wall_ns)
        if self.market_type == "future" and self.expiry_wall_ns is None:
            raise ValueError("future requires expiry")

    def validate_price(self, price: Decimal) -> None:
        decimal(price, positive=True)
        if self.tick_size is not None:
            validate_increment(price, self.tick_size, name="price")
        elif self.constraints.price_rule != "unconstrained":
            raise ValueError("variable price rule requires protocol price validator")

    def validate_quantity(
        self, quantity: Decimal, *, incremental: bool = False
    ) -> None:
        decimal(quantity, nonnegative=not incremental)
        validate_increment(quantity, self.lot_size, name="quantity")

    def validate_order(
        self, price: Decimal, quantity: Decimal, *, order_type: str
    ) -> None:
        choice(order_type, ("limit", "market"), "order type")
        self.validate_price(price)
        self.validate_quantity(quantity)
        decimal(quantity, positive=True)
        from decimal import localcontext
        from .precision import DECIMAL_CONTEXT

        with localcontext(DECIMAL_CONTEXT):
            notional = price * quantity * self.contract_multiplier
        if self.inverse or self.quantity_unit == "contracts":
            raise ValueError("contract orders require explicit contract valuation")
        for value, low, high in (
            (quantity, self.constraints.min_base, self.constraints.max_base),
            (
                quantity,
                None,
                self.constraints.max_limit_base
                if order_type == "limit"
                else self.constraints.max_market_base,
            ),
            (notional, self.constraints.min_notional, self.constraints.max_notional),
        ):
            if low is not None and value < low or high is not None and value > high:
                raise ValueError("order outside instrument constraints")
        if (
            self.constraints.min_notional is not None
            or self.constraints.max_notional is not None
        ) and self.constraints.min_notional_currency != self.quote.asset_id:
            raise ValueError("notional constraints require explicit quote currency")


@dataclass(frozen=True)
class InstrumentQuery:
    base_id: AssetId | None = None
    venue: str | None = None
    market_type: str | None = None
    quote_id: AssetId | None = None
    status: str | None = None


@dataclass(frozen=True)
class MetadataSnapshot:
    source: str
    fetch_wall_ns: int
    payload_hash: str
    instruments: tuple[Instrument, ...]

    def __post_init__(self):
        text(self.source)
        text(self.payload_hash)
        nonnegative_int(self.fetch_wall_ns)
        object.__setattr__(self, "instruments", tuple(self.instruments))
        if any(not isinstance(item, Instrument) for item in self.instruments):
            raise ValueError("invalid metadata instrument")


class UnknownInstrument(LookupError):
    pass


class InstrumentCatalog:
    def __init__(self):
        self._versions: dict[tuple[str, str], Instrument] = {}
        self._latest: dict[str, Instrument] = {}

    def apply_metadata(self, snapshot: MetadataSnapshot) -> tuple[Instrument, ...]:
        versions, latest = self._versions.copy(), self._latest.copy()
        for instrument in snapshot.instruments:
            key = (instrument.instrument_id, instrument.metadata_version)
            if key in versions and versions[key] != instrument:
                raise ValueError("metadata version is immutable")
            old = latest.get(instrument.instrument_id)
            if old is not None and self._identity(old) != self._identity(instrument):
                raise ValueError("stable instrument identity changed")
            native = (instrument.venue, instrument.api_family, instrument.native_symbol)
            if any(
                (item.venue, item.api_family, item.native_symbol) == native
                and item.instrument_id != instrument.instrument_id
                for item in latest.values()
            ):
                raise ValueError("ambiguous native instrument")
            versions[key] = instrument
            if (
                old is None
                or instrument.metadata_asof_wall_ns >= old.metadata_asof_wall_ns
            ):
                latest[instrument.instrument_id] = instrument
        self._versions, self._latest = versions, latest
        return snapshot.instruments

    @staticmethod
    def _identity(item: Instrument):
        return (
            item.venue,
            item.api_family,
            item.native_symbol,
            item.native_asset_id,
            item.dex_namespace,
            item.base.asset_id,
            item.quote.asset_id,
            item.market_type,
            item.settlement.asset_id if item.settlement else None,
            item.expiry_wall_ns,
        )

    def get(
        self, instrument_id: str, metadata_version: str | None = None
    ) -> Instrument:
        try:
            return (
                self._latest[instrument_id]
                if metadata_version is None
                else self._versions[(instrument_id, metadata_version)]
            )
        except KeyError as exc:
            raise UnknownInstrument((instrument_id, metadata_version)) from exc

    def resolve(
        self,
        venue: str,
        api_family: str,
        native_symbol: str,
        metadata_version: str | None = None,
    ) -> Instrument:
        for item in self._latest.values():
            if (item.venue, item.api_family, item.native_symbol) == (
                venue,
                api_family,
                native_symbol,
            ):
                return self.get(item.instrument_id, metadata_version)
        raise UnknownInstrument((venue, api_family, native_symbol, metadata_version))

    def list(
        self, query: InstrumentQuery = InstrumentQuery()
    ) -> tuple[Instrument, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self._latest.values()
                    if (query.base_id is None or item.base.asset_id == query.base_id)
                    and (
                        query.quote_id is None or item.quote.asset_id == query.quote_id
                    )
                    and (query.venue is None or item.venue == query.venue)
                    and (
                        query.market_type is None
                        or item.market_type == query.market_type
                    )
                    and (query.status is None or item.status == query.status)
                ),
                key=lambda item: item.instrument_id,
            )
        )


def synthetic_instrument(
    *,
    venue: str = "synthetic",
    base: str = "BTC",
    quote: str = "USD",
    tick_size: str = "0.01",
    lot_size: str = "0.001",
    **overrides,
) -> Instrument:
    """Explicitly synthetic metadata for offline fixtures, never product discovery."""
    instrument = Instrument(
        instrument_id=InstrumentId(f"{venue}:spot:{base}-{quote}"),
        metadata_version="synthetic-v1",
        venue=venue,
        api_family="synthetic",
        native_symbol=f"{base}-{quote}",
        base=AssetIdentity(AssetId(base), base),
        quote=AssetIdentity(AssetId(quote), quote),
        tick_size=decimal(tick_size),
        lot_size=decimal(lot_size),
        data_access="available",
        execution_access="unknown",
        metadata_source="synthetic",
    )
    return replace(instrument, **overrides) if overrides else instrument
