import { describe, expect, it } from "vitest";
import { moveMark, newHighlight, newLabel, resizeHighlight, resizeLabel } from "./marks";
import type { Highlight, Label } from "./types";

const page = { width: 1654, height: 2339 };
const highlight: Highlight = { id: "h", type: "highlight", x: 707, y: 888, w: 489, h: 42 };
const label: Label = { id: "l", type: "label", x: 1212, y: 888, text: "Grey Electricity Cost", fontSize: 46 };

describe("moving marks", () => {
  it("moves by the drag distance", () => {
    expect(moveMark(highlight, 10, -5, page)).toMatchObject({ x: 717, y: 883, w: 489, h: 42 });
  });

  it("keeps marks on the page", () => {
    expect(moveMark(highlight, -5000, 99999, page)).toMatchObject({ x: 0, y: page.height - 42 });
    const moved = moveMark(label, 5000, 0, page);
    expect(moved.x).toBeLessThan(page.width);
  });
});

describe("resizing highlights", () => {
  it("drags the bottom-right corner", () => {
    expect(resizeHighlight(highlight, "se", 20, 8, page)).toMatchObject({ x: 707, y: 888, w: 509, h: 50 });
  });

  it("drags the top-left corner and keeps the opposite corner", () => {
    const resized = resizeHighlight(highlight, "nw", -7, -8, page);
    expect(resized).toMatchObject({ x: 700, y: 880 });
    expect(resized.x + resized.w).toBe(707 + 489);
    expect(resized.y + resized.h).toBe(888 + 42);
  });

  it("never collapses below the minimum size", () => {
    const resized = resizeHighlight(highlight, "se", -1000, -1000, page);
    expect(resized.w).toBe(12);
    expect(resized.h).toBe(12);
  });
});

describe("labels and new marks", () => {
  it("changes the label font size within limits", () => {
    expect(resizeLabel(label, 4).fontSize).toBe(50);
    expect(resizeLabel(label, -500).fontSize).toBe(12);
  });

  it("creates new marks inside the page", () => {
    const created = newHighlight(page, { x: 0, y: 0 });
    expect(created.x).toBe(0);
    expect(created.y).toBe(0);
    const text = newLabel(page, "Billing period", { x: page.width, y: page.height });
    expect(text.x + text.text.length * text.fontSize * 0.5).toBeLessThanOrEqual(page.width);
  });
});
