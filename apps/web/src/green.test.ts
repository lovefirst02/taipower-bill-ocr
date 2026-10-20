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

describe("Taipower's list and purchase notices", () => {
  it("checks a fee exactly", async () => {
    const { checkRow } = await import("./green");
    expect(checkRow({ kwh: "26,031", rate: "0.0778", fee: "2,025" }).status).toBe("ok");
    expect(checkRow({ kwh: 170322, rate: 0.1788, fee: 30454 }).status).toBe("ok");
    expect(checkRow({ kwh: 240000, rate: 0.9778, fee: 18672 }).status).toBe("check");
  });

  it("checks a notice's formula", async () => {
    const { checkNotice } = await import("./green");
    const values = {
      purchase_rate: 5.6, kwh: 673816, purchase_charge: 3773370, fit_rate: 5.2428, surplus_kwh: 186901,
      surplus_charge: 66761, supply_fees: 205610, certificate_fee: 1743, untaxed: 4047484, tax: 202374, total: 4249858,
    };
    expect(checkNotice(values).map((check) => check.status)).toEqual(["ok", "ok", "ok", "ok", "ok"]);
    expect(checkNotice({ ...values, supply_fees: 205600 })[2].difference).toBe(10);
  });
});
