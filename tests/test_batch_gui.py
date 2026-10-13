import tempfile
import threading
import tkinter as tk
import unittest
from pathlib import Path

from taipower_bill_ocr.batch import (
    FileFinished,
    FileStarted,
    PageDone,
    find_bill_files,
    iter_batch,
)
from taipower_bill_ocr.excel_export import bill_to_record, recompute_other_adjustment
from taipower_bill_ocr.parser import BillResult

from test_excel_export import high_voltage_bill


class FakeExtractor:
    """Returns a charge page for odd pages and a back page for even pages."""

    cross_checks = False

    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def extract(self, path, page_number):
        self.calls.append((path.name, page_number))
        if path.name == self.fail_on:
            raise RuntimeError("unreadable")
        if page_number and page_number % 2 == 0:
            return BillResult({"meter_info": {}, "quality": {"warnings": []}})
        bill = high_voltage_bill()
        bill["quality"]["page_number"] = page_number
        return bill


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "b.JPG").write_bytes(b"")
        (self.root / "a.png").write_bytes(b"")
        (self.root / "notes.txt").write_text("x")
        (self.root / "sub").mkdir()
        (self.root / "sub" / "c.png").write_bytes(b"")

    def tearDown(self):
        self.temp.cleanup()

    def test_finds_supported_files_with_optional_recursion(self):
        names = [path.name for path in find_bill_files(self.root)]
        self.assertEqual(names, ["a.png", "b.JPG"])
        recursive = [path.name for path in find_bill_files(self.root, recursive=True)]
        self.assertEqual(sorted(recursive), ["a.png", "b.JPG", "c.png"])

    def test_failed_file_is_reported_and_batch_continues(self):
        files = find_bill_files(self.root)
        events = list(iter_batch(files, FakeExtractor(fail_on="a.png")))

        finished = [event for event in events if isinstance(event, FileFinished)]
        self.assertEqual(len(finished), 2)
        self.assertIn("unreadable", finished[0].error)
        self.assertIsNone(finished[1].error)
        pages = [event for event in events if isinstance(event, PageDone)]
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].record.row["來源檔案"], "b.JPG")

    def test_cancel_stops_before_next_file(self):
        cancel = threading.Event()
        cancel.set()
        extractor = FakeExtractor()
        self.assertEqual(list(iter_batch(find_bill_files(self.root), extractor, cancel)), [])
        self.assertEqual(extractor.calls, [])


class RecomputeTests(unittest.TestCase):
    def test_edit_refreshes_other_adjustment(self):
        row = bill_to_record(high_voltage_bill()).row
        row["流動電費"] = 130000
        recompute_other_adjustment(row)
        self.assertEqual(row["其他調整費用"], -5000)
        row["基本電費"] = None
        recompute_other_adjustment(row)
        self.assertIsNone(row["其他調整費用"])


class GuiSmokeTests(unittest.TestCase):
    def setUp(self):
        try:
            self.tk = tk.Tk()
        except tk.TclError as exc:  # pragma: no cover - no display
            self.skipTest(f"Tk unavailable: {exc}")
        self.tk.withdraw()

    def tearDown(self):
        self.tk.destroy()

    def test_events_populate_tables_and_review_state(self):
        from taipower_bill_ocr.gui import BillOcrApp

        app = BillOcrApp(self.tk)
        path = Path("bills/2024.pdf")
        app.file_items[path] = app.files_tree.insert("", "end", text="2024.pdf", values=("", "Queued"))
        app.files_total = 1

        clean = bill_to_record(high_voltage_bill(), "2024.pdf")
        flagged = bill_to_record(high_voltage_bill(energy_charge_twd=None), "2024.pdf")
        for event in (
            FileStarted(path, 3),
            PageDone(path, 1, clean),
            PageDone(path, 2, None),
            PageDone(path, 3, flagged),
            FileFinished(path),
        ):
            app._handle_event(event)

        self.assertEqual(len(app.results_tree.get_children()), 2)
        self.assertIn("1 need review", app.summary_var.get())
        self.assertIn("2 bill row(s)", app.files_tree.item(app.file_items[path], "values")[1])

        app.review_only_var.set(True)
        app._refresh_results()
        (flagged_iid,) = app.results_tree.get_children()
        self.assertIn("missing Energy Charge", app.results_tree.set(flagged_iid, "需人工檢查"))

        app.results_tree.selection_set(flagged_iid)
        app._toggle_reviewed()
        self.assertEqual(app.results_tree.get_children(), ())
        self.assertTrue(
            app.results[flagged_iid].export_row()["需人工檢查"].startswith("已人工確認")
        )


if __name__ == "__main__":
    unittest.main()


class FieldLocatorTests(unittest.TestCase):
    def setUp(self):
        from taipower_bill_ocr.parser import OcrLine

        bill = high_voltage_bill()
        bill["blocks"] = {
            "charge_info": {"bounds": {"x": 690, "y": 630, "width": 520, "height": 600}},
            "user_info": {"bounds": {"x": 90, "y": 630, "width": 560, "height": 600}},
        }
        bill.ocr_lines = [
            OcrLine("電號 Customer Number", 100, 490, 320, 40, "payment_info"),
            OcrLine("00-12-3456-78-9", 150, 550, 250, 32, "payment_info"),
            OcrLine("基本電費(約定)", 708, 696, 180, 34, "charge_info"),
            OcrLine("700000.0元", 1035, 697, 158, 36, "charge_info"),
            OcrLine("流動電費", 710, 724, 109, 32, "charge_info"),
            OcrLine("計費度數(度)", 97, 1050, 300, 40, "user_info"),
            OcrLine("尖峰度數", 125, 1078, 110, 35, "user_info"),
            OcrLine("8000", 600, 1086, 40, 27, "user_info"),
            OcrLine("轉供度數(度)", 97, 1120, 300, 40, "user_info"),
            OcrLine("尖峰度數", 125, 1160, 110, 35, "user_info"),
            OcrLine("100", 600, 1165, 40, 27, "user_info"),
        ]
        self.bill = bill

    def locate(self, column, value):
        from taipower_bill_ocr.field_locator import locate_field

        return locate_field(self.bill, column, value)

    def test_value_beside_label(self):
        location = self.locate("基本電費", 700000)
        self.assertTrue(location.exact)
        x, y, w, h = location.box
        self.assertLess(x, 708)
        self.assertGreater(x + w, 1035 + 158)

    def test_value_below_label(self):
        x, y, w, h = self.locate("Custom No. 電號", "00-12-3456-78-9").box
        self.assertGreater(y + h, 550 + 32)

    def test_missing_value_points_at_label_row(self):
        location = self.locate("流動電費", None)
        self.assertTrue(location.exact)
        self.assertAlmostEqual(location.box[1], 720)

    def test_kwh_groups_use_their_own_heading(self):
        billed = self.locate("計費度數_尖峰度數", 8000)
        transferred = self.locate("轉供度數_尖峰度數", 100)
        self.assertLess(billed.box[1], 1100)
        self.assertGreater(transferred.box[1], 1150)

    def test_label_in_an_unexpected_block_is_found_page_wide(self):
        from taipower_bill_ocr.parser import OcrLine

        # Residential layouts print 計費期間 in the bottom-left (invoice) block.
        self.bill.ocr_lines += [
            OcrLine("計費期間", 153, 1537, 100, 30, "invoice_info"),
            OcrLine("113/02/06至113/04/09", 412, 1538, 220, 30, "invoice_info"),
        ]
        location = self.locate("Billing Period 計費期間", "113/02/06~113/04/09")
        self.assertTrue(location.exact)
        self.assertEqual(round(location.box[1]), 1533)

    def test_value_cut_short_by_ocr_still_points_at_its_row(self):
        from taipower_bill_ocr.parser import OcrLine

        self.bill.ocr_lines += [
            OcrLine("計費期間", 153, 1537, 100, 30, "invoice_info"),
            OcrLine("113/02/06至113/0", 412, 1538, 200, 30, "invoice_info"),
        ]
        location = self.locate("Billing Period 計費期間", "113/02/06~113/04/09")
        self.assertTrue(location.exact)
        self.assertLess(location.box[3], 60)

    def test_unknown_position_falls_back_to_block(self):
        location = self.locate("預繳費用", None)
        self.assertFalse(location.exact)
        self.assertEqual(location.box, (690, 630, 520, 600))


class GuiEditFlowTests(unittest.TestCase):
    def setUp(self):
        try:
            self.tk = tk.Tk()
        except tk.TclError as exc:  # pragma: no cover - no display
            self.skipTest(f"Tk unavailable: {exc}")
        self.tk.withdraw()

    def tearDown(self):
        self.tk.destroy()

    def test_form_edit_recomputes_and_review_moves_to_next_flagged_row(self):
        from taipower_bill_ocr.gui import BillOcrApp

        app = BillOcrApp(self.tk)
        path = Path("bills/2024.pdf")
        records = [
            bill_to_record(high_voltage_bill(basic_charge_twd=None), "2024.pdf"),
            bill_to_record(high_voltage_bill(), "2024.pdf"),
            bill_to_record(high_voltage_bill(energy_charge_twd=None), "2024.pdf"),
        ]
        app._handle_event(FileStarted(path, 3))
        for page, record in enumerate(records, start=1):
            app._handle_event(PageDone(path, page, record))
        self.tk.update()
        self.assertEqual(app.form_iid, "row0")

        entry = app.entries["基本電費"]
        entry.delete(0, "end")
        entry.insert(0, "700,000")
        app._commit_and_advance("基本電費")
        row = app.results["row0"].row
        self.assertEqual(row["基本電費"], 700000)
        self.assertEqual(row["其他調整費用"], 5000)
        self.assertIn("基本電費", app.results["row0"].edited)

        entry.delete(0, "end")
        entry.insert(0, "abc")
        self.assertFalse(app._commit_field("基本電費"))
        self.assertEqual(row["基本電費"], 700000)

        app.focused_key = None
        app._mark_reviewed_and_next()
        self.tk.update()
        self.assertTrue(app.results["row0"].reviewed)
        self.assertEqual(app.form_iid, "row2")  # skips the clean row
        self.assertIn("1 need review", app.summary_var.get())


class CrossCheckExtractor(FakeExtractor):
    cross_checks = True

    def __init__(self, cross_check_error=None):
        super().__init__()
        self.cross_check_error = cross_check_error

    def extract(self, path, page_number):
        return high_voltage_bill(basic_charge_twd=None)

    def extract_cross_check(self, path, page_number):
        if self.cross_check_error:
            raise RuntimeError(self.cross_check_error)
        return high_voltage_bill(energy_charge_twd=125000)


class CrossCheckBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "bill.png"
        self.path.write_bytes(b"")

    def tearDown(self):
        self.temp.cleanup()

    def page(self, extractor):
        return next(e for e in iter_batch([self.path], extractor) if isinstance(e, PageDone))

    def test_cross_check_fills_gaps_and_keeps_primary_on_conflict(self):
        record = self.page(CrossCheckExtractor()).record
        self.assertEqual(record.row["基本電費"], 700000)  # filled from Windows
        self.assertEqual(record.row["流動電費"], 120000)  # Paddle kept
        self.assertEqual(record.alternatives, {"流動電費": 125000})
        notes = [issue.en for issue in record.issues]
        self.assertIn("Basic Charge filled in from Windows OCR", notes)
        self.assertIn("Engines disagree on Energy Charge: 120,000 vs Windows OCR 125,000", notes)
        # Computed from merged values, so the filled-in gap no longer blocks it.
        self.assertEqual(record.row["其他調整費用"], 5000)

    def test_cross_check_failure_keeps_primary_result(self):
        record = self.page(CrossCheckExtractor(cross_check_error="no OCR language")).record
        self.assertIsNone(record.row["基本電費"])
        self.assertIn("cross-check failed: no OCR language", record.issues[-1].en)
        self.assertIn("交叉檢查失敗", record.row["需人工檢查"])

    def test_matching_engines_add_no_issues(self):
        record = bill_to_record(high_voltage_bill(), cross_check=high_voltage_bill())
        self.assertEqual(record.issues, [])
        self.assertEqual(record.alternatives, {})


class GuiAlternativeTests(unittest.TestCase):
    def setUp(self):
        try:
            self.tk = tk.Tk()
        except tk.TclError as exc:  # pragma: no cover - no display
            self.skipTest(f"Tk unavailable: {exc}")
        self.tk.withdraw()

    def tearDown(self):
        self.tk.destroy()

    def test_use_button_applies_other_engine_value(self):
        from taipower_bill_ocr.gui import BillOcrApp

        app = BillOcrApp(self.tk)
        record = bill_to_record(
            high_voltage_bill(), "2024.pdf",
            cross_check=high_voltage_bill(energy_charge_twd=125000),
        )
        path = Path("bills/2024.pdf")
        app._handle_event(FileStarted(path, 1))
        app._handle_event(PageDone(path, 1, record))
        self.tk.update()

        button = app.alternative_buttons["流動電費"]
        self.assertTrue(button.winfo_ismapped() or button.grid_info())
        self.assertEqual(button.cget("text"), "Use 125,000")
        self.assertFalse(app.alternative_buttons["基本電費"].grid_info())

        app._use_alternative("流動電費")
        row = app.results["row0"].row
        self.assertEqual(row["流動電費"], 125000)
        self.assertEqual(row["其他調整費用"], 0)
        self.assertFalse(button.grid_info())
