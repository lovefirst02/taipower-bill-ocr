"""Workbooks written from reviewed bills: the 17-column report and the master.

Both are rebuilt from the database on every submit, so they always match
what reviewers confirmed; a workbook held open in Excel is retried later.
"""

from __future__ import annotations

import copy
import io
from pathlib import Path
from typing import Any, Iterable, Mapping

from taipower_bill_ocr.dataset import PROGRAM_VERSION
from taipower_bill_ocr.excel_export import COLUMNS, REVIEW_COLUMN
from taipower_bill_ocr.master_workbook import add_document, empty_sheets, save_master, save_tables

from .store import Store

UPLOADER, SUBMITTED_AT, STATUS, EVIDENCE = "上傳者", "送出時間", "狀態", "證據檔"
REPORT_COLUMNS = [*COLUMNS, UPLOADER, SUBMITTED_AT, STATUS, EVIDENCE]
REVIEWED = "已人工確認"
CORRECTED = "reviewer_corrected"

# Where each report column lives in the full field list.
_KWH = {"尖峰度數": "peak_kwh", "半尖峰度數": "semi_peak_kwh", "週六半尖峰度數": "saturday_semi_peak_kwh", "離峰度數": "off_peak_kwh"}
COLUMN_FIELDS: dict[str, tuple[str, str]] = {
    "Custom No. 電號": ("user_info", "customer_number"),
    **{f"計費度數_{label}": ("user_info", f"energy_consumption.{key}") for label, key in _KWH.items()},
    **{f"轉供度數_{label}": ("user_info", f"transferred_energy.{key}") for label, key in _KWH.items()},
    "基本電費": ("charge_info", "basic_charge_twd"),
    "流動電費": ("charge_info", "energy_charge_twd"),
    "功率因數調整費": ("charge_info", "power_factor_adjustment_twd"),
    "預繳費用": ("charge_info", "prepaid_applied_twd"),
    "稅前應繳總金額": ("charge_info", "pretax_total_twd"),
    "應繳總金額": ("charge_info", "total_amount_twd"),
}


def evidence_path(output_dir: Path, page: Mapping[str, Any]) -> Path:
    stem = Path(page["filename"]).stem
    suffix = f"_p{page['page_number']}" if page["page_number"] else ""
    return output_dir / "evidence" / page["user"] / f"{page['upload_id']}_{stem}{suffix}.pdf"


def report_row(page: Mapping[str, Any], output_dir: Path | None = None) -> list[Any]:
    row = dict(page["row"] or {})
    submitted = page["status"] == "submitted"
    if submitted:
        original = row.get(REVIEW_COLUMN)
        row[REVIEW_COLUMN] = f"{REVIEWED}；{original}" if original else REVIEWED
    evidence = None
    if submitted and output_dir is not None:
        evidence = str(evidence_path(output_dir, page).relative_to(output_dir))
    extras = {
        UPLOADER: page["user"],
        SUBMITTED_AT: page.get("submitted_at"),
        STATUS: "submitted" if submitted else "draft",
        EVIDENCE: evidence,
    }
    return [extras[column] if column in extras else row.get(column) for column in REPORT_COLUMNS]


def export_workbook(pages: Iterable[Mapping[str, Any]]) -> bytes:
    """A 17-column workbook of the given bill pages, for download."""
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "電費帳單"
    sheet.append(REPORT_COLUMNS)
    for page in pages:
        sheet.append(report_row(page))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _split_period(text: Any) -> tuple[str | None, str | None]:
    if not isinstance(text, str) or "~" not in text:
        return None, None
    start, end = text.split("~", 1)
    return start.strip() or None, end.strip() or None


def apply_corrections(dataset: Mapping[str, Any], ocr_row: Mapping[str, Any], row: Mapping[str, Any], reviewer: str) -> dict[str, Any]:
    """The page's full field list with the reviewer's 17-column edits applied.

    Corrected fields keep their OCR reading in ``cross_check_value`` and are
    marked ``reviewer_corrected`` so the master shows what a person changed.
    """
    result = copy.deepcopy(dict(dataset))
    fields = result["fields"]
    targets: list[tuple[str, str, Any, Any]] = []
    new_start, new_end = _split_period(row.get("Billing Period 計費期間"))
    old_start, old_end = _split_period(ocr_row.get("Billing Period 計費期間"))
    if (new_start, new_end) != (old_start, old_end):
        targets += [("user_info", "billing_period.start", new_start, old_start), ("user_info", "billing_period.end", new_end, old_end)]
    for column, (block, key) in COLUMN_FIELDS.items():
        if row.get(column) != ocr_row.get(column):
            targets.append((block, key, row.get(column), ocr_row.get(column)))

    for block, key, value, ocr_value in targets:
        matches = [item for item in fields if item["key"] == key and (item["block"] == block or key == "customer_number")]
        if matches:
            for item in matches:
                item.update(value=value, cross_check_value=item["value"], status=CORRECTED,
                            confidence="high", source=f"reviewer:{reviewer}", checks=None)
        elif value is not None:
            fields.append({
                "block": block, "block_label": block, "key": key, "label": key, "printed_label": None,
                "value": value, "recognized": True, "status": CORRECTED, "confidence": "high",
                "checks": None, "cross_check_value": ocr_value, "source": f"reviewer:{reviewer}",
                "origin": "reviewer", "bbox": None,
            })
    summary = result["summary"]
    summary["customer_number"] = row.get("Custom No. 電號") or summary.get("customer_number")
    if new_start or new_end:
        summary["billing_period_start"], summary["billing_period_end"] = new_start, new_end
    summary["total_amount_twd"] = row.get("應繳總金額", summary.get("total_amount_twd"))
    summary["needs_review"] = False
    summary["confidence_review"] = 0
    return result


def write_workbooks(store: Store, output_dir: Path) -> list[str]:
    """Rebuild both shared workbooks from submitted bills.

    Returns the names of workbooks that could not be replaced because they
    are open in another program (they are rewritten on the next submit).
    """
    submitted = list(store.pages(bills_only=True, submitted_only=True))
    locked: list[str] = []

    report = output_dir / "taipower_bills_17.xlsx"
    if not save_tables(report, {"Bills": (REPORT_COLUMNS, [report_row(page, output_dir) for page in submitted])}):
        locked.append(report.name)

    sheets = empty_sheets()
    by_upload: dict[int, list[Mapping[str, Any]]] = {}
    for page in submitted:
        by_upload.setdefault(page["upload_id"], []).append(page)
    for upload_id, bill_pages in sorted(by_upload.items()):
        upload = store.upload(upload_id)
        corrected = {
            page["id"]: apply_corrections(page["dataset"], page["ocr_row"], page["row"], page["submitted_by"] or page["user"])
            for page in bill_pages
        }
        # Back pages (meter readings, energy saving) of the same upload belong
        # to the bill too, so they are included alongside the reviewed page.
        pages = [
            corrected.get(page["id"], page["dataset"])
            for page in store.pages()
            if page["upload_id"] == upload_id
        ]
        add_document(sheets, {
            "file": upload["filename"],
            "processed_at": upload["processed_at"],
            "program_version": PROGRAM_VERSION,
            "pages": pages,
        })
        sheets["Files"].append([
            upload["filename"], "submitted", len(pages), 0,
            upload["processed_at"], 1, None,
        ])
    master = output_dir / "taipower_bills.xlsx"
    if not save_master(master, sheets):
        locked.append(master.name)
    return locked
