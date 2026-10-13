"""Domain checks that verify field values against the bill itself.

Each check states a property a correct Taipower bill always has, measured on
the sample bills: the charge lines add up to 應繳總金額, 稅前應繳總金額 plus
5% is 應繳總金額, emissions are about 0.5 kg per kWh, per-kWh rates and the
power factor stay in narrow ranges, and a billing period lasts one or two
months. A passing check is evidence for a value; a failing one marks it for
review, and when two OCR engines disagree the check picks the right reading.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Mapping

Values = Mapping[tuple[str, str], Any]

# Charge lines that, with their printed sign, add up to 應繳總金額.
CHARGE_ITEMS = (
    "basic_charge_twd",
    "non_contract_basic_charge_twd",
    "energy_charge_twd",
    "discount_twd",
    "power_factor_adjustment_twd",
    "ebill_discount_twd",
    "meter_rental_twd",
    "shared_public_charge_twd",
    "demand_response_discount_twd",
    "electricity_price_adjustment_twd",
    "supplemental_charge_refund_twd",
    "prepaid_applied_twd",
)
TOU_KWH = ("peak_kwh", "semi_peak_kwh", "saturday_semi_peak_kwh", "off_peak_kwh")

# Observed on the samples: fuel 1.20–2.66, renewable fund 0.0005–0.0023,
# average price 3.44, power factor 95–100. Bounds leave room for change.
RANGES: dict[tuple[str, str], tuple[float, float]] = {
    ("other_info", "fuel_cost_per_kwh_twd"): (0.1, 10.0),
    ("other_info", "renewable_fund_per_kwh_twd"): (0.00001, 0.05),
    ("other_info", "average_price_per_kwh_twd"): (0.5, 20.0),
    ("user_info", "power_factor_percent"): (50.0, 100.0),
}
CARBON_PER_KWH = (0.2, 0.8)  # Taipower's factor has been ~0.49–0.51 kg/kWh
PERIOD_DAYS = (20, 70)  # monthly (29–35) and bimonthly (~57) bills
AMOUNT_TOLERANCE = 1.0  # TWD; printed lines are rounded to 0.1 or 1

# Checks that relate several printed values; passing one is strong evidence.
ARITHMETIC = {"charges_sum", "tax", "carbon_per_kwh", "billing_period"}


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str = ""

    def __str__(self) -> str:
        status = "ok" if self.passed else "fail"
        return f"{self.name}:{status}" + (f"({self.detail})" if self.detail else "")


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").replace("·", ".").rstrip("元度%公斤 "))
    except ValueError:
        return None


def roc_date(value: Any) -> date | None:
    """'113/02/29' -> date(2024, 2, 29); None if not a valid ROC date."""
    parts = str(value).strip().split("/")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    try:
        return date(int(parts[0]) + 1911, int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def billed_kwh(values: Values) -> float | None:
    parts = [_number(values.get(("user_info", f"energy_consumption.{key}"))) for key in TOU_KWH]
    parts = [part for part in parts if part is not None]
    if parts:
        return sum(parts)
    return _number(values.get(("user_info", "energy_consumption_kwh")))


def charges_difference(values: Values) -> float | None:
    """Sum of printed charge lines minus 應繳總金額, if both are known."""
    total = _number(values.get(("charge_info", "total_amount_twd")))
    items = [_number(values.get(("charge_info", key))) for key in CHARGE_ITEMS]
    items = [item for item in items if item is not None]
    if total is None or len(items) < 2:
        return None
    return round(sum(items) - total, 2)


def tax_difference(values: Values) -> float | None:
    """稅前應繳總金額 × 1.05 minus 應繳總金額, if both are known."""
    total = _number(values.get(("charge_info", "total_amount_twd")))
    pretax = _number(values.get(("charge_info", "pretax_total_twd")))
    if total is None or pretax is None:
        return None
    return round(pretax * 1.05 - total, 2)


def _range(path: tuple[str, str]) -> Callable[[Any, Values], CheckResult | None]:
    low, high = RANGES[path]

    def check(value: Any, values: Values) -> CheckResult | None:
        number = _number(value)
        if number is None:
            return CheckResult("range", False, "not a number")
        return CheckResult("range", low <= number <= high, f"{low:g}–{high:g}")

    return check


def _carbon(value: Any, values: Values) -> CheckResult | None:
    emission, kwh = _number(value), billed_kwh(values)
    if emission is None or not kwh:
        return None
    ratio = emission / kwh
    return CheckResult("carbon_per_kwh", CARBON_PER_KWH[0] <= ratio <= CARBON_PER_KWH[1], f"{ratio:.3f} kg/kWh")


def _period(own: str, other: str) -> Callable[[Any, Values], CheckResult | None]:
    def check(value: Any, values: Values) -> CheckResult | None:
        this = roc_date(value)
        if this is None:
            return CheckResult("billing_period", False, "not a date")
        that = roc_date(values.get(("user_info", f"billing_period.{other}")))
        if that is None:
            return None
        start, end = (this, that) if own == "start" else (that, this)
        days = (end - start).days + 1
        return CheckResult("billing_period", PERIOD_DAYS[0] <= days <= PERIOD_DAYS[1], f"{days} days")

    return check


def _tax(value: Any, values: Values, *, own: str) -> CheckResult | None:
    candidate = {**values, ("charge_info", own): value}
    difference = tax_difference(candidate)
    if difference is None:
        return None
    return CheckResult("tax", abs(difference) <= AMOUNT_TOLERANCE, f"Δ{difference:g}")


def _charges(own: str) -> Callable[[Any, Values], CheckResult | None]:
    def check(value: Any, values: Values) -> CheckResult | None:
        difference = charges_difference({**values, ("charge_info", own): value})
        if difference is None:
            return None
        # A gap only shows some line is missing or misread, so only an exact
        # match is reported; it is strong evidence for every line involved.
        if abs(difference) <= AMOUNT_TOLERANCE:
            return CheckResult("charges_sum", True)
        return None

    return check


def _date(value: Any, values: Values) -> CheckResult | None:
    text = str(value)
    if "/" not in text:
        return None  # e.g. '105年02月23日'; format not checked
    return CheckResult("date", roc_date(text) is not None)


CHECKS: dict[tuple[str, str], list[Callable[[Any, Values], CheckResult | None]]] = {
    **{path: [_range(path)] for path in RANGES},
    ("other_info", "carbon_emission_kg"): [_carbon],
    ("user_info", "billing_period.start"): [_period("start", "end")],
    ("user_info", "billing_period.end"): [_period("end", "start")],
    ("charge_info", "total_amount_twd"): [
        lambda value, values: _tax(value, values, own="total_amount_twd"),
        _charges("total_amount_twd"),
    ],
    ("charge_info", "pretax_total_twd"): [lambda value, values: _tax(value, values, own="pretax_total_twd")],
    **{("charge_info", key): [_charges(key)] for key in CHARGE_ITEMS},
}


def check_field(block: str, key: str, value: Any, values: Values) -> list[CheckResult]:
    """Run every check that applies to ``block.key`` with ``value``."""
    checks = list(CHECKS.get((block, key), []))
    if key.endswith("_date"):
        checks.append(_date)
    results = []
    for check in checks:
        result = check(value, values)
        if result is not None:
            results.append(result)
    return results
