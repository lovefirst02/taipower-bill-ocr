"""Batch extraction over a folder of bills, reporting progress page by page."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterator

from .excel_export import REVIEW_COLUMN, BillRecord, Issue, bill_to_record, is_charge_page
from .field_locator import FieldLocation, locate_fields
from .parser import BillResult

PDF_SUFFIXES = {".pdf"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
SUPPORTED_SUFFIXES = PDF_SUFFIXES | IMAGE_SUFFIXES
# "crosscheck" keeps PaddleOCR's values and re-reads charge pages with
# Windows OCR to fill gaps and flag disagreements.
OCR_BACKENDS = ("crosscheck", "paddleocr", "windows")
PADDLE_SCORE_THRESHOLD = 0.45


def find_bill_files(folder: str | Path, *, recursive: bool = False) -> list[Path]:
    """Return supported bill files in ``folder``, sorted by path."""
    root = Path(folder)
    candidates = root.rglob("*") if recursive else root.iterdir()
    return sorted(
        path
        for path in candidates
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )


def count_pages(path: Path) -> int:
    if path.suffix.lower() not in PDF_SUFFIXES:
        return 1
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        return len(pdf.pages)


@lru_cache(maxsize=8)
def render_page(path: Path, page_number: int | None, dpi: int) -> Any:
    """Render a bill page exactly as OCR saw it, so OCR coordinates line up."""
    from PIL import Image, ImageOps

    if path.suffix.lower() in PDF_SUFFIXES:
        import pdfplumber

        with pdfplumber.open(path) as pdf:
            rendered = pdf.pages[(page_number or 1) - 1].to_image(
                resolution=dpi, antialias=True
            )
            return rendered.original.convert("RGB")
    with Image.open(path) as source:
        return ImageOps.exif_transpose(source).convert("RGB")


class PageExtractor:
    """Extract one page at a time, loading the PaddleOCR model only once."""

    def __init__(self, ocr_backend: str = "crosscheck", dpi: int = 200) -> None:
        if ocr_backend not in OCR_BACKENDS:
            raise ValueError(f"unsupported OCR backend: {ocr_backend!r}")
        self.ocr_backend = ocr_backend
        self.dpi = dpi
        self._paddle_engine: Any | None = None

    def _engine(self) -> Any:
        if self._paddle_engine is None:
            from .paddle_ocr import _create_paddle_engine

            self._paddle_engine = _create_paddle_engine("chinese_cht")
        return self._paddle_engine

    @property
    def uses_paddle(self) -> bool:
        return self.ocr_backend in {"crosscheck", "paddleocr"}

    @property
    def cross_checks(self) -> bool:
        return self.ocr_backend == "crosscheck"

    def extract(self, path: Path, page_number: int | None) -> BillResult:
        """Read a page with the primary engine."""
        if self.uses_paddle:
            return self._extract_paddle(path, page_number)
        return self._extract_windows(path, page_number)

    def extract_cross_check(self, path: Path, page_number: int | None) -> BillResult:
        """Read a page with the second engine used for cross-checking."""
        return self._extract_windows(path, page_number)

    def _extract_paddle(self, path: Path, page_number: int | None) -> BillResult:
        from .paddle_ocr import extract_bill_with_paddleocr, extract_pdf_with_paddleocr

        is_pdf = path.suffix.lower() in PDF_SUFFIXES
        options = {
            "score_threshold": PADDLE_SCORE_THRESHOLD,
            "ocr_engine": self._engine(),
        }
        if is_pdf:
            return extract_pdf_with_paddleocr(
                path, page_numbers=[page_number], dpi=self.dpi, **options
            )[0]
        return extract_bill_with_paddleocr(path, **options)

    def _extract_windows(self, path: Path, page_number: int | None) -> BillResult:
        from .pipeline import extract_bill, extract_pdf

        is_pdf = path.suffix.lower() in PDF_SUFFIXES
        if is_pdf:
            return extract_pdf(path, page_numbers=[page_number], dpi=self.dpi)[0]
        return extract_bill(path)


@dataclass
class FileStarted:
    path: Path
    page_count: int


@dataclass
class PageDone:
    path: Path
    page_number: int | None
    record: BillRecord | None  # None when the page has no charge breakdown
    locations: dict[str, FieldLocation] = field(default_factory=dict)


@dataclass
class FileFinished:
    path: Path
    error: str | None = None


BatchEvent = FileStarted | PageDone | FileFinished


def _build_record(
    extractor: PageExtractor,
    bill: BillResult,
    path: Path,
    page_number: int | None,
    source: str,
) -> BillRecord:
    if not extractor.cross_checks:
        return bill_to_record(bill, source)
    try:
        cross_check = extractor.extract_cross_check(path, page_number)
    except Exception as exc:  # noqa: BLE001 - the primary result is still usable
        record = bill_to_record(bill, source)
        record.issues.append(
            Issue(
                f"Windows OCR 交叉檢查失敗：{exc}",
                f"Windows OCR cross-check failed: {exc}",
            )
        )
        record.row[REVIEW_COLUMN] = "；".join(issue.zh for issue in record.issues)
        return record
    return bill_to_record(bill, source, cross_check=cross_check)


def iter_batch(
    files: list[Path],
    extractor: PageExtractor,
    cancel: threading.Event | None = None,
    *,
    source_name: Callable[[Path], str] = lambda path: path.name,
    page_numbers: list[int] | None = None,
) -> Iterator[BatchEvent]:
    """Yield progress events while extracting every page of ``files``.

    A failing file is reported through ``FileFinished.error`` and the batch
    continues; ``cancel`` stops the batch between pages.
    """
    for path in files:
        if cancel is not None and cancel.is_set():
            return
        try:
            page_count = count_pages(path)
            yield FileStarted(path, page_count)
            is_pdf = path.suffix.lower() in PDF_SUFFIXES
            selected = range(1, page_count + 1)
            if is_pdf and page_numbers:
                selected = [number for number in page_numbers if 1 <= number <= page_count]
            for page_number in selected:
                if cancel is not None and cancel.is_set():
                    yield FileFinished(path, "Cancelled")
                    return
                bill = extractor.extract(path, page_number if is_pdf else None)
                if is_charge_page(bill):
                    record = _build_record(
                        extractor, bill, path, page_number if is_pdf else None,
                        source_name(path),
                    )
                    locations = locate_fields(bill, record.row)
                else:
                    record, locations = None, {}
                yield PageDone(path, page_number if is_pdf else None, record, locations)
        except Exception as exc:  # noqa: BLE001 - report and keep the batch going
            yield FileFinished(path, f"{type(exc).__name__}: {exc}")
            continue
        yield FileFinished(path)
