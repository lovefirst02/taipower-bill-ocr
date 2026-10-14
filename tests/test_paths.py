import tempfile
import unittest
from pathlib import Path
from unittest import mock

from taipower_bill_ocr import paths


class PaddleReadablePathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.model = self.root / "桌面" / "models" / "PP-OCRv6_small_det"
        self.model.mkdir(parents=True)
        (self.model / "inference.json").write_text("{}", encoding="utf-8")
        (self.model / "inference.pdiparams").write_bytes(b"\0" * 64)

    def tearDown(self):
        self.temp.cleanup()

    def test_ascii_path_is_used_as_is(self):
        ascii_model = self.root / "models"
        self.assertEqual(paths.paddle_readable(ascii_model), ascii_model)

    def test_short_path_is_used_when_the_drive_has_one(self):
        with mock.patch.object(paths, "short_path", return_value=r"C:\Users\X\DESKTO~1\models\PP-OCR~1"):
            self.assertEqual(paths.paddle_readable(self.model), Path(r"C:\Users\X\DESKTO~1\models\PP-OCR~1"))

    def test_models_are_copied_to_an_ascii_folder_otherwise(self):
        target = self.root / "ascii"
        target.mkdir()
        with mock.patch.object(paths, "short_path", return_value=None), \
             mock.patch.object(paths, "ascii_dir", return_value=target):
            copy = paths.paddle_readable(self.model)
            self.assertEqual(copy, target / "PP-OCRv6_small_det")
            self.assertEqual((copy / "inference.pdiparams").read_bytes(), b"\0" * 64)
            # Already copied: not copied again.
            with mock.patch.object(paths.shutil, "copytree") as copytree:
                self.assertEqual(paths.paddle_readable(self.model), copy)
                copytree.assert_not_called()

    def test_result_is_always_an_ascii_path_with_the_models(self):
        # Whatever this drive supports (short names or not), Paddle gets ASCII.
        target = self.root / "ascii"
        target.mkdir()
        with mock.patch.object(paths, "ascii_dir", return_value=target):
            readable = paths.paddle_readable(self.model)
        self.assertTrue(str(readable).isascii())
        self.assertTrue((readable / "inference.json").is_file())

if __name__ == "__main__":
    unittest.main()
