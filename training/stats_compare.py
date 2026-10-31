"""Compare OCR setups on the same pages, with statistics.

    python training/stats_compare.py out.md samples/truth_rows.json \
        A=reads_a.json B=reads_b.json C=reads_c.json

Each value is a paired trial: both setups read the same page. Differences are
tested with an exact McNemar test (only the values where the two disagree
count) and shown with a 95% interval from resampling whole pages, because
values on one page succeed or fail together. p values are Holm-corrected within
the table. Speed is a Wilcoxon signed-rank test on seconds per page.
"""

from __future__ import annotations

import json
import random
import statistics
import sys
from collections import defaultdict
from itertools import combinations
from math import comb, sqrt
from pathlib import Path

from eval_rows import group, same

RESAMPLES = 5000


def mcnemar(only_first: int, only_second: int) -> float:
    """Two-sided exact p value."""
    n = only_first + only_second
    if n == 0:
        return 1.0
    tail = sum(comb(n, k) for k in range(0, min(only_first, only_second) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def wilson(right: int, total: int) -> tuple[float, float]:
    if not total:
        return 0.0, 0.0
    z, p = 1.96, right / total
    centre = (p + z * z / (2 * total)) / (1 + z * z / total)
    half = z * sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return max(0.0, centre - half), min(1.0, centre + half)


def holm(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    adjusted = [0.0] * len(values)
    running = 0.0
    for rank, index in enumerate(order):
        running = max(running, min(1.0, (len(values) - rank) * values[index]))
        adjusted[index] = running
    return adjusted


def page_interval(pages: list[tuple[int, int, int]], rng: random.Random) -> tuple[float, float]:
    """95% interval of (first − second) accuracy, resampling whole pages. Each page: (first right, second right, values)."""
    if not pages:
        return 0.0, 0.0
    differences = []
    for _ in range(RESAMPLES):
        chosen = rng.choices(pages, k=len(pages))
        total = sum(page[2] for page in chosen)
        differences.append((sum(page[0] for page in chosen) - sum(page[1] for page in chosen)) / total if total else 0.0)
    differences.sort()
    return differences[int(RESAMPLES * 0.025)], differences[int(RESAMPLES * 0.975)]


def outcome(row: dict | None, column: str, expected) -> str:
    found = (row or {}).get(column)
    if found in (None, ""):
        return "blank"
    if same(found, expected):
        return "right"
    # A wrong figure on a page the app flags for checking may yet be caught.
    return "wrong" if (row or {}).get("需人工檢查") else "silent"


def main() -> None:
    out, truth_path = Path(sys.argv[1]), Path(sys.argv[2])
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    setups: dict[str, dict] = {}
    for argument in sys.argv[3:]:
        name, path = argument.split("=", 1)
        setups.setdefault(name, {}).update(json.loads(Path(path).read_text(encoding="utf-8")))
    names = list(setups)
    rng = random.Random(20261030)

    # (subset, field) -> setup -> list of (page, outcome)
    results: dict[tuple[str, str], dict[str, list[tuple[str, str]]]] = defaultdict(lambda: defaultdict(list))
    for path, expected in truth.items():
        if not all(path in setups[name] for name in names):
            continue
        parts = Path(path).parts
        source = "synthetic" if "synth" in path else "real"
        level = parts[-2] if ("degraded" in path or "synth" in path) else "original"
        level = {"clean": "original"}.get(level, level)
        for column, value in expected.items():
            field = group(column)
            for name in names:
                result = outcome(setups[name][path]["row"], column, value)
                for key in ((source, level, field), (source, level, "ALL"), (source, "ALL", "ALL"), (source, "ALL", field)):
                    results[key][name].append((path, result))

    lines = [f"# OCR setups compared ({', '.join(names)})", "",
             f"Paired on the same pages. Pages compared: {sum(1 for path in truth if all(path in setups[name] for name in names))}.", ""]
    lines += ["## Accuracy of each setup", "",
              "Wrong = wrong figure, page flagged for checking; silent = wrong figure, nothing flagged.", "",
              "| Source | Quality | Field | Values | Pages | " + " | ".join(f"{name} right (95% CI) / wrong / silent / blank" for name in names) + " |",
              "|---|---|---|---:|---:|" + "---:|" * len(names)]
    for key in sorted(results):
        table = results[key]
        first = table[names[0]]
        cells = []
        for name in names:
            right = sum(1 for _, item in table[name] if item == "right")
            wrong = sum(1 for _, item in table[name] if item == "wrong")
            silent = sum(1 for _, item in table[name] if item == "silent")
            blank = sum(1 for _, item in table[name] if item == "blank")
            low, high = wilson(right, len(first))
            cells.append(f"{right / len(first):.1%} ({low:.0%}–{high:.0%}) / {wrong / len(first):.1%} / {silent / len(first):.1%} / {blank / len(first):.1%}")
        lines.append(f"| {key[0]} | {key[1]} | {key[2]} | {len(first)} | {len({page for page, _ in first})} | " + " | ".join(cells) + " |")

    lines += ["", "## Paired differences", "",
              "McNemar exact test; p is Holm-corrected over this table. Interval is for the accuracy difference, resampling pages.", "",
              "| Source | Quality | Field | Comparison | Only first right | Only second right | Difference (95% CI) | p (raw) | p (Holm) |",
              "|---|---|---|---|---:|---:|---:|---:|---:|"]
    rows = []
    for key in sorted(results):
        table = results[key]
        for one, two in combinations(names, 2):
            only_one = only_two = 0
            by_page: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
            for (page, a), (_, b) in zip(table[one], table[two]):
                only_one += a == "right" and b != "right"
                only_two += b == "right" and a != "right"
                by_page[page][0] += a == "right"
                by_page[page][1] += b == "right"
                by_page[page][2] += 1
            pages = [tuple(item) for item in by_page.values()]
            total = sum(page[2] for page in pages)
            low, high = page_interval(pages, rng)
            difference = (sum(page[0] for page in pages) - sum(page[1] for page in pages)) / total
            rows.append((key, one, two, only_one, only_two, difference, low, high, mcnemar(only_one, only_two)))
    adjusted = holm([row[-1] for row in rows])
    for row, p_holm in zip(rows, adjusted):
        key, one, two, only_one, only_two, difference, low, high, p = row
        mark = " **" if p_holm < 0.05 else ""
        lines.append(f"| {key[0]} | {key[1]} | {key[2]} | {one} vs {two} | {only_one} | {only_two} | {difference:+.1%} ({low:+.1%}, {high:+.1%}) | {p:.4f} | {p_holm:.4f}{mark} |")

    lines += ["", "## Seconds per page", ""]
    timed = {name: {path: setups[name][path]["seconds"] for path in truth if path in setups[name] and setups[name][path].get("seconds") is not None} for name in names}
    lines += ["| Setup | Pages | Median | Mean | Slowest |", "|---|---:|---:|---:|---:|"]
    for name in names:
        values = list(timed[name].values())
        if values:
            lines.append(f"| {name} | {len(values)} | {statistics.median(values):.2f} | {statistics.mean(values):.2f} | {max(values):.2f} |")
    try:
        from scipy.stats import wilcoxon
    except ImportError:
        wilcoxon = None
    if wilcoxon:
        lines += ["", "| Comparison | Pages | Median difference (s) | Wilcoxon p |", "|---|---:|---:|---:|"]
        for one, two in combinations(names, 2):
            shared = [path for path in timed[one] if path in timed[two]]
            if len(shared) > 5:
                differences = [timed[one][path] - timed[two][path] for path in shared]
                if any(differences):
                    lines.append(f"| {one} vs {two} | {len(shared)} | {statistics.median(differences):+.2f} | {wilcoxon(differences).pvalue:.4g} |")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    main()
