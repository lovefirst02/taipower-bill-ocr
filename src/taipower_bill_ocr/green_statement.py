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

from .keyword_extract import Line, compact

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


def _number(text: str) -> int | float | None:
    """A number in a table cell, ignoring a stray character or two (e.g. a cursor read as "I")."""
    flat = re.sub(r"[,，\s]", "", text)
    cleaned = re.sub(r"[^\d.\-]", "", flat)
    digits = sum(char.isdigit() for char in flat)
    if not flat or digits < len(flat) * 0.6 or not re.fullmatch(r"-?\d+(\.\d+)?", cleaned):
        return None
    value = float(cleaned)
    return int(value) if "." not in cleaned and value.is_integer() else value


def _column_of(text: str) -> str | None:
    flat = compact(text)
    best: tuple[int, str] | None = None
    for name, words in COLUMNS.items():
        for word in words:
            if word in flat and (best is None or len(word) > best[0]):
                best = (len(word), name)
    return best[1] if best else None


def _union(boxes: Sequence[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    left = min(box[0] for box in boxes)
    top = min(box[1] for box in boxes)
    right = max(box[0] + box[2] for box in boxes)
    bottom = max(box[1] + box[3] for box in boxes)
    return (left, top, right - left, bottom - top)


def read_statement(lines: Iterable[Line]) -> Statement | None:
    """The statement's table, or None when the page has no such table.

    A table needs a header with the transferred kWh, the charge and the meter.
    """
    lines = [line for line in lines if line.text.strip()]
    for anchor in (line for line in lines if _column_of(line.text) == "kwh"):
        band = [
            line for line in lines
            if line.page == anchor.page and abs(line.middle - anchor.middle) < anchor.height * 1.6
        ]
        headers: dict[str, list[Line]] = {}
        for line in band:
            name = _column_of(line.text)
            if name:
                headers.setdefault(name, []).append(line)
        if not {"kwh", "cost", "meter"} <= headers.keys():
            continue
        centres = {name: sum(l.x + l.width / 2 for l in found) / len(found) for name, found in headers.items()}
        header_bottom = max(line.y + line.height for found in headers.values() for line in found)
        statement = Statement(headers={
            name: (anchor.page, _union([line.box for line in found])) for name, found in headers.items()
        })
        statement.lines = _rows(lines, anchor.page, header_bottom, centres, anchor.height)
        if statement.lines:
            return statement
    return None


def _rows(lines: list[Line], page: int | None, top: float, centres: dict[str, float], height: float) -> list[StatementLine]:
    below = sorted((line for line in lines if line.page == page and line.y >= top - height * 0.2), key=lambda line: line.middle)
    # Group into rows by height.
    groups: list[list[Line]] = []
    for line in below:
        if groups and abs(line.middle - sum(l.middle for l in groups[-1]) / len(groups[-1])) < max(line.height, height) * 0.7:
            groups[-1].append(line)
        else:
            groups.append([line])
    order = sorted(centres, key=centres.get)
    result = []
    for group in groups:
        cells: dict[str, list[Line]] = {}
        for line in group:
            centre = line.x + line.width / 2
            name = min(order, key=lambda column: abs(centres[column] - centre))
            cells.setdefault(name, []).append(line)
        text = {name: " ".join(cell.text for cell in sorted(found, key=lambda cell: cell.x)) for name, found in cells.items()}
        meter = re.sub(r"\D", "", text.get("meter", ""))
        values = {name: _number(text[name]) for name in NUMERIC if name in text}
        if len(meter) < 6 or values.get("kwh") is None or values.get("cost") is None:
            continue
        result.append(StatementLine(
            meter=meter, energy=(text.get("energy") or "").strip() or None, page=page,
            box=_union([line.box for line in group]),
            cells={name: _union([cell.box for cell in found]) for name, found in cells.items()},
            **values,
        ))
    return result


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
