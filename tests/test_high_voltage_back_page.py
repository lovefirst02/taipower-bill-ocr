import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from taipower_bill_ocr import extract_pdf


class HighVoltageBackPageIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.front_bill, cls.bill = extract_pdf(
            PROJECT_ROOT
            / "samples"
            / "official_high_voltage_2021_two_page.pdf",
            page_numbers=[1, 2],
            dpi=250,
        )

    def test_reconstructs_billing_period_across_current_and_next_rows(self):
        """Catches requiring both period dates to appear on one OCR row."""
        self.assertEqual(
            self.front_bill.get("user_info.billing_period"),
            {"start": "109/12/01", "end": "109/12/31"},
        )

    def test_auto_classification_keeps_high_voltage_on_its_existing_path(self):
        """Catches routing a high-voltage notice through bank-receipt rules."""
        self.assertEqual(self.front_bill.get("document_type"), "high_voltage_bill")

    def test_auto_classifies_a_headerless_high_voltage_back_page(self):
        """Catches requiring every page to repeat the high-voltage document title."""
        self.assertEqual(self.bill.get("document_type"), "high_voltage_bill")

    def test_reconstructs_unruled_energy_saving_table(self):
        """Catches flattening the energy comparison table into unrelated lines."""
        self.assertEqual(
            self.bill.get("energy_saving_info.comparison_rows"),
            [
                {
                    "period": "current",
                    "days": 31,
                    "energy_kwh": 15000,
                    "daily_average_kwh": 483.87,
                },
                {
                    "period": "same_period_last_year",
                    "days": 31,
                    "energy_kwh": 20000,
                    "daily_average_kwh": 645.16,
                },
                {
                    "period": "next_period_last_year",
                    "days": 30,
                    "energy_kwh": 30000,
                    "daily_average_kwh": 1000.0,
                },
            ],
        )

    def test_reconstructs_unruled_meter_reading_columns(self):
        """Catches shifting readings left when the two middle cells are blank."""
        self.assertEqual(self.bill.get("meter_info.customer_number"), "00123456789")
        self.assertEqual(self.bill.get("meter_info.billing_period_code"), "11001")
        self.assertEqual(self.bill.get("meter_info.group_code"), "01")
        self.assertEqual(self.bill.get("meter_info.group_name"), "經常")
        self.assertEqual(self.bill.get("meter_info.meter_number"), "012345678")
        self.assertEqual(self.bill.get("meter_info.multiplier"), 600)
        self.assertEqual(
            self.bill.get("meter_info.readings"),
            [
                {"meter_type": "01", "previous": "03000.7", "removed": None, "installed": None, "current": "04000.0"},
                {"meter_type": "03", "previous": "05200.4", "removed": None, "installed": None, "current": "05300.6"},
                {"meter_type": "04", "previous": "03500.9", "removed": None, "installed": None, "current": "03600.3"},
                {"meter_type": "06", "previous": "018.000", "removed": None, "installed": None, "current": "018.000"},
                {"meter_type": "08", "previous": "018.000", "removed": None, "installed": None, "current": "018.000"},
                {"meter_type": "09", "previous": "00700.5", "removed": None, "installed": None, "current": "00700.5"},
                {"meter_type": "10", "previous": "007.100", "removed": None, "installed": None, "current": "007.100"},
                {"meter_type": "11", "previous": "00900.4", "removed": None, "installed": None, "current": "00960.8"},
                {"meter_type": "12", "previous": "017.000", "removed": None, "installed": None, "current": "018.000"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
