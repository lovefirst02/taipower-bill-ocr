"""Whole synthetic high-voltage bills with transferred kWh, for testing end to end.

Laid out after Taipower's published sample bill; every figure is random and the
charges add up. Writes pages at three qualities and truth.json with the columns
the app should fill in.

    python training/synth_pages.py samples/synth_pages 30
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
from degrade_pages import LEVELS, degrade  # noqa: E402

SCALE = 1654 / 1123  # the sample's coordinates, at 200 dpi A4
FONT, BOLD = "C:/Windows/Fonts/msjh.ttc", "C:/Windows/Fonts/msjhbd.ttc"
KINDS = ("尖峰度數", "半尖峰度數", "週六半尖峰度數", "離峰度數")
BANNERS = [(187, 150, 220), (190, 130, 160), (70, 160, 225), (240, 130, 170)]


def _font(size: float, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(BOLD if bold else FONT, int(size * SCALE))


def _figures(rng: random.Random) -> dict:
    digits = rng.choice([4, 5, 5, 6, 6, 6])
    billed = [rng.randint(0, 10 ** digits) if rng.random() > 0.08 else 0 for _ in KINDS]
    # Transferred kWh are a share of what was used, and come in every size.
    transferred = [rng.randint(0, 10 ** rng.choice([3, 4, 5, 5, 6])) if rng.random() > 0.08 else 0 for _ in KINDS]
    basic = rng.randint(50, 900) * 1000 * 1.0 + rng.choice([0, 0, 0.5])
    energy = round(rng.uniform(5e4, 4e6), 1)
    power = -round(rng.uniform(100, 60000), 1)
    shared = round(rng.uniform(0, 9000), 1)
    pretax = int(round(basic + energy + power + shared))
    tax = round(pretax * 0.05)
    year, month = rng.randint(108, 115), rng.randint(1, 11)
    last = {2: 28, 4: 30, 6: 30, 9: 30, 11: 30}.get(month, 31)
    meter = f"{rng.randint(0, 99):02d}-{rng.randint(0, 99):02d}-{rng.randint(0, 9999):04d}-{rng.randint(0, 99):02d}-{rng.randint(0, 9)}"
    return {
        "meter": meter, "start": f"{year}/{month:02d}/01", "end": f"{year}/{month:02d}/{last}", "due": f"{year}/{month + 1:02d}/20",
        "billed": billed, "transferred": transferred, "basic": basic, "energy": energy, "power": power, "shared": shared,
        "pretax": pretax, "tax": tax, "total": pretax + tax,
    }


# Ways a bill might differ from the published sample, to see how the app copes.
VARIANTS = {
    "order": {"order": (3, 2, 1, 0)},
    "transfer_first": {"transfer_first": True},
    "wording": {"names": ("尖峰用電度數", "半尖峰用電度數", "週六半尖峰用電度數", "離峰用電度數")},
    "heading": {"billed_heading": "用電度數（度）", "transfer_heading": "本期轉供度數（度）"},
    "two_stage": {"order": (0, 1, 3)},
}


def draw_bill(figures: dict, rng: random.Random, variant: dict | None = None) -> Image.Image:
    variant = variant or {}
    order = variant.get("order", (0, 1, 2, 3))
    names = variant.get("names", KINDS)
    page = Image.new("RGB", (1654, 2339), (255, 255, 255))
    draw = ImageDraw.Draw(page)
    colour = rng.choice(BANNERS)
    at = lambda x, y: (x * SCALE, y * SCALE)
    box = lambda x0, y0, x1, y1: [x0 * SCALE, y0 * SCALE, x1 * SCALE, y1 * SCALE]

    def left(x, y, text, size=19, bold=False):
        draw.text(at(x, y), text, font=_font(size, bold), fill=(25, 25, 25))

    def right(x, y, text, size=19, bold=False):
        font = _font(size, bold)
        draw.text((x * SCALE - font.getlength(text), y * SCALE), text, font=font, fill=(25, 25, 25))

    def banner(x0, y0, x1, text):
        draw.rectangle(box(x0, y0, x1, y0 + 38), fill=colour)
        draw.text(at(x0 + 10, y0 + 2), text, font=_font(24, True), fill=(255, 255, 255))

    draw.rectangle(box(30, 43, 1094, 146), fill=colour)
    draw.text(at(145, 65), "台灣電力公司", font=_font(30, True), fill=(255, 255, 255))
    draw.rectangle(box(500, 66, 1094, 146), fill=(240, 232, 248))
    year, month = figures["due"].split("/")[:2]
    left(515, 72, f"{year} 年 {month} 月 繳費通知單(高壓電力用戶)", 24)
    left(515, 108, "Electricity Bill (High Voltage)", 20)

    banner(30, 300, 285, "電號 Customer Number")
    banner(312, 300, 527, "繳費期限 Due Date")
    banner(555, 300, 835, "應繳總金額 Total Amount")
    banner(30, 406, 433, "用戶資訊 Basic Info.")
    banner(460, 406, 835, "計費內容 Charge Info.")
    left(62, 345, figures["meter"], 24)
    left(350, 345, figures["due"].replace("/", " / "), 24)
    right(825, 340, f"***{figures['total']:,} 元", 24)
    draw.rectangle(box(30, 444, 433, 1217), outline=colour)
    draw.rectangle(box(460, 444, 835, 1217), outline=colour)

    y = 447
    for label, value in (("用電種類：", "高壓需量電力非營業用電"), ("用電地址：台北市＊＊＊", ""), ("用戶營利事業統一編號：", "********"),
                         ("契約容量（瓩）", ""), ("  經常（尖峰）契約", str(rng.randint(100, 5000))), ("最高需量（瓩）", ""),
                         ("  經常需量", str(rng.randint(100, 5000)))):
        left(40, y, label)
        if value:
            right(422, y, value)
        y += 23
    def section(heading, values, y):
        left(40, y, heading)
        y += 23
        for index in order:
            left(58, y, names[index])
            right(422, y, str(values[index]))
            y += 23
        return y

    billed = (variant.get("billed_heading", "計費度數（度）/ Energy Consumption(kWh)"), figures["billed"])
    transferred = (variant.get("transfer_heading", "轉供度數（度）"), figures["transferred"])
    first, second = (transferred, billed) if variant.get("transfer_first") else (billed, transferred)
    y = section(*first, y)
    left(40, y, "功率因數（％）")
    right(422, y, str(rng.randint(85, 100)))
    y += 23
    y = section(*second, y)

    y = 447
    for label, value in (("基本電費（約定）", figures["basic"]), ("流動電費", figures["energy"]), ("功率因數調整費", figures["power"]), ("分攤公共電費", figures["shared"])):
        left(471, y, label)
        right(825, y, f"{value:.1f} 元")
        y += 23
    y += 23
    left(471, y, "稅前應繳總金額")
    right(825, y, f"{figures['pretax']:.1f} 元")
    left(471, y + 23, "營業稅")
    right(825, y + 23, f"{figures['tax']:.1f} 元")
    left(471, y + 46, "應繳總金額", 19, True)
    right(825, y + 46, f"{figures['total']:,} 元", 19, True)
    draw.line([at(470, y + 72), at(825, y + 72)], fill=(0, 0, 0))

    # The period sits in one of two ways on real bills.
    if rng.random() < 0.5:
        left(106, 1155, "抄表日")
        left(322, 1155, "計費期間")
        left(40, 1176, "本次")
        left(106, 1176, figures["end"])
        left(322, 1176, figures["start"] + "~")
        left(40, 1198, "下次")
        left(106, 1198, figures["due"])
        left(322, 1198, figures["end"])
    else:
        left(40, 1176, "計費期間：")
        right(422, 1176, f"{figures['start']}至{figures['end']}")
        left(40, 1198, "本次抄表日：")
        right(422, 1198, figures["due"])
    return page


def truth_of(figures: dict, variant: dict | None = None) -> dict:
    row = {"Billing Period 計費期間": f"{figures['start']}~{figures['end']}", "Custom No. 電號": figures["meter"]}
    for kind, value in zip(KINDS, figures["billed"]):
        row[f"計費度數_{kind}"] = value
    for kind, value in zip(KINDS, figures["transferred"]):
        row[f"轉供度數_{kind}"] = value
    for index, kind in enumerate(KINDS):
        if variant and "order" in variant and index not in variant["order"]:
            del row[f"計費度數_{kind}"], row[f"轉供度數_{kind}"]
    row.update({"基本電費": figures["basic"], "流動電費": figures["energy"], "功率因數調整費": figures["power"],
                "稅前應繳總金額": figures["pretax"], "應繳總金額": figures["total"]})
    return row


def main() -> None:
    out, count = Path(sys.argv[1]), int(sys.argv[2])
    # A third argument draws each variant at clean and phone quality instead.
    jobs = [(None, None, ("clean", *LEVELS))] if len(sys.argv) < 4 else [
        (name, variant, ("clean", "phone")) for name, variant in VARIANTS.items()]
    truth = {}
    for name, variant, levels in jobs:
      for index in range(count):
        rng = random.Random(f"bill-{name or ''}{index}")
        figures = _figures(rng)
        page = draw_bill(figures, rng, variant)
        image = cv2.cvtColor(np.asarray(page), cv2.COLOR_RGB2BGR)
        for level in levels:
            folder = out / (f"{name}_{level}" if name else level)
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{index:03d}.jpg"
            result = image if level == "clean" else degrade(image, LEVELS[level], random.Random(f"{index}-{level}"))
            ok, encoded = cv2.imencode(".jpg", result, [cv2.IMWRITE_JPEG_QUALITY, 95])
            encoded.tofile(str(path))
            truth[path.as_posix()] = truth_of(figures, variant)
    (out / "truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
