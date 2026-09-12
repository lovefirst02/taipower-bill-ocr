import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from taipower_bill_ocr.parser import OcrLine, parse_bill_layout


class DetailedHighVoltageBillTests(unittest.TestCase):
    def setUp(self):
        self.lines = [
            OcrLine("Billing period:20260601-20260630", 50, 150, 326, 23),
            OcrLine("基本資料", 203, 185, 136, 35),
            OcrLine("計費內容", 648, 185, 133, 35),
            OcrLine("電價種類：", 50, 242, 96, 21),
            OcrLine("高壓電力非營業用", 294, 242, 159, 21),
            OcrLine("時間種類：", 50, 263, 92, 21),
            OcrLine("三段式時間電價", 293, 259, 140, 26),
            OcrLine("繳費期限：", 53, 341, 96, 24),
            OcrLine("115年07月23日", 294, 342, 141, 20),
            OcrLine("基本電費(約定)：", 495, 221, 145, 24),
            OcrLine("335,400.0元", 703, 223, 119, 20),
            OcrLine("流動電費：", 494, 240, 91, 24),
            OcrLine("1,558,228.5元", 704, 242, 137, 21),
            OcrLine("功率因數調整費：", 496, 262, 148, 21),
            OcrLine("-25,450.3元", 703, 263, 115, 20),
            OcrLine("稅前應缴總金额：", 496, 321, 148, 21),
            OcrLine("1.591,649.0元", 703, 322, 137, 21),
            OcrLine("應繳總金額：", 497, 382, 123, 24),
            OcrLine("1,671,231元", 698, 383, 121, 21),
            OcrLine("用電資料", 216, 642, 129, 32),
            OcrLine("電表資料", 647, 640, 128, 32),
            OcrLine("計費度數", 65, 686, 79, 21),
            OcrLine("尖峰度數：", 182, 676, 89, 21),
            OcrLine("128210", 328, 676, 73, 21),
            OcrLine("離峰度數：", 184, 695, 87, 21),
            OcrLine("140054", 328, 695, 74, 21),
            OcrLine("尖峰度數：", 185, 714, 87, 21),
            OcrLine("31190", 328, 714, 62, 21),
            OcrLine("轉供度數", 66, 743, 79, 21),
            OcrLine("半尖峰度數：", 184, 731, 107, 24),
            OcrLine("244800", 328, 733, 72, 18),
            OcrLine("週六半尖峰度數：68600", 184, 750, 206, 21),
            OcrLine("離峰度數：", 184, 769, 90, 24),
            OcrLine("253146", 329, 772, 72, 18),
            OcrLine("經常（尖峰）契約：", 68, 790, 167, 20),
            OcrLine("1500", 330, 790, 49, 19),
            OcrLine("經常（尖峰）需量：", 69, 808, 164, 21),
            OcrLine("1428", 330, 809, 49, 18),
            OcrLine("功率因數（%）：", 70, 864, 145, 21),
            OcrLine("100", 331, 864, 38, 18),
            OcrLine("組別：01(經常)", 498, 675, 98, 17),
            OcrLine("表號：021900276", 634, 674, 121, 17),
            OcrLine("電表倍數：2000", 785, 675, 107, 14),
            OcrLine("表別", 495, 691, 43, 22),
            OcrLine("上期指數", 582, 689, 70, 22),
            OcrLine("本期指數", 838, 689, 78, 22),
            OcrLine("01", 495, 712, 27, 20),
            OcrLine("06280.8", 582, 711, 78, 20),
            OcrLine("06403.2", 838, 710, 77, 18),
        ]

    def test_auto_classifies_the_four_panel_detailed_layout(self):
        result = parse_bill_layout(self.lines)

        self.assertEqual(result.get("document_type"), "detailed_high_voltage_bill")
        self.assertEqual(
            set(result["blocks"]),
            {"user_info", "charge_info", "energy_usage", "meter_info"},
        )

    def test_incomplete_marker_set_does_not_enter_four_panel_parser(self):
        lines = [
            line
            for line in self.lines
            if not line.text.startswith("計費內容")
        ]

        result = parse_bill_layout(lines)

        self.assertNotEqual(result.get("document_type"), "detailed_high_voltage_bill")

    def test_energy_groups_recover_layout_when_meter_title_is_missed(self):
        """Catches readable usage rows being flattened into charge_info."""
        lines = [
            line for line in self.lines if not line.text.startswith("電表資料")
        ]

        result = parse_bill_layout(lines)

        self.assertEqual(result.get("document_type"), "detailed_high_voltage_bill")
        self.assertEqual(
            result.get("energy_usage.energy_consumption"),
            {"peak_kwh": 128210, "off_peak_kwh": 140054},
        )
        self.assertEqual(
            result.get("energy_usage.transferred_energy"),
            {
                "peak_kwh": 31190,
                "semi_peak_kwh": 244800,
                "saturday_semi_peak_kwh": 68600,
                "off_peak_kwh": 253146,
            },
        )

    def test_original_panel_titles_classify_without_annotation_text(self):
        """Catches the optional blue Billing period annotation becoming required."""
        lines = [
            line for line in self.lines if "Billing period" not in line.text
        ]

        result = parse_bill_layout(lines)

        self.assertEqual(result.get("document_type"), "detailed_high_voltage_bill")
        self.assertEqual(
            result.get("energy_usage.energy_consumption"),
            {"peak_kwh": 128210, "off_peak_kwh": 140054},
        )
        self.assertEqual(
            result.get("energy_usage.transferred_energy"),
            {
                "peak_kwh": 31190,
                "semi_peak_kwh": 244800,
                "saturday_semi_peak_kwh": 68600,
                "off_peak_kwh": 253146,
            },
        )
        self.assertEqual(result.get("energy_usage.contract_capacity_kw"), 1500)
        self.assertEqual(result.get("energy_usage.max_demand_kw"), 1428)
        self.assertEqual(result.get("energy_usage.power_factor_percent"), 100)

    def test_accepts_common_simplified_character_ocr_variants(self):
        substitutions = {
            "計費內容": "計費内容",
            "組別：01(經常)": "组别：01(經常)",
            "電表倍數：2000": "電表倍数：2000",
        }
        lines = [
            OcrLine(
                substitutions.get(line.text, line.text),
                line.x,
                line.y,
                line.width,
                line.height,
            )
            for line in self.lines
        ]

        result = parse_bill_layout(lines)

        self.assertEqual(result.get("document_type"), "detailed_high_voltage_bill")
        self.assertEqual(result.get("meter_info.group_code"), "01")
        self.assertEqual(result.get("meter_info.multiplier"), 2000)

    def test_uses_group_ranges_for_compact_usage_table(self):
        """Catches midpoint grouping and the previous-period rows leaking in."""
        lines = [
            OcrLine("Billing period:20260601-20260630", 342, 1211, 697, 57),
            OcrLine("基本資料", 662, 1282, 293, 80),
            OcrLine("計費內容", 1629, 1287, 297, 77),
            OcrLine("用電資料", 685, 2252, 278, 73),
            OcrLine("電表資料", 1619, 2266, 285, 73),
            OcrLine("計費度數", 367, 2338, 169, 48),
            OcrLine("尖峰度數：", 617, 2326, 187, 44),
            OcrLine("128210", 928, 2333, 155, 42),
            OcrLine("離峰度數：", 619, 2365, 185, 44),
            OcrLine("140054", 929, 2372, 153, 40),
            OcrLine("尖峰度數：", 619, 2404, 187, 47),
            OcrLine("31190", 927, 2411, 129, 42),
            OcrLine("轉供度數", 371, 2458, 169, 46),
            OcrLine("半尖峰度數：", 620, 2443, 229, 47),
            OcrLine("244800", 928, 2450, 155, 42),
            OcrLine("调六半尖峰度數：68600", 621, 2481, 438, 52),
            OcrLine("離峰度數：", 621, 2524, 187, 43),
            OcrLine("253146", 930, 2532, 153, 38),
            OcrLine("經常（尖峰）契约：", 374, 2556, 349, 51),
            OcrLine("1500", 930, 2570, 105, 40),
            OcrLine("前期轉供度數", 387, 2885, 242, 45),
            OcrLine("週六半尖峰度數：80400", 630, 2911, 433, 48),
        ]

        result = parse_bill_layout(lines)

        self.assertEqual(
            result.get("energy_usage.transferred_energy"),
            {
                "peak_kwh": 31190,
                "semi_peak_kwh": 244800,
                "saturday_semi_peak_kwh": 68600,
                "off_peak_kwh": 253146,
            },
        )
        self.assertEqual(
            len(result.get("energy_usage.raw_energy_rows.energy_consumption")), 2
        )
        self.assertEqual(result.get("energy_usage.contract_capacity_kw"), 1500)

    def test_boundary_adjacent_peak_belongs_to_transfer_group(self):
        """Catches the first transfer row being pulled into billed consumption."""
        result = parse_bill_layout(self.lines)

        self.assertEqual(
            result.get("energy_usage.transferred_energy.peak_kwh"), 31190
        )
        self.assertEqual(
            len(result.get("energy_usage.raw_energy_rows.energy_consumption")), 2
        )
        self.assertEqual(
            len(result.get("energy_usage.raw_energy_rows.transferred_energy")), 4
        )

    def test_usage_rows_are_not_dropped_at_low_pdf_render_scale(self):
        """Catches the fixed x<150 filter deleting rows rendered at low DPI."""
        scaled = [
            OcrLine(
                line.text,
                line.x * 0.5,
                line.y * 0.5,
                line.width * 0.5,
                line.height * 0.5,
            )
            for line in self.lines
        ]

        result = parse_bill_layout(scaled)

        self.assertEqual(
            result.get("energy_usage.energy_consumption"),
            {"peak_kwh": 128210, "off_peak_kwh": 140054},
        )
        self.assertEqual(
            result.get("energy_usage.transferred_energy"),
            {
                "peak_kwh": 31190,
                "semi_peak_kwh": 244800,
                "saturday_semi_peak_kwh": 68600,
                "off_peak_kwh": 253146,
            },
        )

    def test_extracts_fields_from_each_detailed_layout_panel(self):
        result = parse_bill_layout(self.lines)

        self.assertEqual(result.get("user_info.tariff_type"), "高壓電力非營業用")
        self.assertEqual(result.get("user_info.time_type"), "三段式時間電價")
        self.assertEqual(
            result.get("user_info.billing_period"),
            {"start": "115/06/01", "end": "115/06/30"},
        )
        self.assertEqual(result.get("charge_info.basic_charge_twd"), 335400)
        self.assertEqual(result.get("charge_info.energy_charge_twd"), 1558228.5)
        self.assertEqual(result.get("charge_info.pretax_total_twd"), 1591649)
        self.assertEqual(result.get("charge_info.total_amount_twd"), 1671231)
        self.assertEqual(
            result.get("energy_usage.transferred_energy"),
            {
                "peak_kwh": 31190,
                "semi_peak_kwh": 244800,
                "saturday_semi_peak_kwh": 68600,
                "off_peak_kwh": 253146,
            },
        )
        self.assertEqual(result.get("energy_usage.contract_capacity_kw"), 1500)
        self.assertEqual(result.get("energy_usage.max_demand_kw"), 1428)
        self.assertEqual(result.get("energy_usage.power_factor_percent"), 100)
        self.assertEqual(result.get("meter_info.meter_number"), "021900276")
        self.assertEqual(result.get("meter_info.multiplier"), 2000)
        self.assertEqual(
            result.get("meter_info.readings")[0],
            {
                "meter_type": "01",
                "previous": "06280.8",
                "removed": None,
                "installed": None,
                "current": "06403.2",
            },
        )

    def test_extracts_complete_original_basic_information_panel(self):
        """Catches the detailed parser returning only tariff and due date."""
        lines = [
            line for line in self.lines if "Billing period" not in line.text
        ] + [
            OcrLine("代繳帳號：", 53, 303, 96, 21),
            OcrLine("000005830******", 293, 303, 156, 21),
            OcrLine("帳單月份：", 53, 320, 96, 21),
            OcrLine("115年07月", 295, 323, 110, 20),
            OcrLine("計費期間：", 54, 369, 96, 21),
            OcrLine("115年06月01日至11", 294, 362, 165, 20),
            OcrLine("5年06月30日", 293, 377, 120, 20),
            OcrLine("本次抄表日：", 56, 400, 110, 21),
            OcrLine("115年07月01日", 295, 400, 141, 20),
            OcrLine("下次抄表日：", 57, 419, 110, 21),
            OcrLine("115年08月01日", 296, 419, 141, 20),
            OcrLine("輪流停電組別：", 56, 437, 128, 21),
            OcrLine("B", 296, 440, 15, 18),
            OcrLine("饋線代號：", 58, 458, 96, 21),
            OcrLine("YY56", 296, 458, 50, 20),
            OcrLine("客服專線：", 59, 478, 96, 21),
            OcrLine("1911", 297, 479, 45, 20),
            OcrLine("本公司營利事業統一編號：34096996", 58, 495, 310, 21),
            OcrLine("服務單位：", 60, 516, 96, 21),
            OcrLine("林口服務所", 297, 515, 110, 21),
            OcrLine("服務地址：", 62, 546, 96, 21),
            OcrLine("桃園市龜山區文化一路", 297, 546, 205, 21),
            OcrLine("51號", 297, 565, 45, 20),
            OcrLine("行業別：", 61, 580, 80, 21),
            OcrLine("其他電力設備及配備製", 298, 570, 210, 21),
            OcrLine("造業(289)", 298, 589, 95, 21),
        ]

        result = parse_bill_layout(lines)

        expected = {
            "autopay_account": "000005830******",
            "bill_month": "115/07",
            "billing_period": {"start": "115/06/01", "end": "115/06/30"},
            "current_reading_date": "115/07/01",
            "next_reading_date": "115/08/01",
            "outage_group": "B",
            "feeder_code": "YY56",
            "customer_service_phone": "1911",
            "business_tax_id": "34096996",
            "service_unit": "林口服務所",
            "service_address": "桃園市龜山區文化一路51號",
            "industry": "其他電力設備及配備製造業(289)",
        }
        for key, value in expected.items():
            self.assertEqual(result.get(f"user_info.{key}"), value, key)

    def test_normalizes_observed_basic_info_ocr_variants(self):
        """Catches real Paddle typos and tall-line continuations staying raw."""
        replacements = {
            "高壓電力非營業用": "高座電力非堂案用",
            "三段式時間電價": "段式時間電價",
        }
        lines = [
            OcrLine(
                replacements.get(line.text, line.text),
                line.x,
                line.y,
                line.width,
                line.height,
            )
            for line in self.lines
        ] + [
            OcrLine("请線代號：", 58, 458, 96, 21),
            OcrLine("YY56", 296, 458, 50, 20),
            OcrLine("本公司坐利事業统一缅就：", 58, 495, 250, 21),
            OcrLine("34096996", 340, 495, 90, 21),
            OcrLine("服務地址：", 62, 546, 96, 50),
            OcrLine("桃園市山區文化一路", 297, 535, 205, 40),
            OcrLine("51號", 297, 570, 45, 40),
            OcrLine("行業別：", 61, 600, 80, 50),
            OcrLine("其他電力設備及配備製", 298, 575, 210, 40),
            OcrLine("造業(289)", 298, 635, 95, 40),
        ]

        result = parse_bill_layout(lines)

        self.assertEqual(result.get("user_info.tariff_type"), "高壓電力非營業用")
        self.assertEqual(result.get("user_info.time_type"), "三段式時間電價")
        self.assertEqual(result.get("user_info.feeder_code"), "YY56")
        self.assertEqual(result.get("user_info.business_tax_id"), "34096996")
        self.assertEqual(
            result.get("user_info.service_address"), "桃園市龜山區文化一路51號"
        )
        self.assertEqual(
            result.get("user_info.industry"), "其他電力設備及配備製造業(289)"
        )

    def test_extracts_customer_number_from_inline_and_separate_value(self):
        """Catches the detailed Basic Info parser omitting the 電號 row."""
        variants = (
            [OcrLine("電號：00123456789", 50, 220, 210, 21)],
            [
                OcrLine("電號：", 50, 220, 80, 21),
                OcrLine("00-12-3456-78-9", 294, 220, 155, 21),
            ],
            [
                OcrLine("电號：", 50, 220, 80, 21),
                OcrLine("00123456789", 294, 220, 135, 21),
            ],
        )

        for extra_lines in variants:
            with self.subTest(lines=[line.text for line in extra_lines]):
                result = parse_bill_layout([*self.lines, *extra_lines])
                expected = {
                    "電號：00123456789": "00123456789",
                    "電號：": "00-12-3456-78-9",
                    "电號：": "00123456789",
                }[extra_lines[0].text]
                self.assertEqual(result.get("user_info.customer_number"), expected)


if __name__ == "__main__":
    unittest.main()
