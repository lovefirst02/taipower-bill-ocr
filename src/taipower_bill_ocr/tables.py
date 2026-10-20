"""Reading printed tables and forms from OCR lines.

Used for green power documents (a retailer's statement, Taipower's transfer
fee list, a retailer's purchase notice): their layouts are fixed, so values
are found by their header or label.

OCR on photos of screens mixes in simplified characters (费, 当) and reads a
thousands comma as a point (4,654 → 4.654); both are handled here.
"""

from __future__ import annotations

import re
from typing import Sequence

from .keyword_extract import Line, compact

# Simplified (or misread) characters that OCR returns for traditional ones.
_TRADITIONAL = str.maketrans({
    "费": "費", "电": "電", "当": "當", "额": "額", "税": "稅", "証": "證", "证": "證", "单": "單", "价": "價",
    "购": "購", "务": "務", "号": "號", "户": "戶", "应": "應", "转": "轉", "输": "輸", "数": "數", "际": "際",
    "实": "實", "余": "餘", "张": "張", "审": "審", "查": "查", "货": "貨", "缴": "繳", "资": "資", "讯": "訊",
    "愿": "應",  # 應付稅金 read as 愿付
})

Box = tuple[float, float, float, float]


def normal(text: str) -> str:
    """Text for matching: no spaces, lower case, traditional characters."""
    return compact(text).translate(_TRADITIONAL)


def number(text: str, integer: bool = False) -> int | float | None:
    """A number in a cell, ignoring a stray character (a cursor read as "I").

    For whole amounts (kWh, NT$), "4.654" is 4,654: a thousands comma read as
    a point.
    """
    flat = re.sub(r"[,，\s]", "", text)
    if integer and re.fullmatch(r"\d{1,3}([.,]\d{3})+", re.sub(r"\s", "", text)):
        flat = re.sub(r"[.,]", "", text.strip())
    cleaned = re.sub(r"[^\d.\-]", "", flat)
    digits = sum(char.isdigit() for char in flat)
    if not flat or digits < len(flat) * 0.6 or not re.fullmatch(r"-?\d+(\.\d+)?", cleaned):
        return None
    value = float(cleaned)
    return int(value) if "." not in cleaned and value.is_integer() else value


def match(text: str, names: dict[str, Sequence[str]]) -> str | None:
    """The name whose keyword is the longest one in ``text``."""
    flat = normal(text)
    best: tuple[int, str] | None = None
    for name, words in names.items():
        for word in words:
            if normal(word) in flat and (best is None or len(normal(word)) > best[0]):
                best = (len(normal(word)), name)
    return best[1] if best else None


def union(boxes: Sequence[Box]) -> Box:
    left = min(box[0] for box in boxes)
    top = min(box[1] for box in boxes)
    right = max(box[0] + box[2] for box in boxes)
    bottom = max(box[1] + box[3] for box in boxes)
    return (left, top, right - left, bottom - top)


def header(lines: Sequence[Line], anchor: Line, columns: dict[str, Sequence[str]]) -> dict[str, list[Line]]:
    """The header cells on the anchor's band (headers may wrap onto two lines)."""
    found: dict[str, list[Line]] = {}
    for line in lines:
        if line.page == anchor.page and abs(line.middle - anchor.middle) < anchor.height * 1.6:
            name = match(line.text, columns)
            if name:
                found.setdefault(name, []).append(line)
    return found


def rows(
    lines: Sequence[Line],
    page: int | None,
    top: float,
    bottom: float,
    centres: dict[str, float],
    height: float,
) -> list[dict[str, list[Line]]]:
    """Table rows between ``top`` and ``bottom``: each row's lines by column."""
    inside = sorted(
        (line for line in lines if line.page == page and top - height * 0.2 <= line.y < bottom),
        key=lambda line: line.middle,
    )
    groups: list[list[Line]] = []
    for line in inside:
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
        result.append({name: sorted(found, key=lambda cell: cell.x) for name, found in cells.items()})
    return result


def text_of(cells: dict[str, list[Line]], name: str) -> str:
    return " ".join(cell.text for cell in cells.get(name, []))
