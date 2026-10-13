import unittest

from taipower_bill_ocr.dataset import (
    AGREE,
    DISAGREE,
    FILLED,
    PRIMARY_ONLY,
    UNVERIFIED,
    page_record,
)
from taipower_bill_ocr.parser import BillResult, OcrLine


def bill(**blocks):
    return BillResult({"document_type": "high_voltage_bill", "quality": {"warnings": []}, **blocks})


def fields_by_key(record):
    return {(item["block"], item["key"]): item for item in record.fields}


class PageRecordTests(unittest.TestCase):
    def test_nested_fields_get_dotted_keys_and_printed_labels(self):
        record = page_record(
            bill(user_info={"energy_consumption": {"peak_kwh": 8000}, "billing_period": {"start": "109/12/01", "end": "109/12/31"}}),
            file="a.pdf", page=1,
        )
        fields = fields_by_key(record)
        self.assertEqual(fields[("user_info", "energy_consumption.peak_kwh")]["label"], "計費度數／尖峰度數")
        self.assertEqual(record.summary["billing_period_start"], "109/12/01")
        self.assertTrue(all(item["recognized"] for item in record.fields))

    def test_list_of_rows_becomes_table_cells(self):
        record = page_record(
            bill(meter_info={"readings": [{"meter_type": "01", "previous": "03000.7"}, {"meter_type": "03", "previous": "05200.4"}]}),
            file="a.pdf", page=2,
        )
        self.assertEqual(record.fields, [])
        cells = {(cell["row"], cell["column"]): cell for cell in record.table_rows}
        self.assertEqual(cells[(2, "previous")]["value"], "05200.4")
        self.assertEqual(cells[(1, "previous")]["column_label"], "上期指數")

    def test_printed_label_matching_a_known_item_is_recognised(self):
        record = page_record(bill(charge_info={"配合減少用電減收": -8976}), file="a.pdf", page=1)
        item = fields_by_key(record)[("charge_info", "demand_response_discount_twd")]
        self.assertTrue(item["recognized"])

    def test_raw_label_copy_of_a_known_field_is_dropped(self):
        record = page_record(
            bill(user_info={"energy_consumption": {"peak_kwh": 346944}, "尖峰度數": 346944}),
            file="a.pdf", page=1,
        )
        self.assertEqual(len(record.fields), 1)
        self.assertEqual(record.summary["duplicates_dropped"], 1)

    def test_headings_paired_with_each_other_are_dropped(self):
        record = page_record(
            bill(
                meter_info={"表別": "上期指數", "裝表指數": "本期指數"},
                payment_info={"缴費期限DueDate": "應缴總金额TotalAmount", "due_date": "110/01/20"},
                charge_introduction={"逾繳費期限": "3~14天"},
            ),
            file="a.pdf", page=1,
        )
        keys = {(item["block"], item["key"]) for item in record.fields}
        self.assertEqual(keys, {("payment_info", "due_date"), ("charge_introduction", "逾繳費期限")})
        self.assertEqual(record.summary["header_mispairs_dropped"], 3)
        self.assertEqual(fields_by_key(record)[("charge_introduction", "逾繳費期限")]["status"], UNVERIFIED)

    def test_cross_check_status_for_every_recognised_field(self):
        primary = bill(
            charge_info={"basic_charge_twd": 849680, "demand_response_discount_twd": -8976},
            other_info={"fuel_cost_per_kwh_twd": 2.3396},
            user_info={"tariff_type": "高壓電力綜合非營業用"},
        )
        windows = bill(
            charge_info={"basic_charge_twd": 849680, "demand_response_discount_twd": "-8976·0元", "meter_rental_twd": 71},
            other_info={"fuel_cost_per_kwh_twd": 23396},
        )
        record = page_record(primary, file="a.pdf", page=9, cross_check=windows)
        fields = fields_by_key(record)

        self.assertEqual(fields[("charge_info", "basic_charge_twd")]["status"], AGREE)
        # Windows renders the decimal point as '·'; still the same amount.
        self.assertEqual(fields[("charge_info", "demand_response_discount_twd")]["status"], AGREE)
        fuel = fields[("other_info", "fuel_cost_per_kwh_twd")]
        self.assertEqual((fuel["status"], fuel["cross_check_value"]), (DISAGREE, 23396))
        rental = fields[("charge_info", "meter_rental_twd")]
        self.assertEqual((rental["status"], rental["source"]), (FILLED, "cross_check"))
        self.assertEqual(fields[("user_info", "tariff_type")]["status"], PRIMARY_ONLY)
        self.assertTrue(record.summary["needs_review"])
        self.assertEqual(record.summary["engines_disagree"], 1)

    def test_value_location_comes_from_the_ocr_line_that_printed_it(self):
        page = bill(charge_info={"basic_charge_twd": 634220})
        page.ocr_lines = [
            OcrLine("基本電費(約定)", 708, 696, 180, 34, "charge_info"),
            OcrLine("634220.0元", 1035, 697, 158, 36, "charge_info"),
        ]
        record = page_record(page, file="a.pdf", page=1)
        self.assertEqual(record.fields[0]["bbox"], {"x": 1035, "y": 697, "width": 158, "height": 36})
        self.assertEqual(record.ocr_lines[1]["text"], "634220.0元")

    def test_cross_check_failure_marks_page_for_review(self):
        record = page_record(
            bill(charge_info={"basic_charge_twd": 1}), file="a.pdf", page=1, cross_check_error="no OCR language"
        )
        self.assertTrue(record.summary["needs_review"])
        self.assertIn("cross_check_failed: no OCR language", record.summary["warnings"])


if __name__ == "__main__":
    unittest.main()
