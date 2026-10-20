"""Workbooks written from reviewed bills: the 17-column report and the master.

Both are rebuilt from the database on every submit, so they always match
what reviewers confirmed. Writes are serialised, and a workbook held open in
Excel is retried in the background until it can be replaced.
"""

from __future__ import annotations

import copy
import io
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from taipower_bill_ocr.dataset import PROGRAM_VERSION
from taipower_bill_ocr.excel_export import COLUMNS, REVIEW_COLUMN
from taipower_bill_ocr.master_workbook import add_document, empty_sheets, save_master, save_tables

from taipower_bill_ocr.periods import split_by_month

from . import documents, green
from .store import Store, meter_number

UPLOADER, SUBMITTER, SUBMITTED_AT, STATUS, EVIDENCE = "上傳者", "送出者", "送出時間", "狀態", "證據檔"
DOCUMENT_TYPE, VALUE_SOURCES = "文件類型", "欄位來源"
REPORT_COLUMNS = [*COLUMNS, documents.USAGE, DOCUMENT_TYPE, VALUE_SOURCES, UPLOADER, SUBMITTER, SUBMITTED_AT, STATUS, EVIDENCE]

log = logging.getLogger("taipower_web_api.reports")
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


REOPENED_DIR = "_reopened"


def user_folder(user: str) -> str:
    """A user name made safe to use as a folder name."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", user).strip(" .") or "user"


def evidence_folder(output_dir: Path, user: str) -> Path:
    return output_dir / "evidence" / user_folder(user)


def evidence_path(output_dir: Path, page: Mapping[str, Any]) -> Path:
    """Where a submitted bill's evidence PDF is (named when it was submitted)."""
    if page.get("evidence_file"):
        return output_dir / page["evidence_file"]
    # Submitted before names were recorded: "<upload id>_<file>_p<page>.pdf".
    stem = Path(page["filename"]).stem
    suffix = f"_p{page['page_number']}" if page["page_number"] else ""
    return evidence_folder(output_dir, page["user"]) / f"{page['upload_id']}_{stem}{suffix}.pdf"


def new_evidence_path(output_dir: Path, page: Mapping[str, Any]) -> Path:
    """A free path named after the uploaded file: "bill.pdf", else "bill (2).pdf"."""
    folder = evidence_folder(output_dir, page["user"])
    stem = Path(download_name(page)).stem
    target, copy = folder / f"{stem}.pdf", 1
    while target.exists():
        copy += 1
        target = folder / f"{stem} ({copy}).pdf"
    return target


def download_name(page: Mapping[str, Any]) -> str:
    """The name a downloaded evidence PDF gets: the uploaded file's own name."""
    return f"{Path(page['filename']).stem}.pdf"


def archive_evidence(output_dir: Path, page: Mapping[str, Any]) -> Path | None:
    """Move a reopened bill's submitted PDF to evidence/_reopened/<user>/.

    The PDF no longer matches anything in the workbooks, so it should not sit
    beside the submitted ones; it is kept (timestamped) rather than deleted.
    Returns the archived path, or None when there was nothing to move.
    """
    saved = evidence_path(output_dir, page)
    if not saved.is_file():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = output_dir / "evidence" / REOPENED_DIR / saved.parent.name / f"{saved.stem}_{stamp}{saved.suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(saved, target)
    return target


def workbook_dir(output_dir: Path, user: str | None) -> Path:
    """Where a set of workbooks goes: the output folder, or a user's folder in it."""
    return output_dir if user is None else output_dir / user_folder(user)


def report_row(page: Mapping[str, Any], output_dir: Path | None = None) -> list[Any]:
    row = dict(page["row"] or {})
    submitted = page["status"] == "submitted"
    if page.get("kind") == documents.OTHER:
        # Notes about a value found by keyword no longer apply once it was
        # picked or typed in by the reviewer.
        sources = page.get("sources") or {}
        notes = [
            issue["zh"] for issue in page.get("issues") or []
            if all(sources.get(column) in (None, "keyword") for column in issue.get("columns") or [])
        ]
        row[REVIEW_COLUMN] = "；".join(notes) or None
    if submitted:
        original = row.get(REVIEW_COLUMN)
        row[REVIEW_COLUMN] = f"{REVIEWED}；{original}" if original else REVIEWED
    evidence = None
    if submitted and output_dir is not None:
        saved = evidence_path(output_dir, page)
        # Relative to the output folder; bills submitted before the folder
        # was changed keep their full path.
        evidence = str(saved.relative_to(output_dir)) if saved.is_relative_to(output_dir) else str(saved)
    kind = page.get("kind") or documents.TAIPOWER
    extras = {
        DOCUMENT_TYPE: documents.DOCUMENT_TYPES.get(kind, kind),
        VALUE_SOURCES: documents.source_text(kind, page.get("sources")),
        UPLOADER: page["user"],
        SUBMITTER: page.get("submitted_by"),
        SUBMITTED_AT: page.get("submitted_at"),
        STATUS: "submitted" if submitted else "draft",
        EVIDENCE: evidence,
    }
    return [extras[column] if column in extras else row.get(column) for column in REPORT_COLUMNS]


MONTHLY_COLUMNS = [
    "據點", "據點代碼", "電號", "月份", "天數", documents.USAGE, "轉供度數", "稅前應繳總金額", "應繳總金額",
    DOCUMENT_TYPE, "Billing Period 計費期間", "來源檔案", "頁碼", STATUS, UPLOADER, SUBMITTER, EVIDENCE, "備註",
]
_BILLED_KWH = [f"計費度數_{label}" for label in ("尖峰度數", "半尖峰度數", "週六半尖峰度數", "離峰度數")]
_TRANSFERRED_KWH = [f"轉供度數_{label}" for label in ("尖峰度數", "半尖峰度數", "週六半尖峰度數", "離峰度數")]


def _total(row: Mapping[str, Any], columns: list[str]) -> Any:
    values = [row.get(column) for column in columns if isinstance(row.get(column), (int, float))]
    return sum(values) if values else None


def usage_kwh(row: Mapping[str, Any]) -> Any:
    """The period's usage: 用電度數, else the billed kWh of all time-of-use periods."""
    return row.get(documents.USAGE) if row.get(documents.USAGE) is not None else _total(row, _BILLED_KWH)


def month_shares(page: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The bill's usage and cost per calendar month, split by days (adds up exactly)."""
    row = page.get("row") or {}
    amounts = {
        documents.USAGE: usage_kwh(row),
        "轉供度數": _total(row, _TRANSFERRED_KWH),
        "稅前應繳總金額": row.get("稅前應繳總金額"),
        "應繳總金額": row.get("應繳總金額"),
    }
    return [
        {"月份": share.month, "天數": share.days, **share.amounts}
        for share in split_by_month(row.get("Billing Period 計費期間"), amounts)
    ]


def monthly_rows(pages: Iterable[Mapping[str, Any]], store: Store, output_dir: Path | None = None) -> list[list[Any]]:
    """One row per site, 電號 and month: what the budget and Intelex use."""
    rows = []
    for page in pages:
        row = page.get("row") or {}
        site = store.site_of(page) or {}
        base = dict(zip(REPORT_COLUMNS, report_row(page, output_dir)))
        common = {
            "據點": site.get("name"), "據點代碼": site.get("code"), "電號": meter_number(row.get("Custom No. 電號")),
            DOCUMENT_TYPE: base[DOCUMENT_TYPE], "Billing Period 計費期間": row.get("Billing Period 計費期間"),
            "來源檔案": row.get("來源檔案") or page.get("filename"), "頁碼": page.get("page_number"),
            STATUS: base[STATUS], UPLOADER: base[UPLOADER], SUBMITTER: base[SUBMITTER], EVIDENCE: base[EVIDENCE],
        }
        shares = month_shares(page)
        if not shares:
            # The period could not be read: the whole bill, not split.
            shares = [{
                "月份": None, "天數": None, documents.USAGE: usage_kwh(row), "轉供度數": _total(row, _TRANSFERRED_KWH),
                "稅前應繳總金額": row.get("稅前應繳總金額"), "應繳總金額": row.get("應繳總金額"),
                "備註": "計費期間無法解析，未拆分",
            }]
        for share in shares:
            values = {**common, **share}
            rows.append([values.get(column) for column in MONTHLY_COLUMNS])
    return rows


def _is_green(page: Mapping[str, Any]) -> bool:
    return page.get("kind") == green.GREEN


def green_common(output_dir: Path | None):
    """The columns a green power line shares with its statement."""
    def common(page: Mapping[str, Any]) -> dict[str, Any]:
        base = dict(zip(REPORT_COLUMNS, report_row(page, output_dir)))
        return {key: base[key] for key in ("來源檔案", STATUS, UPLOADER, SUBMITTER, EVIDENCE)} | {"來源檔案": page.get("filename")}
    return common


def report_tables(pages: list[Mapping[str, Any]], store: Store, output_dir: Path | None, tolerance: float) -> dict[str, tuple[list[str], list[list[Any]]]]:
    """The report workbook's sheets: bills, their months, and green power statements."""
    bills = [page for page in pages if not _is_green(page)]
    statements = [page for page in pages if _is_green(page)]
    # Compared across every green power document, submitted or not.
    checks = green.check_rows(store)
    return {
        "Bills": (REPORT_COLUMNS, [report_row(page, output_dir) for page in bills]),
        # Each bill per site and calendar month, split by days.
        "Monthly": (MONTHLY_COLUMNS, monthly_rows(bills, store, output_dir)),
        # Green power: retailers' statements and notices per month with their
        # checks, Taipower's transfer fee lists, and how they all agree.
        "GreenPower": (green.GREEN_COLUMNS, green.green_rows(statements, store, tolerance, green_common(output_dir))),
        "TransferList": (green.LIST_COLUMNS, green.list_rows(statements, store)),
        "GreenCheck": (green.CHECK_COLUMNS, checks[0]),
        "GreenServices": (green.SERVICE_COLUMNS, checks[1]),
    }


def export_workbook(pages: Iterable[Mapping[str, Any]], store: Store | None = None, tolerance: float = 3) -> bytes:
    """A 17-column workbook of the given bill pages, for download (and their months)."""
    from openpyxl import Workbook

    pages = list(pages)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "電費帳單"
    sheet.append(REPORT_COLUMNS)
    for page in pages:
        if not _is_green(page):
            sheet.append(report_row(page))
    if store is not None:
        tables = report_tables(pages, store, None, tolerance)
        for name in ("Monthly", "GreenPower", "TransferList", "GreenCheck", "GreenServices"):
            columns, rows = tables[name]
            extra = workbook.create_sheet(name)
            extra.append(columns)
            for line in rows:
                extra.append(line)
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


# How each value of another kind of document is recorded in the master's Fields.
_DOCUMENT_FIELDS = {
    documents.PERIOD: ("billing_period", "計費期間"),
    documents.USAGE: ("energy_kwh", "用電度數"),
    documents.PRETAX: ("pretax_total_twd", "未稅金額"),
    documents.TOTAL: ("total_amount_twd", "含稅金額"),
}
_SOURCE_STATUS = {"keyword": "found_by_keyword", "pick": "picked_by_reviewer", "manual": "entered_by_reviewer"}


def green_record(page: Mapping[str, Any], reviewer: str) -> dict[str, Any]:
    """The master's record of a green power statement: one field per line value."""
    record = copy.deepcopy(dict(page["dataset"]))
    statement = page.get("green") or {}
    kind = green.type_of(statement)
    for key, label, value, box in green.master_fields(statement):
        record["fields"].append({
            "block": "green_power", "block_label": green.TYPE_NAMES[kind], "key": key, "label": label,
            "printed_label": None, "value": value, "recognized": True, "status": "reviewed",
            "confidence": "reviewed", "checks": None, "cross_check_value": None,
            "source": f"{kind}:{reviewer}", "origin": kind,
            "bbox": dict(zip(("x", "y", "width", "height"), box)) if box else None,
        })
    start, end = _split_period(statement.get("period"))
    record["summary"].update(
        document_type=f"green_power_{kind}", billing_period_start=start, billing_period_end=end,
        total_amount_twd=green.total_cost(page), needs_review=False, confidence_review=0,
    )
    return record


def document_record(page: Mapping[str, Any], reviewer: str) -> dict[str, Any]:
    """The master's record of a document that is not a Taipower bill.

    Its OCR text is kept as read; the reviewed values are added as fields of
    a "document" block, each with where it came from.
    """
    record = copy.deepcopy(dict(page["dataset"]))
    row, ocr_row = page["row"] or {}, page["ocr_row"] or {}
    sources, locations = page.get("sources") or {}, page.get("locations") or {}
    for column, (key, label) in _DOCUMENT_FIELDS.items():
        value = row.get(column)
        if value is None:
            continue
        source = sources.get(column, "manual")
        location = locations.get(column)
        box = dict(zip(("x", "y", "width", "height"), location["box"])) if location else None
        record["fields"].append({
            "block": "document", "block_label": "其他單據", "key": key, "label": label, "printed_label": None,
            "value": value, "recognized": True, "status": _SOURCE_STATUS.get(source, source), "confidence": "reviewed",
            "checks": None, "cross_check_value": ocr_row.get(column) if ocr_row.get(column) != value else None,
            "source": f"{source}:{reviewer}", "origin": source, "bbox": box,
        })
    start, end = _split_period(row.get(documents.PERIOD))
    record["summary"].update(
        document_type="other_document", billing_period_start=start, billing_period_end=end,
        total_amount_twd=row.get(documents.TOTAL) if row.get(documents.TOTAL) is not None else row.get(documents.PRETAX),
        needs_review=False, confidence_review=0,
    )
    return record


def write_workbooks(store: Store, output_dir: Path, user: str | None = None, tolerance: float = 3) -> list[str]:
    """Rebuild both workbooks from submitted bills.

    With ``user`` only that user's bills are written, into their own folder
    (see ``workbook_dir``). Returns the names of workbooks that could not be
    replaced because they are open in another program.
    """
    submitted = list(store.pages(user=user, bills_only=True, submitted_only=True))
    locked: list[str] = []
    target_dir = workbook_dir(output_dir, user)

    report = target_dir / "taipower_bills_17.xlsx"
    if not save_tables(report, report_tables(submitted, store, output_dir, tolerance)):
        locked.append(report.name)

    sheets = empty_sheets()
    by_upload: dict[int, list[Mapping[str, Any]]] = {}
    for page in submitted:
        by_upload.setdefault(page["upload_id"], []).append(page)
    for upload_id, bill_pages in sorted(by_upload.items()):
        upload = store.upload(upload_id)
        corrected = {
            page["id"]: (
                green_record(page, page["submitted_by"] or page["user"])
                if _is_green(page)
                else document_record(page, page["submitted_by"] or page["user"])
                if page.get("kind") == documents.OTHER
                else apply_corrections(page["dataset"], page["ocr_row"], page["row"], page["submitted_by"] or page["user"])
            )
            for page in bill_pages
        }
        # Back pages (meter readings, energy saving) of the same upload belong
        # to the bill too, so they are included alongside the reviewed page.
        pages = []
        for page in store.pages():
            if page["upload_id"] != upload_id:
                continue
            record = copy.deepcopy(corrected.get(page["id"], page["dataset"]))
            record["summary"].update(
                uploaded_by=upload["user"],
                submitted_by=bill_pages[0]["submitted_by"],
                submitted_at=bill_pages[0]["submitted_at"],
            )
            pages.append(record)
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
    master = target_dir / "taipower_bills.xlsx"
    if not save_master(master, sheets):
        locked.append(master.name)
    return locked


class WorkbookWriter:
    """Rebuilds the workbooks one request at a time.

    Simultaneous submits would otherwise race on the same files. When a
    workbook is open in Excel the write is retried every ``retry_seconds``
    in the background, so it does not wait for the next submit.

    With ``per_user`` each user's workbooks are written separately; ``write``
    is then given the user whose bills changed.
    """

    def __init__(self, store: Store, output_dir: Path, retry_seconds: float = 60, per_user: bool = False, tolerance: float = 3) -> None:
        self.store = store
        # Green power statements' allowed difference, for their checks.
        self.tolerance = tolerance
        self.output_dir = output_dir
        self.retry_seconds = retry_seconds
        self.per_user = per_user
        self._lock = threading.Lock()
        # Workbook sets still to write: None (shared) or a user name.
        self._pending: set[str | None] = set()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._retry_loop, name="workbook-retry", daemon=True)

    @property
    def pending(self) -> bool:
        return bool(self._pending)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def write_all(self) -> list[str]:
        """Rebuild every workbook: the shared pair, or each user's."""
        if not self.per_user:
            return self.write()
        users = {page["user"] for page in self.store.pages(bills_only=True, submitted_only=True)}
        return [name for user in sorted(users) for name in self.write(user)]

    def write(self, user: str | None = None) -> list[str]:
        """Rebuild the workbooks now; returns the ones that were locked."""
        key = user if self.per_user else None
        with self._lock:
            locked = write_workbooks(self.store, self.output_dir, key, self.tolerance)
            if locked:
                self._pending.add(key)
                log.warning("%s open elsewhere; retrying every %ss", ", ".join(locked), self.retry_seconds)
            else:
                self._pending.discard(key)
            return locked

    def _retry_loop(self) -> None:
        while not self._stop.wait(self.retry_seconds):
            for key in list(self._pending):
                try:
                    self.write(key)
                except Exception:  # noqa: BLE001 - keep retrying
                    log.exception("Retrying the workbooks failed")
