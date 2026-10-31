import csv
import tempfile
import unittest
from pathlib import Path

from taipower_bill_ocr.batch import PageDone
from taipower_bill_ocr.bill_csv import CSV_COLUMNS, DETAIL_COLUMNS, write_csv
from taipower_bill_ocr import bill_csv
from unittest import mock


class Record:
    row = {"Billing Period 計費期間": "113/01/01~113/01/31", "Custom No. 電號": "17-94-2403-50-4", "基本電費": 634220,
           "預繳費用": None, "來源檔案": "a.png", "頁碼": None, "需人工檢查": "缺少流動電費"}


def read_back(details):
    with tempfile.TemporaryDirectory() as folder:
        out = Path(folder) / "bills.csv"
        events = [PageDone(Path("a.png"), None, Record()), PageDone(Path("b.png"), None, None)]
        with mock.patch.object(bill_csv, "iter_batch", lambda files, extractor: iter(events)):
            result = write_csv([Path("a.png")], out, object(), details=details)
        with out.open(encoding="utf-8-sig", newline="") as handle:
            return result, list(csv.DictReader(handle))


class BillCsvTests(unittest.TestCase):
    def test_the_columns_asked_for_and_no_more(self):
        (written, skipped, failures), rows = read_back(False)
        self.assertEqual((written, skipped, failures), (1, 1, []))
        self.assertEqual(tuple(rows[0]), CSV_COLUMNS)
        self.assertEqual(rows[0]["Billing Period 計費期間"], "113/01/01~113/01/31")
        self.assertEqual(rows[0]["基本電費"], "634220")
        # Not read stays empty, never a guess.
        self.assertEqual(rows[0]["預繳費用"], "")

    def test_details_add_the_source_page_and_what_to_check(self):
        _, rows = read_back(True)
        self.assertEqual(tuple(rows[0]), CSV_COLUMNS + DETAIL_COLUMNS)
        self.assertEqual((rows[0]["來源檔案"], rows[0]["需人工檢查"]), ("a.png", "缺少流動電費"))
