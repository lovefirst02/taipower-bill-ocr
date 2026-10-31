"""Where wrong values come from: not boxed, boxed but misread, or read but put in the wrong field.

    python training/error_sources.py samples/truth_rows.json reads.json

Attribution from the lines the setup read. A value exactly on some line was read
(so a miss is the parser's). Else a line much like it means it was boxed and
misread. Else it was probably not boxed at all (or read beyond recognition).
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from eval_rows import group, same  # noqa: E402

SIMILAR = 0.6


def flat(text: str) -> str:
    return re.sub(r"[\s,，*元]", "", str(text))


def forms(value) -> list[str]:
    """The ways a value may be printed."""
    if isinstance(value, (int, float)):
        whole = f"{value:.1f}"
        return [whole, whole[:-2]] if whole.endswith(".0") else [whole]
    return [str(value)]


def pieces(column: str, value) -> list[list[str]]:
    if column.startswith("Billing") and "~" in str(value):
        return [[part] for part in str(value).split("~")]
    return [forms(value)]


def source(pieces_of_value: list[list[str]], lines: list[str]) -> str:
    worst = "read"
    for options in pieces_of_value:
        if any(re.search(rf"(?<![\d.]){re.escape(option)}(?![\d.])", line) for option in options for line in lines):
            continue
        close = max((SequenceMatcher(None, option, line).ratio() for option in options for line in lines), default=0)
        if close >= SIMILAR:
            worst = "misread" if worst == "read" else worst
        else:
            return "not boxed"
    return worst


def main() -> None:
    truth = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    reads = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    table: dict[tuple[str, str], Counter] = {}
    fields: dict[tuple[str, str], Counter] = {}
    for path, expected in truth.items():
        if path not in reads:
            continue
        kind = "synthetic" if "synth" in path else "real"
        level = Path(path).parent.name if ("degraded" in path or "synth" in path) else "original"
        level = {"clean": "original"}.get(level, level)
        lines = [flat(line["text"]).replace("～", "~") for line in reads[path]["lines"]]
        row = reads[path]["row"] or {}
        for column, value in expected.items():
            if value == 0:  # a lone 0 is on every page: tells nothing
                continue
            result = "right"
            if not same(row.get(column), value):
                found = source(pieces(column, value), lines)
                result = {"read": "read, wrong field", "misread": "boxed, misread", "not boxed": "not boxed"}[found]
            table.setdefault((kind, level), Counter())[result] += 1
            fields.setdefault((kind, group(column)), Counter())[result] += 1
    kinds = ["right", "read, wrong field", "boxed, misread", "not boxed"]
    for title, data in (("By quality", table), ("By field", fields)):
        print(f"\n{title}\n{'':26}" + "".join(f"{kind:>20}" for kind in kinds))
        for key, counter in sorted(data.items()):
            total = sum(counter.values())
            print(f"{key[0]:10} {key[1]:12} {total:4d}" + "".join(f"{counter[kind]:>13} {counter[kind] / total:5.1%}" for kind in kinds))


if __name__ == "__main__":
    main()
