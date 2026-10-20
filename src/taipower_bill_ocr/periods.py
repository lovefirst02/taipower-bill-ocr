"""Split a billing period's amounts across the calendar months it covers.

A bill for 113/01/15~113/02/14 is shared by January (17 days) and February
(14 days) in proportion to days. Amounts are split exactly: decimal
arithmetic, and the remainder of rounding goes to the months with the
largest fractions, so the parts always add up to the original amount.

Days are counted inclusively, as on Taipower bills (113/01/01~113/01/31 is
31 days; the next bill starts on 02/01).
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

_DATE = re.compile(r"(\d{2,4})\s*[/.\-年]\s*(\d{1,2})\s*[/.\-月]\s*(\d{1,2})")


def parse_date(text: str) -> date | None:
    """A date written with a ROC (113) or western (2024) year."""
    match = _DATE.search(text)
    if match is None:
        return None
    year, month, day = (int(part) for part in match.groups())
    if year < 1000:
        year += 1911
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_period(period: str | None) -> tuple[date, date] | None:
    """The first and last day of a period such as "113/01/15~113/02/14"."""
    if not period:
        return None
    dates = [parsed for match in _DATE.finditer(period) if (parsed := parse_date(match.group()))]
    if len(dates) < 2 or dates[1] < dates[0]:
        return None
    return dates[0], dates[1]


@dataclass(frozen=True)
class MonthShare:
    month: str  # "2024-01"
    days: int
    amounts: dict[str, Any]


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _places(value: Decimal) -> int:
    """Decimal places of the original amount: its parts keep the same precision."""
    exponent = value.normalize().as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0


def _split(total: Decimal, weights: list[int]) -> list[Decimal]:
    """``total`` in proportion to ``weights``, in its own precision, adding up exactly."""
    places = _places(total)
    unit = Decimal(1).scaleb(-places)
    whole = sum(weights)
    exact = [total * weight / whole for weight in weights]
    # Round towards zero, then hand out the remaining units by largest fraction.
    sign = -1 if total < 0 else 1
    floored = [(value / unit).to_integral_value(rounding="ROUND_DOWN") * unit for value in exact]
    remainder = int(((total - sum(floored)) / unit).to_integral_value())
    order = sorted(range(len(exact)), key=lambda index: (-(abs(exact[index] - floored[index])), index))
    for index in order[: abs(remainder)]:
        floored[index] += unit * sign
    return floored


def _plain(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)


def split_by_month(period: str | None, amounts: Mapping[str, Any]) -> list[MonthShare]:
    """Each month's days and share of ``amounts`` (amounts that are None stay None).

    Returns [] when the period cannot be read.
    """
    parsed = parse_period(period)
    if parsed is None:
        return []
    start, end = parsed
    months: list[tuple[str, int]] = []
    day = start
    while day <= end:
        last = date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])
        stop = min(last, end)
        months.append((f"{day.year:04d}-{day.month:02d}", (stop - day).days + 1))
        day = stop + timedelta(days=1)
    weights = [days for _, days in months]
    split: dict[str, list[Any]] = {}
    for name, value in amounts.items():
        number = _decimal(value)
        split[name] = [None] * len(months) if number is None else [_plain(part) for part in _split(number, weights)]
    return [
        MonthShare(month, days, {name: parts[index] for name, parts in split.items()})
        for index, (month, days) in enumerate(months)
    ]
