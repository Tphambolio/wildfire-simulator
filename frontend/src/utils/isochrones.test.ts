import { describe, expect, it } from "vitest";
import {
  DEFAULT_ISO_HOURS,
  computeIsochrones,
  isochroneLabelsGeoJSON,
  isochronesToGeoJSON,
} from "./isochrones";
import { frame, growingFire, square } from "../test/frames";
import { fixture } from "../test/fixture";

describe("computeIsochrones", () => {
  it("returns nothing without frames", () => {
    expect(computeIsochrones([])).toEqual([]);
  });

  it("picks the closest frame per target, in time order, and skips targets past the run", () => {
    const isos = computeIsochrones(growingFire(4), [8, 2, 0.5, 1, 4]);
    // 8 h is beyond 4 h + 0.5 h; 0.5 h resolves to the 1 h frame, so the explicit 1 h target is a duplicate
    expect(isos.map((i) => i.timeHours)).toEqual([1, 2, 4]);
    expect(isos.map((i) => i.label)).toEqual(["T+30min", "T+2h", "T+4h"]);
  });

  it("labels fractional hours and colours imminent contours red", () => {
    const isos = computeIsochrones([frame(1.5, square(0.01)), frame(3, square(0.02))], [1.5, 3]);
    expect(isos.map((i) => i.label)).toEqual(["T+1.5h", "T+3h"]);
    expect(computeIsochrones(growingFire(1), [0.5])[0].color).toBe("#ff1744");
  });

  it("ignores frames with fewer than 3 perimeter points", () => {
    const isos = computeIsochrones([frame(1, [[53.5, -113.5]]), frame(2, square(0.01))], [1]);
    expect(isos).toHaveLength(1);
    expect(isos[0].timeHours).toBe(2);
  });

  it("works on the recorded fixture run (4 h, 15 min snapshots)", () => {
    const isos = computeIsochrones(fixture.frames, DEFAULT_ISO_HOURS);
    expect(isos.map((i) => i.timeHours)).toEqual([1, 2, 4]);
    for (const iso of isos) expect(iso.perimeter.length).toBeGreaterThanOrEqual(3);
  });
});

describe("isochrone GeoJSON", () => {
  const isos = computeIsochrones(growingFire(2), [1, 2]);

  it("emits closed LineStrings in [lng, lat] order", () => {
    const fc = isochronesToGeoJSON(isos);
    expect(fc.features).toHaveLength(2);
    const g = fc.features[0].geometry as GeoJSON.LineString;
    expect(g.coordinates[0]).toEqual(g.coordinates[g.coordinates.length - 1]);
    expect(g.coordinates).toHaveLength(5);
    const [lng, lat] = g.coordinates[0];
    expect(lng).toBeLessThan(-100);
    expect(lat).toBeGreaterThan(50);
  });

  it("anchors each label at the northernmost perimeter point", () => {
    const fc = isochroneLabelsGeoJSON(isos);
    const p = fc.features[1].geometry as GeoJSON.Point;
    expect(p.coordinates[1]).toBeCloseTo(53.52, 10);
    expect(fc.features[1].properties).toMatchObject({ label: "T+2h", time_hours: 2 });
  });
});
