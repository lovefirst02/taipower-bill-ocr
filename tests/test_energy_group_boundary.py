"""A damaged transfer heading must not donate its readings to billed energy."""
import unittest

from taipower_bill_ocr.parser import (
    OcrLine, _energy_groups, _bank_autopay_energy_groups_from_raw_lines,
)


class EnergyGroupBoundaryTests(unittest.TestCase):
    def test_coordinate_group_with_missing_billed_row_does_not_borrow_transfer(self):
        for heading in ("供度數（度）", "專供度數（度）"):
            with self.subTest(heading=heading):
                texts = ["計費度數", "1309", "離峰度數 1776", heading,
                         "經常（尖峰）度數 11891", "週六半尖峰度數 1200"]
                groups, _ = _energy_groups([
                    OcrLine(t, 40, i * 30, 250, 20) for i, t in enumerate(texts)
                ])
                self.assertEqual(groups.get("energy_consumption"), {"off_peak_kwh": 1776})
                self.assertEqual(groups.get("transferred_energy"), {
                    "peak_kwh": 11891, "saturday_semi_peak_kwh": 1200})

    def test_raw_group_preserves_billed_zero_and_recovers_transfer(self):
        groups = _bank_autopay_energy_groups_from_raw_lines([
            "費度數 / Energy", "Consumption(kWh)",
            "週六半尖峰度數", "0", "0", "供度數（度）",
            "週六半尖峰度數", "1200", "1200",
        ])
        self.assertEqual(groups, {
            "energy_consumption": {"saturday_semi_peak_kwh": 0},
            "transferred_energy": {"saturday_semi_peak_kwh": 1200},
        })

    def test_raw_missing_billed_value_stops_at_transfer_heading(self):
        groups = _bank_autopay_energy_groups_from_raw_lines([
            "計費度數 / Energy Consumption(kWh)", "週六半尖峰度數",
            "供度數（度）", "週六半尖峰度數", "1200",
        ])
        self.assertNotIn("saturday_semi_peak_kwh", groups.get("energy_consumption", {}))
        self.assertEqual(groups.get("transferred_energy"), {"saturday_semi_peak_kwh": 1200})

    def test_coordinate_zero_is_retained_with_damaged_transfer_heading(self):
        texts = ["計費度數", "週六半尖峰度數 0", "供度數（度）", "週六半尖峰度數 1200"]
        groups, _ = _energy_groups([OcrLine(t, 40, i * 30, 250, 20) for i, t in enumerate(texts)])
        self.assertEqual(groups, {
            "energy_consumption": {"saturday_semi_peak_kwh": 0},
            "transferred_energy": {"saturday_semi_peak_kwh": 1200},
        })
