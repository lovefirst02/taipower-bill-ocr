import unittest
from datetime import date

from taipower_bill_ocr.periods import parse_period, split_by_month


class SplitByMonthTests(unittest.TestCase):
    def test_a_bill_across_two_months_is_shared_by_days(self):
        shares = split_by_month("113/01/15~113/02/14", {"kwh": 31000, "cost": 100000})
        self.assertEqual([(share.month, share.days) for share in shares], [("2024-01", 17), ("2024-02", 14)])
        self.assertEqual([share.amounts["kwh"] for share in shares], [17000, 14000])
        self.assertEqual(sum(share.amounts["cost"] for share in shares), 100000)

    def test_parts_always_add_up_to_the_amount(self):
        for total in (1, 2, 7, 100, 12345, 1480377, 99999):
            shares = split_by_month("113/12/15~114/01/14", {"x": total})
            self.assertEqual(sum(share.amounts["x"] for share in shares), total, total)
        # 10 split over 3 + 3 + 3 + 1 days style uneven shares still adds up.
        shares = split_by_month("2024/01/30~2024/03/01", {"x": 10})
        self.assertEqual(sum(share.amounts["x"] for share in shares), 10)

    def test_decimals_keep_their_precision(self):
        shares = split_by_month("113/01/15~113/02/14", {"kwh": 1234.5})
        self.assertEqual(sum(share.amounts["kwh"] for share in shares), 1234.5)
        self.assertTrue(all(round(share.amounts["kwh"], 1) == share.amounts["kwh"] for share in shares))

    def test_one_month_and_missing_values(self):
        shares = split_by_month("113/01/01~113/01/31", {"kwh": 500, "cost": None})
        self.assertEqual([(share.month, share.days, share.amounts["kwh"], share.amounts["cost"]) for share in shares],
                         [("2024-01", 31, 500, None)])

    def test_leap_february_and_negative_amounts(self):
        shares = split_by_month("113/02/20~113/03/10", {"adj": -101})
        self.assertEqual([share.days for share in shares], [10, 10])  # 2024 has 29 February days
        self.assertEqual(sum(share.amounts["adj"] for share in shares), -101)

    def test_unreadable_periods(self):
        self.assertEqual(split_by_month(None, {"x": 1}), [])
        self.assertEqual(split_by_month("113/02/14~113/01/15", {"x": 1}), [])
        self.assertEqual(parse_period("113/01/01~113/01/31"), (date(2024, 1, 1), date(2024, 1, 31)))


if __name__ == "__main__":
    unittest.main()
