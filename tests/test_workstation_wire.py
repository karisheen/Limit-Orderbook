"""Public wire values remain exact and independent under scalar/schema caching."""

from decimal import Decimal, localcontext

import pytest

from src.workstation.book import Level
from src.workstation.precision import DECIMAL_CONTEXT, DERIVED_DECIMAL_POLICY
from src.workstation.wire import dumps, loads, to_wire


class MisleadingDecimal(Decimal):
    def __str__(self):
        return "0"


def test_lossless_wire_preserves_numeric_equal_decimal_representations():
    values = tuple(Decimal(value) for value in ("1.0", "1.00", "1E+0", "-0", "0E-8"))
    restored = loads(dumps(values, canonical=False), tuple[Decimal, ...])
    assert tuple(value.as_tuple() for value in restored) == tuple(
        value.as_tuple() for value in values
    )
    assert to_wire(values) == ["1", "1", "1", "0", "0"]


@pytest.mark.parametrize("canonical", [True, False])
@pytest.mark.parametrize(
    "values",
    [
        (Decimal("0"), Decimal("0E-39")),
        (Decimal("1"), Decimal("1." + "0" * 38)),
        (Decimal("0"), MisleadingDecimal("0E-39")),
    ],
)
def test_wire_cache_cannot_bypass_native_decimal_bounds(values, canonical):
    with pytest.raises(ValueError):
        dumps(values, canonical=canonical)


def test_decimal_subclass_text_cannot_change_authoritative_wire_value():
    encoded = dumps((Decimal("0"), MisleadingDecimal("2.00")), canonical=False)
    restored = loads(encoded, tuple[Decimal, ...])
    assert tuple(value.as_tuple() for value in restored) == (
        Decimal("0").as_tuple(),
        Decimal("2.00").as_tuple(),
    )


def test_wire_output_mutation_cannot_corrupt_later_publication():
    level = Level(Decimal("1.00"), Decimal("2.00"))
    first = to_wire(level, canonical=False)
    first["price"] = "999"
    first.pop("$type")
    restored = loads(dumps(level, canonical=False), Level)
    assert restored.price.as_tuple() == Decimal("1.00").as_tuple()
    assert restored.quantity.as_tuple() == Decimal("2.00").as_tuple()


def test_recurring_derived_value_roundtrips_without_relaxing_native_default():
    with localcontext(DECIMAL_CONTEXT):
        value = Decimal(1) / Decimal(7)
    encoded = dumps((value, None), decimal_policy=DERIVED_DECIMAL_POLICY)
    restored = loads(
        encoded, tuple[Decimal, Decimal | None], decimal_policy=DERIVED_DECIMAL_POLICY
    )
    assert restored[0].as_tuple() == value.as_tuple()
    with pytest.raises(ValueError):
        dumps(value)
    with pytest.raises(ValueError):
        loads(encoded, tuple[Decimal, Decimal | None])


@pytest.mark.parametrize(
    "text", ["1." + "2" * 80, "1E-257", "1E121", "NaN", "Infinity"]
)
def test_derived_output_bounds_are_enforced_in_both_directions(text):
    with pytest.raises(ValueError):
        dumps(Decimal(text), decimal_policy=DERIVED_DECIMAL_POLICY)
    encoded = ('{"schema_version":1,"value":"' + text + '"}').encode()
    with pytest.raises(ValueError):
        loads(encoded, Decimal, decimal_policy=DERIVED_DECIMAL_POLICY)


def test_derived_wire_policy_cannot_weaken_native_level_constructor():
    encoded = (
        '{"schema_version":1,"value":{"$type":"Level","price":"0.'
        + "1" * 80
        + '","quantity":"1"}}'
    ).encode()
    with pytest.raises(ValueError):
        loads(encoded, Level, decimal_policy=DERIVED_DECIMAL_POLICY)
