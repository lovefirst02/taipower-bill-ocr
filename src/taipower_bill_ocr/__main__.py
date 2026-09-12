from __future__ import annotations

import argparse
import json

from pathlib import Path

from .pipeline import extract_bill, extract_pdf


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Use Windows OCR to extract a Taiwan Power bill as JSON."
    )
    parser.add_argument("input", help="Path to a bill image or image-based PDF")
    parser.add_argument("--language", default="zh-Hant", help="Windows OCR language")
    parser.add_argument(
        "--pages", help="Comma-separated, 1-based PDF page numbers (default: all)"
    )
    parser.add_argument("--dpi", type=int, default=200, help="PDF rendering DPI")
    parser.add_argument("--pretty", action="store_true", help="Indent JSON output")
    args = parser.parse_args()

    input_path = Path(args.input)
    if input_path.suffix.lower() == ".pdf":
        page_numbers = (
            [int(value.strip()) for value in args.pages.split(",")]
            if args.pages
            else None
        )
        result = extract_pdf(
            input_path,
            page_numbers=page_numbers,
            dpi=args.dpi,
            language=args.language,
        )
    else:
        result = extract_bill(input_path, language=args.language)
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
        )
    )


if __name__ == "__main__":
    main()
