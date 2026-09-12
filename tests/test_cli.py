import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CommandLineTests(unittest.TestCase):
    def test_prints_machine_readable_utf8_json(self):
        """Catches offering structured extraction only as an in-process API."""
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        env["PYTHONIOENCODING"] = "utf-8"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "taipower_bill_ocr",
                str(PROJECT_ROOT / "samples" / "summer_2021.jpg"),
            ],
            cwd=PROJECT_ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["charge_info"]["total_amount_twd"], 2014)

    def test_accepts_selected_pages_from_an_image_pdf(self):
        """Catches sending a scanned PDF directly to Pillow instead of rendering it."""
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        env["PYTHONIOENCODING"] = "utf-8"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "taipower_bill_ocr",
                str(
                    PROJECT_ROOT
                    / "samples"
                    / "high_voltage_asia_university_2024.pdf"
                ),
                "--pages",
                "1",
            ],
            cwd=PROJECT_ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result[0]["charge_info"]["total_amount_twd"], 2068925)


if __name__ == "__main__":
    unittest.main()
