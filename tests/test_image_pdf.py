import sys
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import taipower_bill_ocr


class ImagePdfIntegrationTests(unittest.TestCase):
    def test_ocr_is_scoped_to_detected_block_rectangles(self):
        """Catches returning to full-page OCR with cross-block field assignment."""
        bill = taipower_bill_ocr.extract_pdf(
            PROJECT_ROOT / "samples" / "high_voltage_asia_university_2024.pdf",
            page_numbers=[1],
            dpi=200,
        )[0]

        self.assertEqual(
            bill["quality"]["ocr_profile"], "blockwise_high_accuracy"
        )
        for block_key in (
            "payment_info",
            "user_info",
            "charge_info",
            "other_info",
        ):
            bounds = bill["blocks"][block_key]["bounds"]
            self.assertGreater(bounds["width"], 0)
            self.assertGreater(bounds["height"], 0)

        user_text = "".join(bill["blocks"]["user_info"]["raw_lines"])
        charge_text = "".join(bill["blocks"]["charge_info"]["raw_lines"])
        self.assertNotIn("流動電費", user_text)
        self.assertNotIn("契約容量", charge_text)

    def test_extracts_vertically_stacked_payment_summary_fields(self):
        """Catches leaving account number and due date only in raw OCR lines."""
        bill = taipower_bill_ocr.extract_pdf(
            PROJECT_ROOT / "samples" / "high_voltage_asia_university_2024.pdf",
            page_numbers=[1],
            dpi=200,
        )[0]

        self.assertEqual(
            bill.get("payment_info.customer_number"), "17-94-2403-50-4"
        )
        self.assertEqual(bill.get("payment_info.due_date"), "113/02/20")
        self.assertEqual(bill.get("payment_info.total_amount_twd"), 2068925)

    def test_extracts_high_voltage_scan_without_a_text_layer(self):
        """Catches rejecting scanned PDFs or depending on extractable PDF text."""
        extract_pdf = getattr(taipower_bill_ocr, "extract_pdf", None)
        self.assertIsNotNone(extract_pdf, "extract_pdf API is missing")

        started = time.perf_counter()
        bills = extract_pdf(
            PROJECT_ROOT / "samples" / "high_voltage_asia_university_2024.pdf",
            page_numbers=[1],
            dpi=200,
        )
        elapsed = time.perf_counter() - started

        self.assertEqual(len(bills), 1)
        self.assertEqual(bills[0].get("user_info.tariff_type"), "高壓電力綜合非營業用")
        self.assertEqual(bills[0].get("user_info.contract_capacity_kw"), 3800)
        self.assertEqual(bills[0].get("user_info.energy_consumption.peak_kwh"), 0)
        self.assertEqual(bills[0].get("charge_info.basic_charge_twd"), 634220.0)
        self.assertEqual(bills[0].get("charge_info.energy_charge_twd"), 1531108.8)
        self.assertEqual(bills[0].get("charge_info.discount_twd"), -64959.8)
        self.assertEqual(
            bills[0].get("charge_info.power_factor_adjustment_twd"), -31505.5
        )
        self.assertEqual(bills[0].get("charge_info.total_amount_twd"), 2068925)
        self.assertEqual(bills[0]["quality"]["source"], "image_pdf")
        self.assertEqual(bills[0]["quality"]["page_number"], 1)
        self.assertLess(elapsed, 12, f"one scanned page took {elapsed:.1f}s")

    def test_keeps_negative_adjustments_across_high_voltage_months(self):
        """Catches dropping a minus sign when charge-column OCR candidates disagree."""
        bills = taipower_bill_ocr.extract_pdf(
            PROJECT_ROOT / "samples" / "high_voltage_asia_university_2024.pdf",
            page_numbers=[2, 3],
            dpi=200,
        )

        expected = [
            (1542127.6, -62602.0, -31706.1, 2082101),
            (2761198.8, -96510.7, -49483.6, 3249486),
        ]
        actual = [
            (
                bill.get("charge_info.energy_charge_twd"),
                bill.get("charge_info.discount_twd"),
                bill.get("charge_info.power_factor_adjustment_twd"),
                bill.get("charge_info.total_amount_twd"),
            )
            for bill in bills
        ]
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
