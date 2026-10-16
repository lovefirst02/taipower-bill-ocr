import { describe, expect, it } from "vitest";
import { isClick, linesAt, parsePeriod, PERIOD, placeLabel, USAGE, valueFor } from "./pick";

const line = (text: string, x: number, y: number, w: number, h = 30) => ({ text, x, y, w, h });
const lines = [
  line("計費期間：113/01/01 ~ 113/01/31", 100, 140, 520),
  line("甲公司", 100, 380, 100),
  line("15,000", 390, 380, 100),
  line("45,000", 590, 380, 100),
];

describe("picking a value", () => {
  it("reads the number under a click", () => {
    const click = { x: 420, y: 395, w: 0, h: 0 };
    expect(isClick(click)).toBe(true);
    const picked = linesAt(lines, click);
    expect(picked.map((item) => item.text)).toEqual(["15,000"]);
    expect(valueFor(USAGE, true, picked, click)).toBe(15000);
  });

  it("reads the period from a dragged box", () => {
    const box = { x: 90, y: 130, w: 560, h: 50 };
    expect(valueFor(PERIOD, false, linesAt(lines, box), box)).toBe("113/01/01~113/01/31");
  });

  it("takes the number nearest the click when a line has several", () => {
    const row = [line("本期 31 15000 483.87", 100, 100, 400)];
    const click = { x: 330, y: 110, w: 0, h: 0 }; // over "15000"
    expect(valueFor(USAGE, true, linesAt(row, click), click)).toBe(15000);
  });

  it("finds nothing on an empty spot", () => {
    const click = { x: 900, y: 900, w: 0, h: 0 };
    expect(linesAt(lines, click)).toEqual([]);
    expect(valueFor(USAGE, true, [], click)).toBeNull();
  });
});

describe("parsePeriod", () => {
  it("reads ROC and western dates", () => {
    expect(parsePeriod("113年2月1日至113年2月29日")).toBe("113/02/01~113/02/29");
    expect(parsePeriod("2024-03-01 - 2024-03-31")).toBe("2024/03/01~2024/03/31");
    expect(parsePeriod("113/01/01")).toBeNull();
  });
});

describe("placeLabel", () => {
  it("avoids text on the value's row", () => {
    const value = { x: 390, y: 330, w: 100, h: 30 };
    const row = [{ x: 590, y: 330, w: 100, h: 30 }, { x: 780, y: 330, w: 80, h: 30 }];
    const spot = placeLabel(value, { w: 300, h: 40 }, { width: 1654, height: 2339 }, row);
    const covers = row.some((box) => spot.x < box.x + box.w && spot.x + 300 > box.x && spot.y < box.y + box.h && spot.y + 40 > box.y);
    expect(covers).toBe(false);
  });
});
