"""A bill read from a PDF's own text, without looking at the page.

Most PDFs of bills are scans with no text, and some have text that says nothing
readable. The text is used only where the bill it gives raises no question at
all; anything less goes to the models, so this can only save time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .excel_export import bill_to_record, is_charge_page
from .parser import BillResult, OcrLine, parse_bill_layout

_LINE_GAP = 0.8  # of the text's height: wider than this between two words, they are two cells
_MIN_WORDS = 20  # fewer than this, the page's text is a stamp or a caption over a scan


def lines_of(words: list[dict[str, Any]], scale: float = 1.0) -> list[OcrLine]:
    """pdfplumber's words as lines of cells: "經 常 ( 尖 峰 ) 度 數" stays one, its figure another."""
    lines: list[OcrLine] = []
    rows: list[list[dict[str, Any]]] = []
    for word in sorted(words, key=lambda item: (item["top"] + item["bottom"]) / 2):
        middle = (word["top"] + word["bottom"]) / 2
        if rows and abs(middle - (rows[-1][0]["top"] + rows[-1][0]["bottom"]) / 2) < (word["bottom"] - word["top"]) / 2:
            rows[-1].append(word)
        else:
            rows.append([word])
    for row in rows:
        row.sort(key=lambda item: item["x0"])
        cell = [row[0]]
        for word in row[1:] + [None]:
            if word is not None and word["x0"] - cell[-1]["x1"] <= (word["bottom"] - word["top"]) * _LINE_GAP:
                cell.append(word)
                continue
            top, bottom = min(item["top"] for item in cell), max(item["bottom"] for item in cell)
            lines.append(OcrLine("".join(item["text"] for item in cell), cell[0]["x0"] * scale, top * scale,
                                 (cell[-1]["x1"] - cell[0]["x0"]) * scale, (bottom - top) * scale))
            cell = [word]
    return sorted(lines, key=lambda line: (line.y, line.x))


def read_page(path: Path, page_number: int | None, dpi: int) -> BillResult | None:
    """The bill in this PDF page's text; None if the page must be read by a model."""
    import pdfplumber

    try:
        with pdfplumber.open(path) as pdf:
            words = pdf.pages[(page_number or 1) - 1].extract_words()
        if len(words) < _MIN_WORDS:
            return None
        lines = lines_of(words, dpi / 72)  # the parser's distances are in pixels of a page at this size
        bill = parse_bill_layout(lines)
        if not is_charge_page(bill) or bill_to_record(bill, path.name).issues:
            return None
    except Exception:  # noqa: BLE001 - the models can still read the page
        return None
    bill.ocr_lines = lines
    bill["quality"].update({"source": "pdf_text", "page_number": page_number, "dpi": dpi})
    return bill
