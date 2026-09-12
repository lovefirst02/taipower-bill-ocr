import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageChops


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import taipower_bill_ocr

try:
    from taipower_bill_ocr.paddle_ocr import (
        _create_paddle_engine,
        extract_bill_with_paddleocr,
        extract_pdf_with_paddleocr,
        recognize_with_paddleocr,
    )
except ImportError:
    _create_paddle_engine = None
    extract_bill_with_paddleocr = None
    extract_pdf_with_paddleocr = None
    recognize_with_paddleocr = None


class FakePaddleResult:
    def __init__(self, payload):
        self.json = {"res": payload}


class FakePaddleEngine:
    def __init__(self, payload):
        self.payload = payload
        self.paths = []

    def predict(self, path):
        self.paths.append(path)
        return [FakePaddleResult(self.payload)]


class BlockAwarePaddleEngine:
    """A deterministic stand-in for expensive inference on real block crops."""

    def predict(self, path):
        block_key = Path(path).stem
        with Image.open(path) as image:
            width, height = image.size

        def box(center_x, center_y, box_width=0.08, box_height=0.02):
            half_width = width * box_width / 2
            half_height = height * box_height / 2
            return [
                width * center_x - half_width,
                height * center_y - half_height,
                width * center_x + half_width,
                height * center_y + half_height,
            ]

        if block_key == "payment_info":
            payload = {
                "rec_texts": ["繳費期限 110/01/20", "電號 00-12-3456-78-9"],
                "rec_scores": [0.99, 0.99],
                "rec_boxes": [
                    box(0.35, 0.35, 0.35),
                    box(0.35, 0.65, 0.35),
                ],
            }
        elif block_key == "user_info":
            payload = {
                "rec_texts": ["經常度數", "858"],
                "rec_scores": [0.99, 0.98],
                "rec_boxes": [box(0.20, 0.20), box(0.75, 0.20)],
            }
        elif block_key == "charge_info":
            payload = {
                "rec_texts": ["應繳總金額 1,234元"],
                "rec_scores": [0.99],
                "rec_boxes": [box(0.50, 0.25, 0.35)],
            }
        elif block_key == "energy_saving_info":
            texts = []
            boxes = []
            for row_y, values in (
                (0.07, ("31", "15000", "483.87")),
                (0.09, ("31", "20000", "645.16")),
                (0.11, ("30", "30000", "1000.0")),
            ):
                for center_x, value in zip((0.32, 0.55, 0.82), values):
                    texts.append(value)
                    boxes.append(box(center_x, row_y, 0.10, 0.008))
            payload = {
                "rec_texts": texts,
                "rec_scores": [0.99] * len(texts),
                "rec_boxes": boxes,
            }
        elif block_key == "meter_info":
            payload = {
                "rec_texts": ["電號00123456789", "01", "03000.7", "04000.0"],
                "rec_scores": [0.99] * 4,
                "rec_boxes": [
                    box(0.35, 0.10, 0.40),
                    box(0.05, 0.25, 0.05),
                    box(0.23, 0.25, 0.14),
                    box(0.85, 0.25, 0.14),
                ],
            }
        else:
            payload = {"rec_texts": [], "rec_scores": [], "rec_boxes": []}
        return [FakePaddleResult(payload)]


class PaddleOcrTests(unittest.TestCase):
    def setUp(self):
        if recognize_with_paddleocr is None:
            self.fail("PaddleOCR extraction module has not been implemented")

    def test_converts_rec_boxes_and_filters_low_confidence_text(self):
        engine = FakePaddleEngine(
            {
                "rec_texts": ["用戶資訊 Basic Info.", "計費度數", "858", "雜訊"],
                "rec_scores": [0.99, 0.98, 0.96, 0.20],
                "rec_boxes": [
                    [10, 10, 260, 40],
                    [20, 70, 120, 95],
                    [180, 70, 225, 95],
                    [10, 120, 60, 140],
                ],
            }
        )

        lines = recognize_with_paddleocr(
            PROJECT_ROOT / "samples" / "summer_2021.jpg",
            score_threshold=0.5,
            ocr_engine=engine,
        )

        self.assertEqual([line.text for line in lines], [
            "用戶資訊 Basic Info.", "計費度數", "858"
        ])
        self.assertEqual((lines[2].x, lines[2].y, lines[2].width, lines[2].height),
                         (180.0, 70.0, 45.0, 25.0))

    def test_default_engine_uses_small_cpu_models(self):
        """Catches accidentally restoring CPU-hostile medium model defaults."""

        class CapturingPaddleOCR:
            def __init__(self, **configuration):
                self.configuration = configuration

        fake_module = types.SimpleNamespace(PaddleOCR=CapturingPaddleOCR)
        with patch.dict(sys.modules, {"paddleocr": fake_module}):
            engine = _create_paddle_engine("chinese_cht")

        self.assertEqual(engine.configuration.get("device"), "cpu")
        self.assertEqual(
            engine.configuration.get("text_detection_model_name"),
            "PP-OCRv6_small_det",
        )
        self.assertEqual(
            engine.configuration.get("text_recognition_model_name"),
            "PP-OCRv6_small_rec",
        )
        self.assertNotIn("lang", engine.configuration)

    def test_extracts_structured_bill_from_paddle_result(self):
        engine = FakePaddleEngine(
            {
                "rec_texts": ["用戶資訊 Basic Info.", "經常度數", "858"],
                "rec_scores": [0.99, 0.98, 0.96],
                "rec_boxes": [
                    [10, 10, 260, 40],
                    [20, 70, 120, 95],
                    [180, 70, 225, 95],
                ],
            }
        )

        result = extract_bill_with_paddleocr(
            PROJECT_ROOT / "samples" / "summer_2021.jpg",
            ocr_engine=engine,
        )

        self.assertEqual(result.get("user_info.energy_consumption_kwh"), 858)
        self.assertEqual(result.get("quality.ocr_engine"), "paddleocr")
        self.assertEqual(result.get("quality.score_threshold"), 0.0)

    def test_keeps_parser_detected_detailed_layout_type(self):
        """Catches the image detector overwriting OCR-text layout classification."""
        engine = FakePaddleEngine(
            {
                "rec_texts": [
                    "Billing period:20260601-20260630",
                    "基本資料",
                    "計費內容",
                    "用電資料",
                    "電表資料",
                ],
                "rec_scores": [0.99] * 5,
                "rec_boxes": [
                    [20, 20, 340, 45],
                    [50, 80, 250, 115],
                    [550, 80, 750, 115],
                    [50, 500, 250, 535],
                    [550, 500, 750, 535],
                ],
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "detailed.png"
            Image.new("RGB", (1000, 900), "white").save(image_path)
            result = extract_bill_with_paddleocr(image_path, ocr_engine=engine)

        self.assertEqual(result.get("document_type"), "detailed_high_voltage_bill")

    def test_partial_block_detection_falls_back_to_full_page_ocr(self):
        """Catches a lone charge crop discarding the lower usage table."""

        class FullPageAwareEngine:
            def predict(self, path):
                if Path(path).stem == "charge_info":
                    payload = {
                        "rec_texts": ["基本電費(約定)：", "335,400.0元"],
                        "rec_scores": [0.99, 0.99],
                        "rec_boxes": [[10, 20, 160, 45], [220, 20, 350, 45]],
                    }
                else:
                    payload = {
                        "rec_texts": [
                            "Billing period:20260601-20260630",
                            "基本資料",
                            "計費內容",
                            "用電資料",
                            "電表資料",
                            "計費度數",
                            "尖峰度數：",
                            "128210",
                            "轉供度數",
                            "半尖峰度數：",
                            "244800",
                        ],
                        "rec_scores": [0.99] * 11,
                        "rec_boxes": [
                            [20, 20, 340, 45],
                            [50, 80, 250, 115],
                            [550, 80, 750, 115],
                            [50, 500, 250, 535],
                            [550, 500, 750, 535],
                            [20, 555, 120, 580],
                            [160, 540, 280, 565],
                            [350, 540, 430, 565],
                            [20, 655, 120, 680],
                            [160, 635, 300, 660],
                            [350, 635, 430, 660],
                        ],
                    }
                return [FakePaddleResult(payload)]

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "partial-detection.png"
            Image.new("RGB", (1000, 900), "white").save(image_path)
            with patch(
                "taipower_bill_ocr.paddle_ocr._paddle_block_bounds",
                return_value=(
                    {"charge_info": (500, 100, 900, 400)},
                    "high_voltage_bill",
                    "pink_template",
                ),
            ):
                result = extract_bill_with_paddleocr(
                    image_path,
                    ocr_engine=FullPageAwareEngine(),
                )

        self.assertEqual(
            result.get("energy_usage.energy_consumption.peak_kwh"), 128210
        )
        self.assertEqual(
            result.get("energy_usage.transferred_energy.semi_peak_kwh"), 244800
        )
        self.assertEqual(result.get("quality.ocr_profile"), "full_page_fallback")

    def test_accepts_polygon_coordinates_when_boxes_are_unavailable(self):
        engine = FakePaddleEngine(
            {
                "rec_texts": ["電表資料 Meter Info."],
                "rec_scores": [0.93],
                "rec_polys": [[[30, 50], [230, 48], [232, 80], [31, 82]]],
            }
        )

        lines = recognize_with_paddleocr(
            PROJECT_ROOT / "samples" / "summer_2021.jpg",
            ocr_engine=engine,
        )

        self.assertEqual(
            (lines[0].x, lines[0].y, lines[0].width, lines[0].height),
            (30.0, 48.0, 202.0, 34.0),
        )

    def test_pdf_reuses_one_engine_for_all_selected_pages(self):
        engine = FakePaddleEngine(
            {
                "rec_texts": ["用戶資訊 Basic Info.", "計費度數 858"],
                "rec_scores": [0.99, 0.96],
                "rec_boxes": [[10, 10, 260, 40], [20, 70, 225, 95]],
            }
        )

        with patch(
            "taipower_bill_ocr.paddle_ocr._create_paddle_engine",
            return_value=engine,
        ) as create_engine:
            bills = extract_pdf_with_paddleocr(
                PROJECT_ROOT
                / "samples"
                / "official_high_voltage_2021_two_page.pdf",
                page_numbers=[1, 2],
                dpi=72,
            )

        self.assertEqual(create_engine.call_count, 1)
        self.assertGreater(len(engine.paths), 2)
        self.assertEqual([bill.get("quality.page_number") for bill in bills], [1, 2])
        self.assertTrue(all(bill.get("quality.ocr_engine") == "paddleocr"
                            for bill in bills))

    def test_original_image_pipeline_can_select_paddle_without_changing_default_api(self):
        """Catches accepting a backend option but still routing the image to Windows OCR."""
        engine = FakePaddleEngine(
            {
                "rec_texts": ["用戶資訊 Basic Info.", "經常度數", "858"],
                "rec_scores": [0.99, 0.98, 0.96],
                "rec_boxes": [
                    [10, 10, 260, 40],
                    [20, 70, 120, 95],
                    [180, 70, 225, 95],
                ],
            }
        )
        with patch(
            "taipower_bill_ocr.paddle_ocr._create_paddle_engine",
            return_value=engine,
        ):
            try:
                result = taipower_bill_ocr.extract_bill(
                    PROJECT_ROOT / "samples" / "summer_2021.jpg",
                    ocr_backend="paddleocr",
                    paddle_score_threshold=0.45,
                )
            except TypeError as exc:
                self.fail(f"original pipeline does not accept PaddleOCR: {exc}")

        self.assertEqual(result.get("user_info.energy_consumption_kwh"), 858)
        self.assertEqual(result.get("quality.ocr_engine"), "paddleocr")

    def test_original_pdf_pipeline_can_select_paddle_for_image_pages(self):
        """Catches the PDF entry point silently dropping the selected OCR backend."""
        engine = FakePaddleEngine(
            {
                "rec_texts": ["用戶資訊 Basic Info.", "經常度數 858"],
                "rec_scores": [0.99, 0.96],
                "rec_boxes": [[10, 10, 260, 40], [20, 70, 225, 95]],
            }
        )
        with patch(
            "taipower_bill_ocr.paddle_ocr._create_paddle_engine",
            return_value=engine,
        ):
            try:
                bills = taipower_bill_ocr.extract_pdf(
                    PROJECT_ROOT
                    / "samples"
                    / "official_high_voltage_2021_two_page.pdf",
                    page_numbers=[1],
                    dpi=72,
                    ocr_backend="paddleocr",
                    paddle_score_threshold=0.45,
                )
            except TypeError as exc:
                self.fail(f"PDF pipeline does not accept PaddleOCR: {exc}")

        self.assertEqual(bills[0].get("user_info.energy_consumption_kwh"), 858)
        self.assertEqual(bills[0].get("quality.ocr_engine"), "paddleocr")

    def test_paddle_pipeline_ocr_is_scoped_to_detected_blocks(self):
        """Catches reverting Paddle to full-page OCR with cross-column assignment."""
        with patch(
            "taipower_bill_ocr.paddle_ocr._create_paddle_engine",
            return_value=BlockAwarePaddleEngine(),
        ):
            result = taipower_bill_ocr.extract_bill(
                PROJECT_ROOT / "samples" / "high_voltage_2024_page02.png",
                ocr_backend="paddleocr",
            )

        self.assertEqual(result.get("user_info.energy_consumption_kwh"), 858)
        self.assertEqual(result.get("charge_info.total_amount_twd"), 1234)
        self.assertEqual(
            result.get("quality.ocr_profile"), "blockwise_high_accuracy"
        )
        self.assertGreater(result["blocks"]["user_info"]["bounds"]["width"], 0)

    def test_paddle_pipeline_reconstructs_back_page_unruled_tables(self):
        """Catches dropping Paddle word coordinates needed by back-page tables."""
        with patch(
            "taipower_bill_ocr.paddle_ocr._create_paddle_engine",
            return_value=BlockAwarePaddleEngine(),
        ):
            result = taipower_bill_ocr.extract_pdf(
                PROJECT_ROOT
                / "samples"
                / "official_high_voltage_2021_two_page.pdf",
                page_numbers=[2],
                dpi=200,
                ocr_backend="paddleocr",
            )[0]

        comparison_rows = result.get("energy_saving_info.comparison_rows")
        self.assertIsNotNone(comparison_rows)
        self.assertEqual(comparison_rows[0]["energy_kwh"], 15000)
        self.assertEqual(result.get("meter_info.customer_number"), "00123456789")
        readings = result.get("meter_info.readings")
        self.assertIsNotNone(readings)
        self.assertEqual(
            readings[0],
            {
                "meter_type": "01",
                "previous": "03000.7",
                "removed": None,
                "installed": None,
                "current": "04000.0",
            },
        )

    def test_paddle_reconstructs_tables_with_e65853_red_headers(self):
        """Catches red title bars forcing Paddle into full-page fallback."""
        import pdfplumber

        source_pdf = (
            PROJECT_ROOT / "samples" / "official_high_voltage_2021_two_page.pdf"
        )
        with tempfile.TemporaryDirectory() as temp_dir, pdfplumber.open(
            source_pdf
        ) as pdf:
            source = pdf.pages[1].to_image(
                resolution=200, antialias=True
            ).original.convert("RGB")
            hue, saturation, value = source.convert("HSV").split()
            purple_hue = hue.point(lambda item: 255 if 185 <= item <= 252 else 0)
            saturated = saturation.point(lambda item: 255 if item >= 24 else 0)
            visible = value.point(lambda item: 255 if item >= 130 else 0)
            header_mask = ImageChops.multiply(
                ImageChops.multiply(purple_hue, saturated), visible
            )
            recolored = Image.composite(
                Image.new("RGB", source.size, "#E65853"), source, header_mask
            )
            sample_path = Path(temp_dir) / "red-high-voltage-back-page.png"
            recolored.save(sample_path)

            result = extract_bill_with_paddleocr(
                sample_path,
                ocr_engine=BlockAwarePaddleEngine(),
            )

        self.assertEqual(result.get("quality.block_detection"), "color_independent")
        self.assertEqual(
            result.get("energy_saving_info.comparison_rows")[0]["energy_kwh"],
            15000,
        )
        self.assertEqual(result.get("meter_info.readings")[0]["current"], "04000.0")

    def test_paddle_keeps_high_voltage_due_date_out_of_bank_payment_date(self):
        """Catches classifying a wide high-voltage invoice row as a bank receipt."""
        with patch(
            "taipower_bill_ocr.paddle_ocr._create_paddle_engine",
            return_value=BlockAwarePaddleEngine(),
        ):
            result = taipower_bill_ocr.extract_pdf(
                PROJECT_ROOT
                / "samples"
                / "official_high_voltage_2021_two_page.pdf",
                page_numbers=[1],
                dpi=200,
                ocr_backend="paddleocr",
            )[0]

        self.assertEqual(result["document_type"], "high_voltage_bill")
        self.assertEqual(result.get("payment_info.due_date"), "110/01/20")
        self.assertIsNone(result.get("payment_info.payment_date"))


if __name__ == "__main__":
    unittest.main()
