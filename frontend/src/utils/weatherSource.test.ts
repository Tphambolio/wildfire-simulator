import { describe, expect, it } from "vitest";
import { isPyraSource } from "./weatherSource";

describe("isPyraSource", () => {
  it("recognises the API's Pyra tier label", () => {
    expect(
      isPyraSource(
        "Pyra · Edmonton Blatchford · as of noon LST forecast 2026-10-10 (chain EDMONTON BLATCHFORD 2026-10-09)",
      ),
    ).toBe(true);
  });
  it("leaves CWFIS and estimate sources alone", () => {
    expect(isPyraSource("CWFIS — EDMONTON BLATCHFORD (AB)")).toBe(false);
    expect(isPyraSource("Open-Meteo GEM (gem_seamless) (FWI cold-start estimate; no CWFIS station data)")).toBe(false);
    expect(isPyraSource(null)).toBe(false);
    expect(isPyraSource(undefined)).toBe(false);
  });
});
