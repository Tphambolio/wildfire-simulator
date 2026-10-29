import { beforeEach, describe, expect, it } from "vitest";
import { loadIncidents, loadScenarios, normaliseIncident, readStoredArray, writeStored } from "./storage";

const KEY = "t-incidents";

describe("browser storage (QA 2026-10-11)", () => {
  beforeEach(() => localStorage.clear());

  it("reads a non-array or unparseable value as empty and keeps the raw text aside", () => {
    localStorage.setItem(KEY, '{"not":"an array"}');
    expect(readStoredArray(KEY)).toEqual([]);
    expect(localStorage.getItem(`${KEY}.unreadable`)).toBe('{"not":"an array"}');
    localStorage.setItem("t2", "[{trunc");
    expect(readStoredArray("t2")).toEqual([]);
    expect(localStorage.getItem("t2.unreadable")).toBe("[{trunc");
  });

  it("drops entries without an id and fills an old incident's missing fields", () => {
    localStorage.setItem(KEY, JSON.stringify([null, 3, { name: "no id" }, { id: "old", name: "Old fire" }]));
    const [inc, ...rest] = loadIncidents(KEY);
    expect(rest).toEqual([]);
    expect(inc.id).toBe("old");
    expect(inc.operationalPeriods).toHaveLength(1);
    expect(inc.operationalPeriods[0].annotations).toEqual([]);
    expect(inc.activePeriodIndex).toBe(0);
    expect(inc.status).toBe("active");
    expect(inc.evacTiers).toEqual([]);
  });

  it("clamps a stale active period index and repairs period fields", () => {
    const inc = normaliseIncident({
      id: "x", name: "X", activePeriodIndex: 7,
      operationalPeriods: [{ day: 1, date: "2026-10-10", annotations: "bad", frameSummaries: null, finalPerimeter: "nope" },
        "garbage", { day: 2 }],
    })!;
    expect(inc.operationalPeriods.map((p) => p.day)).toEqual([1, 2]);
    expect(inc.activePeriodIndex).toBe(1);
    expect(inc.operationalPeriods[0].annotations).toEqual([]);
    expect(inc.operationalPeriods[0].frameSummaries).toEqual([]);
    expect(inc.operationalPeriods[0].finalPerimeter).toBeNull();
    expect(typeof inc.operationalPeriods[1].date).toBe("string");
  });

  it("gives an older saved scenario the Setup defaults it lacks", () => {
    localStorage.setItem("s", JSON.stringify([
      { id: "a", name: "Old", weather: { wind_speed: 10, wind_direction: 90 } },
      { id: "b", name: "No weather" },
    ]));
    const [s, ...rest] = loadScenarios("s");
    expect(rest).toEqual([]);
    expect(s.fwi).toEqual({ ffmc: 90, dmc: 45, dc: 300 });
    expect(s.multiDayDays).toHaveLength(1);
    expect(s.simMode).toBe("single");
    expect(s.durationHours).toBe(4);
  });

  it("keeps run IDs of either length verbatim (8-hex before 2026-10-13, 32-hex since)", () => {
    // Stored incidents and exported incident files carry each period's run ID; the API made
    // 8 hex characters before 2026-10-13 and 32 since (api services/run_ids.py). Both reload.
    const legacy = "1a2b3c4d";
    const current = "0123456789abcdef0123456789abcdef";
    localStorage.setItem(KEY, JSON.stringify([{
      id: "inc", name: "Two eras",
      operationalPeriods: [
        { day: 1, simulationId: legacy, simulationStatus: "completed" },
        { day: 2, simulationId: current, simulationStatus: "completed" },
      ],
    }]));
    const [inc] = loadIncidents(KEY);
    expect(inc.operationalPeriods.map((p) => p.simulationId)).toEqual([legacy, current]);
    const reimported = normaliseIncident(JSON.parse(JSON.stringify({ ...inc, id: "copy" })))!;
    expect(reimported.operationalPeriods.map((p) => p.simulationId)).toEqual([legacy, current]);
  });

  it("reports a refused write", () => {
    const orig = Storage.prototype.setItem;
    Storage.prototype.setItem = () => {
      throw new DOMException("full", "QuotaExceededError");
    };
    try {
      expect(writeStored(KEY, [1])).toBe(false);
    } finally {
      Storage.prototype.setItem = orig;
    }
    expect(writeStored(KEY, [1])).toBe(true);
  });
});

describe("incident name rule (E9, 2026-10-24)", () => {
  it("an empty or blank name becomes 'Untitled incident'; others are trimmed", async () => {
    const { normaliseIncidentName, UNTITLED_INCIDENT } = await import("./storage");
    expect(UNTITLED_INCIDENT).toBe("Untitled incident");
    expect(normaliseIncidentName("")).toBe("Untitled incident");
    expect(normaliseIncidentName("   ")).toBe("Untitled incident");
    expect(normaliseIncidentName(undefined)).toBe("Untitled incident");
    expect(normaliseIncidentName("  Millcreek ")).toBe("Millcreek");
    expect(normaliseIncident({ id: "x", name: "" })?.name).toBe("Untitled incident");
    expect(normaliseIncident({ id: "x", name: " Keep " })?.name).toBe("Keep");
  });
});
