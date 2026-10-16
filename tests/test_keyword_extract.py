import unittest

from taipower_bill_ocr.keyword_extract import (
    PERIOD,
    PRETAX,
    TOTAL,
    USAGE,
    Line,
    extract,
    numbers,
    parse_period,
)


def row(y, *cells, page=1, height=30):
    """Lines for one table row: (text, x, width) cells at the same height."""
    return [Line(text, x, y, width, height, page) for text, x, width in cells]


# A landlord's allocation table: the building's total, then each tenant.
TABLE = [
    *row(80, ("電費分攤明細表", 400, 260)),
    *row(140, ("計費期間：113/01/01 ~ 113/01/31", 100, 520)),
    *row(200, ("全棟總度數", 100, 160), ("85,000 度", 300, 140), ("全棟電費", 600, 140), ("255,000 元", 780, 160)),
    *row(280, ("承租戶", 100, 100), ("樓層", 260, 60), ("分攤度數", 380, 130), ("未稅金額", 580, 130), ("稅額", 780, 70), ("含稅金額", 920, 130)),
    *row(330, ("乙公司", 100, 100), ("5F", 260, 40), ("20,000", 390, 100), ("60,000", 590, 100), ("3,000", 780, 80), ("63,000", 930, 100)),
    *row(380, ("甲公司", 100, 100), ("3F", 260, 40), ("15,000", 390, 100), ("45,000", 590, 100), ("2,250", 780, 80), ("47,250", 930, 100)),
]


class KeywordExtractionTests(unittest.TestCase):
    def test_company_row_of_an_allocation_table(self):
        result = extract(TABLE, {"company_names": ["甲公司"]})
        self.assertEqual(result.best(PERIOD).value, "113/01/01~113/01/31")
        self.assertEqual(result.best(USAGE).value, 15000)
        self.assertEqual(result.best(PRETAX).value, 45000)
        self.assertEqual(result.best(TOTAL).value, 47250)
        # Other tenants' and the building's numbers stay as alternatives.
        self.assertIn(20000, [candidate.value for candidate in result.candidates[USAGE]])
        self.assertNotEqual(result.best(USAGE).value, 85000)

    def test_without_a_company_name_the_building_total_is_not_chosen(self):
        result = extract(TABLE)
        self.assertNotEqual(result.best(USAGE).value, 85000)
        self.assertNotEqual(result.best(PRETAX).value, 255000)

    def test_letter_with_everything_in_one_sentence(self):
        letter = [Line("貴公司 113年2月1日至113年2月29日 用電 12,345 度，電費 37,035 元（含稅）", 80, 300, 1400, 34)]
        result = extract(letter)
        self.assertEqual(result.best(PERIOD).value, "113/02/01~113/02/29")
        self.assertEqual(result.best(USAGE).value, 12345)
        self.assertEqual(result.best(TOTAL).value, 37035)
        # Only the amount with tax was given: before tax is worked out and said so.
        self.assertEqual(result.best(PRETAX).value, 35271)
        self.assertTrue(any("÷ 1.05" in note for note in result.notes))

    def test_western_dates_and_an_amount_without_tax_wording(self):
        lines = [
            *row(100, ("使用期間", 80, 120), ("2024/03/01 - 2024/03/31", 240, 330)),
            *row(150, ("用電量", 80, 100), ("8,000 kWh", 240, 140)),
            *row(200, ("金額", 80, 70), ("NT$ 24,000", 240, 160)),
        ]
        result = extract(lines)
        self.assertEqual(result.best(PERIOD).value, "2024/03/01~2024/03/31")
        self.assertEqual(result.best(USAGE).value, 8000)
        self.assertEqual(result.best(PRETAX).value, 24000)
        self.assertNotIn(TOTAL, result.candidates)
        self.assertTrue(any("does not say whether tax" in note for note in result.notes))

    def test_values_on_another_page_keep_their_page(self):
        lines = [*row(100, ("電費通知", 80, 160), page=1), *row(300, ("分攤度數", 80, 120), ("6,500", 240, 90), page=2)]
        best = extract(lines).best(USAGE)
        self.assertEqual((best.value, best.page), (6500, 2))

    def test_unrelated_document_finds_nothing(self):
        lines = [*row(100, ("會議紀錄", 80, 160)), *row(150, ("出席人員：王小明、陳小華", 80, 400))]
        self.assertEqual(extract(lines).candidates, {})

    def test_unusual_price_is_pointed_out(self):
        lines = [*row(100, ("用電度數", 80, 120), ("15,000", 240, 90)), *row(150, ("未稅金額", 80, 120), ("450", 240, 60))]
        self.assertTrue(any("NT$/kWh" in note for note in extract(lines).notes))

    def test_keywords_can_be_changed(self):
        lines = [*row(100, ("電力使用", 80, 120), ("7,200", 240, 90))]
        self.assertNotIn(USAGE, extract(lines).candidates)
        self.assertEqual(extract(lines, {"usage_keywords": ["電力使用"]}).best(USAGE).value, 7200)


class ParsingTests(unittest.TestCase):
    def test_periods(self):
        self.assertEqual(parse_period("113/01/01~113/01/31"), "113/01/01~113/01/31")
        self.assertEqual(parse_period("113.1.1 至 113.1.31"), "113/01/01~113/01/31")
        self.assertEqual(parse_period("2024-01-05 ~ 2024-02-04"), "2024/01/05~2024/02/04")
        self.assertIsNone(parse_period("113/01/31~113/01/01"))  # backwards
        self.assertIsNone(parse_period("113/01/01"))

    def test_numbers_skip_dates(self):
        self.assertEqual([value for value, _ in numbers("113/01/01 用電 1,234.5 度")], [1234.5])
        self.assertEqual([value for value, _ in numbers("NT$12,345")], [12345])


if __name__ == "__main__":
    unittest.main()
