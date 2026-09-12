from __future__ import annotations

import asyncio
import tempfile
from difflib import SequenceMatcher
from pathlib import Path

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps
from winrt.windows.globalization import Language
from winrt.windows.graphics.imaging import BitmapDecoder
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage import FileAccessMode, StorageFile

from .parser import OcrLine


class WindowsOcrEngine:
    """Use Windows.Media.Ocr locally and preserve each line's bounding box."""

    def __init__(self, language: str = "zh-Hant") -> None:
        self.language = language

    def recognize(
        self, image_path: str | Path, *, profile: str = "accurate"
    ) -> list[OcrLine]:
        return asyncio.run(self.recognize_async(image_path, profile=profile))

    def recognize_blocks(
        self, image_path: str | Path
    ) -> tuple[
        list[OcrLine],
        dict[str, dict[str, int]],
        dict[str, list[OcrLine]],
        str,
        str,
    ]:
        """Detect high-voltage bill blocks, then OCR each crop independently."""
        return asyncio.run(self.recognize_blocks_async(image_path))

    async def recognize_blocks_async(
        self, image_path: str | Path
    ) -> tuple[
        list[OcrLine],
        dict[str, dict[str, int]],
        dict[str, list[OcrLine]],
        str,
        str,
    ]:
        path = Path(image_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)

        engine = OcrEngine.try_create_from_language(Language(self.language))
        if engine is None:
            raise RuntimeError(f"Windows OCR language {self.language!r} is unavailable")
        english_engine = OcrEngine.try_create_from_language(Language("en-US"))

        titles = {
            "payment_info": "繳費資訊 Payment Info.",
            "user_info": "用戶資訊 Basic Info.",
            "charge_info": "計費內容 Charge Info.",
            "other_info": "其他資訊 Other Info.",
            "invoice_info": "前期發票資訊 Invoice",
            "charge_introduction": "電費計收說明 Charge Introduction",
            "energy_saving_info": "節電資訊 Energy Saving Info.",
            "meter_info": "電表資料 Meter Info.",
            "carrier_info": "載具資訊 Carrier",
            "receipt_stamp": "收款章 Receipt Stamp",
        }
        with Image.open(path) as source:
            color_image = ImageOps.exif_transpose(source).convert("RGB")
            document_type = await self._document_type_from_header(
                color_image, engine, english_engine
            )
            if document_type == "bank_autopay_receipt":
                detected = self._bank_autopay_block_bounds(color_image)
                detection_method = "pink_bank_autopay"
            else:
                detected = self._high_voltage_block_bounds(color_image)
                detection_method = "pink_template"
                if not detected:
                    detected = self._high_voltage_back_block_bounds(color_image)
                    if detected:
                        document_type = "high_voltage_bill"
            if not detected:
                detected = await self._color_independent_block_bounds(
                    color_image, engine, english_engine
                )
                detection_method = "color_independent"
            if not detected:
                fallback = await self.recognize_async(
                    path, profile="high_voltage"
                )
                return (
                    fallback,
                    {},
                    {},
                    document_type,
                    "full_page_fallback",
                )

            max_dimension = int(OcrEngine.max_image_dimension)
            lines: list[OcrLine] = []
            words_by_block: dict[str, list[OcrLine]] = {}
            with tempfile.TemporaryDirectory(prefix="taipower_blocks_") as temp_dir:
                for block_key, (left, top, right, bottom) in detected.items():
                    lines.append(
                        OcrLine(
                            titles[block_key],
                            left,
                            top,
                            right - left,
                            max(1, round((bottom - top) * 0.04)),
                            block_key,
                        )
                    )
                    crop = ImageEnhance.Contrast(
                        color_image.crop((left, top, right, bottom)).convert("L")
                    ).enhance(1.45)
                    scale = min(
                        6.0,
                        (max_dimension - 2) / max(crop.width, crop.height),
                    )
                    if scale > 1.0:
                        crop = crop.resize(
                            (round(crop.width * scale), round(crop.height * scale)),
                            Image.Resampling.LANCZOS,
                        )
                    else:
                        scale = 1.0
                    crop_path = Path(temp_dir) / f"{block_key}.png"
                    crop.save(crop_path)
                    crop_engines = [engine]
                    if english_engine is not None:
                        crop_engines.append(english_engine)
                    for crop_engine in crop_engines:
                        recognized, words = await self._recognize_file_with_words(
                            crop_engine, crop_path
                        )
                        for line in recognized:
                            lines.append(
                                OcrLine(
                                    line.text,
                                    left + line.x / scale,
                                    top + line.y / scale,
                                    line.width / scale,
                                    line.height / scale,
                                    block_key,
                                )
                            )
                        block_words = words_by_block.setdefault(block_key, [])
                        for word in words:
                            block_words.append(
                                OcrLine(
                                    word.text,
                                    left + word.x / scale,
                                    top + word.y / scale,
                                    word.width / scale,
                                    word.height / scale,
                                    block_key,
                                )
                            )

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

    async def _document_type_from_header(
        self,
        image: Image.Image,
        engine: OcrEngine,
        english_engine: OcrEngine | None,
    ) -> str:
        """Classify a page before choosing a layout-specific block detector."""
        header = image.crop((0, 0, image.width, round(image.height * 0.24)))
        header = ImageEnhance.Contrast(header.convert("L")).enhance(1.45)
        max_dimension = int(OcrEngine.max_image_dimension)
        scale = min(4.0, (max_dimension - 2) / max(header.width, header.height))
        if scale > 1:
            header = header.resize(
                (round(header.width * scale), round(header.height * scale)),
                Image.Resampling.LANCZOS,
            )
        with tempfile.TemporaryDirectory(prefix="taipower_type_") as temp_dir:
            header_path = Path(temp_dir) / "header.png"
            header.save(header_path)
            lines = await self._recognize_file(engine, header_path)
            if english_engine is not None:
                lines.extend(await self._recognize_file(english_engine, header_path))
        compact = "".join(_line.text for _line in lines)
        compact = "".join(character.lower() for character in compact if character.isalnum())
        if "金融機構代繳" in compact or "paymentreceipt" in compact:
            return "bank_autopay_receipt"
        if "高壓電力用戶" in compact or "highvoltage" in compact:
            return "high_voltage_bill"
        return "electricity_bill"

    async def _color_independent_block_bounds(
        self,
        image: Image.Image,
        engine: OcrEngine,
        english_engine: OcrEngine | None,
    ) -> dict[str, tuple[int, int, int, int]]:
        """Find saturated title bars of any hue, then confirm their OCR titles."""
        regions = [
            region
            for region in self._colored_regions(image)
            if region[2] - region[0] >= image.width * 0.12
            and image.height * 0.008 <= region[3] - region[1] <= image.height * 0.15
        ]
        if not regions:
            return {}

        canonical_to_key = {
            "用戶資訊 Basic Info.": "user_info",
            "計費內容 Charge Info.": "charge_info",
            "繳費資訊 Payment Info.": "payment_info",
            "其他資訊 Other Info.": "other_info",
            "電費計收說明 Charge Introduction": "charge_introduction",
            "節電資訊 Energy Saving Info.": "energy_saving_info",
            "電表資訊 Meter Info.": "meter_info",
        }
        recognized_headers: dict[str, tuple[int, int, int, int]] = {}
        with tempfile.TemporaryDirectory(prefix="taipower_color_headers_") as temp_dir:
            for index, region in enumerate(regions):
                left, top, right, bottom = region
                grayscale = ImageOps.autocontrast(image.crop(region).convert("L"))
                header_lines: list[OcrLine] = []
                for variant_index, header in enumerate(
                    (grayscale, ImageOps.invert(grayscale))
                ):
                    scale = min(
                        10.0,
                        (int(OcrEngine.max_image_dimension) - 2)
                        / max(header.width, header.height),
                    )
                    if scale > 1:
                        header = header.resize(
                            (
                                round(header.width * scale),
                                round(header.height * scale),
                            ),
                            Image.Resampling.LANCZOS,
                        )
                    header_path = (
                        Path(temp_dir) / f"header-{index}-{variant_index}.png"
                    )
                    header.save(header_path)
                    header_lines.extend(
                        await self._recognize_file(engine, header_path)
                    )
                    if english_engine is not None:
                        header_lines.extend(
                            await self._recognize_file(english_engine, header_path)
                        )
                canonical = next(
                    (
                        value
                        for line in header_lines
                        if (value := self._canonical_header(line.text)) is not None
                    ),
                    None,
                )
                block_key = canonical_to_key.get(canonical or "")
                if block_key and block_key not in recognized_headers:
                    recognized_headers[block_key] = region

        if not ({"user_info", "charge_info"} & recognized_headers.keys()):
            return {}

        bounds: dict[str, tuple[int, int, int, int]] = {}
        for block_key, (left, top, right, bottom) in recognized_headers.items():
            lower_headers = [
                candidate
                for candidate in recognized_headers.values()
                if candidate[1] > top
                and min(right, candidate[2]) - max(left, candidate[0])
                >= min(right - left, candidate[2] - candidate[0]) * 0.3
            ]
            content_bottom = (
                min(candidate[1] for candidate in lower_headers)
                if lower_headers
                else round(image.height * 0.98)
            )
            bounds[block_key] = (left, top, right, content_bottom)

        if "payment_info" in bounds:
            left, top, right, bottom = bounds["payment_info"]
            same_row = [
                region
                for region in regions
                if abs(region[1] - top) <= max(5, round(image.height * 0.006))
                and region[0] <= right
            ]
            if same_row:
                bounds["payment_info"] = (
                    min(region[0] for region in same_row), top, right, bottom
                )
        return bounds

    @classmethod
    def _high_voltage_block_bounds(
        cls, image: Image.Image
    ) -> dict[str, tuple[int, int, int, int]]:
        """Locate blocks from the repeated pink title-bar geometry."""
        regions = cls._pink_regions(image)
        tolerance = max(5, round(image.height * 0.006))
        rows: list[list[tuple[int, int, int, int]]] = []
        for region in sorted(regions, key=lambda item: (item[1], item[0])):
            if region[2] - region[0] < image.width * 0.12:
                continue
            row = next(
                (items for items in rows if abs(items[0][1] - region[1]) <= tolerance),
                None,
            )
            if row is None:
                rows.append([region])
            else:
                row.append(region)
                row.sort(key=lambda item: item[0])

        summary = next((row for row in rows if len(row) >= 4), None)
        if summary is None:
            return {}
        main = next(
            (row for row in rows if len(row) == 2 and row[0][1] > summary[0][1]),
            None,
        )
        if main is None:
            return {}
        invoice = next(
            (
                row[0]
                for row in rows
                if len(row) == 1
                and row[0][1] > main[0][1]
                and row[0][0] < image.width * 0.2
            ),
            None,
        )
        content_bottom = invoice[1] if invoice else round(image.height * 0.78)
        other = next(
            (
                row[0]
                for row in rows
                if len(row) == 1
                and main[0][1] < row[0][1] < content_bottom
                and row[0][0] > image.width * 0.6
            ),
            None,
        )

        bounds = {
            "payment_info": (
                summary[0][0],
                summary[0][1],
                summary[2][2],
                main[0][1],
            ),
            "user_info": (main[0][0], main[0][1], main[0][2], content_bottom),
            "charge_info": (main[1][0], main[1][1], main[1][2], content_bottom),
        }
        if other:
            bounds["other_info"] = (other[0], other[1], other[2], content_bottom)
        if invoice:
            bounds["invoice_info"] = (
                invoice[0],
                invoice[1],
                invoice[2],
                round(image.height * 0.94),
            )
        return bounds

    @classmethod
    def _bank_autopay_block_bounds(
        cls, image: Image.Image
    ) -> dict[str, tuple[int, int, int, int]]:
        """Locate blocks on a pink financial-institution payment receipt."""
        regions = cls._pink_regions(image)
        tolerance = max(5, round(image.height * 0.006))
        rows: list[list[tuple[int, int, int, int]]] = []
        for region in sorted(regions, key=lambda item: (item[1], item[0])):
            row = next(
                (items for items in rows if abs(items[0][1] - region[1]) <= tolerance),
                None,
            )
            if row is None:
                rows.append([region])
            else:
                row.append(region)
                row.sort(key=lambda item: item[0])

        summary = next(
            (
                row
                for row in rows
                if len([r for r in row if r[2] - r[0] > image.width * 0.12]) >= 4
                and row[0][1] < image.height * 0.35
            ),
            None,
        )
        content_row = next(
            (
                row
                for row in rows
                if summary is not None
                and row[0][1] > summary[0][1]
                and row[0][1] < image.height * 0.45
                and any(
                    image.width * 0.30 < item[0] < image.width * 0.48
                    and item[2] > image.width * 0.62
                    for item in row
                )
            ),
            None,
        )
        bottom_row = next(
            (
                row
                for row in rows
                if row[0][1] > image.height * 0.70
                and any(item[0] < image.width * 0.05 for item in row)
                and any(item[2] > image.width * 0.80 for item in row)
            ),
            None,
        )
        if summary is None or content_row is None or bottom_row is None:
            return {}

        charge_header = next(
            item
            for item in content_row
            if image.width * 0.30 < item[0] < image.width * 0.48
            and item[2] > image.width * 0.62
        )
        content_top = content_row[0][1]
        content_bottom = bottom_row[0][1]
        user_left = min(item[0] for item in content_row)
        user_right = max(
            item[2] for item in content_row if item[2] < charge_header[0]
        )
        other_headers = [
            item
            for row in rows
            for item in row
            if content_top < item[1] < content_bottom
            and item[0] > image.width * 0.65
        ]
        if not other_headers:
            return {}
        other_header = min(other_headers, key=lambda item: item[1])

        summary_regions = [
            item for item in summary if item[2] - item[0] > image.width * 0.12
        ]
        bounds = {
            "payment_info": (
                summary_regions[0][0],
                summary_regions[0][1],
                summary_regions[2][2],
                content_top,
            ),
            "user_info": (user_left, content_top, user_right, content_bottom),
            "charge_info": (
                charge_header[0], content_top, charge_header[2], content_bottom
            ),
            "other_info": (
                other_header[0], other_header[1], other_header[2], content_bottom
            ),
            "invoice_info": (
                min(item[0] for item in bottom_row),
                content_bottom,
                round(image.width * 0.48),
                round(image.height * 0.97),
            ),
            "carrier_info": (
                round(image.width * 0.485),
                content_bottom,
                max(item[2] for item in bottom_row),
                round(image.height * 0.97),
            ),
        }
        return {
            key: value
            for key, value in bounds.items()
            if value[2] > value[0] and value[3] > value[1]
        }

    @classmethod
    def _high_voltage_back_block_bounds(
        cls, image: Image.Image
    ) -> dict[str, tuple[int, int, int, int]]:
        """Locate the three useful blocks on the high-voltage bill back page."""
        regions = [
            item
            for item in cls._pink_regions(image)
            if item[2] - item[0] >= image.width * 0.35
        ]
        tolerance = max(5, round(image.height * 0.006))
        rows: list[list[tuple[int, int, int, int]]] = []
        for region in sorted(regions, key=lambda item: (item[1], item[0])):
            row = next(
                (items for items in rows if abs(items[0][1] - region[1]) <= tolerance),
                None,
            )
            if row is None:
                rows.append([region])
            else:
                row.append(region)
                row.sort(key=lambda item: item[0])

        top_pair = next((row for row in rows if len(row) == 2), None)
        if top_pair is None:
            return {}
        lower_left = next(
            (
                row[0]
                for row in rows
                if len(row) == 1
                and row[0][1] > top_pair[0][1]
                and row[0][0] < image.width * 0.2
                and row[0][2] < image.width * 0.65
            ),
            None,
        )
        bottom_wide = next(
            (
                row[0]
                for row in rows
                if len(row) == 1
                and lower_left is not None
                and row[0][1] > lower_left[1]
                and row[0][2] - row[0][0] > image.width * 0.75
            ),
            None,
        )
        if lower_left is None or bottom_wide is None:
            return {}

        left, right = top_pair
        return {
            "charge_introduction": (left[0], left[1], left[2], lower_left[1]),
            "energy_saving_info": (right[0], right[1], right[2], bottom_wide[1]),
            "meter_info": (
                lower_left[0],
                lower_left[1],
                lower_left[2],
                bottom_wide[1],
            ),
        }

    async def recognize_async(
        self, image_path: str | Path, *, profile: str = "accurate"
    ) -> list[OcrLine]:
        path = Path(image_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)

        engine = OcrEngine.try_create_from_language(Language(self.language))
        if engine is None:
            available = ", ".join(
                item.language_tag for item in OcrEngine.available_recognizer_languages
            )
            raise RuntimeError(
                f"Windows OCR language {self.language!r} is unavailable; "
                f"installed OCR languages: {available or 'none'}"
            )
        english_engine = OcrEngine.try_create_from_language(Language("en-US"))

        with Image.open(path) as source:
            color_image = ImageOps.exif_transpose(source).convert("RGB")
            image = ImageEnhance.Contrast(color_image.convert("L")).enhance(1.35)
            lines: list[OcrLine] = []
            pink_regions = self._pink_regions(color_image)
            max_dimension = int(OcrEngine.max_image_dimension)
            tile_height = (
                image.height
                if profile == "high_voltage"
                else min(image.height, max(1, int(image.width * 1.15)))
            )
            overlap = max(1, int(tile_height * 0.08))
            step = max(1, tile_height - overlap)
            origins = list(range(0, image.height, step))
            if origins and origins[-1] + tile_height >= image.height:
                origins[-1] = max(0, image.height - tile_height)
            origins = list(dict.fromkeys(origins))

            with tempfile.TemporaryDirectory(prefix="taipower_ocr_") as temp_dir:
                for index, origin_y in enumerate(origins):
                    tile = image.crop(
                        (0, origin_y, image.width, min(image.height, origin_y + tile_height))
                    )
                    color_tile = color_image.crop(
                        (0, origin_y, image.width, min(image.height, origin_y + tile_height))
                    )
                    scale = min(
                        4.0,
                        (max_dimension - 2) / max(tile.width, tile.height),
                    )
                    if scale > 1.0:
                        tile = tile.resize(
                            (round(tile.width * scale), round(tile.height * scale)),
                            Image.Resampling.LANCZOS,
                        )
                        color_tile = color_tile.resize(
                            tile.size, Image.Resampling.LANCZOS
                        )
                    else:
                        scale = 1.0
                    variants = (
                        (tile,)
                        if profile == "high_voltage"
                        else (
                            tile,
                            ImageOps.invert(tile),
                            ImageOps.invert(self._pink_header_text(color_tile)),
                        )
                    )
                    for variant_index, variant in enumerate(variants):
                        tile_path = (
                            Path(temp_dir) / f"tile_{index}_{variant_index}.png"
                        )
                        variant.save(tile_path)
                        tile_engines = [engine]
                        if (
                            profile != "high_voltage"
                            and
                            english_engine is not None
                            and self.language.lower() != "en-us"
                        ):
                            tile_engines.append(english_engine)
                        for tile_engine in tile_engines:
                            recognized = await self._recognize_file(
                                tile_engine, tile_path
                            )
                            for line in recognized:
                                mapped = OcrLine(
                                    text=line.text,
                                    x=line.x / scale,
                                    y=origin_y + line.y / scale,
                                    width=line.width / scale,
                                    height=line.height / scale,
                                )
                                lines.append(mapped)
                                canonical = self._canonical_header(line.text)
                                if canonical:
                                    lines.append(
                                        OcrLine(
                                            text=canonical,
                                            x=mapped.x,
                                            y=mapped.y,
                                            width=mapped.width,
                                            height=mapped.height,
                                        )
                                    )

                if profile == "high_voltage":
                    target_crops = (
                        ("summary", 0.04, 0.18, 0.78, 0.30),
                        ("charge", 0.40, 0.28, 0.78, 0.62),
                    )
                    for name, left_ratio, top_ratio, right_ratio, bottom_ratio in target_crops:
                        left = round(color_image.width * left_ratio)
                        top = round(color_image.height * top_ratio)
                        right = round(color_image.width * right_ratio)
                        bottom = round(color_image.height * bottom_ratio)
                        crop = ImageEnhance.Contrast(
                            color_image.crop((left, top, right, bottom)).convert("L")
                        ).enhance(1.35)
                        scale = min(
                            6.0,
                            (max_dimension - 2) / max(crop.width, crop.height),
                        )
                        if scale > 1.0:
                            crop = crop.resize(
                                (
                                    round(crop.width * scale),
                                    round(crop.height * scale),
                                ),
                                Image.Resampling.LANCZOS,
                            )
                        else:
                            scale = 1.0
                        crop_path = Path(temp_dir) / f"high_voltage_{name}.png"
                        crop.save(crop_path)
                        crop_engines = [engine]
                        if english_engine is not None:
                            crop_engines.append(english_engine)
                        for crop_engine in crop_engines:
                            recognized = await self._recognize_file(
                                crop_engine, crop_path
                            )
                            for line in recognized:
                                lines.append(
                                    OcrLine(
                                        text=line.text,
                                        x=left + line.x / scale,
                                        y=top + line.y / scale,
                                        width=line.width / scale,
                                        height=line.height / scale,
                                    )
                                )

                for index, (left, top, right, bottom) in enumerate(
                    pink_regions if profile != "high_voltage" else []
                ):
                    header = color_image.crop((left, top, right, bottom))
                    header = self._pink_header_text(header)
                    scale = min(
                        6.0,
                        (max_dimension - 2) / max(header.width, header.height),
                    )
                    if scale > 1.0:
                        header = header.resize(
                            (round(header.width * scale), round(header.height * scale)),
                            Image.Resampling.LANCZOS,
                        )
                    else:
                        scale = 1.0
                    header_path = Path(temp_dir) / f"header_{index}.png"
                    header.save(header_path)
                    header_engines = [engine]
                    if english_engine is not None and self.language.lower() != "en-us":
                        header_engines.append(english_engine)
                    for header_engine in header_engines:
                        recognized = await self._recognize_file(
                            header_engine, header_path
                        )
                        for line in recognized:
                            mapped = OcrLine(
                                text=line.text,
                                x=left + line.x / scale,
                                y=top + line.y / scale,
                                width=line.width / scale,
                                height=line.height / scale,
                            )
                            lines.append(mapped)
                            canonical = self._canonical_header(line.text)
                            if canonical:
                                lines.append(
                                    OcrLine(
                                        text=canonical,
                                        x=mapped.x,
                                        y=mapped.y,
                                        width=mapped.width,
                                        height=mapped.height,
                                    )
                                )

            lines.extend(
                self._infer_pink_template_headers(
                    pink_regions, color_image.width, color_image.height
                )
            )

        deduplicated: list[OcrLine] = []
        for line in sorted(lines, key=lambda item: (item.y, item.x)):
            duplicate = any(
                existing.text == line.text
                and abs(existing.y - line.y) <= max(existing.height, line.height)
                for existing in deduplicated[-20:]
            )
            if not duplicate:
                deduplicated.append(line)
        return deduplicated

    @staticmethod
    def _canonical_header(text: str) -> str | None:
        candidates = {
            "basicinfo": "用戶資訊 Basic Info.",
            "chargeinfo": "計費內容 Charge Info.",
            "paymentinfo": "繳費資訊 Payment Info.",
            "otherinfo": "其他資訊 Other Info.",
            "chargeintroduction": "電費計收說明 Charge Introduction",
            "energysavinginfo": "節電資訊 Energy Saving Info.",
            "meterinfo": "電表資訊 Meter Info.",
        }
        normalized = "".join(character for character in text.lower() if character.isalnum())
        if len(normalized) < 5:
            return None
        alias, score = max(
            (
                (alias, SequenceMatcher(None, normalized, alias).ratio())
                for alias in candidates
            ),
            key=lambda item: item[1],
        )
        return candidates[alias] if score >= 0.58 else None

    @staticmethod
    def _pink_mask(image: Image.Image) -> Image.Image:
        hue, saturation, value = image.convert("HSV").split()
        pink_hue = hue.point(lambda item: 255 if 185 <= item <= 252 else 0)
        pink_saturation = saturation.point(lambda item: 255 if item >= 24 else 0)
        visible = value.point(lambda item: 255 if item >= 130 else 0)
        return ImageChops.multiply(
            ImageChops.multiply(pink_hue, pink_saturation), visible
        )

    @staticmethod
    def _colored_mask(image: Image.Image) -> Image.Image:
        _, saturation, value = image.convert("HSV").split()
        saturated = saturation.point(lambda item: 255 if item >= 35 else 0)
        visible_ink = value.point(lambda item: 255 if 60 <= item <= 250 else 0)
        return ImageChops.multiply(saturated, visible_ink)

    @classmethod
    def _pink_header_text(cls, image: Image.Image) -> Image.Image:
        saturation = image.convert("HSV").split()[1]
        return ImageEnhance.Contrast(ImageOps.autocontrast(saturation)).enhance(1.4)

    @classmethod
    def _pink_regions(cls, image: Image.Image) -> list[tuple[int, int, int, int]]:
        mask = cls._pink_mask(image)
        pixels = mask.load()

        def runs(flags: list[bool], allowed_gap: int = 3) -> list[tuple[int, int]]:
            found: list[tuple[int, int]] = []
            start = None
            last_true = None
            for index, active in enumerate(flags):
                if active:
                    if start is None:
                        start = index
                    last_true = index
                elif start is not None and index - last_true > allowed_gap:
                    found.append((start, last_true + 1))
                    start = None
                    last_true = None
            if start is not None:
                found.append((start, last_true + 1))
            return found

        row_threshold = max(8, image.width // 100)
        active_rows = [
            sum(1 for x in range(image.width) if pixels[x, y]) >= row_threshold
            for y in range(image.height)
        ]
        regions: list[tuple[int, int, int, int]] = []
        for top, bottom in runs(active_rows):
            if bottom - top < 4:
                continue
            active_columns = [
                sum(1 for y in range(top, bottom) if pixels[x, y]) >= 2
                for x in range(image.width)
            ]
            for left, right in runs(active_columns):
                if right - left >= 35:
                    regions.append((left, top, right, bottom))
        return regions

    @classmethod
    def _colored_regions(cls, image: Image.Image) -> list[tuple[int, int, int, int]]:
        mask = cls._colored_mask(image)
        pixels = mask.load()

        def runs(flags: list[bool], allowed_gap: int = 3) -> list[tuple[int, int]]:
            found: list[tuple[int, int]] = []
            start = None
            last_true = None
            for index, active in enumerate(flags):
                if active:
                    if start is None:
                        start = index
                    last_true = index
                elif start is not None and index - last_true > allowed_gap:
                    found.append((start, last_true + 1))
                    start = None
                    last_true = None
            if start is not None:
                found.append((start, last_true + 1))
            return found

        row_threshold = max(8, image.width // 100)
        active_rows = [
            sum(1 for x in range(image.width) if pixels[x, y]) >= row_threshold
            for y in range(image.height)
        ]
        regions: list[tuple[int, int, int, int]] = []
        for top, bottom in runs(active_rows):
            if bottom - top < 4:
                continue
            active_columns = [
                sum(1 for y in range(top, bottom) if pixels[x, y]) >= 2
                for x in range(image.width)
            ]
            for left, right in runs(active_columns):
                if right - left >= 35:
                    regions.append((left, top, right, bottom))
        return regions

    @staticmethod
    def _infer_pink_template_headers(
        regions: list[tuple[int, int, int, int]],
        image_width: int,
        image_height: int,
    ) -> list[OcrLine]:
        """Infer titles only when the legacy bill's colored-grid pattern matches."""
        rows: list[list[tuple[int, int, int, int]]] = []
        tolerance = max(4, round(image_height * 0.006))
        for region in sorted(regions, key=lambda item: (item[1], item[0])):
            if region[2] - region[0] < image_width * 0.05:
                continue
            matching_row = next(
                (row for row in rows if abs(row[0][1] - region[1]) <= tolerance),
                None,
            )
            if matching_row is None:
                rows.append([region])
            else:
                matching_row.append(region)
                matching_row.sort(key=lambda item: item[0])

        inferred: list[tuple[str, tuple[int, int, int, int]]] = []
        summary = next((row for row in rows if len(row) >= 4), None)
        if summary:
            inferred.append(("繳費資訊 Payment Info.", summary[-1]))

        upper_pairs = [
            row
            for row in rows
            if len(row) == 2
            and row[0][1] < image_height * 0.5
            and (summary is None or row[0][1] > summary[0][1])
        ]
        if upper_pairs:
            main = upper_pairs[0]
            inferred.extend(
                [
                    ("用戶資訊 Basic Info.", main[0]),
                    ("計費內容 Charge Info.", main[1]),
                ]
            )
            right_single = next(
                (
                    row[0]
                    for row in rows
                    if len(row) == 1
                    and row[0][1] > main[0][1]
                    and row[0][1] < image_height * 0.5
                    and row[0][0] > image_width * 0.6
                ),
                None,
            )
            if right_single:
                inferred.append(("其他資訊 Other Info.", right_single))

        lower_pair = next(
            (
                row
                for row in rows
                if len(row) == 2 and row[0][1] > image_height * 0.5
            ),
            None,
        )
        if lower_pair:
            inferred.extend(
                [
                    ("電費計收說明 Charge Introduction", lower_pair[0]),
                    ("節電資訊 Energy Saving Info.", lower_pair[1]),
                ]
            )
            meter = next(
                (
                    row[0]
                    for row in rows
                    if len(row) == 1
                    and row[0][1] > lower_pair[0][1]
                    and row[0][0] < image_width * 0.5
                ),
                None,
            )
            if meter:
                inferred.append(("電表資訊 Meter Info.", meter))

        return [
            OcrLine(
                text=title,
                x=left,
                y=top,
                width=right - left,
                height=bottom - top,
            )
            for title, (left, top, right, bottom) in inferred
        ]

    async def _recognize_file(
        self, engine: OcrEngine, image_path: Path
    ) -> list[OcrLine]:
        lines, _ = await self._recognize_file_with_words(engine, image_path)
        return lines

    async def _recognize_file_with_words(
        self, engine: OcrEngine, image_path: Path
    ) -> tuple[list[OcrLine], list[OcrLine]]:
        storage_file = await StorageFile.get_file_from_path_async(str(image_path))

        stream = await storage_file.open_async(FileAccessMode.READ)
        bitmap = None
        try:
            decoder = await BitmapDecoder.create_async(stream)
            bitmap = await decoder.get_software_bitmap_async()
            result = await engine.recognize_async(bitmap)
            lines: list[OcrLine] = []
            all_words: list[OcrLine] = []
            for recognized_line in result.lines:
                words = list(recognized_line.words)
                if not words:
                    continue
                all_words.extend(
                    OcrLine(
                        text=word.text.strip(),
                        x=word.bounding_rect.x,
                        y=word.bounding_rect.y,
                        width=word.bounding_rect.width,
                        height=word.bounding_rect.height,
                    )
                    for word in words
                    if word.text.strip()
                )
                left = min(word.bounding_rect.x for word in words)
                top = min(word.bounding_rect.y for word in words)
                right = max(
                    word.bounding_rect.x + word.bounding_rect.width for word in words
                )
                bottom = max(
                    word.bounding_rect.y + word.bounding_rect.height for word in words
                )
                lines.append(
                    OcrLine(
                        text=recognized_line.text.strip(),
                        x=left,
                        y=top,
                        width=right - left,
                        height=bottom - top,
                    )
                )
            return lines, all_words
        finally:
            if bitmap is not None:
                bitmap.close()
            stream.close()
