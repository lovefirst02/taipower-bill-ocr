import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from taipower_bill_ocr.parser import parse_bill_layout
from taipower_bill_ocr.windows_ocr import WindowsOcrEngine


class WindowsOcrIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sample = PROJECT_ROOT / "samples" / "user_sample_2021.jpg"
        cls.lines = WindowsOcrEngine(language="zh-Hant").recognize(sample)

    def test_recognizes_section_titles_with_coordinates_from_user_sample(self):
        """Catches returning plain OCR text without usable layout coordinates."""
        combined = "\n".join(line.text for line in self.lines)
        self.assertIn("用戶資訊", combined)
        self.assertIn("計費內容", combined)
        self.assertTrue(
            all(line.width > 0 and line.height > 0 for line in self.lines)
        )

    def test_detects_every_named_block_in_user_sample(self):
        """Catches silently omitting a colored section when its tiny title is fuzzy."""
        result = parse_bill_layout(self.lines)

        expected = {
            "user_info",
            "charge_info",
            "payment_info",
            "other_info",
            "charge_introduction",
            "energy_saving_info",
            "meter_info",
        }
        self.assertTrue(expected.issubset(result["blocks"]), result["blocks"].keys())


if __name__ == "__main__":
    unittest.main()
