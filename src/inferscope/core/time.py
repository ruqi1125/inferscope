"""精确且有界的秒到纳秒换算。"""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

MAX_NANOSECOND_DIGITS = 4096


def seconds_to_nanoseconds(seconds: Decimal, field: str) -> int:
    if not seconds.is_finite() or seconds < 0:
        raise ValueError(f"{field} 必须是有限的非负秒数")
    if seconds.is_zero():
        return 0

    adjusted = seconds.adjusted()
    if adjusted < -10:
        return 0
    if adjusted + 10 > MAX_NANOSECOND_DIGITS:
        raise ValueError(f"{field} 超出支持范围（纳秒值最多 {MAX_NANOSECOND_DIGITS} 位）")

    return round(Fraction(seconds) * 1_000_000_000)
