/**
 * Ensemble ("range of outcomes") rasters: decoding, arrival lines and extents.
 *
 * GET /api/v1/simulations/{id}/ensemble returns, once complete, P10/P50/P90 arrival rasters
 * (base64 little-endian int16, whole minutes after ignition, -1 = fewer than that share of
 * members reached the cell; row-major from the north-west corner) and burn probability
 * (base64 uint8 percent). P10 is the early arrival: one member in ten
 * reaches the cell this early or earlier.
 *
 * Arrival lines are contoured with marching squares on the cell-centre lattice, so any
 * interval works independently of the snapshot spacing (design spec §3.3). Only the window
 * of cells any member reached is scanned (a 4 h fire covers well under 1 % of the city grid).
 *
 * Engine coordinates are [lat, lng]; GeoJSON coordinates (returned here) are [lng, lat].
 */

import { formatClock, formatClockAt, formatElapsed } from "./time";

// ── API shapes ───────────────────────────────────────────────────────────────

export interface EnsembleMember {
  wind_dir_offset_deg: number;
  wind_speed_factor: number;
  ffmc: number;
  dmc: number;
  dc: number;
  grass_cure: number;
  fmc: number;
  ros_multiplier: number;
  area_ha: number;
}

export interface EnsembleResponse {
  status: "pending" | "running" | "completed" | "failed";
  done: number;
  total: number;
  error?: string | null;
  rows?: number;
  cols?: number;
  lat_min?: number;
  lat_max?: number;
  lng_min?: number;
  lng_max?: number;
  duration_minutes?: number;
  encoding?: string;
  arrival?: { p10: string; p50: string; p90: string };
  burn_probability?: string;
  area_ha?: { min: number; p50: number; max: number };
  members?: EnsembleMember[];
  note?: string;
}

/** Decoded ensemble: typed rasters plus the window of cells any member reached. */
export interface EnsembleGrids {
  rows: number;
  cols: number;
  latMin: number;
  latMax: number;
  lngMin: number;
  lngMax: number;
  durationMinutes: number;
  /** Arrival minutes, -1 = not reached at that percentile */
  p10: Int16Array;
  p50: Int16Array;
  p90: Int16Array;
  /** Burn probability, percent of members (0-100) */
  prob: Uint8Array;
  /** Inclusive window [r0..r1] x [c0..c1] of cells with probability > 0 (null: nothing burned) */
  window: { r0: number; r1: number; c0: number; c1: number } | null;
  cellAreaHa: number;
  areaHa: { min: number; p50: number; max: number };
  members: EnsembleMember[];
  note: string;
}

// ── Decoding ─────────────────────────────────────────────────────────────────

/** Base64 to bytes. */
export function base64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

const LITTLE_ENDIAN = new Uint8Array(new Uint16Array([1]).buffer)[0] === 1;

/** Base64 of little-endian int16 to an Int16Array (n values). */
export function decodeInt16LE(b64: string, n?: number): Int16Array {
  const bytes = base64ToBytes(b64);
  const len = n ?? bytes.length >> 1;
  if (bytes.length < len * 2) throw new Error(`int16 raster too short: ${bytes.length} bytes for ${len} cells`);
  if (LITTLE_ENDIAN) return new Int16Array(bytes.buffer, 0, len);
  const view = new DataView(bytes.buffer);
  const out = new Int16Array(len);
  for (let i = 0; i < len; i++) out[i] = view.getInt16(i * 2, true);
  return out;
}

const M_PER_DEG = 111_320;

/** Decode a completed ensemble response (null if it is not complete). */
export function decodeEnsemble(resp: EnsembleResponse): EnsembleGrids | null {
  if (resp.status !== "completed" || !resp.arrival || !resp.burn_probability || !resp.rows || !resp.cols) return null;
  const { rows, cols } = resp;
  const n = rows * cols;
  const latMin = resp.lat_min!, latMax = resp.lat_max!, lngMin = resp.lng_min!, lngMax = resp.lng_max!;
  const prob = base64ToBytes(resp.burn_probability);
  if (prob.length < n) throw new Error(`burn probability raster too short: ${prob.length} for ${n} cells`);
  let r0 = rows, r1 = -1, c0 = cols, c1 = -1;
  for (let r = 0; r < rows; r++) {
    const off = r * cols;
    for (let c = 0; c < cols; c++) {
      if (prob[off + c] === 0) continue;
      if (r < r0) r0 = r;
      if (r > r1) r1 = r;
      if (c < c0) c0 = c;
      if (c > c1) c1 = c;
    }
  }
  const cellLat = (latMax - latMin) / rows;
  const cellLng = (lngMax - lngMin) / cols;
  const cellAreaHa =
    (cellLat * M_PER_DEG * cellLng * M_PER_DEG * Math.cos((((latMax + latMin) / 2) * Math.PI) / 180)) / 1e4;
  return {
    rows, cols, latMin, latMax, lngMin, lngMax,
    durationMinutes: resp.duration_minutes ?? 0,
    p10: decodeInt16LE(resp.arrival.p10, n),
    p50: decodeInt16LE(resp.arrival.p50, n),
    p90: decodeInt16LE(resp.arrival.p90, n),
    prob: prob.subarray(0, n),
    window: r1 < 0 ? null : { r0, r1, c0, c1 },
    cellAreaHa,
    areaHa: resp.area_ha ?? { min: 0, p50: 0, max: 0 },
    members: resp.members ?? [],
    note: resp.note ?? "",
  };
}

// ── Arrival lines (marching squares) ────────────────────────────────────────

/** A raster on the ensemble grid: cell (r, c) is values[r * cols + c]. */
export interface ArrivalRaster {
  values: Int16Array;
  rows: number;
  cols: number;
  window: { r0: number; r1: number; c0: number; c1: number } | null;
}

/**
 * Contour of {cells reached by `level` minutes} (0 <= v <= level) as closed rings in grid
 * coordinates ([x, y] = [column, row] of cell centres). Edge crossings are interpolated
 * linearly between two reached arrival times; next to a cell that is never reached the
 * crossing is put half-way. The window is padded with unreached cells, so every ring closes.
 */
export function contourRings(raster: ArrivalRaster, level: number): number[][][] {
  const { values, rows, cols, window } = raster;
  if (!window) return [];
  // Lattice of cell centres from (r0-1, c0-1) to (r1+1, c1+1); outside the grid = unreached
  const x0 = window.c0 - 1, y0 = window.r0 - 1;
  const W = window.c1 - window.c0 + 3; // lattice columns
  const H = window.r1 - window.r0 + 3; // lattice rows
  const val = (lx: number, ly: number): number => {
    const r = ly + y0, c = lx + x0;
    if (r < 0 || c < 0 || r >= rows || c >= cols) return -1;
    return values[r * cols + c];
  };
  // Inside-flags and values on the lattice, computed once
  const v = new Float64Array(W * H);
  const inside = new Uint8Array(W * H);
  for (let ly = 0; ly < H; ly++) {
    for (let lx = 0; lx < W; lx++) {
      const x = val(lx, ly);
      v[ly * W + lx] = x;
      inside[ly * W + lx] = x >= 0 && x <= level ? 1 : 0;
    }
  }
  // Edge crossing point: horizontal edge (lx,ly)-(lx+1,ly) key 2k, vertical (lx,ly)-(lx,ly+1) key 2k+1
  const pts = new Map<number, [number, number]>();
  const frac = (a: number, b: number): number => {
    // a, b: lattice indices, exactly one inside. Fraction of the way from a to b.
    const va = v[a], vb = v[b];
    if (va < 0 || vb < 0) return 0.5;
    if (va === vb) return 0.5;
    const t = (level - va) / (vb - va);
    return t < 0 ? 0 : t > 1 ? 1 : t;
  };
  const point = (key: number): void => {
    if (pts.has(key)) return;
    const k = key >> 1;
    const lx = k % W, ly = (k - lx) / W;
    if (key & 1) {
      const t = frac(k, k + W);
      pts.set(key, [lx + x0, ly + y0 + t]);
    } else {
      const t = frac(k, k + 1);
      pts.set(key, [lx + x0 + t, ly + y0]);
    }
  };
  // Segments as pairs of edge keys
  const segA: number[] = [];
  const segB: number[] = [];
  const add = (a: number, b: number) => {
    point(a);
    point(b);
    segA.push(a);
    segB.push(b);
  };
  for (let ly = 0; ly < H - 1; ly++) {
    for (let lx = 0; lx < W - 1; lx++) {
      const k = ly * W + lx;
      const tl = inside[k], tr = inside[k + 1], bl = inside[k + W], br = inside[k + W + 1];
      const code = (tl << 3) | (tr << 2) | (br << 1) | bl;
      if (code === 0 || code === 15) continue;
      const T = 2 * k, L = 2 * k + 1, B = 2 * (k + W), R = 2 * (k + 1) + 1;
      switch (code) {
        case 1: add(L, B); break;
        case 2: add(B, R); break;
        case 3: add(L, R); break;
        case 4: add(T, R); break;
        case 5: add(T, R); add(L, B); break; // saddle: reached corners kept apart
        case 6: add(T, B); break;
        case 7: add(T, L); break;
        case 8: add(T, L); break;
        case 9: add(T, B); break;
        case 10: add(T, L); add(B, R); break; // saddle
        case 11: add(T, R); break;
        case 12: add(L, R); break;
        case 13: add(R, B); break;
        case 14: add(L, B); break;
      }
    }
  }
  // Stitch: every crossing point belongs to exactly two segments (closed rings)
  const bySeg = new Map<number, number[]>();
  for (let i = 0; i < segA.length; i++) {
    for (const key of [segA[i], segB[i]]) {
      const list = bySeg.get(key);
      if (list) list.push(i);
      else bySeg.set(key, [i]);
    }
  }
  const used = new Uint8Array(segA.length);
  const rings: number[][][] = [];
  for (let s = 0; s < segA.length; s++) {
    if (used[s]) continue;
    used[s] = 1;
    const start = segA[s];
    const ring: number[][] = [pts.get(start)!];
    let key = segB[s];
    let seg = s;
    for (let guard = 0; guard <= segA.length; guard++) {
      ring.push(pts.get(key)!);
      if (key === start) break;
      const next = (bySeg.get(key) ?? []).find((i) => i !== seg && !used[i]);
      if (next === undefined) break;
      used[next] = 1;
      key = segA[next] === key ? segB[next] : segA[next];
      seg = next;
    }
    if (ring.length >= 3) rings.push(ring);
  }
  return rings;
}

/** Grid coordinates (column, row of cell centres) to [lng, lat]. */
export function gridToLngLat(
  g: Pick<EnsembleGrids, "rows" | "cols" | "latMin" | "latMax" | "lngMin" | "lngMax">,
  x: number,
  y: number,
): [number, number] {
  const cellLat = (g.latMax - g.latMin) / g.rows;
  const cellLng = (g.lngMax - g.lngMin) / g.cols;
  return [g.lngMin + (x + 0.5) * cellLng, g.latMax - (y + 0.5) * cellLat];
}

/** Ring area in grid cells (absolute shoelace). */
export function ringArea(ring: number[][]): number {
  let a = 0;
  for (let i = 1; i < ring.length; i++) a += ring[i - 1][0] * ring[i][1] - ring[i][0] * ring[i - 1][1];
  return Math.abs(a) / 2;
}

/** One pass of Chaikin corner cutting on a closed ring (softens the cell staircase). */
export function chaikin(ring: number[][]): number[][] {
  const n = ring.length - 1; // closed: last == first
  if (n < 3) return ring;
  const out: number[][] = [];
  for (let i = 0; i < n; i++) {
    const [x0, y0] = ring[i];
    const [x1, y1] = ring[i + 1];
    out.push([0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1], [0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1]);
  }
  out.push(out[0]);
  return out;
}

/**
 * Contour rings of a percentile raster at `minutes`, as [lng, lat] rings for the map.
 * Islands and holes smaller than `minCells` cells (single unburnable cells, specks reached by
 * a few members) are dropped, except the largest ring, and the rings are smoothed once.
 */
export function arrivalRings(
  g: EnsembleGrids,
  which: "p10" | "p50" | "p90",
  minutes: number,
  minCells = 4,
): number[][][] {
  const rings = contourRings({ values: g[which], rows: g.rows, cols: g.cols, window: g.window }, minutes);
  if (rings.length === 0) return [];
  const areas = rings.map(ringArea);
  const largest = areas.indexOf(Math.max(...areas));
  return rings
    .filter((_, i) => i === largest || areas[i] >= minCells)
    .map((ring) => chaikin(ring).map(([x, y]) => gridToLngLat(g, x, y)));
}

/** Area (ha) of the cells a percentile raster has reached by `minutes`. */
export function areaByMinutes(g: EnsembleGrids, which: "p10" | "p50" | "p90", minutes: number): number {
  if (!g.window) return 0;
  const a = g[which];
  const { r0, r1, c0, c1 } = g.window;
  let n = 0;
  for (let r = r0; r <= r1; r++) {
    const off = r * g.cols;
    for (let c = c0; c <= c1; c++) {
      const x = a[off + c];
      if (x >= 0 && x <= minutes) n++;
    }
  }
  return n * g.cellAreaHa;
}

/**
 * Interval between arrival lines: the smallest of 15 min, 30 min, 1 h, 2 h, 3 h, 6 h, 12 h
 * and 24 h that gives at most `maxLines` lines over the run (design spec §3.3).
 */
export function arrivalInterval(durationMinutes: number, maxLines = 8): number {
  for (const step of [15, 30, 60, 120, 180, 360, 720, 1440]) {
    if (Math.floor(durationMinutes / step) <= maxLines) return step;
  }
  return 1440;
}

/**
 * Arrival line levels (minutes after ignition) every interval up to the run end. With a
 * scenario start they fall on round clock times (14:00, 14:30, ... in America/Edmonton), else
 * on whole intervals after ignition.
 */
export function arrivalLevels(durationMinutes: number, start: Date | null = null, maxLines = 8): number[] {
  const step = arrivalInterval(durationMinutes, maxLines);
  let first = step;
  if (start) {
    const [hh, mm] = formatClock(start).split(":").map(Number);
    const secs = start.getUTCSeconds() / 60;
    const ofDay = hh * 60 + mm + secs;
    first = step - (ofDay % step);
    if (first < 1e-6) first = step;
  }
  const out: number[] = [];
  for (let m = first; m <= durationMinutes + 1e-6; m += step) out.push(+m.toFixed(4));
  return out;
}

/** "14:45" with a scenario start, else "T+1:05". */
export function clockLabel(minutes: number, start: Date | null): string {
  return start ? formatClockAt(start, minutes / 60) : formatElapsed(minutes / 60);
}

/** One P10 arrival line with its label anchor. */
export interface ArrivalLine {
  minutes: number;
  label: string;
  rings: number[][][];
  /** Label anchor [lng, lat]: on the longest ring, farthest from `origin` (the head side) */
  anchor: [number, number] | null;
}

function ringLength(ring: number[][]): number {
  let s = 0;
  for (let i = 1; i < ring.length; i++) s += Math.hypot(ring[i][0] - ring[i - 1][0], ring[i][1] - ring[i - 1][1]);
  return s;
}

/** The cell the fire reaches first (the ignition), as {lat, lng}. */
export function earliestCell(g: EnsembleGrids, which: "p10" | "p50" | "p90" = "p10"): { lat: number; lng: number } | null {
  if (!g.window) return null;
  const a = g[which];
  const { r0, r1, c0, c1 } = g.window;
  let best = Infinity, br = -1, bc = -1;
  for (let r = r0; r <= r1; r++) {
    for (let c = c0; c <= c1; c++) {
      const m = a[r * g.cols + c];
      if (m >= 0 && m < best) { best = m; br = r; bc = c; }
    }
  }
  if (br < 0) return null;
  const [lng, lat] = gridToLngLat(g, bc, br);
  return { lat, lng };
}

/**
 * P10 (early) arrival lines at `levels`, labelled in clock time. Labels sit where
 * each line is farthest from `origin` (default: the earliest cell, i.e. the ignition), so
 * they line up along the head.
 */
export function arrivalLines(
  g: EnsembleGrids,
  levels: number[],
  start: Date | null,
  origin: { lat: number; lng: number } | null = earliestCell(g),
  which: "p10" | "p50" | "p90" = "p10",
): ArrivalLine[] {
  const out: ArrivalLine[] = [];
  for (const minutes of levels) {
    const rings = arrivalRings(g, which, minutes);
    if (rings.length === 0) continue;
    let longest = rings[0];
    let best = ringLength(longest);
    for (const r of rings) {
      const len = ringLength(r);
      if (len > best) { best = len; longest = r; }
    }
    let anchor: [number, number] | null = null;
    if (origin) {
      const kx = Math.cos((origin.lat * Math.PI) / 180);
      let far = -1;
      for (const [lng, lat] of longest) {
        const d = ((lng - origin.lng) * kx) ** 2 + (lat - origin.lat) ** 2;
        if (d > far) { far = d; anchor = [lng, lat]; }
      }
    } else {
      for (const p of longest) if (!anchor || p[1] > anchor[1]) anchor = [p[0], p[1]];
    }
    out.push({ minutes, label: clockLabel(minutes, start), rings, anchor });
  }
  return out;
}

/** Cells reached at a percentile as fire points (lat, lng, hours) for the neighbourhood check. */
export function arrivalPoints(g: EnsembleGrids, which: "p10" | "p50" | "p90" = "p10"): { lat: number; lng: number; h: number }[] {
  const out: { lat: number; lng: number; h: number }[] = [];
  if (!g.window) return out;
  const a = g[which];
  const { r0, r1, c0, c1 } = g.window;
  for (let r = r0; r <= r1; r++) {
    for (let c = c0; c <= c1; c++) {
      const m = a[r * g.cols + c];
      if (m < 0) continue;
      const [lng, lat] = gridToLngLat(g, c, r);
      out.push({ lat, lng, h: m / 60 });
    }
  }
  return out.sort((p, q) => p.h - q.h);
}

// ── Burn probability ramp ───────────────────────────────────────────────────

/**
 * Sequential single-hue ramp for burn probability (light to dark purple, ColorBrewer Purples).
 * Kept off the fire (inferno) ramp, the evacuation blue and the fuel colours; a simple
 * sequential ramp reads best under time pressure (EOC best-practice review).
 */
export const PROB_STOPS: Array<[number, [number, number, number]]> = [
  [1, [239, 237, 245]],
  [25, [188, 189, 220]],
  [50, [128, 125, 186]],
  [75, [84, 39, 143]],
  [100, [63, 0, 125]],
];

/** RGBA for a burn probability percent; transparent at 0. */
export function probColor(p: number): [number, number, number, number] {
  if (p <= 0) return [0, 0, 0, 0];
  let lo = PROB_STOPS[0], hi = PROB_STOPS[PROB_STOPS.length - 1];
  for (let i = 0; i + 1 < PROB_STOPS.length; i++) {
    if (p >= PROB_STOPS[i][0] && p <= PROB_STOPS[i + 1][0]) { lo = PROB_STOPS[i]; hi = PROB_STOPS[i + 1]; break; }
  }
  const t = hi[0] === lo[0] ? 0 : Math.min(1, Math.max(0, (p - lo[0]) / (hi[0] - lo[0])));
  const c = lo[1].map((v, k) => Math.round(v + (hi[1][k] - v) * t)) as [number, number, number];
  return [c[0], c[1], c[2], Math.round(150 + 80 * Math.min(1, p / 100))];
}

/** CSS colour of the ramp at a percent (legends). */
export function probCss(p: number): string {
  const [r, g, b] = probColor(p);
  return `rgb(${r}, ${g}, ${b})`;
}

/**
 * RGBA pixels of the burn probability window (one pixel per cell) and the image corners
 * ([lng, lat], clockwise from the top left) for a MapLibre image source.
 */
export function probabilityPixels(g: EnsembleGrids): {
  width: number;
  height: number;
  data: Uint8ClampedArray;
  coordinates: [[number, number], [number, number], [number, number], [number, number]];
} | null {
  if (!g.window) return null;
  const { r0, r1, c0, c1 } = g.window;
  const width = c1 - c0 + 1, height = r1 - r0 + 1;
  const data = new Uint8ClampedArray(width * height * 4);
  for (let r = r0; r <= r1; r++) {
    for (let c = c0; c <= c1; c++) {
      const [R, G, B, A] = probColor(g.prob[r * g.cols + c]);
      const i = ((r - r0) * width + (c - c0)) * 4;
      data[i] = R; data[i + 1] = G; data[i + 2] = B; data[i + 3] = A;
    }
  }
  const cellLat = (g.latMax - g.latMin) / g.rows;
  const cellLng = (g.lngMax - g.lngMin) / g.cols;
  const west = g.lngMin + c0 * cellLng, east = g.lngMin + (c1 + 1) * cellLng;
  const north = g.latMax - r0 * cellLat, south = g.latMax - (r1 + 1) * cellLat;
  return {
    width, height, data,
    coordinates: [[west, north], [east, north], [east, south], [west, south]],
  };
}

// ── GeoJSON ─────────────────────────────────────────────────────────────────

/** What MapView draws for an ensemble (built in App from the decoded grids). */
export interface EnsembleMapLayers {
  /** P10 arrival lines at the interval levels, labelled in clock time */
  lines: ArrivalLine[];
  /** Selected timeline time, minutes after ignition: lines after it are drawn as projected */
  selectedMinutes: number;
  /** P10 and P50 extents at the selected time */
  nowP10: number[][][];
  nowP50: number[][][];
  /** P90 footprint at the end of the run */
  p90: number[][][];
  prob: ReturnType<typeof probabilityPixels>;
  show: { lines: boolean; p50: boolean; p90: boolean; prob: boolean };
}

/** Rings as one MultiLineString feature (closed rings, [lng, lat]). */
export function ringsFeature(rings: number[][][], properties: Record<string, unknown>): GeoJSON.Feature {
  return { type: "Feature", properties, geometry: { type: "MultiLineString", coordinates: rings } };
}
