// Adding up grey and green power for the dashboard.
import type { MixRow } from "./types";

export const SOLAR = "太陽能";
export const WIND = "風力";
export const UNCLASSIFIED = "未分類";

export interface MixTotal {
  grey_kwh: number;
  green_kwh: Record<string, number>;
  green_total: number;
  /** kWh of rows whose grey power is known: the base of the green share. */
  total_kwh: number;
  green_in_total: number;
  grey_cost: number;
  green_cost: number;
  /** For unit prices: kWh of rows that have a cost. */
  grey_kwh_costed: number;
  green_kwh_costed: number;
  /** Rows with green power but no bill (their grey power is unknown). */
  without_bill: number;
  /** Rows whose grey power is known: none means no bill at all. */
  with_bill: number;
}

const empty = (): MixTotal => ({
  grey_kwh: 0, green_kwh: {}, green_total: 0, total_kwh: 0, green_in_total: 0,
  grey_cost: 0, green_cost: 0, grey_kwh_costed: 0, green_kwh_costed: 0, without_bill: 0, with_bill: 0,
});

/** Integers stay exact; costs shared by kWh keep two decimals. */
const add = (a: number, b: number) => Math.round((a + b) * 100) / 100;

export function total(rows: MixRow[]): MixTotal {
  const sum = empty();
  for (const row of rows) {
    sum.grey_kwh = add(sum.grey_kwh, row.grey_kwh ?? 0);
    if (row.grey_kwh !== null) sum.with_bill += 1;
    for (const [energy, kwh] of Object.entries(row.green_kwh)) sum.green_kwh[energy] = add(sum.green_kwh[energy] ?? 0, kwh);
    sum.green_total = add(sum.green_total, row.green_total);
    if (row.total_kwh !== null) {
      sum.total_kwh = add(sum.total_kwh, row.total_kwh);
      sum.green_in_total = add(sum.green_in_total, row.green_total);
    } else if (row.green_total) {
      sum.without_bill += 1;
    }
    if (row.grey_cost !== null) {
      sum.grey_cost = add(sum.grey_cost, row.grey_cost);
      sum.grey_kwh_costed = add(sum.grey_kwh_costed, row.grey_kwh ?? 0);
    }
    if (row.green_cost !== null) {
      sum.green_cost = add(sum.green_cost, row.green_cost);
      sum.green_kwh_costed = add(sum.green_kwh_costed, row.green_total);
    }
  }
  return sum;
}

/** The green share of the usage whose grey power is known; null without any. */
export function greenShare(sum: MixTotal): number | null {
  return sum.total_kwh ? sum.green_in_total / sum.total_kwh : null;
}

/** NT$ per kWh; null when there is nothing to divide. */
export function unitPrice(cost: number, kwh: number): number | null {
  return kwh ? Math.round((cost / kwh) * 10000) / 10000 : null;
}

/** Rows grouped by a key, in key order. */
export function groupBy(rows: MixRow[], key: (row: MixRow) => string): [string, MixTotal][] {
  const groups = new Map<string, MixRow[]>();
  for (const row of rows) groups.set(key(row), [...(groups.get(key(row)) ?? []), row]);
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([name, items]) => [name, total(items)]);
}

/** 太陽能, 風力, then other energies, 未分類 last. */
export function energies(rows: MixRow[]): string[] {
  const found = new Set(rows.flatMap((row) => Object.keys(row.green_kwh)));
  const rest = [...found].filter((name) => ![SOLAR, WIND, UNCLASSIFIED].includes(name)).sort();
  return [...[SOLAR, WIND].filter((name) => found.has(name)), ...rest, ...(found.has(UNCLASSIFIED) ? [UNCLASSIFIED] : [])];
}
