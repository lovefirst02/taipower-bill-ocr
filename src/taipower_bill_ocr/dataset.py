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
from .parser import _compact as _parser_compact

# Simplified or variant glyphs OCR emits for characters printed on bills,
# on top of the handful the parser already folds.
_TRADITIONAL = str.maketrans(
    "调节务号业营种类价时间楼帐单发动应总减码账网输续线馈项户统编规则说币记录处区乡镇县条请联执证机关样余灯压览据汇银厅场厂断缘纳经费约缴额别组数电计税内优离转",
    "調節務號業營種類價時間樓帳單發動應總減碼帳網輸續線饋項戶統編規則說幣記錄處區鄉鎮縣條請聯執證機關樣餘燈壓覽據匯銀廳場廠斷緣納經費約繳額別組數電計稅內優離轉",
)


def _normalize(text: Any) -> str:
    """Compare labels and values across OCR engines and glyph variants."""
    return _parser_compact(str(text).translate(_TRADITIONAL))

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
    "non_contract_basic_charge_twd": "基本電費(非約定)",
    "taipower_business_tax_id": "本公司營利事業統一編號",
    "invoice_period": "發票期別",
    "remittance_bank": "匯款銀行",
    "remittance_account": "匯款帳號",
    "payee_name": "收款人戶名",
    "service_unit_address": "服務地址",
    "next_debit_date": "下次電費扣繳日",
    "debit_date": "本次電費扣繳日",
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
    # Normalising drops punctuation, masking stars and case, and folds glyph
    # variants, so 台北市 * * * and 台北市*** or （ and ( compare equal.
    return _normalize(a) == _normalize(b)


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
# Other printed wordings of known items.
_LABEL_ALIASES = {
    "用電種類": "tariff_type",
    "用戶營利事業統一編號": "business_tax_id",
    "營利事業統一編號": "business_tax_id",
    "服務電話": "customer_service_phone",
    "本次抄表日": "current_reading_date",
    "用電計費期間": "billing_period",
    "繳費總金額": "total_amount_twd",
    "基本電費約定": "basic_charge_twd",
    "經常需量": "max_demand_kw",
    "經常尖峰需量": "max_demand_kw",
    "經常尖峰契約": "contract_capacity_kw",
    "經常契約": "contract_capacity_kw",
}
_LABEL_TO_KEY = {
    _normalize(label): key for key, label in KEY_LABELS.items() if key not in _NESTED_ONLY_KEYS
} | {_normalize(label): key for label, key in _LABEL_ALIASES.items()}
_NUMERIC_SUFFIXES = ("_twd", "_kwh", "_kw", "_kg", "_percent")
_NOTE_START = re.compile(r"^\s*(?:\d+[.、．]|[◆※■●◎©]|註)")


def _known_key(label: str) -> tuple[str | None, bool]:
    """Map a printed label to a known key; the flag is True for a fuzzy match.

    OCR misreads glyphs but rarely adds or drops them, so a fuzzy match must
    have the same length and differ in at most one glyph (two for labels of
    seven or more): 功率因數调整簧 is 功率因數調整費, while 逾繳費期限 (one glyph
    longer) is not 繳費期限. Different wordings of an item are listed in
    ``_LABEL_ALIASES`` instead. English halves of bilingual labels are ignored.
    """
    normalized = re.sub(r"[a-z]", "", _normalize(label))
    if normalized in _LABEL_TO_KEY:
        return _LABEL_TO_KEY[normalized], False
    if len(normalized) < 4:
        return None, False
    allowed = 2 if len(normalized) >= 7 else 1
    close = {
        key
        for candidate, key in _LABEL_TO_KEY.items()
        if len(candidate) == len(normalized)
        and sum(a != b for a, b in zip(candidate, normalized)) <= allowed
    }
    if len(close) == 1:
        return close.pop(), True
    return None, False


_NOT_A_NUMBER = object()


def _typed(key: str, value: Any) -> Any:
    """Turn '849680.0元' into a number for amount/quantity keys.

    Returns ``_NOT_A_NUMBER`` when such a key gets text that is not a number
    ('****2.09.4元'), so the reading is not filed under the known item.
    """
    if not key.endswith(_NUMERIC_SUFFIXES):
        return value
    number = _as_number(value)
    if number is None:
        return _NOT_A_NUMBER
    return int(number) if number.is_integer() else number


@dataclass
class _Extraction:
    """One engine's reading of a page, normalised for comparison."""

    fields: dict[tuple[str, ...], Any]
    tables: list[tuple[list[str], int, str, Any]]
    printed: dict[tuple[str, ...], str]  # path -> label as printed, when it differs
    headings: dict[str, set[str]]  # printed labels per block, for mispair checks
    duplicates: int = 0
    from_ocr_lines: set[tuple[str, ...]] = field(default_factory=set)

    @property
    def supplemented(self) -> int:
        return len(self.from_ocr_lines)


def _canonicalize(extraction: _Extraction) -> None:
    """Map printed-label keys onto known keys and drop duplicate copies.

    The parser keeps unknown 'label: value' pairs under their printed label,
    which also catches known items it stored elsewhere (優惠度數, or 尖峰度數
    already held under 計費度數).
    """
    known_values: dict[tuple[str, str], list[Any]] = {}
    for path, value in extraction.fields.items():
        if all(_is_known(part) for part in path[1:]):
            known_values.setdefault((path[0], _normalize(KEY_LABELS[path[-1]])), []).append(value)

    result: dict[tuple[str, ...], Any] = {}
    for path, value in extraction.fields.items():
        if len(path) == 2 and not _is_known(path[1]):
            if any(_same(value, known) for known in known_values.get((path[0], _normalize(path[1])), [])):
                extraction.duplicates += 1
                continue
            key, _ = _known_key(path[1])
            typed = _typed(key, value) if key else _NOT_A_NUMBER
            if (
                key is not None
                and (path[0], key) not in extraction.fields
                and typed is not _NOT_A_NUMBER
                and not _is_header_mispair(path, value, extraction.headings.get(path[0], set()))
            ):
                result[(path[0], key)] = typed
                if _normalize(path[1]) != _normalize(KEY_LABELS[key]):
                    extraction.printed[(path[0], key)] = path[1]
                continue
        result[path] = value
    extraction.fields = result


def _used_texts(extraction: _Extraction) -> list[str]:
    texts = [_normalize(value) for value in extraction.fields.values()]
    texts += [_normalize(part) for path in extraction.fields for part in path[1:]]
    texts += [_normalize(KEY_LABELS.get(part, part)) for path in extraction.fields for part in path[1:]]
    texts += [_normalize(cell) for *_, cell in extraction.tables if cell is not None]
    texts += [_normalize(label) for label in extraction.printed.values()]
    return [text for text in texts if len(text) >= 2]


def _is_used(text: str, used: list[str]) -> bool:
    normalized = _normalize(text)
    return len(normalized) < 2 or any(item in normalized or normalized in item for item in used)


def _looks_like_label(text: str) -> bool:
    normalized = _normalize(text)
    return (
        2 <= len(normalized) <= 16
        and len(re.findall(r"[㐀-鿿]", normalized)) >= 2
        and not _NOTE_START.match(text)
        and "。" not in text
    )


def _same_row(a: OcrLine, b: OcrLine) -> bool:
    return abs((a.y + a.height / 2) - (b.y + b.height / 2)) <= max(a.height, b.height) * 0.6


def _supplement(extraction: _Extraction, lines: list[OcrLine]) -> None:
    """Add 'label: value' items the parser left in unused OCR lines.

    Handles a label and value in one line (用電地址：…), a label with its value
    on the same row, values continued on the next line (addresses), and
    combined labels such as 本次抄表日/收費日：113/04/10;113/04/18.
    """
    used = _used_texts(extraction)
    taken: set[int] = set()
    ordered = sorted(lines, key=lambda line: (line.y, line.x))

    def add(block: str, label: str, value: str) -> None:
        # A label wrapped onto the value's line ('本次抄表日/收費' + '日:113/…').
        fragment = re.match(r"^([㐀-鿿]{1,2})\s*[：:]\s*", value)
        if fragment:
            label, value = label + fragment.group(1), value[fragment.end():]
        labels, values = label.split("/"), re.split(r"[;；]", value)
        if len(labels) > 1 and len(values) == 1:  # dates run together: 113/01/30113/02/23
            values = re.findall(r"\d{2,3}/\d{1,2}/\d{1,2}", value) or values
        if len(labels) > 1 and len(labels) == len(values):  # 本次抄表日/收費日：d1;d2
            for part_label, part_value in zip(labels, values):
                add(block, part_label, part_value)
            return
        value = value.strip(" ：:;；,，")
        if not value or _normalize(value) == _normalize(label):
            return
        if _normalize(value) in _KNOWN_LABELS or (
            not re.search(r"\d", value) and re.search(r"[A-Za-z]{4,}", value)
        ):
            return  # a heading, not a value
        key, fuzzy = _known_key(label)
        typed = _typed(key, value) if key else value
        if typed is _NOT_A_NUMBER:
            key, fuzzy, typed = None, False, value
        path = (block, key) if key else (block, re.sub(r"\s+", "", label).strip("：:"))
        if path in extraction.fields:
            return
        extraction.fields[path] = typed
        if key and (fuzzy or _normalize(label) != _normalize(KEY_LABELS[key])):
            extraction.printed[path] = label.strip()
        extraction.from_ocr_lines.add(path)

    for index, line in enumerate(ordered):
        if id(line) in taken or _is_used(line.text, used):
            continue
        block = line.block_key or "page"
        text = line.text.strip()
        match = re.match(r"^(?P<label>[^：:]{2,24}?)\s*[：:](?P<value>.*)$", text)
        if match and _looks_like_label(match["label"]):
            label, value = match["label"].strip(), match["value"].strip()
            if "地址" in label and value:  # addresses wrap onto the next line
                for follower in ordered[index + 1:index + 3]:
                    if (
                        follower.block_key == line.block_key
                        and 0 < follower.y - line.y <= line.height * 1.8
                        and not re.search(r"[：:]", follower.text)
                        and not _is_used(follower.text, used)
                    ):
                        value += follower.text.strip()
                        taken.add(id(follower))
            labels, values = label.split("/"), re.split(r"[;；]", value)
            if len(labels) > 1 and len(labels) == len(values):
                for part_label, part_value in zip(labels, values):
                    add(block, part_label, part_value)
            elif value:
                add(block, label, value)
            else:  # 'label：' with the value printed further along the row
                partner = _row_partner(line, ordered, used, taken)
                if partner is not None:
                    add(block, label, partner.text)
                    taken.add(id(partner))
            taken.add(id(line))
        elif glued := re.match(
            r"^(?P<label>[㐀-鿿()（）]{3,16}?)\s*(?P<value>-?[\d,]+(?:[.·]\d+)?\s*(?:元|度|公斤|%)?)$", text
        ):  # label and number run together: 每度繳交再生基金0.0023元
            add(block, glued["label"], glued["value"])
            taken.add(id(line))
        elif not re.search(r"\d", text) and _looks_like_label(text):
            partner = _row_partner(line, ordered, used, taken)
            if partner is not None and re.search(r"\d", partner.text):
                add(block, text, partner.text)
                taken.update((id(line), id(partner)))


def _row_partner(label: OcrLine, lines: list[OcrLine], used: list[str], taken: set[int]) -> OcrLine | None:
    candidates = [
        other
        for other in lines
        if other is not label
        and id(other) not in taken
        and other.block_key == label.block_key
        and _same_row(label, other)
        and other.x >= label.x + label.width * 0.5
        and not _is_used(other.text, used)
        and not _looks_like_label(other.text.rstrip("：:"))
        and not re.match(r"^[^：:]{2,24}[：:]", other.text)
    ]
    return min(candidates, key=lambda other: other.x, default=None)


def _extract(bill: BillResult) -> _Extraction:
    fields, tables = _flatten(bill)
    headings: dict[str, set[str]] = {}
    for path in fields:  # headings as printed, before keys are canonicalised
        headings.setdefault(path[0], set()).update(path[1:])
    extraction = _Extraction(fields, tables, {}, headings)
    _canonicalize(extraction)
    _supplement(extraction, list(bill.ocr_lines))
    return extraction


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
    candidates = [line for line in lines if (line.block_key or "page") == block] or lines
    number = _as_number(value)
    if number is not None and not isinstance(value, str):
        matches = [
            line
            for line in candidates
            if any(_as_number(token) == number for token in _NUMBER.findall(line.text.replace("·", ".")))
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
    mine = _extract(bill)
    theirs = _extract(cross_check) if cross_check is not None else None
    primary = mine.fields
    other = theirs.fields if theirs is not None else {}
    # Unrecognised labels are matched across engines by their normalised text.
    other_by_label = {(path[0], *map(_normalize, path[1:])): value for path, value in other.items()}
    lines = list(bill.ocr_lines)
    other_lines = list(cross_check.ocr_lines) if cross_check is not None else []

    fields: list[dict[str, Any]] = []
    dropped = 0
    paths = list(primary) + [
        path for path in other if path not in primary and all(_is_known(part) for part in path[1:])
    ]
    for path in paths:
        recognized = all(_is_known(part) for part in path[1:])
        printed = mine.printed.get(path)
        origin = "ocr_line" if path in mine.from_ocr_lines else "parser"
        if path in primary:
            value = primary[path]
            if not recognized and _is_header_mispair(path, value, mine.headings.get(path[0], set())):
                dropped += 1
                continue
            alternative = None
            if not recognized:
                # Both engines reading the same label and value is evidence;
                # anything else stays unverified rather than flagging review.
                twin = other_by_label.get((path[0], *map(_normalize, path[1:])))
                status = AGREE if twin is not None and _same(value, twin) else UNVERIFIED
            elif theirs is None or path not in other:
                status = PRIMARY_ONLY
            elif _same(value, other[path]):
                status = AGREE
            else:
                status, alternative = DISAGREE, other[path]
            source, box = "primary", _locate(lines, path[0], value)
        else:
            value, status, alternative = other[path], FILLED, None
            printed = theirs.printed.get(path) if theirs is not None else None
            origin = "ocr_line" if theirs is not None and path in theirs.from_ocr_lines else "parser"
            source, box = "cross_check", _locate(other_lines, path[0], value)
        if value is None:
            continue
        fields.append(
            {
                "block": path[0],
                "block_label": _BLOCK_LABELS.get(path[0], "頁面" if path[0] == "page" else path[0]),
                "key": ".".join(path[1:]),
                "label": _label(list(path[1:])),
                "printed_label": printed,
                "value": value,
                "recognized": recognized,
                "status": status,
                "cross_check_value": alternative,
                "source": source,
                "origin": origin,
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
        for path, index, column, cell in mine.tables
    ]

    period_start = _first(primary, ("user_info", "billing_period", "start"), ("payment_info", "billing_period", "start"))
    period_end = _first(primary, ("user_info", "billing_period", "end"), ("payment_info", "billing_period", "end"))
    counts = {
        status: sum(1 for item in fields if item["status"] == status)
        for status in (AGREE, DISAGREE, FILLED, PRIMARY_ONLY, UNVERIFIED)
    }
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
        "fields_unrecognized": sum(1 for item in fields if not item["recognized"]),
        "engines_agree": counts[AGREE],
        "engines_disagree": counts[DISAGREE],
        "filled_from_cross_check": counts[FILLED],
        "primary_only": counts[PRIMARY_ONLY],
        "unverified": counts[UNVERIFIED],
        "supplemented_from_ocr_lines": mine.supplemented,
        "header_mispairs_dropped": dropped,
        "duplicates_dropped": mine.duplicates,
        "needs_review": bool(counts[DISAGREE] or warnings),
        "warnings": "; ".join(warnings) or None,
    }
    ocr_lines = [
        {"text": line.text, "x": round(line.x, 1), "y": round(line.y, 1), "width": round(line.width, 1), "height": round(line.height, 1), "block": line.block_key}
        for line in lines
    ]
    quality = {key: value for key, value in (bill.get("quality") or {}).items() if key != "warnings"}
    return PageRecord(summary, fields, table_rows, ocr_lines, quality)
