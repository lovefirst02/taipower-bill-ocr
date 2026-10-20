import { describe, expect, it } from "vitest";
import { overview, status } from "./budgetOverview";
import type { EnergyPlan, Plan, PlanEnergy, PlanMonth, SitePlan } from "./types";

const month = (index: number, values: Partial<PlanMonth>): PlanMonth => ({
  month: `2026-${String(index + 1).padStart(2, "0")}`, kwh: null, kwh_source: null, rate: null, rate_source: null, estimated_cost: 100,
  planned: 100, actual_kwh: null, actual_cost: null, actual_rate: null, has_actual: false, adopted_cost: 100, prior_carry: 0, available: 100,
  balance: 0, forecast_kwh: null, forecast_cost: 100, forecast_source: "estimate", adjusted_budget: null, ...values,
});

const energy = (key: PlanEnergy, annual: number, actualMonths: number, actual = 120): EnergyPlan => {
  const months = Array.from({ length: 12 }, (_, index) =>
    month(index, index < actualMonths ? { has_actual: true, actual_cost: actual, forecast_cost: actual } : {}));
  const spent = actualMonths * actual;
  const yearEnd = months.reduce((sum, item) => sum + (item.forecast_cost ?? 0), 0);
  return {
    energy: key, annual_input: annual, annual, method: "approved", allocation: "estimate", adjustment_pct: 0, months, actual_cost: spent,
    utilization: spent / annual, forecast: { months_closed: actualMonths, trend: null, price: null, year_end: yearEnd, variance: yearEnd - annual, remaining: annual - spent },
  };
};

const site = (name: string, entity: string | null, grey: EnergyPlan, solar: EnergyPlan, wind: EnergyPlan): SitePlan => ({
  site: name, entity, total_budget: null, energies_total: grey.annual + solar.annual + wind.annual, allocation: null,
  energies: { grey, solar, wind },
});

const plan: Plan = {
  year: 2026, can_edit: true, sites: [
    site("TNF", "TW21", energy("grey", 1200, 3), energy("solar", 1200, 0), energy("wind", 0, 0)),
    site("TTT", "TW01", energy("grey", 2000, 3, 100), energy("solar", 0, 0), energy("wind", 0, 0)),
  ],
};

describe("budget overview", () => {
  it("tells how the year is going", () => {
    expect(status(1000, 900)).toBe("on track");
    expect(status(1000, 1040)).toBe("at risk");
    expect(status(1000, 1100)).toBe("over");
    expect(status(0, 100)).toBe("none");
  });

  it("adds up the sites and energies chosen", () => {
    const all = overview(plan, "all", "all");
    expect([all.total.budget, all.total.actual]).toEqual([4400, 3 * 120 + 3 * 100]);
    expect(all.lastClosed).toBe(2);
    // The actual line stops at the last month with documents; the forecast goes on.
    expect(all.months[2].cumulativeActual).toBe(660);
    expect(all.months[3].cumulativeActual).toBeNull();
    expect(all.months[11].cumulativeForecast).toBe(all.total.forecast);
    // The site most over budget comes first.
    expect(all.bySite.map((item) => item.name)).toEqual(["TTT", "TNF"].sort((a, b) =>
      all.bySite.find((item) => item.name === b)!.variance - all.bySite.find((item) => item.name === a)!.variance));
  });

  it("follows the entity and energy chosen", () => {
    const tw21Grey = overview(plan, "TW21", "grey");
    expect([tw21Grey.total.budget, tw21Grey.bySite.length, tw21Grey.byEnergy.length]).toEqual([1200, 1, 1]);
    expect(tw21Grey.total.forecast).toBe(3 * 120 + 9 * 100);
    expect(tw21Grey.total.status).toBe("at risk");  // 1,260 against 1,200: 5% over
    const ttt = overview(plan, "all", "all", "TTT");
    expect([ttt.bySite.map((item) => item.name), ttt.total.budget]).toEqual([["TTT"], 2000]);
  });
});
