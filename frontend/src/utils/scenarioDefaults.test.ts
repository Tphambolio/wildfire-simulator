import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { fallbackScenarioConfig } from "./scenarioDefaults";

describe("fallbackScenarioConfig", () => {
  it("keeps the unsourced WUI modifiers and the OSM water mask off, like the Setup defaults", () => {
    const c = fallbackScenarioConfig(null);
    expect(c.includeWUI).toBe(false);
    expect(c.includeWater).toBe(false);
    expect(c.useEdmontonGrid).toBe(true);
  });

  it("matches the WeatherPanel initial state for the WUI and water options", () => {
    const src = readFileSync("src/components/WeatherPanel.tsx", "utf8");
    expect(src).toMatch(/const \[includeWUI, setIncludeWUI\] = useState\(false\)/);
    expect(src).toMatch(/const \[includeWater, setIncludeWater\] = useState\(false\)/);
  });

  it("carries the ignition point", () => {
    expect(fallbackScenarioConfig({ lat: 53.5, lng: -113.5 }).ignitionPoint).toEqual({ lat: 53.5, lng: -113.5 });
  });

  it("is what App passes to the scenario panel (no inline fallback with WUI on)", () => {
    const app = readFileSync("src/App.tsx", "utf8");
    expect(app).toContain("fallbackScenarioConfig(ignitionPoint)");
    expect(app).not.toMatch(/includeWUI:\s*true/);
  });
});
