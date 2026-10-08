/**
 * Critical assets and major roads: when the modelled fire reaches them.
 *
 * For each asset (hospital, school, substation, ...) the modelled fire's first arrival within
 * 500 m and inside it (0 m: the asset lies in a burned cell), for the single run and, when an
 * ensemble is in, its worst-credible (P10) arrival raster. Major roads: the first time the
 * modelled fire reaches each named road, and the reached stretches for the map. These are model
 * outputs in clock time, consistent with the Neighbourhoods card (utils/evacZones.ts); FireSim
 * makes no recommendation from them (Travis, 2026-10-08).
 *
 * Replaces the earlier "at-risk" flag (any vertex in a cell with burn probability >= 50 %, only
 * after a Monte Carlo run). Burn probability stays a map layer.
 *
 * Engine coordinates are [lat, lng]; GeoJSON coordinates are [lng, lat].
 */

import type { SimulationFrame } from "../types/simulation";
import { arrivalPoints, type EnsembleGrids } from "./ensemble";
import { ARRIVAL_BUFFER_M, featureName, firePoints, labelPoint, perimeterContains } from "./evacZones";
import { formatClockAt, formatElapsed } from "./time";

// ── Categories ──────────────────────────────────────────────────────────────

export type AssetCategory =
  | "eoc"
  | "hospital"
  | "fire_station"
  | "police"
  | "reception"
  | "seniors"
  | "school"
  | "water"
  | "power"
  | "transit"
  | "custom";

export type SymbolShape = "star" | "square" | "diamond" | "circle" | "hexagon" | "triangle";

export interface CategoryStyle {
  /** Card group heading */
  label: string;
  /** Map symbol: letter(s) on a shape, so categories differ without colour */
  letter: string;
  shape: SymbolShape;
}

/** Display order and map symbols. */
export const ASSET_CATEGORIES: Record<AssetCategory, CategoryStyle> = {
  eoc: { label: "Emergency operations centre", letter: "E", shape: "star" },
  hospital: { label: "Hospitals and care facilities", letter: "H", shape: "square" },
  fire_station: { label: "Fire stations", letter: "F", shape: "diamond" },
  police: { label: "Police stations", letter: "P", shape: "diamond" },
  reception: { label: "Recreation centres (candidate reception centres)", letter: "R", shape: "circle" },
  seniors: { label: "Seniors centres", letter: "Sr", shape: "circle" },
  school: { label: "Schools", letter: "S", shape: "circle" },
  water: { label: "Water and wastewater treatment", letter: "W", shape: "hexagon" },
  power: { label: "Power plants and substations", letter: "Pw", shape: "hexagon" },
  transit: { label: "LRT stations", letter: "T", shape: "square" },
  custom: { label: "Your layers", letter: "L", shape: "triangle" },
};

export const CATEGORY_ORDER = Object.keys(ASSET_CATEGORIES) as AssetCategory[];

export function isAssetCategory(v: unknown): v is AssetCategory {
  return typeof v === "string" && v in ASSET_CATEGORIES;
}

/** Short attribution of the bundled Edmonton layers for the map (the basemap credits OSM). */
export const EDMONTON_MAP_ATTRIBUTION = "City of Edmonton Open Data · Government of Alberta · Statistics Canada ODHF";

/** Sources and licences of the bundled Edmonton layers, for the card footer. */
export const EDMONTON_ATTRIBUTION =
  "City of Edmonton Open Data (Open Government Licence – City of Edmonton) · Government of Alberta continuing care list (Open Government Licence – Alberta) · Statistics Canada ODHF (Open Government Licence – Canada) · © OpenStreetMap contributors (ODbL)";

// ── Assets ──────────────────────────────────────────────────────────────────

export interface Asset {
  id: string;
  name: string;
  category: AssetCategory;
  detail: string | null;
  source: string;
  /** Every source of the asset (names, de-duplicated; the bundled layer's `sources`) */
  sources: string[];
  licence: string;
  /** Data-quality flag to show with the asset, e.g. "Unverified manual point" */
  verify: string | null;
  /** Symbol and label position, [lng, lat] */
  anchor: [number, number];
  /** Distances are measured to this (a point, or a site outline) */
  geometry: GeoJSON.Geometry;
}

/** Source names from a `sources` list ([{source, source_id}] or strings), primary first. */
export function sourceNames(list: unknown, primary: string): string[] {
  const out = [primary];
  if (Array.isArray(list)) {
    for (const s of list) {
      const n = typeof s === "string" ? s : s && typeof s === "object" ? (s as { source?: unknown }).source : null;
      if (typeof n === "string" && n && !out.includes(n)) out.push(n);
    }
  }
  return out;
}

/**
 * Assets from a GeoJSON layer: the bundled schema {name, category, source, source_id, licence,
 * sources, detail, verify, anchor}, or any user layer (points and polygons; lines are roads, not
 * assets).
 */
export function assetsFromGeoJSON(
  fc: GeoJSON.FeatureCollection | null | undefined,
  opts: { idPrefix?: string; category?: AssetCategory; source?: string; licence?: string } = {},
): Asset[] {
  const out: Asset[] = [];
  const seen = new Set<string>();
  (fc?.features ?? []).forEach((f, i) => {
    const g = f.geometry;
    if (!g || !["Point", "MultiPoint", "Polygon", "MultiPolygon"].includes(g.type)) return;
    const p = (f.properties ?? {}) as Record<string, unknown>;
    let anchor: [number, number] | null = null;
    if (Array.isArray(p.anchor) && p.anchor.length === 2) anchor = [Number(p.anchor[0]), Number(p.anchor[1])];
    else if (g.type === "Point") anchor = [g.coordinates[0], g.coordinates[1]];
    else if (g.type === "MultiPoint") anchor = g.coordinates[0] ? [g.coordinates[0][0], g.coordinates[0][1]] : null;
    else anchor = labelPoint(f);
    if (!anchor || !Number.isFinite(anchor[0]) || !Number.isFinite(anchor[1])) return;
    let id = `${opts.idPrefix ?? ""}${p.source_id ?? i}`;
    if (seen.has(id)) id = `${id}#${i}`;
    seen.add(id);
    const source = String(p.source ?? opts.source ?? "Your layer");
    out.push({
      id,
      name: featureName(f) === "Community" ? `Feature ${i + 1}` : featureName(f),
      category: isAssetCategory(p.category) ? p.category : opts.category ?? "custom",
      detail: p.detail != null ? String(p.detail) : p.type != null ? String(p.type) : null,
      source,
      sources: sourceNames(p.sources, source),
      licence: String(p.licence ?? opts.licence ?? ""),
      verify: typeof p.verify === "string" && p.verify ? p.verify : null,
      anchor,
      geometry: g,
    });
  });
  return out;
}

// ── Fire sources ────────────────────────────────────────────────────────────

export interface FirePoint {
  lat: number;
  lng: number;
  /** Hours after the scenario start */
  h: number;
}

/** Where and when the modelled fire burned, in one form for the single run and the ensemble. */
export interface FireSource {
  /** Burned cell centres (or sampled perimeter points), sorted by time */
  pts: FirePoint[];
  /** Cell size in metres (null: perimeter run without cells) */
  cellM: number | null;
  /** Perimeter runs: frames, so an asset swept over between sampled points still counts */
  perimeterFrames: SimulationFrame[] | null;
}

/** The single run's fire: burned cells timed by arrival, or perimeter points per frame. */
export function fireFromFrames(frames: SimulationFrame[]): FireSource | null {
  if (frames.length === 0) return null;
  const pts = firePoints(frames);
  if (pts.length === 0) return null;
  const hasCells = frames.some((f) => (f.burned_cells?.length ?? 0) > 0);
  if (!hasCells) return { pts, cellM: null, perimeterFrames: frames };
  // Cell size from the final area and cell count (grid runs report every burned cell)
  const area = frames[frames.length - 1].area_ha;
  const cellM = area > 0 ? Math.sqrt((area * 10_000) / pts.length) : null;
  return { pts, cellM, perimeterFrames: null };
}

/** The ensemble's arrival raster at a percentile (P10 = worst-credible) as a fire source. */
export function fireFromEnsemble(g: EnsembleGrids, which: "p10" | "p50" | "p90" = "p10"): FireSource | null {
  const pts = arrivalPoints(g, which);
  if (pts.length === 0) return null;
  return { pts, cellM: Math.sqrt(g.cellAreaHa * 10_000), perimeterFrames: null };
}

/**
 * Distance within which a fire point counts as "inside": half a cell diagonal (the asset lies
 * in that burned cell), or, for perimeter runs, the sampling half-step.
 */
export function insideToleranceM(fire: FireSource): number {
  return fire.cellM ? fire.cellM * Math.SQRT1_2 : 20;
}

// ── Spatial index ───────────────────────────────────────────────────────────

const M_PER_DEG = 111_320;

interface FireIndex {
  fire: FireSource;
  lat0: number;
  kx: number;
  bucketM: number;
  /** bucket key -> indices into pts, in time order */
  buckets: Map<string, number[]>;
  xs: Float64Array;
  ys: Float64Array;
  bbox: [number, number, number, number]; // metres [minX, minY, maxX, maxY]
}

function buildIndex(fire: FireSource, bucketM: number): FireIndex {
  const { pts } = fire;
  let latSum = 0;
  for (const p of pts) latSum += p.lat;
  const lat0 = latSum / pts.length;
  const kx = M_PER_DEG * Math.cos((lat0 * Math.PI) / 180);
  const xs = new Float64Array(pts.length);
  const ys = new Float64Array(pts.length);
  const buckets = new Map<string, number[]>();
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  pts.forEach((p, i) => {
    const x = p.lng * kx;
    const y = (p.lat - lat0) * M_PER_DEG;
    xs[i] = x;
    ys[i] = y;
    if (x < minX) minX = x;
    if (x > maxX) maxX = x;
    if (y < minY) minY = y;
    if (y > maxY) maxY = y;
    const key = `${Math.floor(x / bucketM)},${Math.floor(y / bucketM)}`;
    const b = buckets.get(key);
    if (b) b.push(i);
    else buckets.set(key, [i]);
  });
  return { fire, lat0, kx, bucketM, buckets, xs, ys, bbox: [minX, minY, maxX, maxY] };
}

/** Geometry in the index's metric frame: points, polylines and polygons (rings). */
interface Shape {
  points: number[][];
  lines: number[][][];
  polys: number[][][][];
  bbox: [number, number, number, number];
}

function toShape(idx: FireIndex, g: GeoJSON.Geometry): Shape {
  const xy = ([lng, lat]: number[]) => [lng * idx.kx, (lat - idx.lat0) * M_PER_DEG];
  const s: Shape = { points: [], lines: [], polys: [], bbox: [Infinity, Infinity, -Infinity, -Infinity] };
  switch (g.type) {
    case "Point": s.points.push(xy(g.coordinates)); break;
    case "MultiPoint": for (const c of g.coordinates) s.points.push(xy(c)); break;
    case "LineString": s.lines.push(g.coordinates.map(xy)); break;
    case "MultiLineString": for (const l of g.coordinates) s.lines.push(l.map(xy)); break;
    case "Polygon": s.polys.push(g.coordinates.map((r) => r.map(xy))); break;
    case "MultiPolygon": for (const p of g.coordinates) s.polys.push(p.map((r) => r.map(xy))); break;
    default: break;
  }
  const grow = ([x, y]: number[]) => {
    if (x < s.bbox[0]) s.bbox[0] = x;
    if (y < s.bbox[1]) s.bbox[1] = y;
    if (x > s.bbox[2]) s.bbox[2] = x;
    if (y > s.bbox[3]) s.bbox[3] = y;
  };
  s.points.forEach(grow);
  s.lines.forEach((l) => l.forEach(grow));
  s.polys.forEach((p) => p.forEach((r) => r.forEach(grow)));
  return s;
}

function ringContains(ring: number[][], x: number, y: number): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function segDist(x: number, y: number, x1: number, y1: number, x2: number, y2: number): number {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const len2 = dx * dx + dy * dy;
  const t = len2 > 0 ? Math.max(0, Math.min(1, ((x - x1) * dx + (y - y1) * dy) / len2)) : 0;
  return Math.hypot(x - (x1 + t * dx), y - (y1 + t * dy));
}

/** Distance in metres from (x, y) to the shape (0 inside a polygon). */
function shapeDist(s: Shape, x: number, y: number): number {
  let best = Infinity;
  for (const [px, py] of s.points) best = Math.min(best, Math.hypot(x - px, y - py));
  for (const l of s.lines) {
    for (let i = 1; i < l.length; i++) best = Math.min(best, segDist(x, y, l[i - 1][0], l[i - 1][1], l[i][0], l[i][1]));
  }
  for (const poly of s.polys) {
    if (ringContains(poly[0], x, y) && !poly.slice(1).some((h) => ringContains(h, x, y))) return 0;
    for (const ring of poly) {
      for (let i = 1; i < ring.length; i++) best = Math.min(best, segDist(x, y, ring[i - 1][0], ring[i - 1][1], ring[i][0], ring[i][1]));
    }
  }
  return best;
}

/** Fire point indices in the buckets that can lie within `r` of the bbox. */
function* candidates(idx: FireIndex, bbox: [number, number, number, number], r: number): Generator<number[]> {
  const b = idx.bucketM;
  const bx0 = Math.floor((bbox[0] - r) / b), bx1 = Math.floor((bbox[2] + r) / b);
  const by0 = Math.floor((bbox[1] - r) / b), by1 = Math.floor((bbox[3] + r) / b);
  for (let bx = bx0; bx <= bx1; bx++) {
    for (let by = by0; by <= by1; by++) {
      const list = idx.buckets.get(`${bx},${by}`);
      if (list) yield list;
    }
  }
}

function overlaps(idx: FireIndex, bbox: [number, number, number, number], r: number): boolean {
  const [x0, y0, x1, y1] = idx.bbox;
  return !(bbox[2] + r < x0 || bbox[0] - r > x1 || bbox[3] + r < y0 || bbox[1] - r > y1);
}

// ── Asset arrival ───────────────────────────────────────────────────────────

/** Hours after the start the modelled fire is first inside the asset (0 m) and within the buffer. */
export interface Reach {
  inside: number | null;
  near: number | null;
}

/**
 * First arrival of the modelled fire within `bufferM` of each asset, and inside it. Only
 * assets the fire comes within `bufferM` of are in the map.
 */
export function assetReach(fire: FireSource | null, assets: Asset[], bufferM: number = ARRIVAL_BUFFER_M): Map<string, Reach> {
  const out = new Map<string, Reach>();
  if (!fire || fire.pts.length === 0 || assets.length === 0) return out;
  const idx = buildIndex(fire, Math.max(bufferM, 100));
  const tol = insideToleranceM(fire);
  const { pts } = fire;
  for (const a of assets) {
    const shape = toShape(idx, a.geometry);
    if (!Number.isFinite(shape.bbox[0]) || !overlaps(idx, shape.bbox, bufferM)) {
      if (!fire.perimeterFrames) continue;
    }
    let near = Infinity;
    let inside = Infinity;
    for (const list of candidates(idx, shape.bbox, bufferM)) {
      for (const i of list) {
        const h = pts[i].h;
        if (h >= near && h >= inside) break; // lists are in time order
        const d = shapeDist(shape, idx.xs[i], idx.ys[i]);
        if (d <= bufferM && h < near) near = h;
        if (d <= tol && h < inside) inside = h;
      }
    }
    if (fire.perimeterFrames) {
      // Perimeter runs: the asset is inside once a perimeter contains its anchor
      const [lng, lat] = a.anchor;
      for (const f of fire.perimeterFrames) {
        if (f.time_hours >= inside) break;
        if (f.perimeter.length >= 3 && perimeterContains(f.perimeter, lat, lng)) {
          inside = f.time_hours;
          break;
        }
      }
      if (inside < near) near = inside;
    }
    if (Number.isFinite(near)) {
      out.set(a.id, { near, inside: Number.isFinite(inside) ? inside : null });
    }
  }
  return out;
}

// ── Roads ───────────────────────────────────────────────────────────────────

/** OSM classes counted as major roads. */
export const MAJOR_ROAD_CLASSES = ["motorway", "trunk", "primary", "secondary"];

/** Road name from OSM-style properties. */
export function roadName(f: GeoJSON.Feature): string {
  const p = (f.properties ?? {}) as Record<string, unknown>;
  const name = p.name ?? p.NAME ?? p.ref ?? p.label;
  return name ? String(name) : "Unnamed road";
}

/** A reached stretch of road (at most PIECE_M long), [lng, lat]. */
export interface RoadPiece {
  name: string;
  coords: [[number, number], [number, number]];
  /** Hours after the start the modelled fire first reached it */
  h: number;
}

const PIECE_M = 50;

/**
 * Where and when the modelled fire reaches each road: on it, i.e. a burned cell on the road
 * centreline (within half a cell diagonal). Lines with an OSM `highway` class outside
 * MAJOR_ROAD_CLASSES are skipped; lines without one (user layers) are all kept.
 */
export function roadReach(
  fire: FireSource | null,
  roads: GeoJSON.FeatureCollection | null | undefined,
): { first: Map<string, number>; pieces: RoadPiece[] } {
  const first = new Map<string, number>();
  const pieces: RoadPiece[] = [];
  if (!fire || fire.pts.length === 0 || !roads) return { first, pieces };
  const tol = insideToleranceM(fire);
  const idx = buildIndex(fire, Math.max(4 * tol, 100));
  const { pts } = fire;
  const toLngLat = (x: number, y: number): [number, number] => [x / idx.kx, y / M_PER_DEG + idx.lat0];
  for (const f of roads.features) {
    const g = f.geometry;
    if (!g || (g.type !== "LineString" && g.type !== "MultiLineString")) continue;
    const cls = (f.properties as Record<string, unknown> | null)?.highway;
    if (cls != null && !MAJOR_ROAD_CLASSES.includes(String(cls))) continue;
    const shape = toShape(idx, g);
    if (!overlaps(idx, shape.bbox, tol)) continue;
    const name = roadName(f);
    // Unnamed stretches (a ramp joined to no named road) are drawn, not listed by name
    const named = name !== "Unnamed road";
    for (const line of shape.lines) {
      for (let s = 1; s < line.length; s++) {
        const [x1, y1] = line[s - 1];
        const [x2, y2] = line[s];
        const n = Math.max(1, Math.ceil(Math.hypot(x2 - x1, y2 - y1) / PIECE_M));
        for (let k = 0; k < n; k++) {
          const ax = x1 + ((x2 - x1) * k) / n, ay = y1 + ((y2 - y1) * k) / n;
          const bx = x1 + ((x2 - x1) * (k + 1)) / n, by = y1 + ((y2 - y1) * (k + 1)) / n;
          const bbox: [number, number, number, number] = [Math.min(ax, bx), Math.min(ay, by), Math.max(ax, bx), Math.max(ay, by)];
          if (!overlaps(idx, bbox, tol)) continue;
          let h = Infinity;
          for (const list of candidates(idx, bbox, tol)) {
            for (const i of list) {
              if (pts[i].h >= h) break;
              if (segDist(idx.xs[i], idx.ys[i], ax, ay, bx, by) <= tol) h = pts[i].h;
            }
          }
          if (!Number.isFinite(h)) continue;
          pieces.push({ name, coords: [toLngLat(ax, ay), toLngLat(bx, by)], h });
          if (named && h < (first.get(name) ?? Infinity)) first.set(name, h);
        }
      }
    }
  }
  return { first, pieces };
}

// ── Rows for the card and the reports ───────────────────────────────────────

export interface AssetRow {
  asset: Asset;
  /** Ensemble worst-credible (P10); null without an ensemble or when not reached */
  worst: Reach | null;
  /** Single run; null when not reached */
  single: Reach | null;
  /** Earliest of the two "within 500 m" times (sort key) */
  first: number;
}

/** Assets the modelled fire comes within the buffer of (in either run), by first arrival. */
export function assetRows(assets: Asset[], single: Map<string, Reach>, worst: Map<string, Reach> | null): AssetRow[] {
  const rows: AssetRow[] = [];
  for (const asset of assets) {
    const s = single.get(asset.id) ?? null;
    const w = worst?.get(asset.id) ?? null;
    if (!s && !w) continue;
    rows.push({ asset, single: s, worst: w, first: Math.min(w?.near ?? Infinity, s?.near ?? Infinity) });
  }
  return rows.sort((a, b) => a.first - b.first || a.asset.name.localeCompare(b.asset.name));
}

export interface RoadRow {
  name: string;
  worst: number | null;
  single: number | null;
  first: number;
}

export function roadRows(single: Map<string, number>, worst: Map<string, number> | null): RoadRow[] {
  const names = new Set([...single.keys(), ...(worst?.keys() ?? [])]);
  return [...names]
    .map((name) => {
      const s = single.get(name) ?? null;
      const w = worst?.get(name) ?? null;
      return { name, single: s, worst: w, first: Math.min(w ?? Infinity, s ?? Infinity) };
    })
    .sort((a, b) => a.first - b.first || a.name.localeCompare(b.name));
}

/** Rows grouped by category in display order (empty groups left out). */
export function groupRows(rows: AssetRow[]): Array<{ category: AssetCategory; rows: AssetRow[] }> {
  return CATEGORY_ORDER.map((category) => ({ category, rows: rows.filter((r) => r.asset.category === category) })).filter(
    (g) => g.rows.length > 0,
  );
}

// ── Wording (model output, never an instruction) ────────────────────────────

/** "15:32", or "T+1:05" without a start time. */
export function clock(hours: number, start: Date | null): string {
  return start ? formatClockAt(start, hours) : formatElapsed(hours);
}

/**
 * "14:45 (worst-credible) · 15:20 (single run)" with an ensemble, else "15:20".
 * Null when neither run reaches it.
 */
export function timePair(worst: number | null, single: number | null, start: Date | null, hasEnsemble: boolean): string | null {
  if (!hasEnsemble) return single !== null ? clock(single, start) : null;
  if (worst === null && single === null) return null;
  const w = worst !== null ? `${clock(worst, start)} (worst-credible)` : "not reached (worst-credible)";
  const s = single !== null ? `${clock(single, start)} (single run)` : "not reached (single run)";
  return `${w} · ${s}`;
}

/** The one or two lines shown for an asset: within 500 m, then inside (when reached). */
export function assetReachPhrases(row: AssetRow, start: Date | null, hasEnsemble: boolean, bufferM = ARRIVAL_BUFFER_M): string[] {
  const out: string[] = [];
  const near = timePair(row.worst?.near ?? null, row.single?.near ?? null, start, hasEnsemble);
  if (near) out.push(`Fire within ${bufferM} m by ${near}`);
  const inside = timePair(row.worst?.inside ?? null, row.single?.inside ?? null, start, hasEnsemble);
  if (inside) out.push(`Inside the modelled fire by ${inside}`);
  return out;
}

export function roadReachPhrase(row: RoadRow, start: Date | null, hasEnsemble: boolean): string {
  return `Fire on the road by ${timePair(row.worst, row.single, start, hasEnsemble) ?? "not reached"}`;
}

/** Everything the summaries and forms need about reached assets and roads. */
export interface CriticalReach {
  assets: AssetRow[];
  roads: RoadRow[];
  /** An ensemble is in: worst-credible times lead */
  hasEnsemble: boolean;
  start: Date | null;
  bufferM: number;
}

/**
 * Data-quality notes for the card footer: how many assets no current official source confirms
 * (flagged "verify" in the bundled layer).
 */
export function assetDataNotes(assets: Asset[]): string[] {
  const notes: string[] = [];
  const others = assets.filter((a) => a.verify);
  if (others.length > 0) {
    notes.push(`${others.length} care ${others.length === 1 ? "facility is" : "facilities are"} in no current official list (marked “verify”).`);
  }
  return notes;
}

/** The asset's name with its data-quality flag: "City of Edmonton EOC [Unverified manual point]". */
export function assetLabel(a: Asset): string {
  return a.verify ? `${a.name} [${a.verify}]` : a.name;
}

/** Plain-text lines for the situation report (labelled as model output). */
export function criticalReachLines(r: CriticalReach): string[] {
  const lines: string[] = [];
  lines.push(
    `  Model output for this run${r.hasEnsemble ? " (worst-credible = ensemble P10; single run beside it)" : ""}; not an instruction.`,
  );
  if (r.assets.length === 0) lines.push(`  Assets: none within ${r.bufferM} m of the modelled fire.`);
  for (const g of groupRows(r.assets)) {
    lines.push(`  ${ASSET_CATEGORIES[g.category].label}:`);
    for (const row of g.rows) {
      lines.push(`    ${assetLabel(row.asset)}: ${assetReachPhrases(row, r.start, r.hasEnsemble, r.bufferM).join("; ")}`);
    }
  }
  if (r.roads.length > 0) {
    lines.push("  Major roads reached:");
    for (const row of r.roads) lines.push(`    ${row.name}: ${roadReachPhrase(row, r.start, r.hasEnsemble)}`);
  }
  return lines;
}

/** Reached assets as GeoJSON points (export), with their times in hours and clock time. */
export function criticalReachFeatures(r: CriticalReach): GeoJSON.Feature[] {
  const t = (h: number | null | undefined) => (h == null ? null : +h.toFixed(3));
  const c = (h: number | null | undefined) => (h == null ? null : clock(h, r.start));
  return r.assets.map((row) => ({
    type: "Feature",
    geometry: { type: "Point", coordinates: row.asset.anchor },
    properties: {
      layer: "asset_reached_by_modelled_fire",
      name: row.asset.name,
      category: row.asset.category,
      source: row.asset.source,
      sources: row.asset.sources.join("; "),
      verify: row.asset.verify,
      licence: row.asset.licence,
      within_m: r.bufferM,
      near_hours_single: t(row.single?.near),
      near_clock_single: c(row.single?.near),
      inside_hours_single: t(row.single?.inside),
      inside_clock_single: c(row.single?.inside),
      near_hours_worst_credible: t(row.worst?.near),
      near_clock_worst_credible: c(row.worst?.near),
      inside_hours_worst_credible: t(row.worst?.inside),
      inside_clock_worst_credible: c(row.worst?.inside),
      note: "Model output (FireSim), not an instruction",
    },
  }));
}

// ── Map data ────────────────────────────────────────────────────────────────

/**
 * Asset symbols for the map: one point per asset at its anchor, with `icon` (category and
 * reached state), `reached` (0/1) and the label text for reached ones.
 */
export function assetsToMapGeoJSON(
  assets: Asset[],
  rows: AssetRow[],
  shown: ReadonlySet<AssetCategory>,
  start: Date | null,
  hasEnsemble: boolean,
): GeoJSON.FeatureCollection {
  const byId = new Map(rows.map((r) => [r.asset.id, r]));
  return {
    type: "FeatureCollection",
    features: assets
      .filter((a) => shown.has(a.category))
      .map((a) => {
        const row = byId.get(a.id);
        const first = row ? (hasEnsemble ? row.worst?.near ?? row.single?.near : row.single?.near) ?? row.first : null;
        return {
          type: "Feature",
          geometry: { type: "Point", coordinates: a.anchor },
          properties: {
            id: a.id,
            name: a.name,
            category: a.category,
            detail: a.detail,
            source: a.sources.join("; "),
            verify: a.verify,
            reached: row ? 1 : 0,
            icon: `asset-${a.category}${row ? "-reached" : ""}`,
            label: row && first !== null ? `${a.name} · ${ARRIVAL_BUFFER_M} m by ${clock(first, start)}${hasEnsemble && row.worst ? " (worst-credible)" : ""}` : null,
          },
        } as GeoJSON.Feature;
      }),
  };
}

/** Reached road stretches for the map, with the time each was first reached. */
export function roadPiecesToGeoJSON(single: RoadPiece[], worst: RoadPiece[] | null): GeoJSON.FeatureCollection {
  const feats: GeoJSON.Feature[] = [];
  for (const p of worst ?? []) {
    feats.push({ type: "Feature", geometry: { type: "LineString", coordinates: p.coords }, properties: { name: p.name, h: p.h, run: "worst" } });
  }
  for (const p of single) {
    feats.push({ type: "Feature", geometry: { type: "LineString", coordinates: p.coords }, properties: { name: p.name, h: p.h, run: "single" } });
  }
  return { type: "FeatureCollection", features: feats };
}
