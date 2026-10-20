import unittest

from taipower_bill_ocr.green_statement import check_line, is_green, read_statement, statement_period
from taipower_bill_ocr.keyword_extract import Line

# The retailer's table as OCR reads it (a statement shared by the user;
# the 電能/轉供費率 header is split over two lines).
HEADER = [
    ("使用電號", 150, 215), ("能源別", 440, 215), ("基準價", 640, 215), ("電能", 890, 200), ("轉供費率", 830, 240),
    ("憑證費率", 1050, 220), ("每度單價", 1265, 222), ("轉供度數", 1485, 222), ("本月電費", 1710, 222),
]
ROWS = [
    ("10512918917", "風能", "5.8000", "0.30520", "0.00000", "6.1052", "36,145", "220,671"),
    ("10512918917", "風能", "5.8000", "0.30520", "0.00000", "6.1052", "242,478", "1,480,377"),
    ("10512918918", "風能", "5.9000", "0.30520", "0.00000", "6.2052", "120,208", "745,913"),
    ("10518691508", "風能", "5.8000", "0.30520", "0.00000", "6.1049", "2,688", "16,410"),
    ("10518691508", "風能", "5.8000", "0.30520", "0.00000", "6.1052", "18,213", "111,194"),
    ("10518691509", "風能", "5.9000", "0.30520", "0.00000", "6.2053", "9,481", "58,832"),
    ("10512918917", "餘電加收", "3.0331", "0.00000", "0.00000", "3.0331", "78", "237"),
    ("10512918917", "餘電加收", "3.5916", "0.00000", "0.00000", "3.5916", "552", "1,983"),
]
X = (125, 455, 640, 838, 1052, 1288, 1505, 1722)


def statement_lines():
    lines = [Line("本月消費明細如下：", 40, 90, 260, 34, 1)]
    lines += [Line(text, x, y, len(text) * 32, 36, 1) for text, x, y in HEADER]
    for index, row in enumerate(ROWS):
        y = 320 + index * 100
        lines += [Line(text, x, y, len(text) * 18, 34, 1) for text, x in zip(row, X)]
    return lines


class ReadStatementTests(unittest.TestCase):
    def test_every_line_of_the_table_is_read(self):
        statement = read_statement(statement_lines())
        self.assertIsNotNone(statement)
        lines = [line.as_json() for line in statement.lines]
        self.assertEqual(len(lines), 8)
        first = lines[0]
        self.assertEqual(
            (first["meter"], first["energy"], first["base"], first["transfer_rate"], first["certificate_rate"], first["kwh"], first["cost"]),
            ("10512918917", "風能", 5.8, 0.3052, 0.0, 36145, 220671),
        )
        self.assertEqual([line["energy"] for line in lines[-2:]], ["餘電加收", "餘電加收"])
        self.assertEqual(sum(line["cost"] for line in lines), 2635617)
        self.assertIn("kwh", statement.headers)

    def test_cells_with_a_stray_character_are_still_read(self):
        from taipower_bill_ocr.green_statement import _number

        self.assertEqual(_number("I0.00000"), 0.0)  # a text cursor in the photo
        self.assertEqual(_number("1,480,377"), 1480377)
        self.assertIsNone(_number("風能"))
        self.assertIsNone(_number(""))

    def test_a_page_without_the_table_is_not_a_statement(self):
        self.assertIsNone(read_statement([Line("電費分攤明細表", 100, 100, 300, 40, 1), Line("轉供度數", 100, 200, 120, 30, 1)]))


class CheckLineTests(unittest.TestCase):
    def test_small_differences_from_finer_rounding_are_fine(self):
        lines = [line.as_json() for line in read_statement(statement_lines()).lines]
        checks = [check_line(line) for line in lines]
        self.assertEqual([check["difference"] for check in checks], [-1, 0, -2, -1, 0, 0, 0, 0])
        self.assertEqual({check["status"] for check in checks}, {"ok"})
        self.assertEqual(checks[0]["expected"], 220672)

    def test_a_real_mismatch_is_pointed_out(self):
        check = check_line({"kwh": 36145, "cost": 230000, "base": 5.8, "transfer_rate": 0.3052, "certificate_rate": 0})
        self.assertEqual(check["status"], "check")
        self.assertEqual(check["difference"], 230000 - 220672)
        self.assertEqual(check_line({"kwh": 10, "cost": 61, "base": 5.8, "transfer_rate": 0.3052}, tolerance=0)["status"], "ok")
        self.assertEqual(check_line({"kwh": None, "cost": 5})["status"], "incomplete")

    def test_surcharges_are_not_green_power(self):
        self.assertTrue(is_green({"energy": "風能"}))
        self.assertFalse(is_green({"energy": "餘電加收"}))


class PeriodTests(unittest.TestCase):
    def test_a_month_or_a_range(self):
        self.assertEqual(statement_period([Line("帳單月份：113年01月", 0, 0, 200, 30)]), "113/01/01~113/01/31")
        self.assertEqual(statement_period([Line("計費期間 2024/02/01~2024/02/29", 0, 0, 300, 30)]), "2024/02/01~2024/02/29")
        self.assertIsNone(statement_period([Line("本月消費明細如下：", 0, 0, 200, 30)]))


if __name__ == "__main__":
    unittest.main()
