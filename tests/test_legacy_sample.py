import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from taipower_bill_ocr import extract_bill


class LegacyBillIntegrationTests(unittest.TestCase):
    def test_builds_virtual_blocks_for_pre_block_layout(self):
        """Catches treating an old headerless bill as one giant charge block."""
        result = extract_bill(PROJECT_ROOT / "samples" / "legacy_2013.jpg")

        self.assertEqual(result.get("user_info.tariff_type"), "表燈非營業用")
        self.assertEqual(result.get("user_info.business_tax_id"), "04860092")
        self.assertEqual(result.get("charge_info.energy_charge_twd"), 2246.5)
        self.assertEqual(result.get("charge_info.total_amount_twd"), 2022)
        self.assertEqual(result.get("other_info.outage_group"), "B")
        self.assertEqual(result.get("other_info.feeder_code"), "wx68")
        self.assertIn("legacy_virtual_blocks", result["quality"]["warnings"])


if __name__ == "__main__":
    unittest.main()
