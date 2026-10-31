import unittest
from pathlib import Path
from unittest import mock

import pdfplumber

from taipower_bill_ocr import batch, pdf_text
from taipower_bill_ocr.excel_export import bill_to_record


def word(text, x, y, height=10.0):
    return {"text": text, "x0": x, "x1": x + height * len(text), "top": y, "bottom": y + height}


# A made-up bill on the e-bill site's table layout.
TABLE_PAGE = [
    word("基本資料", 120, 10), word("計費內容", 330, 40),
    word("10512345678", 180, 40), word("電號:", 50, 49),
    word("基本電費(約定):", 290, 60), word("400,000.0元", 400, 60),
    word("流動電費:", 290, 72), word("100,000.0元", 400, 72),
    word("功率因數調整費:", 290, 84), word("-5,000.0元", 400, 84),
    word("稅前應繳總金額:", 290, 96), word("500,000.0元", 400, 96),
    word("營業稅:", 290, 108), word("25,000.0元", 400, 108),
    word("應繳總金額:", 290, 120), word("525,000元", 400, 120),
    word("計費期間:", 50, 104), word("115年07月01日", 180, 98), word("至", 300 - 60, 98),
    word("115年07月31日", 180, 110),
    word("用電資料", 120, 300), word("電表資料", 330, 300),
    word("計費度數", 50, 335), word("尖峰度數:", 110, 320), word("1000", 200, 320),
    word("週六半尖峰度數:", 110, 332), word("200", 270, 332),
    word("離峰度數:", 110, 344), word("3000", 200, 344),
    word("轉供度數", 50, 375), word("尖峰度數:", 110, 360), word("400", 200, 360),
    word("離峰度數:", 110, 372), word("500", 200, 372),
]


def pdf_of(words):
    page = mock.Mock()
    page.extract_words.return_value = words
    pdf = mock.MagicMock()
    pdf.__enter__.return_value.pages = [page]
    return mock.patch.object(pdfplumber, "open", return_value=pdf)


class LinesOfTests(unittest.TestCase):
    def test_widely_spaced_words_are_one_line_and_a_far_figure_another(self):
        words = [word(char, 10 + index * 14, 50) for index, char in enumerate("經常(尖峰)度數")] + [word("912", 300, 50)]

        lines = pdf_text.lines_of(words)

        self.assertEqual([line.text for line in lines], ["經常(尖峰)度數", "912"])

    def test_rows_stay_apart_and_positions_scale(self):
        lines = pdf_text.lines_of([word("離峰度數", 10, 80), word("尖峰度數", 10, 50)], 2.0)

        self.assertEqual([line.text for line in lines], ["尖峰度數", "離峰度數"])
        self.assertEqual((lines[0].x, lines[0].y), (20.0, 100.0))


class ReadPageTests(unittest.TestCase):
    def test_a_page_with_no_questions_comes_from_its_text(self):
        with pdf_of(TABLE_PAGE):
            bill = pdf_text.read_page(Path("a.pdf"), 1, 200)

        row = bill_to_record(bill, "a.pdf").row
        self.assertEqual(bill.get("quality.source"), "pdf_text")
        self.assertEqual(row["Custom No. 電號"], "10-51-2345-67-8")
        self.assertEqual(row["計費度數_尖峰度數"], 1000)
        self.assertEqual(row["轉供度數_離峰度數"], 500)
        self.assertEqual(row["應繳總金額"], 525000)
        self.assertEqual(row["頁碼"], 1)

    def test_a_scan_has_no_text_to_go_by(self):
        with pdf_of([]):
            self.assertIsNone(pdf_text.read_page(Path("a.pdf"), 1, 200))

    def test_totals_that_disagree_go_to_the_models(self):
        words = [word("999,999元", 400, 120) if item["text"] == "525,000元" else item for item in TABLE_PAGE]
        with pdf_of(words):
            self.assertIsNone(pdf_text.read_page(Path("a.pdf"), 1, 200))

    def test_a_peak_row_missing_goes_to_the_models(self):
        words = [item for item in TABLE_PAGE if item["text"] != "1000"]
        with pdf_of(words):
            self.assertIsNone(pdf_text.read_page(Path("a.pdf"), 1, 200))

    def test_text_that_cannot_be_opened_goes_to_the_models(self):
        with mock.patch.object(pdfplumber, "open", side_effect=ValueError("broken")):
            self.assertIsNone(pdf_text.read_page(Path("a.pdf"), 1, 200))


class ExtractorTests(unittest.TestCase):
    def test_no_model_is_loaded_when_the_text_will_do(self):
        extractor = batch.PageExtractor("onnx")
        with pdf_of(TABLE_PAGE), mock.patch.object(extractor, "_engine") as engine:
            bill = extractor.extract(Path("a.pdf"), 1)

        engine.assert_not_called()
        self.assertEqual(bill.get("quality.source"), "pdf_text")

    def test_the_models_read_what_the_text_cannot(self):
        extractor = batch.PageExtractor("onnx")
        read = mock.Mock(return_value="read by a model")
        with pdf_of([]), mock.patch.object(extractor, "_engine"), mock.patch.object(extractor, "_read_with", read), \
                mock.patch.object(batch, "has_bill_values", return_value=True):
            self.assertEqual(extractor.extract(Path("a.pdf"), 1), "read by a model")

    def test_images_never_look_for_text(self):
        extractor = batch.PageExtractor("onnx")
        read = mock.Mock(return_value="read by a model")
        with mock.patch.object(pdf_text, "read_page") as text, mock.patch.object(extractor, "_engine"), \
                mock.patch.object(extractor, "_read_with", read), mock.patch.object(batch, "has_bill_values", return_value=True):
            extractor.extract(Path("a.png"), None)

        text.assert_not_called()


if __name__ == "__main__":
    unittest.main()
