import { afterEach, describe, expect, it } from "vitest";
import { LIVE_RUN_KEY, LIVE_RUN_MAX_AGE_MS, clearLiveRun, readLiveRun, saveLiveRun, type LiveRunRecord } from "./liveRun";

const rec = (over: Partial<LiveRunRecord> = {}): LiveRunRecord => ({
  id: "a64435a5aff2bbd799e6f40f33d46390",
  startIso: "2026-10-17T10:57:00.000Z",
  durationHours: 72,
  startedAt: 1_000_000,
  ensembleMembers: null,
  burningPeriod: { start_hour: 10, end_hour: 20 },
  runParams: null,
  runTarget: { incidentId: "inc-1", periodIndex: 0 },
  savedAt: 1_000_500,
  ...over,
});

describe("live run kept for a reload (field test 2026-10-18, Issue 2)", () => {
  afterEach(() => sessionStorage.clear());

  it("round-trips the run's ID, span and target in sessionStorage", () => {
    saveLiveRun(rec());
    expect(JSON.parse(sessionStorage.getItem(LIVE_RUN_KEY)!).id).toBe(rec().id);
    expect(localStorage.getItem(LIVE_RUN_KEY)).toBeNull(); // this tab only
    expect(readLiveRun(1_000_000 + 60_000)).toEqual(rec());
  });

  it("ignores and drops a record older than the limit", () => {
    saveLiveRun(rec());
    expect(readLiveRun(1_000_000 + LIVE_RUN_MAX_AGE_MS + 1)).toBeNull();
    expect(sessionStorage.getItem(LIVE_RUN_KEY)).toBeNull();
  });

  it("reads nothing after Clear results, from an unreadable value, or without an ID", () => {
    saveLiveRun(rec());
    clearLiveRun();
    expect(readLiveRun(1_000_100)).toBeNull();
    sessionStorage.setItem(LIVE_RUN_KEY, "{not json");
    expect(readLiveRun(1_000_100)).toBeNull();
    sessionStorage.setItem(LIVE_RUN_KEY, JSON.stringify({ ...rec(), id: "" }));
    expect(readLiveRun(1_000_100)).toBeNull();
  });

  it("fills missing optional fields with null", () => {
    sessionStorage.setItem(LIVE_RUN_KEY, JSON.stringify({ id: "abc", savedAt: 5 }));
    expect(readLiveRun(10)).toEqual({
      id: "abc", startIso: null, durationHours: null, startedAt: null, ensembleMembers: null,
      burningPeriod: null, runParams: null, runTarget: null, savedAt: 5,
    });
  });
});
