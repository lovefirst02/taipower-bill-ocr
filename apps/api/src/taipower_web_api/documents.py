"""Documents that are not Taipower bills, such as a landlord's allocation.

When no page of an upload is a Taipower bill, its first page becomes an
item of kind "other". The billing period, usage (kWh) and cost are looked
for by keywords (``taipower_bill_ocr.keyword_extract``); what is not found
is picked on the page image or typed in by the reviewer.

Each value keeps where it came from (``SOURCES``), shown on the page and in
the Excel report.
"""

from __future__ import annotations

from typing import Any

from taipower_bill_ocr.excel_export import COLUMNS, REVIEW_COLUMN
from taipower_bill_ocr.keyword_extract import (
    PERIOD,
    PRETAX,
    TOTAL,
    USAGE,
    Extraction,
    extract,
    lines_from_dataset,
)

from .evidence import USAGE_LABEL, _highlight_and_label
from .settings import KEYWORD_SETTINGS, Settings
from .store import Store

TAIPOWER, OTHER = "taipower", "other"
DOCUMENT_TYPES = {TAIPOWER: "台電帳單", OTHER: "其他單據", "green": "綠電明細"}

# Where a value came from.
SOURCES = {
    "taipower": "台電帳單辨識",
    "keyword": "關鍵字",
    "pick": "圖上點選",
    "manual": "手動輸入",
    "alternative": "另一個辨識結果",
}
# What is looked for by keyword on a non-Taipower document; every other
# column stays open for the reviewer to fill in.
DOCUMENT_COLUMNS = [PERIOD, USAGE, PRETAX, TOTAL]
# The red label written beside each value's highlight.
LABELS = {PERIOD: "Billing period", USAGE: USAGE_LABEL, PRETAX: "Grey Electricity Cost"}
_NAMES = {PERIOD: ("Billing period", "計費期間"), USAGE: ("Usage (kWh)", "用電度數"),
          PRETAX: ("Cost before tax", "未稅金額"), TOTAL: ("Cost with tax", "含稅金額")}


# Printed on Taipower bills, old and new; a landlord's statement may name
# 台電 or the meter's 電號, but not Taipower's own charge lines.
TAIPOWER_WORDS = ("台灣電力", "臺灣電力", "taiwanpower", "流動電費", "基本電費", "計費度數", "經常度數", "繳費憑證")


def is_taipower_bill(bill: Any) -> bool:
    """Whether a page is a Taipower bill: a known layout, or Taipower's own wording.

    The parser's fallback layout reads any document, so its result alone
    does not tell (a landlord's allocation also has a 計費期間).
    """
    if bill.get("document_type") not in (None, "unknown"):
        return True
    text = "".join(line.text for line in getattr(bill, "ocr_lines", None) or []).replace(" ", "").lower()
    return any(word.lower() in text for word in TAIPOWER_WORDS)


def keywords_of(settings: Settings) -> dict[str, list[str]]:
    return {key: list(getattr(settings, key)) for key in KEYWORD_SETTINGS}


def document_pages(store: Store, first: dict[str, Any]) -> list[dict[str, Any]]:
    """Every page of the document, first page first."""
    return store.item_pages(first)


def detect(store: Store, first: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Values, marks and review notes for a document, found by keyword.

    Returns the page columns to store on the document's first page.
    """
    pages = document_pages(store, first)
    lines = [line for page in pages for line in lines_from_dataset(page["dataset"], page["page_number"])]
    found = extract(lines, keywords_of(settings))

    row: dict[str, Any] = {column: None for column in COLUMNS}
    row.update({USAGE: None, "來源檔案": first["filename"], "頁碼": first["page_number"]})
    issues, alternatives, locations, sources = [], {}, {}, {}
    for column in DOCUMENT_COLUMNS:
        best = found.best(column)
        en, zh = _NAMES[column]
        if best is None:
            if column != TOTAL:
                issues.append({"zh": f"找不到{zh}，請在圖上點選或手動輸入", "en": f"{en}: not found — pick it on the image or type it", "columns": [column]})
            continue
        row[column] = best.value
        sources[column] = "keyword"
        locations[column] = {"box": list(best.box), "exact": True, "page": best.page}
        others = [candidate.value for candidate in found.candidates[column][1:] if candidate.value != best.value]
        if others:
            alternatives[column] = others[0]
        issues.append({
            "zh": f"{zh}以關鍵字「{best.keyword}」找到，請確認" if best.keyword else f"{zh}由頁面內容推測，請確認",
            "en": f"{en}: found by the keyword {best.keyword} — check it" if best.keyword else f"{en}: found on the page — check it",
            "columns": [column],
        })
    for note in found.notes:
        issues.append({"zh": note, "en": note, "columns": []})
    row[REVIEW_COLUMN] = "；".join(issue["zh"] for issue in issues) or None

    marks, notes = _marks(pages, found, first)
    return {
        "kind": OTHER,
        "is_bill": True,
        "row": row,
        "ocr_row": dict(row),
        "issues": issues,
        "alternatives": alternatives,
        "locations": locations,
        "marks": marks,
        "mark_notes": notes,
        "sources": sources,
    }


def _marks(pages: list[dict[str, Any]], found: Extraction, first: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    by_number = {page["page_number"]: page for page in pages}
    taken: dict[Any, list[tuple[float, ...]]] = {
        page["page_number"]: [(line["x"], line["y"], line["width"], line["height"]) for line in page["dataset"].get("ocr_lines") or []]
        for page in pages
    }
    marks, notes = [], []
    for column, label in LABELS.items():
        best = found.best(column)
        if best is None:
            notes.append(f"{label}: not found — pick the value on the image to mark it")
            continue
        page = by_number.get(best.page, first)
        added = _highlight_and_label(label, best.box, (page["image_width"], page["image_height"]), taken[page["page_number"]], [])
        for mark in added:
            mark["field"] = column
            if page["id"] != first["id"]:
                mark["page"] = page["page_number"]
        marks += added
    return marks, notes


def source_text(kind: str | None, sources: dict[str, str] | None) -> str:
    """Where each value came from, for the Excel report."""
    sources = sources or {}
    parts = [f"{_NAMES.get(column, (column, column))[1]}:{SOURCES.get(source, source)}" for column, source in sources.items()]
    if (kind or TAIPOWER) == TAIPOWER:
        parts.append(f"其他:{SOURCES['taipower']}")
    elif kind == "green" and not parts:
        parts.append("明細表辨識")
    return "；".join(parts)
