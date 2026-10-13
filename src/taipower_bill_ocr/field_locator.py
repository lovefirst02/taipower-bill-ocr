"""Locate each spreadsheet column's value on the bill image for previews."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from .excel_export import _ENERGY_GROUPS, _TOU_COMPONENTS
from .parser import BillResult, OcrLine

Box = tuple[float, float, float, float]  # x, y, width, height in image pixels

_KWH_ALIASES = {
    "peak_kwh": ("經常尖峰度數", "尖峰度數"),
    "semi_peak_kwh": ("半尖峰度數",),
    "saturday_semi_peak_kwh": ("週六半尖峰度數",),
    "off_peak_kwh": ("離峰度數",),
}
_GROUP_HEADINGS = {
    "energy_consumption": ("計費度數",),
    "transferred_energy": ("轉供度數",),
}

# column -> (blocks to search, label aliases, kWh group or None)
_FIELD_HINTS: dict[str, tuple[tuple[str, ...], tuple[str, ...], str | None]] = {
    "Billing Period 計費期間": (("user_info", "payment_info"), ("計費期間",), None),
    "Custom No. 電號": (("payment_info", "user_info"), ("電號",), None),
    **{
        f"{group_label}_{component_label}": (
            ("user_info", "energy_usage"),
            _KWH_ALIASES[component_key],
            group_key,
        )
        for group_label, group_key in _ENERGY_GROUPS
        for component_label, component_key in _TOU_COMPONENTS
    },
    "基本電費": (("charge_info",), ("基本電費",), None),
    "流動電費": (("charge_info",), ("流動電費",), None),
    "功率因數調整費": (("charge_info",), ("功率因數調整費",), None),
    "預繳費用": (("charge_info",), ("本期預繳",), None),
    "其他調整費用": (("charge_info",), (), None),
    "稅前應繳總金額": (("charge_info",), ("稅前應繳總金額",), None),
    "應繳總金額": (("charge_info", "payment_info"), ("應繳總金額", "繳費總金額"), None),
}

_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


@dataclass(frozen=True)
class FieldLocation:
    box: Box
    exact: bool  # False when only the surrounding block could be found


def _compact(text: str) -> str:
    return re.sub(r"[\s：:（）()]", "", text).replace("缴", "繳")


def _center_y(line: OcrLine) -> float:
    return line.y + line.height / 2


def _same_row(a: OcrLine, b: OcrLine) -> bool:
    return abs(_center_y(a) - _center_y(b)) <= max(a.height, b.height) * 0.6


def _union(lines: Iterable[OcrLine], pad: float = 4) -> Box:
    lines = list(lines)
    left = min(line.x for line in lines) - pad
    top = min(line.y for line in lines) - pad
    right = max(line.x + line.width for line in lines) + pad
    bottom = max(line.y + line.height for line in lines) + pad
    return (left, top, right - left, bottom - top)


def _matches_value(line: OcrLine, value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        for token in _NUMBER.findall(line.text):
            try:
                if abs(float(token.replace(",", "")) - value) < 0.01:
                    return True
            except ValueError:
                continue
        return False
    digits = re.sub(r"\D", "", str(value))
    return len(digits) >= 4 and digits in re.sub(r"\D", "", line.text)


def _group_scope(lines: list[OcrLine], group_key: str) -> list[OcrLine]:
    """Restrict kWh labels to the rows under their 計費度數/轉供度數 heading."""
    headings = {
        key: min(
            (
                line.y
                for line in lines
                if any(_compact(line.text).startswith(alias) for alias in aliases)
            ),
            default=None,
        )
        for key, aliases in _GROUP_HEADINGS.items()
    }
    start = headings.get(group_key)
    if start is None:
        return lines if group_key == "energy_consumption" else []
    later = [y for key, y in headings.items() if y is not None and y > start]
    end = min(later, default=float("inf"))
    return [line for line in lines if start < _center_y(line) < end]


def _value_candidates(label: OcrLine, scope: list[OcrLine]) -> list[OcrLine]:
    beside = [
        line
        for line in scope
        if line is not label and _same_row(line, label) and line.x > label.x
    ]
    below = [
        line
        for line in scope
        if line is not label
        and label.y + label.height <= line.y <= label.y + label.height * 3
        and line.x < label.x + label.width
        and line.x + line.width > label.x
    ]
    return sorted(beside, key=lambda line: line.x) + sorted(below, key=lambda line: line.y)


def _truncated_reading(line: OcrLine, value: Any) -> bool:
    """A text value cut short by OCR, e.g. '113/02/06至113/0' for a period."""
    if value is None or isinstance(value, (int, float)):
        return False
    wanted = re.sub(r"\D", "", str(value))
    read = re.sub(r"\D", "", line.text)
    return len(wanted) >= 6 and len(read) >= 6 and wanted[:6] == read[:6]


def _search(
    bill: BillResult, scope: list[OcrLine], aliases: tuple[str, ...], value: Any, block_key: str | None
) -> tuple[FieldLocation | None, Box | None]:
    """Find ``value`` beside one of its labels in ``scope``.

    Returns an exact location, or the label's row as a fallback when the
    value printed there was cut short by OCR.
    """
    fallback: Box | None = None
    labels = [line for line in scope if any(_compact(line.text).startswith(alias) for alias in aliases)]
    for label in sorted(labels, key=lambda line: line.y):
        if _matches_value(label, value):
            return FieldLocation(_union([label]), True), None
        candidates = _value_candidates(label, scope)
        match = next((line for line in candidates if _matches_value(line, value)), None)
        if match is None and value is not None:
            # Table columns wrap a value over two lines under the heading
            # (計費期間 / 109/12/01~ / 109/12/31).
            for first, second in zip(candidates, candidates[1:]):
                joined = OcrLine(first.text + second.text, first.x, first.y, first.width, first.height)
                if _matches_value(joined, value):
                    return FieldLocation(_union([label, first, second]), True), None
        if match is None and candidates and value is not None:
            partial = next((line for line in candidates if _truncated_reading(line, value)), None)
            if partial is not None and fallback is None:
                fallback = _union([label, partial])
            continue  # this label's neighbour holds a different value
        if match is None:
            # Value missing: point at the label row where it should appear.
            bounds = bill.get(f"blocks.{block_key}.bounds") if block_key else None
            right = bounds["x"] + bounds["width"] if bounds else label.x + label.width * 3
            return FieldLocation(
                (label.x - 4, label.y - 4, right - label.x + 8, label.height + 8), True
            ), None
        return FieldLocation(_union([label, match]), True), None
    if value is not None:
        matches = [line for line in scope if _matches_value(line, value)]
        if len(matches) == 1:
            return FieldLocation(_union(matches), True), None
    return None, fallback


def locate_field(
    bill: BillResult, column: str, value: Any
) -> FieldLocation | None:
    """Return where ``column``'s value is printed, or its block as a fallback.

    The blocks the value normally sits in are searched first, then the whole
    page (layouts differ: residential bills print 計費期間 in another block).
    """
    hint = _FIELD_HINTS.get(column)
    if hint is None:
        return None
    block_keys, aliases, group_key = hint
    lines = list(bill.ocr_lines)

    fallback: Box | None = None
    for block_key in block_keys:
        scope = [line for line in lines if line.block_key == block_key]
        if not scope and block_key == block_keys[0]:
            scope = [line for line in lines if line.block_key is None]
        if group_key:
            scope = _group_scope(scope, group_key)
        found, partial = _search(bill, scope, aliases, value, block_key)
        if found is not None:
            return found
        fallback = fallback or partial
    if not group_key:  # kWh labels repeat per group, so never search them page-wide
        hinted = set(block_keys)
        for block_key in sorted({line.block_key for line in lines if line.block_key not in hinted}, key=str):
            scope = [line for line in lines if line.block_key == block_key]
            found, partial = _search(bill, scope, aliases, value, block_key)
            if found is not None:
                return found
            fallback = fallback or partial
    if fallback is not None:
        return FieldLocation(fallback, True)

    for block_key in block_keys:
        bounds = bill.get(f"blocks.{block_key}.bounds")
        if bounds:
            return FieldLocation(
                (bounds["x"], bounds["y"], bounds["width"], bounds["height"]), False
            )
    return None


def locate_fields(bill: BillResult, row: dict[str, Any]) -> dict[str, FieldLocation]:
    """Locate every spreadsheet column that can be pointed at on the page."""
    located = {}
    for column in _FIELD_HINTS:
        location = locate_field(bill, column, row.get(column))
        if location is not None:
            located[column] = location
    return located
