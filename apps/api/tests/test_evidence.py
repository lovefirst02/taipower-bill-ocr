import io
import unittest

from PIL import Image

from taipower_bill_ocr.field_locator import FieldLocation
from taipower_web_api.evidence import _overlap, default_marks, draw_png, find_usage, label_size, pdf_bytes

PAGE = (1654, 2339)
CHARGE_BLOCK = (693, 634, 518, 1188)
USER_BLOCK = (96, 634, 559, 1188)
PRETAX_ROW = (706, 887, 490, 41)
ROW = {"稅前應繳總金額": 1970405, "應繳總金額": 2068925, "Billing Period 計費期間": "113/01/01~113/01/31"}


def labels(marks):
    return {mark["text"]: mark for mark in marks if mark["type"] == "label"}


def label_box(mark):
    width, height = label_size(mark["text"], mark["fontSize"])
    return (mark["x"], mark["y"], width, height)


class LabelPlacementTests(unittest.TestCase):
    def test_label_avoids_printed_text(self):
        # Text printed right of and above the row: the label must go elsewhere.
        printed = [(1215, 870, 420, 80), (706, 840, 490, 40)]
        marks, _ = default_marks({"稅前應繳總金額": FieldLocation(PRETAX_ROW, True)}, PAGE, ROW, printed)
        box = label_box(labels(marks)["Grey Electricity Cost"])
        self.assertTrue(all(_overlap(box, text) == 0 for text in printed))

    def test_label_never_covers_its_highlight(self):
        marks, _ = default_marks({"稅前應繳總金額": FieldLocation(PRETAX_ROW, True)}, PAGE, ROW)
        self.assertEqual(_overlap(label_box(labels(marks)["Grey Electricity Cost"]), PRETAX_ROW), 0)

    def test_label_stays_inside_a_block_rather_than_in_the_gap_between_blocks(self):
        marks, _ = default_marks(
            {"稅前應繳總金額": FieldLocation(PRETAX_ROW, True)}, PAGE, ROW, regions=[CHARGE_BLOCK, USER_BLOCK]
        )
        box = label_box(labels(marks)["Grey Electricity Cost"])
        self.assertEqual(_overlap(box, CHARGE_BLOCK), box[2] * box[3])

    def test_two_labels_do_not_overlap_each_other(self):
        marks, _ = default_marks(
            {
                "稅前應繳總金額": FieldLocation(PRETAX_ROW, True),
                "Billing Period 計費期間": FieldLocation((706, 935, 490, 41), True),
            },
            PAGE,
            ROW,
        )
        placed = labels(marks)
        self.assertEqual(_overlap(label_box(placed["Billing period"]), label_box(placed["Grey Electricity Cost"])), 0)


class RenderingTests(unittest.TestCase):
    def test_png_has_yellow_highlight_and_red_text(self):
        image = Image.new("RGB", (800, 400), "white")
        marks = [
            {"id": "a", "type": "highlight", "x": 100, "y": 100, "w": 200, "h": 40},
            {"id": "b", "type": "label", "x": 350, "y": 100, "text": "Billing period", "fontSize": 40},
        ]
        result = draw_png(image, marks)
        red, green, blue = result.getpixel((200, 120))
        self.assertTrue(red > 240 and green > 230 and blue < 200)  # translucent yellow
        reds = [pixel for pixel in result.crop((350, 100, 650, 150)).getdata() if pixel[0] > 150 and pixel[1] < 90]
        self.assertGreater(len(reds), 50)  # red bold text

    def test_labels_use_a_plain_bold_font_with_cjk_fallback(self):
        from taipower_web_api.evidence import label_font_path

        latin, chinese = label_font_path("Grey Electricity Cost"), label_font_path("灰電費用")
        if latin is None:
            self.skipTest("Windows fonts not available")
        self.assertEqual(latin.name.lower(), "arialbd.ttf")
        self.assertEqual(chinese.name.lower(), "msjhbd.ttc")
        image = Image.new("RGB", (600, 200), "white")
        marked = draw_png(image, [{"id": "c", "type": "label", "x": 20, "y": 40, "text": "灰電費用", "fontSize": 60}])
        reds = [pixel for pixel in marked.getdata() if pixel[0] > 150 and pixel[1] < 90]
        self.assertGreater(len(reds), 300)  # real glyphs, not empty boxes

    def test_image_upload_gets_a_pdf_with_the_marked_page(self):
        from pypdf import PdfReader

        image = Image.new("RGB", (1654, 2339), "white")
        content = pdf_bytes(__import__("pathlib").Path("bill.png"), None, image, 200, [])
        page = PdfReader(io.BytesIO(content)).pages[0]
        self.assertAlmostEqual(float(page.mediabox.width), 1654 * 72 / 200, places=1)


def line(text, x, y, w=60, h=30):
    return {"text": text, "x": x, "y": y, "width": w, "height": h, "block": "energy_saving_info"}


class UsageTests(unittest.TestCase):
    LINES = [line("度數", 1261, 114), line("本期", 860, 147), line("31", 1086, 150), line("15,000", 1254, 150), line("去年同期", 862, 186), line("20000", 1254, 186)]

    def test_value_read_into_the_table_is_matched_on_the_row(self):
        table = [
            {"table": "comparison_rows", "row": 1, "column": "period", "value": "current"},
            {"table": "comparison_rows", "row": 1, "column": "energy_kwh", "value": 15000},
        ]
        box, exact = find_usage({"ocr_lines": self.LINES, "table_rows": table})
        self.assertTrue(exact)
        self.assertLessEqual(box[0], 860)
        self.assertGreaterEqual(box[0] + box[2], 1254 + 60)
        self.assertLess(box[3], 50)  # 去年同期 below is not included

    def test_value_under_the_heading_is_used_when_the_table_was_not_read(self):
        box, exact = find_usage({"ocr_lines": self.LINES, "table_rows": []})
        self.assertTrue(exact)
        self.assertGreaterEqual(box[0] + box[2], 1254 + 60)

    def test_label_alone_when_no_value_is_beside_it(self):
        box, exact = find_usage({"ocr_lines": [line("本期", 860, 147)], "table_rows": []})
        self.assertFalse(exact)
        self.assertEqual(box, (860, 147, 60, 30))
        self.assertIsNone(find_usage({"ocr_lines": [line("本期碳排量", 860, 147)], "table_rows": []}))


if __name__ == "__main__":
    unittest.main()
