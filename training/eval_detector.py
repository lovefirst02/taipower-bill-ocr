"""Field by field, one detector's reads against another's, tuning pages kept apart from test pages.

    python training/eval_detector.py samples/truth_rows.json Paddle=D.json Own=E.json

The real bills are split once, by their place in samples/labels.json (every third
for tuning), before any result was seen. Thresholds are chosen on the tuning pages
only; the test pages say how good it is. Not boxed as in error_sources.py.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from error_sources import flat, pieces, source  # noqa: E402
from eval_rows import group, same  # noqa: E402

TUNING = ("high_voltage_2024_page01", "industrial_tou_2024", "legacy_2013", "user_sample_2021")


def split_of(path: str) -> str:
    return "tuning" if Path(path).stem in TUNING else "test"


def quality_of(path: str) -> str:
    return Path(path).parent.name if "degraded" in path else "original"


def outcome(entry: dict, column: str, value) -> str:
    row = entry["row"] or {}
    found = row.get(column)
    if same(found, value):
        return "right"
    if found not in (None, ""):
        return "wrong"
    lines = [flat(line["text"]).replace("～", "~") for line in entry["lines"]]
    return "not boxed" if value != 0 and source(pieces(column, value), lines) == "not boxed" else "blank"


def tally(truth: dict, reads: dict, pages: set[str] | None = None) -> dict[tuple[str, str, str], Counter]:
    table: dict[tuple[str, str, str], Counter] = {}
    for path, expected in truth.items():
        if path not in reads or "synth" in path or (pages is not None and path not in pages):
            continue
        for column, value in expected.items():
            result = outcome(reads[path], column, value)
            for key in ((split_of(path), quality_of(path), "ALL"), (split_of(path), "ALL", "ALL"), (split_of(path), "ALL", group(column))):
                table.setdefault(key, Counter())[result] += 1
    return table


def main() -> None:
    truth = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    setups = {}
    for argument in sys.argv[2:]:
        name, path = argument.split("=", 1)
        setups[name] = tally(truth, json.loads(Path(path).read_text(encoding="utf-8")))
    keys = sorted({key for table in setups.values() for key in table})
    print(f"{'split':7} {'quality':9} {'field':9} {'values':>6}  " + "  ".join(f"{name + ' right/wrong/blank/not boxed':>38}" for name in setups))
    for key in keys:
        cells = []
        total = 0
        for table in setups.values():
            counter = table.get(key, Counter())
            total = sum(counter.values())
            cells.append(f"{counter['right'] / max(total, 1):>12.1%} {counter['wrong'] / max(total, 1):>7.1%} {counter['blank'] / max(total, 1):>7.1%} {counter['not boxed'] / max(total, 1):>9.1%}")
        print(f"{key[0]:7} {key[1]:9} {key[2]:9} {total:6d}  " + "  ".join(cells))


if __name__ == "__main__":
    main()
