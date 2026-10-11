import unittest

from taipower_bill_ocr.parser import OcrLine, parse_bill_layout


def bill(rows, detailed=False):
    if detailed:
        lines = [OcrLine("基本資料", 100, 20, 120, 20),
                 OcrLine("計費內容", 600, 20, 120, 20),
                 OcrLine("用電資料", 100, 600, 120, 20),
                 OcrLine("電表資料", 600, 600, 120, 20)]
    else:
        lines = [OcrLine("計費內容 Charge Info.", 500, 20, 250, 20)]
    for i, (label, value) in enumerate(rows):
        lines.append(OcrLine(label, 510, 70 + i * 35, 180, 20))
        if value is not None:
            lines.append(OcrLine(value, 750, 70 + i * 35, 100, 20))
    return parse_bill_layout(lines)


class PrepaidChargeTests(unittest.TestCase):
    def test_printed_signed_value_and_chinese_mapping_on_both_layouts(self):
        for detailed in (False, True):
            for label in ("本期預繳", "本期預繳金額"):
                for text, expected in (("-1,200.5元", -1200.5), ("0元", 0), ("+268元", 268)):
                    with self.subTest(detailed=detailed, label=label, text=text):
                        result = bill([(label, text)], detailed)
                        self.assertEqual(result.get("charge_info.prepaid_applied_twd"), expected)
                        self.assertEqual(result.get("by_label.計費內容.本期預繳"), expected)

    def test_inline_value_is_supported_without_colon_on_both_layouts(self):
        for detailed in (False, True):
            with self.subTest(detailed=detailed):
                self.assertEqual(bill([("本期預繳金額 -268元", None)], detailed).get(
                    "charge_info.prepaid_applied_twd"), -268)

    def test_balance_or_historical_amount_is_not_current_applied_amount(self):
        for detailed in (False, True):
            result = bill([("本期預繳餘額", "9000元"), ("本期預繳金額餘額", "8000元"),
                           ("前期預繳金額", "7000元"), ("本期預繳", "-268元")], detailed)
            self.assertEqual(result.get("charge_info.prepaid_applied_twd"), -268)

    def test_absent_or_unreadable_prepaid_is_not_zero(self):
        for detailed in (False, True):
            for rows in ([('本期預繳餘額', '9000元')], [('本期預繳', None)]):
                with self.subTest(detailed=detailed, rows=rows):
                    self.assertIsNone(bill(rows, detailed).get("charge_info.prepaid_applied_twd"))

    def test_small_or_zero_total_after_prepayment_is_not_reconstructed(self):
        for total in ("0元", "20元"):
            for prepaid in ("-980元", None):
                with self.subTest(total=total, prepaid=prepaid):
                    result = bill([("流動電費", "1000元"), ("本期預繳", prepaid), ("應繳總金額", total)])
                    self.assertEqual(result.get("charge_info.total_amount_twd"), int(total[:-1]))

    def test_missing_total_is_not_guessed_from_prepaid_amount(self):
        result = bill([("流動電費", "1,000元"), ("本期預繳", "-1,000元")])
        self.assertIsNone(result.get("charge_info.total_amount_twd"))
