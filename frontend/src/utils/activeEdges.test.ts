import { describe, expect, it } from "vitest";
import {
  buildObservedRequest,
  segmentLines,
  segmentReadout,
  activeEdgesGeometry,
  activeLines,
  buildOverrideRequest,
  framePerimeterToPolygon,
  perimeterFromGeoJSON,
  perimeterRing,
  perimeterSides,
  type Lnglat,
} from "./activeEdges";

// A square around (-113.5, 53.5), 4 points per side, closed ring
function square(): Lnglat[] {
  const r: Lnglat[] = [];
  const d = 0.01;
  for (let i = 0; i < 4; i++) r.push([-113.5 - d + (2 * d * i) / 4, 53.5 - d]); // south, W→E
  for (let i = 0; i < 4; i++) r.push([-113.5 + d, 53.5 - d + (2 * d * i) / 4]); // east, S→N
  for (let i = 0; i < 4; i++) r.push([-113.5 + d - (2 * d * i) / 4, 53.5 + d]); // north, E→W
  for (let i = 0; i < 4; i++) r.push([-113.5 - d, 53.5 + d - (2 * d * i) / 4]); // west, N→S
  r.push(r[0]);
  return r;
}

describe("perimeter input", () => {
  it("reads Feature, FeatureCollection and bare geometry; rejects others", () => {
    const poly = { type: "Polygon", coordinates: [square()] };
    expect(perimeterFromGeoJSON(poly).type).toBe("Polygon");
    expect(perimeterFromGeoJSON({ type: "Feature", geometry: poly, properties: {} }).type).toBe("Polygon");
    expect(perimeterFromGeoJSON({ type: "FeatureCollection", features: [{ type: "Feature", geometry: poly }] }).type).toBe("Polygon");
    expect(() => perimeterFromGeoJSON({ type: "LineString", coordinates: [] })).toThrow(/Unsupported/);
    expect(() => perimeterFromGeoJSON({ type: "FeatureCollection", features: [] })).toThrow(/No Polygon/);
  });

  it("closes a frame perimeter ([lat, lng]) into a GeoJSON polygon", () => {
    const poly = framePerimeterToPolygon([[53.5, -113.5], [53.5, -113.49], [53.51, -113.49]])!;
    expect(poly.coordinates[0]).toEqual([[-113.5, 53.5], [-113.49, 53.5], [-113.49, 53.51], [-113.5, 53.5]]);
    expect(framePerimeterToPolygon([[53.5, -113.5]])).toBeNull();
    const multi: GeoJSON.MultiPolygon = { type: "MultiPolygon", coordinates: [[square().slice(0, 4)], [square()]] };
    expect(perimeterRing(multi)).toHaveLength(17);
  });
});

describe("perimeter sides", () => {
  it("splits a round perimeter into 8 sides of 4 segments each, one line per side", () => {
    // 32 vertices on a circle (metric, so the bearing of each vertex is exact), from north
    const kx = Math.cos((53.5 * Math.PI) / 180);
    const ring: Lnglat[] = Array.from({ length: 32 }, (_, i) => {
      const b = (i * 2 * Math.PI) / 32; // segment midpoints at 5.625 + 11.25 k degrees: none on a sector edge
      return [-113.5 + (0.01 * Math.sin(b)) / kx, 53.5 + 0.01 * Math.cos(b)];
    });
    ring.push(ring[0]);
    const sides = perimeterSides(ring);
    for (const lines of Object.values(sides)) {
      expect(lines).toHaveLength(1);
      expect(lines[0]).toHaveLength(5);
    }
    expect(sides.E[0].every(([lng]) => lng > -113.5)).toBe(true);
    expect(sides.N[0].every(([, lat]) => lat > 53.5)).toBe(true);
  });

  it("puts every segment of a square on exactly one side", () => {
    const sides = perimeterSides(square());
    const segs = Object.values(sides).flat().reduce((n, l) => n + l.length - 1, 0);
    expect(segs).toBe(16);
    expect(sides.E.flat().every(([lng]) => Math.abs(lng - -113.49) < 1e-9)).toBe(true);
  });
});

describe("override request", () => {
  const perimeter: GeoJSON.Polygon = { type: "Polygon", coordinates: [square()] };
  const base = {
    simulationId: "abc", perimeter, durationHours: 4, snapshotMinutes: 30,
    bufferM: null, startTime: "2026-07-15T15:00:00-06:00",
    burningPeriod: { start_hour: 10, end_hour: 20 }, ffmcSpinUp: false,
  };

  it("whole perimeter: no active_edges", () => {
    const req = buildOverrideRequest({ ...base, mode: "whole", lines: [] });
    expect(req.active_edges).toBeUndefined();
    expect(req).toMatchObject({ simulation_id: "abc", start_time: base.startTime, burning_period: base.burningPeriod });
    expect(req.ffmc_spin_up).toBeUndefined();
  });

  it("marked edges: a MultiLineString of drawn lines and sides, with the buffer", () => {
    const sides = perimeterSides(square());
    const drawn: Lnglat[][] = [[[-113.49, 53.495], [-113.49, 53.505]], [[-113.5, 53.5]]]; // second is too short
    const lines = activeLines(drawn, ["N"], sides);
    expect(lines).toHaveLength(2);
    const req = buildOverrideRequest({ ...base, mode: "marked", lines, bufferM: 60, ffmcSpinUp: true });
    expect(req.active_edges).toEqual(activeEdgesGeometry(lines));
    expect((req.active_edges as GeoJSON.MultiLineString).type).toBe("MultiLineString");
    expect(req.active_edge_buffer_m).toBe(60);
    expect(req.ffmc_spin_up).toBe(true);
  });

  it("marked with nothing marked is an error", () => {
    expect(() => buildOverrideRequest({ ...base, mode: "marked", lines: [] })).toThrow(/at least one/);
  });
});

describe("active segments (A1)", () => {
  const perimeter: GeoJSON.Polygon = { type: "Polygon", coordinates: [square()] };
  const base = {
    perimeter, durationHours: 6, snapshotMinutes: 30, lines: [] as Lnglat[][], bufferM: null,
    startTime: "2026-07-15T15:00:00-06:00", burningPeriod: null, ffmcSpinUp: false,
  };

  it("classified edges are sent as active_edges, with no hold", () => {
    const edges: GeoJSON.MultiLineString = { type: "MultiLineString", coordinates: [[[0, 0], [1, 0]]] };
    const req = buildOverrideRequest({ ...base, simulationId: "a", mode: "classified", classifiedEdges: edges });
    expect(req.active_edges).toEqual(edges);
    expect(req).not.toHaveProperty("hold_hours");
  });

  it("classified with no active segment throws (nothing would spread)", () => {
    expect(() => buildOverrideRequest({ ...base, simulationId: "a", mode: "classified", classifiedEdges: null }))
      .toThrow(/nothing would spread/);
    expect(() => buildOverrideRequest({ ...base, simulationId: "a", mode: "classified",
      classifiedEdges: { type: "MultiLineString", coordinates: [] } })).toThrow(/nothing would spread/);
  });

  it("segment lines and readout from a classification", () => {
    const fc: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [
      { type: "Feature", properties: { status: "active" }, geometry: { type: "LineString", coordinates: [[0, 0], [1, 0]] } },
      { type: "Feature", properties: { status: "inactive" }, geometry: { type: "LineString", coordinates: [[1, 0], [1, 1]] } },
      { type: "Feature", properties: { status: "inactive" }, geometry: { type: "LineString", coordinates: [[1, 1], [0, 0]] } },
    ] };
    expect(segmentLines(fc)).toEqual([[[0, 0], [1, 0]]]);
    expect(segmentReadout({ n_segments: 3, n_active: 1 })).toBe("Segments active 1 of 3");
    expect(segmentReadout(null)).toBeNull();
  });

  it("standalone request: Setup's run without ignition, start or ensemble; perimeter at T", () => {
    const setupReq = {
      ignition_lat: 53.5, ignition_lng: -113.5, start_time: "2026-07-15T10:00:00-06:00",
      weather: { wind_speed: 20, wind_direction: 270, temperature: 25, relative_humidity: 30, precipitation_24h: 0 },
      fwi_overrides: { ffmc: 90, dmc: 40, dc: 300 }, duration_hours: 4, fuel_grid_path: "/app/data/x.tif",
      ensemble: { n_members: 20 }, ffmc_spin_up: true, snapshot_interval_minutes: 30, fuel_type: "C2",
    };
    const req = buildObservedRequest(setupReq, { ...base, mode: "marked", lines: [[[-113.49, 53.495], [-113.49, 53.505]]] },
      "2026-07-15T15:00:00-06:00");
    expect(req).not.toHaveProperty("ignition_lat");
    expect(req).not.toHaveProperty("start_time");
    expect(req).not.toHaveProperty("ensemble");
    expect(req.observation_time).toBe("2026-07-15T15:00:00-06:00");
    expect(req.duration_hours).toBe(6);
    expect(req.perimeter_geojson).toEqual(perimeter);
    expect(req).not.toHaveProperty("hold_hours");
    expect((req.active_edges as GeoJSON.MultiLineString).coordinates).toHaveLength(1);
    expect(req.ffmc_spin_up).toBe(false);
    expect(req.fuel_grid_path).toBe("/app/data/x.tif");
  });
});
