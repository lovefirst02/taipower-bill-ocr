"""Taipower's transfer fee list (費用清單) for green power (轉供).

One list per transfer service (服務編號) and month. Each fee — 轉供輸電費,
轉供配電費 and so on — has a table with a row per user meter (用戶電號) and
generator (發電電號): the month's transferred kWh, the rate and the fee.
Taipower works each fee out as kWh × rate rounded to the NT$, so every row
can be checked exactly. The kWh here is the reference for transferred green
power; retailers' statements should agree with it.

No OCR happens here.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Iterable

from . import tables
from .keyword_extract import Line

COLUMNS: dict[str, tuple[str, ...]] = {
    "no": ("編號",),
    "user_meter": ("用戶電號",),
    "user_device": ("用戶表號",),
    "generator": ("發電電號",),
    "generator_device": ("發電表號",),
    "kwh": ("當月服務使用量", "服務使用量", "使用量"),
    "rate": ("輸電費率", "配電費率", "費率"),
    "fee": ("費用(元)", "費用"),
}
_SECTION = re.compile(r"(轉供.{1,8}費清單|.{2,8}費清單)")
_SERVICE = re.compile(r"服務編號\s*[:：]?\s*([0-9][0-9\-\s]{6,})")
_MONTH = re.compile(r"(\d{3,4})\s*年\s*(\d{1,2})\s*月")


@dataclass
class TransferRow:
    section: str
    user_meter: str | None
    generator: str | None
    kwh: Any = None
    rate: Any = None
    fee: Any = None
    user_device: str | None = None
    generator_device: str | None = None
    page: int | None = None
    box: tuple[float, float, float, float] | None = None
    # Why a value was changed from what OCR read.
    note: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "note": self.note,
            "section": self.section, "user_meter": self.user_meter, "user_device": self.user_device,
            "generator": self.generator, "generator_device": self.generator_device, "kwh": self.kwh,
            "rate": self.rate, "fee": self.fee, "page": self.page, "box": list(self.box) if self.box else None,
        }


@dataclass
class TransferList:
    service: str | None
    period: str | None
    rows: list[TransferRow] = field(default_factory=list)


def _digits(text: str) -> str | None:
    digits = re.sub(r"\D", "", text)
    return digits if len(digits) >= 6 else None


def _code(text: str) -> str | None:
    code = re.sub(r"[^0-9A-Za-z]", "", text).upper()
    return code or None


def read_transfer_list(lines: Iterable[Line]) -> TransferList | None:
    """The list's fee tables, or None when the page is not a transfer fee list."""
    lines = [line for line in lines if line.text.strip()]
    if not any("清單" in tables.normal(line.text) for line in lines):
        return None
    service = next((re.sub(r"\s", "", match.group(1)) for line in lines if (match := _SERVICE.search(tables.normal(line.text)))), None)
    period = None
    for line in lines:
        if (found := _MONTH.search(line.text)) is not None:
            year, month = int(found.group(1)), int(found.group(2))
            if 1 <= month <= 12:
                days = calendar.monthrange(year + 1911 if year < 1000 else year, month)[1]
                period = f"{year}/{month:02d}/01~{year}/{month:02d}/{days:02d}"
                break
    result = TransferList(service=service, period=period)
    sections = sorted(
        (line for line in lines if _SECTION.search(tables.normal(line.text)) and "費用清單" not in tables.normal(line.text)),
        key=lambda line: (line.page or 0, line.y),
    )
    anchors = [line for line in lines if tables.match(line.text, COLUMNS) == "kwh"]
    for anchor in sorted(anchors, key=lambda line: (line.page or 0, line.y)):
        headers = tables.header(lines, anchor, COLUMNS)
        if not {"kwh", "fee"} <= headers.keys() or not ({"user_meter", "generator"} & headers.keys()):
            continue
        # The section title just above this table, and where the next one starts.
        above = [section for section in sections if section.page == anchor.page and section.y < anchor.y]
        title = tables.normal(above[-1].text) if above else "轉供費"
        below = [section.y for section in sections if section.page == anchor.page and section.y > anchor.y]
        bottom = min(below) if below else float("inf")
        centres = {name: sum(l.x + l.width / 2 for l in found) / len(found) for name, found in headers.items()}
        top = max(line.y + line.height for found in headers.values() for line in found)
        meter = None
        for cells in tables.rows(lines, anchor.page, top, bottom, centres, anchor.height):
            kwh = tables.number(tables.text_of(cells, "kwh"), integer=True)
            fee = tables.number(tables.text_of(cells, "fee"), integer=True)
            if kwh is None or fee is None:
                continue
            # A meter with several generators is written once, on its first row.
            meter = _digits(tables.text_of(cells, "user_meter")) or meter
            result.rows.append(TransferRow(
                section=title, user_meter=meter, generator=_digits(tables.text_of(cells, "generator")),
                kwh=kwh, rate=tables.number(tables.text_of(cells, "rate")), fee=fee,
                user_device=_code(tables.text_of(cells, "user_device")),
                generator_device=_code(tables.text_of(cells, "generator_device")),
                page=anchor.page, box=tables.union([line.box for found in cells.values() for line in found]),
            ))
    _fix_rates(result.rows)
    return result if result.rows else None


def _fix_rates(rows: list[TransferRow]) -> None:
    """A rate unlike the rest of its section, misread (e.g. under a cursor), is put right.

    Only when the section's usual rate gives exactly the printed fee.
    """
    from collections import Counter

    by_section: dict[str, list[TransferRow]] = {}
    for row in rows:
        by_section.setdefault(row.section, []).append(row)
    for section_rows in by_section.values():
        usual = Counter(row.rate for row in section_rows if row.rate is not None).most_common(1)
        if not usual:
            continue
        rate = usual[0][0]
        for row in section_rows:
            if row.rate == rate or row.kwh is None or row.fee is None:
                continue
            expected = (Decimal(str(row.kwh)) * Decimal(str(rate))).quantize(Decimal(1), rounding=ROUND_HALF_UP)
            if expected == Decimal(str(row.fee)):
                row.note = f"rate read as {row.rate}; the section's {rate} gives the printed fee"
                row.rate = rate


def check_row(row: dict[str, Any]) -> dict[str, Any]:
    """Taipower's fee is kWh × rate rounded to the NT$: it should match exactly."""
    try:
        kwh, rate, fee = (Decimal(str(row[name])) for name in ("kwh", "rate", "fee"))
    except (KeyError, TypeError, InvalidOperation):
        return {"expected": None, "difference": None, "status": "incomplete"}
    if row.get("kwh") is None or row.get("rate") is None or row.get("fee") is None:
        return {"expected": None, "difference": None, "status": "incomplete"}
    expected = (kwh * rate).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    difference = fee - expected
    return {"expected": int(expected), "difference": int(difference) if difference % 1 == 0 else float(difference),
            "status": "ok" if difference == 0 else "check"}
