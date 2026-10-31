"""Synthetic bill pages with every line of text boxed, to train the text detector.

No real bill is drawn or copied: the layouts are random arrangements of what bills
are made of (banners, panels, tables, tight rows, wide-spaced figures, barcodes,
QR codes, watermarks, highlighter, redaction), then photographed badly. The clean
pages of synth_pages.py taught a detector nothing: it missed none of their lines.

    python training/synth_det.py out_folder 12    # pages with their boxes drawn on
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
import synth  # noqa: E402

WIDTH, HEIGHT = 1654, 2339
SHRINK = 0.6  # the target region is the line shrunk to this share of its area, as DBNet's formula has it
TOO_SMALL = 5  # pixels high on the final image: nobody could read it, so neither right nor wrong

_DIR = Path("C:/Windows/Fonts")
_CJK = [(_DIR / name, weight) for name, weight in (("msjh.ttc", 8), ("msjhbd.ttc", 3), ("msjhl.ttc", 1), ("mingliu.ttc", 5),
                                                   ("mingliub.ttc", 1), ("kaiu.ttf", 1), ("simsun.ttc", 1), ("msyh.ttc", 1)) if (_DIR / name).exists()]
_LATIN = [(_DIR / name, 1) for name in ("arial.ttf", "arialbd.ttf", "calibri.ttf", "tahoma.ttf", "consola.ttf", "times.ttf", "cour.ttf", "segoeui.ttf") if (_DIR / name).exists()]
_CACHE: dict[tuple[Path, int], ImageFont.FreeTypeFont] = {}

# Characters of the notices, addresses and names that are not in the bill's own vocabulary.
_WORDS = (
    "逾繳費期限第天起至第天者按應付電費計收遲付費用未滿元者將併於下次電費中收取前得向代收單位繳費如欲採金融機構電話語音網際網路自動櫃員機"
    "辦理之金融機構可參考本公司網站或向服務單位洽詢依下列標示項目操作逾此截止日假日不順延請駕臨本公司各區營業處服務中心所新北市台北市台中市"
    "高雄市桃園市台南市基隆市新竹縣彰化縣屏東縣區鄉鎮路街段巷弄號樓之地下層至地上財團法人大學股份有限公司工業科技電子製造醫院學校社區管理委員會"
    "王陳林黃張李吳劉蔡楊許鄭謝郭洪邱曾廖賴徐周葉蘇莊呂江何蕭羅高潘簡朱鍾彭游詹胡施沈余趙盧梁顏柯孫魏翁戴范宋方鄧杜傅侯曹程溫田康姜"
    "本單僅作通知用付款時當另給繳費憑證其他事項請參閱背面說明各項金額數字係由機器印出如發現非機器列印或有塗改字跡無收人蓋章者概屬無效營業稅已併入"
    "聯為雲端發票及載具資訊妥善保存以利兌獎勿撕下行動支付掃描使用銀行電力排碳量每度繳交再生基金節約用電減少地球暖化衝擊費用戶期可比省比例較去年"
)
_ENGLISH = ("Taiwan Power Company", "Electricity Bill", "High Voltage", "Payment Receipt", "www.taipower.com.tw", "Basic Info.", "Charge Info.",
            "Other Info.", "Invoice", "Carrier", "Receipt Stamp", "Year", "Month", "Customer Number", "Due Date", "Total Amount", "Payment Info.")
_PALE = [(214, 240, 236), (232, 244, 250), (250, 240, 228), (240, 232, 248), (238, 246, 226), (255, 250, 225), (245, 245, 245)]
_BANNERS = [(187, 150, 220), (190, 130, 160), (70, 160, 225), (240, 130, 170), (40, 120, 200), (90, 170, 120), (235, 150, 70), (150, 150, 150),
            (205, 170, 190), (170, 205, 235), (210, 190, 225)]  # a faded print: white on these is hard to see
_INK = [(20, 20, 20), (35, 35, 35), (50, 50, 60), (20, 40, 90), (70, 70, 70)]


def _font(path: Path, size: float) -> ImageFont.FreeTypeFont:
    key = (path, max(8, int(size)))
    if key not in _CACHE:
        _CACHE[key] = ImageFont.truetype(str(path), key[1], index=0)
    return _CACHE[key]


def _sentence(rng: random.Random, low: int = 4, high: int = 24) -> str:
    length = rng.randint(low, high)
    start = rng.randrange(len(_WORDS) - length)
    text = _WORDS[start:start + length] if rng.random() < 0.6 else "".join(rng.choice(_WORDS) for _ in range(length))
    if rng.random() < 0.3:
        at = rng.randrange(len(text))
        text = text[:at] + rng.choice(["，", "：", "（", "）", "、", "。", "/"]) + text[at:]
    return text


def _label(rng: random.Random) -> str:
    choice = rng.random()
    if choice < 0.72:
        text = rng.choice(synth.LABELS)
        if rng.random() < 0.4:
            text = text.replace("(", "（").replace(")", "）")
        return text + (rng.choice(["：", ":"]) if rng.random() < 0.2 else "")
    if choice < 0.92:
        return _sentence(rng, 2, 9)
    return rng.choice(_ENGLISH)


def _value(rng: random.Random) -> str:
    choice = rng.random()
    if choice < 0.38:
        text = synth._money(rng)
    elif choice < 0.7:
        text = synth._kwh(rng)
    elif choice < 0.8:
        text = synth._date(rng)
    elif choice < 0.85:
        text = synth._period(rng)
    elif choice < 0.9:
        text = synth._meter(rng)
    elif choice < 0.95:
        text = synth._small(rng)
    else:
        return _sentence(rng, 2, 8)
    if rng.random() < 0.05:  # printed wide apart, as bills do with figures in full-width characters
        text = " ".join(text)
    return text


class Canvas:
    """A page that remembers where each line of text went, as (x0, y0, x1, y1)."""

    def __init__(self, rng: random.Random, paper: tuple[int, int, int]) -> None:
        self.rng = rng
        self.page = Image.new("RGB", (WIDTH, HEIGHT), paper)
        self.draw = ImageDraw.Draw(self.page)
        self.lines: list[tuple[float, float, float, float]] = []
        self.ignored: list[tuple[float, float, float, float]] = []
        self.main = rng.choices([f[0] for f in _CJK], [f[1] for f in _CJK])[0]
        self.ink = rng.choice(_INK)

    def font_for(self, text: str, bold: bool = False) -> Path:
        if bold:
            return next((f[0] for f in _CJK if "bd" in f[0].name), self.main)
        if text.isascii() and _LATIN and self.rng.random() < 0.3:
            return self.rng.choice(_LATIN)[0]
        return self.main

    def text(self, x: float, y: float, text: str, size: float, *, right: bool = False, colour=None, bold: bool = False) -> tuple[float, float, float, float]:
        font = _font(self.font_for(text, bold), size)
        if right:
            x -= font.getlength(text)
        box = self.draw.textbbox((x, y), text, font=font)
        self.draw.text((x, y), text, font=font, fill=colour or self.ink)
        self.lines.append(box)
        return box

    def run(self, x: float, y: float, parts: list[tuple[str, float]], colour=None, bold: bool = False) -> None:
        """Pieces at different sizes on one baseline ("電號 Customer Number"), boxed as one line."""
        boxes = []
        biggest = max(size for _, size in parts)
        for text, size in parts:
            font = _font(self.font_for(text, bold), size)
            top = y + (biggest - size) * 0.8
            boxes.append(self.draw.textbbox((x, top), text, font=font))
            self.draw.text((x, top), text, font=font, fill=colour or self.ink)
            x = boxes[-1][2] + size * 0.35
        self.lines.append((min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)))


def _barcode(c: Canvas, x: float, y: float, width: float, height: float) -> None:
    at = x
    while at < x + width:
        bar = c.rng.choice([1.5, 2, 3, 4, 6])
        c.draw.rectangle([at, y, at + bar, y + height], fill=(10, 10, 10))
        at += bar + c.rng.choice([1.5, 2, 3, 5])


def _qr(c: Canvas, x: float, y: float, size: float) -> None:
    cells = c.rng.choice([25, 29, 33])
    cell = size / cells
    for row in range(cells):
        for column in range(cells):
            corner = (row < 7 or row >= cells - 7) and column < 7 or row < 7 and column >= cells - 7
            if not corner and c.rng.random() < 0.5:
                c.draw.rectangle([x + column * cell, y + row * cell, x + (column + 1) * cell, y + (row + 1) * cell], fill=(10, 10, 10))
    for cx, cy in ((x, y), (x + (cells - 7) * cell, y), (x, y + (cells - 7) * cell)):
        c.draw.rectangle([cx, cy, cx + 7 * cell, cy + 7 * cell], outline=(10, 10, 10), width=max(1, int(cell)))
        c.draw.rectangle([cx + 2 * cell, cy + 2 * cell, cx + 5 * cell, cy + 5 * cell], fill=(10, 10, 10))


def _logo(c: Canvas, x: float, y: float, size: float, colour) -> None:
    c.draw.ellipse([x, y, x + size, y + size], outline=colour, width=max(2, int(size / 12)))
    c.draw.polygon([(x + size * .5, y + size * .15), (x + size * .25, y + size * .8), (x + size * .75, y + size * .8)], outline=colour, width=max(2, int(size / 16)))
    c.draw.line([(x + size * .3, y + size * .55), (x + size * .7, y + size * .55)], fill=colour, width=max(2, int(size / 20)))


def _rows(c: Canvas, x0: float, x1: float, y: float, bottom: float, size: float, spacing: float) -> float:
    """Label on the left, figure on the right, as tight as bills print them."""
    rng = c.rng
    while y + size * 1.2 < bottom:
        kind = rng.random()
        indent = size if rng.random() < 0.25 else 0
        if kind < 0.72:
            label, value = _label(rng), _value(rng)
            font = _font(c.main, size)
            # Bills never print a figure over its label: the gap between them is what parts two lines.
            if font.getlength(label) + font.getlength(value) * 1.15 + indent + size * 1.5 < x1 - x0:
                c.text(x0 + indent, y, label, size)
                c.text(x1, y, value, size, right=True, bold=rng.random() < 0.12)
            elif font.getlength(label) + indent < x1 - x0:
                c.text(x0 + indent, y, label, size)
        elif kind < 0.88:
            label = _label(rng)
            if _font(c.main, size).getlength(label) + indent < x1 - x0:
                c.text(x0 + indent, y, label, size)
        elif kind < 0.97:  # an empty row or a gap between groups
            pass
        else:
            c.draw.line([(x0, y + size * 0.6), (x1, y + size * 0.6)], fill=c.ink, width=2)
        y += size * spacing
    return y


def _paragraph(c: Canvas, x0: float, x1: float, y: float, size: float, spacing: float, lines: int) -> float:
    per_line = max(4, int((x1 - x0) / max(size, 1)) - 1)
    for index in range(lines):
        c.text(x0 + (size if index == 0 else 0), y, _sentence(c.rng, max(3, per_line - 4), per_line), size)
        y += size * spacing
    return y


def _banner(c: Canvas, x0: float, y: float, x1: float, colour, size: float) -> float:
    height = size * c.rng.uniform(1.35, 1.8)
    c.draw.rectangle([x0, y, x1, y + height], fill=colour)
    white = (255, 255, 255)
    words = c.rng.choice(synth.LABELS).split(" ")
    room = x1 - x0 - size
    while _font(c.main, size).getlength(" ".join(words)) * 1.1 > room and (len(words) > 1 or len(words[0]) > 2):
        words = words[:-1] if len(words) > 1 else [words[0][:len(words[0]) // 2 + 1]]
    if len(words) > 1 and c.rng.random() < 0.8:
        c.run(x0 + size * 0.4, y + size * 0.1, [(words[0], size), (" ".join(words[1:]), size * c.rng.uniform(0.65, 0.9))], colour=white, bold=c.rng.random() < 0.4)
    else:
        c.text(x0 + size * 0.4, y + size * 0.1, words[0], size, colour=white, bold=c.rng.random() < 0.4)
    return y + height


def _modern(c: Canvas) -> None:
    """A heading band, a row of small boxes, panels of tight rows, barcodes at the foot."""
    rng = c.rng
    margin = rng.uniform(60, 170)
    left, right = margin, WIDTH - margin * rng.uniform(0.7, 1.1)
    size = rng.uniform(22, 32)
    spacing = rng.uniform(1.02, 1.5)
    colour = rng.choice(_BANNERS)
    y = rng.uniform(40, 160)
    if rng.random() < 0.3:
        c.text(left, y, "電子帳單", size * 1.3)
        y += size * 2.2
    band = rng.uniform(100, 170)
    c.draw.rectangle([left, y, right, y + band], fill=colour)
    _logo(c, left + 20, y + band * 0.1, band * 0.8, (255, 255, 255))
    c.run(left + band, y + band * 0.15, [("台灣電力公司", size * 1.4)], colour=(255, 255, 255), bold=True)
    c.text(left + band, y + band * 0.58, "Taiwan Power Company", size * 0.85, colour=(255, 255, 255))
    split = left + (right - left) * rng.uniform(0.38, 0.5)
    if rng.random() < 0.8:
        c.draw.rectangle([split, y, right, y + band], fill=rng.choice(_PALE))
    c.text(split + 20, y + band * 0.12, f"{rng.randint(98, 116)}年{rng.randint(1, 12):02d}月 繳費通知單({rng.choice(['高壓電力用戶', '繳費憑證', '低壓用戶'])})", size * 1.15)
    c.text(split + 20, y + band * 0.55, rng.choice(["Feb. 2024 Electricity Bill (High Voltage)", "Apr. 2024 Electricity Bill (Payment Receipt)"]), size * 1.05)
    y += band + size * 0.6
    for line in range(rng.randint(0, 3)):
        c.text(left + rng.uniform(40, 120), y, _sentence(rng, 6, 20) if line else str(rng.randint(100000, 999999)), size * 1.05)
        y += size * 1.3
    y += rng.uniform(10, 60)
    if rng.random() < 0.7:
        c.text(left + (right - left) * rng.uniform(0.2, 0.4), y, "先生/女士/寶號", size)
        c.text(right, y, f"{rng.choice('gGE')}{rng.randint(10, 99)}JV01 {rng.choice('gG')}{rng.randint(10 ** 12, 10 ** 13 - 1)}", size * 0.95, right=True, bold=True)
        y += size * 1.7
    # The small boxes.
    cuts = sorted(rng.uniform(0.2, 0.8) for _ in range(rng.randint(1, 3)))
    edges = [left, *[left + (right - left) * cut for cut in cuts], right]
    bottom = y
    for x0, x1 in zip(edges, edges[1:]):
        if x1 - x0 < 160:
            continue
        low = _banner(c, x0 + 8, y, x1 - 8, colour, size * rng.uniform(0.95, 1.2))
        c.draw.rectangle([x0 + 8, low, x1 - 8, low + size * 2], outline=colour)
        c.text(x0 + 30, low + size * 0.35, _value(rng), size * rng.uniform(1, 1.2))
        bottom = max(bottom, low + size * 2)
    y = bottom + size * 0.3
    if rng.random() < 0.7:
        c.text(left + rng.uniform(0, 80), y, _sentence(rng, 18, 36), size * 0.8)
    y += size * 1.3
    # The panels.
    foot = HEIGHT - rng.uniform(250, 800)
    cuts = sorted(rng.uniform(0.25, 0.75) for _ in range(rng.choice([0, 1, 2, 2])))
    edges = [left, *[left + (right - left) * cut for cut in cuts], right]
    for x0, x1 in zip(edges, edges[1:]):
        if x1 - x0 < 240:
            continue
        top = _banner(c, x0 + 8, y, x1 - 8, colour, size * rng.uniform(1, 1.2))
        boxed = rng.random() < 0.7
        if boxed:
            c.draw.rectangle([x0 + 8, top, x1 - 8, foot], outline=colour, width=rng.choice([1, 2]))
        stop = foot - size if rng.random() < 0.3 else top + (foot - top) * rng.uniform(0.35, 0.95)
        # Some rows are printed closer than a line's own height: the dates at a panel's foot touch.
        tight = rng.random() < 0.5 and foot - top > size * 8
        if tight:
            stop = min(stop, foot - size * 5)
        end = _rows(c, x0 + 22, x1 - 22, top + size * 0.3, stop, size, spacing if rng.random() < 0.6 else rng.uniform(0.86, 1.02))
        if tight:
            gap = rng.uniform(0.8, 0.98)
            for row in range(rng.randint(2, 4)):
                c.text(x0 + 22, foot - size * (0.4 + gap * (3 - row)), rng.choice(("計費期間：", "本次抄表日：", "下次抄表日：", "本次抄表日/收費日：", "下次抄表日/收費日：")), size)
                c.text(x1 - 22, foot - size * (0.4 + gap * (3 - row)), synth._date(rng) if rng.random() < 0.6 else synth._date(rng) + "至" + synth._date(rng), size * rng.uniform(0.92, 1), right=True)
            end = foot
        if rng.random() < 0.5 and foot - end > size * 5:
            _paragraph(c, x0 + 22, x1 - 22, foot - size * 4.5, size * 0.9, 1.2, 3)
        elif rng.random() < 0.3 and x1 - x0 > 280 and foot - end > 240:
            _qr(c, x0 + 40, end + 20, 180)
    y = foot + size * 0.6
    if rng.random() < 0.7:
        c.text(left + 200, y, "◎" + _sentence(rng, 14, 30) + "◎", size * 0.85, colour=(220, 60, 60), bold=rng.random() < 0.5)
    y += size * 1.6
    # The foot of the page.
    while y + 130 < HEIGHT - 40:
        kind = rng.random()
        if kind < 0.35:
            width = rng.uniform(250, 600)
            x = rng.uniform(left, max(left + 1, right - width - 200))
            _barcode(c, x, y, width, rng.uniform(40, 80))
            c.text(x + rng.uniform(10, 60), y + 85, str(rng.randint(10 ** 10, 10 ** 15)), size * 0.85)
            if rng.random() < 0.6:
                c.text(x + width + 10, y + 10, _label(rng), size * 0.85)
            y += 125
        elif kind < 0.7:
            y = _banner(c, left, y, right, colour, size * 1.1) + 12
        else:
            x = left + rng.uniform(10, 50)
            c.text(x, y, _label(rng), size)
            c.text(x + rng.uniform(180, 330), y, _value(rng), size)
            if rng.random() < 0.4:
                c.text(x + rng.uniform(680, 800), y, _label(rng), size * 0.9)
            y += size * rng.uniform(1.25, 1.6)


def _legacy(c: Canvas) -> None:
    """The older bills: a tinted page, a large pale emblem behind everything, ruled boxes, notices."""
    rng = c.rng
    size = rng.uniform(20, 30)
    spacing = rng.uniform(1.1, 1.5)
    paper = c.page.getpixel((0, 0))
    pale = tuple(max(0, value - rng.randint(10, 26)) for value in paper)
    if rng.random() < 0.8:
        cx, cy, radius = WIDTH * rng.uniform(0.3, 0.7), HEIGHT * rng.uniform(0.35, 0.7), rng.uniform(400, 800)
        for grow in (1, 0.82):
            c.draw.ellipse([cx - radius * grow, cy - radius * grow, cx + radius * grow, cy + radius * grow], outline=pale, width=rng.randint(14, 40))
        for _ in range(rng.randint(2, 6)):
            c.draw.line([(cx + rng.uniform(-1, 1) * radius, cy + rng.uniform(-1, 1) * radius) for _ in range(3)], fill=pale, width=rng.randint(14, 40))
    left, right = rng.uniform(40, 130), WIDTH - rng.uniform(40, 130)
    y = rng.uniform(30, 90)
    _logo(c, left, y, 130, (40, 80, 170))
    c.text(left + 150, y + 15, "台灣電力公司", size * 2, colour=(30, 50, 120), bold=True)
    c.text(left + 150, y + 15 + size * 2.2, "www.taipower.com.tw", size * 1.05, colour=(40, 90, 170))
    c.text(left + 620, y + 15, f"{rng.randint(98, 116)}年 (Year) {rng.randint(1, 12):02d}月 (Month) 電費通知及收據", size * 1.4)
    y += 190
    if rng.random() < 0.7:
        x = right - 750
        c.draw.rectangle([x, y, x + 620, y + 180], outline=c.ink, width=2)
        _paragraph(c, x + 30, x + 590, y + 30, size * 0.8, 1.4, 3)
        _qr(c, x + 640, y, 150)
    y += 220
    c.text(left + rng.uniform(400, 800), y, "先生/女士/寶號", size)
    y += size * 2
    # The ruled box of three.
    box = size * 3.6
    c.draw.rectangle([left, y, left + 1180, y + box], outline=c.ink, width=rng.choice([2, 3, 4]))
    c.draw.line([(left, y + box / 2), (left + 1180, y + box / 2)], fill=c.ink, width=2)
    for edge, name in zip((0, 400, 780), ("電號 (Customer Number)", "繳費期限 (Due Date)", "應繳總金額 (Total Amount)")):
        c.draw.line([(left + edge, y), (left + edge, y + box)], fill=c.ink, width=2)
        c.text(left + edge + 14, y + 5, name, size * 0.95)
        c.text(left + edge + 40, y + box / 2 + 6, _value(rng), size * 1.1)
    y += box + size * 0.5
    y = _paragraph(c, left + 40, left + 1180, y, size, 1.28, rng.randint(3, 7))
    y += size * 0.8
    if rng.random() < 0.8:
        c.draw.rectangle([left + 40, y + size * 1.3, left + 1000, y + size * 2.8], outline=c.ink, width=2)
        for at, name in enumerate(("代收截止日", "電號", "查核碼", "應繳總金額")):
            c.text(left + 90 + at * 240, y, name, size * 0.8)
            c.text(left + 110 + at * 240, y + size * 1.55, _value(rng)[:12], size * 0.95)
        y += size * 3.5
    # Two columns of short rows, a side table.
    middle = left + rng.uniform(560, 700)
    end = HEIGHT - rng.uniform(120, 700)
    _rows(c, left, middle - 40, y, end, size * 0.95, spacing)
    _rows(c, middle, right - rng.uniform(0, 480), y, end, size * 0.9, rng.uniform(1.05, 1.3))
    if rng.random() < 0.7:
        x, top = right - 360, y - size * 18
        c.draw.rectangle([x, top, right - 10, top + size * 14], outline=c.ink, width=2)
        for at in range(1, 4):
            c.draw.line([(x + at * 87, top), (x + at * 87, top + size * 14)], fill=c.ink)
        for at, name in enumerate(("表別", "本期", "上期", "倍數")):
            c.text(x + 8 + at * 87, top + 6, name, size * 0.8)
            c.text(x + 8 + at * 87, top + size * 1.8, f"{rng.randint(0, 99999):05d}"[:rng.randint(2, 5)], size * 0.85)
    y = end
    while y + size < HEIGHT - 30:
        c.text(left + rng.uniform(0, 30), y, _label(rng), size * 0.95)
        c.text(left + rng.uniform(180, 300), y, _value(rng), size * 0.95)
        y += size * rng.uniform(1.25, 1.7)


def _table(c: Canvas) -> None:
    """A grid of figures, the kind of page that comes with bills: text close to the rules."""
    rng = c.rng
    size = rng.uniform(16, 26)
    left, right = rng.uniform(40, 120), WIDTH - rng.uniform(40, 120)
    y = rng.uniform(60, 160)
    c.text(WIDTH / 2 - 300, y, _sentence(rng, 8, 16), size * 1.5, bold=True)
    y += size * 2.5
    while y < HEIGHT - 300:
        columns = rng.randint(3, 9)
        rows = rng.randint(3, 22)
        high = size * rng.uniform(1.25, 1.8)
        wide = (right - left) / columns
        for row in range(rows + 1):
            c.draw.line([(left, y + row * high), (right, y + row * high)], fill=c.ink)
        for column in range(columns + 1):
            c.draw.line([(left + column * wide, y), (left + column * wide, y + rows * high)], fill=c.ink)
        for row in range(rows):
            for column in range(columns):
                if rng.random() < 0.1:
                    continue
                text = _label(rng)[:int(wide / size)] if row == 0 or column == 0 else _value(rng)[:int(wide / size * 1.6)]
                if text.strip():
                    c.text(left + column * wide + 6, y + row * high + (high - size * 1.25) / 2, text, size)
        y += rows * high + rng.uniform(40, 160)


def _marks(c: Canvas) -> None:
    """What people and systems add on top: highlighter, red frames, stamps, redaction."""
    rng = c.rng
    overlay = c.page.convert("RGBA")
    layer = Image.new("RGBA", overlay.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    lines = c.lines
    if lines and rng.random() < 0.35:
        ink = rng.choice([(255, 240, 60, 110), (120, 240, 120, 90), (255, 170, 210, 90)])
        for x0, y0, x1, y1 in rng.sample(lines, min(len(lines), rng.randint(1, 8))):
            draw.rectangle([x0 - 6, y0 - 4, x1 + 6, y1 + 4], fill=ink)
    if lines and rng.random() < 0.35:
        ink = rng.choice([(220, 40, 40, 255), (235, 165, 40, 255)])
        for x0, y0, x1, y1 in rng.sample(lines, min(len(lines), rng.randint(1, 5))):
            draw.rectangle([x0 - 8, y0 - 6, x1 + 8, y1 + 6], outline=ink, width=rng.choice([2, 3]))
    if rng.random() < 0.3:  # a diagonal watermark: not a line of the bill, so neither right nor wrong
        mark = Image.new("RGBA", (1100, 200), (0, 0, 0, 0))
        ImageDraw.Draw(mark).text((10, 10), rng.choice(["僅供參考", "副本", "SAMPLE", "範例", "作廢"]), font=_font(c.main, 150), fill=(150, 150, 150, rng.randint(35, 80)))
        mark = mark.rotate(rng.uniform(-40, 40), expand=True)
        x, y = rng.randint(0, 800), rng.randint(0, 1800)
        layer.alpha_composite(mark, (x, y))
        box = mark.getbbox()
        c.ignored.append((x + box[0], y + box[1], x + box[2], y + box[3]))
    if rng.random() < 0.15:
        x, y, radius = rng.uniform(200, 1400), rng.uniform(300, 2100), rng.uniform(70, 130)
        draw.ellipse([x - radius, y - radius, x + radius, y + radius], outline=(210, 50, 50, 170), width=5)
    c.page = Image.alpha_composite(overlay, layer).convert("RGB")
    c.draw = ImageDraw.Draw(c.page)
    if lines and rng.random() < 0.45:  # blocks over names and addresses: what is under them cannot be read
        hide = rng.choice([(190, 215, 210), (230, 230, 230), (200, 200, 200), (20, 20, 20), None])
        for x0, y0, x1, y1 in rng.sample(lines, min(len(lines), rng.randint(1, 6))):
            if hide is None:
                area = c.page.crop((int(x0), int(y0), int(x1) + 1, int(y1) + 1))
                small = area.resize((max(1, area.width // 14), max(1, area.height // 14)))
                c.page.paste(small.resize(area.size), (int(x0), int(y0)))
            else:
                c.draw.rectangle([x0 - 4, y0 - 3, x1 + 4, y1 + 3], fill=hide)
            c.ignored.append((x0, y0, x1, y1))
        gone = set(c.ignored)
        c.lines = [line for line in lines if line not in gone]


def draw_page(rng: random.Random) -> Canvas:
    style = rng.random()
    if style < 0.55:
        paper = (255, 255, 255) if rng.random() < 0.8 else rng.choice(_PALE)
        c = Canvas(rng, paper)
        _modern(c)
    elif style < 0.85:
        c = Canvas(rng, rng.choice(_PALE))
        _legacy(c)
    else:
        c = Canvas(rng, (255, 255, 255))
        _table(c)
    _marks(c)
    return c


def _rectangle(box: tuple[float, ...]) -> np.ndarray:
    x0, y0, x1, y1 = box
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.float32)


def kernel_of(box: tuple[float, ...]) -> tuple[float, float, float, float]:
    """The line shrunk by DBNet's formula: its middle, which the model learns to find."""
    x0, y0, x1, y1 = box
    width, height = max(x1 - x0, 1), max(y1 - y0, 1)
    distance = width * height * (1 - SHRINK ** 2) / (2 * (width + height))
    return x0 + distance, y0 + distance, x1 - distance, y1 - distance


def _background(rng: random.Random, height: int, width: int) -> np.ndarray:
    base = np.array([rng.randint(60, 235) for _ in range(3)], dtype=np.float32)
    grain = np.random.default_rng(rng.getrandbits(32)).normal(0, rng.uniform(1, 12), (height // 4 + 1, width // 4 + 1, 1))
    grain = cv2.resize(grain, (width, height))[..., None]
    return np.clip(base + grain, 0, 255).astype(np.uint8)


def photograph(page: np.ndarray, boxes: list[tuple[float, ...]], ignored: list[tuple[float, ...]], rng: random.Random) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """The page as a scanner or a phone gave it; where the kernels, unreadable areas and whole lines went."""
    h, w = page.shape[:2]
    hard = max(rng.random(), rng.random())  # from a clean scan to a bad photo, more often bad: clean pages are the easy ones
    edge = rng.uniform(0, 0.09) if rng.random() < 0.6 else 0.0
    corners = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    skew = 0.002 + 0.03 * hard ** 2
    jitter = lambda: rng.uniform(-skew, skew)
    target = np.float32([[w * (edge + jitter()), h * (edge + jitter())], [w * (1 - edge + jitter()), h * (edge + jitter())],
                         [w * (1 - edge + jitter()), h * (1 - edge + jitter())], [w * (edge + jitter()), h * (1 - edge + jitter())]])
    angle = rng.uniform(-1, 1) * (0.3 + 6 * hard ** 2)
    rotate = np.vstack([cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1), [0, 0, 1]])
    matrix = rotate @ cv2.getPerspectiveTransform(corners, target)
    # Pasted over the desk it lay on; paper corners may also leave the frame.
    desk = _background(rng, h, w)
    out = cv2.warpPerspective(page, matrix, (w, h), flags=cv2.INTER_AREA if rng.random() < 0.5 else cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
    mask = cv2.warpPerspective(np.full((h, w), 255, np.uint8), matrix, (w, h))
    out = np.where(mask[..., None] > 127, out, desk)

    def along(values: list[tuple[float, ...]], kernel: bool) -> np.ndarray:
        if not values:
            return np.zeros((0, 4, 2), dtype=np.float32)
        rectangles = np.stack([_rectangle(kernel_of(box) if kernel else box) for box in values])
        return cv2.perspectiveTransform(rectangles.reshape(1, -1, 2), matrix).reshape(-1, 4, 2)

    kernels, whole, unread = along(boxes, True), along(boxes, False), along(ignored, False)
    out = out.astype(np.float32)
    # Uneven light, a crease, a glare.
    if rng.random() < 0.7:
        low = 1 - rng.uniform(0, 0.45) * max(hard, 0.3)
        ramp = np.linspace(low, 1, w, dtype=np.float32)[None, :, None] if rng.random() < 0.5 else np.linspace(low, 1, h, dtype=np.float32)[:, None, None]
        out *= ramp[:, ::-1] if rng.random() < 0.5 and ramp.shape[1] > 1 else ramp
    if rng.random() < 0.25:
        y = rng.randint(100, h - 100)
        out[y:y + rng.randint(2, 7)] *= rng.uniform(0.75, 0.97)
    if rng.random() < 0.15:
        glare = np.zeros((h // 8, w // 8), dtype=np.float32)
        cv2.circle(glare, (rng.randint(0, w // 8), rng.randint(0, h // 8)), rng.randint(15, 70), 1.0, -1)
        glare = cv2.resize(cv2.GaussianBlur(glare, (0, 0), 12), (w, h))[..., None]
        out = out * (1 - glare * 0.5) + 255 * glare * 0.5
    if rng.random() < 0.4:  # washed out: a faint print or an exposure gone wrong
        out = out * rng.uniform(0.55, 0.95) + rng.uniform(5, 70)
    if rng.random() < 0.3:  # a colour cast
        out *= np.array([rng.uniform(0.85, 1.05) for _ in range(3)], dtype=np.float32)
    out = np.clip(out, 0, 255).astype(np.uint8)
    # A range of sizes, blurred and compressed as far as it would have been.
    # A small photo is looked at twice the size (OnnxOcrEngine._boxes_and_crops): soft, with its compression showing.
    small_photo = rng.random() < 0.2
    scale = rng.uniform(0.2, 0.42) if small_photo else rng.uniform(0.28, 1.35)
    out = cv2.resize(out, (max(64, int(w * scale)), max(64, int(h * scale))), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
    if rng.random() < 0.3 + 0.7 * hard:
        out = cv2.GaussianBlur(out, (0, 0), rng.uniform(0.3, 0.6 + 1.2 * hard))
    if rng.random() < 0.3 + 0.7 * hard:
        noise = np.random.default_rng(rng.getrandbits(32)).normal(0, rng.uniform(1, 3 + 12 * hard), out.shape)
        out = np.clip(out + noise, 0, 255).astype(np.uint8)
    if rng.random() < 0.15:  # a fax or a photocopy
        grey = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
        out = cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR)
    if rng.random() < 0.7:
        ok, encoded = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, rng.randint(max(25, int(95 - 70 * hard)), 95)])
        out = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if small_photo:
        out = cv2.resize(out, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    # Lines too small to make out are neither right nor wrong.
    size = np.array([out.shape[1] / w, out.shape[0] / h], dtype=np.float32)
    kernels, whole, unread = kernels * size, whole * size, unread * size
    tall = np.linalg.norm(whole[:, 3] - whole[:, 0], axis=1) if len(whole) else np.zeros(0)
    small = tall < TOO_SMALL
    unread = np.concatenate([unread, whole[small]])
    return out, kernels[~small], unread, whole[~small]


def sample_page(rng: random.Random) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """A photographed page; the kernels; the areas not to learn from; and the whole boxes, to measure with."""
    c = draw_page(rng)
    boxes = [box for box in c.lines if box[3] - box[1] >= 3 and box[2] - box[0] >= 3]
    page = cv2.cvtColor(np.asarray(c.page), cv2.COLOR_RGB2BGR)
    return photograph(page, boxes, c.ignored, rng)


def main() -> None:
    out, count = Path(sys.argv[1]), int(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        rng = random.Random(f"preview-{index}")
        image, kernels, unread, whole = sample_page(rng)
        shown = image.copy()
        cv2.polylines(shown, [box.astype(np.int32) for box in whole], True, (0, 200, 0), 1)
        cv2.polylines(shown, [box.astype(np.int32) for box in kernels], True, (0, 0, 255), 1)
        cv2.polylines(shown, [box.astype(np.int32) for box in unread], True, (255, 0, 0), 1)
        cv2.imwrite(str(out / f"{index:03d}.jpg"), shown)
        print(index, image.shape, len(kernels))


if __name__ == "__main__":
    main()
