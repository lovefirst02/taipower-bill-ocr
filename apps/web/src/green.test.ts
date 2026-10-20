import { describe, expect, it } from "vitest";
import { cellNumber, checkLine, isGreen } from "./green";

const line = (kwh: string | number, cost: string | number, base = "5.8000", transfer = "0.30520") =>
  ({ kwh, cost, base, transfer_rate: transfer, certificate_rate: "0.00000" });

describe("checkLine", () => {
  it("matches the server's check of a statement", () => {
    expect(checkLine(line(36145, 220671), 3)).toEqual({ expected: 220672, difference: -1, status: "ok" });
    expect(checkLine(line("242,478", "1,480,377"), 3)).toEqual({ expected: 1480377, difference: 0, status: "ok" });
    expect(checkLine(line(120208, 745913, "5.9000"), 3).difference).toBe(-2);
  });

  it("points out a charge that does not fit", () => {
    expect(checkLine(line(36145, 230000), 3).status).toBe("check");
    expect(checkLine({ kwh: null, cost: 1 }, 3).status).toBe("incomplete");
  });

  it("uses the printed price when there is no base price", () => {
    expect(checkLine({ kwh: 78, cost: 237, unit_price: "3.0331" }, 0).expected).toBe(237);
  });
});

describe("cells", () => {
  it("reads typed numbers", () => {
    expect(cellNumber("1,480,377")).toBe(1480377);
    expect(cellNumber("abc")).toBeNull();
    expect(isGreen({ energy: "餘電加收" })).toBe(false);
    expect(isGreen({ energy: "風能" })).toBe(true);
  });
});
