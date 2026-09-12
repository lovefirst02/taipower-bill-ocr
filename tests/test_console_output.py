import io
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

try:
    from taipower_bill_ocr.console import configure_utf8_output
except ImportError:
    configure_utf8_output = None


class ConsoleOutputTests(unittest.TestCase):
    def test_utf8_configuration_prints_characters_unsupported_by_cp950(self):
        """Catches Paddle output crashing pprint on a CP950 Windows console."""
        if configure_utf8_output is None:
            self.fail("UTF-8 console configuration has not been implemented")

        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding="cp950")
        configure_utf8_output(stream)

        print("電壓", file=stream)
        stream.flush()

        self.assertEqual(buffer.getvalue().decode("utf-8").splitlines(), ["電壓"])


if __name__ == "__main__":
    unittest.main()
