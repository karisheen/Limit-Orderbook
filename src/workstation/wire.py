"""Explicit tagged schema: decimal and integer fields are JSON strings, never floats."""

import base64
import binascii
from dataclasses import fields, is_dataclass
from decimal import Decimal
from functools import lru_cache
import json
import types
from typing import Union, get_args, get_origin, get_type_hints

from .precision import (
    DEFAULT_DECIMAL_POLICY,
    DecimalPolicy,
    canonical_decimal,
    decimal,
    exact_json_loads,
)

WIRE_SCHEMA_VERSION = 1
_DECIMAL_MEMO_LIMIT = 8192


@lru_cache(maxsize=1)
def _registry() -> dict[str, type]:
    from . import analytics, book, costs, events, instruments, precision, quality

    result: dict[str, type] = {}
    for module in (book, events, instruments, precision, quality, analytics, costs):
        for name, cls in vars(module).items():
            if (
                isinstance(cls, type)
                and is_dataclass(cls)
                and cls.__module__ == module.__name__
            ):
                result[name] = cls
    return result


@lru_cache(maxsize=128)
def _record_hints(record_type):
    return get_type_hints(record_type)


@lru_cache(maxsize=128)
def _record_fields(record_type: type) -> tuple[tuple[str, ...], frozenset[str]]:
    names = tuple(field.name for field in fields(record_type))
    return names, frozenset((*names, "$type"))


def to_wire(
    value,
    *,
    canonical: bool = True,
    decimal_policy: DecimalPolicy = DEFAULT_DECIMAL_POLICY,
):
    # Only lexical scalar strings are memoized, for this one call. Numeric Decimal
    # equality would merge distinct scales and can even bypass exponent bounds.
    return _to_wire(value, canonical, {}, decimal_policy)


def _to_wire(value, canonical, decimal_memo, decimal_policy):
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, Decimal):
        lexical = str(Decimal(value))
        encoded = decimal_memo.get(lexical)
        if encoded is None:
            encoded = (
                canonical_decimal(value, policy=decimal_policy)
                if canonical
                else str(decimal(value, policy=decimal_policy))
            )
            if len(decimal_memo) < _DECIMAL_MEMO_LIMIT:
                decimal_memo[lexical] = encoded
        return encoded
    if isinstance(value, int):
        return str(value)
    if isinstance(value, bytes):
        return {"$bytes": base64.b64encode(value).decode("ascii")}
    if (
        is_dataclass(value)
        and type(value).__name__ in _registry()
        and _registry()[type(value).__name__] is type(value)
    ):
        return {
            "$type": type(value).__name__,
            **{
                name: _to_wire(
                    getattr(value, name), canonical, decimal_memo, decimal_policy
                )
                for name in _record_fields(_registry()[type(value).__name__])[0]
            },
        }
    if isinstance(value, (tuple, frozenset)):
        items = sorted(value) if isinstance(value, frozenset) else value
        return [
            _to_wire(item, canonical, decimal_memo, decimal_policy) for item in items
        ]
    raise ValueError(f"unsupported wire type: {type(value).__name__}")


def _decode(value, expected, decimal_policy):
    origin, args = get_origin(expected), get_args(expected)
    if origin in (Union, types.UnionType):
        for option in args:
            try:
                return _decode(value, option, decimal_policy)
            except (ValueError, TypeError):
                continue
        raise ValueError("wire value does not match union")
    if expected is type(None):
        if value is not None:
            raise ValueError("expected null")
        return None
    if origin is not None and str(origin) == "typing.Literal":
        if value not in args:
            raise ValueError("invalid literal")
        return value
    if origin in (tuple, frozenset):
        if not isinstance(value, list):
            raise ValueError("expected wire array")
        if origin is frozenset:
            result = frozenset(_decode(item, args[0], decimal_policy) for item in value)
            if len(result) != len(value):
                raise ValueError("duplicate set member")
            return result
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode(item, args[0], decimal_policy) for item in value)
        if len(value) != len(args):
            raise ValueError("tuple length mismatch")
        return tuple(
            _decode(item, kind, decimal_policy)
            for item, kind in zip(value, args, strict=True)
        )
    if expected is Decimal:
        if not isinstance(value, str):
            raise ValueError("decimal wire value must be text")
        return decimal(value, policy=decimal_policy)
    if expected is bytes:
        if not isinstance(value, dict) or set(value) != {"$bytes"}:
            raise ValueError("expected encoded bytes")
        try:
            return base64.b64decode(value["$bytes"], validate=True)
        except (ValueError, TypeError, binascii.Error) as exc:
            raise ValueError("invalid bytes") from exc
    if isinstance(expected, type) and issubclass(expected, bool):
        if type(value) is not bool:
            raise ValueError("expected boolean")
        return value
    if isinstance(expected, type) and issubclass(expected, int):
        if (
            not isinstance(value, str)
            or len(value) > 128
            or not value
            or (value[0] == "-" and not value[1:].isascii())
            or not value.lstrip("-").isdigit()
        ):
            raise ValueError("integer wire value must be decimal text")
        return expected(int(value))
    if isinstance(expected, type) and issubclass(expected, str):
        if not isinstance(value, str):
            raise ValueError("expected text")
        return expected(value)
    if isinstance(expected, type) and is_dataclass(expected):
        if not isinstance(value, dict) or value.get("$type") != expected.__name__:
            raise ValueError("incorrect wire record type")
        names, allowed = _record_fields(expected)
        if value.keys() != allowed:
            raise ValueError("missing/unknown wire fields")
        hints = _record_hints(expected)
        return expected(
            **{
                name: _decode(value[name], hints[name], decimal_policy)
                for name in names
            }
        )
    raise ValueError(f"unsupported wire annotation: {expected}")


def from_wire(
    value,
    expected_type=None,
    *,
    decimal_policy: DecimalPolicy = DEFAULT_DECIMAL_POLICY,
):
    if expected_type is None:
        if not isinstance(value, dict) or value.get("$type") not in _registry():
            raise ValueError("unknown wire record")
        expected_type = _registry()[value["$type"]]
    return _decode(value, expected_type, decimal_policy)


def dumps(
    value,
    *,
    canonical: bool = True,
    decimal_policy: DecimalPolicy = DEFAULT_DECIMAL_POLICY,
) -> bytes:
    return json.dumps(
        {
            "schema_version": WIRE_SCHEMA_VERSION,
            "value": to_wire(value, canonical=canonical, decimal_policy=decimal_policy),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def loads(
    payload: bytes | str,
    expected_type=None,
    *,
    max_bytes: int = 268_435_456,
    decimal_policy: DecimalPolicy = DEFAULT_DECIMAL_POLICY,
):
    value = exact_json_loads(payload, max_bytes=max_bytes)
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "value"}
        or type(value["schema_version"]) is not int
        or value["schema_version"] != WIRE_SCHEMA_VERSION
    ):
        raise ValueError("unsupported wire schema")
    return from_wire(value["value"], expected_type, decimal_policy=decimal_policy)
