from __future__ import annotations

import argparse
import json
import sys

from pathlib import Path

from .batch import FileFinished, PageDone, PageExtractor, iter_batch
from .excel_export import bills_to_rows, write_rows_to_excel
from .pipeline import extract_bill, extract_pdf


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract Taiwan Power bills as JSON or an Excel sheet."
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        metavar="input",
        help="Path to a bill image or image-based PDF (several allowed with --excel)",
    )
    parser.add_argument("--language", default="zh-Hant", help="Windows OCR language")
    parser.add_argument(
        "--pages", help="Comma-separated, 1-based PDF page numbers (default: all)"
    )
    parser.add_argument("--dpi", type=int, default=200, help="PDF rendering DPI")
    parser.add_argument("--pretty", action="store_true", help="Indent JSON output")
    parser.add_argument(
        "--excel",
        metavar="OUTPUT.xlsx",
        help="Write one row per bill page with charges to this Excel file",
    )
    parser.add_argument(
        "--ocr-backend",
        choices=("crosscheck", "paddleocr", "windows", "hybrid"),
        help=(
            "OCR engine (default: crosscheck with --excel, otherwise windows). "
            "crosscheck keeps PaddleOCR values and flags disagreements with Windows OCR"
        ),
    )
    args = parser.parse_args()
    ocr_backend = args.ocr_backend or ("crosscheck" if args.excel else "windows")
    if ocr_backend == "crosscheck" and not args.excel:
        parser.error("--ocr-backend crosscheck requires --excel")
    backend_options = (
        {}
        if ocr_backend == "windows"
        else {"ocr_backend": ocr_backend, "paddle_score_threshold": 0.45}
    )
    page_numbers = (
        [int(value.strip()) for value in args.pages.split(",")]
        if args.pages
        else None
    )

    def extract(input_path: Path):
        if input_path.suffix.lower() == ".pdf":
            return extract_pdf(
                input_path,
                page_numbers=page_numbers,
                dpi=args.dpi,
                language=args.language,
                **backend_options,
            )
        return extract_bill(input_path, language=args.language, **backend_options)

    if args.excel and ocr_backend != "hybrid":
        rows = []
        extractor = PageExtractor(ocr_backend, args.dpi)
        for event in iter_batch(
            [Path(value) for value in args.inputs], extractor, page_numbers=page_numbers
        ):
            if isinstance(event, PageDone) and event.record is not None:
                rows.append(event.record.row)
            elif isinstance(event, FileFinished) and event.error:
                print(f"Skipped {event.path}: {event.error}", file=sys.stderr)
        output = write_rows_to_excel(rows, args.excel)
        flagged = sum(1 for row in rows if row.get("需人工檢查"))
        print(f"Wrote {len(rows)} row(s) to {output} ({flagged} need review)")
        return

    if args.excel:
        rows = []
        for input_path in map(Path, args.inputs):
            result = extract(input_path)
            bills = result if isinstance(result, list) else [result]
            rows.extend(bills_to_rows(bills, source=input_path.name))
        output = write_rows_to_excel(rows, args.excel)
        print(f"Wrote {len(rows)} row(s) to {output}")
        return

    if len(args.inputs) != 1:
        parser.error("multiple inputs require --excel")
    result = extract(Path(args.inputs[0]))
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
        )
    )


if __name__ == "__main__":
    main()
