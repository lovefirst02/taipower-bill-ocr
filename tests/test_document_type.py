import unittest

from taipower_bill_ocr.parser import BillResult, OcrLine, refine_document_type


def result(tariff=None):
    return BillResult({"user_info": {"tariff_type": tariff}} if tariff else {})


class RefineDocumentTypeTests(unittest.TestCase):
    def test_tariff_decides_high_voltage(self):
        self.assertEqual(refine_document_type(result("高壓電力綜合非營業用"), [], "high_voltage_bill"), "high_voltage_bill")
        self.assertEqual(refine_document_type(result("高压需量電力"), [], "unknown"), "high_voltage_bill")

    def test_residential_on_the_pink_template_is_an_ordinary_bill(self):
        self.assertEqual(refine_document_type(result("表燈非營業用"), [], "high_voltage_bill"), "electricity_bill")
        self.assertEqual(refine_document_type(result("表燈非營業用"), [], "unknown"), "electricity_bill")

    def test_tariff_text_in_user_block_is_used_when_not_parsed(self):
        lines = [OcrLine("電價種類：表燈非營業用", 0, 0, 10, 10, "user_info")]
        self.assertEqual(refine_document_type(result(), lines, "high_voltage_bill"), "electricity_bill")

    def test_back_page_without_tariff_keeps_its_type(self):
        self.assertEqual(refine_document_type(result(), [], "high_voltage_bill"), "high_voltage_bill")

    def test_receipts_are_left_alone(self):
        self.assertEqual(refine_document_type(result("電力需量營業用"), [], "bank_autopay_receipt"), "bank_autopay_receipt")


if __name__ == "__main__":
    unittest.main()
