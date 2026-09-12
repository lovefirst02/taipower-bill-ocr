import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from taipower_bill_ocr import extract_bill
from taipower_bill_ocr.parser import BillResult, OcrLine, enrich_bank_autopay_receipt


class BankAutopayReceiptParserTests(unittest.TestCase):
    def test_recovers_energy_groups_from_raw_lines_on_full_page_fallback(self):
        """Catches bank receipts whose block detector provides no word coordinates."""
        raw_lines = [
            "離峰需量",
            "72",
            "72",
            "費度數（度）/ Energy",
            "Consumption(kwh)",
            "（）/ Energy Consumption(kwh)",
            "(92111%)",
            "經常（尖峰）度數",
            "912",
            "912",
            "週六半尖峰度數",
            "107",
            "107",
            "離峰度數",
            "780",
            "780",
            "專供度數（度）",
            "經常（尖峰）度數",
            "10968",
            "10968",
            "週六半尖峰度數",
            "1333",
            "1333",
        ]
        fields = {}
        result = BillResult(
            {
                "blocks": {
                    "user_info": {
                        "title": "用戶資訊",
                        "fields": fields,
                        "raw_lines": raw_lines,
                    }
                },
                "user_info": fields,
                "by_label": {},
                "quality": {},
            }
        )

        enrich_bank_autopay_receipt(result, {}, {})

        self.assertEqual(
            result.get("user_info.energy_consumption"),
            {
                "peak_kwh": 912,
                "saturday_semi_peak_kwh": 107,
                "off_peak_kwh": 780,
            },
        )
        self.assertEqual(
            result.get("user_info.transferred_energy"),
            {
                "peak_kwh": 10968,
                "saturday_semi_peak_kwh": 1333,
            },
        )

    def test_recovers_energy_groups_when_ji_is_missing_from_billing_heading(self):
        """The OCR can read 計費度數 as 費度數 and emit duplicate values/noise."""
        words = [
            OcrLine("費度數（度）/ Energy", 80, 300, 250, 20, "user_info"),
            OcrLine("Consumption(kwh)", 80, 325, 220, 20, "user_info"),
            OcrLine("（）/ Energy Consumption(kwh)", 80, 350, 300, 20, "user_info"),
            OcrLine("(92111%)", 760, 350, 100, 20, "user_info"),
            OcrLine("經常（尖峰）度數", 120, 385, 220, 20, "user_info"),
            OcrLine("912", 820, 385, 55, 20, "user_info"),
            OcrLine("912", 822, 386, 55, 20, "user_info"),
            OcrLine("週六半尖峰度數", 120, 420, 220, 20, "user_info"),
            OcrLine("107", 820, 420, 55, 20, "user_info"),
            OcrLine("107", 822, 421, 55, 20, "user_info"),
            OcrLine("離峰度數", 120, 455, 150, 20, "user_info"),
            OcrLine("780", 820, 455, 55, 20, "user_info"),
            OcrLine("780", 822, 456, 55, 20, "user_info"),
            OcrLine("轉供度數（度）", 80, 500, 190, 20, "user_info"),
            OcrLine("經常（尖峰）度數", 120, 535, 220, 20, "user_info"),
            OcrLine("10968", 800, 535, 90, 20, "user_info"),
        ]
        result = BillResult(
            {
                "blocks": {"user_info": {"title": "用戶資訊", "fields": {}}},
                "user_info": {},
                "by_label": {},
                "quality": {},
            }
        )

        enrich_bank_autopay_receipt(
            result,
            {"user_info": words},
            {"user_info": {"x": 0, "y": 0, "width": 1000, "height": 1000}},
        )

        self.assertEqual(
            result.get("user_info.energy_consumption"),
            {
                "peak_kwh": 912,
                "saturday_semi_peak_kwh": 107,
                "off_peak_kwh": 780,
            },
        )
        self.assertEqual(
            result.get("user_info.transferred_energy"),
            {"peak_kwh": 10968},
        )


class BankAutopayReceiptIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.bill = extract_bill(
                PROJECT_ROOT / "samples" / "official_bank_autopay_receipt_2024.jpg",
                ocr_profile="blockwise_high_accuracy",
            )
        except ValueError as error:
            cls.bill = None
            cls.extraction_error = error
        else:
            cls.extraction_error = None

    def test_auto_detects_bank_autopay_without_invalid_block_crops(self):
        """Catches treating the bottom invoice row as the main content row."""
        self.assertIsNone(self.extraction_error)
        self.assertEqual(self.bill.get("document_type"), "bank_autopay_receipt")
        for block_key in ("payment_info", "user_info", "charge_info", "other_info"):
            self.assertIn("bounds", self.bill["blocks"][block_key])

    def test_extracts_bank_autopay_specific_payment_and_usage_fields(self):
        """Catches reusing due-date and high-voltage-only field mappings."""
        self.assertIsNotNone(self.bill)
        self.assertEqual(self.bill.get("payment_info.payment_date"), "113/01/23")
        self.assertEqual(self.bill.get("charge_info.total_amount_twd"), 47039)
        self.assertEqual(self.bill.get("user_info.contract_capacity_kw"), 70)
        self.assertEqual(self.bill.get("user_info.max_demand_kw"), 71)
        self.assertEqual(self.bill.get("user_info.energy_consumption_kwh"), 12600)
        self.assertEqual(self.bill.get("user_info.power_factor_percent"), 100)


if __name__ == "__main__":
    unittest.main()
