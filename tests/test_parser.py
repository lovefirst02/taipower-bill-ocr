import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from taipower_bill_ocr.parser import OcrLine, parse_bill_layout


class BillLayoutParserTests(unittest.TestCase):
    def test_matches_tightly_spaced_energy_rows_and_rejects_percent_noise(self):
        """Catches adjacent-row values and 111% OCR noise replacing kWh values."""
        lines = [
            OcrLine("用戶資訊 Basic Info.", 85, 300, 180, 24),
            OcrLine("計費度數（度）/Energy Consumption(kWh)", 92, 511, 392, 33),
            OcrLine("經常(尖峰)度數", 114, 535, 138, 21),
            OcrLine("912", 446, 539, 39, 23),
            OcrLine("週六半尖峰度數", 113, 552, 138, 26),
            OcrLine("107", 447, 558, 37, 23),
            OcrLine("離峰度數", 116, 574, 78, 21),
            OcrLine("780", 447, 578, 38, 23),
            OcrLine("轉供度數（度）", 96, 590, 132, 27),
            OcrLine("經常(尖峰)度數", 115, 610, 131, 26),
            OcrLine("10968", 424, 618, 60, 22),
            OcrLine("週六半尖峰度數", 115, 629, 136, 26),
            OcrLine("1333", 433, 637, 51, 22),
            OcrLine("離峰度數", 117, 650, 79, 24),
            OcrLine("111%", 410, 657, 42, 20),
            OcrLine("8820", 435, 658, 47, 19),
        ]

        result = parse_bill_layout(lines)

        self.assertEqual(
            result.get("user_info.energy_consumption"),
            {
                "peak_kwh": 912,
                "saturday_semi_peak_kwh": 107,
                "off_peak_kwh": 780,
            },
        )
        self.assertEqual(
            result.get("user_info.transferred_energy"),
            {
                "peak_kwh": 10968,
                "saturday_semi_peak_kwh": 1333,
                "off_peak_kwh": 8820,
            },
        )

    def test_does_not_reuse_duplicate_ocr_value_for_a_missing_energy_row(self):
        """Catches duplicate OCR passes filling two printed rows with one value."""
        lines = [
            OcrLine("用戶資訊 Basic Info.", 40, 100, 300, 24),
            OcrLine("計費度數（度）", 50, 140, 150, 18),
            OcrLine("經常(尖峰)度數", 70, 170, 150, 20),
            OcrLine("912", 260, 174, 40, 12),
            OcrLine("912", 261, 175, 40, 12),
            OcrLine("週六半尖峰度數", 70, 177, 170, 20),
        ]

        result = parse_bill_layout(lines)

        self.assertEqual(
            result.get("user_info.energy_consumption"),
            {"peak_kwh": 912},
        )

    def test_accepts_mixed_traditional_and_simplified_energy_labels(self):
        """Catches Paddle mixing scripts inside otherwise valid energy labels."""
        lines = [
            OcrLine("用戶資訊 Basic Info.", 40, 100, 300, 24),
            OcrLine("計费度數（度）/Energy Consumption(kWh)", 50, 140, 270, 18),
            OcrLine("經常(尖峰)度数 13680", 70, 170, 190, 18),
            OcrLine("週六半尖峰度数 1200", 70, 200, 190, 18),
            OcrLine("離峰度數 7520", 70, 230, 150, 18),
        ]

        result = parse_bill_layout(lines)

        self.assertEqual(
            result.get("user_info.energy_consumption"),
            {
                "peak_kwh": 13680,
                "saturday_semi_peak_kwh": 1200,
                "off_peak_kwh": 7520,
            },
        )

    def test_pairs_energy_values_that_fall_near_the_neighboring_column(self):
        """Catches right-edge user values being assigned to the charge block."""
        lines = [
            OcrLine("用戶資訊 Basic Info.", 85, 537, 180, 24),
            OcrLine("計費內容 Charge Info.", 469, 539, 197, 24),
            OcrLine("計費度數（度）/Energy Consumption(kWh)", 90, 780, 338, 18),
            OcrLine("經常(尖峰)度數", 107, 797, 121, 18),
            OcrLine("13680", 377, 798, 54, 18),
            OcrLine("週六半尖峰度數", 109, 814, 117, 18),
            OcrLine("1200", 384, 813, 48, 18),
            OcrLine("離峰度數", 109, 830, 69, 18),
            OcrLine("7520", 388, 833, 43, 18),
        ]

        result = parse_bill_layout(lines)

        self.assertEqual(
            result.get("user_info.energy_consumption"),
            {
                "peak_kwh": 13680,
                "saturday_semi_peak_kwh": 1200,
                "off_peak_kwh": 7520,
            },
        )

    def test_does_not_fill_missing_billed_row_from_transfer_when_heading_is_missed(self):
        """Catches a missed transfer heading extending the billed group to block end."""
        lines = [
            OcrLine("用戶資訊 Basic Info.", 40, 100, 300, 24),
            OcrLine("計費度數（度）", 50, 140, 150, 18),
            OcrLine("經常(尖峰)度數 1513", 70, 170, 190, 18),
            OcrLine("離峰度數 1440", 70, 200, 150, 18),
            OcrLine("轉供度教（度）", 50, 240, 150, 18),
            OcrLine("經常(尖峰)度數 6887", 70, 270, 190, 18),
            OcrLine("週六半尖峰度數 547", 70, 300, 190, 18),
            OcrLine("離峰度數 7440", 70, 330, 150, 18),
        ]

        result = parse_bill_layout(lines)

        self.assertEqual(
            result.get("user_info.energy_consumption"),
            {"peak_kwh": 1513, "off_peak_kwh": 1440},
        )
        self.assertEqual(
            result.get("user_info.transferred_energy"),
            {
                "peak_kwh": 6887,
                "saturday_semi_peak_kwh": 547,
                "off_peak_kwh": 7440,
            },
        )
        self.assertIn(
            "transferred_energy_heading_inferred",
            result.get("quality.warnings"),
        )

    def test_does_not_assign_ungrouped_repeated_energy_row_to_billed_energy(self):
        """Catches the generic field fallback treating every energy row as billed."""
        lines = [
            OcrLine("用戶資訊 Basic Info.", 40, 100, 300, 24),
            OcrLine("週六半尖峰度數 547", 70, 180, 190, 18),
        ]

        result = parse_bill_layout(lines)

        self.assertIsNone(result.get("user_info.energy_consumption"))

    def test_extracts_parenthesized_peak_rows_from_billed_and_transferred_groups(self):
        """Catches dropping 經常(尖峰) rows from high-voltage bills."""
        lines = [
            OcrLine("用戶資訊 Basic Info.", 40, 100, 300, 24),
            OcrLine("計費度數（度）/ Energy Consumption(kWh)", 50, 140, 270, 18),
            OcrLine("經常(尖峰)度數", 70, 170, 150, 18),
            OcrLine("1513", 260, 170, 50, 18),
            OcrLine("週六半尖峰度數", 70, 200, 150, 18),
            OcrLine("53", 270, 200, 30, 18),
            OcrLine("離峰度數", 70, 230, 100, 18),
            OcrLine("1440", 260, 230, 50, 18),
            OcrLine("转供度数（度）", 50, 270, 150, 18),
            OcrLine("经常(尖峰)度数", 70, 300, 150, 18),
            OcrLine("6887", 260, 300, 50, 18),
            OcrLine("周六半尖峰度数", 70, 330, 150, 18),
            OcrLine("547", 270, 330, 40, 18),
            OcrLine("离峰度数", 70, 360, 100, 18),
            OcrLine("7440", 260, 360, 50, 18),
        ]

        result = parse_bill_layout(lines)

        self.assertEqual(
            result.get("user_info.energy_consumption"),
            {
                "peak_kwh": 1513,
                "saturday_semi_peak_kwh": 53,
                "off_peak_kwh": 1440,
            },
        )
        self.assertEqual(
            result.get("user_info.transferred_energy"),
            {
                "peak_kwh": 6887,
                "saturday_semi_peak_kwh": 547,
                "off_peak_kwh": 7440,
            },
        )

    def test_keeps_billed_and_transferred_energy_in_separate_groups(self):
        """Catches filling a missing billed row from the transferred-energy group."""
        lines = [
            OcrLine("用戶資訊 Basic Info.", 40, 100, 300, 24),
            OcrLine("計費度數（度）", 50, 140, 150, 18),
            OcrLine("尖峰度數 8000", 70, 170, 150, 18),
            OcrLine("離峰度數 1000", 70, 200, 150, 18),
            OcrLine("轉供度數（度）", 50, 240, 150, 18),
            OcrLine("尖峰度數 100", 70, 270, 150, 18),
            OcrLine("半尖峰度數 200", 70, 300, 170, 18),
        ]

        result = parse_bill_layout(lines)

        self.assertEqual(result.get("user_info.energy_consumption.peak_kwh"), 8000)
        self.assertEqual(result.get("user_info.energy_consumption.off_peak_kwh"), 1000)
        self.assertIsNone(result.get("user_info.energy_consumption.semi_peak_kwh"))
        self.assertEqual(result.get("user_info.transferred_energy.peak_kwh"), 100)
        self.assertEqual(
            result.get("user_info.transferred_energy.semi_peak_kwh"), 200
        )

    def test_preserves_new_fields_and_entirely_unknown_blocks(self):
        """Catches silently discarding fields added by a future bill revision."""
        lines = [
            OcrLine("用戶資訊 Basic Info.", 40, 100, 220, 24),
            OcrLine("電價種類", 50, 140, 90, 18),
            OcrLine("表燈非營業用", 170, 140, 130, 18),
            OcrLine("新制回饋度數", 50, 170, 120, 18),
            OcrLine("18.5", 210, 170, 45, 18),
            OcrLine("再生能源資訊 Renewable Energy Info.", 40, 260, 330, 24),
            OcrLine("綠電憑證張數", 50, 300, 120, 18),
            OcrLine("12", 210, 300, 30, 18),
            OcrLine("憑證批號", 50, 330, 90, 18),
            OcrLine("REC-2026-A", 210, 330, 110, 18),
        ]

        result = parse_bill_layout(lines)

        self.assertEqual(result.get("user_info.新制回饋度數"), 18.5)
        self.assertEqual(
            result["blocks"]["unknown_1"]["title"],
            "再生能源資訊 Renewable Energy Info.",
        )
        self.assertEqual(result.get("unknown_1.綠電憑證張數"), 12)
        self.assertEqual(result.get("unknown_1.憑證批號"), "REC-2026-A")
        self.assertEqual(
            result["by_label"]["再生能源資訊"]["綠電憑證張數"], 12
        )

    def test_separates_basic_and_charge_columns_into_queryable_blocks(self):
        """Catches losing fields when two side-by-side blocks share Y positions."""
        lines = [
            OcrLine("用戶資訊 Basic Info.", x=40, y=200, width=220, height=24),
            OcrLine("計費內容 Charge Info.", x=280, y=200, width=220, height=24),
            OcrLine("用電種類：表燈非營業", x=45, y=240, width=190, height=18),
            OcrLine("流動電費 1474.9元", x=285, y=240, width=190, height=18),
            OcrLine("計費度數 705", x=45, y=270, width=160, height=18),
            OcrLine("電子帳單優惠減收金額 -10.0元", x=285, y=270, width=210, height=18),
            OcrLine("公共用電分攤戶數 106", x=45, y=300, width=190, height=18),
            OcrLine("應繳總金額 1,687元", x=285, y=300, width=190, height=18),
        ]

        result = parse_bill_layout(lines)

        self.assertEqual(result.get("user_info.energy_consumption_kwh"), 705)
        self.assertEqual(result.get("user_info.shared_households"), 106)
        self.assertEqual(result.get("charge_info.energy_charge_twd"), 1474.9)
        self.assertEqual(result.get("charge_info.total_amount_twd"), 1687)

    def test_pairs_spaced_labels_with_values_to_their_right(self):
        """Catches dropping table values emitted separately from spaced OCR labels."""
        lines = [
            OcrLine("用 戶 資 訊 Basic Info.", 100, 300, 260, 30),
            OcrLine("計 費 內 容 Charge Info.", 500, 300, 270, 30),
            OcrLine("其 他 資 訊 Other Info.", 900, 600, 250, 30),
            OcrLine("前 期 發 票 資 訊 Invoice", 100, 900, 300, 30),
            OcrLine("電 價 種 類", 110, 350, 100, 20),
            OcrLine("表 燈 非 營 業 用", 300, 350, 160, 20),
            OcrLine("時 間 種 類", 110, 380, 100, 20),
            OcrLine("非 時 間 電 價", 300, 380, 140, 20),
            OcrLine("底 度", 110, 410, 60, 20),
            OcrLine("40", 430, 410, 30, 20),
            OcrLine("經 常 度 數", 130, 440, 100, 20),
            OcrLine("610", 420, 440, 40, 20),
            OcrLine("公 共 分 攤 戶 數", 110, 470, 150, 20),
            OcrLine("434", 420, 470, 40, 20),
            OcrLine("本公司營利事業統一編號", 110, 500, 210, 20),
            OcrLine("00423107", 360, 500, 80, 20),
            OcrLine("計 費 期 間", 110, 530, 100, 20),
            OcrLine("113/02/06 至 113/04/09", 250, 530, 210, 20),
            OcrLine("本次抄表日/收費日", 110, 560, 170, 20),
            OcrLine("113/04/10；113/04/16", 300, 560, 160, 20),
            OcrLine("下次抄表日/收費日", 110, 590, 170, 20),
            OcrLine("113/06/11；113/06/19", 300, 590, 160, 20),
            OcrLine("流 動 電 費", 510, 350, 100, 20),
            OcrLine("1173.0 元", 720, 350, 80, 20),
            OcrLine("分 攤 公 共 電 費", 510, 380, 150, 20),
            OcrLine("920.7 元", 720, 380, 80, 20),
            OcrLine("應 繳 總 金 額", 510, 410, 130, 20),
            OcrLine("2,094 元", 720, 410, 80, 20),
            OcrLine("輪 流 停 電 組 別", 910, 650, 150, 20),
            OcrLine("H", 1120, 650, 20, 20),
            OcrLine("饋 線 代 號", 910, 680, 100, 20),
            OcrLine("CY36", 1080, 680, 60, 20),
            OcrLine("發 票 號 碼", 110, 950, 100, 20),
            OcrLine("WG-73787182", 300, 950, 130, 20),
            OcrLine("金 額 ( 元 )", 110, 980, 100, 20),
            OcrLine("1467", 370, 980, 60, 20),
        ]

        result = parse_bill_layout(lines)

        self.assertEqual(result.get("user_info.tariff_type"), "表燈非營業用")
        self.assertEqual(result.get("user_info.time_type"), "非時間電價")
        self.assertEqual(result.get("user_info.base_reading"), 40)
        self.assertEqual(result.get("user_info.energy_consumption_kwh"), 610)
        self.assertEqual(result.get("user_info.business_tax_id"), "00423107")
        self.assertEqual(result.get("user_info.billing_period.start"), "113/02/06")
        self.assertEqual(result.get("user_info.billing_period.end"), "113/04/09")
        self.assertEqual(result.get("user_info.current_reading_date"), "113/04/10")
        self.assertEqual(result.get("user_info.current_charge_date"), "113/04/16")
        self.assertEqual(result.get("user_info.next_reading_date"), "113/06/11")
        self.assertEqual(result.get("user_info.next_charge_date"), "113/06/19")
        self.assertEqual(result.get("charge_info.shared_public_charge_twd"), 920.7)
        self.assertEqual(result.get("other_info.outage_group"), "H")
        self.assertEqual(result.get("other_info.feeder_code"), "CY36")
        self.assertEqual(result.get("invoice_info.invoice_number"), "WG-73787182")
        self.assertEqual(result.get("invoice_info.amount_twd"), 1467)
        self.assertEqual(
            result["by_label"]["用戶資訊"],
            {
                "電價種類": "表燈非營業用",
                "時間種類": "非時間電價",
                "底度": 40,
                "計費度數": 610,
                "公共分攤戶數": 434,
                "營業統編": "00423107",
                "計費期間": {"start": "113/02/06", "end": "113/04/09"},
                "本次抄表日": "113/04/10",
                "本次收費日": "113/04/16",
                "下次抄表日": "113/06/11",
                "下次收費日": "113/06/19",
            },
        )


if __name__ == "__main__":
    unittest.main()
