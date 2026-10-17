import { describe, expect, it } from "vitest";
import { deleteSetup, loadSetup, saveSetup, setupKey } from "./setupStore";
import { fallbackScenarioConfig } from "./scenarioDefaults";

/** Round-3 field test R8: the incident's setup comes back after a reload. */
describe("setup kept per incident across a reload (R8)", () => {
  const snap = {
    ...fallbackScenarioConfig({ lat: 53.46, lng: -113.66 }),
    startIso: "2026-10-13T04:57:00-06:00",
    grassCure: 80,
    weatherOrigin: { manual: true, source: null, station: null, distanceKm: null, timestamp: null },
  };

  it("saves and reads back under the incident id, or scratch without one", () => {
    saveSetup("inc-1", snap);
    expect(loadSetup("inc-1")).toEqual(snap);
    expect(loadSetup("inc-2")).toBeNull();
    expect(setupKey(null)).toBe("firesim.setup.scratch");
    saveSetup(null, { ...snap, grassCure: 60 });
    expect(loadSetup(null)?.grassCure).toBe(60);
    deleteSetup("inc-1");
    expect(loadSetup("inc-1")).toBeNull();
  });

  it("ignores unreadable or foreign values", () => {
    localStorage.setItem(setupKey("bad"), "{not json");
    expect(loadSetup("bad")).toBeNull();
    localStorage.setItem(setupKey("odd"), JSON.stringify({ setup: { weather: null } }));
    expect(loadSetup("odd")).toBeNull();
  });
});
