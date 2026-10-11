import unittest

from test_prepaid_charge import bill


class PaymentTotalLabelTests(unittest.TestCase):
    def test_pretax_label_alone_is_not_total(self):
        for detailed in (False, True):
            with self.subTest(detailed=detailed):
                result = bill([("稅前應繳總金額 1175元", None)], detailed)
                self.assertIsNone(result.get("charge_info.total_amount_twd"))

    def test_payment_total_label_on_both_layouts(self):
        for detailed in (False, True):
            for inline in (False, True):
                for separator in ("", ":", "："):
                    with self.subTest(detailed=detailed, inline=inline, separator=separator):
                        row = (("繳費總金額" + separator + " 1,234元", None) if inline
                               else ("繳費總金額" + separator, "1,234元"))
                        result = bill([("稅前應繳總金額", "1175元"), row], detailed)
                        self.assertEqual(result.get("charge_info.total_amount_twd"), 1234)
                        self.assertEqual(result.get("charge_info.pretax_total_twd"), 1175)

    def test_existing_due_total_and_new_label_are_equivalent(self):
        for detailed in (False, True):
            self.assertEqual(bill([("應繳總金額", "1234元")], detailed).get("charge_info.total_amount_twd"),
                             bill([("繳費總金額", "1234元")], detailed).get("charge_info.total_amount_twd"))

    def test_printed_zero_with_prepaid_is_preserved(self):
        for detailed in (False, True):
            with self.subTest(detailed=detailed):
                result = bill([("流動電費", "1000元"), ("本期預繳", "-1000元"),
                               ("繳費總金額", "0元")], detailed)
                self.assertEqual(result.get("charge_info.total_amount_twd"), 0)
