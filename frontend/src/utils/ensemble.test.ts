import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { gunzipSync } from "node:zlib";
import { describe, expect, it } from "vitest";
import {
  areaByMinutes,
  arrivalInterval,
  arrivalLevels,
  arrivalLines,
  arrivalPoints,
  arrivalRings,
  base64ToBytes,
  chaikin,
  clockLabel,
  contourRings,
  decodeEnsemble,
  decodeInt16LE,
  probColor,
  probabilityPixels,
  ringArea,
  type ArrivalRaster,
  type EnsembleResponse,
} from "./ensemble";
import { arrivalsToGeoJSON, neighbourhoodArrivalsFromPoints } from "./evacZones";

/** Base64 of little-endian int16 values (as the API encodes them). */
function encodeInt16(values: number[]): string {
  const buf = Buffer.alloc(values.length * 2);
  values.forEach((v, i) => buf.writeInt16LE(v, i * 2));
  return buf.toString("base64");
}

/** A raster with every cell given by f(r, c), window = cells >= 0 (as decodeEnsemble does). */
function raster(rows: number, cols: number, f: (r: number, c: number) => number): ArrivalRaster {
  const values = new Int16Array(rows * cols);
  let r0 = rows, r1 = -1, c0 = cols, c1 = -1;
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const v = f(r, c);
      values[r * cols + c] = v;
      if (v >= 0) {
        r0 = Math.min(r0, r); r1 = Math.max(r1, r); c0 = Math.min(c0, c); c1 = Math.max(c1, c);
      }
    }
  }
  return { values, rows, cols, window: r1 < 0 ? null : { r0, r1, c0, c1 } };
}

const closed = (ring: number[][]) => ring[0][0] === ring[ring.length - 1][0] && ring[0][1] === ring[ring.length - 1][1];

describe("decoding", () => {
  it("decodes little-endian int16 including -1 and large minutes", () => {
    const vals = [-1, 0, 1, 255, 256, 1439, 32767, -1];
    expect(Array.from(decodeInt16LE(encodeInt16(vals)))).toEqual(vals);
  });

  it("decodes base64 bytes", () => {
    expect(Array.from(base64ToBytes(Buffer.from([0, 10, 100, 255]).toString("base64")))).toEqual([0, 10, 100, 255]);
  });

  it("decodeEnsemble finds the window of reached cells and the cell area", () => {
    const rows = 4, cols = 5;
    const p = new Array(rows * cols).fill(-1);
    p[1 * cols + 2] = 10;
    p[2 * cols + 3] = 20;
    const prob = Buffer.from(p.map((v) => (v >= 0 ? 50 : 0)));
    const resp: EnsembleResponse = {
      status: "completed", done: 5, total: 5, rows, cols,
      lat_min: 53.5, lat_max: 53.5 + rows * 0.0002, lng_min: -113.5, lng_max: -113.5 + cols * 0.0003,
      duration_minutes: 60,
      arrival: { p10: encodeInt16(p), p50: encodeInt16(p), p90: encodeInt16(p) },
      burn_probability: prob.toString("base64"),
      area_ha: { min: 1, p50: 2, max: 3 }, members: [], note: "uncalibrated",
    };
    const g = decodeEnsemble(resp)!;
    expect(g.window).toEqual({ r0: 1, r1: 2, c0: 2, c1: 3 });
    expect(g.p10[1 * cols + 2]).toBe(10);
    // 0.0002 deg lat x 0.0003 deg lng at 53.5 N
    expect(g.cellAreaHa).toBeCloseTo((0.0002 * 111320 * 0.0003 * 111320 * Math.cos((53.5004 * Math.PI) / 180)) / 1e4, 6);
    expect(areaByMinutes(g, "p10", 15)).toBeCloseTo(g.cellAreaHa, 9);
    expect(areaByMinutes(g, "p10", 60)).toBeCloseTo(2 * g.cellAreaHa, 9);
    expect(decodeEnsemble({ status: "running", done: 2, total: 5 })).toBeNull();
    expect(g.membersFailed).toBe(0);
    expect(decodeEnsemble({ ...resp, members_ok: 4, members_failed: 1, failed: [{ member: 2, error: "x" }] })!.membersFailed).toBe(1);
  });
});

describe("contourRings (marching squares)", () => {
  // A cone: arrival = 10 min per cell of distance from (20, 20), out to 15 cells
  const cone = raster(41, 41, (r, c) => {
    const d = Math.hypot(r - 20, c - 20);
    return d <= 15 ? Math.round(d * 10) : -1;
  });

  it("gives one closed ring at the expected radius for each level", () => {
    for (const level of [30, 60, 100, 140]) {
      const rings = contourRings(cone, level);
      expect(rings).toHaveLength(1);
      expect(closed(rings[0])).toBe(true);
      const radius = level / 10;
      for (const [x, y] of rings[0]) {
        expect(Math.abs(Math.hypot(x - 20, y - 20) - radius)).toBeLessThan(0.6);
      }
    }
  });

  it("at the outer edge (next to unreached cells) puts the line half-way", () => {
    const rings = contourRings(cone, 1000);
    expect(rings).toHaveLength(1);
    for (const [x, y] of rings[0]) {
      const d = Math.hypot(x - 20, y - 20);
      expect(d).toBeGreaterThan(14.4);
      expect(d).toBeLessThan(16.1);
    }
  });

  it("a single reached cell gives a diamond around it", () => {
    const one = raster(5, 5, (r, c) => (r === 2 && c === 2 ? 5 : -1));
    const rings = contourRings(one, 10);
    expect(rings).toHaveLength(1);
    expect(rings[0]).toHaveLength(5);
    const pts = rings[0].slice(0, 4).map(([x, y]) => `${x},${y}`).sort();
    expect(pts).toEqual(["1.5,2", "2,1.5", "2,2.5", "2.5,2"]);
  });

  it("two separate fires give two rings; nothing reached gives none", () => {
    const two = raster(10, 20, (r, c) => (r >= 3 && r <= 6 && ((c >= 2 && c <= 5) || (c >= 12 && c <= 15)) ? 10 : -1));
    expect(contourRings(two, 30)).toHaveLength(2);
    expect(contourRings(two, 5)).toHaveLength(0);
    expect(contourRings(raster(4, 4, () => -1), 30)).toHaveLength(0);
  });

  it("an unburned island inside the fire gives an inner ring", () => {
    const ring = raster(15, 15, (r, c) => {
      const d = Math.max(Math.abs(r - 7), Math.abs(c - 7));
      return d >= 2 && d <= 5 ? 20 : -1;
    });
    expect(contourRings(ring, 30)).toHaveLength(2);
  });

  it("diagonal (saddle) cells are kept as separate rings", () => {
    const diag = raster(6, 6, (r, c) => ((r === 2 && c === 2) || (r === 3 && c === 3) ? 10 : -1));
    const rings = contourRings(diag, 20);
    expect(rings).toHaveLength(2);
    for (const r of rings) expect(closed(r)).toBe(true);
  });

  it("fire reaching the grid edge still closes", () => {
    const edge = raster(5, 5, (_r, c) => (c <= 1 ? 10 : -1));
    const rings = contourRings(edge, 20);
    expect(rings).toHaveLength(1);
    expect(closed(rings[0])).toBe(true);
  });

  it("interpolates linearly between reached arrival times", () => {
    // 1-D ramp along columns: 0, 10, 20, ... ; level 25 crosses between c=2 (20) and c=3 (30)
    const ramp = raster(5, 8, (r, c) => (r >= 1 && r <= 3 ? c * 10 : -1));
    const xs = contourRings(ramp, 25)[0].filter(([, y]) => y === 2).map(([x]) => x);
    expect(xs).toContain(2.5);
  });
});

describe("arrival line levels", () => {
  it("chooses 15/30/60 min so there are at most 8 lines", () => {
    expect(arrivalInterval(120)).toBe(15);
    expect(arrivalInterval(240)).toBe(30);
    expect(arrivalInterval(480)).toBe(60);
    expect(arrivalInterval(24 * 60)).toBe(180);
    expect(arrivalLevels(240)).toEqual([30, 60, 90, 120, 150, 180, 210, 240]);
    expect(arrivalLevels(100)).toEqual([15, 30, 45, 60, 75, 90]);
  });

  it("falls on round clock times after the scenario start (America/Edmonton)", () => {
    // 13:01 MDT start, 4 h: 13:30, 14:00, ... 17:00
    const start = new Date("2026-07-15T13:01:00-06:00");
    const levels = arrivalLevels(240, start);
    expect(levels[0]).toBe(29);
    expect(levels.map((m) => clockLabel(m, start))).toEqual(["13:30", "14:00", "14:30", "15:00", "15:30", "16:00", "16:30", "17:00"]);
    // Starting on the hour: the first line is one interval later
    expect(arrivalLevels(240, new Date("2026-07-15T14:00:00-06:00"))[0]).toBe(30);
    // 2 h interval on a 12 h run from 07:00: on even hours, 08:00, 10:00, ...
    const l12 = arrivalLevels(720, new Date("2026-07-15T07:00:00-06:00"));
    expect(l12.map((m) => clockLabel(m, new Date("2026-07-15T07:00:00-06:00")))).toEqual(["08:00", "10:00", "12:00", "14:00", "16:00", "18:00"]);
  });

  it("keeps every line at most 8 per run", () => {
    for (const d of [60, 90, 240, 360, 600, 1440]) {
      expect(arrivalLevels(d, new Date("2026-07-15T13:07:00-06:00")).length).toBeLessThanOrEqual(8);
    }
  });
});

describe("display rings", () => {
  it("chaikin keeps the ring closed and inside the original bounds", () => {
    const sq = [[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]];
    const r = chaikin(sq);
    expect(r[0]).toEqual(r[r.length - 1]);
    expect(r).toHaveLength(9);
    for (const [x, y] of r) {
      expect(x).toBeGreaterThanOrEqual(0); expect(x).toBeLessThanOrEqual(4);
      expect(y).toBeGreaterThanOrEqual(0); expect(y).toBeLessThanOrEqual(4);
    }
    expect(ringArea(sq)).toBe(16);
  });
});

describe("burn probability ramp", () => {
  it("is transparent at 0 and darkens monotonically", () => {
    expect(probColor(0)[3]).toBe(0);
    let prevL = Infinity;
    for (const p of [1, 10, 25, 50, 75, 90, 100]) {
      const [r, g, b, a] = probColor(p);
      expect(a).toBeGreaterThan(0);
      const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
      expect(lum).toBeLessThan(prevL);
      prevL = lum;
    }
  });
});

// ── The recorded ensemble (record_fixture.py, 30 members on the Edmonton grid) ──
const recorded = JSON.parse(
  gunzipSync(readFileSync(resolve(__dirname, "../../tests/fixtures/ensemble.json.gz"))).toString("utf8"),
) as EnsembleResponse;

describe("recorded ensemble", () => {
  const t0 = performance.now();
  const g = decodeEnsemble(recorded)!;
  const decodeMs = performance.now() - t0;

  it("decodes quickly and finds a small window", () => {
    expect(g.rows * g.cols).toBeGreaterThan(400_000);
    expect(g.window).not.toBeNull();
    const w = g.window!;
    expect((w.r1 - w.r0 + 1) * (w.c1 - w.c0 + 1)).toBeLessThan(g.rows * g.cols * 0.05);
    console.info(`decode ${g.rows}x${g.cols} ensemble: ${decodeMs.toFixed(1)} ms`);
    expect(decodeMs).toBeLessThan(1000);
  });

  it("P10 <= P50 <= P90 wherever defined, and a later percentile is never defined without an earlier one", () => {
    let defined = 0;
    for (let i = 0; i < g.p10.length; i++) {
      const a = g.p10[i], b = g.p50[i], c = g.p90[i];
      if (b >= 0) {
        expect(a).toBeGreaterThanOrEqual(0);
        expect(a).toBeLessThanOrEqual(b);
      }
      if (c >= 0) {
        expect(b).toBeGreaterThanOrEqual(0);
        expect(b).toBeLessThanOrEqual(c);
      }
      if (a >= 0) {
        defined++;
        // P10 reached means at least 10 % of members reached the cell
        expect(g.prob[i]).toBeGreaterThanOrEqual(10);
        expect(a).toBeLessThanOrEqual(g.durationMinutes);
      }
    }
    expect(defined).toBeGreaterThan(100);
  });

  it("the P10 area is at least the median at every level, within the member range", () => {
    for (const m of arrivalLevels(g.durationMinutes)) {
      expect(areaByMinutes(g, "p10", m)).toBeGreaterThanOrEqual(areaByMinutes(g, "p50", m));
      expect(areaByMinutes(g, "p50", m)).toBeGreaterThanOrEqual(areaByMinutes(g, "p90", m));
    }
    const end = g.durationMinutes;
    expect(areaByMinutes(g, "p50", end)).toBeGreaterThanOrEqual(g.areaHa.min * 0.9);
    expect(areaByMinutes(g, "p50", end)).toBeLessThanOrEqual(g.areaHa.max * 1.1);
  });

  it("contours all P10 lines plus the P10/P50 extents quickly, labelled in clock time", () => {
    const start = new Date("2026-07-15T14:00:00-06:00");
    const t = performance.now();
    const lines = arrivalLines(g, arrivalLevels(g.durationMinutes, start), start, { lat: 53.4606, lng: -113.6596 });
    arrivalRings(g, "p10", 95);
    arrivalRings(g, "p50", 95);
    arrivalRings(g, "p90", g.durationMinutes);
    const ms = performance.now() - t;
    console.info(`contour ${lines.length} P10 lines + 3 extents: ${ms.toFixed(1)} ms`);
    expect(ms).toBeLessThan(1000);
    expect(lines.map((l) => l.label)).toEqual(["14:30", "15:00", "15:30", "16:00", "16:30", "17:00", "17:30", "18:00"]);
    // Display rings drop specks and holes below 4 cells (but keep the main ring)
    const raw = contourRings({ values: g.p10, rows: g.rows, cols: g.cols, window: g.window }, g.durationMinutes);
    const shown = arrivalRings(g, "p10", g.durationMinutes);
    expect(shown.length).toBeGreaterThan(0);
    expect(shown.length).toBeLessThanOrEqual(raw.length);
    for (const l of lines) {
      expect(l.anchor).not.toBeNull();
      for (const r of l.rings) expect(closed(r)).toBe(true);
    }
    // Each line lies in the grid bounds
    for (const [lng, lat] of lines[lines.length - 1].rings.flat()) {
      expect(lat).toBeGreaterThan(g.latMin);
      expect(lat).toBeLessThan(g.latMax);
      expect(lng).toBeGreaterThan(g.lngMin);
      expect(lng).toBeLessThan(g.lngMax);
    }
  });

  it("P10 (early) neighbourhood arrivals are no later than the median's and cover them", () => {
    const edmonton = JSON.parse(readFileSync(resolve(__dirname, "../../public/edmonton/neighbourhoods.geojson"), "utf8"));
    const p10 = neighbourhoodArrivalsFromPoints(arrivalPoints(g, "p10"), edmonton);
    const p50 = neighbourhoodArrivalsFromPoints(arrivalPoints(g, "p50"), edmonton);
    expect(p50.length).toBeGreaterThan(0);
    expect(p10.length).toBeGreaterThanOrEqual(p50.length);
    const worst = new Map(p10.map((a) => [a.name, a.arrivalHours]));
    for (const a of p50) {
      expect(worst.has(a.name)).toBe(true);
      expect(worst.get(a.name)!).toBeLessThanOrEqual(a.arrivalHours + 1e-9);
    }
    // Labelled as P10 on the map
    const fc = arrivalsToGeoJSON(p10, new Date("2026-07-15T14:00:00-06:00"), true);
    expect(String(fc.features[0].properties?.arrival_label)).toMatch(/^P10 \(early\): fire within 500 m by \d{2}:\d{2}$/);
  });

  it("gives arrival points and a probability image of the window", () => {
    const pts = arrivalPoints(g, "p10");
    expect(pts.length).toBeGreaterThan(100);
    expect(pts[0].h).toBeLessThanOrEqual(pts[pts.length - 1].h);
    const img = probabilityPixels(g)!;
    const w = g.window!;
    expect(img.width).toBe(w.c1 - w.c0 + 1);
    expect(img.data.length).toBe(img.width * img.height * 4);
  });
});
