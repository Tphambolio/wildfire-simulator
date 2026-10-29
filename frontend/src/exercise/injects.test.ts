import { describe, expect, it } from "vitest";
import { EMPTY_INJECT_LOG, addInject, dueInjects, markDelivered, withdrawInject } from "./injects";

const T0 = Date.UTC(2026, 6, 15, 16, 0);
const H = 3_600_000;

describe("inject log", () => {
  it("adds injects sorted by exercise time, scheduled, with a default title", () => {
    let log = addInject(EMPTY_INJECT_LOG, {
      exerciseTimeMs: T0 + 2 * H, title: "  ", payload: { kind: "wind_shift", windSpeedKmh: 35, windDirectionDeg: 270, hiddenFromForecast: true },
    }, "2026-10-25T10:00:00Z");
    log = addInject(log, { exerciseTimeMs: T0 + H, title: "Perimeter from RPAS flight 1", payload: { kind: "perimeter", perimeterGeojson: null, observedAtIso: "2026-07-15T11:00:00-06:00" } });
    expect(log.injects.map((i) => i.kind)).toEqual(["perimeter", "wind_shift"]);
    expect(log.injects[1].title).toBe("Wind 35 km/h from 270°");
    expect(log.injects[1].createdAtIso).toBe("2026-10-25T10:00:00Z");
    expect(log.injects.every((i) => i.status === "scheduled" && i.id)).toBe(true);
    expect(new Set(log.injects.map((i) => i.id)).size).toBe(2);
    expect(EMPTY_INJECT_LOG.injects).toEqual([]);
  });

  it("dueInjects lists scheduled injects at or before the exercise time, in order", () => {
    let log = addInject(EMPTY_INJECT_LOG, { exerciseTimeMs: T0 + 3 * H, title: "late", payload: { kind: "note", text: "x" } });
    log = addInject(log, { exerciseTimeMs: T0 + H, title: "early", payload: { kind: "resource", text: "Two engines released" } });
    expect(dueInjects(log, T0).map((i) => i.title)).toEqual([]);
    expect(dueInjects(log, T0 + H).map((i) => i.title)).toEqual(["early"]);
    expect(dueInjects(log, T0 + 5 * H).map((i) => i.title)).toEqual(["early", "late"]);
  });

  it("markDelivered and withdrawInject change only a scheduled inject, once", () => {
    let log = addInject(EMPTY_INJECT_LOG, { exerciseTimeMs: T0 + H, title: "a", payload: { kind: "spot_fire", lat: 53.5, lng: -113.5 } });
    log = addInject(log, { exerciseTimeMs: T0 + 2 * H, title: "b", payload: { kind: "note", text: "n" } });
    const [a, b] = log.injects;
    const d = markDelivered(log, a.id, T0 + H + 1000);
    expect(d.injects[0].status).toBe("delivered");
    expect(d.injects[0].deliveredAtMs).toBe(T0 + H + 1000);
    expect(dueInjects(d, T0 + 10 * H).map((i) => i.id)).toEqual([b.id]);
    // Already delivered: unchanged (same object)
    expect(markDelivered(d, a.id, T0)).toBe(d);
    expect(withdrawInject(d, a.id)).toBe(d);
    const w = withdrawInject(d, b.id);
    expect(w.injects[1].status).toBe("withdrawn");
    expect(dueInjects(w, T0 + 10 * H)).toEqual([]);
    expect(withdrawInject(w, "missing")).toBe(w);
  });
});
