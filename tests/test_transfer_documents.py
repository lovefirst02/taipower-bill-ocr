import unittest

from taipower_bill_ocr.keyword_extract import Line
from taipower_bill_ocr.purchase_notice import check_notice, read_notice
from taipower_bill_ocr.tables import number
from taipower_bill_ocr.transfer_list import check_row, read_transfer_list


def table(top, rows, xs=(150, 280, 500, 700, 920, 1290, 1440, 1760)):
    return [
        Line(text, x, top + index * 100, max(40, len(text) * 18), 34)
        for index, row in enumerate(rows) for text, x in zip(row, xs) if text
    ]


def header(y):
    cells = [("編號", 133), ("用戶電號", 288), ("用戶表號", 504), ("發電電號", 717), ("發電表號", 931),
             ("當月服務使用量(度)", 1109), ("費用(元)", 1669)]
    return [Line(text, x, y, len(text) * 30, 37) for text, x in cells] + [
        Line("輸電費率", 1434, y - 20, 110, 37), Line("(元/度)", 1441, y + 18, 100, 36),
    ]


# Taipower's list for one service, as OCR reads a photo of it: simplified
# characters (费, 电), 4,654 read as 4.654, and a rate under a cursor.
WIND_LIST = [
    Line("台灣電力公司", 843, 44, 360, 67), Line("費用清單", 848, 145, 300, 92),
    Line("服務編號：1-11311-08-00502", 67, 295, 700, 59), Line("2026年08月", 1658, 300, 180, 42),
    Line("轉供輸電費清單", 67, 380, 260, 44), *header(457),
    *table(549, [
        ("1", "10512918917", "WA12001353", "08376141004", "TI19701899", "26,031", "0.0778", "2,025"),
        ("", "", "WA12001353", "08376175049", "WB20201007", "170,322", "0.0778", "13,251"),
        ("2", "10518691508", "TA20855027", "08376141004", "TI19701899", "2,006", "0.9778", "156"),
    ]),
    Line("轉供配电费清單", 85, 916, 260, 41), *header(993),
    *table(1087, [("1", "10512918917", "WA12001353", "08376141004", "TI19701899", "26,031", "0.1788", "4.654")]),
]


class TransferListTests(unittest.TestCase):
    def test_every_fee_table_and_row_is_read(self):
        found = read_transfer_list(WIND_LIST)
        self.assertEqual((found.service, found.period), ("1-11311-08-00502", "2026/08/01~2026/08/31"))
        rows = [row.as_json() for row in found.rows]
        self.assertEqual([(row["section"], row["user_meter"], row["generator"], row["kwh"], row["fee"]) for row in rows], [
            ("轉供輸電費清單", "10512918917", "08376141004", 26031, 2025),
            ("轉供輸電費清單", "10512918917", "08376175049", 170322, 13251),  # the meter is written once
            ("轉供輸電費清單", "10518691508", "08376141004", 2006, 156),
            ("轉供配電費清單", "10512918917", "08376141004", 26031, 4654),  # 4.654 is 4,654
        ])
        self.assertEqual({check_row(row)["status"] for row in rows}, {"ok"})

    def test_a_misread_rate_is_put_right_only_when_the_fee_proves_it(self):
        row = read_transfer_list(WIND_LIST).rows[2]
        self.assertEqual(row.rate, 0.0778)
        self.assertIn("0.9778", row.note)
        self.assertEqual(check_row({"kwh": 100, "rate": 0.0778, "fee": 9})["status"], "check")

    def test_rows_are_read_by_their_look_when_the_header_is_unreadable(self):
        # Small print under a stamp: the header comes out as nonsense, the
        # photo is at a slant, and some rates and a small fee are lost.
        def slanted(top, row):
            xs = (50, 90, 190, 280, 370, 520, 590, 710)
            return [Line(text, x, top - x * 0.03, max(30, len(text) * 9), 20) for text, x in zip(row, xs) if text]

        lines = [
            Line("台湾電力公司", 326, 504, 150, 26), Line("费用清單", 332, 546, 120, 30),
            Line("服務编就：1-11210-08-00210", 5, 603, 306, 32), Line("2026年07月", 656, 604, 83, 21),
            Line("棒供輸電费清單", 3, 639, 121, 26),
            Line("当月联格使用量(度）", 441, 665, 114, 20), Line("普用(丸)", 664, 662, 55, 21),
            *slanted(710, ("1", "05785193319", "KU20021670", "08376003416", "TI20850896", "104.210", "", "8.108")),
            *slanted(750, ("2", "05785204210", "GB21900276", "08376003416", "TI20850896", "650.995", "0.0778", "50.647")),
            *slanted(790, ("3", "", "", "08376003416", "TI20850896", "27.296", "0.0778", "2.124")),
            *slanted(830, ("4", "10505503331", "TC18742828", "08376003416", "TI20850896", "3,024", "0.07T8", "")),
        ]
        found = read_transfer_list(lines)
        self.assertEqual((found.service, found.period), ("1-11210-08-00210", "2026/07/01~2026/07/31"))
        rows = [row.as_json() for row in found.rows]
        self.assertEqual([(row["section"], row["user_meter"], row["generator"], row["kwh"], row["fee"]) for row in rows], [
            ("轉供輸電費清單", "05785193319", "08376003416", 104210, 8108),
            ("轉供輸電費清單", "05785204210", "08376003416", 650995, 50647),
            # A user meter OCR missed is not guessed from the row above.
            ("轉供輸電費清單", None, "08376003416", 27296, 2124),
            ("轉供輸電費清單", "10505503331", "08376003416", 3024, None),
        ])
        self.assertIn("rate not read", rows[0]["note"])  # the section's rate gives the printed fee
        self.assertEqual(rows[0]["rate"], 0.0778)
        self.assertIn("not read", rows[2]["note"])
        self.assertEqual(check_row(rows[3])["status"], "incomplete")

    def test_a_table_running_onto_the_next_page_keeps_its_section(self):
        next_page = [Line(line.text, line.x, line.y, line.width, line.height, 2)
                     for line in table(100, [("2", "10518691508", "TA20855027", "08376141004", "TI19701899", "2,006", "0.1788", "359")])]
        first_page = [Line(line.text, line.x, line.y, line.width, line.height, 1) for line in WIND_LIST]
        rows = read_transfer_list(first_page + next_page).rows
        self.assertEqual((rows[-1].section, rows[-1].user_meter, rows[-1].fee, rows[-1].page), ("轉供配電費清單", "10518691508", 359, 2))
        # Read on its own, a page without a title or header is not a list.
        self.assertIsNone(read_transfer_list(next_page))

    def test_a_fee_table_with_meters_not_read_and_rates_a_column_off_is_put_right(self):
        # 電力調度: rates with seven decimals read into the fee column (one without its "0.0"),
        # and the user meters not read at all.
        dispatch = [
            Line("轉供電力調度費清單", 85, 1200, 300, 41), *header(1277),
            *table(1370, [
                ("1", "", "", "08376141004", "TI19701899", "26,031", "", "0.0274175"),
                ("", "", "", "08376175049", "WB20201007", "170,322", "", "2741604"),
                ("2", "", "", "08376141004", "TI19701899", "2,006", "", "0.0274451"),
            ]),
        ]
        rows = [row for row in read_transfer_list(WIND_LIST + dispatch).rows if row.section == "轉供電力調度費清單"]
        self.assertEqual([(row.user_meter, row.rate, row.fee) for row in rows], [
            ("10512918917", 0.0274175, 714),     # 26,031 × 0.0274175
            ("10512918917", 0.02741604, 4670),   # the rate without its 0.0
            ("10518691508", 0.0274451, 55),
        ])
        self.assertTrue(all("another fee table" in row.note and "worked out" in row.note for row in rows))

    def test_a_meter_carried_down_gives_way_to_the_one_of_its_generator_and_kwh(self):
        # 配電: the second meter's number was not read, so the row above's was carried down.
        lines = WIND_LIST + [
            Line("轉供配电费清單", 85, 1200, 260, 41), *header(1277),
            *table(1370, [
                ("1", "10512918917", "WA12001353", "08376141004", "TI19701899", "26,031", "0.1788", "4,654"),
                ("2", "", "TA20855027", "08376141004", "TI19701899", "2,006", "0.1788", "359"),
            ]),
        ]
        last = read_transfer_list(lines).rows[-1]
        self.assertEqual((last.user_meter, last.kwh), ("10518691508", 2006))

    def test_other_pages_are_not_lists(self):
        self.assertIsNone(read_transfer_list([Line("本月消費明細如下：", 0, 0, 200, 30)]))


def label(text, x, y):
    return Line(text, x, y, len(text) * 12, 24)


# A retailer's purchase notice, as OCR reads a photo of it.
NOTICE = [
    label("企業購電電費通知單", 1055, 225), label("電能轉/直供服務編號：1-11210-08-00210", 90, 307),
    label("計费期間", 88, 344), label("Billing Period", 87, 363), label("2026/08/01-2026/08/31", 386, 358),
    label("購售费率(元/度)", 143, 595), label("Purchase Rate (NT/kWh)", 143, 618), label("5.6000", 638, 624),
    label("實際購售電量(度)", 707, 606), label("Actual Purchase Volume (kWh)", 704, 629), label("673,816", 1180, 637),
    label("實際購售電量電费(元)", 1257, 616), label("Actual Purchase Volume Charges (NT)", 1254, 639), label("3,773,370", 1789, 645),
    label("台電蔓購费率(元/度)", 144, 677), label("TPC FiT Rate (NT/kWh)", 143, 699), label("5.2428", 636, 706),
    label("餘電電量(度)", 706, 691), label("Surplus Power Volume (kWh)", 703, 711), label("186,901", 1177, 719),
    label("餘電電費(元)", 1254, 700), label("Surplus Power Fees(NT)", 1250, 722), label("66.761", 1812, 732),
    label("電能供應费用(元)", 90, 744), label("Power Supply Fees (NT)", 88, 766), label("205,610", 629, 776),
    label("憑證審查費(元)", 704, 758), label("T-REC Review Fee (NTD)", 700, 779), label("1,743", 1191, 789),
    label("憑證張數(張)", 1249, 780), label("T-REC (PCS)", 1247, 800), label("581", 1833, 800),
    label("每期價金未税金額合計(元)", 91, 824), label("Installment Price Exculsive of Tax", 90, 846), label("4,047,484", 610, 857),
    label("愿付税金(元)", 703, 850), label("Tax", 700, 872), label("202,374", 1172, 868),
    label("本期應付總额(元)", 1249, 860), label("Installment Price (NT)", 1248, 884), label("4,249,858", 1779, 883),
]


class PurchaseNoticeTests(unittest.TestCase):
    def test_values_are_read_beside_their_labels(self):
        notice = read_notice(NOTICE)
        self.assertEqual((notice.service, notice.period), ("1-11210-08-00210", "2026/08/01~2026/08/31"))
        self.assertEqual(notice.values, {
            "purchase_rate": 5.6, "kwh": 673816, "purchase_charge": 3773370, "fit_rate": 5.2428, "surplus_kwh": 186901,
            "surplus_charge": 66761, "supply_fees": 205610, "certificate_fee": 1743, "certificates": 581,
            "untaxed": 4047484, "tax": 202374, "total": 4249858,
        })

    def test_the_notice_formula_is_checked(self):
        checks = check_notice(read_notice(NOTICE).values)
        self.assertEqual([check["status"] for check in checks], ["ok"] * 5)
        values = dict(read_notice(NOTICE).values, supply_fees=205600)
        self.assertEqual({check["label"]: check["difference"] for check in check_notice(values)}["A×B + (A−C)×D + E + F"], 10)


class NumberTests(unittest.TestCase):
    def test_thousands_read_as_a_point(self):
        self.assertEqual(number("4.654", integer=True), 4654)
        self.assertEqual(number("4.654"), 4.654)
        self.assertEqual(number("1,480,377", integer=True), 1480377)


if __name__ == "__main__":
    unittest.main()
