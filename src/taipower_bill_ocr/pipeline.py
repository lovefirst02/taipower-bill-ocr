from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .parser import (
    BillResult,
    enrich_bank_autopay_receipt,
    enrich_unruled_tables,
    parse_bill_layout,
)
from .windows_ocr import WindowsOcrEngine


def _missing(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _equivalent(left: Any, right: Any) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        return left.strip().casefold() == right.strip().casefold()
    return left == right


def _merge_fields(
    primary: dict[str, Any],
    secondary: dict[str, Any],
    *,
    prefix: str,
    stats: dict[str, Any],
) -> None:
    for key, paddle_value in secondary.items():
        if _missing(paddle_value):
            continue
        path = f"{prefix}.{key}" if prefix else key
        if key not in primary or _missing(primary[key]):
            primary[key] = deepcopy(paddle_value)
            stats["filled_from_paddle_count"] += 1
            stats["filled_from_paddle"].append(path)
            continue

        windows_value = primary[key]
        if isinstance(windows_value, dict) and isinstance(paddle_value, dict):
            _merge_fields(
                windows_value,
                paddle_value,
                prefix=path,
                stats=stats,
            )
        elif _equivalent(windows_value, paddle_value):
            stats["agreement_count"] += 1
        else:
            stats["conflicts"].append(
                {
                    "path": path,
                    "windows": deepcopy(windows_value),
                    "paddleocr": deepcopy(paddle_value),
                }
            )


def _fill_missing(primary: dict[str, Any], secondary: dict[str, Any]) -> None:
    for key, value in secondary.items():
        if key not in primary or _missing(primary[key]):
            primary[key] = deepcopy(value)
        elif isinstance(primary[key], dict) and isinstance(value, dict):
            _fill_missing(primary[key], value)


def merge_ocr_results(
    windows_result: BillResult, paddle_result: BillResult
) -> BillResult:
    """Keep Windows values, fill gaps from Paddle, and report disagreements."""
    merged = BillResult(deepcopy(windows_result))
    stats: dict[str, Any] = {
        "primary": "windows",
        "secondary": "paddleocr",
        "agreement_count": 0,
        "filled_from_paddle_count": 0,
        "conflict_count": 0,
        "filled_from_paddle": [],
        "conflicts": [],
        "windows_quality": deepcopy(windows_result.get("quality", {})),
        "paddleocr_quality": deepcopy(paddle_result.get("quality", {})),
    }

    windows_blocks = merged.setdefault("blocks", {})
    paddle_blocks = paddle_result.get("blocks", {})
    for block_key, paddle_block in paddle_blocks.items():
        if block_key not in windows_blocks:
            copied_block = deepcopy(paddle_block)
            windows_blocks[block_key] = copied_block
            merged[block_key] = copied_block.get("fields", {})
            filled = merged[block_key]
            if filled:
                stats["filled_from_paddle_count"] += 1
                stats["filled_from_paddle"].append(block_key)
            continue

        windows_fields = merged.get(block_key)
        if not isinstance(windows_fields, dict):
            windows_fields = windows_blocks[block_key].setdefault("fields", {})
            merged[block_key] = windows_fields
        _merge_fields(
            windows_fields,
            paddle_block.get("fields", {}),
            prefix=block_key,
            stats=stats,
        )
        windows_blocks[block_key]["fields"] = windows_fields
        windows_blocks[block_key]["ocr_raw_lines"] = {
            "windows": deepcopy(windows_blocks[block_key].get("raw_lines", [])),
            "paddleocr": deepcopy(paddle_block.get("raw_lines", [])),
        }

    merged_by_label = merged.setdefault("by_label", {})
    _fill_missing(merged_by_label, paddle_result.get("by_label", {}))

    stats["conflict_count"] = len(stats["conflicts"])
    quality = merged.setdefault("quality", {})
    warnings = quality.setdefault("warnings", [])
    for conflict in stats["conflicts"]:
        warning = f"hybrid_conflict:{conflict['path']}"
        if warning not in warnings:
            warnings.append(warning)
    quality["ocr_engine"] = "hybrid"
    quality["hybrid"] = stats
    return merged


def extract_bill(
    image_path: str | Path,
    *,
    language: str = "zh-Hant",
    ocr_profile: str = "accurate",
    ocr_backend: str = "windows",
    paddle_score_threshold: float = 0.45,
) -> BillResult:
    """OCR one bill image and return blocks containing plain field dictionaries."""
    if ocr_backend == "hybrid":
        windows_result = extract_bill(
            image_path,
            language=language,
            ocr_profile=ocr_profile,
            ocr_backend="windows",
            paddle_score_threshold=paddle_score_threshold,
        )
        paddle_result = extract_bill(
            image_path,
            language=language,
            ocr_profile=ocr_profile,
            ocr_backend="paddleocr",
            paddle_score_threshold=paddle_score_threshold,
        )
        return merge_ocr_results(windows_result, paddle_result)

    if ocr_backend == "paddleocr":
        from .paddle_ocr import extract_bill_with_paddleocr

        paddle_language = (
            "chinese_cht" if language in {"zh-Hant", "zh-TW"} else language
        )
        return extract_bill_with_paddleocr(
            image_path,
            language=paddle_language,
            score_threshold=paddle_score_threshold,
        )
    if ocr_backend != "windows":
        raise ValueError(
            f"unsupported OCR backend: {ocr_backend!r}; "
            "expected 'windows', 'paddleocr', or 'hybrid'"
        )

    engine = WindowsOcrEngine(language=language)
    if ocr_profile == "blockwise_high_accuracy":
        (
            lines,
            bounds,
            words_by_block,
            document_type,
            detection_method,
        ) = engine.recognize_blocks(image_path)
    else:
        lines = engine.recognize(image_path, profile=ocr_profile)
        bounds = {}
        words_by_block = {}
        document_type = "unknown"
        detection_method = "full_page"
    result = parse_bill_layout(lines)
    result.ocr_lines = lines
    # Layout parsing has stronger evidence than the coarse page-level detector.
    # Keep a parser-selected subtype while retaining the detector result for
    # legacy layouts whose parser does not set ``document_type``.
    document_type = result.get("document_type") or document_type
    result["document_type"] = document_type
    result["quality"]["block_detection"] = detection_method
    for block_key, block_bounds in bounds.items():
        if block_key in result["blocks"]:
            result["blocks"][block_key]["bounds"] = block_bounds
    enrich_unruled_tables(result, words_by_block, bounds)
    if document_type == "bank_autopay_receipt":
        enrich_bank_autopay_receipt(result, words_by_block, bounds)
        payment_fields = result.get("payment_info", {})
        payment_date = payment_fields.pop("due_date", None)
        if payment_date:
            payment_fields["payment_date"] = payment_date
    return result


def extract_pdf(
    pdf_path: str | Path,
    *,
    page_numbers: list[int] | None = None,
    dpi: int = 200,
    language: str = "zh-Hant",
    ocr_backend: str = "windows",
    paddle_score_threshold: float = 0.45,
) -> list[BillResult]:
    """Render image-PDF pages and return one structured bill per selected page."""
    if ocr_backend == "hybrid":
        windows_results = extract_pdf(
            pdf_path,
            page_numbers=page_numbers,
            dpi=dpi,
            language=language,
            ocr_backend="windows",
            paddle_score_threshold=paddle_score_threshold,
        )
        paddle_results = extract_pdf(
            pdf_path,
            page_numbers=page_numbers,
            dpi=dpi,
            language=language,
            ocr_backend="paddleocr",
            paddle_score_threshold=paddle_score_threshold,
        )
        if len(windows_results) != len(paddle_results):
            raise RuntimeError("OCR backends returned different PDF page counts")
        return [
            merge_ocr_results(windows_result, paddle_result)
            for windows_result, paddle_result in zip(
                windows_results, paddle_results
            )
        ]

    if ocr_backend == "paddleocr":
        from .paddle_ocr import extract_pdf_with_paddleocr

        paddle_language = (
            "chinese_cht" if language in {"zh-Hant", "zh-TW"} else language
        )
        return extract_pdf_with_paddleocr(
            pdf_path,
            page_numbers=page_numbers,
            dpi=dpi,
            language=paddle_language,
            score_threshold=paddle_score_threshold,
        )
    if ocr_backend != "windows":
        raise ValueError(
            f"unsupported OCR backend: {ocr_backend!r}; "
            "expected 'windows', 'paddleocr', or 'hybrid'"
        )

    import pdfplumber

    path = Path(pdf_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)

    bills: list[BillResult] = []
    with pdfplumber.open(path) as pdf, TemporaryDirectory(
        prefix="taipower_pdf_"
    ) as temp_dir:
        selected = page_numbers or list(range(1, len(pdf.pages) + 1))
        for page_number in selected:
            if page_number < 1 or page_number > len(pdf.pages):
                raise ValueError(f"page number out of range: {page_number}")
            page_image = pdf.pages[page_number - 1].to_image(
                resolution=dpi, antialias=True
            )
            image_path = Path(temp_dir) / f"page-{page_number}.png"
            page_image.original.save(image_path)
            bill = extract_bill(
                image_path,
                language=language,
                ocr_profile="blockwise_high_accuracy",
            )
            bill["quality"].update(
                {
                    "source": "image_pdf",
                    "page_number": page_number,
                    "dpi": dpi,
                    "ocr_profile": "blockwise_high_accuracy",
                }
            )
            bills.append(bill)
    return bills
