// Checking green power documents as the server does, while they are edited.
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

/** kWh × rate rounded half up to the NT$, without floating-point error. */
export function times(kwh: number, rate: number): number {
  const product = Math.round(kwh * KWH) * Math.round(rate * RATE);
  const scale = KWH * RATE;
  return Math.sign(product) * Math.floor((Math.abs(product) + scale / 2) / scale);
}

export interface LineCheck {
  expected: number | null;
  difference: number | null;
  status: "ok" | "check" | "incomplete";
}

/** A retailer's statement line: the charge vs kWh × (base + transfer + certificate rates). */
export function checkLine(line: Partial<Record<keyof GreenLine, Cell>>, tolerance: number): LineCheck {
  const kwh = cellNumber(line.kwh);
  const cost = cellNumber(line.cost);
  const base = cellNumber(line.base);
  const rates = base === null && cellNumber(line.unit_price) !== null
    ? [cellNumber(line.unit_price)]
    : [base, cellNumber(line.transfer_rate), cellNumber(line.certificate_rate)];
  const price = rates.reduce<number>((sum, rate) => sum + (rate ?? 0), 0);
  if (kwh === null || cost === null || price === 0) return { expected: null, difference: null, status: "incomplete" };
  const expected = times(kwh, Math.round(price * RATE) / RATE);
  const difference = Math.round((cost - expected) * 100) / 100;
  return { expected, difference, status: Math.abs(difference) <= tolerance ? "ok" : "check" };
}

/** A row of Taipower's transfer fee list: the fee is exactly kWh × rate, rounded. */
export function checkRow(row: Record<string, Cell>): LineCheck {
  const kwh = cellNumber(row.kwh);
  const rate = cellNumber(row.rate);
  const fee = cellNumber(row.fee);
  if (kwh === null || rate === null || fee === null) return { expected: null, difference: null, status: "incomplete" };
  const expected = times(kwh, rate);
  return { expected, difference: fee - expected, status: fee === expected ? "ok" : "check" };
}

export interface NoticeCheck extends LineCheck {
  field: string;
  label: string;
}

/** A purchase notice's formula: A×B, (A−C)×D, untaxed = A×B + (A−C)×D + E + F, tax 5%, total. */
export function checkNotice(values: Record<string, Cell>): NoticeCheck[] {
  const get = (name: string) => cellNumber(values[name]);
  const [a, b, c, d, e, f] = ["purchase_rate", "kwh", "fit_rate", "surplus_kwh", "supply_fees", "certificate_fee"].map(get);
  const check = (field: string, label: string, expected: number | null): NoticeCheck => {
    const printed = get(field);
    if (expected === null || printed === null) return { field, label, expected: null, difference: null, status: "incomplete" };
    return { field, label, expected, difference: printed - expected, status: printed === expected ? "ok" : "check" };
  };
  const purchase = a !== null && b !== null ? times(b, a) : null;
  const surplus = a !== null && c !== null && d !== null ? times(d, Math.round((a - c) * RATE) / RATE) : null;
  const parts = [get("purchase_charge"), get("surplus_charge"), e, f];
  const untaxed = parts.every((part) => part !== null) ? parts.reduce<number>((sum, part) => sum + (part ?? 0), 0) : null;
  const printedUntaxed = get("untaxed");
  const tax = printedUntaxed !== null ? times(printedUntaxed, 0.05) : null;
  const printedTax = get("tax");
  return [
    check("purchase_charge", "A×B", purchase),
    check("surplus_charge", "(A−C)×D", surplus),
    check("untaxed", "A×B + (A−C)×D + E + F", untaxed),
    check("tax", "untaxed × 5%", tax),
    check("total", "untaxed + tax", printedUntaxed !== null && printedTax !== null ? printedUntaxed + printedTax : null),
  ];
}

/** Surcharges such as 餘電加收 are not transferred green power. */
export function isGreen(line: { energy?: Cell }): boolean {
  return !String(line.energy ?? "").includes("餘電");
}

/** Digits only: 00-12-3456-78-9 and 00123456789 are the same meter. */
export function meterNumber(value: Cell): string {
  return String(value ?? "").replace(/\D/g, "");
}
