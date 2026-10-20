// Checking a green power statement line, as the server does: the charge vs
// kWh × (base price + transfer rate + certificate rate), rounded half up.
import type { GreenLine } from "./types";

export type Cell = string | number | null | undefined;

/** A number typed or read into a cell ("1,480,377" too); null when there is none. */
export function cellNumber(value: Cell): number | null {
  if (value === null || value === undefined) return null;
  const text = String(value).replace(/[,\s]/g, "");
  if (!/^-?\d+(\.\d+)?$/.test(text)) return null;
  return Number(text);
}

// Exact integer arithmetic: rates to 6 decimals, kWh to 3.
const RATE = 1_000_000;
const KWH = 1_000;

export interface LineCheck {
  expected: number | null;
  difference: number | null;
  status: "ok" | "check" | "incomplete";
}

export function checkLine(line: Partial<Record<keyof GreenLine, Cell>>, tolerance: number): LineCheck {
  const kwh = cellNumber(line.kwh);
  const cost = cellNumber(line.cost);
  const base = cellNumber(line.base);
  const rates = base === null && cellNumber(line.unit_price) !== null
    ? [cellNumber(line.unit_price)]
    : [base, cellNumber(line.transfer_rate), cellNumber(line.certificate_rate)];
  const price = rates.reduce<number>((sum, rate) => sum + Math.round((rate ?? 0) * RATE), 0);
  if (kwh === null || cost === null || price === 0) return { expected: null, difference: null, status: "incomplete" };
  const product = Math.round(kwh * KWH) * price; // in units of 1 / (KWH * RATE) NT$
  const scale = KWH * RATE;
  const expected = Math.sign(product) * Math.floor((Math.abs(product) + scale / 2) / scale);
  const difference = Math.round((cost - expected) * 100) / 100;
  return { expected, difference, status: Math.abs(difference) <= tolerance ? "ok" : "check" };
}

/** Surcharges such as 餘電加收 are not transferred green power. */
export function isGreen(line: { energy?: Cell }): boolean {
  return !String(line.energy ?? "").includes("餘電");
}
