"""Planning the electricity budget: site × month × energy (grey, solar, wind).

A site has one annual budget: what is not set for an energy is shared
among the energies left by their estimated costs (all to grey when none has
an estimate). For each site and energy of a year:

* the estimated kWh and unit price of every month — typed in, or by default
  last year's actual kWh of the month, and for the price grey's average
  actual price last year, green power's contract (price and the fees on
  top) or else last year's actual price;
* estimated cost = kWh × price × (1 + adjustment %);
* the annual budget — approved, or else the estimated costs added up —
  shared out over the months by their estimated cost (planned budget);
* the actual kWh and cost from the documents; the adopted cost is the
  actual when there is one, else the estimate;
* the balance rolls on: prior carry (January 0, then last month's balance),
  available = planned + carry, month-end balance = available − adopted;
* a rolling forecast: months with documents are their actuals; the others
  are last year's month × this year's trend (actual kWh so far ÷ last
  year's for the same months), else this year's average month so far,
  else the estimate — at this year's actual price so far (else the
  estimated price). The year-end
  forecast is held against the annual budget, and what is left of the
  budget is shared over the months to come by the forecast (adjusted
  budget). The planned budget itself does not change.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Mapping

from .budget import contract_for, per_kwh
from .mix import SOLAR, WIND, energy_mix
from .store import Store

ENERGIES = ("grey", "solar", "wind")
ENERGY_NAMES = {"grey": "Grey", "solar": "Solar", "wind": "Wind"}
_GREEN = {"solar": SOLAR, "wind": WIND}
MONTHS = [f"{number:02d}" for number in range(1, 13)]


def _d(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001 - not a number
        return None


def _plain(value: Decimal | None, places: int = 2) -> int | float | None:
    if value is None:
        return None
    value = value.quantize(Decimal(1) / (10 ** places), rounding=ROUND_HALF_UP) if places else value.quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return int(value) if value == value.to_integral_value() else float(value)


def actuals(store: Store, service_energy: Mapping[str, str]) -> dict[tuple[str, str, str], tuple[Decimal | None, Decimal | None]]:
    """(site, YYYY-MM, energy) → (kWh, cost) from every document."""
    pages = store.pages(bills_only=True, visible_only=True)
    found: dict[tuple[str, str, str], list[Decimal | None]] = defaultdict(lambda: [None, None])

    def add(key: tuple[str, str, str], index: int, value: Any) -> None:
        number = _d(value)
        if number is not None:
            found[key][index] = (found[key][index] or Decimal(0)) + number

    for row in energy_mix(pages, store, service_energy)["rows"]:
        if not row.get("site"):
            continue
        site, month = row["site"], row["month"]
        add((site, month, "grey"), 0, row.get("grey_kwh"))
        add((site, month, "grey"), 1, row.get("grey_cost"))
        for energy, name in _GREEN.items():
            add((site, month, energy), 0, (row.get("green_kwh") or {}).get(name))
            add((site, month, energy), 1, (row.get("green_cost_by_energy") or {}).get(name))
    return {key: (value[0], value[1]) for key, value in found.items()}


def _largest_remainder(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """``total`` shared by ``weights`` in whole NT$, adding up exactly."""
    whole = sum(weights)
    if not whole:
        weights, whole = [Decimal(1)] * len(weights), Decimal(len(weights))
    raw = [total * weight / whole for weight in weights]
    floors = [value.to_integral_value(rounding="ROUND_FLOOR") for value in raw]
    left = int((total.to_integral_value(rounding=ROUND_HALF_UP) - sum(floors)))
    order = sorted(range(len(raw)), key=lambda index: raw[index] - floors[index], reverse=True)
    for index in order[:max(left, 0)]:
        floors[index] += 1
    return floors


def plan(store: Store, year: int, service_energy: Mapping[str, str]) -> dict[str, Any]:
    """Every site's plan for a year, worked out."""
    sites = store.sites()
    entities = {entity["id"]: entity["name"] for entity in store.entities()}
    contracts = store.contracts()
    saved = {(row["site"], row["energy"]): row for row in store.budget_plans(year)}
    actual = actuals(store, service_energy)
    result = []
    for site in sites:
        name = site["name"]
        energies = {}
        for energy in ENERGIES:
            energies[energy] = _energy_plan(name, site.get("entity_id"), energy, year, saved.get((name, energy)), actual, contracts)
        total_row = saved.get((name, "total"))
        approved_total = _d(total_row["annual_budget"]) if total_row else None
        if approved_total is not None:
            # The site's budget, less the energies set by hand, shared by the others' estimates.
            unset = [energy for energy in ENERGIES if energies[energy]["annual_input"] is None]
            left = approved_total - sum((_d(energies[energy]["annual_input"]) or Decimal(0) for energy in ENERGIES if energy not in unset), Decimal(0))
            if unset:
                weights = [sum((_d(month["estimated_cost"]) or Decimal(0) for month in energies[energy]["months"]), Decimal(0)) for energy in unset]
                if not sum(weights):
                    weights = [Decimal(1) if energy == "grey" else Decimal(0) for energy in unset]
                    if not sum(weights):
                        weights = [Decimal(1)] * len(unset)
                shares = _largest_remainder(max(left, Decimal(0)), weights)
                for energy, share in zip(unset, shares):
                    energies[energy] = _energy_plan(name, site.get("entity_id"), energy, year, saved.get((name, energy)), actual, contracts,
                                                    site_share=share)
        sum_annual = sum((_d(energies[energy]["annual"]) or Decimal(0) for energy in ENERGIES), Decimal(0))
        result.append({
            "site": name, "entity": entities.get(site.get("entity_id")),
            "total_budget": _plain(approved_total), "energies_total": _plain(sum_annual),
            "allocation": None if approved_total is None else ("ok" if approved_total == sum_annual else "check"),
            "energies": energies,
        })
    return {"year": year, "sites": result}


def _energy_plan(site: str, entity_id: Any, energy: str, year: int, saved: Mapping[str, Any] | None,
                 actual: Mapping[tuple[str, str, str], tuple[Decimal | None, Decimal | None]], contracts: list,
                 site_share: Decimal | None = None) -> dict[str, Any]:
    """``site_share``: the energy's part of the site's budget, when not set for the energy itself."""
    saved = saved or {}
    typed = saved.get("months") or {}
    adjustment = _d(saved.get("adjustment_pct")) or Decimal(0)
    last_year = {month: actual.get((site, f"{year - 1}-{month}", energy), (None, None)) for month in MONTHS}
    # Last year's actual price over the months with both kWh and cost.
    this_year = {month: actual.get((site, f"{year}-{month}", energy), (None, None)) for month in MONTHS}

    def average_kwh(values: Mapping[str, tuple[Decimal | None, Decimal | None]]) -> Decimal | None:
        known = [kwh for kwh, _ in values.values() if kwh is not None]
        return sum(known) / len(known) if known else None

    def price(values: Mapping[str, tuple[Decimal | None, Decimal | None]]) -> Decimal | None:
        priced = [(kwh, cost) for kwh, cost in values.values() if kwh and cost is not None]
        return sum(cost for _, cost in priced) / sum(kwh for kwh, _ in priced) if priced else None

    # Without last year's month: last year's average month, else this year's so far.
    last_average, this_average = average_kwh(last_year), average_kwh(this_year)
    last_price, this_price = price(last_year), price(this_year)

    months = []
    for month in MONTHS:
        key = f"{year}-{month}"
        given = typed.get(month) or {}
        kwh, kwh_source = _d(given.get("kwh")), "manual"
        if kwh is None:
            for candidate, source in ((last_year[month][0], "last year"), (last_average, "last year avg"), (this_average, "this year avg"), (None, None)):
                if candidate is not None or source is None:
                    kwh, kwh_source = candidate, source
                    break
        rate, rate_source = _d(given.get("rate")), "manual"
        if rate is None:
            contract = contract_for(contracts, entity_id, _GREEN[energy], key) if energy in _GREEN else None
            if contract is not None:
                rate, rate_source = Decimal(str(per_kwh(contract))), "contract"
            elif last_price is not None:
                rate, rate_source = last_price, "last year"
            elif this_price is not None:
                rate, rate_source = this_price, "this year"
            else:
                rate_source = None
        estimated = kwh * rate * (1 + adjustment / 100) if kwh is not None and rate is not None else None
        actual_kwh, actual_cost = actual.get((site, key, energy), (None, None))
        months.append({"month": key, "kwh": _plain(kwh, 0), "kwh_source": kwh_source, "rate": _plain(rate, 4), "rate_source": rate_source,
                       "estimated_cost": estimated, "actual_kwh": actual_kwh, "actual_cost": actual_cost})

    estimates = [item["estimated_cost"] or Decimal(0) for item in months]
    approved = _d(saved.get("annual_budget"))
    if approved is not None:
        annual, method = approved, "approved"
    elif site_share is not None:
        annual, method = site_share, "site share"
    else:
        annual, method = sum(estimates, Decimal(0)).quantize(Decimal(1), rounding=ROUND_HALF_UP), "estimate"
    # Shared by the estimates; a month without one weighs as the average
    # month, and with none at all the budget is shared evenly.
    known = [item["estimated_cost"] for item in months if item["estimated_cost"]]
    if len(known) == 12:
        weights, allocation = estimates, "estimate"
    elif known:
        average = sum(known) / len(known)
        weights, allocation = [item["estimated_cost"] or average for item in months], "estimate + average"
    else:
        weights, allocation = [Decimal(1)] * 12, "even"
    for item in months:
        item["weight_source"] = "estimate" if item["estimated_cost"] else ("average" if allocation != "even" else "even")
    planned = _largest_remainder(annual, weights) if annual else [Decimal(0)] * 12
    carry = Decimal(0)
    for item, share in zip(months, planned):
        has_actual = item["actual_cost"] is not None
        adopted = item["actual_cost"] if has_actual else (item["estimated_cost"] or Decimal(0))
        available = share + carry
        balance = available - adopted
        item.update(planned=_plain(share), has_actual=has_actual, adopted_cost=_plain(adopted), prior_carry=_plain(carry),
                    available=_plain(available), balance=_plain(balance), estimated_cost=_plain(item["estimated_cost"]),
                    actual_kwh=_plain(item["actual_kwh"], 0), actual_cost=_plain(item["actual_cost"]),
                    actual_rate=_plain(item["actual_cost"] / item["actual_kwh"], 4) if has_actual and item["actual_kwh"] else None)
        carry = balance
    spent = sum((_d(item["actual_cost"]) or Decimal(0) for item in months), Decimal(0))
    forecast = _forecast(months, last_year, adjustment, annual, spent)
    return {
        "energy": energy, "annual_input": _plain(approved), "annual": _plain(annual),
        "method": method, "allocation": allocation, "adjustment_pct": _plain(adjustment, 4),
        "months": months, "actual_cost": _plain(spent),
        "utilization": float(round(spent / annual, 4)) if annual else None,
        "forecast": forecast,
    }


def _forecast(months: list[dict[str, Any]], last_year: Mapping[str, tuple[Decimal | None, Decimal | None]],
              adjustment: Decimal, annual: Decimal, spent: Decimal) -> dict[str, Any]:
    """The year as it is going: actuals so far, the months to come by this year's trend."""
    closed = [item for item in months if item["has_actual"]]
    last_kwh = {item["month"]: last_year[item["month"][5:]][0] for item in months}
    # The trend: this year's kWh against last year's, over the same months.
    pairs = [(_d(item["actual_kwh"]), last_kwh[item["month"]]) for item in closed]
    pairs = [(actual, base) for actual, base in pairs if actual is not None and base]
    trend = sum(actual for actual, _ in pairs) / sum(base for _, base in pairs) if pairs else None
    # Without last year: this year's average month so far (the run rate).
    so_far = [_d(item["actual_kwh"]) for item in closed if item["actual_kwh"] is not None]
    run_rate = sum(so_far) / len(so_far) if so_far else None
    priced = [(_d(item["actual_kwh"]), _d(item["actual_cost"])) for item in closed]
    priced = [(kwh, cost) for kwh, cost in priced if kwh and cost is not None]
    actual_price = sum(cost for _, cost in priced) / sum(kwh for kwh, _ in priced) if priced else None

    open_costs: list[Decimal] = []
    for item in months:
        if item["has_actual"]:
            item.update(forecast_kwh=item["actual_kwh"], forecast_cost=item["actual_cost"], forecast_source="actual")
            continue
        if last_kwh[item["month"]] is not None and trend is not None:
            kwh, source = last_kwh[item["month"]] * trend, "trend"
        elif run_rate is not None:
            kwh, source = run_rate, "run rate"
        else:
            kwh, source = _d(item["kwh"]), "estimate"
        price = actual_price if actual_price is not None else _d(item["rate"])
        cost = kwh * price * (1 + adjustment / 100) if kwh is not None and price is not None else None
        item.update(forecast_kwh=_plain(kwh, 0), forecast_cost=_plain(cost), forecast_source=source)
        open_costs.append(cost or Decimal(0))

    # What is left of the budget, shared over the months to come by the forecast.
    remaining = annual - spent
    open_months = [item for item in months if not item["has_actual"]]
    shares = _largest_remainder(remaining, open_costs) if open_months and remaining > 0 else [Decimal(0)] * len(open_months)
    for item, share in zip(open_months, shares):
        item["adjusted_budget"] = _plain(share)
    for item in closed:
        item["adjusted_budget"] = None
    year_end = spent + sum(open_costs, Decimal(0))
    return {
        "months_closed": len(closed),
        "trend": float(round(trend, 4)) if trend is not None else None,
        "price": _plain(actual_price, 4),
        "year_end": _plain(year_end),
        "variance": _plain(year_end - annual),
        "remaining": _plain(remaining),
    }


# ------------------------------------------------------------------ Power BI
FACT_COLUMNS = ["Year", "Month", "MonthStart", "SiteName", "EnergyType", "AnnualBudget", "EstimatedKWh", "EstimatedPrice",
                "EstimatedCost", "PlannedBudget", "ActualKWh", "ActualCost", "AdoptedCost", "PriorCarry", "AvailableBudget",
                "MonthBalance", "DataStatus", "BudgetMethod", "ForecastKWh", "ForecastCost", "AdjustedBudget"]
ACTUAL_COLUMNS = ["Year", "Month", "MonthStart", "SiteName", "ActualKWh", "ActualCost", "GreyKWh", "GreyCost",
                  "SolarKWh", "SolarCost", "WindKWh", "WindCost"]


def fact_rows(planned: Mapping[str, Any]) -> list[list[Any]]:
    """PowerBI_EnergyBudget: a row per site, month and energy."""
    from datetime import date

    rows = []
    for site in planned["sites"]:
        for energy in ENERGIES:
            item = site["energies"][energy]
            for month in item["months"]:
                year, number = (int(part) for part in month["month"].split("-"))
                rows.append([
                    year, number, date(year, number, 1), site["site"], ENERGY_NAMES[energy], item["annual"], month["kwh"], month["rate"],
                    month["estimated_cost"], month["planned"], month["actual_kwh"], month["actual_cost"], month["adopted_cost"],
                    month["prior_carry"], month["available"], month["balance"], "Actual" if month["has_actual"] else "Estimated",
                    {"approved": "Approved", "site share": "Site share", "estimate": "Estimate"}[item["method"]] + {"estimate": "", "estimate + average": " / gaps at average",
                                                                                  "even": " / even"}[item["allocation"]],
                    month["forecast_kwh"], month["forecast_cost"], month["adjusted_budget"],
                ])
    return rows


def actual_rows(actual: Mapping[tuple[str, str, str], tuple[Decimal | None, Decimal | None]]) -> list[list[Any]]:
    """Actual_Data: a row per site and month, each energy's kWh and cost."""
    from datetime import date

    by_month: dict[tuple[str, str], dict[str, tuple[Decimal | None, Decimal | None]]] = defaultdict(dict)
    for (site, month, energy), values in actual.items():
        by_month[(site, month)][energy] = values
    rows = []
    for (site, month), energies in sorted(by_month.items(), key=lambda item: (item[0][1], item[0][0])):
        year, number = (int(part) for part in month.split("-"))
        values = [energies.get(energy, (None, None)) for energy in ENERGIES]
        total_kwh = sum((kwh or Decimal(0) for kwh, _ in values), Decimal(0))
        total_cost = sum((cost or Decimal(0) for _, cost in values), Decimal(0))
        rows.append([year, number, date(year, number, 1), site, _plain(total_kwh, 0), _plain(total_cost),
                     *[_plain(value, 0 if index % 2 == 0 else 2) for pair in values for index, value in enumerate(pair)]])
    return rows


def planned_years(store: Store, years: Iterable[int]) -> list[int]:
    return sorted({year for year in years if store.budget_plans(year)})


# --------------------------------------------------------------- Excel in/out
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
SUMMARY_SHEET = "Electricity Budget"
ENERGY_SHEETS = {"grey": "Electricity Budget - Grey", "solar": "Electricity Budget - Solar", "wind": "Electricity Budget - Wind"}


def template_workbook(store: Store, year: int, service_energy: Mapping[str, str]) -> bytes:
    """The year's plan to fill in: typed values filled, defaults shown in their own columns (left empty: the default)."""
    import io

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    def head(sheet) -> None:
        for cell in sheet[1]:
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="DDEBF7")

    planned = plan(store, year, service_energy)
    saved = {(row["site"], row["energy"]): row for row in store.budget_plans(year)}
    workbook = Workbook()
    summary = workbook.active
    summary.title = SUMMARY_SHEET
    summary.append(["SiteName", "Entity", "Site Annual Budget", "Grey Budget", "Solar Budget", "Wind Budget", "Allocation Check"])
    head(summary)
    for index, site in enumerate(planned["sites"], start=2):
        typed = [(saved.get((site["site"], energy)) or {}).get("annual_budget") for energy in ("total", "grey", "solar", "wind")]
        check = f'=IF(C{index}="","",IF(ROUND(C{index}-SUM(D{index}:F{index}),0)=0,"OK","Check"))'
        summary.append([site["site"], site["entity"], *typed, check])
    grey_font = Font(color="808080")
    for energy, title in ENERGY_SHEETS.items():
        sheet = workbook.create_sheet(title)
        sheet.append(["SiteName", "AnnualBudget", "Adjustment %",
                      *[f"{name} kWh" for name in MONTH_NAMES], *[f"{name} Rate" for name in MONTH_NAMES],
                      *[f"{name} kWh (default)" for name in MONTH_NAMES], *[f"{name} Rate (default)" for name in MONTH_NAMES]])
        head(sheet)
        for site in planned["sites"]:
            row = saved.get((site["site"], energy)) or {}
            typed_months = row.get("months") or {}
            months = site["energies"][energy]["months"]
            sheet.append([
                site["site"], row.get("annual_budget"), row.get("adjustment_pct") or None,
                *[(typed_months.get(month) or {}).get("kwh") for month in MONTHS],
                *[(typed_months.get(month) or {}).get("rate") for month in MONTHS],
                *[item["kwh"] if item["kwh_source"] != "manual" else None for item in months],
                *[item["rate"] if item["rate_source"] != "manual" else None for item in months],
            ])
            for cell in sheet[sheet.max_row][27:]:
                cell.font = grey_font
        sheet.freeze_panes = "B2"
    notes = workbook.create_sheet("說明")
    for line in (
        f"{year} 年電力預算。SiteName 須與系統 Settings → Sites 相同。",
        "Electricity Budget：各 Site 的年度總預算（可不填）與 Grey／Solar／Wind 年度預算；能源預算留空 = 依各月預估費用加總。",
        "各能源工作表：每月預估度數（kWh）與單價（Rate），留空 = 使用系統預設（灰色欄位：去年同月實際度數；灰電單價為去年平均實際單價，綠電為合約單價＋轉供費＋憑證費）。",
        "預估費用 = 度數 × 單價 × (1 + Adjustment %)；年度預算依各月預估費用比例分配到各月。",
        "匯入時只讀 kWh、Rate、AnnualBudget、Adjustment % 與 Electricity Budget 的金額；預設欄位僅供參考。",
    ):
        notes.append([line])
    notes.column_dimensions["A"].width = 120
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _number(value: Any) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return float(str(value).replace(",", "").replace("%", "").strip())


def read_workbook(content: bytes, store: Store, year: int) -> tuple[list[dict[str, Any]], list[str]]:
    """The plan rows of a filled-in template, and what is wrong with it (nothing is imported then)."""
    import io

    from openpyxl import load_workbook

    try:
        workbook = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    except Exception:  # noqa: BLE001 - any unreadable file
        return [], ["not an Excel file (.xlsx)"]
    sites = {site["name"] for site in store.sites()}
    plans: dict[tuple[str, str], dict[str, Any]] = {}
    errors: list[str] = []

    def entry(site: str, energy: str) -> dict[str, Any]:
        return plans.setdefault((site, energy), {"site": site, "energy": energy, "annual_budget": None, "adjustment_pct": 0, "months": {}})

    def rows_of(title: str) -> list[tuple]:
        if title not in workbook.sheetnames:
            errors.append(f"sheet {title!r} not found (use the template)")
            return []
        return list(workbook[title].iter_rows(values_only=True))

    for number, cells in enumerate(rows_of(SUMMARY_SHEET)[1:], start=2):
        site = str(cells[0] or "").strip()
        if not site:
            continue
        if site not in sites:
            errors.append(f"{SUMMARY_SHEET} row {number}: site {site!r} is not in Settings → Sites")
            continue
        try:
            for energy, value in zip(("total", "grey", "solar", "wind"), cells[2:6]):
                if _number(value) is not None:
                    entry(site, energy)["annual_budget"] = _number(value)
        except ValueError:
            errors.append(f"{SUMMARY_SHEET} row {number}: a budget is not a number")
    for energy, title in ENERGY_SHEETS.items():
        for number, cells in enumerate(rows_of(title)[1:], start=2):
            site = str(cells[0] or "").strip()
            if not site:
                continue
            if site not in sites:
                errors.append(f"{title} row {number}: site {site!r} is not in Settings → Sites")
                continue
            cell = lambda index: cells[index] if index < len(cells) else None  # noqa: E731
            try:
                item = entry(site, energy)
                if _number(cell(1)) is not None:
                    item["annual_budget"] = _number(cell(1))
                item["adjustment_pct"] = _number(cell(2)) or 0
                for index, month in enumerate(MONTHS):
                    kwh, rate = _number(cell(3 + index)), _number(cell(15 + index))
                    if kwh is not None or rate is not None:
                        item["months"][month] = {key: value for key, value in (("kwh", kwh), ("rate", rate)) if value is not None}
            except ValueError:
                errors.append(f"{title} row {number}: a value is not a number")
    return list(plans.values()), errors
