// Geometry for editing evidence marks, in page-image pixels.
import type { Highlight, Label, Mark, ViewPage } from "./types";

export const MIN_SIZE = 12;
// Same fonts as the server: Arial Bold, Microsoft JhengHei Bold for Chinese.
export const LABEL_FONT = "Arial, 'Microsoft JhengHei', sans-serif";

export type Handle = "nw" | "ne" | "sw" | "se";

export interface Bounds {
  width: number;
  height: number;
}

const clamp = (value: number, low: number, high: number) =>
  Math.min(Math.max(value, low), Math.max(low, high));

/** Approximate size of a red label (the server measures the real font). */
export function labelSize(label: Pick<Label, "text" | "fontSize">): { w: number; h: number } {
  // Bold Latin glyphs are about 0.6 em wide; Chinese characters a full em.
  const ems = [...label.text].reduce((sum, char) => sum + (/[㐀-鿿]/.test(char) ? 1 : 0.6), 0);
  return { w: ems * label.fontSize, h: label.fontSize * 1.1 };
}

export function markBox(mark: Mark): { x: number; y: number; w: number; h: number } {
  if (mark.type === "highlight") return { x: mark.x, y: mark.y, w: mark.w, h: mark.h };
  const { w, h } = labelSize(mark);
  return { x: mark.x, y: mark.y, w, h };
}

/** Move a mark by (dx, dy), keeping it on the page. */
export function moveMark<T extends Mark>(mark: T, dx: number, dy: number, page: Bounds): T {
  const box = markBox(mark);
  return {
    ...mark,
    x: clamp(mark.x + dx, 0, page.width - box.w),
    y: clamp(mark.y + dy, 0, page.height - box.h),
  };
}

/** Drag one corner of a highlight; the opposite corner stays put. */
export function resizeHighlight(mark: Highlight, handle: Handle, dx: number, dy: number, page: Bounds): Highlight {
  let left = mark.x;
  let top = mark.y;
  let right = mark.x + mark.w;
  let bottom = mark.y + mark.h;
  if (handle.includes("w")) left = clamp(left + dx, 0, right - MIN_SIZE);
  if (handle.includes("e")) right = clamp(right + dx, left + MIN_SIZE, page.width);
  if (handle.includes("n")) top = clamp(top + dy, 0, bottom - MIN_SIZE);
  if (handle.includes("s")) bottom = clamp(bottom + dy, top + MIN_SIZE, page.height);
  return { ...mark, x: left, y: top, w: right - left, h: bottom - top };
}

/** Grow or shrink a label's text from its bottom-right corner. */
export function resizeLabel(mark: Label, dy: number): Label {
  return { ...mark, fontSize: clamp(mark.fontSize + dy, 12, 200) };
}

let counter = 0;
export function newId(): string {
  counter += 1;
  return `m${Date.now().toString(36)}${counter}`;
}

export function newHighlight(page: Bounds, around?: { x: number; y: number }): Highlight {
  const w = page.width * 0.25;
  const h = page.height * 0.02;
  const centerX = around?.x ?? page.width / 2;
  const centerY = around?.y ?? page.height / 2;
  return {
    id: newId(),
    type: "highlight",
    x: clamp(centerX - w / 2, 0, page.width - w),
    y: clamp(centerY - h / 2, 0, page.height - h),
    w,
    h,
  };
}

export function newLabel(page: Bounds, text: string, around?: { x: number; y: number }): Label {
  const fontSize = Math.round(page.width * 0.027);
  const { w, h } = labelSize({ text, fontSize });
  return {
    id: newId(),
    type: "label",
    text,
    fontSize,
    x: clamp((around?.x ?? page.width / 2) - w / 2, 0, page.width - w),
    y: clamp((around?.y ?? page.height / 2) - h / 2, 0, page.height - h),
  };
}

export const EVIDENCE_LABELS = ["Billing period", "Grey Electricity Cost", "Electricity Usage (kWh)"];

/** Whether a mark sits on the given page of the review (the bill page has none set). */
export function isOnPage(mark: Mark, page: ViewPage): boolean {
  return page.bill ? mark.page === undefined || mark.page === page.page : mark.page === page.page;
}
