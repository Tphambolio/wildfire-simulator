import { describe, expect, it } from "vitest";
import { curingFactor, defaultGrassCure, effectiveGrassCure, inSpringCuringWindow } from "./curing";

describe("grass curing default (M1)", () => {
  it("is 95 % from day 60 to 149 and absent outside", () => {
    expect(defaultGrassCure(59)).toBeNull();
    expect(defaultGrassCure(60)).toBe(95);
    expect(defaultGrassCure(110)).toBe(95);
    expect(defaultGrassCure(149)).toBe(95);
    expect(defaultGrassCure(150)).toBeNull();
    expect(defaultGrassCure(196)).toBeNull();
    expect(defaultGrassCure(null)).toBeNull();
    expect(inSpringCuringWindow(300)).toBe(false);
  });

  it("uses the entry before the default", () => {
    expect(effectiveGrassCure(60, 110)).toBe(60);
    expect(effectiveGrassCure(null, 110)).toBe(95);
    expect(effectiveGrassCure(null, 196)).toBeNull();
    expect(effectiveGrassCure(70, 196)).toBe(70);
  });

  it("matches GLC-X-10 eq 35b", () => {
    expect(curingFactor(60)).toBeCloseTo(0.2, 9);
    expect(curingFactor(95)).toBeCloseTo(0.9, 9);
    expect(curingFactor(100)).toBeCloseTo(1.0, 9);
    expect(curingFactor(50)).toBeCloseTo(0.005 * (Math.exp(3.05) - 1), 12);
  });
});
