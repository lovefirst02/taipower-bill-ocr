import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from mistral_ocr_test import extract_bill_with_mistral
except ModuleNotFoundError:
    extract_bill_with_mistral = None


class FakeFiles:
    def __init__(self):
        self.deleted_file_id = None

    def upload(self, *, file, purpose):
        if purpose != "ocr" or not file["content"]:
            raise AssertionError("local PDF was not uploaded for OCR")
        return SimpleNamespace(id="uploaded-file")

    def get_signed_url(self, *, file_id):
        return SimpleNamespace(url=f"https://example.invalid/{file_id}")

    def delete(self, *, file_id):
        self.deleted_file_id = file_id


class FakeOcr:
    def process(self, **request):
        if not request.get("document_annotation_prompt"):
            raise AssertionError("structured OCR prompt was omitted")
        if request.get("document_annotation_format") is None:
            raise AssertionError("structured output format was omitted")
        annotation = {
            "document_type": "高壓電力帳單",
            "pages": [
                {
                    "page_number": 2,
                    "blocks": [
                        {
                            "block_name_zh": "節電資訊",
                            "canonical_key": "energy_saving_info",
                            "fields": [
                                {
                                    "label_zh": "度數",
                                    "canonical_key": "energy_kwh",
                                    "value": "15000",
                                    "unit": "kWh",
                                    "context": "本期",
                                    "occurrence": 1,
                                },
                                {
                                    "label_zh": "度數",
                                    "canonical_key": "energy_kwh",
                                    "value": "20000",
                                    "unit": "kWh",
                                    "context": "去年同期",
                                    "occurrence": 2,
                                },
                            ],
                            "tables": [],
                        }
                    ],
                }
            ],
            "warnings": [],
        }
        return SimpleNamespace(
            model="mistral-ocr-latest",
            document_annotation=json.dumps(annotation, ensure_ascii=False),
            pages=[SimpleNamespace(index=1, markdown="## 節電資訊")],
            usage_info=SimpleNamespace(model_dump=lambda: {"pages_processed": 1}),
        )


class FakeMistralClient:
    def __init__(self):
        self.files = FakeFiles()
        self.ocr = FakeOcr()


class MistralOcrTestFileTests(unittest.TestCase):
    def test_returns_structured_annotation_without_losing_duplicate_chinese_labels(self):
        self.assertIsNotNone(
            extract_bill_with_mistral,
            "mistral_ocr_test.py has not been implemented",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "bill.pdf"
            pdf_path.write_bytes(b"%PDF-test")
            client = FakeMistralClient()

            result = extract_bill_with_mistral(
                pdf_path,
                client=client,
                document_annotation_format={"type": "json_schema"},
            )

        fields = result["structured"]["pages"][0]["blocks"][0]["fields"]
        self.assertEqual(
            [(item["label_zh"], item["context"], item["value"]) for item in fields],
            [("度數", "本期", "15000"), ("度數", "去年同期", "20000")],
        )
        self.assertEqual(result["pages"][0]["markdown"], "## 節電資訊")
        self.assertEqual(client.files.deleted_file_id, "uploaded-file")


if __name__ == "__main__":
    unittest.main()
