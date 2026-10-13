"""Pure helpers for the batch-inference business-key boundary."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any


BUSINESS_KEY_MAX_CHARS = 512
BUSINESS_KEY_DECIMAL_MAX_PRECISION = 38
BUSINESS_KEY_DECIMAL_MAX_SCALE = 18
BUSINESS_KEY_PATTERN = re.compile(r"^-?[A-Za-z0-9][A-Za-z0-9._:@/-]{0,511}$")
SUPPORTED_BUSINESS_KEY_SPARK_TYPES = frozenset(
    {"string", "tinyint", "smallint", "int", "bigint"}
)
DECIMAL_SPARK_TYPE_PATTERN = re.compile(r"^decimal\(([0-9]+),([0-9]+)\)$")
INTEGRAL_BOUNDS = {
    "tinyint": (-(1 << 7), (1 << 7) - 1),
    "smallint": (-(1 << 15), (1 << 15) - 1),
    "int": (-(1 << 31), (1 << 31) - 1),
    "bigint": (-(1 << 63), (1 << 63) - 1),
}


def is_supported_spark_type_name(type_name: Any) -> bool:
    """Return whether a Spark simpleString is in the closed key type set."""
    if not isinstance(type_name, str) or type_name != type_name.strip():
        return False
    if type_name in SUPPORTED_BUSINESS_KEY_SPARK_TYPES:
        return True
    match = DECIMAL_SPARK_TYPE_PATTERN.fullmatch(type_name)
    if match is None:
        return False
    precision_text, scale_text = match.groups()
    if len(precision_text) > 2 or len(scale_text) > 2:
        return False
    precision, scale = int(precision_text), int(scale_text)
    return 1 <= precision <= BUSINESS_KEY_DECIMAL_MAX_PRECISION and 0 <= scale <= min(
        precision, BUSINESS_KEY_DECIMAL_MAX_SCALE
    )


def _decimal_contract(type_name: str) -> tuple[int, int] | None:
    match = DECIMAL_SPARK_TYPE_PATTERN.fullmatch(type_name)
    if match is None:
        return None
    precision, scale = (int(part) for part in match.groups())
    return precision, scale


def _canonical_decimal(value: Decimal, precision: int, scale: int) -> str | None:
    if not value.is_finite():
        return None
    sign, digits_tuple, exponent = value.as_tuple()
    if not isinstance(exponent, int):
        return None
    digits = list(digits_tuple)
    if not any(digits):
        return "0"
    trailing_zeros = 0
    while digits[-1] == 0:
        digits.pop()
        trailing_zeros += 1
    effective_exponent = exponent + trailing_zeros
    required_scale = max(-effective_exponent, 0)
    integer_digits = max(len(digits) + effective_exponent, 0)
    if required_scale > scale or integer_digits > precision - scale:
        return None

    digit_text = "".join(str(digit) for digit in digits)
    if effective_exponent >= 0:
        text = digit_text + ("0" * effective_exponent)
    else:
        decimal_position = len(digit_text) + effective_exponent
        if decimal_position > 0:
            text = digit_text[:decimal_position] + "." + digit_text[decimal_position:]
        else:
            text = "0." + ("0" * -decimal_position) + digit_text
    return ("-" if sign else "") + text


def canonical_business_key(value: Any, spark_type_name: Any) -> str | None:
    """Canonicalize one supported scalar value or return None when invalid."""
    if not is_supported_spark_type_name(spark_type_name):
        return None
    if spark_type_name == "string":
        if not isinstance(value, str):
            return None
        text = value
    elif spark_type_name.startswith("decimal("):
        if not isinstance(value, Decimal):
            return None
        contract = _decimal_contract(spark_type_name)
        if contract is None:
            return None
        text = _canonical_decimal(value, *contract)
        if text is None:
            return None
    else:
        if type(value) is not int:
            return None
        lower, upper = INTEGRAL_BOUNDS[spark_type_name]
        if not lower <= value <= upper:
            return None
        text = str(value)
    if len(text) > BUSINESS_KEY_MAX_CHARS or text != text.strip():
        return None
    if BUSINESS_KEY_PATTERN.fullmatch(text) is None:
        return None
    return text
