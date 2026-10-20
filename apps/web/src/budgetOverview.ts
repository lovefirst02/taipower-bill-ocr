// The budget at a glance: totals, the year month by month, and each site's state.
import type { Plan, PlanEnergy, SitePlan } from "./types";

export const PLAN_ENERGIES: PlanEnergy[] = ["grey", "solar", "wind"];

export type Status = "on track" | "at risk" | "over" | "none";

/** On track when the year-end forecast is within the budget; at risk up to 5% over; over beyond. */
export function status(budget: number, forecast: number): Status {
  if (!budget) return "none";
  const over = (forecast - budget) / budget;
  return over <= 0 ? "on track" : over <= 0.05 ? "at risk" : "over";
}

export interface Summary {
  name: string;
  budget: number;
  /** Spent so far: the months with documents. */
  actual: number;
  forecast: number;
  variance: number;
  /** actual ÷ budget */
  used: number | null;
  status: Status;
}

const summary = (name: string, budget: number, actual: number, forecast: number): Summary => ({
  name, budget, actual, forecast, variance: forecast - budget,
  used: budget ? actual / budget : null, status: status(budget, forecast),
});

export interface MonthPoint {
  month: string;
  planned: number;
  /** null for a month without documents yet. */
  actual: number | null;
  forecast: number;
  cumulativePlanned: number;
  /** null after the last month with documents. */
  cumulativeActual: number | null;
  cumulativeForecast: number;
}

export interface Overview {
  total: Summary;
  byEnergy: Summary[];
  bySite: (Summary & { entity: string | null })[];
  months: MonthPoint[];
  /** The last month with documents in any site and energy shown (0-11), or -1. */
  lastClosed: number;
}

const add = (values: number[]) => values.reduce((sum, value) => sum + value, 0);

/** The plan as chosen (an entity, an energy, or all), added up. */
export function overview(plan: Plan, entity: string, energy: PlanEnergy | "all"): Overview {
  const sites = plan.sites.filter((site) => entity === "all" || (site.entity ?? "(no entity)") === entity);
  const energies = energy === "all" ? PLAN_ENERGIES : [energy];
  const of = (site: SitePlan, key: PlanEnergy) => site.energies[key];

  let lastClosed = -1;
  const months: MonthPoint[] = Array.from({ length: 12 }, (_, index) => {
    const parts = sites.flatMap((site) => energies.map((key) => of(site, key).months[index]));
    const closed = parts.some((month) => month.has_actual);
    if (closed) lastClosed = index;
    return {
      month: parts[0]?.month ?? "",
      planned: add(parts.map((month) => month.planned)),
      actual: closed ? add(parts.map((month) => month.actual_cost ?? 0)) : null,
      forecast: add(parts.map((month) => month.forecast_cost ?? 0)),
      cumulativePlanned: 0, cumulativeActual: null, cumulativeForecast: 0,
    };
  });
  let planned = 0, actual = 0, forecast = 0;
  months.forEach((month, index) => {
    planned += month.planned;
    forecast += month.forecast;
    actual += month.actual ?? 0;
    month.cumulativePlanned = planned;
    month.cumulativeForecast = forecast;
    month.cumulativeActual = index <= lastClosed ? actual : null;
  });

  const byEnergy = energies.map((key) => {
    const plans = sites.map((site) => of(site, key));
    return summary(key, add(plans.map((item) => item.annual)), add(plans.map((item) => item.actual_cost)), add(plans.map((item) => item.forecast.year_end)));
  });
  const bySite = sites.map((site) => {
    const plans = energies.map((key) => of(site, key));
    return {
      ...summary(site.site, add(plans.map((item) => item.annual)), add(plans.map((item) => item.actual_cost)), add(plans.map((item) => item.forecast.year_end))),
      entity: site.entity,
    };
  }).sort((a, b) => b.variance - a.variance);  // the most over budget first
  const total = summary("Total", add(byEnergy.map((item) => item.budget)), add(byEnergy.map((item) => item.actual)), add(byEnergy.map((item) => item.forecast)));
  return { total, byEnergy, bySite, months, lastClosed };
}
