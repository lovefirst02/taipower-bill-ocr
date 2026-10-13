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
        self.assertEqual(detail["mark_notes"], [])

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

    def test_workbook_open_in_excel_is_reported(self):
        self.upload("Amy", "jan.png")
        item_id = self.first_item("Amy")["id"]
        with mock.patch.object(reports, "save_tables", return_value=False):
            result = self.client.post(f"/api/items/{item_id}/submit", json={"user": "Amy"}).json()
        self.assertIn("taipower_bills_17.xlsx", result["locked_workbooks"])

    def test_user_name_is_required(self):
        response = self.client.post("/api/uploads", data={"user": "  "}, files=[("files", ("a.png", png_bytes(), "image/png"))])
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
