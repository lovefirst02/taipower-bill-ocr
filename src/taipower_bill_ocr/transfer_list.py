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
# A service number on its own, when OCR garbles the words before it (服務编就).
_SERVICE_NUMBER = re.compile(r"(?<!\d)(\d-\d{5}-\d{2}-\d{5})(?!\d)")
# A row's cells: a meter (11 digits), a device (letters and digits) and a rate (0.0778).
_METER = re.compile(r"^\d{10,11}$")
_DEVICE = re.compile(r"^[A-Z]{1,3}\d{6,10}$")
_RATE = re.compile(r"^\d?\.\d{4,5}$")
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
    # The user meter was read on this row (not carried down from the row above).
    meter_read: bool = True

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
    """The list's fee tables, or None when the lines are not a transfer fee list.

    The lines may span pages: a table running onto the next page keeps its
    section, and its columns when that page repeats no header.
    """
    lines = [line for line in lines if line.text.strip()]
    titled = any("清單" in tables.normal(line.text) for line in lines)
    ordered = sorted(lines, key=lambda line: (line.page or 0, line.y))
    service = next((re.sub(r"\s", "", match.group(1)) for line in ordered if (match := _SERVICE.search(tables.normal(line.text)))), None)
    service = service or next((match.group(1) for line in ordered if (match := _SERVICE_NUMBER.search(tables.normal(line.text)))), None)
    period = None
    for line in ordered:
        if (found := _MONTH.search(line.text)) is not None:
            year, month = int(found.group(1)), int(found.group(2))
            if 1 <= month <= 12:
                days = calendar.monthrange(year + 1911 if year < 1000 else year, month)[1]
                period = f"{year}/{month:02d}/01~{year}/{month:02d}/{days:02d}"
                break
    result = TransferList(service=service, period=period)
    sections = [line for line in ordered if _SECTION.search(tables.normal(line.text)) and "費用清單" not in tables.normal(line.text)]
    titles = {id(line): _section_name(line.text) for line in sections}
    section: str | None = None
    centres: dict[str, float] | None = None
    row_height = 0.0
    meter: str | None = None
    for page in sorted({line.page for line in lines}, key=lambda number: number or 0):
        on_page = [line for line in lines if line.page == page]
        page_sections = [line for line in sections if line.page == page]
        found_tables = []
        for anchor in sorted((line for line in on_page if tables.match(line.text, COLUMNS) == "kwh"), key=lambda line: line.y):
            headers = tables.header(on_page, anchor, COLUMNS)
            if not {"kwh", "fee"} <= headers.keys() or not ({"user_meter", "generator"} & headers.keys()):
                continue
            # Without a title such as 費用清單, only Taipower's own header will do.
            if not titled and not {"user_meter", "generator"} <= headers.keys():
                continue
            top = max(line.y + line.height for found in headers.values() for line in found)
            found_tables.append((anchor.y, top, {name: sum(l.x + l.width / 2 for l in found) / len(found) for name, found in headers.items()}, anchor.height))
        if centres is not None:
            # The previous page's table, carried on above this page's first header or title.
            found_tables.insert(0, (float("-inf"), float("-inf"), centres, row_height))
        for index, (anchor_y, top, columns, height) in enumerate(found_tables):
            above = [line for line in page_sections if line.y < anchor_y]
            if above and titles[id(above[-1])] != section:
                section, meter = titles[id(above[-1])], None
            later = [line.y for line in page_sections if line.y > anchor_y]
            if index + 1 < len(found_tables):
                later.append(found_tables[index + 1][0])
            bottom = min(later) if later else float("inf")
            for cells in tables.rows(on_page, page, top, bottom, columns, height):
                kwh = tables.number(tables.text_of(cells, "kwh"), integer=True)
                fee = tables.number(tables.text_of(cells, "fee"), integer=True)
                if kwh is None or fee is None:
                    continue
                # A meter with several generators is written once, on its first row.
                read = _digits(tables.text_of(cells, "user_meter"))
                meter = read or meter
                result.rows.append(TransferRow(
                    meter_read=bool(read),
                    section=section or "轉供費", user_meter=meter, generator=_digits(tables.text_of(cells, "generator")),
                    kwh=kwh, rate=tables.number(tables.text_of(cells, "rate")), fee=fee,
                    user_device=_code(tables.text_of(cells, "user_device")),
                    generator_device=_code(tables.text_of(cells, "generator_device")),
                    page=page, box=tables.union([line.box for found in cells.values() for line in found]),
                ))
            if anchor_y != float("-inf"):
                centres, row_height = columns, height
        if (titled or result.rows) and not any(row.page == page for row in result.rows):
            # No header could be read (small print, a stamp over it): each
            # row is still told by its cells' look.
            for cells_line in _signature_rows(on_page):
                above = [line for line in page_sections if line.y < cells_line["y"]]
                if above and titles[id(above[-1])] != section:
                    section, meter = titles[id(above[-1])], None
                # Not carried down: a meter OCR missed must not take the row above's.
                result.rows.append(TransferRow(
                    section=section or "轉供費", user_meter=cells_line["user_meter"], generator=cells_line["generator"],
                    meter_read=bool(cells_line["user_meter"]),
                    note=None if cells_line["user_meter"] else "用戶電號 not read: type it in",
                    kwh=cells_line["kwh"], rate=cells_line["rate"], fee=cells_line["fee"],
                    user_device=cells_line["user_device"], generator_device=cells_line["generator_device"],
                    page=page, box=cells_line["box"],
                ))
        # A title at the foot of the page names the table on the next one.
        if page_sections and (not found_tables or page_sections[-1].y > found_tables[-1][0]):
            last = titles[id(page_sections[-1])]
            if last != section:
                section, meter = last, None
    _fix_cells(result.rows)
    _fix_rates(result.rows)
    _fill_meters(result.rows)
    _fill_fees(result.rows)
    return result if result.rows else None


def _add_note(row: TransferRow, note: str) -> None:
    row.note = f"{row.note}; {note}" if row.note else note


def _fix_cells(rows: list[TransferRow]) -> None:
    """A rate read into the fee column is put back: a fee is whole NT$, a rate is below 1.

    Rates with many decimals (電力調度 0.0274175) are often read a column off,
    sometimes without their "0.0" (2741604): such a figure is a rate when it
    reads like the other rates of its fee table.
    """
    for row in rows:
        if isinstance(row.fee, float) and not row.fee.is_integer() and row.fee < 1 and row.rate is None:
            row.rate, row.fee = row.fee, None
            _add_note(row, "the rate was read in the fee column")
    by_section: dict[str, list[TransferRow]] = {}
    for row in rows:
        by_section.setdefault(row.section, []).append(row)
    for section_rows in by_section.values():
        rates = sorted(row.rate for row in section_rows if isinstance(row.rate, (int, float)) and 0 < row.rate < 1)
        if not rates:
            continue
        usual = rates[len(rates) // 2]
        for row in section_rows:
            if not isinstance(row.fee, int) or not row.kwh or row.fee / row.kwh < 1:
                continue  # a fee of a few NT$ a kWh at most
            for prefix in ("0.0", "0."):
                candidate = float(prefix + str(row.fee))
                if abs(candidate - usual) <= usual * 0.05:
                    if row.rate is None:
                        row.rate = candidate
                    row.fee = None
                    _add_note(row, f"the rate {candidate} was read as a whole number in the fee column")
                    break


def _fill_meters(rows: list[TransferRow]) -> None:
    """A user meter OCR missed is the one of the same generator and kWh in another fee table.

    Every fee table of a list has the same rows (meter, generator, kWh); only
    a match that is one meter is used, and a meter carried down from the row
    above gives way to it.
    """
    # Meters read on their row first; else carried down (a meter written once for its generators).
    read: dict[tuple[Any, Any], set[str]] = {}
    carried: dict[tuple[Any, Any], set[str]] = {}
    for row in rows:
        if row.user_meter and row.generator and row.kwh is not None:
            (read if row.meter_read else carried).setdefault((row.generator, row.kwh), set()).add(row.user_meter)
    for row in rows:
        if row.meter_read and row.user_meter:
            continue
        key = (row.generator, row.kwh)
        found = read.get(key) or set()
        if not found:
            # Only from another fee table: a row's own carried meter is no evidence for itself.
            others = {other.user_meter for other in rows if other is not row and other.section != row.section
                      and (other.generator, other.kwh) == key and other.user_meter}
            found = others if others <= carried.get(key, set()) else set()
        if len(found) != 1:
            continue
        meter = next(iter(found))
        if meter != row.user_meter:
            row.user_meter = meter
            row.meter_read = True
            row.note = None if row.note and row.note.startswith("用戶電號 not read") else row.note
            _add_note(row, "用戶電號 from another fee table of the list (same generator and kWh)")


def _fill_fees(rows: list[TransferRow]) -> None:
    """A fee not read is worked out as kWh × rate, and said so.

    Only from a whole rate: Taipower's have four decimals or more (0.0778,
    0.0274175); one with digits lost (0.07T8 read as 0.078) is left alone.
    """
    for row in rows:
        whole_rate = row.rate is not None and len(str(row.rate).split(".")[-1]) >= 4
        if row.fee is None and row.kwh is not None and whole_rate:
            row.fee = int((Decimal(str(row.kwh)) * Decimal(str(row.rate))).quantize(Decimal(1), rounding=ROUND_HALF_UP))
            _add_note(row, "fee not read: worked out as kWh × rate — check it on the page")


def _section_name(text: str) -> str:
    """A fee table's title, with a garbled 轉供 put right (棒供輸電費清單)."""
    name = tables.normal(text)
    return re.sub(r"^.?供", "轉供", name) if not name.startswith("轉供") and "供" in name[:2] else name


def _is_rate(text: str) -> bool:
    """0.0778, or what OCR makes of it (0.07T8, 0.077, 4.0778): never a thousands figure."""
    flat = re.sub(r"[\s,，]", "", text)
    if flat.startswith(("0.", ".")):
        return True
    value = tables.number(flat)
    return bool(_RATE.match(flat)) and value is not None and value < 10


def _signature_rows(lines: list[Line]) -> list[dict[str, Any]]:
    """Rows told by their cells: [no] user meter, device, generator, device, kWh, rate, fee.

    A meter written only on its first generator's row leaves it out of the
    rows below (the caller carries it down). A cell OCR missed is left empty.
    """
    if not lines:
        return []
    height = sorted(line.height for line in lines)[len(lines) // 2]
    # A photo is often taken at a slant: a row's cells drift up or down
    # across the page. The drift is measured between meters on one row.
    meters_seen = [line for line in lines if _METER.match(re.sub(r"[\s,，]", "", line.text))]
    slopes = sorted(
        (b.middle - a.middle) / (b.x - a.x)
        for a in meters_seen for b in meters_seen
        if 80 < b.x - a.x < 600 and abs(b.middle - a.middle) < height * 2
    )
    slope = slopes[len(slopes) // 2] if slopes else 0.0
    level = lambda line: line.middle - slope * (line.x + line.width / 2)
    groups: list[list[Line]] = []
    for line in sorted(lines, key=level):
        if groups and abs(level(line) - sum(level(l) for l in groups[-1]) / len(groups[-1])) < height * 0.7:
            groups[-1].append(line)
        else:
            groups.append([line])
    found = []
    for group in groups:
        cells = sorted(group, key=lambda line: line.x)
        flat = [re.sub(r"[\s,，]", "", tables.normal(line.text)).upper() for line in cells]
        meters = [index for index, text in enumerate(flat) if _METER.match(text)]
        devices = [index for index, text in enumerate(flat) if _DEVICE.match(text)]
        if not meters:
            continue
        last_code = max(meters + devices)
        rate_at = next((index for index in range(last_code + 1, len(cells)) if _is_rate(cells[index].text)), None)
        amounts = [index for index in range(last_code + 1, len(cells))
                   if index != rate_at and not _is_rate(cells[index].text) and isinstance(tables.number(cells[index].text, integer=True), int)]
        if not amounts:
            continue
        # The user meter comes before the first device; the generator after it.
        first_device = devices[0] if devices else None
        user = next((index for index in meters if first_device is None or index < first_device), None)
        generator = next((index for index in meters if index != user and (first_device is None or index > first_device)), None)
        if user is not None and generator is None and len(meters) == 1 and first_device is None:
            user, generator = None, user
        user_device = next((index for index in devices if user is not None and index > user and (generator is None or index < generator)), None)
        generator_device = next((index for index in devices if generator is not None and index > generator), None)
        if rate_at is not None:
            kwh = [index for index in amounts if index < rate_at]
            fee = [index for index in amounts if index > rate_at]
            kwh_at, fee_at = (kwh[-1] if kwh else None), (fee[0] if fee else None)
        else:
            kwh_at, fee_at = amounts[0], (amounts[-1] if len(amounts) > 1 else None)
        found.append({
            "y": min(line.y for line in cells),
            "user_meter": flat[user] if user is not None else None,
            "generator": flat[generator] if generator is not None else None,
            "user_device": flat[user_device] if user_device is not None else None,
            "generator_device": flat[generator_device] if generator_device is not None else None,
            "kwh": tables.number(cells[kwh_at].text, integer=True) if kwh_at is not None else None,
            "kwh_x": cells[kwh_at].x + cells[kwh_at].width / 2 if kwh_at is not None else None,
            "rate": tables.number(re.sub(r"[^\d.]", "", cells[rate_at].text)) if rate_at is not None else None,
            "fee": tables.number(cells[fee_at].text, integer=True) if fee_at is not None else None,
            "box": tables.union([line.box for line in cells]),
        })
    # A row whose user meter OCR missed shows only the generator and its device.
    generators = {row["generator"] for row in found if row["generator"]}
    for row in found:
        if row["generator"] is None and row["user_meter"] in generators:
            row["generator"], row["generator_device"] = row["user_meter"], row["user_device"]
            row["user_meter"], row["user_device"] = None, None
    # One amount without a rate beside it: kWh or fee, by which column it lines up with.
    kwh_xs = sorted(row["kwh_x"] for row in found if row["kwh"] is not None and row["fee"] is not None)
    fee_xs = sorted(row["box"][0] + row["box"][2] for row in found if row["kwh"] is not None and row["fee"] is not None)
    if kwh_xs and fee_xs:
        kwh_x, fee_x = kwh_xs[len(kwh_xs) // 2], fee_xs[len(fee_xs) // 2]
        for row in found:
            if row["fee"] is None and row["kwh"] is not None and row["rate"] is None and abs(row["kwh_x"] - fee_x) < abs(row["kwh_x"] - kwh_x):
                row["kwh"], row["fee"] = None, row["kwh"]
    return [row for row in found if row["kwh"] is not None or row["fee"] is not None]


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
                fixed = (f"rate read as {row.rate}" if row.rate is not None else "rate not read") + f"; the section's {rate} gives the printed fee"
                row.note = f"{row.note}; {fixed}" if row.note else fixed
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
