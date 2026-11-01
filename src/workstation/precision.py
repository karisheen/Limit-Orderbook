"""Bounded exact decimal parsing; incoming market values are never rounded."""

from dataclasses import dataclass
from decimal import Context, Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
import json
import re

DECIMAL_CONTEXT_VERSION = "decimal-v1-p80-half-even"
DECIMAL_CONTEXT = Context(prec=80, rounding=ROUND_HALF_EVEN)
MAX_MARKET_DATA_BYTES = 32 * 1024 * 1024
_DECIMAL_TEXT = re.compile(
    r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z"
)


@dataclass(frozen=True)
class DecimalPolicy:
    max_chars: int = 128
    max_digits: int = 38
    max_exponent: int = 38
    max_abs: Decimal = Decimal("1e30")

    def __post_init__(self):
        for value in (self.max_chars, self.max_digits, self.max_exponent):
            if type(value) is not int or value <= 0:
                raise ValueError("decimal policy bounds must be positive integers")
        if (
            not isinstance(self.max_abs, Decimal)
            or not self.max_abs.is_finite()
            or self.max_abs <= 0
        ):
            raise ValueError("invalid decimal magnitude bound")


DEFAULT_DECIMAL_POLICY = DecimalPolicy()


def decimal(
    value: str | int | Decimal,
    *,
    positive: bool = False,
    nonnegative: bool = False,
    policy: DecimalPolicy = DEFAULT_DECIMAL_POLICY,
) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError(
            "decimal requires original text, integer, or Decimal; floats forbidden"
        )
    if isinstance(value, str):
        if len(value) > policy.max_chars or not _DECIMAL_TEXT.fullmatch(value):
            raise ValueError("invalid decimal text")
    elif isinstance(value, int) and value.bit_length() > policy.max_digits * 4:
        raise ValueError("decimal integer too large")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("invalid decimal") from exc
    if not result.is_finite():
        raise ValueError("nonfinite decimal")
    parts = result.as_tuple()
    if (
        len(parts.digits) > policy.max_digits
        or abs(int(parts.exponent)) > policy.max_exponent
    ):
        raise ValueError("decimal precision/exponent exceeded")
    if result.copy_abs() > policy.max_abs:
        raise ValueError("decimal magnitude exceeded")
    if positive and result <= 0 or nonnegative and result < 0:
        raise ValueError("invalid decimal sign")
    return result


def canonical_decimal(value: Decimal) -> str:
    value = decimal(value)
    if not value:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def validate_increment(
    value: Decimal, increment: Decimal, *, name: str = "value"
) -> None:
    value, increment = decimal(value), decimal(increment, positive=True)
    with localcontext(DECIMAL_CONTEXT):
        if value % increment:
            raise ValueError(f"{name} is off increment")


def scaled_decimal(value: int, scale: int) -> Decimal:
    if type(value) is not int or type(scale) is not int or not 0 <= scale <= 38:
        raise ValueError("invalid scaled integer")
    if value.bit_length() > 128:
        raise ValueError("scaled integer overflow")
    with localcontext(DECIMAL_CONTEXT):
        return decimal(Decimal(value).scaleb(-scale))


def nonnegative_int(value: int, name: str = "integer") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def text(value: str, name: str = "identifier") -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 512
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError(f"invalid {name}")
    return value


def choice(value: str, allowed: tuple[str, ...], name: str) -> None:
    if value not in allowed:
        raise ValueError(f"invalid {name}: {value!r}")


def exact_json_loads(payload: bytes | str, *, max_bytes: int = MAX_MARKET_DATA_BYTES):
    if not isinstance(payload, (bytes, str)) or len(payload) > max_bytes:
        raise ValueError("JSON message size exceeded")

    def reject_constant(value):
        raise ValueError(f"nonfinite JSON number: {value}")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(
            payload,
            parse_float=decimal,
            parse_int=lambda s: int(decimal(s)),
            parse_constant=reject_constant,
            object_pairs_hook=pairs,
        )
    except (UnicodeError, RecursionError, json.JSONDecodeError) as exc:
        raise ValueError("malformed JSON") from exc
