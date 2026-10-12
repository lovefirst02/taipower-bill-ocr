"""Optional PaddleOCR 3.x adapter for comparing OCR engines."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from PIL import Image, ImageEnhance, ImageOps

from .parser import (
    BillResult,
    OcrLine,
    enrich_bank_autopay_receipt,
    enrich_unruled_tables,
    parse_bill_layout,
)
from .windows_ocr import WindowsOcrEngine


_BLOCK_TITLES = {
    "payment_info": "繳費資訊 Payment Info.",
    "user_info": "用戶資訊 Basic Info.",
    "charge_info": "計費內容 Charge Info.",
    "other_info": "其他資訊 Other Info.",
    "invoice_info": "前期發票資訊 Invoice",
    "carrier_info": "載具資訊 Carrier",
    "charge_introduction": "電費計收說明 Charge Introduction",
    "energy_saving_info": "節電資訊 Energy Saving Info.",
    "meter_info": "電表資料 Meter Info.",
}


DETECTION_MODEL = "PP-OCRv6_small_det"
RECOGNITION_MODEL = "PP-OCRv6_small_rec"


def bundled_model_dir() -> Path | None:
    """Folder holding pre-downloaded Paddle models, if one is available.

    ``TAIPOWER_PADDLE_MODELS`` wins; a packaged app also looks in the
    ``models`` folder beside its executable so it can run offline.
    """
    configured = os.environ.get("TAIPOWER_PADDLE_MODELS")
    candidates = [Path(configured)] if configured else []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "models")
    return next(
        (
            path
            for path in candidates
            if (path / DETECTION_MODEL).is_dir() and (path / RECOGNITION_MODEL).is_dir()
        ),
        None,
    )


def _create_paddle_engine(language: str) -> Any:
    try:
        from paddleocr import PaddleOCR
    except ImportError as exc:
        raise RuntimeError(
            "PaddleOCR 尚未安裝；請先執行：uv sync --extra paddleocr"
        ) from exc

    model_options: dict[str, Any] = {}
    if (model_dir := bundled_model_dir()) is not None:
        model_options = {
            "text_detection_model_dir": str(model_dir / DETECTION_MODEL),
            "text_recognition_model_dir": str(model_dir / RECOGNITION_MODEL),
        }
    return PaddleOCR(
        device="cpu",
        text_detection_model_name=DETECTION_MODEL,
        text_recognition_model_name=RECOGNITION_MODEL,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        engine="paddle",
        **model_options,
    )


def _result_data(result: Any) -> Mapping[str, Any]:
    payload = getattr(result, "json", result)
    if callable(payload):
        payload = payload()
    if not isinstance(payload, Mapping):
        raise TypeError("PaddleOCR result must expose a mapping through .json")
    nested = payload.get("res")
    return nested if isinstance(nested, Mapping) else payload


def _rectangle(coordinates: Any, *, polygon: bool) -> tuple[float, float, float, float]:
    values = coordinates.tolist() if hasattr(coordinates, "tolist") else coordinates
    if polygon:
        points = list(values)
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
        left, top, right, bottom = min(xs), min(ys), max(xs), max(ys)
    else:
        left, top, right, bottom = (float(value) for value in values)
    return left, top, right - left, bottom - top


def _iter_paddle_lines(
    results: Iterable[Any], score_threshold: float
) -> Iterable[OcrLine]:
    for result in results:
        data = _result_data(result)
        texts = data.get("rec_texts", [])
        scores = data.get("rec_scores", [])
        coordinates = data.get("rec_boxes")
        polygon = coordinates is None
        if polygon:
            coordinates = data.get("rec_polys", [])

        for index, text in enumerate(texts):
            if index >= len(coordinates):
                continue
            score = float(scores[index]) if index < len(scores) else 1.0
            cleaned = str(text).strip()
            if not cleaned or score < score_threshold:
                continue
            x, y, width, height = _rectangle(coordinates[index], polygon=polygon)
            yield OcrLine(cleaned, x, y, width, height)


def recognize_with_paddleocr(
    image_path: str | Path,
    *,
    language: str = "chinese_cht",
    score_threshold: float = 0.0,
    ocr_engine: Any | None = None,
) -> list[OcrLine]:
    """Run PaddleOCR 3.x on one image and return parser-compatible text lines."""
    path = Path(image_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if not 0.0 <= score_threshold <= 1.0:
        raise ValueError("score_threshold must be between 0 and 1")

    engine = ocr_engine or _create_paddle_engine(language)
    lines = list(_iter_paddle_lines(engine.predict(str(path)), score_threshold))
    return sorted(lines, key=lambda line: (line.y, line.x))


def _paddle_block_bounds(
    image: Image.Image,
) -> tuple[dict[str, tuple[int, int, int, int]], str, str]:
    front_bounds = WindowsOcrEngine._high_voltage_block_bounds(image)
    if "invoice_info" in front_bounds:
        return front_bounds, "high_voltage_bill", "pink_template"

    bank_bounds = WindowsOcrEngine._bank_autopay_block_bounds(image)
    if bank_bounds:
        return bank_bounds, "bank_autopay_receipt", "pink_bank_autopay"

    if front_bounds:
        return front_bounds, "high_voltage_bill", "pink_template"

    back_bounds = WindowsOcrEngine._high_voltage_back_block_bounds(image)
    if back_bounds:
        return back_bounds, "high_voltage_bill", "pink_template"

    colored_mask = WindowsOcrEngine._colored_mask(image)
    normalized = Image.new("RGB", image.size, "white")
    normalized.paste((190, 100, 200), mask=colored_mask)

    front_bounds = WindowsOcrEngine._high_voltage_block_bounds(normalized)
    if "invoice_info" in front_bounds:
        return front_bounds, "high_voltage_bill", "color_independent"

    bank_bounds = WindowsOcrEngine._bank_autopay_block_bounds(normalized)
    if bank_bounds:
        return bank_bounds, "bank_autopay_receipt", "color_independent"

    if front_bounds:
        return front_bounds, "high_voltage_bill", "color_independent"

    back_bounds = WindowsOcrEngine._high_voltage_back_block_bounds(normalized)
    if back_bounds:
        return back_bounds, "high_voltage_bill", "color_independent"

    return {}, "unknown", "full_page_fallback"


def _recognize_paddle_blocks(
    image_path: Path,
    *,
    engine: Any,
    language: str,
    score_threshold: float,
) -> tuple[
    list[OcrLine],
    dict[str, dict[str, int]],
    dict[str, list[OcrLine]],
    str,
    str,
]:
    with Image.open(image_path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        detected, document_type, detection_method = _paddle_block_bounds(image)
        # One detected rectangle is not enough evidence to crop the page.  A
        # false-positive charge header otherwise removes every lower table from
        # Paddle's input, leaving the parser no chance to recover usage data.
        if len(detected) == 1:
            detected = {}
            detection_method = "partial_block_fallback"
        if not detected:
            lines = recognize_with_paddleocr(
                image_path,
                language=language,
                score_threshold=score_threshold,
                ocr_engine=engine,
            )
            return lines, {}, {}, document_type, detection_method

        lines: list[OcrLine] = []
        words_by_block: dict[str, list[OcrLine]] = {}
        with TemporaryDirectory(prefix="taipower_paddle_blocks_") as temp_dir:
            for block_key, (left, top, right, bottom) in detected.items():
                title = _BLOCK_TITLES[block_key]
                lines.append(
                    OcrLine(
                        title,
                        left,
                        top,
                        right - left,
                        max(1, round((bottom - top) * 0.04)),
                        block_key,
                    )
                )
                crop = image.crop((left, top, right, bottom))
                crop = ImageEnhance.Contrast(
                    ImageOps.autocontrast(crop.convert("RGB"))
                ).enhance(1.15)
                crop_path = Path(temp_dir) / f"{block_key}.png"
                crop.save(crop_path)
                recognized = recognize_with_paddleocr(
                    crop_path,
                    language=language,
                    score_threshold=score_threshold,
                    ocr_engine=engine,
                )
                mapped_lines = [
                    OcrLine(
                        line.text,
                        left + line.x,
                        top + line.y,
                        line.width,
                        line.height,
                        block_key,
                    )
                    for line in recognized
                ]
                lines.extend(mapped_lines)
                words_by_block[block_key] = mapped_lines

    bounds = {
        key: {
            "x": left,
            "y": top,
            "width": right - left,
            "height": bottom - top,
        }
        for key, (left, top, right, bottom) in detected.items()
    }
    return lines, bounds, words_by_block, document_type, detection_method


# Labels whose value sits to their right on the same row. When Paddle's text
# detector skips that value, OCR-ing just the row again usually finds it.
_RETRY_LABELS = (
    "基本電費",
    "流動電費",
    "功率因數調整費",
    "本期預繳",
    "稅前應繳總金額",
    "應繳總金額",
    "計費期間",
    "尖峰度數",
    "半尖峰度數",
    "週六半尖峰度數",
    "離峰度數",
)
# The payment strip prints values below their labels, so it is not retried.
_RETRY_BLOCKS = {"user_info", "charge_info", "energy_usage", None}


def _compact_label(text: str) -> str:
    return re.sub(r"[\s:：()（）]", "", text).replace("缴", "繳")


def _labels_missing_values(lines: Sequence[OcrLine]) -> list[OcrLine]:
    """Known label lines with no number printed to their right."""
    missing = []
    for label in lines:
        compact = _compact_label(label.text)
        alias = next((a for a in _RETRY_LABELS if compact.startswith(a)), None)
        if alias is None or label.block_key not in _RETRY_BLOCKS:
            continue
        if re.search(r"\d", compact[len(alias):]):
            continue  # value printed inline with the label
        center = label.y + label.height / 2
        has_value = any(
            other is not label
            and re.search(r"\d", other.text)
            and other.x >= label.x + label.width * 0.5
            and abs(other.y + other.height / 2 - center)
            <= max(label.height, other.height) * 0.6
            for other in lines
        )
        if not has_value:
            missing.append(label)
    return missing


def _retry_missing_rows(
    image: Image.Image,
    lines: list[OcrLine],
    bounds: Mapping[str, Mapping[str, int]],
    *,
    engine: Any,
    score_threshold: float,
) -> list[str]:
    """Re-OCR rows whose value was not detected; returns the recovered labels.

    Detection misses depend on the input image (colour and grayscale each miss
    different values), so the row is tried in colour first, then grayscale.
    New lines are appended to ``lines`` in original-image coordinates.
    """
    import numpy as np

    recovered = []
    for label in _labels_missing_values(lines):
        block = bounds.get(label.block_key) if label.block_key else None
        right = block["x"] + block["width"] if block else image.width
        left = max(0, round(label.x) - 4)
        top = max(0, round(label.y) - 6)
        box = (left, top, min(image.width, right), min(image.height, round(label.y + label.height) + 6))
        if box[2] - box[0] < 10 or box[3] - box[1] < 10:
            continue
        row = image.crop(box)
        for variant in (row, ImageOps.grayscale(row).convert("RGB")):
            # Paddle reads image files as BGR; pass arrays the same way.
            bgr = np.ascontiguousarray(np.asarray(variant)[:, :, ::-1])
            values = [
                line
                for line in _iter_paddle_lines(engine.predict(bgr), score_threshold)
                if re.search(r"\d", line.text)
                and left + line.x >= label.x + label.width * 0.5
            ]
            if values:
                lines.extend(
                    OcrLine(
                        line.text,
                        left + line.x,
                        top + line.y,
                        line.width,
                        line.height,
                        label.block_key,
                    )
                    for line in values
                )
                recovered.append(label.text)
                break
    return recovered


def extract_bill_with_paddleocr(
    image_path: str | Path,
    *,
    language: str = "chinese_cht",
    score_threshold: float = 0.0,
    ocr_engine: Any | None = None,
) -> BillResult:
    """OCR one bill image with PaddleOCR and return the existing BillResult schema."""
    path = Path(image_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    engine = ocr_engine or _create_paddle_engine(language)
    (
        lines,
        bounds,
        words_by_block,
        document_type,
        detection_method,
    ) = _recognize_paddle_blocks(
        path,
        engine=engine,
        language=language,
        score_threshold=score_threshold,
    )
    with Image.open(path) as source:
        page_image = ImageOps.exif_transpose(source).convert("RGB")
    recovered = _retry_missing_rows(
        page_image,
        lines,
        bounds,
        engine=engine,
        score_threshold=score_threshold,
    )
    result = parse_bill_layout(lines)
    result.ocr_lines = lines
    if recovered:
        result["quality"]["paddle_row_retry"] = recovered
    # A layout-specific parser can identify a document more precisely than the
    # coarse, pre-parser detector (which commonly reports photographed detailed
    # bills as ``unknown``).  Preserve that more specific classification.
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
    result["quality"].update(
        {
            "ocr_engine": "paddleocr",
            "ocr_profile": (
                "blockwise_high_accuracy" if bounds else "full_page_fallback"
            ),
            "score_threshold": score_threshold,
            "recognized_line_count": len(lines),
        }
    )
    return result


def extract_pdf_with_paddleocr(
    pdf_path: str | Path,
    *,
    page_numbers: Sequence[int] | None = None,
    dpi: int = 250,
    language: str = "chinese_cht",
    score_threshold: float = 0.0,
    ocr_engine: Any | None = None,
) -> list[BillResult]:
    """Render image-PDF pages and OCR them while reusing one PaddleOCR model."""
    import pdfplumber

    path = Path(pdf_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if dpi <= 0:
        raise ValueError("dpi must be greater than zero")

    engine = ocr_engine or _create_paddle_engine(language)
    bills: list[BillResult] = []
    with pdfplumber.open(path) as pdf, TemporaryDirectory(
        prefix="taipower_paddle_pdf_"
    ) as temp_dir:
        selected = list(page_numbers) if page_numbers is not None else list(
            range(1, len(pdf.pages) + 1)
        )
        for page_number in selected:
            if page_number < 1 or page_number > len(pdf.pages):
                raise ValueError(f"page number out of range: {page_number}")
            rendered = pdf.pages[page_number - 1].to_image(
                resolution=dpi, antialias=True
            )
            image_path = Path(temp_dir) / f"page-{page_number}.png"
            rendered.original.save(image_path)
            bill = extract_bill_with_paddleocr(
                image_path,
                language=language,
                score_threshold=score_threshold,
                ocr_engine=engine,
            )
            bill["quality"].update(
                {
                    "source": "image_pdf",
                    "page_number": page_number,
                    "dpi": dpi,
                }
            )
            bills.append(bill)
    return bills
