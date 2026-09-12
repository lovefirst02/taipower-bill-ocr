import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from taipower_bill_ocr import extract_bill


class HighVoltageBillIntegrationTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        sample = PROJECT_ROOT / "samples" / "high_voltage_2024_page01.png"
        cls.result = extract_bill(sample)

    def test_extracts_fields_unique_to_high_voltage_time_of_use_bill(self):
        """Catches treating an industrial time-of-use bill as a residential bill."""
        expected = {
            "user_info.tariff_type": "高壓電力綜合非營業用",
            "user_info.time_type": "三段式時間電價",
            "user_info.contract_capacity_kw": 3800,
            "user_info.max_demand.semi_peak_non_summer_kw": 1859,
            "user_info.max_demand.saturday_semi_peak_kw": 1164,
            "user_info.max_demand.off_peak_kw": 1883,
            "user_info.energy_consumption.peak_kwh": 0,
            "user_info.energy_consumption.semi_peak_kwh": 313152,
            "user_info.energy_consumption.saturday_semi_peak_kwh": 41192,
            "user_info.energy_consumption.off_peak_kwh": 274776,
            "user_info.energy_consumption.discount_kwh": 629120,
            "user_info.power_factor_percent": 98,
            "charge_info.basic_charge_twd": 634220.0,
            "charge_info.energy_charge_twd": 1531108.8,
            "charge_info.discount_twd": -64959.8,
            "charge_info.power_factor_adjustment_twd": -31505.5,
            "charge_info.ebill_discount_twd": -10.0,
            "charge_info.meter_rental_twd": 71.0,
            "charge_info.pretax_total_twd": 1970405.0,
            "charge_info.business_tax_twd": 98520.0,
            "charge_info.total_amount_twd": 2068925,
        }

        actual = {path: self.result.get(path) for path in expected}
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
