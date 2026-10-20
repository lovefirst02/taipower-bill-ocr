import { describe, expect, it } from "vitest";
import { periodMonth } from "./periods";

describe("periodMonth", () => {
  it("reads ROC and western dates", () => {
    expect(periodMonth("115/08/01~115/08/31")).toBe("2026-08");
    expect(periodMonth("2026/08/01~2026/08/31")).toBe("2026-08");
  });

  it("takes the month with most of the days", () => {
    expect(periodMonth("115/07/10~115/08/09")).toBe("2026-07");
    expect(periodMonth("115/07/25~115/08/24")).toBe("2026-08");
  });

  it("gives nothing for what cannot be read", () => {
    expect(periodMonth(null)).toBeNull();
    expect(periodMonth("period ?")).toBeNull();
  });
});
