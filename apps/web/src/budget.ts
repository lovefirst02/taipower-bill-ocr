// Budget against actual, for the dashboard.
import { SOLAR, WIND } from "./mix";
import type { BudgetRow, MixRow } from "./types";

export interface Metric {
  key: string;
  label: string;
  unit: "kWh" | "NT$";
  actual: (row: MixRow) => number;
  budget: (row: BudgetRow) => number | null;
}

export const METRICS: Metric[] = [
  { key: "total_cost", label: "總費用", unit: "NT$", actual: (row) => (row.grey_cost ?? 0) + (row.green_cost ?? 0), budget: (row) => row.total_cost },
  { key: "total_kwh", label: "總用電", unit: "kWh", actual: (row) => (row.grey_kwh ?? 0) + row.green_total, budget: (row) => row.total_kwh },
  { key: "grey_cost", label: "灰電費用", unit: "NT$", actual: (row) => row.grey_cost ?? 0, budget: (row) => row.grey_cost },
  { key: "grey_kwh", label: "灰電度數", unit: "kWh", actual: (row) => row.grey_kwh ?? 0, budget: (row) => row.grey_kwh },
  { key: "solar_cost", label: "太陽能費用", unit: "NT$", actual: (row) => row.green_cost_by_energy?.[SOLAR] ?? 0, budget: (row) => row.solar_cost },
  { key: "solar_kwh", label: "太陽能度數", unit: "kWh", actual: (row) => row.green_kwh[SOLAR] ?? 0, budget: (row) => row.solar_kwh },
  { key: "wind_cost", label: "風力費用", unit: "NT$", actual: (row) => row.green_cost_by_energy?.[WIND] ?? 0, budget: (row) => row.wind_cost },
  { key: "wind_kwh", label: "風力度數", unit: "kWh", actual: (row) => row.green_kwh[WIND] ?? 0, budget: (row) => row.wind_kwh },
];

export interface Comparison {
  name: string;
  budget: number | null;
  actual: number;
  /** actual − budget; null without a budget. */
  variance: number | null;
  /** variance ÷ budget. */
  ratio: number | null;
  /** Part of the budget was estimated from a contract price. */
  estimated: boolean;
  /** Documents have come in for it (a month not yet in is not compared). */
  has_actual: boolean;
  /** The whole budget, months without documents too (the total only). */
  full_budget?: number | null;
}

const round = (value: number) => Math.round(value * 100) / 100;

/** Budget and actual of one metric, grouped (by month, site or entity); groups in name order. */
export function compare(
  metric: Metric,
  actuals: MixRow[],
  budgets: BudgetRow[],
  key: (row: { month: string; site: string | null; entity: string | null }) => string,
): Comparison[] {
  const groups = new Map<string, Comparison>();
  const group = (name: string) => {
    if (!groups.has(name)) groups.set(name, { name, budget: null, actual: 0, variance: null, ratio: null, estimated: false, has_actual: false });
    return groups.get(name)!;
  };
  for (const row of actuals) {
    const item = group(key(row));
    item.actual = round(item.actual + metric.actual(row));
    item.has_actual = true;
  }
  for (const row of budgets) {
    const value = metric.budget(row);
    if (value === null || value === undefined) continue;
    const item = group(key(row));
    item.budget = round((item.budget ?? 0) + value);
    if (row.estimated.includes(metric.key) || (metric.key === "total_cost" && row.estimated.length > 0)) item.estimated = true;
  }
  return [...groups.values()]
    .map((item) => {
      const variance = item.budget === null || !item.has_actual ? null : round(item.actual - item.budget);
      return { ...item, variance, ratio: item.budget && variance !== null ? variance / item.budget : null };
    })
    .sort((a, b) => a.name.localeCompare(b.name));
}

/** All groups added up: budget against actual where documents have come in, and the whole budget. */
export function overall(items: Comparison[]): Comparison {
  const sum = (values: (number | null)[]) => (values.some((value) => value !== null) ? round(values.reduce<number>((total, value) => total + (value ?? 0), 0)) : null);
  const compared = items.filter((item) => item.has_actual);
  const budget = sum(compared.map((item) => item.budget));
  const actual = round(compared.reduce((total, item) => total + item.actual, 0));
  const variance = budget === null ? null : round(actual - budget);
  return {
    name: "Total", budget, actual, variance, ratio: budget ? (variance ?? 0) / budget : null,
    estimated: items.some((item) => item.estimated), has_actual: compared.length > 0, full_budget: sum(items.map((item) => item.budget)),
  };
}
