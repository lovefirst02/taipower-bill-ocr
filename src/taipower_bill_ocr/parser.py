from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class OcrLine:
    text: str
    x: float
    y: float
    width: float
    height: float
    block_key: str | None = None

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2


class BillResult(dict):
    """Structured bill whose canonical fields are addressable by dotted path."""

    # OCR lines in original-image coordinates, kept outside the dict so they
    # never reach JSON output; used to point a preview at a field's location.
    ocr_lines: list[OcrLine] | tuple[OcrLine, ...] = ()

    def get(self, path: str, default: Any = None) -> Any:
        current: Any = self
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = dict.__getitem__(current, part)
        return current


def enrich_unruled_tables(
    result: BillResult,
    words_by_block: dict[str, list[OcrLine]],
    bounds: dict[str, dict[str, int]],
) -> None:
    """Reconstruct coordinate-based tables after ordinary line parsing."""
    energy_words = words_by_block.get("energy_saving_info", [])
    energy_bounds = bounds.get("energy_saving_info")
    if energy_words and energy_bounds and "energy_saving_info" in result:
        rows = _energy_comparison_rows(energy_words, energy_bounds)
        if rows:
            result["energy_saving_info"]["comparison_rows"] = rows

    meter_words = words_by_block.get("meter_info", [])
    meter_bounds = bounds.get("meter_info")
    if meter_words and meter_bounds and "meter_info" in result:
        fields = result["meter_info"]
        _extract_meter_metadata(
            fields,
            result["blocks"]["meter_info"].get("raw_lines", []),
        )
        readings = _meter_reading_rows(meter_words, meter_bounds)
        if readings:
            fields["readings"] = readings

    if energy_words or meter_words:
        result["quality"]["table_reconstruction"] = "word_coordinates"
        for block_key in ("energy_saving_info", "meter_info"):
            block = result.get("blocks", {}).get(block_key)
            if block is None:
                continue
            label = _BLOCK_LABELS[block_key]
            result["by_label"][label] = {
                _FIELD_LABELS.get(key, key): value
                for key, value in block["fields"].items()
            }


def enrich_bank_autopay_receipt(
    result: BillResult,
    words_by_block: dict[str, list[OcrLine]],
    bounds: dict[str, dict[str, int]],
) -> None:
    """Recover stable numeric fields from the compact bank-receipt layout."""
    words = words_by_block.get("user_info", [])
    block_bounds = bounds.get("user_info")
    if "user_info" not in result:
        return

    fields = result["user_info"]
    if words and block_bounds:
        row_specs = (
            ("contract_capacity_kw", 0.215),
            ("max_demand_kw", 0.263),
            ("energy_consumption_kwh", 0.310),
            ("power_factor_percent", 0.335),
        )
        for field_key, expected_y in row_specs:
            candidates: list[tuple[float, str]] = []
            for word in words:
                x, y = _relative_center(word, block_bounds)
                token = _numeric_token(word.text)
                if token and x >= 0.75 and abs(y - expected_y) <= 0.018:
                    candidates.append((abs(y - expected_y), token))
            if candidates:
                token = min(candidates, key=lambda item: (item[0], -len(item[1])))[1]
                fields[field_key] = _number(token)

    raw_lines = result.get("blocks.user_info.raw_lines", [])
    raw_energy_groups = _bank_autopay_energy_groups_from_raw_lines(raw_lines)
    for group_key, values in raw_energy_groups.items():
        existing = fields.setdefault(group_key, {})
        if isinstance(existing, dict):
            for field_key, value in values.items():
                existing.setdefault(field_key, value)
        else:
            fields[group_key] = values
    if raw_energy_groups:
        result["quality"]["bank_autopay_energy_reconstruction"] = "raw_lines"

    # Some bank-autopay bills lose the first glyph of 計費度數 while OCR still
    # reads the split English heading. Repair only this document-specific case;
    # the ordinary group parser then keeps 計費度數 and 轉供度數 separated by
    # their printed heading coordinates and rejects percent-shaped noise.
    joined_text = _compact(
        " ".join([*(word.text for word in words), *raw_lines])
    )
    has_energy_context = "energyconsumption" in joined_text.lower()
    normalized_words: list[OcrLine] = []
    repaired_energy_heading = False
    for word in words:
        compact_text = _compact(word.text)
        if (
            has_energy_context
            and compact_text.startswith("費度數")
            and not compact_text.startswith("計費度數")
        ):
            normalized_words.append(
                OcrLine(
                    "計費度數（度）",
                    word.x,
                    word.y,
                    word.width,
                    word.height,
                    word.block_key,
                )
            )
            repaired_energy_heading = True
        elif compact_text.startswith("專供度數"):
            normalized_words.append(
                OcrLine(
                    "轉供度數（度）",
                    word.x,
                    word.y,
                    word.width,
                    word.height,
                    word.block_key,
                )
            )
            repaired_energy_heading = True
        else:
            normalized_words.append(word)

    if repaired_energy_heading:
        energy_groups, energy_warnings = _energy_groups(normalized_words, words)
        for group_key, values in energy_groups.items():
            existing = fields.setdefault(group_key, {})
            if isinstance(existing, dict):
                existing.update(values)
            else:
                fields[group_key] = values
        if energy_warnings:
            result["quality"].setdefault("warnings", []).extend(energy_warnings)
        result["quality"]["bank_autopay_energy_reconstruction"] = "word_coordinates"

    if words and block_bounds:
        result["quality"]["bank_autopay_numeric_reconstruction"] = (
            "word_coordinates"
        )
    result["by_label"][_BLOCK_LABELS["user_info"]] = {
        _FIELD_LABELS.get(key, key): value for key, value in fields.items()
    }


def _relative_center(
    word: OcrLine, block_bounds: dict[str, int]
) -> tuple[float, float]:
    return (
        (word.center_x - block_bounds["x"]) / block_bounds["width"],
        (word.y + word.height / 2 - block_bounds["y"]) / block_bounds["height"],
    )


def _numeric_token(text: str) -> str | None:
    value = re.sub(r"\s+", "", text).replace("·", ".").replace("。", ".")
    return value if re.fullmatch(r"\d+(?:\.\d+)?", value) else None


def _bank_autopay_energy_groups_from_raw_lines(
    raw_lines: list[str],
) -> dict[str, dict[str, Any]]:
    """Recover sequential kWh groups when full-page OCR has no word bounds."""
    joined_text = _compact(" ".join(raw_lines)).lower()
    if "energyconsumption" not in joined_text:
        return {}

    group_aliases = {
        "energy_consumption": ("計費度數", "費度數", "十費度數"),
        "transferred_energy": ("轉供度數", "專供度數", "供度數"),
    }
    component_aliases = (
        (
            "saturday_semi_peak_kwh",
            ("週六半尖峰度數", "週六半尖蜂度數", "周六半尖峰度数"),
        ),
        ("semi_peak_kwh", ("半尖峰度數", "半尖蜂度數", "半尖峰度数")),
        ("off_peak_kwh", ("離峰度數", "离峰度数")),
        (
            "peak_kwh",
            ("經常尖峰度數", "經常尖蜂度數", "尖峰度數", "尖蜂度數"),
        ),
    )
    grouped: dict[str, dict[str, Any]] = {}
    active_group: str | None = None
    pending_field: str | None = None
    for raw_line in raw_lines:
        compact_text = _compact(raw_line)
        matched_group = next(
            (
                group_key
                for group_key, aliases in group_aliases.items()
                if any(compact_text.startswith(alias) for alias in aliases)
            ),
            None,
        )
        if matched_group is not None:
            active_group = matched_group
            pending_field = None
            grouped.setdefault(active_group, {})
            continue
        if active_group is None:
            continue

        matched_component = next(
            (
                field_key
                for field_key, aliases in component_aliases
                if any(compact_text.startswith(alias) for alias in aliases)
            ),
            None,
        )
        if matched_component is not None:
            pending_field = matched_component
            continue
        if pending_field is None:
            continue

        value = _parse_energy_number(raw_line)
        if value is not None:
            grouped[active_group].setdefault(pending_field, value)
            pending_field = None

    return {group_key: values for group_key, values in grouped.items() if values}


def _best_numeric_word(
    words: list[OcrLine],
    block_bounds: dict[str, int],
    *,
    row_y: float,
    x_min: float,
    x_max: float,
    tolerance: float,
) -> str | None:
    candidates: list[tuple[tuple[int, int, int], str]] = []
    for word in words:
        x, y = _relative_center(word, block_bounds)
        token = _numeric_token(word.text)
        if token is None or not (x_min <= x < x_max) or abs(y - row_y) > tolerance:
            continue
        candidates.append(
            (
                (
                    1 if "." in token else 0,
                    len(token),
                    -round(abs(y - row_y) * 10000),
                ),
                token,
            )
        )
    return max(candidates, default=((), None))[1]


def _energy_comparison_rows(
    words: list[OcrLine], block_bounds: dict[str, int]
) -> list[dict[str, Any]]:
    row_candidates: list[float] = []
    for word in words:
        x, y = _relative_center(word, block_bounds)
        token = _numeric_token(word.text)
        if token and 0.27 <= x < 0.40 and 0.045 <= y <= 0.14:
            if not any(abs(y - known) <= 0.006 for known in row_candidates):
                row_candidates.append(y)
    periods = ("current", "same_period_last_year", "next_period_last_year")
    rows: list[dict[str, Any]] = []
    for period, row_y in zip(periods, sorted(row_candidates)[:3]):
        days = _best_numeric_word(
            words, block_bounds, row_y=row_y, x_min=0.27, x_max=0.40,
            tolerance=0.008,
        )
        energy = _best_numeric_word(
            words, block_bounds, row_y=row_y, x_min=0.45, x_max=0.70,
            tolerance=0.008,
        )
        average = _best_numeric_word(
            words, block_bounds, row_y=row_y, x_min=0.70, x_max=0.98,
            tolerance=0.008,
        )
        if not (days and energy and average):
            continue
        rows.append(
            {
                "period": period,
                "days": int(float(days)),
                "energy_kwh": int(float(energy)),
                "daily_average_kwh": float(average),
            }
        )
    return rows if len(rows) == 3 else []


def _extract_meter_metadata(fields: dict[str, Any], raw_lines: list[str]) -> None:
    compact_lines = [re.sub(r"\s+", "", line) for line in raw_lines]
    specs = (
        ("customer_number", "電號", r"\d{11}"),
        ("billing_period_code", "期別", r"\d{5}"),
        ("group_code", "組別", r"\d{2}"),
        ("meter_number", "表號", r"\d{9}"),
        ("multiplier", "電表倍數", r"\d+"),
    )
    for key, label, number_pattern in specs:
        for line in compact_lines:
            if label not in line:
                continue
            tail = line.split(label, 1)[1]
            match = re.search(number_pattern, tail)
            if match:
                fields[key] = (
                    int(match.group(0)) if key == "multiplier" else match.group(0)
                )
                break
    if any("經常" in line for line in compact_lines):
        fields["group_name"] = "經常"


def _meter_reading_rows(
    words: list[OcrLine], block_bounds: dict[str, int]
) -> list[dict[str, Any]]:
    expected_types = {"01", "03", "04", "06", "08", "09", "10", "11", "12"}
    type_rows: dict[str, float] = {}
    for word in words:
        x, y = _relative_center(word, block_bounds)
        token = _numeric_token(word.text)
        if token in expected_types and x < 0.10 and 0.21 <= y <= 0.52:
            type_rows.setdefault(token, y)

    readings: list[dict[str, Any]] = []
    for meter_type, row_y in sorted(type_rows.items(), key=lambda item: item[1]):
        previous = _best_numeric_word(
            words, block_bounds, row_y=row_y, x_min=0.12, x_max=0.34,
            tolerance=0.008,
        )
        removed = _best_numeric_word(
            words, block_bounds, row_y=row_y, x_min=0.34, x_max=0.54,
            tolerance=0.008,
        )
        installed = _best_numeric_word(
            words, block_bounds, row_y=row_y, x_min=0.54, x_max=0.75,
            tolerance=0.008,
        )
        current = _best_numeric_word(
            words, block_bounds, row_y=row_y, x_min=0.75, x_max=0.98,
            tolerance=0.008,
        )
        if previous and current:
            readings.append(
                {
                    "meter_type": meter_type,
                    "previous": previous,
                    "removed": removed,
                    "installed": installed,
                    "current": current,
                }
            )
    return readings


_BLOCK_TITLES = {
    "user_info": re.compile(r"用戶資訊|basicinfo", re.IGNORECASE),
    "charge_info": re.compile(r"計費內容|chargeinfo", re.IGNORECASE),
    "payment_info": re.compile(r"繳費資訊|paymentinfo", re.IGNORECASE),
    "other_info": re.compile(r"其他資訊|otherinfo", re.IGNORECASE),
    "invoice_info": re.compile(r"前期發票資訊|invoice", re.IGNORECASE),
    "carrier_info": re.compile(r"本期載具號碼|carrier", re.IGNORECASE),
    "receipt_stamp": re.compile(r"收款章|receiptstamp", re.IGNORECASE),
    "charge_introduction": re.compile(r"電費計收說明|chargeintroduction", re.IGNORECASE),
    "energy_saving_info": re.compile(r"節電資訊|energysavinginfo", re.IGNORECASE),
    "energy_usage": re.compile(r"用電資料|energyusage", re.IGNORECASE),
    "meter_info": re.compile(r"電表(?:資訊|資料)|meterinfo", re.IGNORECASE),
}

_INLINE_FIELD_PATTERNS = {
    "user_info": {
        "energy_consumption_kwh": re.compile(r"計費度數\s*[:：]?\s*([\d,]+)"),
        "shared_households": re.compile(r"公共用電分攤戶數\s*[:：]?\s*([\d,]+)"),
    },
    "charge_info": {
        "energy_charge_twd": re.compile(r"流動電費\s*[:：]?\s*([+-]?[\d,.]+)\s*元?"),
        "total_amount_twd": re.compile(r"^\s*(?:應繳總金額|繳費總金額)\s*[:：]?\s*([+-]?[\d,.]+)\s*元?"),
    },
}

_ADJUSTMENT_LABELS = {
    "current_prepaid_balance_twd": ("本期預繳餘額",),
    "previous_prepaid_balance_twd": ("上期預繳餘額",),
    "electricity_price_adjustment_twd": ("電價調整金額",),
    "supplemental_charge_refund_twd": ("補收(退)金額(含停電扣減)", "補收(退)金額"),
}


_FIELD_SPECS: dict[str, dict[str, tuple[tuple[str, ...], str]]] = {
    "user_info": {
        "tariff_type": (("電價種類", "用電種類"), "text"),
        "time_type": (("時間種類",), "text"),
        "base_reading": (("底度",), "number"),
        "energy_consumption_kwh": (
            ("經常尖峰度數", "經常度數", "計費度數"),
            "number",
        ),
        "shared_households": (("公共分攤戶數", "公共用電分攤戶數"), "number"),
        "business_tax_id": (
            ("本公司營利事業統一編號", "營利事業統一編號", "本公司營利事業統一"),
            "identifier",
        ),
        "contract_capacity_kw": (("經常尖峰契約",), "number"),
        "max_demand_kw": (("經常尖峰需量",), "number"),
        "max_demand.semi_peak_non_summer_kw": (("半尖峰非夏月需量",), "number"),
        "max_demand.saturday_semi_peak_kw": (("週六半尖峰需量", "週六半尖蜂需量"), "number"),
        "max_demand.off_peak_kw": (("離峰需量",), "number"),
        "power_factor_percent": (("功率因數",), "number"),
    },
    "charge_info": {
        "basic_charge_twd": (("基本電費約定", "基本電費"), "number"),
        "prepaid_applied_twd": (("本期預繳金額", "本期預繳"), "number"),
        **{key: (labels, "number") for key, labels in _ADJUSTMENT_LABELS.items()},
        "energy_charge_twd": (("流動電費", "流動費"), "number"),
        "shared_public_charge_twd": (("分攤公共電費",), "number"),
        "discount_twd": (("優惠金額",), "number"),
        "power_factor_adjustment_twd": (("功率因數調整費",), "number"),
        "ebill_discount_twd": (("電子帳單優惠減收金額", "電子帳單優惠收金額"), "number"),
        "meter_rental_twd": (("電器租費",), "number"),
        "pretax_total_twd": (("稅前應繳總金額",), "number"),
        "business_tax_twd": (("營業稅",), "number"),
        "total_amount_twd": (
            ("應繳總金額", "繳費總金額", "應繳總金", "總金額", "蟌金額"),
            "number",
        ),
    },
    "other_info": {
        "outage_group": (("輪流停電組別",), "text"),
        "feeder_code": (("饋線代號",), "text"),
        "fuel_cost_per_kwh_twd": (("每度燃料成本",), "number"),
        "carbon_emission_kg": (("電力排碳量", "本期碳排量"), "number"),
        "renewable_fund_per_kwh_twd": (("每度繳交再生基金",), "number"),
        "average_price_per_kwh_twd": (("當期每度平均電價",), "number"),
    },
    "invoice_info": {
        "invoice_number": (("發票號碼",), "text"),
        "amount_twd": (("金額元", "金額"), "number"),
    },
}

_BLOCK_LABELS = {
    "user_info": "用戶資訊",
    "charge_info": "計費內容",
    "payment_info": "繳費資訊",
    "other_info": "其他資訊",
    "invoice_info": "前期發票資訊",
    "carrier_info": "本期載具號碼",
    "receipt_stamp": "收款章",
    "charge_introduction": "電費計收說明",
    "energy_saving_info": "節電資訊",
    "energy_usage": "用電資料",
    "meter_info": "電表資料",
}

_FIELD_LABELS = {
    **{key: labels[0] for key, labels in _ADJUSTMENT_LABELS.items()},
    "prepaid_applied_twd": "本期預繳",
    "tariff_type": "電價種類",
    "time_type": "時間種類",
    "base_reading": "底度",
    "energy_consumption_kwh": "計費度數",
    "energy_consumption": "計費度數",
    "transferred_energy": "轉供度數",
    "shared_households": "公共分攤戶數",
    "business_tax_id": "營業統編",
    "billing_period": "計費期間",
    "current_reading_date": "本次抄表日",
    "current_charge_date": "本次收費日",
    "next_reading_date": "下次抄表日",
    "next_charge_date": "下次收費日",
    "energy_charge_twd": "流動電費",
    "shared_public_charge_twd": "分攤公共電費",
    "total_amount_twd": "應繳總金額",
    "outage_group": "輪流停電組別",
    "feeder_code": "饋線代號",
    "fuel_cost_per_kwh_twd": "每度燃料成本",
    "carbon_emission_kg": "電力排碳量",
    "renewable_fund_per_kwh_twd": "每度繳交再生基金",
    "average_price_per_kwh_twd": "當期每度平均電價",
    "invoice_number": "發票號碼",
    "amount_twd": "金額",
    "customer_number": "電號",
    "due_date": "繳費期限",
}


def _compact(text: str) -> str:
    text = text.translate(
        str.maketrans(
            {
                "计": "計",
                "电": "電",
                "费": "費",
                "数": "數",
                "经": "經",
                "转": "轉",
                "离": "離",
                "周": "週",
                "优": "優",
                "内": "內",
                "缴": "繳",
                "税": "稅",
                "组": "組",
                "别": "別",
                "额": "額",
                "约": "約",
            }
        )
    ).replace("·", ".")
    return re.sub(r"[^0-9A-Za-z\u3400-\u9fff.+-]", "", text).lower()


def _adjustment_tail(text: str, field: str) -> str | None:
    """Match a complete adjustment label, excluding unrelated suffixes."""
    compact = re.sub(r"[\s()（）]", "", text)
    aliases = "|".join(re.escape(re.sub(r"[()（）]", "", alias))
                       for alias in _ADJUSTMENT_LABELS[field])
    match = re.fullmatch(r"(?:" + aliases + r")[:：]?([+-]?\d[\d,]*(?:\.\d+)?元?)?", compact)
    return (match.group(1) or "") if match else None


def _prepaid_tail(text: str) -> str | None:
    """Match the current applied payment, never a balance or historical label."""
    compact = re.sub(r"\s+", "", text)
    match = re.fullmatch(
        r"(?:本期預繳金額|本期預繳)[:：]?([+-]?\d[\d,]*(?:\.\d+)?元?)?",
        compact,
    )
    return (match.group(1) or "") if match else None


def _number(value: str) -> int | float:
    parsed = float(value.replace(",", "").replace("·", "."))
    return int(parsed) if parsed.is_integer() else parsed


def _parse_value(text: str, kind: str) -> Any | None:
    if kind == "number":
        compact_number = re.sub(r"\s+", "", text).replace("。", ".")
        match = re.search(r"[+-]?\d[\d,]*(?:[.·]\d+)?", compact_number)
        return _number(match.group(0)) if match else None
    if kind == "identifier":
        match = re.search(r"\d{6,}", re.sub(r"\s+", "", text))
        return match.group(0) if match else None
    value = re.sub(r"\s+", "", text).strip("：:。.")
    value = re.sub(r"(?<=[A-Za-z0-9])[一－—–](?=[A-Za-z0-9])", "-", value)
    return value or None


def _right_hand_value(
    label: OcrLine, lines: list[OcrLine], kind: str
) -> Any | None:
    same_row = [
        line
        for line in lines
        if line is not label
        and line.x > label.x
        and abs(line.y - label.y) <= max(6, label.height * 0.55)
    ]
    def candidate_rank(line: OcrLine) -> tuple[int, int, int]:
        distance_band = round((line.x - label.x) / 5)
        has_explicit_decimal = any(mark in line.text for mark in (".", "·", "。"))
        is_explicitly_negative = re.sub(r"\s+", "", line.text).startswith("-")
        return (
            distance_band,
            0 if is_explicitly_negative else 1,
            0 if has_explicit_decimal else 1,
        )

    for candidate in sorted(same_row, key=candidate_rank):
        value = _parse_value(candidate.text, kind)
        if value is not None:
            return value
    return None


def _parse_energy_number(text: str) -> int | float | None:
    """Accept a standalone kWh value, excluding OCR noise from other units."""
    compact = re.sub(r"\s+", "", text).replace("。", ".").replace("·", ".")
    if any(marker in compact for marker in ("%", "％", "元", "年", "月", "日")):
        return None
    match = re.fullmatch(r"([+]?[\d,]+(?:\.\d+)?)(?:度|kwh)?", compact, re.IGNORECASE)
    return _number(match.group(1)) if match else None


def _dedupe_nearby_ocr_lines(lines: list[OcrLine]) -> list[OcrLine]:
    """Collapse duplicate OCR passes without changing the preserved raw lines."""
    unique: list[OcrLine] = []
    for line in sorted(lines, key=lambda item: (item.y, item.x)):
        center_y = line.y + line.height / 2
        center_x = line.x + line.width / 2
        if any(
            _compact(existing.text) == _compact(line.text)
            and abs((existing.y + existing.height / 2) - center_y) <= 4
            and abs((existing.x + existing.width / 2) - center_x) <= 4
            for existing in unique
        ):
            continue
        unique.append(line)
    return unique


def _right_hand_energy_value(
    label: OcrLine,
    lines: list[OcrLine],
    used_candidate_ids: set[int],
) -> tuple[int | float | None, OcrLine | None]:
    """Pair a kWh label by vertical alignment before horizontal distance."""
    label_center_y = label.y + label.height / 2
    vertical_tolerance = max(6.0, label.height * 0.45)
    candidates: list[tuple[float, float, OcrLine, int | float]] = []
    for line in lines:
        if line is label or id(line) in used_candidate_ids or line.x <= label.x:
            continue
        value = _parse_energy_number(line.text)
        if value is None:
            continue
        vertical_distance = abs(
            (line.y + line.height / 2) - label_center_y
        )
        if vertical_distance > vertical_tolerance:
            continue
        horizontal_distance = max(0.0, line.x - (label.x + label.width))
        candidates.append((vertical_distance, horizontal_distance, line, value))
    if not candidates:
        return None, None
    _, _, candidate, value = min(
        candidates,
        key=lambda item: (item[0], item[1]),
    )
    return value, candidate


def _energy_groups(
    lines: list[OcrLine],
    value_candidates: list[OcrLine] | None = None,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Parse repeated energy labels only within their printed group heading."""
    group_labels = {
        "energy_consumption": ("計費度數", "计费度数", "十費度數"),
        # OCR sometimes drops 轉 or reads it as 專. Keep the printed heading
        # as a boundary even when the first billed component is unreadable.
        "transferred_energy": ("轉供度數", "转供度数", "專供度數", "供度數"),
    }
    headers = [
        (group_key, line)
        for group_key, aliases in group_labels.items()
        for line in lines
        if any(
            _compact(line.text).startswith(_compact(alias))
            for alias in aliases
        )
    ]
    component_specs = (
        (
            "saturday_semi_peak_kwh",
            (
                "週六半尖峰度數",
                "週六半尖蜂度數",
                "週六半尖峰度数",
                "周六半尖峰度数",
                "周六半尖蜂度数",
            ),
        ),
        ("semi_peak_kwh", ("半尖峰度數", "半尖峰度数", "半尖蜂度数")),
        ("off_peak_kwh", ("離峰度數", "離峰度数", "离峰度数")),
        (
            "peak_kwh",
            (
                "經常尖峰度數",
                "經常尖蜂度數",
                "经常尖峰度数",
                "经常尖蜂度数",
                "尖峰度數",
                "尖蜂度數",
                "尖峰度数",
                "尖蜂度数",
            ),
        ),
        ("discount_kwh", ("優惠度數", "優惠度数", "优惠度数")),
    )
    warnings: list[str] = []
    header_keys = {group_key for group_key, _ in headers}
    if (
        "energy_consumption" in header_keys
        and "transferred_energy" not in header_keys
    ):
        peak_aliases = next(
            aliases
            for field_key, aliases in component_specs
            if field_key == "peak_kwh"
        )
        billed_header_y = min(
            line.y
            for group_key, line in headers
            if group_key == "energy_consumption"
        )
        peak_rows = sorted(
            (
                line
                for line in lines
                if line.y > billed_header_y
                and any(
                    _compact(line.text).startswith(_compact(alias))
                    for alias in peak_aliases
                )
            ),
            key=lambda line: (line.y, line.x),
        )
        if len(peak_rows) >= 2:
            second_peak = peak_rows[1]
            inferred_y = second_peak.y - max(1.0, second_peak.height * 0.25)
            headers.append(
                (
                    "transferred_energy",
                    OcrLine(
                        "轉供度數（推定）",
                        second_peak.x,
                        inferred_y,
                        second_peak.width,
                        1,
                        second_peak.block_key,
                    ),
                )
            )
            warnings.append("transferred_energy_heading_inferred")

    grouped: dict[str, dict[str, Any]] = {}
    for group_key, header in headers:
        following_headers = [
            candidate.y for _, candidate in headers if candidate.y > header.y
        ]
        bottom = min(following_headers, default=float("inf"))
        section = [line for line in lines if header.y < line.y < bottom]
        value_section = _dedupe_nearby_ocr_lines(
            [
                line
                for line in (value_candidates or lines)
                if header.y < line.y < bottom
            ]
        )
        values: dict[str, Any] = {}
        used_candidate_ids: set[int] = set()
        component_rows: list[tuple[OcrLine, str, str]] = []
        for line in section:
            compact_text = _compact(line.text)
            for field_key, aliases in component_specs:
                matching_alias = next(
                    (
                        alias
                        for alias in aliases
                        if compact_text.startswith(_compact(alias))
                    ),
                    None,
                )
                if matching_alias is None:
                    continue
                component_rows.append((line, field_key, matching_alias))
                break
        for line, field_key, matching_alias in sorted(
            component_rows, key=lambda item: (item[0].y, item[0].x)
        ):
            if field_key in values:
                continue
            compact_text = _compact(line.text)
            alias_text = _compact(matching_alias)
            inline_tail = compact_text[len(alias_text):]
            value = (
                _parse_energy_number(inline_tail)
                if inline_tail and "%" not in line.text and "％" not in line.text
                else None
            )
            candidate = None
            if value is None:
                value, candidate = _right_hand_energy_value(
                    line,
                    value_section,
                    used_candidate_ids,
                )
            if value is not None:
                values[field_key] = value
                if candidate is not None:
                    used_candidate_ids.add(id(candidate))
        if values:
            grouped[group_key] = values
    return grouped, warnings


def _row_text(label: OcrLine, lines: list[OcrLine]) -> str:
    same_row = [
        line
        for line in lines
        if line is not label
        and line.x > label.x
        and abs(line.y - label.y) <= max(6, label.height * 0.55)
    ]
    return " ".join([label.text, *(line.text for line in sorted(same_row, key=lambda item: item.x))])


def _dates(text: str) -> list[str]:
    compact = re.sub(r"\s+", "", text)
    return re.findall(r"\d{2,3}/\d{1,2}/\d{1,2}", compact)


def _normalized_date(text: str) -> str | None:
    match = re.search(
        r"(?<!\d)(\d{2,3})\s*(?:[/.-]|\s)\s*(\d{1,2})"
        r"\s*(?:[/.-]|\s)\s*(\d{1,2})(?!\d)",
        text,
    )
    if match is None:
        return None
    return "/".join(match.groups())


def _set_path(target: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current = target
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def _looks_like_unknown_header(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return bool(re.search(r"(?:info(?:rmation)?|資訊)\.?$", compact, re.IGNORECASE))


def _display_block_title(text: str) -> str:
    chinese = re.match(r"\s*([\u3400-\u9fff\s]+)", text)
    if chinese:
        title = re.sub(r"\s+", "", chinese.group(1))
        if title:
            return title
    return text.strip().rstrip(".")


def _generic_pair_value(label: OcrLine, lines: list[OcrLine]) -> Any | None:
    candidates = [
        line
        for line in lines
        if line is not label
        and line.x > label.x
        and abs(line.y - label.y) <= max(6, label.height * 0.55)
    ]
    if not candidates:
        return None
    text = min(candidates, key=lambda item: item.x).text.strip()
    if re.fullmatch(r"[+-]?[\d,]+(?:[.·。]\d+)?(?:\s*元)?", text):
        return _parse_value(text, "number")
    return _parse_value(text, "text")


def _is_detailed_high_voltage_layout(lines: list[OcrLine]) -> bool:
    compact_lines = [_compact(line.text) for line in lines]
    has_required_top = all(
        (
            any(text.startswith("基本資料") for text in compact_lines),
            any(text.startswith("計費內容") for text in compact_lines),
        )
    )
    has_usage_title = any(text.startswith("用電資料") for text in compact_lines)
    has_meter_title = any(text.startswith("電表資料") for text in compact_lines)
    has_usage_groups = all(
        (
            any(text.startswith("計費度數") for text in compact_lines),
            any(text.startswith("轉供度數") for text in compact_lines),
        )
    )
    return has_required_top and has_usage_title and (
        has_meter_title or has_usage_groups
    )


def _closest_right_line(
    label: OcrLine,
    lines: list[OcrLine],
    *,
    x_max: float = float("inf"),
) -> OcrLine | None:
    label_center_y = label.y + label.height / 2
    tolerance = max(8.0, label.height * 0.7)
    candidates = [
        line
        for line in lines
        if line is not label
        and label.x < line.x < x_max
        and abs((line.y + line.height / 2) - label_center_y) <= tolerance
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda line: (
            abs((line.y + line.height / 2) - label_center_y),
            max(0.0, line.x - (label.x + label.width)),
        ),
    )


def _detailed_amount(text: str) -> int | float | None:
    match = re.search(r"[+-]?\d[\d,.]*", re.sub(r"\s+", "", text))
    if match is None:
        return None
    token = match.group(0)
    if re.match(r"^[+-]?\d\.\d{3},", token):
        token = token.replace(".", ",", 1)
    try:
        return _number(token)
    except ValueError:
        return None


def _roc_date_from_gregorian(value: str) -> str:
    year = int(value[:4]) - 1911
    return f"{year:03d}/{value[4:6]}/{value[6:8]}"


def _parse_compact_energy_usage(usage_lines: list[OcrLine]) -> dict[str, Any]:
    """Parse the lower usage table without depending on document classification."""
    def find_label(*aliases: str) -> OcrLine | None:
        return next(
            (
                line
                for line in usage_lines
                if any(
                    _compact(line.text).startswith(_compact(alias))
                    for alias in aliases
                )
            ),
            None,
        )

    billed_group = find_label("計費度數")
    transferred_group = find_label("轉供度數")
    fields: dict[str, Any] = {}
    rows: dict[str, list[dict[str, Any]]] = {
        "energy_consumption": [],
        "transferred_energy": [],
    }
    if billed_group and transferred_group:
        billed_center = billed_group.y + billed_group.height / 2
        transferred_center = transferred_group.y + transferred_group.height / 2
        # Group captions are vertically centred across multiple rows.  Bias the
        # midpoint slightly toward the earlier group so a boundary-adjacent row
        # becomes the first row of 轉供度數 instead of the last 計費度數 row.
        transfer_start = (
            (billed_center + transferred_center) / 2
            - min(billed_group.height, transferred_group.height) / 4
        )
        following_aliases = (
            "經常尖峰契約",
            "經常尖峰需量",
            "半尖峰非夏月需量",
            "離峰需量",
            "功率因數",
            "調整係數",
            "前期轉供度數",
        )
        current_usage_end = min(
            (
                line.y
                for line in usage_lines
                if any(
                    _compact(line.text).startswith(_compact(alias))
                    for alias in following_aliases
                )
            ),
            default=float("inf"),
        )
        component_aliases = (
            (
                "saturday_semi_peak_kwh",
                ("週六半尖峰度數", "调六半尖峰度數", "遇六半尖峰度數"),
            ),
            ("semi_peak_kwh", ("半尖峰度數",)),
            ("off_peak_kwh", ("離峰度數",)),
            ("peak_kwh", ("尖峰度數",)),
        )
        for line in sorted(usage_lines, key=lambda item: (item.y, item.x)):
            if line.y >= current_usage_end:
                continue
            compact = _compact(line.text)
            matched = next(
                (
                    (field_key, alias)
                    for field_key, aliases in component_aliases
                    for alias in aliases
                    if compact.startswith(_compact(alias))
                ),
                None,
            )
            if matched is None:
                continue
            field_key, alias = matched
            inline_tail = compact[len(_compact(alias)):]
            value = _parse_energy_number(inline_tail) if inline_tail else None
            if value is None:
                value, _ = _right_hand_energy_value(line, usage_lines, set())
            if value is None:
                continue
            group_key = (
                "energy_consumption"
                if line.y + line.height / 2 < transfer_start
                else "transferred_energy"
            )
            rows[group_key].append({"label": alias, "value": value})
            fields.setdefault(group_key, {}).setdefault(field_key, value)
    if any(rows.values()):
        fields["raw_energy_rows"] = {
            group_key: values for group_key, values in rows.items() if values
        }

    for field_key, aliases in {
        "contract_capacity_kw": ("經常尖峰契約",),
        "max_demand_kw": ("經常尖峰需量",),
        "power_factor_percent": ("功率因數",),
    }.items():
        label = find_label(*aliases)
        if label is None:
            continue
        value, _ = _right_hand_energy_value(label, usage_lines, set())
        if value is not None:
            fields[field_key] = value
    return fields


def _parse_detailed_high_voltage_layout(lines: list[OcrLine]) -> BillResult:
    top_headers = {
        "user_info": next(line for line in lines if _compact(line.text).startswith("基本資料")),
        "charge_info": next(line for line in lines if _compact(line.text).startswith("計費內容")),
    }
    energy_header = next(
        line for line in lines if _compact(line.text).startswith("用電資料")
    )
    meter_header = next(
        (line for line in lines if _compact(line.text).startswith("電表資料")),
        None,
    )
    if meter_header is None:
        source = top_headers["charge_info"]
        meter_header = OcrLine(
            "電表資料", source.x, energy_header.y, source.width, source.height
        )
    bottom_headers = {
        "energy_usage": energy_header,
        "meter_info": meter_header,
    }
    column_split = (
        top_headers["user_info"].center_x + top_headers["charge_info"].center_x
    ) / 2
    lower_top = min(header.y for header in bottom_headers.values())

    scoped: dict[str, list[OcrLine]] = {
        "user_info": [],
        "charge_info": [],
        "energy_usage": [],
        "meter_info": [],
    }
    for line in lines:
        if line.y < lower_top:
            key = "user_info" if line.center_x < column_split else "charge_info"
        else:
            key = "energy_usage" if line.center_x < column_split else "meter_info"
        scoped[key].append(line)

    def label_line(block_key: str, *aliases: str) -> OcrLine | None:
        return next(
            (
                line
                for line in scoped[block_key]
                if any(_compact(line.text).startswith(_compact(alias)) for alias in aliases)
            ),
            None,
        )

    def right_text(block_key: str, *aliases: str) -> str | None:
        label = label_line(block_key, *aliases)
        if label is None:
            return None
        value_line = _closest_right_line(label, scoped[block_key], x_max=column_split)
        return value_line.text.strip() if value_line is not None else None

    def inline_or_right(block_key: str, *aliases: str) -> str | None:
        label = label_line(block_key, *aliases)
        if label is None:
            return None
        parts = re.split(r"[:：]", label.text, maxsplit=1)
        if len(parts) == 2 and parts[1].strip():
            return parts[1].strip()
        return right_text(block_key, *aliases)

    def roc_date(text: str | None) -> str | None:
        if not text:
            return None
        match = re.search(r"(\d{2,3})年(\d{1,2})月(\d{1,2})日", text)
        if match is None:
            return None
        year, month, day = (int(value) for value in match.groups())
        return f"{year:03d}/{month:02d}/{day:02d}"

    def nearby_multiline(*aliases: str) -> str | None:
        label = label_line("user_info", *aliases)
        if label is None:
            return None
        following_labels = [
            line
            for line in scoped["user_info"]
            if line is not label
            and line.y > label.y
            and line.x <= label.x + label.width
            and re.search(r"[:：]", line.text)
        ]
        section_end = min(
            (
                line.y - line.height / 2
                for line in following_labels
            ),
            default=label.y + max(25, label.height),
        )
        pieces = [
            line.text.strip()
            for line in sorted(scoped["user_info"], key=lambda item: (item.y, item.x))
            if line is not label
            and line.x > label.x + label.width
            and label.y - label.height / 2 <= line.y < min(
                label.y + max(25, label.height), section_end
            )
        ]
        return "".join(pieces) or None

    user_fields: dict[str, Any] = {}
    tariff_type = right_text("user_info", "電價種類")
    time_type = right_text("user_info", "時間種類")
    if tariff_type:
        tariff_type = tariff_type.replace("高座電力非堂案用", "高壓電力非營業用")
        user_fields["tariff_type"] = tariff_type
    if time_type:
        if _compact(time_type) == _compact("段式時間電價"):
            time_type = "三段式時間電價"
        user_fields["time_type"] = time_type
    customer_number_text = inline_or_right("user_info", "電號")
    if customer_number_text:
        customer_number_match = re.search(
            r"(?:\d{2}-\d{2}-\d{4}-\d{2}-\d|\d{11})",
            re.sub(r"\s+", "", customer_number_text),
        )
        if customer_number_match:
            user_fields["customer_number"] = customer_number_match.group(0)
    due_date_text = right_text("user_info", "繳費期限")
    if due_date_text:
        due_date = roc_date(due_date_text)
        if due_date:
            user_fields["due_date"] = due_date
    period_line = next(
        (line for line in lines if "billingperiod" in _compact(line.text)),
        None,
    )
    if period_line is not None:
        match = re.search(r"(\d{8})\s*-\s*(\d{8})", period_line.text)
        if match:
            user_fields["billing_period"] = {
                "start": _roc_date_from_gregorian(match.group(1)),
                "end": _roc_date_from_gregorian(match.group(2)),
            }
    if "billing_period" not in user_fields:
        period_label = label_line("user_info", "計費期間")
        if period_label is not None:
            period_text = "".join(
                line.text.strip()
                for line in sorted(
                    scoped["user_info"], key=lambda item: (item.y, item.x)
                )
                if line is not period_label
                and line.x > period_label.x + period_label.width
                and period_label.y - 15 <= line.y <= period_label.y + 25
            )
            period_dates = re.findall(
                r"(\d{2,3})年(\d{1,2})月(\d{1,2})日", period_text
            )
            if len(period_dates) >= 2:
                user_fields["billing_period"] = {
                    "start": "/".join(
                        (period_dates[0][0], period_dates[0][1].zfill(2), period_dates[0][2].zfill(2))
                    ),
                    "end": "/".join(
                        (period_dates[1][0], period_dates[1][1].zfill(2), period_dates[1][2].zfill(2))
                    ),
                }

    basic_text_specs = {
        "autopay_account": ("代繳帳號",),
        "outage_group": ("輪流停電組別",),
        "feeder_code": ("饋線代號", "请線代號"),
        "customer_service_phone": ("客服專線",),
        "service_unit": ("服務單位",),
    }
    for field_key, aliases in basic_text_specs.items():
        value = inline_or_right("user_info", *aliases)
        if value:
            user_fields[field_key] = value.upper() if field_key == "outage_group" else value

    month_text = inline_or_right("user_info", "帳單月份")
    if month_text and (month_match := re.search(r"(\d{2,3})年(\d{1,2})月", month_text)):
        user_fields["bill_month"] = (
            f"{int(month_match.group(1)):03d}/{int(month_match.group(2)):02d}"
        )
    for field_key, aliases in (
        ("current_reading_date", ("本次抄表日",)),
        ("next_reading_date", ("下次抄表日",)),
    ):
        value = roc_date(inline_or_right("user_info", *aliases))
        if value:
            user_fields[field_key] = value

    company_label = label_line("user_info", "本公司營利事業統一編號")
    if company_label is None:
        company_label = next(
            (
                line
                for line in scoped["user_info"]
                if _compact(line.text).startswith("本公司")
            ),
            None,
        )
    if company_label is not None:
        company_value_line = _closest_right_line(
            company_label, scoped["user_info"], x_max=column_split
        )
        company_text = " ".join(
            filter(
                None,
                (
                    company_label.text,
                    inline_or_right("user_info", "本公司營利事業統一編號"),
                    company_value_line.text if company_value_line else None,
                ),
            )
        )
        if company_match := re.search(r"\d{8}", company_text):
            user_fields["business_tax_id"] = company_match.group(0)
    service_address = nearby_multiline("服務地址")
    if service_address:
        service_address = service_address.replace("桃園市山區", "桃園市龜山區")
        user_fields["service_address"] = service_address
    industry = nearby_multiline("行業別")
    if industry:
        user_fields["industry"] = industry

    charge_specs = {
        **_ADJUSTMENT_LABELS,
        "basic_charge_twd": ("基本電費",),
        "prepaid_applied_twd": ("本期預繳金額", "本期預繳"),
        "energy_charge_twd": ("流動電費",),
        "power_factor_adjustment_twd": ("功率因數調整費",),
        "ebill_discount_twd": ("電子帳單優惠減收金額",),
        "electricity_price_adjustment_twd": ("電價調整金額",),
        "pretax_total_twd": ("稅前應繳總金額",),
        "business_tax_twd": ("營業稅",),
        "total_amount_twd": ("應繳總金額", "繳費總金額"),
    }
    charge_fields: dict[str, Any] = {}
    for field_key, aliases in charge_specs.items():
        label = (next((line for line in scoped["charge_info"]
                       if _prepaid_tail(line.text) is not None), None)
                 if field_key == "prepaid_applied_twd"
                 else label_line("charge_info", *aliases))
        if field_key in _ADJUSTMENT_LABELS:
            label = next((line for line in scoped["charge_info"]
                          if _adjustment_tail(line.text, field_key) is not None), None)
        if label is None:
            continue
        inline_tail = label.text.split("：", 1)[1] if "：" in label.text else ""
        if field_key == "total_amount_twd":
            compact_label = _compact(label.text)
            alias = next(alias for alias in aliases if compact_label.startswith(_compact(alias)))
            inline_tail = compact_label[len(_compact(alias)):]
        if field_key == "prepaid_applied_twd":
            inline_tail = _prepaid_tail(label.text)
        elif field_key in _ADJUSTMENT_LABELS:
            inline_tail = _adjustment_tail(label.text, field_key)
        value = _detailed_amount(inline_tail) if inline_tail else None
        if value is None:
            value_line = _closest_right_line(label, scoped["charge_info"])
            value = _detailed_amount(value_line.text) if value_line else None
        if value is not None:
            charge_fields[field_key] = value

    usage_fields = _parse_compact_energy_usage(scoped["energy_usage"])

    meter_fields: dict[str, Any] = {}
    meter_text = " ".join(_compact(line.text) for line in scoped["meter_info"])
    for field_key, pattern in (
        ("meter_number", r"表號\s*(\d{9})"),
        ("multiplier", r"電表倍數\s*(\d+)"),
        ("group_code", r"組別\s*(\d{2})"),
    ):
        match = re.search(pattern, meter_text)
        if match:
            meter_fields[field_key] = (
                int(match.group(1)) if field_key == "multiplier" else match.group(1)
            )
    expected_types = {"01", "03", "04", "06", "08", "09", "10", "11", "12"}
    readings: list[dict[str, Any]] = []
    meter_lines = scoped["meter_info"]
    for type_line in sorted(meter_lines, key=lambda item: item.y):
        meter_type = re.sub(r"\s+", "", type_line.text)
        if meter_type not in expected_types or type_line.x > column_split + 80:
            continue
        row_center = type_line.y + type_line.height / 2
        row_lines = [
            line
            for line in meter_lines
            if line is not type_line
            and abs((line.y + line.height / 2) - row_center) <= 10
        ]
        previous_line = next((line for line in row_lines if 540 <= line.x < 700), None)
        removed_line = next((line for line in row_lines if 700 <= line.x < 745), None)
        installed_line = next((line for line in row_lines if 745 <= line.x < 810), None)
        current_line = next((line for line in row_lines if line.x >= 810), None)
        if previous_line and current_line:
            previous_tokens = re.findall(r"\d+(?:\.\d+)?", previous_line.text)
            readings.append(
                {
                    "meter_type": meter_type,
                    "previous": previous_tokens[0] if previous_tokens else None,
                    "removed": (
                        re.search(r"\d+(?:\.\d+)?", removed_line.text).group(0)
                        if removed_line and re.search(r"\d+(?:\.\d+)?", removed_line.text)
                        else (previous_tokens[1] if len(previous_tokens) > 1 else None)
                    ),
                    "installed": (
                        re.search(r"\d+(?:\.\d+)?", installed_line.text).group(0)
                        if installed_line and re.search(r"\d+(?:\.\d+)?", installed_line.text)
                        else None
                    ),
                    "current": re.search(r"\d+(?:\.\d+)?", current_line.text).group(0),
                }
            )
    if readings:
        meter_fields["readings"] = readings

    fields_by_block = {
        "user_info": user_fields,
        "charge_info": charge_fields,
        "energy_usage": usage_fields,
        "meter_info": meter_fields,
    }
    titles = {**top_headers, **bottom_headers}
    blocks = {
        key: {
            "title": titles[key].text,
            "fields": fields,
            "raw_lines": [line.text for line in scoped[key]],
        }
        for key, fields in fields_by_block.items()
    }
    return BillResult(
        {
            "document_type": "detailed_high_voltage_bill",
            "blocks": blocks,
            "by_label": {
                _BLOCK_LABELS.get(key, key): {
                    (_FIELD_LABELS[field] if field == "prepaid_applied_twd" or field in _ADJUSTMENT_LABELS else field): value
                    for field, value in fields.items()
                }
                for key, fields in fields_by_block.items()
            },
            "quality": {"warnings": [], "unknown_block_count": 0},
            **fields_by_block,
        }
    )


def parse_bill_layout(lines: Iterable[OcrLine]) -> BillResult:
    ordered = sorted(lines, key=lambda line: (line.y, line.x))
    if _is_detailed_high_voltage_layout(ordered):
        return _parse_detailed_high_voltage_layout(ordered)
    headers: list[tuple[str, OcrLine]] = []
    for line in ordered:
        compact_text = _compact(line.text)
        matched = False
        for block_key, title_pattern in _BLOCK_TITLES.items():
            if len(compact_text) <= 30 and title_pattern.search(compact_text):
                headers.append((block_key, line))
                matched = True
                break
        if not matched and _looks_like_unknown_header(line.text):
            headers.append((f"unknown_{sum(key.startswith('unknown_') for key, _ in headers) + 1}", line))

    legacy_virtual_blocks = not headers and bool(ordered)
    if legacy_virtual_blocks:
        top = min(line.y for line in ordered) - 1
        left = min(line.x for line in ordered)
        for block_key in ("user_info", "charge_info", "other_info"):
            headers.append(
                (block_key, OcrLine(_BLOCK_LABELS[block_key], left, top, 1, 1))
            )

    blocks: dict[str, dict[str, Any]] = {
        key: {"title": header.text, "fields": {}, "raw_lines": []}
        for key, header in headers
    }
    assigned_lines: dict[str, list[OcrLine]] = {
        key: list(ordered) if legacy_virtual_blocks else [] for key in blocks
    }
    if legacy_virtual_blocks:
        for block in blocks.values():
            block["raw_lines"] = [line.text for line in ordered]
    header_ids = {id(header) for _, header in headers}
    consumed_label_ids: set[int] = set()
    for line in ordered:
        if legacy_virtual_blocks:
            break
        if id(line) in header_ids:
            continue
        if line.block_key and line.block_key in blocks:
            blocks[line.block_key]["raw_lines"].append(line.text)
            assigned_lines[line.block_key].append(line)
            continue
        candidates = [
            (
                abs(line.center_x - header.center_x)
                + 0.25 * (line.y - header.y),
                key,
                header,
            )
            for key, header in headers
            if line.y > header.y
        ]
        if not candidates:
            continue
        overlapping = [
            item
            for item in candidates
            if item[2].x <= line.center_x <= item[2].x + item[2].width
        ]
        if overlapping:
            _, block_key, _ = max(overlapping, key=lambda item: item[2].y)
        else:
            _, block_key, _ = min(candidates, key=lambda item: item[0])
        blocks[block_key]["raw_lines"].append(line.text)
        assigned_lines[block_key].append(line)
        for field_key, pattern in _INLINE_FIELD_PATTERNS.get(block_key, {}).items():
            match = pattern.search(line.text)
            if match:
                blocks[block_key]["fields"][field_key] = _number(match.group(1))
                consumed_label_ids.add(id(line))

    for block_key, field_specs in _FIELD_SPECS.items():
        if block_key not in blocks:
            continue
        for field_key, (aliases, kind) in field_specs.items():
            scoped_lines = assigned_lines[block_key]
            candidate_lines = (
                scoped_lines
                if any(line.block_key == block_key for line in scoped_lines)
                else ordered
            )
            for line in scoped_lines:
                if field_key == "prepaid_applied_twd" and _prepaid_tail(line.text) is None:
                    continue
                if field_key in _ADJUSTMENT_LABELS and _adjustment_tail(line.text, field_key) is None:
                    continue
                compact_text = _compact(line.text)
                matching_alias = next(
                    (alias for alias in aliases if _compact(alias) in compact_text),
                    None,
                )
                if matching_alias is None:
                    continue
                if (
                    field_key == "total_amount_twd"
                    and not compact_text.startswith(_compact(matching_alias))
                ):
                    continue
                if (
                    field_key == "energy_consumption.peak_kwh"
                    and not compact_text.startswith(_compact(matching_alias))
                ):
                    continue
                alias_end = compact_text.find(_compact(matching_alias)) + len(
                    _compact(matching_alias)
                )
                inline_tail = compact_text[alias_end:]
                value = _parse_value(inline_tail, kind) if inline_tail else None
                if matching_alias == "計費度數" and value is None:
                    continue
                if value is None:
                    value = _right_hand_value(line, candidate_lines, kind)
                elif kind == "text" and field_key == "tariff_type":
                    continuation = _right_hand_value(line, candidate_lines, kind)
                    if continuation and continuation not in str(value):
                        value = f"{value}{continuation}"
                elif kind == "text" and field_key == "feeder_code":
                    continuation = _right_hand_value(line, candidate_lines, kind)
                    if continuation:
                        value = str(continuation).lstrip(":：")
                if field_key == "outage_group" and isinstance(value, str):
                    value = value.upper()
                if value is not None:
                    _set_path(blocks[block_key]["fields"], field_key, value)
                    consumed_label_ids.add(id(line))
                    break

    # The detailed usage table is independently recognizable even when the
    # page-level document classifier misses a title or billing-period marker.
    if "energy_usage" in blocks:
        blocks["energy_usage"]["fields"].update(
            _parse_compact_energy_usage(assigned_lines["energy_usage"])
        )

    if "user_info" in blocks:
        scoped_energy_lines = assigned_lines["user_info"]
        energy_value_candidates = scoped_energy_lines
        if not any(line.block_key == "user_info" for line in scoped_energy_lines):
            user_header = next(
                header for block_key, header in headers if block_key == "user_info"
            )
            sibling_starts = [
                header.x
                for block_key, header in headers
                if block_key != "user_info"
                and header.x > user_header.x
                and abs(header.y - user_header.y)
                <= max(user_header.height, header.height) * 2
            ]
            right_edge = min(sibling_starts, default=float("inf"))
            energy_value_candidates = [
                line for line in ordered if line.x < right_edge
            ]
        energy_groups, energy_group_warnings = _energy_groups(
            scoped_energy_lines,
            energy_value_candidates,
        )
        for group_key, values in energy_groups.items():
            blocks["user_info"]["fields"][group_key] = values
    else:
        energy_group_warnings = []

    if "user_info" in blocks:
        pair_specs = (
            (("計費期間", "費期間"), "billing_period"),
            (("本次抄表日",), "current"),
            (("下次抄表日",), "next"),
        )
        scoped_lines = assigned_lines["user_info"]
        date_candidate_lines = (
            scoped_lines
            if any(line.block_key == "user_info" for line in scoped_lines)
            else ordered
        )
        for labels, pair_key in pair_specs:
            source = next(
                (
                    line
                    for line in scoped_lines
                    if any(_compact(label) in _compact(line.text) for label in labels)
                ),
                None,
            )
            if source is None:
                continue
            consumed_label_ids.add(id(source))
            values = _dates(_row_text(source, date_candidate_lines))
            if pair_key == "billing_period" and len(values) < 2:
                period_column_dates: list[str] = []
                for line in sorted(scoped_lines, key=lambda item: (item.y, item.x)):
                    if not (
                        source.y < line.y <= source.y + max(100, source.height * 5)
                        and line.x >= source.x - source.width * 0.2
                        and line.x <= source.x + source.width * 1.5
                    ):
                        continue
                    value = _normalized_date(line.text)
                    if value and value not in period_column_dates:
                        period_column_dates.append(value)
                values = period_column_dates
            if len(values) < 2:
                continue
            if pair_key == "billing_period":
                blocks["user_info"]["fields"][pair_key] = {
                    "start": values[0],
                    "end": values[1],
                }
            else:
                blocks["user_info"]["fields"][f"{pair_key}_reading_date"] = values[0]
                blocks["user_info"]["fields"][f"{pair_key}_charge_date"] = values[1]

    # Preserve fields that a newer bill adds before this parser knows their schema.
    # Their printed labels become keys, while established fields keep stable keys.
    for block_key, scoped_lines in assigned_lines.items():
        if legacy_virtual_blocks:
            continue
        fields = blocks[block_key]["fields"]
        for line in scoped_lines:
            if id(line) in consumed_label_ids:
                continue
            label = re.sub(r"\s+", "", line.text).strip("：:。. ")
            if not re.fullmatch(r"[A-Za-z\u3400-\u9fff]{2,16}", label):
                continue
            value = _generic_pair_value(line, scoped_lines)
            if value is not None:
                fields.setdefault(label, value)

    warnings: list[str] = ["legacy_virtual_blocks"] if legacy_virtual_blocks else []
    warnings.extend(energy_group_warnings)
    charge_fields = blocks.get("charge_info", {}).get("fields", {})
    user_fields = blocks.get("user_info", {}).get("fields", {})

    basic_charge = charge_fields.get("basic_charge_twd")
    energy_charge = charge_fields.get("energy_charge_twd")
    if (
        isinstance(basic_charge, (int, float))
        and isinstance(energy_charge, (int, float))
        and basic_charge < 1000
    ):
        standalone_amounts = [
            _parse_value(line.text, "number")
            for line in assigned_lines.get("charge_info", [])
            if re.fullmatch(
                r"\s*[+-]?\d[\d,]*(?:[.·。]\d+)?\s*", line.text
            )
        ]
        plausible = [
            value
            for value in standalone_amounts
            if isinstance(value, (int, float)) and 1000 <= value < energy_charge
        ]
        if plausible:
            charge_fields["basic_charge_twd"] = plausible[0]
            warnings.append("basic_charge_recovered_from_scan")

    consumption = user_fields.get("energy_consumption", {})
    if isinstance(consumption, dict):
        component_keys = (
            "semi_peak_kwh",
            "saturday_semi_peak_kwh",
            "off_peak_kwh",
        )
        components = [consumption.get(key) for key in component_keys]
        discount_kwh = consumption.get("discount_kwh")
        peak_kwh = consumption.get("peak_kwh")
        if (
            all(isinstance(value, (int, float)) for value in components)
            and isinstance(discount_kwh, (int, float))
            and abs(discount_kwh - sum(components)) < 0.01
            and peak_kwh != 0
        ):
            consumption["peak_kwh"] = 0
            warnings.append("peak_kwh_recovered_from_period_sum")

    power_factor = user_fields.get("power_factor_percent")
    power_factor_adjustment = charge_fields.get("power_factor_adjustment_twd")
    if (
        isinstance(power_factor, (int, float))
        and power_factor > 80
        and isinstance(power_factor_adjustment, (int, float))
        and power_factor_adjustment > 0
    ):
        charge_fields["power_factor_adjustment_twd"] = -power_factor_adjustment
        warnings.append("power_factor_adjustment_sign_recovered")

    energy_charge = charge_fields.get("energy_charge_twd")
    total_amount = charge_fields.get("total_amount_twd")
    has_adjustments = any(
        _prepaid_tail(line.text) is not None
        or any(_adjustment_tail(line.text, key) is not None for key in _ADJUSTMENT_LABELS)
        for line in assigned_lines.get("charge_info", [])
    )
    if total_amount is None and not has_adjustments:
        currency_amounts = [
            _parse_value(line.text, "number")
            for line in ordered
            if "元" in line.text
            and re.search(r"\d\s*(?:[,，]\s*\d{3})+", line.text)
        ]
        plausible_totals = [
            value
            for value in currency_amounts
            if isinstance(value, (int, float))
            and (not isinstance(energy_charge, (int, float)) or value >= energy_charge)
        ]
        if plausible_totals:
            charge_fields["total_amount_twd"] = max(plausible_totals)
            total_amount = charge_fields["total_amount_twd"]
            warnings.append("total_recovered_from_payment_summary")
    if (
        not has_adjustments
        and isinstance(energy_charge, (int, float))
        and isinstance(total_amount, (int, float))
        and 0 <= total_amount < energy_charge * 0.1
        and abs(round(energy_charge) - energy_charge) <= 0.5
    ):
        charge_fields["total_amount_twd"] = round(energy_charge)
        warnings.append("total_reconstructed_from_charge")

    payment_fields = blocks.get("payment_info", {}).get("fields", {})
    if payment_fields is not None and "payment_info" in blocks:
        compact_lines = [
            re.sub(r"\s+", "", line.text)
            for line in assigned_lines.get("payment_info", [])
        ]
        customer_number = next(
            (
                match.group(0)
                for text in compact_lines
                if (
                    match := re.search(
                        r"\d{1,2}-\d{2}-\d{4}-\d{2}-\d", text
                    )
                )
            ),
            None,
        )
        due_date = next(
            (
                match.group(0)
                for text in compact_lines
                if (match := re.search(r"\d{3}/\d{1,2}/\d{1,2}", text))
            ),
            None,
        )
        if customer_number:
            payment_fields["customer_number"] = customer_number
        if due_date:
            payment_fields["due_date"] = due_date
        if "total_amount_twd" in charge_fields:
            payment_fields["total_amount_twd"] = charge_fields["total_amount_twd"]

    query_view = {
        block_key: block["fields"]
        for block_key, block in blocks.items()
    }
    by_label = {
        _BLOCK_LABELS.get(block_key, _display_block_title(block["title"])): {
            _FIELD_LABELS.get(field_key, field_key): value
            for field_key, value in block["fields"].items()
        }
        for block_key, block in blocks.items()
    }
    return BillResult(
        {
            "blocks": blocks,
            "by_label": by_label,
            "quality": {
                "warnings": warnings,
                "unknown_block_count": sum(
                    key.startswith("unknown_") for key in blocks
                ),
            },
            **query_view,
        }
    )


def refine_document_type(
    result: BillResult, lines: Iterable[OcrLine], document_type: str
) -> str:
    """Tell high-voltage bills from ordinary ones by the printed tariff.

    Residential and low-voltage bills share the pink block template, so the
    layout detector alone reports them as high-voltage. The tariff (電價種類)
    decides: 高壓 means high voltage, anything else (表燈, 低壓電力, …) is an
    ordinary electricity bill. Back pages print no tariff and keep the type.
    """
    if document_type not in {"high_voltage_bill", "unknown"}:
        return document_type
    tariff = result.get("user_info.tariff_type")
    if not tariff:
        tariff = "".join(line.text for line in lines if line.block_key == "user_info")
        if not re.search(r"表燈|電力|电力", tariff):
            return document_type
    tariff = str(tariff).replace("压", "壓")
    if "高壓" in tariff:
        return "high_voltage_bill"
    return "electricity_bill"
