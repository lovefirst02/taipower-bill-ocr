import { describe, expect, it } from "vitest";
import { compare, METRICS, overall } from "./budget";
import type { BudgetRow, MixRow } from "./types";

const actual = (values: Partial<MixRow>): MixRow => ({
  month: "2026-08", meter: "1", document: null, site: "彰化廠", site_code: null, entity: "TW21", grey_kwh: null, green_kwh: {},
  green_total: 0, total_kwh: null, green_share: null, grey_cost: null, green_cost: null, green_cost_by_energy: {}, green_source: null, ...values,
});
const budget = (values: Partial<BudgetRow>): BudgetRow => ({
  month: "2026-08", entity: "TW21", site: "彰化廠", grey_kwh: null, grey_cost: null, solar_kwh: null, solar_cost: null,
  wind_kwh: null, wind_cost: null, total_kwh: null, total_cost: null, estimated: [], ...values,
});
const metric = (key: string) => METRICS.find((item) => item.key === key)!;

describe("budget against actual", () => {
  const actuals = [
    actual({ meter: "1", grey_kwh: 300000, grey_cost: 1600000, green_kwh: { 太陽能: 575841 }, green_total: 575841, green_cost: 3459211, green_cost_by_energy: { 太陽能: 3459211 } }),
    actual({ meter: "2", grey_kwh: 160056, grey_cost: 930078 }),
    actual({ meter: "3", month: "2026-09", grey_kwh: 1000, grey_cost: 5000 }),
  ];
  const budgets = [
    budget({ grey_kwh: 450000, grey_cost: 2500000, solar_kwh: 575000, solar_cost: 3400000, total_cost: 5900000, estimated: ["solar_cost"] }),
    budget({ month: "2026-10", grey_cost: 100 }),
  ];

  it("adds actuals and budgets up by group", () => {
    const byMonth = compare(metric("grey_cost"), actuals, budgets, (row) => row.month);
    expect(byMonth.map((item) => [item.name, item.budget, item.actual, item.variance])).toEqual([
      ["2026-08", 2500000, 2530078, 30078],
      ["2026-09", null, 5000, null],
      ["2026-10", 100, 0, null],  // no documents yet: not compared
    ]);
    expect(byMonth[0].ratio).toBeCloseTo(30078 / 2500000);
  });

  it("marks budgets estimated from a contract", () => {
    const [august] = compare(metric("solar_cost"), actuals, budgets, (row) => row.month);
    expect(august.variance).toBe(59211);
    expect(august.estimated).toBe(true);
    expect(compare(metric("grey_cost"), actuals, budgets, (row) => row.month)[0].estimated).toBe(false);
  });

  it("totals the months with documents, and the whole budget", () => {
    const total = overall(compare(metric("grey_cost"), actuals, budgets, (row) => row.month));
    expect([total.budget, total.actual, total.variance, total.full_budget]).toEqual([2500000, 2535078, 35078, 2500100]);
  });
});
