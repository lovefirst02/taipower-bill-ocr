import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from PIL import Image

from taipower_bill_ocr.parser import BillResult, OcrLine
from taipower_web_api import reports
from taipower_web_api.main import create_app
from taipower_web_api.settings import Settings


def official_sample_bill():
    """Values and label positions of Taipower's official high-voltage example."""
    bill = BillResult({
        "document_type": "high_voltage_bill",
        "quality": {"warnings": []},
        "user_info": {
            "customer_number": "00-12-3456-78-9",
            "billing_period": {"start": "109/12/01", "end": "109/12/31"},
            "energy_consumption": {"peak_kwh": 8000, "semi_peak_kwh": 3000, "saturday_semi_peak_kwh": 3000, "off_peak_kwh": 1000},
        },
        "charge_info": {
            "basic_charge_twd": 700000,
            "energy_charge_twd": 120000,
            "power_factor_adjustment_twd": -25000,
            "pretax_total_twd": 761905,
            "total_amount_twd": 800000,
        },
    })
    bill.ocr_lines = [
        OcrLine("計費期間：", 96, 1695, 125, 31, "user_info"),
        OcrLine("109/12/01至109/12/31", 342, 1697, 287, 30, "user_info"),
        OcrLine("稅前應繳總金額", 710, 891, 186, 28, "charge_info"),
        OcrLine("761905.0元", 1020, 893, 172, 31, "charge_info"),
    ]
    return bill


class FakeExtractor:
    cross_checks = False
    uses_paddle = False

    def extract(self, path, page_number):
        return official_sample_bill()


def png_bytes(size=(1654, 2339)):
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="PNG")
    return buffer.getvalue()


def energy_saving_page():
    """A bill's back page: the 節電資訊 table with this period's usage."""
    page = BillResult({
        "document_type": "high_voltage_bill",
        "quality": {"warnings": []},
        "energy_saving_info": {"comparison_rows": [
            {"period": "current", "days": 31, "energy_kwh": 15000, "daily_average_kwh": 483.87},
            {"period": "same_period_last_year", "days": 31, "energy_kwh": 20000, "daily_average_kwh": 645.16},
        ]},
    })
    page.ocr_lines = [
        OcrLine("用電日數", 1062, 112, 105, 32, "energy_saving_info"),
        OcrLine("度數", 1261, 114, 57, 31, "energy_saving_info"),
        OcrLine("本期", 860, 147, 59, 34, "energy_saving_info"),
        OcrLine("31", 1086, 150, 37, 30, "energy_saving_info"),
        OcrLine("15000", 1254, 150, 78, 29, "energy_saving_info"),
        OcrLine("483.87", 1438, 150, 85, 29, "energy_saving_info"),
        OcrLine("去年同期", 862, 186, 105, 29, "energy_saving_info"),
        OcrLine("20000", 1254, 186, 77, 29, "energy_saving_info"),
    ]
    return page


def three_page_pdf(pages=3):
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = Settings(data_dir=root / "data", output_dir=root / "out", web_dist=root / "no-web")
        self.app = create_app(self.settings, extractor_factory=FakeExtractor)
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.app.state.store._db.close()
        self.temp.cleanup()

    def upload(self, user, *names):
        files = [("files", (name, png_bytes() if name.endswith(".png") else b"text", "application/octet-stream")) for name in names]
        response = self.client.post("/api/uploads", data={"user": user}, files=files)
        self.app.state.worker.wait_idle(30)
        return response.json()

    def first_item(self, user):
        return self.client.get("/api/items", params={"user": user}).json()[0]

    def test_batch_upload_processes_bills_and_rejects_other_files(self):
        result = self.upload("Amy", "jan.png", "feb.png", "notes.txt")
        self.assertEqual([item["filename"] for item in result["accepted"]], ["jan.png", "feb.png"])
        self.assertEqual(result["rejected"], [{"filename": "notes.txt", "reason": "unsupported file type"}])
        uploads = self.client.get("/api/uploads", params={"user": "Amy"}).json()
        self.assertEqual({upload["status"] for upload in uploads}, {"done"})
        items = self.client.get("/api/items", params={"user": "Amy"}).json()
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["billing_period"], "109/12/01~109/12/31")

    def test_evidence_marks_cover_billing_period_and_pretax_amount(self):
        self.upload("Amy", "jan.png")
        detail = self.client.get(f"/api/items/{self.first_item('Amy')['id']}").json()
        labels = {mark["text"]: mark for mark in detail["marks"] if mark["type"] == "label"}
        self.assertEqual(set(labels), {"Billing period", "Grey Electricity Cost"})
        highlights = [mark for mark in detail["marks"] if mark["type"] == "highlight"]
        pretax = next(mark for mark in highlights if mark["group"] == labels["Grey Electricity Cost"]["group"])
        # The highlight covers the label and the amount on the 稅前應繳總金額 row.
        self.assertLessEqual(pretax["x"], 710)
        self.assertGreaterEqual(pretax["x"] + pretax["w"], 1020 + 172)
        self.assertEqual([note for note in detail["mark_notes"] if not note.startswith("Electricity Usage")], [])

    def test_residential_bill_marks_the_total_and_says_so(self):
        bill = official_sample_bill()
        del bill["charge_info"]["pretax_total_twd"]
        bill.ocr_lines = [line for line in bill.ocr_lines if "稅前" not in line.text and "761905" not in line.text]
        bill.ocr_lines.append(OcrLine("應繳總金額", 710, 960, 152, 37, "charge_info"))
        bill.ocr_lines.append(OcrLine("800000元", 1014, 965, 177, 34, "charge_info"))
        with mock.patch.object(FakeExtractor, "extract", lambda self, path, page: bill):
            self.upload("Amy", "home.png")
        detail = self.client.get(f"/api/items/{self.first_item('Amy')['id']}").json()
        self.assertIn("Grey Electricity Cost: no pre-tax line on this bill, marked 應繳總金額 instead", detail["mark_notes"])

    def test_edits_recompute_other_adjustments_and_marks_are_saved(self):
        self.upload("Amy", "jan.png")
        item_id = self.first_item("Amy")["id"]
        marks = self.client.get(f"/api/items/{item_id}").json()["marks"]
        marks[0]["x"] += 25
        response = self.client.put(f"/api/items/{item_id}", json={"user": "Amy", "row": {"流動電費": 125000}, "marks": marks})
        detail = response.json()
        self.assertEqual(detail["row"]["流動電費"], 125000)
        self.assertEqual(detail["row"]["其他調整費用"], 0)  # 800000 - 700000 - 125000 + 25000
        self.assertEqual(detail["marks"][0]["x"], marks[0]["x"])

    def test_evidence_downloads(self):
        self.upload("Amy", "jan.png")
        item_id = self.first_item("Amy")["id"]
        png = self.client.get(f"/api/items/{item_id}/evidence.png")
        self.assertEqual(png.headers["content-type"], "image/png")
        marked = Image.open(io.BytesIO(png.content)).convert("RGB")
        self.assertNotEqual(marked.getpixel((800, 900)), (255, 255, 255))  # highlighted
        pdf = self.client.get(f"/api/items/{item_id}/evidence.pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))

    def test_submit_writes_evidence_and_both_workbooks(self):
        self.upload("Amy", "jan.png")
        item_id = self.first_item("Amy")["id"]
        result = self.client.post(f"/api/items/{item_id}/submit", json={"user": "Amy", "row": {"基本電費": 700100}}).json()
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["locked_workbooks"], [])
        self.assertTrue(Path(result["evidence_file"]).read_bytes().startswith(b"%PDF"))

        report = load_workbook(self.settings.report_workbook)["Bills"]
        header = [cell.value for cell in report[1]]
        row = dict(zip(header, [cell.value for cell in report[2]]))
        self.assertEqual((row["基本電費"], row["上傳者"], row["狀態"]), (700100, "Amy", "submitted"))
        self.assertTrue(row["需人工檢查"].startswith("已人工確認"))
        self.assertTrue(row["證據檔"].endswith(".pdf"))

        fields = load_workbook(self.settings.master_workbook)["Fields"]
        header = [cell.value for cell in fields[1]]
        rows = [dict(zip(header, [cell.value for cell in line])) for line in fields.iter_rows(min_row=2)]
        basic = next(row for row in rows if row["key"] == "basic_charge_twd")
        self.assertEqual((basic["value"], basic["status"], basic["cross_check_value"]), (700100, "reviewer_corrected", 700000))

    def test_submitted_bill_is_read_only_until_reopened(self):
        self.upload("Amy", "jan.png")
        item_id = self.first_item("Amy")["id"]
        self.client.post(f"/api/items/{item_id}/submit", json={"user": "Amy"})
        self.assertEqual(self.client.put(f"/api/items/{item_id}", json={"user": "Amy", "row": {"基本電費": 1}}).status_code, 409)
        reopened = self.client.post(f"/api/items/{item_id}/reopen", json={"user": "Amy"}).json()
        self.assertEqual(reopened["status"], "draft")
        self.assertEqual(load_workbook(self.settings.report_workbook)["Bills"].max_row, 1)  # header only

    def test_export_contains_only_the_users_own_bills(self):
        self.upload("Amy", "jan.png")
        self.upload("Ben", "feb.png")
        content = self.client.get("/api/export.xlsx", params={"user": "Amy"}).content
        sheet = load_workbook(io.BytesIO(content)).active
        header = [cell.value for cell in sheet[1]]
        rows = [dict(zip(header, [cell.value for cell in line])) for line in sheet.iter_rows(min_row=2)]
        self.assertEqual([(row["上傳者"], row["來源檔案"]) for row in rows], [("Amy", "jan.png")])

    def test_all_evidence_pdfs_download_as_one_zip(self):
        import zipfile

        self.upload("Amy", "jan.png", "feb.png")
        self.upload("Ben", "mar.png")
        first = self.first_item("Amy")["id"]
        submitted = self.client.post(f"/api/items/{first}/submit", json={"user": "Amy"}).json()

        response = self.client.get("/api/evidence.zip", params={"user": "Amy"})
        self.assertEqual(response.headers["content-type"], "application/zip")
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        names = sorted(archive.namelist())
        self.assertEqual(len(names), 2)  # Amy's two bills, not Ben's
        self.assertEqual([name.split("/")[0] for name in names], ["draft", "submitted"])
        saved = Path(submitted["evidence_file"])
        self.assertEqual(archive.read(f"submitted/{saved.name}"), saved.read_bytes())
        self.assertTrue(all(archive.read(name).startswith(b"%PDF") for name in names))

        only_submitted = zipfile.ZipFile(io.BytesIO(self.client.get("/api/evidence.zip", params={"user": "Amy", "status": "submitted"}).content))
        self.assertEqual(only_submitted.namelist(), [f"submitted/{saved.name}"])
        self.assertEqual(self.client.get("/api/evidence.zip", params={"user": "Nobody"}).status_code, 404)

    def test_workbook_open_in_excel_is_reported(self):
        self.upload("Amy", "jan.png")
        item_id = self.first_item("Amy")["id"]
        with mock.patch.object(reports, "save_tables", return_value=False):
            result = self.client.post(f"/api/items/{item_id}/submit", json={"user": "Amy"}).json()
        self.assertIn("taipower_bills_17.xlsx", result["locked_workbooks"])

    def test_value_found_only_by_the_cross_check_engine_is_marked_exactly(self):
        # Paddle missed the period; Windows OCR read it, so its line is used.
        bill = official_sample_bill()
        bill.ocr_lines = [line for line in bill.ocr_lines if "109/12" not in line.text and "計費期間" not in line.text]
        bill["blocks"] = {"user_info": {"bounds": {"x": 96, "y": 634, "width": 559, "height": 1188}}}
        windows = official_sample_bill()
        FakeExtractor.cross_checks = True
        try:
            with mock.patch.object(FakeExtractor, "extract", lambda self, path, page: bill), \
                 mock.patch.object(FakeExtractor, "extract_cross_check", lambda self, path, page: windows, create=True):
                self.upload("Amy", "jan.png")
        finally:
            FakeExtractor.cross_checks = False
        detail = self.client.get(f"/api/items/{self.first_item('Amy')['id']}").json()
        period = detail["locations"]["Billing Period 計費期間"]
        self.assertTrue(period["exact"])
        self.assertLess(period["box"][3], 80)  # one line, not the whole block
        self.assertEqual([note for note in detail["mark_notes"] if not note.startswith("Electricity Usage")], [])

    def test_block_only_location_gets_a_small_highlight_to_drag(self):
        bill = official_sample_bill()
        bill.ocr_lines = [line for line in bill.ocr_lines if "計費期間" not in line.text and "109/12" not in line.text]
        bill["blocks"] = {"user_info": {"bounds": {"x": 96, "y": 634, "width": 559, "height": 1188}}}
        with mock.patch.object(FakeExtractor, "extract", lambda self, path, page: bill):
            self.upload("Amy", "jan.png")
        detail = self.client.get(f"/api/items/{self.first_item('Amy')['id']}").json()
        labels = {mark["text"]: mark for mark in detail["marks"] if mark["type"] == "label"}
        period = next(m for m in detail["marks"] if m["type"] == "highlight" and m["group"] == labels["Billing period"]["group"])
        self.assertLess(period["h"], 100)  # not the 1188 px block
        self.assertIn("Billing period: exact line not found, drag the highlight onto Billing Period 計費期間", detail["mark_notes"])

    def test_image_that_is_not_a_bill_is_not_sent_to_review(self):
        empty = BillResult({"document_type": "unknown", "quality": {"warnings": ["legacy_virtual_blocks"]}, "charge_info": {}})
        with mock.patch.object(FakeExtractor, "extract", lambda self, path, page: empty):
            self.upload("Amy", "photo.png")
        uploads = self.client.get("/api/uploads", params={"user": "Amy"}).json()
        self.assertEqual(uploads[0]["status"], "done")
        self.assertEqual(self.client.get("/api/items", params={"user": "Amy"}).json(), [])

    def submit_all(self, user):
        store = self.app.state.store
        for page in store.pages(user=user, bills_only=True):
            store.update_page(page["id"], status="submitted", submitted_at="2026-10-14T09:00:00", submitted_by=user)

    def test_simultaneous_workbook_writes_do_not_clash(self):
        import threading

        self.upload("Amy", *[f"bill{n}.png" for n in range(6)])
        self.submit_all("Amy")
        errors = []

        def write():
            try:
                self.app.state.writer.write()
            except Exception as exc:  # pragma: no cover - the failure being tested
                errors.append(exc)

        threads = [threading.Thread(target=write) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(load_workbook(self.settings.report_workbook)["Bills"].max_row, 7)  # header + 6
        self.assertEqual([p.name for p in self.settings.output_dir.iterdir() if p.name.startswith("~tmp")], [])

    def test_workbook_open_in_excel_is_written_by_the_background_retry(self):
        import time

        writer = reports.WorkbookWriter(self.app.state.store, self.settings.output_dir, retry_seconds=0.1)
        writer.start()
        self.addCleanup(writer.stop)
        self.upload("Amy", "jan.png")
        self.submit_all("Amy")
        real_save = reports.save_tables
        with mock.patch.object(reports, "save_tables", return_value=False):
            self.assertEqual(writer.write(), ["taipower_bills_17.xlsx"])
            self.assertTrue(writer.pending)
        # The file is closed again: the retry loop replaces it without a submit.
        self.assertIs(reports.save_tables, real_save)
        deadline = time.time() + 5
        while writer.pending and time.time() < deadline:
            time.sleep(0.05)
        self.assertFalse(writer.pending)
        self.assertEqual(load_workbook(self.settings.report_workbook)["Bills"].max_row, 2)

    def test_both_workbooks_record_uploader_and_submitter(self):
        self.upload("Amy", "jan.png")
        item_id = self.first_item("Amy")["id"]
        self.client.post(f"/api/items/{item_id}/submit", json={"user": "Amy"})

        report = load_workbook(self.settings.report_workbook)["Bills"]
        header = [cell.value for cell in report[1]]
        row = dict(zip(header, [cell.value for cell in report[2]]))
        self.assertEqual((row["上傳者"], row["送出者"]), ("Amy", "Amy"))
        self.assertTrue(row["送出時間"])

        pages = load_workbook(self.settings.master_workbook)["Pages"]
        header = [cell.value for cell in pages[1]]
        page = dict(zip(header, [cell.value for cell in pages[2]]))
        self.assertEqual((page["uploaded_by"], page["submitted_by"]), ("Amy", "Amy"))
        self.assertEqual(page["submitted_at"], row["送出時間"])

    def test_reopen_moves_the_submitted_pdf_to_the_archive(self):
        self.upload("Amy", "jan.png")
        item_id = self.first_item("Amy")["id"]
        saved = Path(self.client.post(f"/api/items/{item_id}/submit", json={"user": "Amy"}).json()["evidence_file"])
        content = saved.read_bytes()

        reopened = self.client.post(f"/api/items/{item_id}/reopen", json={"user": "Amy"}).json()
        archived = Path(reopened["archived_evidence"])
        self.assertFalse(saved.exists())
        self.assertEqual(archived.parent, self.settings.evidence_dir / "_reopened" / "Amy")
        self.assertTrue(archived.name.startswith(saved.stem))
        self.assertEqual(archived.read_bytes(), content)

        resubmitted = self.client.post(f"/api/items/{item_id}/submit", json={"user": "Amy"}).json()
        self.assertTrue(Path(resubmitted["evidence_file"]).is_file())
        # Reopening a draft has nothing to archive.
        self.client.post(f"/api/items/{item_id}/reopen", json={"user": "Amy"})
        self.assertIsNone(self.client.post(f"/api/items/{item_id}/reopen", json={"user": "Amy"}).json()["archived_evidence"])

    def test_user_name_cannot_escape_the_evidence_folder(self):
        self.upload("../Amy", "jan.png")
        item_id = self.first_item("../Amy")["id"]
        saved = Path(self.client.post(f"/api/items/{item_id}/submit", json={"user": "../Amy"}).json()["evidence_file"])
        self.assertEqual(saved.parent.parent, self.settings.evidence_dir)

    def uploads_of(self, user):
        return {upload["filename"]: upload for upload in self.client.get("/api/uploads", params={"user": user}).json()}

    def test_pause_stops_after_the_current_page_and_resume_reads_the_rest(self):
        import threading
        import time

        started, proceed, read = threading.Event(), threading.Event(), []

        def extract(self_, path, page):
            read.append((Path(path).suffix, page))
            if page == 1:
                started.set()
                proceed.wait(10)
            return official_sample_bill()

        with mock.patch.object(FakeExtractor, "extract", extract):
            files = [("files", ("three.pdf", three_page_pdf(), "application/pdf")), ("files", ("later.png", png_bytes(), "image/png"))]
            self.client.post("/api/uploads", data={"user": "Amy"}, files=files)
            self.assertTrue(started.wait(10))
            self.assertEqual(self.client.post("/api/uploads/pause", json={"user": "Amy"}).json(), {"paused": 2})
            proceed.set()
            deadline = time.time() + 10
            while self.uploads_of("Amy")["three.pdf"]["status"] != "paused" and time.time() < deadline:
                time.sleep(0.05)
            self.app.state.worker.wait_idle(10)
            uploads = self.uploads_of("Amy")
            self.assertEqual((uploads["three.pdf"]["status"], uploads["three.pdf"]["pages_done"]), ("paused", 1))
            self.assertEqual(uploads["later.png"]["status"], "paused")
            self.assertEqual(read, [(".pdf", 1)])

            self.assertEqual(self.client.post("/api/uploads/resume", json={"user": "Amy"}).json(), {"resumed": 2})
            self.app.state.worker.wait_idle(10)
        uploads = self.uploads_of("Amy")
        self.assertEqual((uploads["three.pdf"]["status"], uploads["three.pdf"]["pages_done"]), ("done", 3))
        self.assertEqual(uploads["later.png"]["status"], "done")
        # Page 1 was not read again.
        self.assertEqual(read, [(".pdf", 1), (".pdf", 2), (".pdf", 3), (".png", None)])
        self.assertEqual(len(self.client.get("/api/items", params={"user": "Amy"}).json()), 4)

    def test_pausing_does_not_touch_other_users_uploads(self):
        with mock.patch.object(self.app.state.worker, "submit"):  # keep everything queued
            self.upload("Amy", "a.png")
            self.client.post("/api/uploads", data={"user": "Ben"}, files=[("files", ("b.png", png_bytes(), "image/png"))])
        self.client.post("/api/uploads/pause", json={"user": "Amy"})
        self.assertEqual(self.uploads_of("Amy")["a.png"]["status"], "paused")
        self.assertEqual(self.uploads_of("Ben")["b.png"]["status"], "queued")

    def test_failed_upload_can_be_retried_or_removed(self):
        def broken(self_, path, page):
            raise RuntimeError("cannot read")

        with mock.patch.object(FakeExtractor, "extract", broken):
            self.upload("Amy", "bad.png", "worse.png")
        uploads = self.uploads_of("Amy")
        self.assertEqual({u["status"] for u in uploads.values()}, {"failed"})

        retried = self.client.post(f"/api/uploads/{uploads['bad.png']['id']}/retry", json={"user": "Amy"})
        self.assertEqual(retried.json()["status"], "queued")
        self.app.state.worker.wait_idle(10)
        self.assertEqual(self.uploads_of("Amy")["bad.png"]["status"], "done")

        stored = Path(self.app.state.store.upload(uploads["worse.png"]["id"])["path"])
        self.assertEqual(self.client.delete(f"/api/uploads/{uploads['worse.png']['id']}", params={"user": "Ben"}).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/uploads/{uploads['worse.png']['id']}", params={"user": "Amy"}).status_code, 200)
        self.assertNotIn("worse.png", self.uploads_of("Amy"))
        self.assertFalse(stored.exists())
        # A finished upload stays with its bills.
        self.assertEqual(self.client.delete(f"/api/uploads/{uploads['bad.png']['id']}", params={"user": "Amy"}).status_code, 409)
        self.assertEqual(self.client.post(f"/api/uploads/{uploads['bad.png']['id']}/retry", json={"user": "Amy"}).status_code, 409)

    def upload_two_page_bill(self, user="Amy"):
        def extract(self_, path, page):
            return official_sample_bill() if page == 1 else energy_saving_page()

        with mock.patch.object(FakeExtractor, "extract", extract):
            self.client.post("/api/uploads", data={"user": user}, files=[("files", ("two.pdf", three_page_pdf(2), "application/pdf"))])
            self.app.state.worker.wait_idle(10)
        return self.client.get(f"/api/items/{self.first_item(user)['id']}").json()

    def test_current_usage_on_the_back_page_is_marked(self):
        from pypdf import PdfReader

        detail = self.upload_two_page_bill()
        self.assertEqual([(page["page"], page["bill"]) for page in detail["pages"]], [(1, True), (2, False)])
        usage = [mark for mark in detail["marks"] if mark.get("page") == 2]
        label = next(mark for mark in usage if mark["type"] == "label")
        highlight = next(mark for mark in usage if mark["type"] == "highlight")
        self.assertEqual(label["text"], "Electricity Usage (kWh)")
        # From the 本期 label to the 15000 under 度數, on that row only.
        self.assertLessEqual(highlight["x"], 860)
        self.assertGreaterEqual(highlight["x"] + highlight["w"], 1254 + 78)
        self.assertLess(highlight["h"], 60)
        self.assertEqual(detail["mark_notes"], [])

        item_id = detail["id"]
        self.assertEqual(self.client.get(f"/api/items/{item_id}/image", params={"page": 2}).status_code, 200)
        self.assertEqual(self.client.get(f"/api/items/{item_id}/image", params={"page": 3}).status_code, 404)
        self.assertEqual(self.client.get(f"/api/items/{item_id}/evidence.png", params={"page": 2}).status_code, 200)
        self.assertEqual(len(PdfReader(io.BytesIO(self.client.get(f"/api/items/{item_id}/evidence.pdf").content)).pages), 2)

        # Without marks on the back page, the evidence is the bill page alone.
        bill_marks = [mark for mark in detail["marks"] if "page" not in mark]
        self.client.put(f"/api/items/{item_id}", json={"user": "Amy", "marks": bill_marks})
        self.assertEqual(len(PdfReader(io.BytesIO(self.client.get(f"/api/items/{item_id}/evidence.pdf").content)).pages), 1)
        # Reset puts the usage mark back.
        reset = self.client.post(f"/api/items/{item_id}/reset-marks").json()
        self.assertEqual(sum(mark.get("page") == 2 for mark in reset["marks"]), 2)

        saved = Path(self.client.post(f"/api/items/{item_id}/submit", json={"user": "Amy"}).json()["evidence_file"])
        self.assertEqual(len(PdfReader(str(saved)).pages), 2)

    def test_bill_without_usage_says_so(self):
        self.upload("Amy", "jan.png")
        detail = self.client.get(f"/api/items/{self.first_item('Amy')['id']}").json()
        self.assertEqual(len(detail["pages"]), 1)
        self.assertTrue(any(note.startswith("Electricity Usage (kWh): 本期度數") for note in detail["mark_notes"]))

    def test_user_name_is_required(self):
        response = self.client.post("/api/uploads", data={"user": "  "}, files=[("files", ("a.png", png_bytes(), "image/png"))])
        self.assertEqual(response.status_code, 400)


class PerUserWorkbookTests(unittest.TestCase):
    """Copies of the app on several PCs, each writing its own workbooks."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = Settings(data_dir=root / "data", output_dir=root / "out", web_dist=root / "no-web", workbooks="per_user")
        self.app = create_app(self.settings, extractor_factory=FakeExtractor)
        self.client = TestClient(self.app)
        self.client.__enter__()

    tearDown = ApiTests.tearDown
    upload = ApiTests.upload
    first_item = ApiTests.first_item

    def rows(self, path):
        sheet = load_workbook(path)["Bills"]
        header = [cell.value for cell in sheet[1]]
        return [dict(zip(header, [cell.value for cell in line])) for line in sheet.iter_rows(min_row=2)]

    def test_each_user_gets_their_own_workbooks(self):
        self.upload("Amy", "jan.png")
        self.upload("Ben", "feb.png")
        for user in ("Amy", "Ben"):
            item_id = self.first_item(user)["id"]
            self.assertEqual(self.client.post(f"/api/items/{item_id}/submit", json={"user": user}).json()["locked_workbooks"], [])

        out = self.settings.output_dir
        self.assertFalse((out / "taipower_bills_17.xlsx").exists())
        self.assertEqual([row["來源檔案"] for row in self.rows(out / "Amy" / "taipower_bills_17.xlsx")], ["jan.png"])
        self.assertEqual([row["來源檔案"] for row in self.rows(out / "Ben" / "taipower_bills_17.xlsx")], ["feb.png"])
        self.assertTrue((out / "Amy" / "taipower_bills.xlsx").is_file())
        self.assertEqual(self.client.get("/api/output", params={"user": "Amy"}).json()["folder"], str(out / "Amy"))

        # Reopening rewrites only that user's workbooks.
        self.client.post(f"/api/items/{self.first_item('Amy')['id']}/reopen", json={"user": "Amy"})
        self.assertEqual(self.rows(out / "Amy" / "taipower_bills_17.xlsx"), [])
        self.assertEqual(len(self.rows(out / "Ben" / "taipower_bills_17.xlsx")), 1)

    def test_locked_user_workbook_is_retried_for_that_user(self):
        import time

        writer = reports.WorkbookWriter(self.app.state.store, self.settings.output_dir, retry_seconds=0.1, per_user=True)
        writer.start()
        self.addCleanup(writer.stop)
        self.upload("Amy", "jan.png")
        store = self.app.state.store
        for page in store.pages(user="Amy", bills_only=True):
            store.update_page(page["id"], status="submitted", submitted_at="2026-10-14T09:00:00", submitted_by="Amy")
        with mock.patch.object(reports, "save_tables", return_value=False):
            self.assertEqual(writer.write("Amy"), ["taipower_bills_17.xlsx"])
        deadline = time.time() + 5
        while writer.pending and time.time() < deadline:
            time.sleep(0.05)
        self.assertFalse(writer.pending)
        self.assertEqual(len(self.rows(self.settings.output_dir / "Amy" / "taipower_bills_17.xlsx")), 1)


if __name__ == "__main__":
    unittest.main()
