import { describe, expect, it } from "vitest";
import { parse } from "./components/FieldForm";

const numeric = { key: "基本電費", label: "Basic", editable: true, numeric: true };
const text = { key: "Custom No. 電號", label: "No.", editable: true, numeric: false };

describe("parse", () => {
  it("reads decimals, including while they are being typed", () => {
    expect(parse(numeric, "12.5")).toBe(12.5);
    expect(parse(numeric, "12.")).toBe(12);
    expect(parse(numeric, "0.05")).toBe(0.05);
    expect(parse(numeric, "-25000.75")).toBe(-25000.75);
    expect(parse(numeric, "1,234.5")).toBe(1234.5);
  });

  it("keeps text that is not a number, so it can be flagged", () => {
    expect(parse(numeric, "-")).toBe("-");
    expect(parse(numeric, "")).toBeNull();
    expect(parse(text, "00-12")).toBe("00-12");
  });
});
