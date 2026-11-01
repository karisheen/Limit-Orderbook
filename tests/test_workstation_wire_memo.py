"""Wire scalar reuse preserves policy, representation, and mutable output isolation."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from src.workstation.book import Level
from src.workstation.precision import DEFAULT_DECIMAL_POLICY, DecimalPolicy
from src.workstation.wire import dumps, loads, to_wire


@pytest.mark.parametrize("canonical", [True, False])
def test_wide_policy_repeated_values_preserve_exact_wire_bytes(canonical):
    policy = DecimalPolicy(
        max_chars=2048, max_digits=640, max_exponent=640, max_abs=Decimal("1e640")
    )
    text = "0." + "1" * 600
    value = Decimal(text)
    encoded = dumps((value, value), canonical=canonical, decimal_policy=policy)
    assert (
        encoded
        == ('{"schema_version":1,"value":["' + text + '","' + text + '"]}').encode()
    )
    restored = loads(encoded, tuple[Decimal, ...], decimal_policy=policy)
    assert tuple(item.as_tuple() for item in restored) == (
        value.as_tuple(),
        value.as_tuple(),
    )
    # max_chars restricts original text inputs, not existing Decimal values.
    short_text_policy = DecimalPolicy(
        max_chars=1, max_digits=640, max_exponent=640, max_abs=Decimal("1e640")
    )
    assert (
        dumps((value, value), canonical=canonical, decimal_policy=short_text_policy)
        == encoded
    )
    with pytest.raises(ValueError, match="precision/exponent"):
        dumps((value, value), canonical=canonical)
    with pytest.raises(ValueError, match="precision/exponent"):
        dumps(
            (value, value, Decimal("0"), Decimal("0E-641")),
            canonical=canonical,
            decimal_policy=policy,
        )


@pytest.mark.parametrize("canonical", [True, False])
def test_shared_record_aliases_produce_independent_mutable_wire_outputs(canonical):
    level = Level(Decimal("1.00"), Decimal("2.00"))
    shared = (level, level)
    first = to_wire((shared, shared), canonical=canonical)
    expected_level = {
        "$type": "Level",
        "price": "1" if canonical else "1.00",
        "quantity": "2" if canonical else "2.00",
    }
    first[0][0]["price"] = "999"
    first[0].append(None)
    assert first[0][1] == expected_level
    assert first[1] == [expected_level, expected_level]
    assert to_wire(shared, canonical=canonical) == [expected_level, expected_level]


def test_concurrent_wire_calls_keep_decimal_policy_and_mode_separate():
    strict = DecimalPolicy(max_exponent=1)
    values = (Decimal("1.00"), Decimal("-0.00"))

    def encode(case):
        canonical, restrictive = case
        policy = strict if restrictive else DEFAULT_DECIMAL_POLICY
        if restrictive:
            with pytest.raises(ValueError, match="precision/exponent"):
                dumps(values, canonical=canonical, decimal_policy=policy)
            return
        expected = (
            b'{"schema_version":1,"value":["1","0"]}'
            if canonical
            else b'{"schema_version":1,"value":["1.00","-0.00"]}'
        )
        assert dumps(values, canonical=canonical, decimal_policy=policy) == expected

    cases = [(True, False), (False, False), (True, True), (False, True)] * 8
    with ThreadPoolExecutor(max_workers=4) as executor:
        tuple(executor.map(encode, cases))
