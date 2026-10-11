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
