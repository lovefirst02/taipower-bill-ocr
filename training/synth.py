"""Synthetic crops of what a Taipower bill prints in its value cells.

Each sample is one text line as an OCR detector would box it: a 電號, a billing
period, a kWh figure or a charge, drawn in the fonts bills use and put through
what scanners, fax machines and phone cameras do to a page. No real bill data.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from taipower_bill_ocr.bill_vocabulary import LABELS, READER_CHARSET, normal  # noqa: E402

# Spaces are not read: "634220.0 元" and "634220.0元" are the same to the parser.
CHARSET = READER_CHARSET
HEIGHT = 48
MAX_WIDTH = 640

_FONT_DIR = Path("C:/Windows/Fonts")
_FONTS = [
    # What Taipower's bills print in, more often than the rest.
    ("msjh.ttc", 6), ("msjhbd.ttc", 4), ("mingliu.ttc", 3), ("kaiu.ttf", 1),
    ("arial.ttf", 3), ("arialbd.ttf", 1), ("calibri.ttf", 1), ("tahoma.ttf", 1),
    ("verdana.ttf", 1), ("consola.ttf", 1), ("cour.ttf", 1), ("times.ttf", 1),
    ("segoeui.ttf", 1), ("simsun.ttc", 1), ("Deng.ttf", 1), ("msyh.ttc", 1),
]
FONTS = [(_FONT_DIR / name, weight) for name, weight in _FONTS if (_FONT_DIR / name).exists()]
# Fonts with 至元年月日: a Latin font's bill still prints these in a Chinese one.
_CJK = [_FONT_DIR / name for name in ("msjh.ttc", "msjhbd.ttc", "mingliu.ttc", "kaiu.ttf", "simsun.ttc", "msyh.ttc") if (_FONT_DIR / name).exists()]


def _kwh(rng: random.Random) -> str:
    digits = rng.choice([1, 2, 3, 3, 4, 4, 5, 5, 5, 6, 6, 6, 7])
    value = rng.randint(0 if digits == 1 else 10 ** (digits - 1), 10 ** digits - 1)
    return f"{value:,}" if rng.random() < 0.3 else str(value)


def _money(rng: random.Random) -> str:
    digits = rng.choice([1, 2, 3, 4, 5, 5, 6, 6, 6, 7, 7, 8])
    value = rng.randint(0 if digits == 1 else 10 ** (digits - 1), 10 ** digits - 1)
    style = rng.random()
    if style < 0.45:
        text = f"{value}.{rng.randint(0, 9)}"
    elif style < 0.75:
        text = f"{value:,}"
    elif style < 0.85:
        text = f"{value:,}.{rng.randint(0, 9)}"
    else:
        text = str(value)
    if rng.random() < 0.18:
        text = "-" + text
    if rng.random() < 0.08:
        text = rng.choice(["***", "* * *", "**"]) + text
    if rng.random() < 0.45:
        text += rng.choice(["元", " 元"])
    return text


def _meter(rng: random.Random) -> str:
    parts = [rng.randint(0, 99), rng.randint(0, 99), rng.randint(0, 9999), rng.randint(0, 99), rng.randint(0, 9)]
    if rng.random() < 0.7:
        return f"{parts[0]:02d}-{parts[1]:02d}-{parts[2]:04d}-{parts[3]:02d}-{parts[4]}"
    return f"{parts[0]:02d}{parts[1]:02d}{parts[2]:04d}{parts[3]:02d}{parts[4]}"


def _date(rng: random.Random) -> str:
    year, month, day = rng.randint(98, 116), rng.randint(1, 12), rng.randint(1, 31)
    style = rng.random()
    if style < 0.75:
        return f"{year}/{month:02d}/{day:02d}"
    if style < 0.85:
        return f"{year}年{month:02d}月{day:02d}日"
    if style < 0.93:
        return f"{year}.{month:02d}.{day:02d}"
    return f"{year + 1911}/{month:02d}/{day:02d}"


def _period(rng: random.Random) -> str:
    style = rng.random()
    if style < 0.55:
        return _date(rng) + rng.choice(["至", "~", "至", "~", "-"]) + _date(rng)
    if style < 0.7:
        return _date(rng) + rng.choice(["~", "至"])
    return _date(rng)


def _small(rng: random.Random) -> str:
    style = rng.random()
    if style < 0.4:
        return f"{rng.uniform(0, 20):.{rng.choice([2, 2, 4])}f}"
    if style < 0.7:
        return f"{rng.uniform(80, 100):.{rng.choice([0, 1])}f}" + rng.choice(["", "%"])
    return str(rng.randint(0, 9999))


_FIELDS = [(_money, 5), (_kwh, 5), (_meter, 2), (_period, 3), (_small, 1)]


def _label(rng: random.Random) -> str:
    label = rng.choice(LABELS)
    if rng.random() < 0.25:
        label = label.replace("(", "（").replace(")", "）")
    style = rng.random()
    if style < 0.15:
        return label + rng.choice(["：", ":"])
    if style < 0.3:  # the label and its figures boxed as one line
        return label + rng.choice(["：", " ", ""]) + rng.choice([_money, _kwh, _period])(rng)
    return label


def sample_text(rng: random.Random) -> str:
    if rng.random() < 0.45:
        return _label(rng)
    field = rng.choices([item[0] for item in _FIELDS], [item[1] for item in _FIELDS])[0]
    return field(rng)


def label_of(text: str) -> str:
    return normal(text)


_GLYPHS: dict[tuple[Path, str], bool] = {}


def _has_glyph(path: Path, font: ImageFont.FreeTypeFont, char: str) -> bool:
    """False for a font's empty box: such a sample would teach the box as the character."""
    key = (path, char)
    if key not in _GLYPHS:
        def drawn(glyph: str) -> bytes:
            image = Image.new("L", (96, 96))
            ImageDraw.Draw(image).text((8, 8), glyph, font=font, fill=255)
            return image.tobytes()

        _GLYPHS[key] = drawn(char) != drawn(chr(0xffff))
    return _GLYPHS[key]


def _draw_text(text: str, rng: random.Random) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """The text on a page-coloured canvas, with whatever lies around a real cell."""
    path = rng.choices([item[0] for item in FONTS], [item[1] for item in FONTS])[0]
    size = rng.randint(26, 58)
    font = ImageFont.truetype(str(path), size, index=0)
    chinese_path = path if path in _CJK else rng.choice(_CJK)
    chinese = font if path in _CJK else ImageFont.truetype(str(chinese_path), size, index=0)
    fallback = ImageFont.truetype(str(_CJK[0]), size, index=0)

    def font_of(char: str) -> ImageFont.FreeTypeFont:
        if char.isascii():
            return font
        return chinese if _has_glyph(chinese_path, chinese, char) else fallback

    tracking = rng.choice([0, 0, 0, 1, 2, 3, -1]) * size / 30
    if rng.random() < 0.1:
        text = text.replace("元", " 元")
    advances = [font_of(char).getlength(char) + tracking for char in text]
    width, height = int(sum(advances)) + 4, int(size * 1.35)
    pad_x, pad_y = int(size * 1.4), int(size * 1.2)

    paper = rng.choice([
        (255, 255, 255), (255, 255, 255), (255, 255, 255), (250, 250, 247), (240, 240, 240),
        (248, 232, 238), (255, 247, 150), (255, 240, 80), (225, 225, 230),  # 粉紅 banners, highlighter
    ])
    canvas = Image.new("RGB", (width + pad_x * 2, height + pad_y * 2), paper)
    draw = ImageDraw.Draw(canvas)
    ink = rng.choice([(0, 0, 0), (0, 0, 0), (0, 0, 0), (30, 30, 30), (60, 60, 60), (95, 95, 95), (20, 30, 90)])
    stroke = 1 if rng.random() < 0.1 else 0
    x = float(pad_x)
    for char, advance in zip(text, advances):
        draw.text((x, pad_y), char, font=font_of(char), fill=ink, stroke_width=stroke, stroke_fill=ink)
        x += advance

    # Lines above and below (bills print them almost touching), and ruled cells.
    if rng.random() < 0.45:
        for offset in (-1, 1):
            if rng.random() < 0.6:
                other = sample_text(rng)
                other = "".join(char for char in other if char.isascii())
                spacing = int(size * rng.uniform(0.82, 1.2))
                draw.text((pad_x + rng.randint(-20, 20), pad_y + offset * spacing), other, font=font, fill=ink)
    if rng.random() < 0.3:
        line = rng.choice([(0, 0, 0), (110, 110, 110), (150, 150, 150)])
        if rng.random() < 0.5:
            y = pad_y + rng.choice([-int(size * 0.1), height - int(size * 0.1)])
            draw.line([(0, y), (canvas.width, y)], fill=line, width=rng.randint(1, 2))
        if rng.random() < 0.5:
            at = pad_x - rng.randint(2, 18)
            draw.line([(at, 0), (at, canvas.height)], fill=line, width=rng.randint(1, 2))
        if rng.random() < 0.5:
            at = pad_x + width + rng.randint(2, 18)
            draw.line([(at, 0), (at, canvas.height)], fill=line, width=rng.randint(1, 2))
    if rng.random() < 0.18:  # a stamp or watermark across the cell
        colour = rng.choice([(200, 170, 80), (190, 120, 120), (170, 170, 170)])
        for _ in range(rng.randint(1, 3)):
            box = [rng.randint(-300, canvas.width), rng.randint(-300, canvas.height)]
            radius = rng.randint(80, 300)
            draw.arc([box[0], box[1], box[0] + radius, box[1] + radius], rng.randint(0, 360), rng.randint(0, 360), fill=colour, width=rng.randint(1, 4))
    box = (pad_x, pad_y, pad_x + width, pad_y + height)
    return np.asarray(canvas).copy(), box


def _warp(image: np.ndarray, box: tuple[int, int, int, int], rng: random.Random) -> np.ndarray:
    """A slant or perspective, then the crop a detector would make."""
    h, w = image.shape[:2]
    left, top, right, bottom = box
    if rng.random() < 0.7:
        tilt = rng.uniform(-2.5, 2.5) if rng.random() < 0.8 else rng.uniform(-6, 6)
        spread = (bottom - top) * rng.uniform(0, 0.12)
        source = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
        drift = np.tan(np.radians(tilt)) * w / 2
        target = np.float32([
            [rng.uniform(-spread, spread), drift + rng.uniform(-spread, spread)],
            [w + rng.uniform(-spread, spread), -drift + rng.uniform(-spread, spread)],
            [w + rng.uniform(-spread, spread), h - drift + rng.uniform(-spread, spread)],
            [rng.uniform(-spread, spread), h + drift + rng.uniform(-spread, spread)],
        ])
        matrix = cv2.getPerspectiveTransform(source, target)
        image = cv2.warpPerspective(image, matrix, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    size = bottom - top
    # Detector boxes are sometimes tight, sometimes generous; never cutting a digit in half.
    left = int(max(0, left - rng.uniform(-0.02, 0.45) * size))
    right = int(min(w, right + rng.uniform(-0.02, 0.45) * size))
    top = int(max(0, top - rng.uniform(-0.1, 0.25) * size))
    bottom = int(min(h, bottom + rng.uniform(-0.1, 0.25) * size))
    return image[top:bottom, left:right]


def _degrade(image: np.ndarray, rng: random.Random) -> np.ndarray:
    """Scanner, fax and camera."""
    h, w = image.shape[:2]
    if rng.random() < 0.25:  # a shadow or uneven light across the page
        ramp = np.linspace(rng.uniform(0.65, 1), rng.uniform(0.65, 1), w, dtype=np.float32)[None, :, None]
        image = np.clip(image * ramp, 0, 255).astype(np.uint8)
    if rng.random() < 0.08:  # moiré of a photographed screen
        ys, xs = np.mgrid[0:h, 0:w]
        wave = 1 + rng.uniform(0.02, 0.1) * np.sin((xs * rng.uniform(0.3, 1.2) + ys * rng.uniform(0.3, 1.2)))
        image = np.clip(image * wave[:, :, None], 0, 255).astype(np.uint8)
    # Small print: the text had only so many pixels (a 200 dpi bill's digit is about 22 px).
    scale = rng.choice([1.0, 0.8, 0.6, 0.5, 0.42, 0.35])
    if scale < 1:
        small = cv2.resize(image, (max(4, int(w * scale)), max(4, int(h * scale))), interpolation=rng.choice([cv2.INTER_AREA, cv2.INTER_LINEAR]))
    else:
        small = image
    if rng.random() < 0.12:  # fax or a black-and-white scan
        grey = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
        # Around the page's own threshold: too far off leaves nothing a person could read.
        otsu, _ = cv2.threshold(grey, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        _, binary = cv2.threshold(grey, min(235, otsu + rng.randint(-20, 30)), 255, cv2.THRESH_BINARY)
        small = cv2.cvtColor(binary, cv2.COLOR_GRAY2RGB)
    if rng.random() < 0.15:  # ink too thin or too heavy
        kernel = np.ones((2, 2), np.uint8)
        small = cv2.erode(small, kernel) if rng.random() < 0.5 else cv2.dilate(small, kernel)
    if rng.random() < 0.35:
        small = cv2.GaussianBlur(small, (0, 0), rng.uniform(0.3, 1.1))
    if rng.random() < 0.4:
        noise = np.random.default_rng(rng.getrandbits(32)).normal(0, rng.uniform(2, 14), small.shape)
        small = np.clip(small + noise, 0, 255).astype(np.uint8)
    if rng.random() < 0.5:
        ok, encoded = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, rng.randint(25, 90)])
        if ok:
            small = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    return small


def fit_height(image: np.ndarray) -> np.ndarray:
    """Grey, HEIGHT tall, scaled to 0..1: what the model reads."""
    if image.ndim == 3:
        image = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    h, w = image.shape[:2]
    width = min(MAX_WIDTH, max(8, round(w * HEIGHT / max(h, 1))))
    resized = cv2.resize(image, (width, HEIGHT), interpolation=cv2.INTER_AREA if h > HEIGHT else cv2.INTER_CUBIC)
    return resized.astype(np.float32) / 255


def sample(rng: random.Random) -> tuple[np.ndarray, str]:
    text = sample_text(rng)
    canvas, box = _draw_text(text, rng)
    crop = _warp(canvas, box, rng)
    return fit_height(_degrade(crop, rng)), label_of(text)


def contact_sheet(path: str | Path, count: int = 60, seed: int = 1) -> None:
    """A page of samples, to look at them."""
    rng = random.Random(seed)
    rows = []
    for _ in range(count):
        image, _ = sample(rng)
        row = np.ones((HEIGHT + 6, 420), dtype=np.float32)
        width = min(image.shape[1], 420)
        row[3:3 + HEIGHT, :width] = image[:, :width]
        rows.append(row)
    columns = [np.vstack(rows[index:index + 20]) for index in range(0, count, 20)]
    sheet = np.hstack(columns)
    Image.fromarray((sheet * 255).astype(np.uint8)).save(path)


if __name__ == "__main__":
    import sys

    contact_sheet(sys.argv[1] if len(sys.argv) > 1 else "sheet.png")
