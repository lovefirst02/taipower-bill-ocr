import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from taipower_bill_ocr.parser import BillResult
from taipower_bill_ocr.parser import OcrLine
import taipower_bill_ocr

try:
    from taipower_bill_ocr.pipeline import merge_ocr_results
except ImportError:
    merge_ocr_results = None


def bill_with_fields(**sections):
    blocks = {}
    result = BillResult(
        {
            "blocks": blocks,
            "by_label": {},
            "quality": {"warnings": []},
            "document_type": "high_voltage_bill",
        }
    )
    for block_key, fields in sections.items():
        blocks[block_key] = {
            "title": block_key,
            "fields": fields,
            "raw_lines": [],
        }
        result[block_key] = fields
    return result


class HybridOcrMergeTests(unittest.TestCase):
    def setUp(self):
        if merge_ocr_results is None:
            self.fail("hybrid OCR merge has not been implemented")

    def test_agreed_values_are_kept_without_a_warning(self):
        windows = bill_with_fields(
            payment_info={"customer_number": "00-12-3456-78-9"}
        )
        paddle = bill_with_fields(
            payment_info={"customer_number": "00-12-3456-78-9"}
        )

        merged = merge_ocr_results(windows, paddle)

        self.assertEqual(
            merged.get("payment_info.customer_number"), "00-12-3456-78-9"
        )
        self.assertEqual(merged["quality"]["hybrid"]["agreement_count"], 1)
        self.assertEqual(merged["quality"]["warnings"], [])

    def test_missing_windows_values_are_filled_from_paddle_in_the_block(self):
        windows = bill_with_fields(other_info={"outage_group": "E"})
        paddle = bill_with_fields(
            other_info={"outage_group": "E", "carbon_emission_kg": 311414}
        )

        merged = merge_ocr_results(windows, paddle)

        self.assertEqual(merged.get("other_info.carbon_emission_kg"), 311414)
        self.assertEqual(
            merged["blocks"]["other_info"]["fields"]["carbon_emission_kg"],
            311414,
        )
        self.assertEqual(
            merged["quality"]["hybrid"]["filled_from_paddle_count"], 1
        )

    def test_conflicts_keep_windows_and_record_both_values(self):
        windows = bill_with_fields(charge_info={"total_amount_twd": 800000})
        paddle = bill_with_fields(charge_info={"total_amount_twd": 800008})

        merged = merge_ocr_results(windows, paddle)

        self.assertEqual(merged.get("charge_info.total_amount_twd"), 800000)
        self.assertIn(
            "hybrid_conflict:charge_info.total_amount_twd",
            merged["quality"]["warnings"],
        )
        self.assertEqual(
            merged["quality"]["hybrid"]["conflicts"],
            [
                {
                    "path": "charge_info.total_amount_twd",
                    "windows": 800000,
                    "paddleocr": 800008,
                }
            ],
        )

    def test_zero_is_a_real_windows_value_not_a_missing_value(self):
        windows = bill_with_fields(
            user_info={"energy_consumption": {"peak_kwh": 0}}
        )
        paddle = bill_with_fields(
            user_info={"energy_consumption": {"peak_kwh": 100}}
        )

        merged = merge_ocr_results(windows, paddle)

        self.assertEqual(merged.get("user_info.energy_consumption.peak_kwh"), 0)
        self.assertEqual(merged["quality"]["hybrid"]["conflict_count"], 1)


class FakeWindowsEngine:
    def __init__(self, language="zh-Hant"):
        self.language = language

    def recognize(self, image_path, *, profile="accurate"):
        return [
            OcrLine("用戶資訊 Basic Info.", 10, 10, 250, 30),
            OcrLine("計費內容 Charge Info.", 300, 10, 250, 30),
            OcrLine("經常度數 858", 20, 70, 180, 25),
            OcrLine("應繳總金額 800,000元", 320, 70, 200, 25),
        ]

    def recognize_blocks(self, image_path):
        lines = [
            OcrLine("用戶資訊 Basic Info.", 10, 10, 250, 30, "user_info"),
            OcrLine("計費內容 Charge Info.", 300, 10, 250, 30, "charge_info"),
            OcrLine("經常度數 858", 20, 70, 180, 25, "user_info"),
            OcrLine(
                "應繳總金額 800,000元", 320, 70, 200, 25, "charge_info"
            ),
        ]
        return lines, {}, {}, "high_voltage_bill", "pink_template"


class HybridOcrPipelineTests(unittest.TestCase):
    def test_image_pipeline_exposes_hybrid_as_an_independent_backend(self):
        paddle = bill_with_fields(
            user_info={"energy_consumption_kwh": 858},
            charge_info={"total_amount_twd": 800008},
            other_info={"carbon_emission_kg": 311414},
        )
        with patch(
            "taipower_bill_ocr.pipeline.WindowsOcrEngine", FakeWindowsEngine
        ), patch(
            "taipower_bill_ocr.paddle_ocr.extract_bill_with_paddleocr",
            return_value=paddle,
        ):
            try:
                result = taipower_bill_ocr.extract_bill(
                    PROJECT_ROOT / "samples" / "summer_2021.jpg",
                    ocr_backend="hybrid",
                )
            except ValueError as exc:
                self.fail(f"hybrid image backend is not connected: {exc}")

        self.assertEqual(result.get("user_info.energy_consumption_kwh"), 858)
        self.assertEqual(result.get("charge_info.total_amount_twd"), 800000)
        self.assertEqual(result.get("other_info.carbon_emission_kg"), 311414)
        self.assertEqual(result.get("quality.ocr_engine"), "hybrid")

    def test_pdf_pipeline_merges_corresponding_pages(self):
        paddle_page = bill_with_fields(
            user_info={"energy_consumption_kwh": 858},
            other_info={"carbon_emission_kg": 311414},
        )
        paddle_page["quality"].update(
            {"source": "image_pdf", "page_number": 1, "dpi": 72}
        )
        with patch(
            "taipower_bill_ocr.pipeline.WindowsOcrEngine", FakeWindowsEngine
        ), patch(
            "taipower_bill_ocr.paddle_ocr.extract_pdf_with_paddleocr",
            return_value=[paddle_page],
        ):
            try:
                results = taipower_bill_ocr.extract_pdf(
                    PROJECT_ROOT
                    / "samples"
                    / "official_high_voltage_2021_two_page.pdf",
                    page_numbers=[1],
                    dpi=72,
                    ocr_backend="hybrid",
                )
            except ValueError as exc:
                self.fail(f"hybrid PDF backend is not connected: {exc}")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].get("quality.ocr_engine"), "hybrid")
        self.assertEqual(results[0].get("quality.page_number"), 1)
        self.assertEqual(results[0].get("other_info.carbon_emission_kg"), 311414)


if __name__ == "__main__":
    unittest.main()
