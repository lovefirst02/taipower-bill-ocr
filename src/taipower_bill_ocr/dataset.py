"""Turn a parsed bill page into a complete, consumer-neutral dataset record.

Every value the parser found becomes one *field* row (long format, so new
bill items never change the table shape), multi-row tables become *table
rows*, and the raw OCR lines are kept so pages can be re-parsed later
without OCR. Each field carries evidence: whether a second OCR engine read
the same value, and where on the page it was read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Iterable

from .parser import BillResult, OcrLine, _BLOCK_LABELS, _FIELD_LABELS
from .parser import _compact as _normalize  # folds simplified glyphs OCR emits

SCHEMA_VERSION = 1

try:
    PROGRAM_VERSION = version("taipower-bill-ocr")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    PROGRAM_VERSION = "0+unknown"

# Top-level BillResult entries that are bookkeeping, not bill content.
META_KEYS = {"blocks", "by_label", "quality", "document_type"}
# Parser bookkeeping inside blocks (duplicates of other fields).
SKIPPED_FIELD_KEYS = {"raw_energy_rows"}

# Chinese names as printed on the bill, for every stable key the parser emits.
KEY_LABELS = {
    **_FIELD_LABELS,
    "basic_charge_twd": "基本電費",
    "power_factor_adjustment_twd": "功率因數調整費",
    "pretax_total_twd": "稅前應繳總金額",
    "business_tax_twd": "營業稅",
    "discount_twd": "優惠金額",
    "ebill_discount_twd": "電子帳單優惠減收金額",
    "meter_rental_twd": "電器租費",
    "demand_response_discount_twd": "配合減少用電減收",
    "contract_capacity_kw": "經常契約容量",
    "max_demand_kw": "經常最高需量",
    "max_demand": "最高需量",
    "semi_peak_non_summer_kw": "半尖峰（非夏月）需量",
    "saturday_semi_peak_kw": "週六半尖峰需量",
    "off_peak_kw": "離峰需量",
    "power_factor_percent": "功率因數(%)",
    "peak_kwh": "尖峰度數",
    "semi_peak_kwh": "半尖峰度數",
    "saturday_semi_peak_kwh": "週六半尖峰度數",
    "off_peak_kwh": "離峰度數",
    "discount_kwh": "優惠度數",
    "start": "起",
    "end": "迄",
    "payment_date": "繳費日期",
    "autopay_account": "代繳帳號",
    "service_address": "用電地址",
    "industry": "行業別",
    "bill_month": "帳單月份",
    "service_unit": "服務單位",
    "customer_service_phone": "客服電話",
    "billing_period_code": "期別",
    "group_code": "組別代碼",
    "group_name": "組別",
    "meter_number": "表號",
    "multiplier": "倍數",
    "readings": "電表指數",
    "meter_type": "表別",
    "previous": "上期指數",
    "removed": "拆表指數",
    "installed": "裝表指數",
    "current": "本期指數",
    "comparison_rows": "節電比較",
    "period": "比較項目",
    "days": "用電日數",
    "energy_kwh": "度數",
    "daily_average_kwh": "日平均度數",
}

_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")

# Evidence statuses, strongest first.
AGREE = "engines_agree"
DISAGREE = "engines_disagree"
FILLED = "filled_from_cross_check"
PRIMARY_ONLY = "primary_only"
UNVERIFIED = "unverified"  # unrecognised label: kept, but not cross-checked


@dataclass
class PageRecord:
    summary: dict[str, Any]
    fields: list[dict[str, Any]]
    table_rows: list[dict[str, Any]]
    ocr_lines: list[dict[str, Any]]
    quality: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "fields": self.fields,
            "table_rows": self.table_rows,
            "quality": self.quality,
            "ocr_lines": self.ocr_lines,
        }


def _is_known(key: str) -> bool:
    # Only the parser's vocabulary counts: OCR noise can also look like a
    # lowercase key (e.g. "ax"), so the pattern alone is not enough.
    return key in KEY_LABELS


def _label(path: list[str]) -> str:
    names = [KEY_LABELS.get(part, part) for part in path]
    return "／".join(names)


def _compact(value: Any) -> str:
    return re.sub(r"[\s:：,，]", "", str(value))


def _as_number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    # Windows OCR often renders the decimal point as '·' (e.g. "-8976·0元").
    text = str(value).replace("·", ".").replace(",", "")
    match = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*(?:元|度|%|公斤)?\s*", text)
    return float(match.group(1)) if match else None


def _same(a: Any, b: Any) -> bool:
    numbers = _as_number(a), _as_number(b)
    if None not in numbers:
        return abs(numbers[0] - numbers[1]) < 0.01
    # Codes such as invoice numbers are printed upper case; OCR case is noise.
    return _compact(a).casefold() == _compact(b).casefold()


def _block_items(bill: BillResult) -> Iterable[tuple[str, dict[str, Any]]]:
    for key, value in bill.items():
        if key not in META_KEYS and isinstance(value, dict):
            yield key, value


def _walk(
    value: Any, path: list[str], fields: dict[tuple[str, ...], Any], tables: list[tuple[list[str], int, str, Any]]
) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key not in SKIPPED_FIELD_KEYS:
                _walk(item, [*path, key], fields, tables)
    elif isinstance(value, list) and value and all(isinstance(row, dict) for row in value):
        for index, row in enumerate(value, start=1):
            for column, cell in row.items():
                tables.append((path, index, column, cell))
    elif isinstance(value, list):
        fields[tuple(path)] = "、".join(str(item) for item in value)
    else:
        fields[tuple(path)] = value


def _flatten(bill: BillResult) -> tuple[dict[tuple[str, ...], Any], list[tuple[list[str], int, str, Any]]]:
    fields: dict[tuple[str, ...], Any] = {}
    tables: list[tuple[list[str], int, str, Any]] = []
    for block, content in _block_items(bill):
        _walk(content, [block], fields, tables)
    return fields, tables


# Keys whose printed label only makes sense under a parent (尖峰度數 appears
# under both 計費度數 and 轉供度數), so a bare label cannot be mapped to them.
_NESTED_ONLY_KEYS = {
    "peak_kwh", "semi_peak_kwh", "saturday_semi_peak_kwh", "off_peak_kwh",
    "semi_peak_non_summer_kw", "saturday_semi_peak_kw", "off_peak_kw",
    "start", "end", "readings", "meter_type", "previous", "removed", "installed",
    "current", "comparison_rows", "period", "days", "energy_kwh",
    "daily_average_kwh", "amount_twd",
}
_KNOWN_LABELS = {_normalize(label) for label in KEY_LABELS.values()}
_LABEL_TO_KEY = {
    _normalize(label): key for key, label in KEY_LABELS.items() if key not in _NESTED_ONLY_KEYS
}


def _canonicalize(fields: dict[tuple[str, ...], Any]) -> tuple[dict[tuple[str, ...], Any], int]:
    """Map printed-label keys onto known keys and drop duplicate copies.

    The parser keeps unknown 'label: value' pairs under their printed label,
    which also catches known items it stored elsewhere (優惠度數, or 尖峰度數
    already held under 計費度數). Returns the fields and how many duplicates
    were removed.
    """
    known_values: dict[tuple[str, str], list[Any]] = {}
    for path, value in fields.items():
        if all(_is_known(part) for part in path[1:]):
            known_values.setdefault((path[0], _normalize(KEY_LABELS[path[-1]])), []).append(value)

    result: dict[tuple[str, ...], Any] = {}
    duplicates = 0
    for path, value in fields.items():
        if len(path) == 2 and not _is_known(path[1]):
            label = _normalize(path[1])
            if any(_same(value, known) for known in known_values.get((path[0], label), [])):
                duplicates += 1
                continue
            key = _LABEL_TO_KEY.get(label)
            if key is not None and (path[0], key) not in fields:
                result[(path[0], key)] = value
                continue
        result[path] = value
    return result, duplicates


def _is_header_mispair(path: tuple[str, ...], value: Any, block_keys: set[str]) -> bool:
    """An unrecognised 'label: value' whose value is itself another heading.

    Table headers printed side by side (e.g. 表別 | 上期指數 | 拆表指數) are
    otherwise paired with each other as if one were the other's value; bill
    headings are bilingual (應繳總金額TotalAmount), so an English word with
    no digits also marks a heading.
    """
    if _is_known(path[-1]) or not isinstance(value, str):
        return False
    normalized = _normalize(value)
    if not normalized:
        return False
    if any(normalized in _normalize(key) for key in block_keys if key != path[-1]):
        return True
    if normalized in _KNOWN_LABELS:  # e.g. 裝表指數 -> 本期指數, a column heading
        return True
    return not re.search(r"\d", value) and bool(re.search(r"[A-Za-z]{4,}", value))


def _locate(lines: list[OcrLine], block: str, value: Any) -> dict[str, float] | None:
    """Box of the OCR line in ``block`` that printed ``value``, if unambiguous."""
    if value is None or value == "":
        return None
    candidates = [line for line in lines if line.block_key == block] or lines
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        matches = [
            line
            for line in candidates
            if any(abs(float(token.replace(",", "")) - value) < 0.01 for token in _NUMBER.findall(line.text) if token.replace(",", "").replace("-", "").replace(".", "").isdigit())
        ]
    else:
        target = _compact(value)
        matches = [line for line in candidates if len(target) >= 2 and target in _compact(line.text)]
    if len(matches) != 1:
        return None
    line = matches[0]
    return {"x": round(line.x, 1), "y": round(line.y, 1), "width": round(line.width, 1), "height": round(line.height, 1)}


def _first(fields: dict[tuple[str, ...], Any], *paths: tuple[str, ...]) -> Any:
    return next((fields[path] for path in paths if fields.get(path) is not None), None)


def page_record(
    bill: BillResult,
    *,
    file: str,
    page: int | None,
    cross_check: BillResult | None = None,
    cross_check_error: str | None = None,
) -> PageRecord:
    """Describe one bill page completely, with per-field evidence."""
    primary, primary_tables = _flatten(bill)
    printed_labels: dict[str, set[str]] = {}
    for path in primary:  # headings as printed, before keys are canonicalised
        printed_labels.setdefault(path[0], set()).update(path[1:])
    primary, duplicates = _canonicalize(primary)
    other: dict[tuple[str, ...], Any] = (
        _canonicalize(_flatten(cross_check)[0])[0] if cross_check is not None else {}
    )
    lines = list(bill.ocr_lines)
    other_lines = list(cross_check.ocr_lines) if cross_check is not None else []

    block_keys = printed_labels

    fields: list[dict[str, Any]] = []
    dropped = 0
    paths = list(primary) + [
        path for path in other if path not in primary and all(_is_known(part) for part in path[1:])
    ]
    for path in paths:
        recognized = all(_is_known(part) for part in path[1:])
        if path in primary:
            value = primary[path]
            if not recognized and _is_header_mispair(path, value, block_keys.get(path[0], set())):
                dropped += 1
                continue
            if not recognized:
                status, alternative = UNVERIFIED, None
            elif cross_check is None or path not in other:
                status, alternative = PRIMARY_ONLY, None
            elif _same(value, other[path]):
                status, alternative = AGREE, None
            else:
                status, alternative = DISAGREE, other[path]
            source, box = "primary", _locate(lines, path[0], value)
        else:
            value, status, alternative = other[path], FILLED, None
            source, box = "cross_check", _locate(other_lines, path[0], value)
        if value is None:
            continue
        fields.append(
            {
                "block": path[0],
                "block_label": _BLOCK_LABELS.get(path[0], path[0]),
                "key": ".".join(path[1:]),
                "label": _label(list(path[1:])),
                "value": value,
                "recognized": recognized,
                "status": status,
                "cross_check_value": alternative,
                "source": source,
                "bbox": box,
            }
        )

    table_rows = [
        {
            "block": path[0],
            "table": ".".join(path[1:]),
            "table_label": _label(list(path[1:])),
            "row": index,
            "column": column,
            "column_label": KEY_LABELS.get(column, column),
            "value": cell,
        }
        for path, index, column, cell in primary_tables
    ]

    period_start = _first(primary, ("user_info", "billing_period", "start"), ("payment_info", "billing_period", "start"))
    period_end = _first(primary, ("user_info", "billing_period", "end"), ("payment_info", "billing_period", "end"))
    counts = {status: sum(1 for item in fields if item["status"] == status) for status in (AGREE, DISAGREE, FILLED, PRIMARY_ONLY, UNVERIFIED)}
    warnings = list(bill.get("quality.warnings") or [])
    if cross_check_error:
        warnings.append(f"cross_check_failed: {cross_check_error}")
    summary = {
        "file": file,
        "page": page,
        "document_type": bill.get("document_type"),
        "customer_number": _first(
            primary,
            ("user_info", "customer_number"),
            ("payment_info", "customer_number"),
            ("meter_info", "customer_number"),
        ),
        "billing_period_start": period_start,
        "billing_period_end": period_end,
        "total_amount_twd": _first(primary, ("charge_info", "total_amount_twd"), ("payment_info", "total_amount_twd")),
        "fields_recognized": sum(1 for item in fields if item["recognized"]),
        "fields_unrecognized": counts[UNVERIFIED],
        "engines_agree": counts[AGREE],
        "engines_disagree": counts[DISAGREE],
        "filled_from_cross_check": counts[FILLED],
        "primary_only": counts[PRIMARY_ONLY],
        "header_mispairs_dropped": dropped,
        "duplicates_dropped": duplicates,
        "needs_review": bool(counts[DISAGREE] or warnings),
        "warnings": "; ".join(warnings) or None,
    }
    ocr_lines = [
        {"text": line.text, "x": round(line.x, 1), "y": round(line.y, 1), "width": round(line.width, 1), "height": round(line.height, 1), "block": line.block_key}
        for line in lines
    ]
    quality = {key: value for key, value in (bill.get("quality") or {}).items() if key != "warnings"}
    return PageRecord(summary, fields, table_rows, ocr_lines, quality)
