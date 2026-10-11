import unittest

from test_prepaid_charge import bill


class ChargeAdjustmentTests(unittest.TestCase):
    fields = {
        "本期預繳餘額": "current_prepaid_balance_twd",
        "上期預繳餘額": "previous_prepaid_balance_twd",
        "電價調整金額": "electricity_price_adjustment_twd",
        "補收(退)金額(含停電扣減)": "supplemental_charge_refund_twd",
    }

    def test_distinct_fields_in_both_layouts(self):
        for detailed in (False, True):
            with self.subTest(detailed=detailed):
                result = bill([("本期預繳", "-268元"), ("本期預繳餘額", "100元"),
                               ("上期預繳餘額", "368元"), ("電價調整金額", "-12.5元"),
                               ("補收(退)金額(含停電扣減)", "+20元")], detailed)
                self.assertEqual(result.get("charge_info.prepaid_applied_twd"), -268)
                for key, value in [("current_prepaid_balance_twd", 100),
                                   ("previous_prepaid_balance_twd", 368),
                                   ("electricity_price_adjustment_twd", -12.5),
                                   ("supplemental_charge_refund_twd", 20)]:
                    self.assertEqual(result.get("charge_info." + key), value)

    def test_inline_and_split_signed_amounts(self):
        for detailed in (False, True):
            for label, key in self.fields.items():
                for amount, expected in [("0元", 0), ("-1,200.5元", -1200.5), ("+20元", 20)]:
                    for inline in (False, True):
                        with self.subTest(detailed=detailed, label=label, amount=amount, inline=inline):
                            rows = [(label + "：" + amount, None)] if inline else [(label, amount)]
                            self.assertEqual(bill(rows, detailed).get("charge_info." + key), expected)

    def test_fullwidth_parentheses_and_whitespace(self):
        for detailed in (False, True):
            result = bill([("補收（退）金額\n（含停電扣減）", "-20元")], detailed)
            self.assertEqual(result.get("charge_info.supplemental_charge_refund_twd"), -20)

    def test_missing_fields_do_not_become_zero(self):
        for detailed in (False, True):
            result = bill([("流動電費", "1000元")], detailed)
            for key in self.fields.values():
                self.assertIsNone(result.get("charge_info." + key))

    def test_adjustment_preserves_small_printed_total(self):
        for label in self.fields:
            for amount in ("-1000元", None):
                with self.subTest(label=label, amount=amount):
                    result = bill([("流動電費", "1000元"), (label, amount), ("應繳總金額", "0元")])
                    self.assertEqual(result.get("charge_info.total_amount_twd"), 0)
