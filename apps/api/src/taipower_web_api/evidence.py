"""Evidence marks: highlight values on the bill and label them in red.

Marks are stored in the page image's pixel coordinates (the image OCR read,
rendered at the OCR DPI). They are drawn onto that image for PNG output and,
for PDF uploads, overlaid on the original PDF page so its text stays vector.
"""

from __future__ import annotations

import io
import re
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from PIL import Image, ImageDraw, ImageFont

from taipower_bill_ocr.field_locator import FieldLocation

HIGHLIGHT_RGB = (255, 235, 59)  # highlighter yellow
HIGHLIGHT_ALPHA = 0.40
LABEL_RGB = (211, 47, 47)  # red

# Plain bold fonts that ship with Windows: Arial for Latin text, and
# Microsoft JhengHei when a label has Chinese characters (Arial has none).
_LATIN_FONTS = [Path("C:/Windows/Fonts/arialbd.ttf"), Path("C:/Windows/Fonts/segoeuib.ttf")]
_CJK_FONTS = [Path("C:/Windows/Fonts/msjhbd.ttc")]

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


def label_font_path(text: str = "") -> Path | None:
    """The bold font used for ``text``; CJK text needs a font with CJK glyphs."""
    preferred = _CJK_FONTS + _LATIN_FONTS if re.search(r"[㐀-鿿]", text) else _LATIN_FONTS + _CJK_FONTS
    return next((path for path in preferred if path.exists()), None)


def _pil_font(size: int, text: str = "") -> ImageFont.ImageFont:
    path = label_font_path(text)
    if path is not None:
        return ImageFont.truetype(str(path), size, index=0)
    return ImageFont.load_default(size=size)


def label_size(text: str, font_size: float) -> tuple[float, float]:
    """Width and height a red label takes, in image pixels."""
    font = _pil_font(max(1, round(font_size)), text)
    left, top, right, bottom = font.getbbox(text)
    return right - left, bottom - top


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


def default_marks(
    locations: Mapping[str, FieldLocation | Mapping[str, Any]],
    image_size: tuple[int, int],
    row: Mapping[str, Any] | None = None,
    printed: list[tuple[float, float, float, float]] | None = None,
    regions: list[tuple[float, float, float, float]] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Initial highlights and red labels from where the fields were read.

    Only fields with a value on this bill are marked (residential bills have
    no pre-tax line, so the total is used). When just the surrounding block
    was found, a small highlight is placed at its top for the reviewer to
    drag onto the value, rather than painting the whole block.
    Returns the marks and notes for the reviewer.
    """
    width, height = image_size

    def unpack(location: FieldLocation | Mapping[str, Any]) -> tuple[tuple[float, ...], bool]:
        if isinstance(location, FieldLocation):
            return tuple(location.box), location.exact
        return tuple(location["box"]), bool(location["exact"])

    marks: list[dict[str, Any]] = []
    notes: list[str] = []
    # Printed text (OCR line boxes) and marks already placed: labels avoid both.
    taken: list[tuple[float, ...]] = list(printed or [])
    for target in EVIDENCE_TARGETS:
        candidates = [
            (column, *unpack(locations[column]))
            for column in target["columns"]
            if column in locations and (row is None or row.get(column) is not None)
        ]
        chosen = next((candidate for candidate in candidates if candidate[2]), None) or next(iter(candidates), None)
        if chosen is None:
            notes.append(f"{target['label']}: position not found, add the mark by hand")
            continue
        column, box, exact = chosen
        if column != target["columns"][0]:
            notes.append(f"{target['label']}: no pre-tax line on this bill, marked {column} instead")
        x, y, w, h = box
        if not exact:
            notes.append(f"{target['label']}: exact line not found, drag the highlight onto {column}")
            y += min(80.0, h * 0.06)  # below the block's title bar, where it is visible
            h = max(24.0, height * 0.02)
            w = min(w, width * 0.35)
        group = _new_id()
        marks.append(
            {"id": _new_id(), "type": "highlight", "group": group, "x": x, "y": y, "w": w, "h": h}
        )
        font_size = max(28.0, min(h * 1.1, 60.0))
        text_w, text_h = label_size(target["label"], font_size)
        label_x, label_y = _label_position((x, y, w, h), (text_w, text_h), (width, height), taken, regions or [])
        taken += [(x, y, w, h), (label_x, label_y, text_w, text_h)]
        marks.append(
            {
                "id": _new_id(), "type": "label", "group": group, "text": target["label"],
                "x": label_x, "y": label_y, "fontSize": font_size,
            }
        )
    return marks, notes


def _overlap(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    width = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    height = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return width * height if width > 0 and height > 0 else 0.0


def _label_position(
    box: tuple[float, float, float, float],
    text: tuple[float, float],
    page: tuple[int, int],
    obstacles: list[tuple[float, ...]],
    regions: Sequence[tuple[float, ...]] = (),
) -> tuple[float, float]:
    """Where a red label covers the least printed text, close to its highlight.

    Tries beside, above and below the highlight (one to three lines away),
    scoring each by the printed text it would hide, the part of it that
    sticks out of a block it partly overlaps (so it never straddles a block
    border) and whether it falls outside every block (gaps hold borders and
    graphics OCR cannot see), then by the gap to its highlight. Block
    detection is approximate on some layouts, so any block will do.
    """
    x, y, w, h = box
    text_w, text_h = text
    gap = 12
    step = text_h + 8
    columns = [x + w + 16, x, x + w - text_w, x - text_w - 16]
    rows = [y + (h - text_h) / 2] + [y - gap - text_h - step * n for n in range(3)] + [y + h + gap + step * n for n in range(3)]
    best: tuple[float, float, float, float] | None = None
    for left in columns:
        for top in rows:
            if left < 0 or top < 0 or left + text_w > page[0] or top + text_h > page[1]:
                continue
            spot = (left, top, text_w, text_h)
            if _overlap(spot, box):
                continue  # never on top of its own highlight
            hidden = sum(_overlap(spot, obstacle) for obstacle in obstacles)
            area = text_w * text_h
            inside_any = False
            for region in regions:
                inside = _overlap(spot, region)
                if 0 < inside < area:
                    hidden += area - inside
                inside_any = inside_any or inside >= area
            # Gaps between blocks hold borders and graphics OCR does not see.
            if regions and not inside_any:
                hidden += area
            # Gap between the label and its highlight, edge to edge.
            distance = max(0.0, left - (x + w), x - (left + text_w)) + max(0.0, top - (y + h), y - (top + text_h))
            score = (hidden, distance)
            if best is None or score < best[:2]:
                best = (hidden, distance, left, top)
    if best is None:
        return max(0, min(x, page[0] - text_w)), max(0, y - text_h - gap)
    return best[2], best[3]


def draw_png(image: Image.Image, marks: list[Mapping[str, Any]]) -> Image.Image:
    """The page image with highlights and red labels drawn on it."""
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
            font = _pil_font(max(1, round(mark["fontSize"])), mark["text"])
            left, top, _, _ = font.getbbox(mark["text"])
            draw.text((mark["x"] - left, mark["y"] - top), mark["text"], font=font, fill=LABEL_RGB)
    return combined.convert("RGB")


def png_bytes(image: Image.Image, marks: list[Mapping[str, Any]]) -> bytes:
    buffer = io.BytesIO()
    draw_png(image, marks).save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _register_font(text: str) -> str:
    """The reportlab font name for ``text``, registering the TTF on first use."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    path = label_font_path(text)
    if path is None:
        return "Helvetica-Bold"
    name = f"EvidenceLabel-{path.stem}"
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(name, str(path), subfontIndex=0))
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
    pdf.setFillColorRGB(*(channel / 255 for channel in LABEL_RGB), alpha=1)
    for mark in marks:
        if mark["type"] == "label":
            size = mark["fontSize"] * scale
            pil_font = _pil_font(max(1, round(mark["fontSize"])), mark["text"])
            _, top, _, bottom_px = pil_font.getbbox(mark["text"])
            pdf.setFont(_register_font(mark["text"]), size)
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
    # Embed as JPEG: a scanned or photographed page is several times smaller
    # than lossless, with no visible difference at this quality.
    jpeg = io.BytesIO()
    marked.save(jpeg, format="JPEG", quality=90, optimize=True)
    jpeg.seek(0)
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(width, height))
    pdf.drawImage(ImageReader(jpeg), 0, 0, width, height)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
