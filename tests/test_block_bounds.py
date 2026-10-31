import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

from taipower_bill_ocr import paddle_ocr


class WholePageEngine:
    """Reads the page once, as the ONNX engines do; a made-up bill."""

    reads_whole_page = True
    ROWS = [
        ("電價種類:", 40, 430), ("電力需量營業用", 470, 430),
        ("基本電費(約定)", 840, 440), ("100000.0元", 1280, 440),
        ("流動電費", 840, 475), ("20000.0元", 1280, 475),
        ("稅前應繳總金額", 840, 620), ("120000.0元", 1280, 620),
        ("繳費總金額", 840, 725), ("126,000元", 1280, 725),
    ]

    def predict(self, _page):
        return [{
            "rec_texts": [text for text, _, _ in self.ROWS],
            "rec_scores": [0.99] * len(self.ROWS),
            "rec_polys": [np.array([[x, y], [x + 200, y], [x + 200, y + 30], [x, y + 30]], dtype=float) for _, x, y in self.ROWS],
        }]


def read_with_blocks(blocks):
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "page.png"
        Image.new("RGB", (1654, 2339), "white").save(path)
        with mock.patch.object(paddle_ocr, "_paddle_block_bounds", return_value=(blocks, "unknown", "headers")):
            return paddle_ocr._recognize_paddle_blocks(path, engine=WholePageEngine(), language="chinese_cht", score_threshold=0.45)


class BlockBoundsTests(unittest.TestCase):
    def test_blocks_too_narrow_do_not_lose_what_the_page_read(self):
        narrow = {"user_info": (40, 350, 400, 1200), "charge_info": (1100, 350, 1500, 1200)}

        lines, bounds, _, _, method = read_with_blocks(narrow)

        self.assertEqual(method, "blocks_lost_values")
        self.assertEqual(bounds, {})
        self.assertIn("基本電費(約定)", [line.text for line in lines])

    def test_blocks_that_lose_nothing_are_kept(self):
        wide = {"user_info": (30, 350, 800, 1200), "charge_info": (820, 350, 1600, 1200)}

        lines, bounds, _, _, method = read_with_blocks(wide)

        self.assertEqual(method, "headers")
        self.assertEqual(set(bounds), {"user_info", "charge_info"})
        self.assertEqual({line.block_key for line in lines if line.text == "基本電費(約定)"}, {"charge_info"})


if __name__ == "__main__":
    unittest.main()
