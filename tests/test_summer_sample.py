import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from taipower_bill_ocr import extract_bill


class SummerResidentialBillIntegrationTests(unittest.TestCase):
    def test_detects_exact_f66a75_header_bars(self):
        """Catches excluding bright coral headers at both color-mask boundaries."""
        source_path = PROJECT_ROOT / "samples" / "summer_2021.jpg"
        with Image.open(source_path) as source, tempfile.TemporaryDirectory() as temp_dir:
            recolored = source.convert("RGB")
            draw = ImageDraw.Draw(recolored)
            draw.rectangle((25, 2, 367, 55), fill="#F66A75")
            draw.rectangle((373, 2, 723, 55), fill="#F66A75")
            font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 24)
            draw.text((42, 14), "Basic Info.", fill="white", font=font)
            draw.text((390, 14), "Charge Info.", fill="white", font=font)
            sample_path = Path(temp_dir) / "coral-bill.png"
            recolored.save(sample_path)

            result = extract_bill(
                sample_path,
                ocr_profile="blockwise_high_accuracy",
            )

        self.assertNotEqual(
            result["quality"].get("block_detection"), "full_page_fallback"
        )
        self.assertIn("bounds", result["blocks"]["user_info"])
        self.assertIn("bounds", result["blocks"]["charge_info"])
        self.assertEqual(result.get("user_info.energy_consumption_kwh"), 858)
        self.assertEqual(result.get("charge_info.total_amount_twd"), 2014)

    def test_scopes_non_purple_headers_into_independent_blocks(self):
        """Catches falling back to full-page OCR when title bars are blue-green."""
        result = extract_bill(
            PROJECT_ROOT / "samples" / "summer_2021.jpg",
            ocr_profile="blockwise_high_accuracy",
        )

        self.assertEqual(result["quality"].get("block_detection"), "color_independent")
        self.assertIn("bounds", result["blocks"]["user_info"])
        self.assertIn("bounds", result["blocks"]["charge_info"])
        self.assertEqual(result.get("user_info.energy_consumption_kwh"), 858)
        self.assertEqual(result.get("charge_info.energy_charge_twd"), 2013.8)
        self.assertEqual(result.get("charge_info.total_amount_twd"), 2014)

    def test_recovers_total_when_ocr_drops_the_leading_digit(self):
        """Catches accepting an impossible total below the bill's energy charge."""
        result = extract_bill(PROJECT_ROOT / "samples" / "summer_2021.jpg")

        self.assertEqual(result.get("user_info.tariff_type"), "表燈非營業用")
        self.assertEqual(result.get("user_info.energy_consumption_kwh"), 858)
        self.assertEqual(result.get("charge_info.energy_charge_twd"), 2013.8)
        self.assertEqual(result.get("charge_info.total_amount_twd"), 2014)
        self.assertIn("total_reconstructed_from_charge", result["quality"]["warnings"])


if __name__ == "__main__":
    unittest.main()
