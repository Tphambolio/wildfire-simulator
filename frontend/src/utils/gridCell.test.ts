import { describe, expect, it } from "vitest";
import { cellAreaWeight } from "./gridCell";

describe("cellAreaWeight", () => {
  it("is 1 for the 50 m grid and when the frame has no grid", () => {
    expect(cellAreaWeight({ cell_m: 50, wui_window: false, reason: "no_native_grid", note: "50 m cells" })).toBe(1);
    expect(cellAreaWeight(undefined)).toBe(1);
    expect(cellAreaWeight(null)).toBe(1);
  });

  it("scales 20 m cells by their area (0.16 of a 50 m cell)", () => {
    expect(cellAreaWeight({ cell_m: 20, wui_window: true, reason: "used", note: "" })).toBeCloseTo(0.16, 6);
  });

  it("ignores invalid sizes", () => {
    expect(cellAreaWeight({ cell_m: 0, wui_window: false, reason: "", note: "" })).toBe(1);
    expect(cellAreaWeight({ cell_m: Number.NaN, wui_window: false, reason: "", note: "" })).toBe(1);
  });
});
