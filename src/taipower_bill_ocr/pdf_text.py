"""A bill read from a PDF's own text, without looking at the page.

Most PDFs of bills are scans with no text, and some have text that says nothing
readable. Text that gives a bill's charges and a total that adds up is kept, as
it holds what was printed where a model only reads the picture; the models fill
in only what it lacks, and read whatever page's text cannot be trusted.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .excel_export import BillRecord, _is_number, bill_to_record, is_charge_page
from .parser import BillResult, OcrLine, parse_bill_layout

_LINE_GAP = 0.8  # of the text's height: wider than this between two words, they are two cells
_MIN_WORDS = 20  # fewer than this, the page's text is a stamp or a caption over a scan
_WORD_GAP = 0.5  # points: pdfplumber's default of 3 glues the last word of a cell to the next cell's first
_WALL_SLACK = 1.0  # points a word may overrun the rule beside it
# A long period fills its cell up to the next column's label: 115年07月01日至11|稅前應繳總金額.
_DATE_CUT = re.compile(r"\d{1,2}日(?:[至~～\-－]\d{0,3})?$")
_DATE_GOES_ON = re.compile(r"[\d至~～\-－年月日]")


def _apart(cell: list[dict[str, Any]], word: dict[str, Any], walls: list[tuple[float, float, float]]) -> bool:
    """Whether a word starts a new cell after the words before it."""
    left, right = cell[-1]["x1"], word["x0"]
    if right - left > (word["bottom"] - word["top"]) * _LINE_GAP:
        return True
    middle = (word["top"] + word["bottom"]) / 2
    if any(left - _WALL_SLACK <= x <= right + _WALL_SLACK and top <= middle <= bottom for x, top, bottom in walls):
        return True
    return bool(_DATE_CUT.search("".join(item["text"] for item in cell))) and not _DATE_GOES_ON.match(word["text"])


def lines_of(words: list[dict[str, Any]], scale: float = 1.0, walls: list[tuple[float, float, float]] | None = None) -> list[OcrLine]:
    """pdfplumber's words as lines of cells: "經 常 ( 尖 峰 ) 度 數" stays one, its figure another.

    ``walls`` are the table's vertical rules, as (x, top, bottom): no cell crosses one.
    """
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
            if word is not None and not _apart(cell, word, walls or []):
                cell.append(word)
                continue
            top, bottom = min(item["top"] for item in cell), max(item["bottom"] for item in cell)
            lines.append(OcrLine("".join(item["text"] for item in cell), cell[0]["x0"] * scale, top * scale,
                                 (cell[-1]["x1"] - cell[0]["x0"]) * scale, (bottom - top) * scale))
            cell = [word]
    return sorted(lines, key=lambda line: (line.y, line.x))


def _can_be_trusted(record: BillRecord) -> bool:
    """Text holds what was printed, so gaps are no reason to doubt it: garbling or a wrong total is."""
    row = record.row
    if not _is_number(row["應繳總金額"]) or not (_is_number(row["基本電費"]) or _is_number(row["流動電費"])):
        return False
    return not any({"應繳總金額", "稅前應繳總金額"} <= set(issue.columns) for issue in record.issues)


def read_page(path: Path, page_number: int | None, dpi: int, notes: dict[str, Any] | None = None) -> BillResult | None:
    """The bill in this PDF page's text; None if the page must be read by a model.

    What the page's text was like goes in ``notes``: under "refused" why it was
    not used, under "missing" what a bill that was used still lacks.
    """
    import pdfplumber

    notes = {} if notes is None else notes
    try:
        with pdfplumber.open(path) as pdf:
            page = pdf.pages[(page_number or 1) - 1]
            words = page.extract_words(x_tolerance=_WORD_GAP)
            walls = [(edge["x0"], edge["top"], edge["bottom"]) for edge in page.edges if edge["orientation"] == "v"]
            notes.update(words=len(words), rules=len(walls), page=f"{page.width:.0f}x{page.height:.0f}")
        if len(words) < _MIN_WORDS:
            return None
        # A rule can run through text too, as an underline does: then the rules are no guide.
        best = None
        for rules in ([walls, []] if walls else [[]]):
            lines = lines_of(words, dpi / 72, rules)  # the parser's distances are in pixels of a page at this size
            bill = parse_bill_layout(lines)
            if not is_charge_page(bill):
                continue
            record = bill_to_record(bill, path.name)
            if _can_be_trusted(record) and (best is None or len(record.issues) < len(best[2])):
                best = (bill, lines, record.issues)
            elif not _can_be_trusted(record) and "refused" not in notes:
                notes["refused"] = [issue.zh for issue in record.issues] or ["總金額或電費沒有讀到"]
                notes["lines"] = [line.text for line in lines]
        if best is None:
            return None
    except Exception as error:  # noqa: BLE001 - the models can still read the page
        notes["refused"] = [f"{type(error).__name__}: {error}"]
        return None
    bill, lines, issues = best
    notes.pop("refused", None)
    notes.pop("lines", None)
    if issues:
        notes["missing"] = [issue.zh for issue in issues]
    bill.ocr_lines = lines
    bill["quality"].update({"source": "pdf_text", "page_number": page_number, "dpi": dpi})
    return bill
