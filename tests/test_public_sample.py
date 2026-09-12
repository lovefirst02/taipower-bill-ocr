import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from taipower_bill_ocr import extract_bill


class PublicBillIntegrationTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        sample = PROJECT_ROOT / "samples" / "public_commons_2024.jpg"
        cls.result = extract_bill(sample)

    def test_extracts_verified_fields_from_2024_public_bill(self):
        """Catches regressions against a hand-checked, CC-licensed real bill."""
        expected = {
            "user_info.tariff_type": "表燈非營業用",
            "user_info.time_type": "非時間電價",
            "user_info.base_reading": 40,
            "user_info.energy_consumption_kwh": 610,
            "user_info.shared_households": 434,
            "user_info.business_tax_id": "00423107",
            "user_info.billing_period.start": "113/02/06",
            "user_info.billing_period.end": "113/04/09",
            "user_info.current_reading_date": "113/04/10",
            "user_info.current_charge_date": "113/04/16",
            "user_info.next_reading_date": "113/06/11",
            "user_info.next_charge_date": "113/06/19",
            "charge_info.energy_charge_twd": 1173.0,
            "charge_info.shared_public_charge_twd": 920.7,
            "charge_info.total_amount_twd": 2094,
            "other_info.feeder_code": "CY36",
            "other_info.fuel_cost_per_kwh_twd": 2.6639,
            "other_info.carbon_emission_kg": 302,
            "other_info.renewable_fund_per_kwh_twd": 0.0023,
            "invoice_info.invoice_number": "WG-73787182",
            "invoice_info.amount_twd": 1467,
        }
        actual = {path: self.result.get(path) for path in expected}
        self.assertEqual(actual, expected)

    def test_exposes_each_blocks_fields_as_plain_dictionary(self):
        """Catches wrapping field values in objects that are awkward to consume."""
        fields = self.result["blocks"]["user_info"]["fields"]

        self.assertIs(type(fields), dict)
        self.assertEqual(
            fields,
            {
                "tariff_type": "表燈非營業用",
                "time_type": "非時間電價",
                "base_reading": 40,
                "energy_consumption_kwh": 610,
                "shared_households": 434,
                "business_tax_id": "00423107",
                "billing_period": {"start": "113/02/06", "end": "113/04/09"},
                "current_reading_date": "113/04/10",
                "current_charge_date": "113/04/16",
                "next_reading_date": "113/06/11",
                "next_charge_date": "113/06/19",
            },
        )


if __name__ == "__main__":
    unittest.main()
