"""Flatten parsed bills into spreadsheet rows and write them to .xlsx."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Iterable, Mapping

from .parser import BillResult

_TOU_COMPONENTS = (
    ("尖峰度數", "peak_kwh"),
    ("半尖峰度數", "semi_peak_kwh"),
    ("週六半尖峰度數", "saturday_semi_peak_kwh"),
    ("離峰度數", "off_peak_kwh"),
)
_ENERGY_GROUPS = (
    ("計費度數", "energy_consumption"),
    ("轉供度數", "transferred_energy"),
)

COLUMNS = (
    "Billing Period 計費期間",
    "Custom No. 電號",
    *(
        f"{group_label}_{component_label}"
        for group_label, _ in _ENERGY_GROUPS
        for component_label, _ in _TOU_COMPONENTS
    ),
    "基本電費",
    "流動電費",
    "功率因數調整費",
    "預繳費用",
    "其他調整費用",
    "稅前應繳總金額",
    "應繳總金額",
    "來源檔案",
    "頁碼",
    "需人工檢查",
)

_TEXT_COLUMNS = {"Billing Period 計費期間", "Custom No. 電號"}
REVIEW_COLUMN = "需人工檢查"

# English labels for user interfaces; the spreadsheet keeps the columns above.
COLUMN_LABELS_EN = {
    "Billing Period 計費期間": "Billing Period",
    "Custom No. 電號": "Customer No.",
    **{
        f"{group_label}_{component_label}": f"{group_en} – {component_en}"
        for (group_label, _), group_en in zip(
            _ENERGY_GROUPS, ("Billed kWh", "Transferred kWh")
        )
        for (component_label, _), component_en in zip(
            _TOU_COMPONENTS, ("Peak", "Semi-peak", "Sat. semi-peak", "Off-peak")
        )
    },
    "基本電費": "Basic Charge",
    "流動電費": "Energy Charge",
    "功率因數調整費": "Power Factor Adj.",
    "預繳費用": "Prepaid",
    "其他調整費用": "Other Adjustments",
    "稅前應繳總金額": "Pre-tax Total",
    "應繳總金額": "Total Due",
    "來源檔案": "Source File",
    "頁碼": "Page",
    "需人工檢查": "Review Notes",
}


@dataclass
class Issue:
    """A reason a row needs review, with the columns it concerns."""

    zh: str
    en: str
    columns: list[str] = field(default_factory=list)


@dataclass
class BillRecord:
    row: dict[str, Any]
    issues: list[Issue]
    # Cross-check engine's value for each column where the engines disagree.
    alternatives: dict[str, Any] = field(default_factory=dict)


# Columns read from the bill; the rest are derived or bookkeeping.
DATA_COLUMNS = tuple(
    key for key in COLUMNS if key not in {"其他調整費用", "來源檔案", "頁碼", REVIEW_COLUMN}
)


def _first(bill: BillResult, *paths: str) -> Any:
    for path in paths:
        value = bill.get(path)
        if value is not None:
            return value
    return None


def _format_period(period: Any) -> str | None:
    if isinstance(period, Mapping):
        start, end = period.get("start"), period.get("end")
        if start and end:
            return f"{start}~{end}"
        return start or end
    return period


def _energy_value(bill: BillResult, group_key: str, component_key: str) -> Any:
    # Standard layouts keep the kWh groups under user_info; the detailed
    # high-voltage layout keeps them in its own energy_usage block.
    return _first(
        bill,
        f"user_info.{group_key}.{component_key}",
        f"energy_usage.{group_key}.{component_key}",
    )


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _display(value: Any) -> str:
    if _is_number(value):
        return f"{value:,}"
    return str(value)


def _same_value(a: Any, b: Any) -> bool:
    if _is_number(a) and _is_number(b):
        return abs(a - b) < 0.01
    return re.sub(r"\s+", "", str(a)) == re.sub(r"\s+", "", str(b))


def _period_issue(period: Any) -> Issue | None:
    if not isinstance(period, str) or "~" not in period:
        return None
    start, end = period.split("~", 1)
    # ROC dates are zero-padded (113/01/01), so string order is date order.
    if start >= end:
        return Issue(
            f"計費期間起訖異常（{period}）",
            f"Billing period start is not before end ({period})",
            ["Billing Period 計費期間"],
        )
    return None


def _total_issue(row: Mapping[str, Any], tax: Any) -> Issue | None:
    """Cross-check the payable total against the pre-tax total plus tax."""
    pretax, total = row["稅前應繳總金額"], row["應繳總金額"]
    if not (_is_number(pretax) and _is_number(total)) or row["預繳費用"]:
        return None
    # Accept either the printed tax or the 5% rate, since OCR may misread
    # the tax line while the totals themselves are correct.
    expected = round(pretax * 1.05)
    if abs(total - expected) <= 2:
        return None
    if _is_number(tax) and abs(total - (pretax + tax)) <= 1:
        return None
    return Issue(
        f"應繳總金額與稅前金額＋營業稅不符（預期約 {expected:,}）",
        f"Total due does not match pre-tax total plus tax (expected about {expected:,})",
        ["應繳總金額", "稅前應繳總金額"],
    )


def _passes_bill_checks(
    key: str, value: Any, row: Mapping[str, Any], tax: Any
) -> bool | None:
    """Whether ``value`` is consistent with the rest of the bill; None if untestable."""
    if key == "Billing Period 計費期間":
        return isinstance(value, str) and "~" in value and _period_issue(value) is None
    if key in {"應繳總金額", "稅前應繳總金額"}:
        candidate = {**row, key: value}
        if not (
            _is_number(candidate["稅前應繳總金額"]) and _is_number(candidate["應繳總金額"])
        ) or candidate["預繳費用"]:
            return None
        return _total_issue(candidate, tax) is None
    return None


def is_charge_page(bill: BillResult) -> bool:
    """Return whether a page carries the bill's charge breakdown."""
    return isinstance(bill.get("charge_info"), Mapping)


def _bill_values(bill: BillResult) -> dict[str, Any]:
    """Read every data column from one OCR result."""
    customer_number = _first(
        bill, "user_info.customer_number", "payment_info.customer_number"
    )
    values = {
        "Billing Period 計費期間": _format_period(
            _first(bill, "user_info.billing_period", "payment_info.billing_period")
        ),
        "Custom No. 電號": str(customer_number) if customer_number else None,
        "基本電費": bill.get("charge_info.basic_charge_twd"),
        "流動電費": bill.get("charge_info.energy_charge_twd"),
        "功率因數調整費": bill.get("charge_info.power_factor_adjustment_twd"),
        "預繳費用": bill.get("charge_info.prepaid_applied_twd"),
        "稅前應繳總金額": bill.get("charge_info.pretax_total_twd"),
        "應繳總金額": bill.get("charge_info.total_amount_twd"),
    }
    for group_label, group_key in _ENERGY_GROUPS:
        for component_label, component_key in _TOU_COMPONENTS:
            values[f"{group_label}_{component_label}"] = _energy_value(
                bill, group_key, component_key
            )
    return values


def bill_to_record(
    bill: BillResult,
    source: str | None = None,
    *,
    cross_check: BillResult | None = None,
    cross_check_name: str = "Windows OCR",
) -> BillRecord:
    """Map one parsed bill page onto the spreadsheet columns plus review issues.

    Missing OCR values stay ``None`` rather than being filled with zero; each
    gap or inconsistency becomes an :class:`Issue` naming the affected columns.

    With ``cross_check`` (the same page read by a second OCR engine), ``bill``
    stays authoritative: the second engine only fills values ``bill`` missed,
    and every disagreement is flagged with both values kept.
    """
    issues: list[Issue] = []
    alternatives: dict[str, Any] = {}
    row: dict[str, Any] = dict.fromkeys(COLUMNS)
    row.update(_bill_values(bill))
    tax = bill.get("charge_info.business_tax_twd")

    if cross_check is not None:
        other_values = _bill_values(cross_check)
        if tax is None:
            tax = cross_check.get("charge_info.business_tax_twd")
        conflicts: list[tuple[str, Any, Any]] = []
        for key in DATA_COLUMNS:
            primary, other = row[key], other_values[key]
            label_en = COLUMN_LABELS_EN[key]
            if other is None:
                continue
            if primary is None:
                row[key] = other
                issues.append(
                    Issue(
                        f"{key} 由 {cross_check_name} 補值",
                        f"{label_en} filled in from {cross_check_name}",
                        [key],
                    )
                )
            elif not _same_value(primary, other):
                conflicts.append((key, primary, other))
        for key, primary, other in conflicts:
            # The bill's own arithmetic settles some disagreements: keep the
            # primary value silently when only it is consistent with the bill.
            if (
                _passes_bill_checks(key, primary, row, tax) is True
                and _passes_bill_checks(key, other, row, tax) is False
            ):
                continue
            label_en = COLUMN_LABELS_EN[key]
            alternatives[key] = other
            issues.append(
                Issue(
                    f"OCR 結果不一致：{key} {_display(primary)} / "
                    f"{cross_check_name} {_display(other)}",
                    f"Engines disagree on {label_en}: {_display(primary)} vs "
                    f"{cross_check_name} {_display(other)}",
                    [key],
                )
            )

    if period_issue := _period_issue(row["Billing Period 計費期間"]):
        issues.append(period_issue)

    missing = recompute_other_adjustment(row)
    if missing:
        missing_en = ", ".join(COLUMN_LABELS_EN[label] for label in missing)
        issues.append(
            Issue(
                f"其他調整費用無法計算：缺少{'、'.join(missing)}",
                f"Other adjustments not computed: missing {missing_en}",
                [*missing, "其他調整費用"],
            )
        )
    elif not _is_number(row["功率因數調整費"]):
        issues.append(
            Issue(
                "未辨識功率因數調整費，其他調整費用以 0 計算",
                "Power factor adjustment not found; counted as 0 in other adjustments",
                ["功率因數調整費"],
            )
        )

    if total_issue := _total_issue(row, tax):
        issues.append(total_issue)

    for label in ("Billing Period 計費期間", "Custom No. 電號", "應繳總金額"):
        if row[label] is None:
            issues.append(Issue(f"缺少{label}", f"Missing {COLUMN_LABELS_EN[label]}", [label]))

    # Parser warnings are already English codes describing inferred values.
    issues.extend(
        Issue(warning, warning) for warning in bill.get("quality.warnings") or []
    )
    row["來源檔案"] = source
    row["頁碼"] = bill.get("quality.page_number")
    row[REVIEW_COLUMN] = "；".join(issue.zh for issue in issues) or None
    return BillRecord(row, issues, alternatives)


# The charge lines are printed tax-inclusive and add up to 應繳總金額 (稅前
# 應繳總金額 is that total before tax), so the remainder after the itemised
# columns is everything else listed under 計費內容: 優惠金額, 電子帳單優惠,
# 電器租費, 配合減少用電減收, and any line the parser does not know yet.
OTHER_ADJUSTMENT_REQUIRED = ("應繳總金額", "基本電費", "流動電費")
OTHER_ADJUSTMENT_INPUTS = (*OTHER_ADJUSTMENT_REQUIRED, "功率因數調整費", "預繳費用")


def recompute_other_adjustment(row: dict[str, Any]) -> list[str]:
    """Set 其他調整費用 from the row; returns the required columns that are missing.

    其他調整費用 = 應繳總金額 − 基本電費 − 流動電費 − 功率因數調整費 − 預繳費用.
    A missing power factor adjustment or prepayment counts as 0, since many
    bills do not print those lines.
    """
    missing = [key for key in OTHER_ADJUSTMENT_REQUIRED if not _is_number(row.get(key))]
    if missing:
        row["其他調整費用"] = None
        return missing
    total, basic, energy, power_factor, prepaid = (
        row.get(key) if _is_number(row.get(key)) else 0 for key in OTHER_ADJUSTMENT_INPUTS
    )
    row["其他調整費用"] = round(total - basic - energy - power_factor - prepaid, 2)
    return []


def bill_to_row(bill: BillResult, source: str | None = None) -> dict[str, Any]:
    """Map one parsed bill page onto the spreadsheet columns."""
    return bill_to_record(bill, source).row


def bills_to_rows(
    bills: Iterable[BillResult], source: str | None = None
) -> list[dict[str, Any]]:
    """Convert pages to rows, skipping pages without a charge breakdown."""
    return [bill_to_row(bill, source) for bill in bills if is_charge_page(bill)]


def write_rows_to_excel(
    rows: Iterable[Mapping[str, Any]], path: str | Path | IO[bytes]
) -> Path | IO[bytes]:
    """Write spreadsheet rows to a path or binary stream in the fixed column order."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise ImportError(
            'Excel export requires openpyxl: pip install -e ".[excel]"'
        ) from exc

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "電費帳單"
    sheet.append(list(COLUMNS))
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.freeze_panes = "A2"

    text_column_indexes = [
        index for index, name in enumerate(COLUMNS, start=1) if name in _TEXT_COLUMNS
    ]
    for row in rows:
        sheet.append([row.get(name) for name in COLUMNS])
        # Keep identifiers as text so Excel does not drop the leading zero.
        for index in text_column_indexes:
            sheet.cell(row=sheet.max_row, column=index).number_format = "@"

    for index, name in enumerate(COLUMNS, start=1):
        width = max(
            len(str(cell.value or "")) for cell in next(
                sheet.iter_cols(min_col=index, max_col=index)
            )
        )
        sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = (
            min(max(width, len(name)) + 4, 60)
        )

    output = Path(path) if isinstance(path, (str, Path)) else path
    workbook.save(output)
    return output
