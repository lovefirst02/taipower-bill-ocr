import unittest

from taipower_bill_ocr.checks import (
    billed_kwh,
    charges_difference,
    check_field,
    roc_date,
    tax_difference,
)

# Page 8 of the 2024 high-voltage sample, as printed on the bill.
PAGE_8 = {
    ("charge_info", "basic_charge_twd"): 849680,
    ("charge_info", "energy_charge_twd"): 2739373,
    ("charge_info", "discount_twd"): -107671.5,
    ("charge_info", "power_factor_adjustment_twd"): -52220.7,
    ("charge_info", "ebill_discount_twd"): -10,
    ("charge_info", "meter_rental_twd"): 71,
    ("charge_info", "pretax_total_twd"): 3265926,
    ("charge_info", "total_amount_twd"): 3429222,
    ("user_info", "energy_consumption.peak_kwh"): 157904,
    ("user_info", "energy_consumption.semi_peak_kwh"): 330520,
    ("user_info", "energy_consumption.saturday_semi_peak_kwh"): 85680,
    ("user_info", "energy_consumption.off_peak_kwh"): 298248,
    ("user_info", "energy_consumption.discount_kwh"): 872352,  # not part of the total
    ("user_info", "billing_period.start"): "113/08/01",
    ("user_info", "billing_period.end"): "113/08/31",
}


def results(block, key, value, values=PAGE_8):
    return {result.name: result.passed for result in check_field(block, key, value, values)}


class BillArithmeticTests(unittest.TestCase):
    def test_charge_lines_add_up_to_the_total(self):
        self.assertAlmostEqual(charges_difference(PAGE_8), -0.2)
        self.assertEqual(results("charge_info", "basic_charge_twd", 849680), {"charges_sum": True})

    def test_a_misread_charge_breaks_the_sum_and_is_not_reported_as_passing(self):
        self.assertEqual(results("charge_info", "basic_charge_twd", 84968), {})

    def test_tax_relates_pretax_and_total(self):
        self.assertAlmostEqual(tax_difference(PAGE_8), 0.3)
        self.assertEqual(results("charge_info", "pretax_total_twd", 3265926), {"tax": True})
        self.assertEqual(results("charge_info", "pretax_total_twd", 326592), {"tax": False})

    def test_billed_kwh_sums_time_of_use_rows_only(self):
        self.assertEqual(billed_kwh(PAGE_8), 872352)

    def test_carbon_emission_is_about_half_a_kg_per_kwh(self):
        self.assertEqual(results("other_info", "carbon_emission_kg", 430942), {"carbon_per_kwh": True})
        self.assertEqual(results("other_info", "carbon_emission_kg", 4302), {"carbon_per_kwh": False})

    def test_billing_period_must_span_one_or_two_months(self):
        self.assertEqual(results("user_info", "billing_period.end", "113/08/31"), {"billing_period": True})
        self.assertEqual(results("user_info", "billing_period.end", "113/08/01"), {"billing_period": False})
        self.assertEqual(results("user_info", "billing_period.end", "113/02/30"), {"billing_period": False})


class RangeAndFormatTests(unittest.TestCase):
    def test_per_kwh_rates_stay_in_range(self):
        self.assertEqual(results("other_info", "fuel_cost_per_kwh_twd", 2.3396), {"range": True})
        self.assertEqual(results("other_info", "fuel_cost_per_kwh_twd", 23396), {"range": False})
        self.assertEqual(results("other_info", "renewable_fund_per_kwh_twd", 0.0023), {"range": True})

    def test_power_factor_is_a_percentage(self):
        self.assertEqual(results("user_info", "power_factor_percent", 98), {"range": True})
        self.assertEqual(results("user_info", "power_factor_percent", 980), {"range": False})

    def test_dates_must_exist(self):
        self.assertEqual(roc_date("113/02/29").isoformat(), "2024-02-29")
        self.assertIsNone(roc_date("112/02/29"))
        self.assertEqual(results("user_info", "next_reading_date", "113/11/01"), {"date": True})
        self.assertEqual(results("user_info", "next_reading_date", "113/13/01"), {"date": False})

    def test_fields_without_a_check_get_none(self):
        self.assertEqual(results("other_info", "feeder_code", "FI5C"), {})


if __name__ == "__main__":
    unittest.main()
