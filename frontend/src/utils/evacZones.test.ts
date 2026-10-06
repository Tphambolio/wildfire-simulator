import { describe, expect, it } from "vitest";
import { applyZoneHistory, computeEvacZones, evacZonesToGeoJSON, type EvacZoneLabel } from "./evacZones";
import { collection, frame, growingFire, neighbourhood, square } from "../test/frames";

const SCALES: Record<EvacZoneLabel, number> = { Order: 1, Alert: 1, Watch: 1 };

describe("computeEvacZones", () => {
  it("returns no zones without frames", () => {
    expect(computeEvacZones([], null)).toEqual([]);
  });

  it("only includes tiers within the simulation duration", () => {
    // 4 h run: Order (2 h) is in range, Alert (6 h) is beyond 4 + 1 h, Watch (12 h) too
    expect(computeEvacZones(growingFire(4), null, SCALES).map((z) => z.label)).toEqual(["Order"]);
    // 12 h run: all three
    expect(computeEvacZones(growingFire(12), null, SCALES).map((z) => z.label)).toEqual(["Order", "Alert", "Watch"]);
  });

  it("puts a neighbourhood in a tier when its centroid is inside that tier's perimeter (point in polygon)", () => {
    // Order uses the 2 h frame: half-width 0.02 deg. Alert uses 6 h: 0.06 deg. Watch 12 h: 0.12 deg.
    const comms = collection([
      neighbourhood("Inside2h", 0.015),
      neighbourhood("Edge2hOutside", 0.025),
      neighbourhood("Inside6h", 0.05),
      neighbourhood("Inside12h", 0.1, 0.1),
      neighbourhood("FarAway", 0.5),
    ]);
    const zones = computeEvacZones(growingFire(12), comms, { ...SCALES });
    const byLabel = Object.fromEntries(zones.map((z) => [z.label, z.communitiesAtRisk]));
    expect(byLabel.Order).toEqual(["Inside2h"]);
    expect(byLabel.Alert).toEqual(["Edge2hOutside", "Inside6h"]);
    expect(byLabel.Watch).toEqual(["Inside12h"]);
  });

  it("handles a concave perimeter (a centroid in the notch is outside)", () => {
    // U shape opening north: notch between lng -0.01..+0.01 above lat +0.0
    const d = 0.03;
    const u = [
      [-d, -d], [-d, d], [d, d], [d, 0.01], [0, 0.01], [0, -0.01], [d, -0.01], [d, -d],
    ].map(([a, b]) => [53.5 + a, -113.5 + b]);
    const comms = collection([neighbourhood("InNotch", 0.02), neighbourhood("InArm", 0.02, 0.02)]);
    const zones = computeEvacZones([frame(1, u), frame(2, u)], comms, { ...SCALES });
    expect(zones[0].communitiesAtRisk).toEqual(["InArm"]);
  });

  it("scales a tier perimeter around its centroid", () => {
    const comms = collection([neighbourhood("Ring", 0.03)]);
    const frames = growingFire(4);
    expect(computeEvacZones(frames, comms, { ...SCALES })[0].communitiesAtRisk).toEqual([]);
    const scaled = computeEvacZones(frames, comms, { ...SCALES, Order: 2 })[0];
    expect(scaled.scale).toBe(2);
    expect(scaled.communitiesAtRisk).toEqual(["Ring"]);
    // Scaled 2 h square: 0.08 x 0.08 deg at 53.5 N, about 8.9 km x 5.3 km = ~4700 ha
    expect(scaled.areaHa).toBeGreaterThan(4500);
    expect(scaled.areaHa).toBeLessThan(4900);
  });

  it("returns the same array while scrubbing between frames that pick the same tier frames", () => {
    const comms = collection([neighbourhood("A", 0.015)]);
    const frames = growingFire(4); // frames at 1, 2, 3, 4 h
    // Scrubbed to 3 h and to 4 h: Order picks the 2 h frame both times, Alert/Watch are out of range
    const a = computeEvacZones(frames.slice(0, 3), comms, SCALES);
    const b = computeEvacZones(frames.slice(0, 4), comms, SCALES);
    expect(b).toBe(a);
    // A different communities layer recomputes
    const c = computeEvacZones(frames.slice(0, 4), collection([neighbourhood("A", 0.015)]), SCALES);
    expect(c).not.toBe(b);
    expect(c).toEqual(b);
    // A new scales object recomputes
    const d = computeEvacZones(frames.slice(0, 4), comms, { ...SCALES });
    expect(d).not.toBe(c);
    // Scrubbing back to 1 h picks a different Order frame
    const e = computeEvacZones(frames.slice(0, 1), comms, SCALES);
    expect(e).not.toBe(d);
  });

  it("converts zones to GeoJSON features tagged with the tier", () => {
    const comms = collection([neighbourhood("Inside2h", 0.015)]);
    const fc = evacZonesToGeoJSON(computeEvacZones(growingFire(4), comms, { ...SCALES }));
    expect(fc.features).toHaveLength(1);
    expect(fc.features[0].properties).toMatchObject({ zone_label: "Order", neighbourhood: "Inside2h", name: "Inside2h" });
  });
});

describe("applyZoneHistory", () => {
  it("keeps a committed Order neighbourhood when the scrubber moves back", () => {
    const comms = collection([neighbourhood("A", 0.015), neighbourhood("B", 0.5)]);
    const current = computeEvacZones([frame(1, square(0.001)), frame(2, square(0.002))], comms, { ...SCALES });
    expect(current[0].communitiesAtRisk).toEqual([]);
    const history = new Map<string, EvacZoneLabel>([["A", "Order"], ["B", "Watch"]]);
    const merged = applyZoneHistory(current, history, comms);
    expect(merged.find((z) => z.label === "Order")?.communitiesAtRisk).toEqual(["A"]);
    // Watch is never persisted
    expect(merged.some((z) => z.communitiesAtRisk.includes("B"))).toBe(false);
  });

  it("returns the input when there is no history", () => {
    const zones = computeEvacZones(growingFire(4), null, { ...SCALES });
    expect(applyZoneHistory(zones, new Map(), null)).toBe(zones);
  });
});
