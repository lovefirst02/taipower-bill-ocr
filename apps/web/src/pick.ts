// Picking a value on the page image: the OCR text under a click or inside a
// dragged box becomes the field's value and its evidence highlight.
import type { OcrLine } from "./types";

export const PERIOD = "Billing Period 計費期間";
export const USAGE = "用電度數";
export const PRETAX = "稅前應繳總金額";
export const TOTAL = "應繳總金額";

/** The red label written beside a picked value's highlight (none for other columns). */
export const PICK_LABELS: Record<string, string> = {
  [PERIOD]: "Billing period",
  [USAGE]: "Electricity Usage (kWh)",
  [PRETAX]: "Grey Electricity Cost",
};

export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

// A click moves less than this (image pixels); more is a dragged box.
const CLICK = 6;

export function isClick(box: Box): boolean {
  return box.w < CLICK && box.h < CLICK;
}

function overlap(a: Box, b: Box): number {
  const w = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
  const h = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
  return w > 0 && h > 0 ? w * h : 0;
}

const lineBox = (line: OcrLine): Box => ({ x: line.x, y: line.y, w: line.w, h: line.h });

/** OCR lines under a click (a little slack) or mostly inside a dragged box, in reading order. */
export function linesAt(lines: OcrLine[], box: Box): OcrLine[] {
  const slack = 4;
  const area = isClick(box) ? { x: box.x - slack, y: box.y - slack, w: box.w + slack * 2, h: box.h + slack * 2 } : box;
  return lines
    .filter((line) => {
      const inside = overlap(lineBox(line), area);
      return isClick(box) ? inside > 0 : inside >= line.w * line.h * 0.4;
    })
    .sort((a, b) => (Math.abs(a.y - b.y) < Math.min(a.h, b.h) / 2 ? a.x - b.x : a.y - b.y));
}

export function union(boxes: Box[]): Box {
  const left = Math.min(...boxes.map((box) => box.x));
  const top = Math.min(...boxes.map((box) => box.y));
  const right = Math.max(...boxes.map((box) => box.x + box.w));
  const bottom = Math.max(...boxes.map((box) => box.y + box.h));
  return { x: left, y: top, w: right - left, h: bottom - top };
}

export function linesBox(lines: OcrLine[]): Box {
  return union(lines.map(lineBox));
}

const DATE = /(\d{2,4})\s*[/.\-年]\s*(\d{1,2})\s*[/.\-月]\s*(\d{1,2})\s*日?/g;

/** "113/01/01~113/01/31" from two dates in the text (ROC or western years), as the server writes it. */
export function parsePeriod(text: string): string | null {
  const dates = [...text.matchAll(DATE)]
    .map((match) => match.slice(1, 4).map(Number))
    .filter(([year, month, day]) => month >= 1 && month <= 12 && day >= 1 && day <= 31 && (year >= 1000 || (year >= 50 && year <= 200)));
  if (dates.length < 2) return null;
  const pad = (value: number) => String(value).padStart(2, "0");
  return dates.slice(0, 2).map(([year, month, day]) => `${year}/${pad(month)}/${pad(day)}`).join("~");
}

/** Numbers in the text (dates left out), with where each starts. */
export function numbersIn(text: string): { value: number; at: number; length: number }[] {
  const blanked = text.replace(DATE, (match) => " ".repeat(match.length));
  const found = [];
  for (const match of blanked.matchAll(/-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?/g)) {
    const value = Number(match[0].replace(/,/g, ""));
    if (Number.isFinite(value)) found.push({ value, at: match.index ?? 0, length: match[0].length });
  }
  return found;
}

/**
 * The value for ``column`` in the picked text. With several numbers on a
 * clicked line, the one under the click (by its place in the text) is taken.
 */
export function valueFor(column: string, numeric: boolean, picked: OcrLine[], box: Box): string | number | null {
  const text = picked.map((line) => line.text).join(" ");
  if (!text.trim()) return null;
  if (column === PERIOD) return parsePeriod(text);
  if (!numeric) return text.trim();
  const found = numbersIn(text);
  if (!found.length) return null;
  if (found.length === 1 || !isClick(box) || picked.length !== 1) return found[0].value;
  const line = picked[0];
  const at = ((box.x - line.x) / Math.max(line.w, 1)) * line.text.length;
  return found.reduce((best, item) =>
    Math.abs(item.at + item.length / 2 - at) < Math.abs(best.at + best.length / 2 - at) ? item : best,
  ).value;
}

/**
 * Where a red label covers the least printed text, close to its highlight:
 * beside it, or one to three lines above or below (as the server places them).
 */
export function placeLabel(box: Box, size: { w: number; h: number }, page: { width: number; height: number }, obstacles: Box[]): { x: number; y: number } {
  const gap = 12;
  const step = size.h + 8;
  const columns = [box.x + box.w + 16, box.x, box.x + box.w - size.w, box.x - size.w - 16];
  const rows = [
    box.y + (box.h - size.h) / 2,
    ...[0, 1, 2].map((n) => box.y - gap - size.h - step * n),
    ...[0, 1, 2].map((n) => box.y + box.h + gap + step * n),
  ];
  let best: { hidden: number; distance: number; x: number; y: number } | null = null;
  for (const x of columns) {
    for (const y of rows) {
      if (x < 0 || y < 0 || x + size.w > page.width || y + size.h > page.height) continue;
      const spot = { x, y, w: size.w, h: size.h };
      if (overlap(spot, box)) continue;
      const hidden = obstacles.reduce((sum, obstacle) => sum + overlap(spot, obstacle), 0);
      const distance = Math.max(0, x - (box.x + box.w), box.x - (x + size.w)) + Math.max(0, y - (box.y + box.h), box.y - (y + size.h));
      if (!best || hidden < best.hidden || (hidden === best.hidden && distance < best.distance)) best = { hidden, distance, x, y };
    }
  }
  return best ? { x: best.x, y: best.y } : { x: Math.max(0, Math.min(box.x, page.width - size.w)), y: Math.max(0, box.y - size.h - gap) };
}

export function lineBoxes(lines: OcrLine[]): Box[] {
  return lines.map(lineBox);
}
