"""The app's filled-in columns against known answers, field by field.

    python training/eval_rows.py samples/synth_pages/truth.json reads.json [reads2.json] [--show]
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


def same(found, expected) -> bool:
    if isinstance(expected, (int, float)):
        try:
            return abs(float(found) - float(expected)) < 0.051
        except (TypeError, ValueError):
            return False
    return str(found or "").replace(" ", "") == str(expected).replace(" ", "")


def group(column: str) -> str:
    if column.startswith("計費度數"):
        return "計費度數"
    if column.startswith("轉供度數"):
        return "轉供度數"
    if column.startswith("Billing"):
        return "用電期間"
    if column.startswith("Custom"):
        return "電號"
    return "計價"


def main() -> None:
    show = "--show" in sys.argv
    names = [name for name in sys.argv[1:] if name != "--show"]
    truth = json.loads(Path(names[0]).read_text(encoding="utf-8"))
    reads = {}
    for name in names[1:]:
        reads.update(json.loads(Path(name).read_text(encoding="utf-8")))
    totals: dict[tuple[str, str], Counter] = {}
    perfect: dict[str, Counter] = {}
    for path, expected in truth.items():
        if path not in reads:
            continue
        level = Path(path).parent.name
        row = reads[path]["row"] or {}
        all_right = True
        for column, value in expected.items():
            counter = totals.setdefault((level, group(column)), Counter())
            right = same(row.get(column), value)
            counter["total"] += 1
            counter["right"] += right
            counter["missing"] += row.get(column) in (None, "")
            all_right &= right
            if show and not right:
                print(f"{level}/{Path(path).name} {column}: read {row.get(column)!r}, truth {value!r}")
        counter = perfect.setdefault(level, Counter())
        counter["pages"] += 1
        counter["perfect"] += all_right
    print(f"{'level':7} {'field':8} {'values':>6} {'right':>7} {'blank':>7}")
    for (level, field), counter in sorted(totals.items()):
        print(f"{level:7} {field:8} {counter['total']:6d} {counter['right'] / counter['total']:7.1%} {counter['missing'] / counter['total']:7.1%}")
    for level, counter in sorted(perfect.items()):
        print(f"{level:7} all fields right on {counter['perfect']}/{counter['pages']} pages")


if __name__ == "__main__":
    main()
