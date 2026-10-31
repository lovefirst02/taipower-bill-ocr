import unittest
from pathlib import Path
from unittest import mock

import pdfplumber

from taipower_bill_ocr import batch, pdf_text
from taipower_bill_ocr.excel_export import bill_to_record
from taipower_bill_ocr.parser import BillResult, OcrLine, _join_wrapped_period, parse_bill_layout


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


def pdf_of(words, walls=None):
    page = mock.Mock()
    page.extract_words.return_value = words
    page.edges, page.width, page.height = walls or [], 595, 842
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

    def test_characters_stored_as_kangxi_radicals_read_as_the_characters_they_look_like(self):
        radicals = {"用": "⽤", "金": "⾦", "月": "⽉", "日": "⽇", "至": "⾄", "戶": "⼾"}
        words = [{**item, "text": "".join(radicals.get(char, char) for char in item["text"])} for item in TABLE_PAGE]
        self.assertNotEqual(words, TABLE_PAGE)
        notes = {}
        with pdf_of(words):
            bill = pdf_text.read_page(Path("a.pdf"), 1, 200, notes)

        row = bill_to_record(bill, "a.pdf").row
        self.assertEqual(notes.get("missing"), None)
        self.assertEqual(row["Billing Period 計費期間"], "115/07/01~115/07/31")
        self.assertEqual(row["Custom No. 電號"], "10-51-2345-67-8")
        self.assertEqual(row["計費度數_尖峰度數"], 1000)
        self.assertEqual(row["稅前應繳總金額"], 500000)

    def test_a_scan_has_no_text_to_go_by(self):
        with pdf_of([]):
            self.assertIsNone(pdf_text.read_page(Path("a.pdf"), 1, 200))

    def test_totals_that_disagree_go_to_the_models(self):
        words = [word("999,999元", 400, 120) if item["text"] == "525,000元" else item for item in TABLE_PAGE]
        with pdf_of(words):
            self.assertIsNone(pdf_text.read_page(Path("a.pdf"), 1, 200))

    def test_a_gap_does_not_throw_away_what_the_text_says(self):
        notes = {}
        with pdf_of([item for item in TABLE_PAGE if item["text"] != "1000"]):
            bill = pdf_text.read_page(Path("a.pdf"), 1, 200, notes)

        self.assertEqual(bill_to_record(bill, "a.pdf").row["應繳總金額"], 525000)
        self.assertIn("缺少計費度數_尖峰度數", notes["missing"])

    def test_no_total_means_the_text_says_nothing_to_go_by(self):
        notes = {}
        with pdf_of([item for item in TABLE_PAGE if item["text"] != "525,000元"]):
            self.assertIsNone(pdf_text.read_page(Path("a.pdf"), 1, 200, notes))
        self.assertTrue(notes["refused"])

    def test_text_that_cannot_be_opened_goes_to_the_models(self):
        with mock.patch.object(pdfplumber, "open", side_effect=ValueError("broken")):
            self.assertIsNone(pdf_text.read_page(Path("a.pdf"), 1, 200))


class WrappedPeriodTests(unittest.TestCase):
    """A narrow cell breaks the period mid-year; the reading date sits right under it."""

    def period_of(self, *period_words, walls=None):
        words = [item for item in TABLE_PAGE if item["text"] not in ("115年07月01日", "至", "115年07月31日")]
        words += [word("本次抄表日:", 50, 134), word("115年08月01日", 120, 134), *period_words]
        notes = {}
        with pdf_of(words, walls):
            bill = pdf_text.read_page(Path("a.pdf"), 1, 200, notes)
        self.assertIsNotNone(bill, notes)
        return bill_to_record(bill, "a.pdf").row["Billing Period 計費期間"]

    def test_broken_inside_the_year(self):
        self.assertEqual(self.period_of(word("115年07月01日至11", 120, 98), word("5年07月31日", 120, 110)), "115/07/01~115/07/31")

    def test_with_the_rest_of_the_row_in_cells_of_its_own(self):
        period = self.period_of(word("115年07月01日", 120, 98), word("至", 235, 98), word("11", 258, 98), word("5年07月31日", 120, 110))
        self.assertEqual(period, "115/07/01~115/07/31")

    def test_filling_its_cell_up_to_the_next_columns_label(self):
        self.assertEqual(self.period_of(word("115年07月01日至11", 150, 98), word("5年07月31日", 150, 110)), "115/07/01~115/07/31")

    def test_a_rule_between_two_words_keeps_their_cells_apart(self):
        words = [word("電價", 10, 50), word("高壓", 31, 50)]
        rule = (30.0, 40.0, 70.0)
        self.assertEqual([line.text for line in pdf_text.lines_of(words)], ["電價高壓"])
        self.assertEqual([line.text for line in pdf_text.lines_of(words, 1.0, [rule])], ["電價", "高壓"])
        self.assertEqual([line.text for line in pdf_text.lines_of(words, 1.0, [(30.0, 80.0, 90.0)])], ["電價高壓"])

    def test_rules_running_through_the_text_are_no_guide(self):
        walls = [{"orientation": "v", "x0": 150.0, "top": 90.0, "bottom": 112.0}]
        self.assertEqual(self.period_of(word("115年07月01日至11", 120, 98), word("5年07月31日", 120, 110), walls=walls), "115/07/01~115/07/31")

    def test_the_reading_date_is_never_taken_for_its_end(self):
        lines = [
            OcrLine("計費期間:", 50, 104, 90, 12), OcrLine("115年07月01日", 180, 98, 120, 12),
            OcrLine("本次抄表日:", 50, 134, 90, 12), OcrLine("115年08月01日", 180, 134, 120, 12),
            OcrLine("基本資料", 120, 10, 80, 12), OcrLine("用電資料", 120, 300, 80, 12),
        ]
        self.assertIsNone(bill_to_record(parse_bill_layout(lines), "a.pdf").row["Billing Period 計費期間"])

    def test_the_year_lost_at_the_break_comes_from_the_start(self):
        lines = [OcrLine("115年07月01日至", 180, 98, 120, 12), OcrLine("5年07月31日", 180, 110, 80, 12)]
        self.assertEqual([line.text for line in _join_wrapped_period(lines)], ["115年07月01日至115年07月31日"])

    def test_the_row_above_the_reading_date_is_not_the_reading_date(self):
        lines = [
            OcrLine("115年07月01日至", 621, 429, 300, 38), OcrLine("5年07月31日", 620, 462, 200, 39),
            OcrLine("本次抄表日:", 172, 496, 140, 41), OcrLine("115年08月01日", 620, 498, 300, 40),
        ]
        self.assertEqual(_join_wrapped_period(lines)[0].text, "115年07月01日至115年07月31日")

    def test_a_period_across_new_year_is_not_given_the_starts_year(self):
        lines = [OcrLine("114年12月16日至", 180, 98, 120, 12), OcrLine("5年01月15日", 180, 110, 80, 12)]
        self.assertEqual(_join_wrapped_period(lines), lines)

    def test_an_end_not_read_never_becomes_the_reading_date(self):
        lines = [
            OcrLine("115年07月01日至", 180, 98, 120, 12),
            OcrLine("本次抄表日:", 50, 110, 90, 12), OcrLine("115年08月01日", 180, 110, 120, 12),
        ]
        self.assertEqual(_join_wrapped_period(lines), lines)
        self.assertIsNone(parse_bill_layout(lines).get("user_info.billing_period"))

    def test_two_dates_not_a_period_stay_apart(self):
        lines = [OcrLine("115年08月23日", 180, 98, 120, 12), OcrLine("115年08月01日", 180, 110, 120, 12)]
        self.assertEqual(_join_wrapped_period(lines), lines)


class ExtractorTests(unittest.TestCase):
    def fill(self, read, words=None):
        extractor = batch.PageExtractor("onnx")
        words = [item for item in TABLE_PAGE if item["text"] != "1000"] if words is None else words
        with pdf_of(words), mock.patch.object(extractor, "_engine"), mock.patch.object(extractor, "_read_with", read):
            return extractor.extract(Path("a.pdf"), 1)

    def model_reading(self):
        return BillResult({
            "user_info": {"energy_consumption": {"peak_kwh": 1000, "off_peak_kwh": 9999}},
            "charge_info": {"total_amount_twd": 111, "basic_charge_twd": 222},
            "quality": {},
        })

    def test_a_model_fills_the_gap_and_nothing_else(self):
        bill = self.fill(mock.Mock(return_value=self.model_reading()))
        row = bill_to_record(bill, "a.pdf").row

        self.assertEqual(row["計費度數_尖峰度數"], 1000)
        self.assertEqual(row["計費度數_離峰度數"], 3000)  # the text said so; the model said 9999
        self.assertEqual(row["應繳總金額"], 525000)
        self.assertEqual(row["基本電費"], 400000)
        self.assertIn("計費度數_尖峰度數 由模型補值", row["需人工檢查"])
        self.assertEqual(bill.get("quality.source"), "pdf_text")

    def test_a_gap_no_model_can_fill_stays_flagged(self):
        bill = self.fill(mock.Mock(return_value=BillResult({"quality": {}})))
        self.assertIn("缺少計費度數_尖峰度數", bill_to_record(bill, "a.pdf").row["需人工檢查"])

    def test_the_text_stands_if_the_model_fails(self):
        bill = self.fill(mock.Mock(side_effect=RuntimeError("no model")))
        self.assertEqual(bill_to_record(bill, "a.pdf").row["應繳總金額"], 525000)
        self.assertIn("no model", bill.get("quality.pdf_text")["fill_failed"])

    def test_text_that_cannot_be_trusted_is_left_to_the_models(self):
        words = [word("999,999元", 400, 120) if item["text"] == "525,000元" else item for item in TABLE_PAGE]
        read = mock.Mock(return_value=self.model_reading())
        with mock.patch.object(batch, "has_bill_values", return_value=True):
            bill = self.fill(read, words)
        self.assertIs(bill, read.return_value)
        self.assertTrue(bill.get("quality.pdf_text")["refused"])

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
