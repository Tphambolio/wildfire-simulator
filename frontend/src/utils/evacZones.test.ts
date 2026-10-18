import { describe, expect, it } from "vitest";
import neighbourhoodsRaw from "../../public/edmonton/neighbourhoods.geojson?raw";
import {
  arrivalLabel,
  arrivalTime,
  arrivalsToGeoJSON,
  featureName,
  labelPoint,
  neighbourhoodArrivals,
  planningZones,
  planningZonesToGeoJSON,
  upsertTier,
  type EvacTierRecord,
} from "./evacZones";
import { collection, frame, growingFire, neighbourhood, square } from "../test/frames";
import { fixture } from "../test/fixture";
import { withAllCells } from "../hooks/useSimulation";
import type { SimulationFrame } from "../types/simulation";

const edmonton = JSON.parse(neighbourhoodsRaw) as GeoJSON.FeatureCollection;

/** Haversine metres. */
function metres(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const R = 6_371_000;
  const toRad = Math.PI / 180;
  const dLat = (lat2 - lat1) * toRad;
  const dLng = (lng2 - lng1) * toRad;
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * toRad) * Math.cos(lat2 * toRad) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

/** Brute force: distance from a point to a polygon's outline (vertices densified to ~10 m). */
function distToOutline(f: GeoJSON.Feature, lat: number, lng: number): number {
  const g = f.geometry as GeoJSON.Polygon | GeoJSON.MultiPolygon;
  const polys = g.type === "Polygon" ? [g.coordinates] : g.coordinates;
  let best = Infinity;
  for (const poly of polys) for (const ring of poly) {
    for (let i = 1; i < ring.length; i++) {
      const [x1, y1] = ring[i - 1];
      const [x2, y2] = ring[i];
      const n = Math.max(1, Math.ceil(metres(y1, x1, y2, x2) / 10));
      for (let k = 0; k <= n; k++) {
        const d = metres(lat, lng, y1 + ((y2 - y1) * k) / n, x1 + ((x2 - x1) * k) / n);
        if (d < best) best = d;
      }
    }
  }
  return best;
}

describe("neighbourhoodArrivals on the recorded Terwillegar fixture", () => {
  const raw = fixture.frames as SimulationFrame[];
  // As the app holds them: cumulative cells per frame
  const merged: SimulationFrame[] = [];
  raw.forEach((f, i) => merged.push(withAllCells(f, merged[i - 1])));
  const arrivals = neighbourhoodArrivals(raw, edmonton);

  it("finds neighbourhoods, sorted by arrival time, within the run", () => {
    expect(arrivals.length).toBeGreaterThan(0);
    const last = raw[raw.length - 1].time_hours;
    for (let i = 0; i < arrivals.length; i++) {
      expect(arrivals[i].arrivalHours).toBeGreaterThanOrEqual(0);
      expect(arrivals[i].arrivalHours).toBeLessThanOrEqual(last + 1e-9);
      if (i > 0) expect(arrivals[i].arrivalHours).toBeGreaterThanOrEqual(arrivals[i - 1].arrivalHours);
    }
  });

  it("gives the same result from incremental (streamed) and cumulative (in-app) frames", () => {
    expect(neighbourhoodArrivals(merged, edmonton).map((a) => [a.name, a.arrivalHours])).toEqual(
      arrivals.map((a) => [a.name, a.arrivalHours]),
    );
  });

  it("each arrival is the earliest burned cell within 500 m (brute-force haversine check)", () => {
    // All cells with their arrival time (t, minutes)
    const cells = new Map<string, { lat: number; lng: number; h: number }>();
    for (const f of raw) for (const c of f.burned_cells ?? []) {
      cells.set(`${c.lat},${c.lng}`, { lat: c.lat, lng: c.lng, h: (c.t ?? f.time_hours * 60) / 60 });
    }
    const byName = new Map(edmonton.features.map((f) => [featureName(f), f]));
    for (const a of arrivals) {
      const f = byName.get(a.name)!;
      // There is a cell at that time within 500 m (allow 2 m for the local projection)...
      const atTime = [...cells.values()].filter((c) => Math.abs(c.h - a.arrivalHours) < 1e-9);
      expect(atTime.some((c) => distToOutline(f, c.lat, c.lng) <= 502 || insideOrNear(a, c)), a.name).toBe(true);
      // ...and no earlier cell is clearly within 500 m
      const earlier = [...cells.values()].filter((c) => c.h < a.arrivalHours - 1e-9);
      expect(earlier.some((c) => distToOutline(f, c.lat, c.lng) < 498), a.name).toBe(false);
    }
  });

  it("neighbourhoods the fire never comes near are absent", () => {
    const names = new Set(arrivals.map((a) => a.name));
    expect(names.has("Abbottsfield")).toBe(false); // north-east Edmonton, far from Terwillegar
    expect(arrivals.length).toBeLessThan(20);
    // The ignition is in The Uplands (arrival 0); Edgemont and Cameron Heights are reached later
    expect(arrivals[0]).toMatchObject({ name: "The Uplands", arrivalHours: 0 });
    expect(names.has("Edgemont") && names.has("Cameron Heights")).toBe(true);
  });

  it("labels arrival as clock time from the scenario start", () => {
    const start = new Date("2026-07-15T20:05:00Z"); // 14:05 MDT
    expect(arrivalLabel(1.45, start)).toBe("Fire within 500 m by 15:32");
    expect(arrivalLabel(1.45, null)).toBe("Fire within 500 m by T+1:27");
    const fc = arrivalsToGeoJSON(arrivals, start);
    expect(fc.features).toHaveLength(arrivals.length);
    for (const f of fc.features) expect(f.properties!.arrival_label).toMatch(/^Fire within 500 m by \d{2}:\d{2}$/);
  });
});

// Is the cell inside the neighbourhood (distance 0)? Used where the brute-force outline
// distance would be > 500 m for a cell deep inside a large neighbourhood.
function insideOrNear(a: { feature: GeoJSON.Feature }, c: { lat: number; lng: number }): boolean {
  return neighbourhoodArrivals([frame(0, [], { burned_cells: [{ lat: c.lat, lng: c.lng, intensity: 1, fuel: "O1a", t: 0 }] })], collection([a.feature]), 0).length === 1;
}

describe("neighbourhoodArrivals on synthetic fires", () => {
  // growingFire: square perimeter of half-width 0.01 deg per hour (about 1.1 km of latitude)
  it("perimeter runs: the first frame whose perimeter comes within 500 m, or covers the neighbourhood", () => {
    const comms = collection([
      neighbourhood("Inside1h", 0.005), // inside the 1 h square
      neighbourhood("Near2h", 0.024), // spans 0.023..0.025 deg north
      neighbourhood("Far", 0.5),
    ]);
    const res = Object.fromEntries(neighbourhoodArrivals(growingFire(4), comms).map((a) => [a.name, a.arrivalHours]));
    expect(res.Inside1h).toBe(1);
    // Edge at 0.02 deg (2 h); neighbourhood spans 0.023..0.025 -> 0.003 deg = 334 m from the 2 h edge
    // and 0.013 deg = 1.4 km from the 1 h edge
    expect(res.Near2h).toBe(2);
    expect(res.Far).toBeUndefined();
  });

  it("grid runs: uses each cell's arrival time t (minutes), not the frame time", () => {
    const cells = [
      { lat: 53.5, lng: -113.5, intensity: 100, fuel: "O1a", t: 0 },
      { lat: 53.51, lng: -113.5, intensity: 100, fuel: "O1a", t: 50 }, // 0.01 deg north
    ];
    const comms = collection([neighbourhood("North", 0.012)]); // 0.011..0.013 -> 111 m from the t=50 cell
    const res = neighbourhoodArrivals([frame(1, square(0.02), { burned_cells: cells })], comms);
    expect(res).toHaveLength(1);
    expect(res[0].arrivalHours).toBeCloseTo(50 / 60, 9);
  });

  it("returns nothing without frames or neighbourhoods", () => {
    expect(neighbourhoodArrivals([], edmonton)).toEqual([]);
    expect(neighbourhoodArrivals(growingFire(2), null)).toEqual([]);
  });
});

describe("labelPoint", () => {
  it("is inside every Edmonton neighbourhood", () => {
    for (const f of edmonton.features) {
      const p = labelPoint(f);
      expect(p, featureName(f)).not.toBeNull();
      // Inside: zero distance with a zero buffer from a "cell" at the label point
      const hit = neighbourhoodArrivals(
        [frame(0, [], { burned_cells: [{ lat: p![1], lng: p![0], intensity: 1, fuel: "O1a", t: 0 }] })],
        collection([f]),
        0,
      );
      expect(hit.length, featureName(f)).toBe(1);
    }
  });

  it("falls back to the widest inside span for a U shape whose centroid is outside", () => {
    const u: GeoJSON.Feature = {
      type: "Feature",
      properties: { name: "U" },
      geometry: { type: "Polygon", coordinates: [[[0, 0], [3, 0], [3, 3], [2, 3], [2, 1], [1, 1], [1, 3], [0, 3], [0, 0]]] },
    };
    const [x, y] = labelPoint(u)!;
    // Centroid (1.5, 9.5/7 = 1.357) is in the notch; the label goes in one arm at that height
    expect(y).toBeCloseTo(9.5 / 7, 6);
    expect(x < 1 || x > 2).toBe(true);
  });
});

describe("evacuation status set by Planning", () => {
  const t0 = new Date("2026-07-15T21:00:00Z");

  it("upsertTier sets, replaces and clears one neighbourhood's status", () => {
    let recs: EvacTierRecord[] = [];
    recs = upsertTier(recs, "Rhatigan Ridge", "Watch", t0);
    recs = upsertTier(recs, "Terwillegar Towne", "Order", t0);
    recs = upsertTier(recs, "Rhatigan Ridge", "Alert", t0);
    expect(recs).toEqual([
      { neighbourhood: "Terwillegar Towne", tier: "Order", setAt: t0.toISOString() },
      { neighbourhood: "Rhatigan Ridge", tier: "Alert", setAt: t0.toISOString() },
    ]);
    expect(upsertTier(recs, "Rhatigan Ridge", null)).toHaveLength(1);
  });

  it("planningZones groups by tier, most severe first, with the polygons", () => {
    const comms = collection([neighbourhood("A", 0), neighbourhood("B", 0.1), neighbourhood("C", 0.2)]);
    const recs: EvacTierRecord[] = [
      { neighbourhood: "C", tier: "Watch", setAt: "x" },
      { neighbourhood: "A", tier: "Order", setAt: "x" },
      { neighbourhood: "Unknown", tier: "Order", setAt: "x" },
    ];
    const zones = planningZones(recs, comms);
    expect(zones.map((z) => [z.tier, z.neighbourhoods])).toEqual([
      ["Order", ["A", "Unknown"]],
      ["Watch", ["C"]],
    ]);
    expect(zones[0].features.map(featureName)).toEqual(["A"]); // no polygon for "Unknown"
    const fc = planningZonesToGeoJSON(zones);
    expect(fc.features.map((f) => f.properties)).toEqual([
      { neighbourhood: "A", evac_tier: "Order", map_label: "ORDER", set_by: "Planning", set_at: "x", set_at_local: null },
      { neighbourhood: "C", evac_tier: "Watch", map_label: "WATCH", set_by: "Planning", set_at: "x", set_at_local: null },
    ]);
  });

  it("nothing is generated: no records, no zones, whatever the fire does", () => {
    expect(planningZones([], edmonton)).toEqual([]);
  });
});

// ── Round-3 field test (Claude in Chrome tester, 2026-10-13) ─────────────────

import { evacStatusExport, firePoints } from "./evacZones";

describe("multi-day arrival times (R7)", () => {
  // Three 24 h days; each day's cell `t` counts from that day's start and the cells carried
  // from earlier days start each day at t = 0 (the API's multi-day frames)
  const cell = (lat: number, t: number) => ({ lat, lng: -113.5, intensity: 100, fuel: "O1a", t });
  const days: SimulationFrame[] = [
    frame(12, [], { day: 1, burned_cells: [cell(53.50, 0), cell(53.51, 300)] }),
    frame(24, [], { day: 1, burned_cells: [cell(53.50, 0), cell(53.51, 300), cell(53.52, 900)] }),
    frame(36, [], { day: 2, burned_cells: [cell(53.50, 0), cell(53.51, 0), cell(53.52, 0), cell(53.53, 685)] }),
    frame(48, [], { day: 2, burned_cells: [cell(53.50, 0), cell(53.51, 0), cell(53.52, 0), cell(53.53, 685)] }),
    frame(72, [], { day: 3, burned_cells: [cell(53.50, 0), cell(53.51, 0), cell(53.52, 0), cell(53.53, 0), cell(53.54, 60)] }),
  ];

  it("keeps each cell's true time across days (not the run start)", () => {
    const h = new Map(firePoints(days).map((p) => [p.lat.toFixed(2), p.h]));
    expect(h.get("53.50")).toBe(0);
    expect(h.get("53.51")).toBe(5);
    expect(h.get("53.52")).toBe(15);
    expect(h.get("53.53")).toBeCloseTo(24 + 685 / 60, 6); // day 2, 11:25 after its start
    expect(h.get("53.54")).toBe(49); // day 3, 1 h after its start
  });

  it("gives the same times from incremental frames", () => {
    const incremental: SimulationFrame[] = [
      days[0],
      { ...days[1], burned_cells: [cell(53.52, 900)], cells_offset: 2 },
      days[2], // a day boundary is sent whole
      { ...days[3], burned_cells: [], cells_offset: 4 },
      days[4],
    ];
    expect(firePoints(incremental).map((p) => p.h)).toEqual(firePoints(days).map((p) => p.h));
  });

  it("shows the day for a later calendar day: 'D2 16:25', not the start time", () => {
    const start = new Date("2026-10-13T04:57:00-06:00");
    expect(arrivalLabel(5, start)).toBe("Fire within 500 m by 09:57");
    expect(arrivalLabel(24 + 685 / 60, start)).toBe("Fire within 500 m by D2 16:22");
    expect(arrivalLabel(49, start)).toBe("Fire within 500 m by D3 05:57");
  });

  it("prefixes day 1 too in a multi-day run, so '10:00' isn't read without a day", () => {
    const start = new Date("2026-10-13T04:57:00-06:00");
    expect(arrivalLabel(5, start, 500, true)).toBe("Fire within 500 m by D1 09:57");
    expect(arrivalTime(5, start, true)).toBe("D1 09:57");
    expect(arrivalTime(5, start)).toBe("09:57");
    expect(arrivalTime(5, null, true)).toBe("T+5:00");
  });
});

describe("evacuation-status export times (R2)", () => {
  it("adds America/Edmonton local time with its offset beside UTC", () => {
    const zones = planningZones([{ neighbourhood: "A", tier: "Order", setAt: "2026-10-13T15:12:00.000Z" }], collection([neighbourhood("A", 0.05)]));
    const out = evacStatusExport(zones, "Test", Date.parse("2026-10-13T16:00:00Z"));
    expect(out.features[0].properties).toMatchObject({ set_at: "2026-10-13T15:12:00.000Z", set_at_local: "2026-10-13T09:12:00-06:00" });
    expect(out.metadata).toMatchObject({
      exported_at: "2026-10-13T16:00:00.000Z",
      exported_at_local: "2026-10-13T10:00:00-06:00",
      time_zone: "America/Edmonton",
    });
  });
});
