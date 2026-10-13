import unittest

from PIL import Image

from taipower_bill_ocr.paddle_ocr import _labels_missing_values, _retry_missing_rows
from taipower_bill_ocr.parser import OcrLine


class FakeResult:
    def __init__(self, texts, boxes):
        self.json = {"res": {"rec_texts": texts, "rec_scores": [0.99] * len(texts), "rec_boxes": boxes}}


class FakeRowEngine:
    """Returns queued results, one per predict() call, recording each input."""

    def __init__(self, *results):
        self.results = list(results)
        self.inputs = []

    def predict(self, image):
        self.inputs.append(image)
        texts, boxes = self.results.pop(0) if self.results else ([], [])
        return [FakeResult(texts, boxes)]


BOUNDS = {"charge_info": {"x": 90, "y": 0, "width": 500, "height": 400}}


def label(text, x=100, y=50, block="charge_info"):
    return OcrLine(text, x, y, 150, 30, block)


class MissingValueDetectionTests(unittest.TestCase):
    def test_label_without_value_on_its_row_is_missing(self):
        lines = [label("基本電費(約定)"), label("流動電費", y=90), OcrLine("2739373.0", 420, 92, 120, 28, "charge_info")]
        self.assertEqual([line.text for line in _labels_missing_values(lines)], ["基本電費(約定)"])

    def test_inline_value_counts_as_present(self):
        self.assertEqual(_labels_missing_values([label("基本電費：634220.0元")]), [])

    def test_payment_strip_is_not_retried(self):
        # Its values are printed below the labels, not beside them.
        self.assertEqual(_labels_missing_values([label("應繳總金額", block="payment_info")]), [])

    def test_misread_labels_are_still_retried(self):
        lines = [label("流勤電费"), label("基本電费(约定)", y=90)]
        self.assertEqual(len(_labels_missing_values(lines)), 2)

    def test_unknown_labels_are_ignored(self):
        self.assertEqual(_labels_missing_values([label("營業稅")]), [])


class RowRetryTests(unittest.TestCase):
    def setUp(self):
        self.image = Image.new("RGB", (700, 400), "white")

    def test_recovered_value_is_added_in_page_coordinates(self):
        lines = [label("基本電費(約定)")]
        engine = FakeRowEngine((["基本電費(約定)", "849680.0元"], [[4, 6, 150, 36], [320, 6, 440, 36]]))
        recovered = _retry_missing_rows(self.image, lines, BOUNDS, engine=engine, score_threshold=0.45)

        self.assertEqual(recovered, ["基本電費(約定)"])
        added = lines[-1]
        self.assertEqual(added.text, "849680.0元")
        self.assertEqual((added.x, added.y, added.block_key), (96 + 320, 44 + 6, "charge_info"))
        self.assertEqual(len(lines), 2)  # the re-read label is not duplicated
        # The crop spans from the label to the block's right edge, as BGR.
        crop = engine.inputs[0]
        self.assertEqual(crop.shape[1], 90 + 500 - 96)
        self.assertEqual(crop.shape[2], 3)

    def test_grayscale_is_tried_when_colour_finds_nothing(self):
        lines = [label("功率因數調整費")]
        engine = FakeRowEngine(([], []), (["-56853.5元"], [[300, 6, 420, 36]]))
        recovered = _retry_missing_rows(self.image, lines, BOUNDS, engine=engine, score_threshold=0.45)

        self.assertEqual(recovered, ["功率因數調整費"])
        self.assertEqual(len(engine.inputs), 2)
        grayscale = engine.inputs[1]
        self.assertTrue((grayscale[:, :, 0] == grayscale[:, :, 1]).all())

    def test_nothing_numeric_found_leaves_lines_unchanged(self):
        lines = [label("流動電費")]
        engine = FakeRowEngine((["流動電費"], [[4, 6, 150, 36]]), (["元"], [[300, 6, 330, 36]]))
        self.assertEqual(
            _retry_missing_rows(self.image, lines, BOUNDS, engine=engine, score_threshold=0.45), []
        )
        self.assertEqual(len(lines), 1)


if __name__ == "__main__":
    unittest.main()
