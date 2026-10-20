import { describe, expect, it } from "vitest";
import { energies, greenShare, groupBy, total, unitPrice } from "./mix";
import type { MixRow } from "./types";

const row = (values: Partial<MixRow>): MixRow => ({
  month: "2026-08", meter: "1", document: null, site: "A", site_code: null, grey_kwh: null, green_kwh: {},
  green_total: 0, total_kwh: null, green_share: null, grey_cost: null, green_cost: null, green_source: null, ...values,
});

describe("dashboard totals", () => {
  const rows = [
    row({ meter: "1", grey_kwh: 15000, green_kwh: { 太陽能: 5000 }, green_total: 5000, total_kwh: 20000, grey_cost: 60000, green_cost: 28000.55 }),
    row({ meter: "2", green_kwh: { 風力: 1000, 未分類: 10 }, green_total: 1010, green_cost: 6000.45 }),
    row({ meter: "3", site: "B", month: "2026-09", grey_kwh: 300, total_kwh: 300, grey_cost: 1200 }),
  ];

  it("adds grey and green power and their costs", () => {
    const sum = total(rows);
    expect(sum.grey_kwh).toBe(15300);
    expect(sum.green_kwh).toEqual({ 太陽能: 5000, 風力: 1000, 未分類: 10 });
    expect(sum.green_cost).toBe(34001);
    expect(sum.without_bill).toBe(1);
  });

  it("counts the green share only where the grey power is known", () => {
    expect(greenShare(total(rows))).toBeCloseTo(5000 / 20300);
    expect(greenShare(total([rows[1]]))).toBeNull();
  });

  it("works out unit prices", () => {
    const sum = total(rows);
    expect(unitPrice(sum.grey_cost, sum.grey_kwh_costed)).toBe(4);
    expect(unitPrice(1, 0)).toBeNull();
  });

  it("groups by month or site and orders energies", () => {
    expect(groupBy(rows, (item) => item.month).map(([month]) => month)).toEqual(["2026-08", "2026-09"]);
    expect(groupBy(rows, (item) => item.site ?? "")[1][1].grey_kwh).toBe(300);
    expect(energies(rows)).toEqual(["太陽能", "風力", "未分類"]);
  });
});
