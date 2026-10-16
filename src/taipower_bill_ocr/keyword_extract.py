"""Find the billing period, usage (kWh) and cost on any document by keywords.

Used for electricity documents that are not Taipower bills, typically a
landlord's statement for a building that shares one meter. The layout is
unknown, so values are looked for beside or under configurable keywords and
ranked; the reviewer confirms them (or picks another) before submitting.

Works on OCR lines with page-image coordinates; no OCR happens here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Sequence

# What each value is in the 17-column row (plus the total kWh column).
PERIOD, USAGE, PRETAX, TOTAL = "Billing Period 計費期間", "用電度數", "稅前應繳總金額", "應繳總金額"
FIELDS = (PERIOD, USAGE, PRETAX, TOTAL)

DEFAULT_KEYWORDS: dict[str, list[str]] = {
    # Names the company goes by on landlords' documents (set per company).
    "company_names": [],
    # Words that point at the company's own share rather than the building's.
    "self_terms": ["本公司", "貴公司", "承租戶", "承租人", "租戶", "分攤"],
    "period_keywords": ["計費期間", "用電期間", "使用期間", "抄表期間", "計費日期", "電費期間", "期間"],
    "usage_keywords": ["用電度數", "使用度數", "分攤度數", "本期度數", "實用度數", "度數", "用電量", "kWh", "度"],
    "cost_keywords": [
        "未稅金額", "電費金額", "分攤金額", "分攤電費", "應繳金額", "應付金額", "應繳電費",
        "電費", "金額", "小計", "合計", "總計",
    ],
    # Values near these are usually not the company's: the whole building,
    # shared areas, or another period.
    "exclude_keywords": ["全棟", "整棟", "總表", "大樓", "公設", "上期", "前期", "去年", "累計"],
}

# Whether an amount includes the 5% business tax.
_PRETAX_MARKERS = ("未稅", "稅前", "不含稅", "未含稅", "稅額前")
_TAXED_MARKERS = ("含稅", "應繳", "應付", "總計", "合計", "總金額", "實付", "實收")
TAX_RATE = 1.05

_DATE = re.compile(r"(\d{2,4})\s*[/.\-年]\s*(\d{1,2})\s*[/.\-月]\s*(\d{1,2})\s*日?")
_NUMBER = re.compile(r"(?<![\d/.])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\d/])|(?<![\d/.,])-?\d+(?:\.\d+)?(?![\d/,])")


@dataclass(frozen=True)
class Line:
    text: str
    x: float
    y: float
    width: float
    height: float
    page: int | None = None

    @property
    def box(self) -> tuple[float, float, float, float]:
        return (self.x, self.y, self.width, self.height)

    @property
    def middle(self) -> float:
        return self.y + self.height / 2


@dataclass
class Candidate:
    value: Any
    score: float
    page: int | None
    box: tuple[float, float, float, float]
    keyword: str
    note: str | None = None
    # The text of the line holding the keyword (a table header, a label).
    label: str = ""


@dataclass
class Extraction:
    # Best candidate first, per field; a field nobody found is absent.
    candidates: dict[str, list[Candidate]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def best(self, name: str) -> Candidate | None:
        found = self.candidates.get(name)
        return found[0] if found else None


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


# ------------------------------------------------------------------ parsing
def _date(match: re.Match[str]) -> tuple[int, int, int] | None:
    year, month, day = (int(part) for part in match.groups())
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    if year < 1000 and not 50 <= year <= 200:  # ROC years
        return None
    return year, month, day


def _days(start: tuple[int, int, int], end: tuple[int, int, int]) -> int | None:
    def gregorian(value: tuple[int, int, int]) -> date:
        year, month, day = value
        return date(year + 1911 if year < 1000 else year, month, day)

    try:
        return (gregorian(end) - gregorian(start)).days
    except ValueError:
        return None


def parse_period(text: str) -> str | None:
    """"113/01/01~113/01/31" from two dates in ``text`` (ROC or western years)."""
    dates = [parsed for match in _DATE.finditer(text) if (parsed := _date(match))]
    if len(dates) < 2:
        return None
    start, end = dates[0], dates[1]
    days = _days(start, end)
    if days is None or not 0 < days <= 100:
        return None
    return "~".join(f"{year}/{month:02d}/{day:02d}" for year, month, day in (start, end))


def period_days(period: str) -> int | None:
    dates = [parsed for match in _DATE.finditer(period) if (parsed := _date(match))]
    return _days(dates[0], dates[1]) if len(dates) >= 2 else None


# A number followed by one of these is a floor, a date part or a count, not kWh or money.
_NOT_AMOUNT = re.compile(r"\s*(樓|層|f(?![a-z])|月|日|年|號|天|戶|人|%|％)", re.IGNORECASE)


def numbers(text: str) -> list[tuple[float, int]]:
    """Every number in ``text`` (not part of a date, a floor...) with where it starts."""
    without_dates = _DATE.sub(lambda match: " " * len(match.group()), text)
    found = []
    for match in _NUMBER.finditer(without_dates):
        if _NOT_AMOUNT.match(without_dates, match.end()):
            continue
        try:
            found.append((float(match.group().replace(",", "")), match.start()))
        except ValueError:
            continue
    return found


def plain(value: float) -> int | float:
    """12.0 -> 12, so whole amounts look like the Taipower columns."""
    return int(value) if float(value).is_integer() else round(value, 2)


# ----------------------------------------------------------------- matching
def _rows(lines: Sequence[Line], line: Line) -> list[Line]:
    """Lines on the same row as ``line`` (same page), left to right."""
    return sorted(
        (other for other in lines if other.page == line.page and abs(other.middle - line.middle) < max(line.height, other.height) * 0.6),
        key=lambda other: other.x,
    )


def _row_text(lines: Sequence[Line], line: Line) -> str:
    return compact(" ".join(other.text for other in _rows(lines, line)))


def _union(*boxes: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    left = min(box[0] for box in boxes)
    top = min(box[1] for box in boxes)
    right = max(box[0] + box[2] for box in boxes)
    bottom = max(box[1] + box[3] for box in boxes)
    return (left, top, right - left, bottom - top)


def _keyword_at(text: str, keywords: Sequence[str]) -> tuple[int, str] | None:
    """The most specific keyword in ``text`` (earlier in the list wins)."""
    flat = compact(text)
    for rank, keyword in enumerate(keywords):
        if keyword and compact(keyword) in flat:
            return rank, keyword
    return None


def _neighbours(lines: Sequence[Line], line: Line) -> list[tuple[str, Line, int]]:
    """Where a keyword's value may be: right of it on its row, or under it (a table column)."""
    right = [other for other in _rows(lines, line) if other.x > line.x + line.width * 0.5 and other is not line]
    below = sorted(
        (
            other for other in lines
            if other.page == line.page and other.y > line.y + line.height * 0.5
            and other.y < line.y + line.height * 16
            and min(other.x + other.width, line.x + line.width) - max(other.x, line.x) > -line.height
        ),
        key=lambda other: other.y,
    )
    return [("right", other, index) for index, other in enumerate(right[:4])] + [
        ("below", other, index) for index, other in enumerate(below[:12])
    ]


class _Scorer:
    def __init__(self, lines: Sequence[Line], keywords: dict[str, list[str]]):
        self.lines = lines
        self.self_words = [compact(word) for word in keywords.get("company_names", []) + keywords.get("self_terms", []) if word.strip()]
        self.exclude = [compact(word) for word in keywords.get("exclude_keywords", []) if word.strip()]
        self.self_lines = [line for line in lines if any(word in compact(line.text) for word in self.self_words)]

    def context(self, keyword_line: Line, value_line: Line) -> tuple[float, list[str]]:
        """Bonus for the company's own share, penalty for the building's."""
        score, notes = 0.0, []
        # The value's own row: a table header naming 承租戶 is not the company's row.
        row = _row_text(self.lines, value_line)
        if any(word in row for word in self.self_words):
            score += 40
        elif any(
            own.page == value_line.page and 0 <= value_line.y - own.y < own.height * 6
            for own in self.self_lines
        ):
            score += 15
        excluded = [word for word in self.exclude if word in row + compact(keyword_line.text)]
        if excluded:
            score -= 30
            notes.append(f"near {excluded[0]}")
        return score, notes


def _candidates(
    lines: Sequence[Line],
    keywords: Sequence[str],
    scorer: _Scorer,
    read: "callable[[str, Line, str], list[tuple[Any, float]]]",
) -> list[Candidate]:
    found: list[Candidate] = []
    for line in lines:
        hit = _keyword_at(line.text, keywords)
        if hit is None:
            continue
        rank, keyword = hit
        specificity = max(0, 12 - rank) + min(len(keyword), 6)
        places = [("same", line, 0)] + _neighbours(lines, line)
        for relation, other, distance in places:
            for value, local in read(other.text, other, keyword if relation == "same" else ""):
                position = {"same": 30, "right": 26 - distance * 3, "below": 18 - distance * 1.5}[relation]
                context, notes = scorer.context(line, other)
                box = other.box if relation == "below" else _union(line.box, other.box)
                found.append(Candidate(value, specificity + position + context + local, line.page, box, keyword, ", ".join(notes) or None, line.text))
    best: dict[Any, Candidate] = {}
    for candidate in found:
        if candidate.value not in best or candidate.score > best[candidate.value].score:
            best[candidate.value] = candidate
    return sorted(best.values(), key=lambda candidate: -candidate.score)


UNITS = {"度", "kwh", "元"}


def _near_keyword(text: str, keyword: str, values: list[tuple[float, int]]) -> list[tuple[float, int]]:
    """Numbers ordered by how likely they belong to ``keyword`` in the same text.

    A label is followed by its value ("電費 45,000"); a unit follows its
    number ("15,000 度"). Without a keyword, the order is unchanged.
    """
    if not keyword:
        return values
    match = re.search(r"\s*".join(map(re.escape, keyword)), text, re.IGNORECASE)
    if match is None:
        return values
    unit = compact(keyword) in UNITS

    def distance(item: tuple[float, int]) -> float:
        start = item[1]
        if unit:
            return match.start() - start if start < match.start() else 1000 + start
        return start - match.end() if start >= match.end() else 1000 + match.start() - start

    return sorted(values, key=distance)


def extract(lines: Iterable[Line], keywords: dict[str, list[str]] | None = None) -> Extraction:
    """The best guesses for period, usage, pre-tax and tax-included cost."""
    keywords = {**DEFAULT_KEYWORDS, **(keywords or {})}
    lines = [line for line in lines if line.text.strip()]
    scorer = _Scorer(lines, keywords)
    result = Extraction()

    def read_period(text: str, line: Line, keyword: str) -> list[tuple[Any, float]]:
        period = parse_period(text)
        if period is None:
            return []
        days = period_days(period) or 0
        return [(period, 10 if 20 <= days <= 70 else -10)]

    def read_usage(text: str, line: Line, keyword: str) -> list[tuple[Any, float]]:
        values = [item for item in _near_keyword(text, keyword, numbers(text)) if 0 < item[0] < 1e8]
        unit = 8 if re.search(r"度|kwh", text, re.IGNORECASE) else 0
        return [(plain(value), unit - index * 4) for index, (value, _) in enumerate(values[:2])]

    def read_cost(text: str, line: Line, keyword: str) -> list[tuple[Any, float]]:
        values = [item for item in _near_keyword(text, keyword, numbers(text)) if 0 < item[0] < 1e10]
        money = 6 if re.search(r"元|nt\$|\$|ntd|twd", text, re.IGNORECASE) else 0
        return [(plain(value), money - index * 4) for index, (value, _) in enumerate(values[:2])]

    periods = _candidates(lines, keywords["period_keywords"], scorer, read_period)
    if not periods:
        # A range of dates anywhere is better than nothing (scored low).
        for line in lines:
            if (period := parse_period(line.text)) is not None:
                periods.append(Candidate(period, 0, line.page, line.box, "", "no period keyword"))
    if periods:
        result.candidates[PERIOD] = periods

    usages = _candidates(lines, keywords["usage_keywords"], scorer, read_usage)
    if usages:
        result.candidates[USAGE] = usages

    costs = _candidates(lines, keywords["cost_keywords"], scorer, read_cost)
    pretax, taxed, unknown = [], [], []
    for candidate in costs:
        line_text = compact(" ".join(line.text for line in lines if line.page == candidate.page and _overlaps(line.box, candidate.box)))
        context = compact(candidate.label or candidate.keyword) + line_text
        if any(marker in context for marker in _PRETAX_MARKERS):
            pretax.append(candidate)
        elif any(marker in context for marker in _TAXED_MARKERS):
            taxed.append(candidate)
        else:
            unknown.append(candidate)
    _settle_tax(result, pretax, taxed, unknown)

    _check(result)
    return result


def _overlaps(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    return min(a[0] + a[2], b[0] + b[2]) > max(a[0], b[0]) and min(a[1] + a[3], b[1] + b[3]) > max(a[1], b[1])


def _settle_tax(result: Extraction, pretax: list[Candidate], taxed: list[Candidate], unknown: list[Candidate]) -> None:
    """Pre-tax and tax-included amounts; one is worked out from the other when missing."""
    # An amount stated with tax is better evidence than one of unknown kind.
    if not pretax and unknown and not taxed:
        pretax = [Candidate(c.value, c.score - 5, c.page, c.box, c.keyword, "tax not stated") for c in unknown]
        result.notes.append("Cost: the document does not say whether tax is included; taken as before tax")
    if pretax:
        result.candidates[PRETAX] = sorted(pretax, key=lambda c: -c.score)
    if taxed:
        result.candidates[TOTAL] = sorted(taxed, key=lambda c: -c.score)
    if PRETAX not in result.candidates and taxed:
        best = result.candidates[TOTAL][0]
        derived = plain(round(best.value / TAX_RATE, 0))
        result.candidates[PRETAX] = [Candidate(derived, best.score, best.page, best.box, best.keyword, "worked out from the amount with tax ÷ 1.05")]
        result.notes.append(f"Cost: only an amount with tax was found; before tax = {best.value} ÷ 1.05 = {derived}")


def _check(result: Extraction) -> None:
    period, usage, pretax = result.best(PERIOD), result.best(USAGE), result.best(PRETAX)
    if period is not None:
        days = period_days(period.value)
        if days is not None and not 20 <= days <= 70:
            result.notes.append(f"Billing period: {days} days is unusual for one bill")
    if usage is not None and pretax is not None and usage.value:
        price = pretax.value / usage.value
        if not 1.5 <= price <= 10:
            result.notes.append(f"Cost ÷ usage = {price:.2f} NT$/kWh, outside the usual 1.5–10; check both values")


def lines_from_dataset(dataset: dict[str, Any], page: int | None) -> list[Line]:
    """OCR lines stored with a page (``PageRecord.as_json()['ocr_lines']``)."""
    return [
        Line(item["text"], item["x"], item["y"], item["width"], item["height"], page)
        for item in dataset.get("ocr_lines") or []
    ]
