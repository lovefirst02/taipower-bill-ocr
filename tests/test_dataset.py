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


def line(text, x, y, block="user_info", width=300, height=30):
    return OcrLine(text, x, y, width, height, block)


def page_with_lines(lines, **blocks):
    result = bill(**blocks)
    result.ocr_lines = lines
    return result


class SupplementTests(unittest.TestCase):
    """Items the parser left in OCR lines are recovered as fields."""

    def record(self, lines, cross_lines=None, **blocks):
        cross = page_with_lines(cross_lines, **blocks) if cross_lines is not None else None
        return page_record(page_with_lines(lines, **blocks), file="a.pdf", page=1, cross_check=cross)

    def test_label_and_value_in_one_line(self):
        record = self.record([line("本公司營利事業統一編號：61904459", 97, 1647)])
        item = fields_by_key(record)[("user_info", "taipower_business_tax_id")]
        self.assertEqual((item["value"], item["origin"], item["recognized"]), ("61904459", "ocr_line", True))
        self.assertEqual(record.summary["supplemented_from_ocr_lines"], 1)

    def test_address_continues_on_the_next_line(self):
        record = self.record([
            line("用電地址：台中市霧峰區柳豐路500號地下1", 98, 748),
            line("層至地上6層", 230, 777, width=159),
        ])
        item = fields_by_key(record)[("user_info", "service_address")]
        self.assertEqual(item["value"], "台中市霧峰區柳豐路500號地下1層至地上6層")

    def test_empty_address_does_not_take_the_next_line(self):
        record = self.record([line("用電地址：", 98, 748), line("電子發票", 98, 777)])
        self.assertNotIn(("user_info", "service_address"), fields_by_key(record))

    def test_combined_labels_split_into_separate_fields(self):
        record = self.record([
            line("本次抄表日/收費", 600, 100, "invoice_info"),
            line("日:113/04/10;113/04/18", 760, 102, "invoice_info"),
        ])
        fields = fields_by_key(record)
        self.assertEqual(fields[("invoice_info", "current_reading_date")]["value"], "113/04/10")
        self.assertEqual(fields[("invoice_info", "收費日")]["value"], "113/04/18")

    def test_dates_run_together_are_split(self):
        record = self.record([line("下次抄表日/扣款日：113/01/30113/02/23", 98, 900)])
        fields = fields_by_key(record)
        self.assertEqual(fields[("user_info", "next_reading_date")]["value"], "113/01/30")
        self.assertEqual(fields[("user_info", "扣款日")]["value"], "113/02/23")

    def test_label_and_number_run_together(self):
        record = self.record([line("每度徽交再生基金0.0023元", 1250, 1300, "other_info")])
        item = fields_by_key(record)[("other_info", "renewable_fund_per_kwh_twd")]
        self.assertEqual((item["value"], item["printed_label"]), (0.0023, "每度徽交再生基金"))

    def test_misread_label_maps_to_known_item_and_keeps_printed_text(self):
        record = self.record([], charge_info={"功率因數调整簧": -711})
        item = fields_by_key(record)[("charge_info", "power_factor_adjustment_twd")]
        self.assertEqual((item["value"], item["printed_label"]), (-711, "功率因數调整簧"))

    def test_non_contract_basic_charge_is_not_taken_as_basic_charge(self):
        record = self.record([line("基本重费(非約定)：1200元", 700, 760, "charge_info")])
        fields = fields_by_key(record)
        self.assertNotIn(("charge_info", "basic_charge_twd"), fields)
        self.assertEqual(fields[("charge_info", "non_contract_basic_charge_twd")]["value"], 1200)

    def test_heading_is_not_filed_under_a_known_item(self):
        record = self.record([], payment_info={"電號CustomerNumber": "缴费日期PaymentDate"})
        self.assertEqual(record.fields, [])

    def test_text_in_an_amount_field_stays_unrecognised(self):
        record = self.record([line("應繳總金額：****2.09.4元", 600, 200, "invoice_info")])
        fields = fields_by_key(record)
        self.assertNotIn(("invoice_info", "total_amount_twd"), fields)
        self.assertFalse(fields[("invoice_info", "應繳總金額")]["recognized"])

    def test_row_partner_is_never_another_label_value_line(self):
        record = self.record([
            line("(含寄不寄送實體帳", 100, 300, "page"),
            line("用電計費期間：104年10月28日至104年", 420, 302, "page"),
        ])
        fields = fields_by_key(record)
        self.assertNotIn(("page", "(含寄不寄送實體帳"), fields)
        self.assertEqual(fields[("page", "billing_period")]["value"], "104年10月28日至104年")

    def test_unrecognised_item_read_alike_by_both_engines_counts_as_agreement(self):
        lines = [line("注意事項：請勿重複繳費", 100, 400, "invoice_info")]
        record = self.record(lines, cross_lines=[line("注意事項:請勿重複繳費", 101, 401, "invoice_info")])
        self.assertEqual(fields_by_key(record)[("invoice_info", "注意事項")]["status"], AGREE)

    def test_masking_and_punctuation_differences_are_not_disagreements(self):
        primary = bill(user_info={"service_address": "台北市 * * * *"}, invoice_info={"remittance_bank": "台灣銀行（代碼004)"})
        windows = bill(user_info={"service_address": "台北市*******"}, invoice_info={"remittance_bank": "台灣銀行(代碼004)"})
        record = page_record(primary, file="a.pdf", page=1, cross_check=windows)
        self.assertEqual({item["status"] for item in record.fields}, {AGREE})


if __name__ == "__main__":
    unittest.main()
