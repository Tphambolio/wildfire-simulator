import { describe, expect, it } from "vitest";
import { fwiClass, fwiClassColor, fwiClassTextColor } from "./fwiClass";
import { contrastRatio } from "./contrast";

// CWFIS national FWI map intervals: 0-5 Low, 6-15 Moderate, 16-22 High, 23-29 Very High,
// 30+ Extreme. FWI is continuous, so the class limits fall at the half-integers 5.5, 15.5,
// 22.5 and 29.5 (a value rounds to the integer class it displays as).
describe("fwiClass", () => {
  it.each([
    [0, "Low"],
    [5, "Low"],
    [5.49, "Low"],
    [5.5, "Moderate"],
    [6, "Moderate"],
    [15.49, "Moderate"],
    [15.5, "High"],
    [22.49, "High"],
    [22.5, "Very High"],
    [29.49, "Very High"],
    [29.5, "Extreme"],
    [30, "Extreme"],
    [120, "Extreme"],
  ] as const)("FWI %s is %s", (fwi, label) => {
    expect(fwiClass(fwi)).toBe(label);
  });

  it("each displayed integer maps to its CWFIS class", () => {
    const expected = (n: number) =>
      n <= 5 ? "Low" : n <= 15 ? "Moderate" : n <= 22 ? "High" : n <= 29 ? "Very High" : "Extreme";
    for (let n = 0; n <= 40; n++) expect(fwiClass(n)).toBe(expected(n));
  });
});

describe("fwiClassColor", () => {
  it("gives the same colour for a value and its class label", () => {
    for (const v of [1, 10, 20, 25, 40]) expect(fwiClassColor(v)).toBe(fwiClassColor(fwiClass(v)));
  });

  it("gives distinct colours per class", () => {
    const colours = ["Low", "Moderate", "High", "Very High", "Extreme"].map((c) => fwiClassColor(c));
    expect(new Set(colours).size).toBe(5);
  });

  it("falls back for an unknown label", () => {
    expect(fwiClassColor("Unknown")).toBe("#1a237e");
  });
});

describe("fwiClassTextColor", () => {
  it.each(["Low", "Moderate", "High", "Very High", "Extreme"])("%s badge text meets 4.5:1", (c) => {
    expect(contrastRatio(fwiClassTextColor(c), fwiClassColor(c))).toBeGreaterThanOrEqual(4.5);
  });
});
