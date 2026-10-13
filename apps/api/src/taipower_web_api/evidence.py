"""Evidence marks: highlight values on the bill and label them in red pen.

Marks are stored in the page image's pixel coordinates (the image OCR read,
rendered at the OCR DPI). They are drawn onto that image for PNG output and,
for PDF uploads, overlaid on the original PDF page so its text stays vector.
"""

from __future__ import annotations

import io
import uuid
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, ImageDraw, ImageFont

from taipower_bill_ocr.field_locator import FieldLocation

HIGHLIGHT_RGB = (255, 235, 59)  # highlighter yellow
HIGHLIGHT_ALPHA = 0.40
PEN_RGB = (211, 47, 47)  # red pen

# Handwriting-style fonts that ship with Windows, in order of preference.
_PEN_FONTS = [
    Path("C:/Windows/Fonts/Inkfree.ttf"),
    Path("C:/Windows/Fonts/segoepr.ttf"),
    Path("C:/Windows/Fonts/segoeprb.ttf"),
]

# What to mark, the English label written beside it, and fallbacks.
EVIDENCE_TARGETS = (
    {
        "label": "Billing period",
        "columns": ["Billing Period 計費期間"],
    },
    {
        "label": "Grey Electricity Cost",
        # 稅前應繳總金額 is the pre-tax amount; residential bills do not print
        # it, so the total is marked instead and the reviewer is told.
        "columns": ["稅前應繳總金額", "應繳總金額"],
    },
)


def pen_font_path() -> Path | None:
    return next((path for path in _PEN_FONTS if path.exists()), None)


def _pil_font(size: int) -> ImageFont.ImageFont:
    path = pen_font_path()
    if path is not None:
        return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def label_size(text: str, font_size: float) -> tuple[float, float]:
    """Width and height a red-pen label takes, in image pixels."""
    font = _pil_font(max(1, round(font_size)))
    left, top, right, bottom = font.getbbox(text)
    return right - left, bottom - top


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


def default_marks(
    locations: Mapping[str, FieldLocation | Mapping[str, Any]],
    image_size: tuple[int, int],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Initial highlights and red labels from where the fields were read.

    Returns the marks and notes for the reviewer (e.g. the pre-tax amount is
    not printed, or only the surrounding block was found).
    """
    width, _ = image_size
    marks: list[dict[str, Any]] = []
    notes: list[str] = []
    for target in EVIDENCE_TARGETS:
        column, location = next(
            ((column, locations[column]) for column in target["columns"] if column in locations),
            (None, None),
        )
        if location is None:
            notes.append(f"{target['label']}: position not found, add the mark by hand")
            continue
        box, exact = (
            (location.box, location.exact)
            if isinstance(location, FieldLocation)
            else (tuple(location["box"]), location["exact"])
        )
        if column != target["columns"][0]:
            notes.append(f"{target['label']}: no pre-tax line on this bill, marked {column} instead")
        if not exact:
            notes.append(f"{target['label']}: exact line not found, check the mark")
        x, y, w, h = box
        group = _new_id()
        marks.append(
            {"id": _new_id(), "type": "highlight", "group": group, "x": x, "y": y, "w": w, "h": h}
        )
        font_size = max(28.0, min(h * 1.1, 60.0))
        text_w, text_h = label_size(target["label"], font_size)
        # Beside the highlight when it fits on the page, otherwise above it.
        if x + w + 16 + text_w <= width:
            label_x, label_y = x + w + 16, y + (h - text_h) / 2
        else:
            label_x, label_y = max(0, x + w - text_w), max(0, y - text_h - 12)
        marks.append(
            {
                "id": _new_id(), "type": "label", "group": group, "text": target["label"],
                "x": label_x, "y": label_y, "fontSize": font_size,
            }
        )
    return marks, notes


def draw_png(image: Image.Image, marks: list[Mapping[str, Any]]) -> Image.Image:
    """The page image with highlights and red-pen labels drawn on it."""
    base = image.convert("RGBA")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    alpha = round(255 * HIGHLIGHT_ALPHA)
    for mark in marks:
        if mark["type"] == "highlight":
            draw.rectangle(
                [mark["x"], mark["y"], mark["x"] + mark["w"], mark["y"] + mark["h"]],
                fill=(*HIGHLIGHT_RGB, alpha),
            )
    combined = Image.alpha_composite(base, layer)
    draw = ImageDraw.Draw(combined)
    for mark in marks:
        if mark["type"] == "label":
            font = _pil_font(max(1, round(mark["fontSize"])))
            left, top, _, _ = font.getbbox(mark["text"])
            draw.text((mark["x"] - left, mark["y"] - top), mark["text"], font=font, fill=PEN_RGB)
    return combined.convert("RGB")


def png_bytes(image: Image.Image, marks: list[Mapping[str, Any]]) -> bytes:
    buffer = io.BytesIO()
    draw_png(image, marks).save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _register_pen_font() -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    path = pen_font_path()
    if path is None:
        return "Helvetica-Bold"
    name = "EvidencePen"
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(name, str(path)))
    return name


def _overlay_pdf(page_width: float, page_height: float, scale: float, marks: list[Mapping[str, Any]], offset: tuple[float, float]) -> bytes:
    """A transparent PDF page holding only the marks, in PDF points."""
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(page_width, page_height))
    left, bottom = offset
    pdf.setFillColorRGB(*(channel / 255 for channel in HIGHLIGHT_RGB), alpha=HIGHLIGHT_ALPHA)
    for mark in marks:
        if mark["type"] == "highlight":
            pdf.rect(
                left + mark["x"] * scale,
                bottom + page_height - (mark["y"] + mark["h"]) * scale,
                mark["w"] * scale,
                mark["h"] * scale,
                stroke=0,
                fill=1,
            )
    font = _register_pen_font()
    pdf.setFillColorRGB(*(channel / 255 for channel in PEN_RGB), alpha=1)
    for mark in marks:
        if mark["type"] == "label":
            size = mark["fontSize"] * scale
            pil_font = _pil_font(max(1, round(mark["fontSize"])))
            _, top, _, bottom_px = pil_font.getbbox(mark["text"])
            pdf.setFont(font, size)
            # Place the text baseline so its glyph box matches the PNG output.
            baseline_px = mark["y"] + (bottom_px - top) * 0.8
            pdf.drawString(left + mark["x"] * scale, bottom + page_height - baseline_px * scale, mark["text"])
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def pdf_bytes(
    source: Path,
    page_number: int | None,
    image: Image.Image,
    dpi: int,
    marks: list[Mapping[str, Any]],
) -> bytes:
    """A one-page evidence PDF.

    PDF uploads keep their original (vector) page with the marks overlaid;
    images, and PDF pages that are rotated, embed the marked image instead.
    """
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    if source.suffix.lower() == ".pdf":
        reader = PdfReader(str(source))
        page = reader.pages[(page_number or 1) - 1]
        if not (page.get("/Rotate") or 0) % 360:
            box = page.mediabox
            overlay = PdfReader(
                io.BytesIO(
                    _overlay_pdf(
                        float(box.width), float(box.height), 72 / dpi, marks,
                        (float(box.left), float(box.bottom)),
                    )
                )
            ).pages[0]
            page.merge_page(overlay)
            writer.add_page(page)
            buffer = io.BytesIO()
            writer.write(buffer)
            return buffer.getvalue()

    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    marked = draw_png(image, marks)
    width, height = marked.width * 72 / dpi, marked.height * 72 / dpi
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(width, height))
    pdf.drawImage(ImageReader(marked), 0, 0, width, height)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
