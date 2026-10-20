"""Green power (轉供) statements from electricity retailers: read and check them.

A statement lists, per meter (使用電號) and energy type, the base price,
transfer and certificate rates, the per-kWh price, the transferred kWh and
the month's charge. The table is read from OCR lines by its header row, then
each line is checked:

* charge vs kWh × (base price + transfer rate + certificate rate), within a
  tolerance: retailers work out the charge from finer data (time-of-use or
  interval readings, each rounded), so a few NT$ of difference is normal;
* the printed per-kWh price is the charge ÷ kWh, shown for reference only.

No OCR happens here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Iterable, Sequence

from . import tables
from .keyword_extract import Line

# Each column and the header words naming it, most specific first.
COLUMNS: dict[str, tuple[str, ...]] = {
    "meter": ("使用電號", "用電電號", "電號"),
    "energy": ("能源別", "能源種類", "電源別", "能源"),
    "kwh": ("轉供度數", "轉供電量", "轉供量"),
    "transfer_rate": ("電能轉供費率", "轉供費率", "轉供費"),
    "certificate_rate": ("憑證費率", "憑證費"),
    "unit_price": ("每度單價", "平均單價"),
    "base": ("基準價", "基準單價", "購電單價", "電能單價"),
    "cost": ("本月電費", "本期電費", "應付電費", "電費金額", "電費"),
}
NUMERIC = ("base", "transfer_rate", "certificate_rate", "unit_price", "kwh", "cost")
# Energy types that are surcharges rather than transferred green power.
NOT_GREEN = ("餘電",)

_MONTH = re.compile(r"(\d{2,4})\s*[/.\-年]\s*(\d{1,2})\s*月?")


@dataclass
class StatementLine:
    meter: str | None
    energy: str | None
    base: Any = None
    transfer_rate: Any = None
    certificate_rate: Any = None
    unit_price: Any = None
    kwh: Any = None
    cost: Any = None
    page: int | None = None
    box: tuple[float, float, float, float] | None = None
    # Where each column's value is on the page.
    cells: dict[str, tuple[float, float, float, float]] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {
            "meter": self.meter, "energy": self.energy, "base": self.base, "transfer_rate": self.transfer_rate,
            "certificate_rate": self.certificate_rate, "unit_price": self.unit_price, "kwh": self.kwh,
            "cost": self.cost, "page": self.page, "box": list(self.box) if self.box else None,
            "cells": {name: list(box) for name, box in self.cells.items()},
        }


@dataclass
class Statement:
    lines: list[StatementLine] = field(default_factory=list)
    # Where each column's header is: (page, box), for evidence marks.
    headers: dict[str, tuple[int | None, tuple[float, float, float, float]]] = field(default_factory=dict)


def _number(text: str, integer: bool = False) -> int | float | None:
    return tables.number(text, integer)


def _column_of(text: str) -> str | None:
    return tables.match(text, COLUMNS)


# Whole numbers: a point in them is a thousands comma OCR misread.
_WHOLE = ("kwh", "cost")


def read_statement(lines: Iterable[Line]) -> Statement | None:
    """The statement's table, or None when there is no such table.

    A table needs a header with the transferred kWh, the charge and the
    meter. The lines may span pages: the table goes on over them, with the
    first page's columns where a page repeats no header.
    """
    lines = [line for line in lines if line.text.strip()]
    statement: Statement | None = None
    centres: dict[str, float] | None = None
    height = 0.0
    for page in sorted({line.page for line in lines}, key=lambda number: number or 0):
        on_page = [line for line in lines if line.page == page]
        table = None
        for anchor in (line for line in on_page if _column_of(line.text) == "kwh"):
            headers = tables.header(on_page, anchor, COLUMNS)
            if {"kwh", "cost", "meter"} <= headers.keys():
                table = (anchor, headers)
                break
        if table is not None:
            anchor, headers = table
            centres = {name: sum(l.x + l.width / 2 for l in found) / len(found) for name, found in headers.items()}
            top, height = max(line.y + line.height for found in headers.values() for line in found), anchor.height
            if statement is None:
                statement = Statement(headers={
                    name: (anchor.page, tables.union([line.box for line in found])) for name, found in headers.items()
                })
        elif statement is None or centres is None:
            continue
        else:
            top = float("-inf")  # carried on from the page before
        for cells in tables.rows(on_page, page, top, float("inf"), centres, height):
            meter = re.sub(r"\D", "", tables.text_of(cells, "meter"))
            values = {name: _number(tables.text_of(cells, name), name in _WHOLE) for name in NUMERIC if name in cells}
            if len(meter) < 6 or values.get("kwh") is None or values.get("cost") is None:
                continue
            statement.lines.append(StatementLine(
                meter=meter, energy=tables.text_of(cells, "energy").strip() or None, page=page,
                box=tables.union([line.box for found in cells.values() for line in found]),
                cells={name: tables.union([cell.box for cell in found]) for name, found in cells.items()},
                **values,
            ))
    return statement if statement is not None and statement.lines else None


def statement_period(lines: Iterable[Line]) -> str | None:
    """The statement's period: a range of dates, else a month (e.g. 113年01月 → the whole month)."""
    from .keyword_extract import parse_period

    lines = list(lines)
    for line in lines:
        if (period := parse_period(line.text)) is not None:
            return period
    for line in lines:
        if not re.search(r"期間|月份|年月|帳單月|計費月|電費月|本月", line.text):
            continue
        match = _MONTH.search(line.text)
        if match is None:
            continue
        year, month = int(match.group(1)), int(match.group(2))
        if not 1 <= month <= 12:
            continue
        import calendar

        days = calendar.monthrange(year + 1911 if year < 1000 else year, month)[1]
        return f"{year}/{month:02d}/01~{year}/{month:02d}/{days:02d}"
    return None


# ------------------------------------------------------------------- checks
def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def check_line(line: dict[str, Any], tolerance: Decimal | float = 3) -> dict[str, Any]:
    """The line's charge worked out from its rates, and how far the statement is from it."""
    kwh, cost = _decimal(line.get("kwh")), _decimal(line.get("cost"))
    rates = [_decimal(line.get(name)) for name in ("base", "transfer_rate", "certificate_rate")]
    note = None
    if rates[0] is None and _decimal(line.get("unit_price")) is not None:
        price = _decimal(line.get("unit_price"))
        note = "no base price: the printed per-kWh price was used"
    else:
        price = sum((rate for rate in rates if rate is not None), Decimal(0))
    if kwh is None or cost is None or not price:
        return {"expected": None, "difference": None, "status": "incomplete", "price": None, "note": "kWh, charge or rates missing"}
    expected = (kwh * price).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    difference = cost - expected
    status = "ok" if abs(difference) <= Decimal(str(tolerance)) else "check"
    return {
        "expected": int(expected), "difference": float(difference) if difference % 1 else int(difference),
        "status": status, "price": float(price), "charge_per_kwh": float((cost / kwh).quantize(Decimal("0.000001"))) if kwh else None,
        "note": note,
    }


def is_green(line: dict[str, Any]) -> bool:
    """Whether a line is transferred green power (not a surcharge such as 餘電加收)."""
    return not any(word in (line.get("energy") or "") for word in NOT_GREEN)
