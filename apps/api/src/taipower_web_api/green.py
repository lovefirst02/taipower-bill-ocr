"""Green power statements from electricity retailers (kind "green").

The table is read with ``taipower_bill_ocr.green_statement``; each line is
checked against its rates, and each meter's transferred kWh against the
transferred kWh on that meter's Taipower bills (the two should agree).
The statement's charges are what is reported; the worked-out charge only
checks them.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Mapping

from taipower_bill_ocr.excel_export import COLUMNS
from taipower_bill_ocr.green_statement import check_line, is_green, read_statement, statement_period
from taipower_bill_ocr.keyword_extract import lines_from_dataset
from taipower_bill_ocr.periods import parse_period, split_by_month

from .evidence import _highlight_and_label
from .settings import Settings
from .store import Store, meter_number

GREEN = "green"
EDITABLE_FIELDS = ("meter", "energy", "base", "transfer_rate", "certificate_rate", "unit_price", "kwh", "cost")
TRANSFERRED_KWH = [f"轉供度數_{label}" for label in ("尖峰度數", "半尖峰度數", "週六半尖峰度數", "離峰度數")]
# Evidence labels over the statement's kWh and charge columns.
COLUMN_LABELS = {"kwh": "Green Electricity (kWh)", "cost": "Green Electricity Cost"}


def detect(store: Store, first: dict[str, Any], settings: Settings) -> dict[str, Any] | None:
    """Page columns for a green power statement, or None when the upload is not one."""
    pages = store.item_pages(first)
    for page in pages:
        lines = lines_from_dataset(page["dataset"], page["page_number"])
        statement = read_statement(lines)
        if statement is None:
            continue
        all_lines = [line for other in pages for line in lines_from_dataset(other["dataset"], other["page_number"])]
        period = statement_period(all_lines)
        green = {"period": period, "lines": [line.as_json() for line in statement.lines]}
        row: dict[str, Any] = {column: None for column in COLUMNS}
        row.update({"Billing Period 計費期間": period, "來源檔案": first["filename"], "頁碼": first["page_number"]})
        return {
            "kind": GREEN,
            "is_bill": True,
            "row": row,
            "ocr_row": dict(row),
            "green": green,
            "issues": issues_of(green, settings),
            "marks": _marks(statement, page, first),
            "mark_notes": [],
            "sources": {},
            "alternatives": {},
            "locations": {},
        }
    return None


def _marks(statement: Any, page: dict[str, Any], first: dict[str, Any]) -> list[dict[str, Any]]:
    """A highlight down the kWh and the charge columns, each with its label."""
    taken = [(line["x"], line["y"], line["width"], line["height"]) for line in page["dataset"].get("ocr_lines") or []]
    marks: list[dict[str, Any]] = []
    for column, label in COLUMN_LABELS.items():
        boxes = [line.cells[column] for line in statement.lines if column in line.cells]
        if not boxes:
            continue
        left = min(box[0] for box in boxes)
        top = min(box[1] for box in boxes)
        right = max(box[0] + box[2] for box in boxes)
        bottom = max(box[1] + box[3] for box in boxes)
        pad = 6
        added = _highlight_and_label(
            label, (left - pad, top - pad, right - left + pad * 2, bottom - top + pad * 2),
            (page["image_width"], page["image_height"]), taken, [],
        )
        for mark in added:
            mark["field"] = f"green.{column}"
            if page["id"] != first["id"]:
                mark["page"] = page["page_number"]
        marks += added
    return marks


def checked_lines(green: Mapping[str, Any], tolerance: float) -> list[dict[str, Any]]:
    return [{**line, "check": check_line(line, tolerance), "green": is_green(line)} for line in green.get("lines") or []]


def issues_of(green: Mapping[str, Any], settings: Settings) -> list[dict[str, Any]]:
    issues = []
    if not green.get("period"):
        issues.append({"zh": "找不到明細的期間，請填寫", "en": "Statement period not found — enter it", "columns": ["Billing Period 計費期間"]})
    for index, line in enumerate(checked_lines(green, settings.green_tolerance), start=1):
        check = line["check"]
        if check["status"] == "check":
            issues.append({
                "zh": f"第 {index} 列（電號 {line.get('meter')}）本月電費與度數×費率相差 {check['difference']} 元",
                "en": f"Line {index} (meter {line.get('meter')}): charge differs from kWh × rates by {check['difference']} NT$",
                "columns": [],
            })
        elif check["status"] == "incomplete":
            issues.append({
                "zh": f"第 {index} 列（電號 {line.get('meter')}）缺少度數、電費或費率",
                "en": f"Line {index} (meter {line.get('meter')}): kWh, charge or rates missing",
                "columns": [],
            })
    return issues


def clean_lines(lines: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Reviewed lines: only known fields, numbers as numbers, the 電號 as digits."""
    cleaned = []
    for line in lines:
        item: dict[str, Any] = {}
        for name in EDITABLE_FIELDS:
            value = line.get(name)
            if name == "meter":
                item[name] = meter_number(value)
            elif name == "energy":
                item[name] = (str(value).strip() or None) if value is not None else None
            elif value is None or value == "":
                item[name] = None
            else:
                try:
                    number = Decimal(str(value).replace(",", ""))
                except ArithmeticError:
                    raise ValueError(f"not a number: {value}") from None
                item[name] = int(number) if number == number.to_integral_value() and name in ("kwh", "cost") else float(number)
        for kept in ("page", "box", "cells"):
            if line.get(kept) is not None:
                item[kept] = line[kept]
        cleaned.append(item)
    return cleaned


# ----------------------------------------------------- Taipower cross-check
def transferred_kwh(row: Mapping[str, Any]) -> Any:
    values = [row.get(column) for column in TRANSFERRED_KWH if isinstance(row.get(column), (int, float))]
    return sum(values) if values else None


def _days(period: tuple[date, date]) -> int:
    return (period[1] - period[0]).days + 1


def crosscheck(store: Store, page: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Each meter's transferred kWh on the statement vs on its Taipower bills.

    Same period: compared as is. Overlapping periods: the bills' kWh are
    prorated by days to the statement's period (an estimate, said so).
    """
    green = page.get("green") or {}
    period = parse_period(green.get("period"))
    per_meter: dict[str, Decimal] = {}
    for line in green.get("lines") or []:
        if line.get("meter") and is_green(line) and line.get("kwh") is not None:
            per_meter[line["meter"]] = per_meter.get(line["meter"], Decimal(0)) + Decimal(str(line["kwh"]))
    bills = [
        bill for bill in store.pages(bills_only=True)
        if (bill.get("kind") or "taipower") == "taipower" and not bill.get("hidden")
    ]
    result = []
    for meter, statement_kwh in per_meter.items():
        site = store.site_of({"row": {"Custom No. 電號": meter}}) or {}
        entry: dict[str, Any] = {
            "meter": meter, "site": site.get("name"), "statement_kwh": _plain(statement_kwh),
            "taipower_kwh": None, "difference": None, "how": "none", "bills": [],
        }
        matches = [bill for bill in bills if meter_number((bill.get("row") or {}).get("Custom No. 電號")) == meter]
        if period is None:
            entry["how"] = "no period"
        else:
            total = Decimal(0)
            exact = True
            for bill in matches:
                row = bill.get("row") or {}
                bill_period = parse_period(row.get("Billing Period 計費期間"))
                kwh = transferred_kwh(row)
                if bill_period is None or kwh is None:
                    continue
                start, end = max(bill_period[0], period[0]), min(bill_period[1], period[1])
                if start > end:
                    continue
                overlap = (end - start).days + 1
                share = Decimal(str(kwh)) * overlap / _days(bill_period)
                exact = exact and bill_period == period
                total += share
                entry["bills"].append({"id": bill["id"], "filename": bill["filename"], "period": row.get("Billing Period 計費期間"), "kwh": kwh})
            if entry["bills"]:
                taipower = total.quantize(Decimal(1)) if not exact else total
                entry.update(taipower_kwh=_plain(taipower), difference=_plain(statement_kwh - taipower), how="same period" if exact else "prorated")
        result.append(entry)
    return result


def _plain(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)


# ----------------------------------------------------------------- reports
GREEN_COLUMNS = [
    "據點", "據點代碼", "電號", "月份", "天數", "能源別", "基準價", "轉供費率", "憑證費率", "轉供度數", "本月電費",
    "計算金額", "差額", "核對", "Billing Period 計費期間", "來源檔案", "狀態", "上傳者", "送出者", "證據檔",
]
CHECK_COLUMNS = ["據點", "電號", "Billing Period 計費期間", "售電業者轉供度數", "台電帳單轉供度數", "差異", "比對方式", "台電帳單", "來源檔案"]
HOW = {"same period": "期間相同", "prorated": "期間不同，依天數換算", "none": "找不到同電號的台電帳單", "no period": "明細沒有期間"}


def green_rows(pages: Iterable[Mapping[str, Any]], store: Store, settings_tolerance: float, common_of) -> list[list[Any]]:
    """Each statement line per calendar month (split by days), with its check."""
    rows = []
    for page in pages:
        green = page.get("green") or {}
        common = common_of(page)
        for line in checked_lines(green, settings_tolerance):
            site = store.site_of({"row": {"Custom No. 電號": line.get("meter")}}) or {}
            check = line["check"]
            amounts = {"轉供度數": line.get("kwh"), "本月電費": line.get("cost"), "計算金額": check.get("expected")}
            shares = [{"月份": share.month, "天數": share.days, **share.amounts} for share in split_by_month(green.get("period"), amounts)]
            if not shares:
                shares = [{"月份": None, "天數": None, **amounts}]
            for share in shares:
                values = {
                    **common, "據點": site.get("name"), "據點代碼": site.get("code"), "電號": line.get("meter"),
                    "能源別": line.get("energy"), "基準價": line.get("base"), "轉供費率": line.get("transfer_rate"),
                    "憑證費率": line.get("certificate_rate"), "差額": check.get("difference"),
                    "核對": {"ok": "符合", "check": "需確認", "incomplete": "資料不全"}[check["status"]],
                    "Billing Period 計費期間": green.get("period"), **share,
                }
                rows.append([values.get(column) for column in GREEN_COLUMNS])
    return rows


def check_rows(pages: Iterable[Mapping[str, Any]], store: Store) -> list[list[Any]]:
    """Each statement's meters: transferred kWh vs the Taipower bills."""
    rows = []
    for page in pages:
        for entry in crosscheck(store, page):
            rows.append([
                entry["site"], entry["meter"], (page.get("green") or {}).get("period"), entry["statement_kwh"],
                entry["taipower_kwh"], entry["difference"], HOW.get(entry["how"], entry["how"]),
                "、".join(bill["filename"] for bill in entry["bills"]) or None, page.get("filename"),
            ])
    return rows
