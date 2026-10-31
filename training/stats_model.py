"""How much of the results each factor explains, and how much they vary.

    python training/stats_model.py out.md samples/truth_rows.json A=a.json B=b.json C=c.json

Right or wrong is a yes/no outcome, so "explained" comes from a logistic
regression (pseudo-R², and what each factor adds when dropped), with standard
errors clustered by page. Figures read are also compared with the truth as
numbers (R², MAE, MAPE); R² alone flatters, since a few large values dominate it.
"""

from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from eval_rows import group, same  # noqa: E402

RESAMPLES = 2000


def records(truth: dict, setups: dict[str, dict]) -> list[dict]:
    rows = []
    names = list(setups)
    for path, expected in truth.items():
        if not all(path in setups[name] for name in names):
            continue
        level = Path(path).parts[-2] if ("degraded" in path or "synth" in path) else "original"
        level = {"clean": "original"}.get(level, level)
        source = "synthetic" if "synth" in path else "real"
        for column, value in expected.items():
            for name in names:
                found = (setups[name][path]["row"] or {}).get(column)
                rows.append({"setup": name, "page": path, "column": column, "field": group(column), "quality": level, "source": source,
                             "right": int(same(found, value)), "found": found, "truth": value})
    return rows


def variance_table(data, names: list[str], rng: random.Random) -> list[str]:
    lines = ["## Variation between pages", "",
             "Accuracy per page: its spread, and how much a resample of the pages moves the overall figure. "
             "ICC is the share of the variation that lies between pages rather than within them.", "",
             "| Source | Setup | Pages | Accuracy | Variance per page | SD per page | Lowest page | Bootstrap SD | ICC |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for source in sorted({row["source"] for row in data}):
        for name in names:
            pages: dict[str, list[int]] = defaultdict(list)
            for row in data:
                if row["setup"] == name and row["source"] == source:
                    pages[row["page"]].append(row["right"])
            if not pages:
                continue
            means = np.array([np.mean(values) for values in pages.values()])
            sizes = np.array([len(values) for values in pages.values()])
            everything = np.concatenate([np.array(values) for values in pages.values()])
            boots = []
            keys = list(pages)
            for _ in range(RESAMPLES):
                chosen = rng.choices(keys, k=len(keys))
                boots.append(sum(sum(pages[key]) for key in chosen) / sum(len(pages[key]) for key in chosen))
            lines.append(f"| {source} | {name} | {len(pages)} | {everything.mean():.1%} | {means.var(ddof=1):.4f} | {means.std(ddof=1):.3f} | {means.min():.1%} | {np.std(boots):.4f} | {_icc(pages):.3f} |")
    return lines


def _icc(pages: dict[str, list[int]]) -> float:
    """ANOVA estimate of the intraclass correlation."""
    groups = [np.array(values, dtype=float) for values in pages.values() if len(values)]
    k = len(groups)
    n = sum(len(group_) for group_ in groups)
    if k < 2 or n <= k:
        return float("nan")
    mean = np.concatenate(groups).mean()
    between = sum(len(group_) * (group_.mean() - mean) ** 2 for group_ in groups) / (k - 1)
    within = sum(((group_ - group_.mean()) ** 2).sum() for group_ in groups) / (n - k)
    size = (n - sum(len(group_) ** 2 for group_ in groups) / n) / (k - 1)
    denominator = between + (size - 1) * within
    return float((between - within) / denominator) if denominator else float("nan")


def explained(data, names: list[str]) -> list[str]:
    import pandas as pd
    import statsmodels.api as sm
    import statsmodels.formula.api as smf

    lines = ["", "## What explains right and wrong", "",
             "Logistic regression of each value being right on the setup, the page quality and the field. "
             "A small penalty keeps the fit stable where a group is nearly all right or all wrong, so the coefficients are slightly conservative.", ""]
    frame = pd.DataFrame(data)
    for source in sorted(frame["source"].unique()):
        part = frame[frame["source"] == source]
        factors = {"setup": "C(setup)", "quality": "C(quality)", "field": "C(field)"}
        factors = {name: term for name, term in factors.items() if part[name].nunique() > 1}
        if not factors:
            continue
        formula = "right ~ " + " + ".join(factors.values())
        null = smf.glm("right ~ 1", part, family=sm.families.Binomial()).fit()
        full = smf.glm(formula, part, family=sm.families.Binomial()).fit_regularized(alpha=1e-4, L1_wt=0)
        probabilities = np.clip(full.predict(part), 1e-6, 1 - 1e-6)
        y = part["right"].to_numpy()
        loglike = float((y * np.log(probabilities) + (1 - y) * np.log(1 - probabilities)).sum())
        n = len(part)
        mcfadden = 1 - loglike / null.llf
        cox = 1 - np.exp(2 * (null.llf - loglike) / n)
        nagelkerke = cox / (1 - np.exp(2 * null.llf / n))
        lines += [f"### {source}: {n} readings on {part['page'].nunique()} pages", "",
                  f"McFadden R² {mcfadden:.3f}; Nagelkerke R² {nagelkerke:.3f}.", "",
                  "| Factor left out | McFadden R² without it | R² it accounts for |", "|---|---:|---:|"]
        for name, term in factors.items():
            others = [other for key, other in factors.items() if key != name]
            reduced = smf.glm("right ~ " + (" + ".join(others) or "1"), part, family=sm.families.Binomial())
            fit = reduced.fit_regularized(alpha=1e-4, L1_wt=0) if others else reduced.fit()
            p = np.clip(fit.predict(part), 1e-6, 1 - 1e-6)
            ll = float((y * np.log(p) + (1 - y) * np.log(1 - p)).sum())
            r2 = 1 - ll / null.llf
            lines.append(f"| {name} | {r2:.3f} | {mcfadden - r2:.3f} |")
        if "setup" in factors and part["setup"].nunique() > 1:
            lines += ["", "Odds of a right reading against the first setup, other factors held equal; intervals clustered by page.", "",
                      "| Setup | Odds ratio | 95% CI | p |", "|---|---:|---:|---:|"]
            try:
                robust = smf.glm(formula, part, family=sm.families.Binomial()).fit(
                    cov_type="cluster", cov_kwds={"groups": part["page"].astype("category").cat.codes.to_numpy()})
                intervals = robust.conf_int()
                for term in robust.params.index:
                    if term.startswith("C(setup)"):
                        name = term.split("[T.")[1].rstrip("]")
                        lines.append(f"| {name} | {np.exp(robust.params[term]):.2f} | {np.exp(intervals.loc[term, 0]):.2f}–{np.exp(intervals.loc[term, 1]):.2f} | {robust.pvalues[term]:.4f} |")
            except Exception as error:  # noqa: BLE001 - perfect separation: say so, the rest stands
                lines.append(f"| not estimable | | | {type(error).__name__}: a group is all right or all wrong |")
        lines.append("")
    return lines


def numeric(data, names: list[str]) -> list[str]:
    lines = ["## Figures as numbers", "",
             "Only figures that were read at all; blanks are in the tables above. Median APE is the typical miss; "
             "R² on the log scale stops a few large values from dominating.", "",
             "| Source | Setup | Figures read | R² | R² (log) | MAE | MAPE | Median APE | Off by over 1% |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for source in sorted({row["source"] for row in data}):
        for name in names:
            pairs = []
            for row in data:
                if row["setup"] != name or row["source"] != source or not isinstance(row["truth"], (int, float)):
                    continue
                try:
                    pairs.append((float(row["truth"]), float(row["found"])))
                except (TypeError, ValueError):
                    continue
            if len(pairs) < 3:
                continue
            truth, found = np.array(pairs).T
            total = ((truth - truth.mean()) ** 2).sum()
            r2 = 1 - ((truth - found) ** 2).sum() / total if total else float("nan")
            log_truth, log_found = np.sign(truth) * np.log1p(np.abs(truth)), np.sign(found) * np.log1p(np.abs(found))
            total = ((log_truth - log_truth.mean()) ** 2).sum()
            r2_log = 1 - ((log_truth - log_found) ** 2).sum() / total if total else float("nan")
            nonzero = truth != 0
            ape = np.abs(truth[nonzero] - found[nonzero]) / np.abs(truth[nonzero])
            lines.append(f"| {source} | {name} | {len(pairs)} | {r2:.4f} | {r2_log:.4f} | {np.abs(truth - found).mean():,.1f} | {ape.mean():.2%} | {np.median(ape):.2%} | {(ape > 0.01).mean():.1%} |")
    return lines


def main() -> None:
    out, truth_path = Path(sys.argv[1]), Path(sys.argv[2])
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    setups: dict[str, dict] = {}
    for argument in sys.argv[3:]:
        name, path = argument.split("=", 1)
        setups.setdefault(name, {}).update(json.loads(Path(path).read_text(encoding="utf-8")))
    names = list(setups)
    data = records(truth, setups)
    rng = random.Random(20261030)
    lines = [f"# Variation and explanatory power ({', '.join(names)})", ""]
    lines += variance_table(data, names, rng)
    lines += explained(data, names)
    lines += numeric(data, names)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
