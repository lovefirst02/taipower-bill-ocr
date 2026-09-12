"""Offline Taiwan Power bill OCR and structured parsing."""

from .parser import BillResult, OcrLine, parse_bill_layout
from .paddle_ocr import (
    extract_bill_with_paddleocr,
    extract_pdf_with_paddleocr,
    recognize_with_paddleocr,
)
from .pipeline import extract_bill, extract_pdf

__all__ = [
    "BillResult",
    "OcrLine",
    "extract_bill",
    "extract_bill_with_paddleocr",
    "extract_pdf",
    "extract_pdf_with_paddleocr",
    "parse_bill_layout",
    "recognize_with_paddleocr",
]
