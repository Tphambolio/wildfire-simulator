/**
 * Active edges of an observed (RPAS) perimeter for POST /simulations/perimeter-override.
 *
 * Only the parts of the perimeter within `active_edge_buffer_m` of the active edges spread;
 * the rest is treated as burned out (held-out validation, docs/validation.md). Edges come from
 * lines drawn on the map and/or whole perimeter sides picked from a list (keyboard).
 * Coordinates are GeoJSON [lng, lat].
 */
import type {
  ActiveSegmentsInfo, BurningPeriod, FWIOverrides, ObservedPerimeterRequest, PerimeterOverrideRequest,
  SimulationCreate, WeatherChange, WeatherParams,
} from "../types/simulation";

export type Lnglat = [number, number];

/** Which edges are active: the whole perimeter, edges marked by hand, or classified from
 * RPAS hotspots / VIIRS detections (A1; docs/active-segments.md) */
export type ReconMode = "whole" | "marked" | "classified";

/** A classification from POST /simulations/classify-perimeter */
export interface Classified {
  source: string;
  segments: GeoJSON.FeatureCollection;
  activeEdges: GeoJSON.MultiLineString | null;
  summary: ActiveSegmentsInfo & { n_detections_read?: number; n_detections_used?: number };
}

/** Active lines of classified segments (feature property `status` "active"; the others are
 * not active and, as with hand-marked edges, treated as burned out). */
export function segmentLines(fc: GeoJSON.FeatureCollection | null | undefined): Lnglat[][] {
  const out: Lnglat[][] = [];
  for (const f of fc?.features ?? []) {
    if (f.geometry?.type !== "LineString" || f.properties?.status !== "active") continue;
    out.push(f.geometry.coordinates as Lnglat[]);
  }
  return out;
}

/** "Segments active M of N" readout, or null without a classification. */
export function segmentReadout(s: { n_active?: number; n_segments?: number } | null | undefined): string | null {
  if (!s || s.n_active === undefined || s.n_segments === undefined) return null;
  return `Segments active ${s.n_active} of ${s.n_segments}`;
}

/** Message when a classification has no active segment (the restart would not spread). */
export const NO_ACTIVE_SEGMENT =
  "No segment is active (no detection near the perimeter in the 12 h before T): nothing would spread. " +
  "Choose “Whole perimeter active” or mark active edges.";

export const SIDES = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"] as const;
export type Side = (typeof SIDES)[number];
export const SIDE_NAMES: Record<Side, string> = {
  N: "North", NE: "North-east", E: "East", SE: "South-east",
  S: "South", SW: "South-west", W: "West", NW: "North-west",
};

/** Outer ring of a Polygon, or of the largest part of a MultiPolygon (by vertex count). */
export function perimeterRing(geom: GeoJSON.Geometry | null): Lnglat[] | null {
  if (!geom) return null;
  if (geom.type === "Polygon") return (geom.coordinates[0] as Lnglat[]) ?? null;
  if (geom.type === "MultiPolygon") {
    let best: Lnglat[] | null = null;
    for (const poly of geom.coordinates) {
      const ring = poly[0] as Lnglat[];
      if (!best || ring.length > best.length) best = ring;
    }
    return best;
  }
  return null;
}

/** A Polygon geometry from a frame perimeter ([[lat, lng], ...], open ring). */
export function framePerimeterToPolygon(perimeter: number[][]): GeoJSON.Polygon | null {
  if (!perimeter || perimeter.length < 3) return null;
  const ring: Lnglat[] = perimeter.map(([lat, lng]) => [lng, lat]);
  const [a, b] = [ring[0], ring[ring.length - 1]];
  if (a[0] !== b[0] || a[1] !== b[1]) ring.push([a[0], a[1]]);
  return { type: "Polygon", coordinates: [ring] };
}

/** Accept a GeoJSON Feature, FeatureCollection or bare Polygon/MultiPolygon geometry. */
export function perimeterFromGeoJSON(gj: Record<string, unknown>): GeoJSON.Polygon | GeoJSON.MultiPolygon {
  let geometry: Record<string, unknown> | null = null;
  if (gj.type === "Feature") geometry = gj.geometry as Record<string, unknown>;
  else if (gj.type === "Polygon" || gj.type === "MultiPolygon") geometry = gj;
  else if (gj.type === "FeatureCollection") {
    const features = (gj.features as Array<Record<string, unknown>>) ?? [];
    const f = features.find((x) => {
      const g = x.geometry as Record<string, unknown> | null;
      return g && (g.type === "Polygon" || g.type === "MultiPolygon");
    });
    if (!f) throw new Error("No Polygon or MultiPolygon found in the FeatureCollection.");
    geometry = f.geometry as Record<string, unknown>;
  } else {
    throw new Error(`Unsupported GeoJSON type '${String(gj.type)}'. Expected Polygon, MultiPolygon, Feature, or FeatureCollection.`);
  }
  if (!geometry || (geometry.type !== "Polygon" && geometry.type !== "MultiPolygon")) {
    throw new Error(`Geometry type '${String(geometry?.type)}' is not supported. Expected Polygon or MultiPolygon.`);
  }
  return geometry as unknown as GeoJSON.Polygon | GeoJSON.MultiPolygon;
}

/** Compass side (of 8) of a bearing in degrees clockwise from north. */
function sideOf(bearing: number): Side {
  return SIDES[Math.round((((bearing % 360) + 360) % 360) / 45) % 8];
}

/**
 * The perimeter split into its 8 compass sides as seen from the ring's centre: each ring
 * segment goes to the side of its midpoint's bearing; consecutive segments on one side are
 * joined into a line. A side can have several lines (or none) on an irregular perimeter.
 */
export function perimeterSides(ring: Lnglat[]): Record<Side, Lnglat[][]> {
  const out = Object.fromEntries(SIDES.map((s) => [s, [] as Lnglat[][]])) as Record<Side, Lnglat[][]>;
  const pts = ring.length > 1 && ring[0][0] === ring[ring.length - 1][0] && ring[0][1] === ring[ring.length - 1][1]
    ? ring.slice(0, -1)
    : ring.slice();
  if (pts.length < 3) return out;
  const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length;
  const cy = pts.reduce((s, p) => s + p[1], 0) / pts.length;
  const kx = Math.cos((cy * Math.PI) / 180); // metres east per degree, relative to north
  const segSide = pts.map((p, i) => {
    const q = pts[(i + 1) % pts.length];
    const mx = ((p[0] + q[0]) / 2 - cx) * kx;
    const my = (p[1] + q[1]) / 2 - cy;
    return sideOf((Math.atan2(mx, my) * 180) / Math.PI);
  });
  // Start at a side change so a run is not split across the ring's start
  const n = pts.length;
  let start = segSide.findIndex((s, i) => s !== segSide[(i - 1 + n) % n]);
  if (start < 0) start = 0;
  let line: Lnglat[] = [];
  let cur: Side | null = null;
  for (let k = 0; k < n; k++) {
    const i = (start + k) % n;
    const s = segSide[i];
    if (s !== cur) {
      if (cur && line.length >= 2) out[cur].push(line);
      cur = s;
      line = [pts[i]];
    }
    line.push(pts[(i + 1) % n]);
  }
  if (cur && line.length >= 2) out[cur].push(line);
  return out;
}

/** All active lines: drawn polylines plus the lines of the selected sides. */
export function activeLines(drawn: Lnglat[][], sides: Iterable<Side>, sideLines: Record<Side, Lnglat[][]> | null): Lnglat[][] {
  const lines = drawn.filter((l) => l.length >= 2);
  if (sideLines) for (const s of sides) lines.push(...sideLines[s]);
  return lines;
}

/** MultiLineString of the active lines, or null when there are none. */
export function activeEdgesGeometry(lines: Lnglat[][]): GeoJSON.MultiLineString | null {
  return lines.length ? { type: "MultiLineString", coordinates: lines } : null;
}

export interface OverrideInputs {
  simulationId: string;
  perimeter: GeoJSON.Polygon | GeoJSON.MultiPolygon;
  durationHours: number;
  snapshotMinutes: number;
  /** "whole": the whole perimeter is active (no active_edges sent); "marked": only the lines;
   * "classified": the active segments of a hotspot / VIIRS classification */
  mode: ReconMode;
  /** "classified": the classification's active edges (null = none active) */
  classifiedEdges?: GeoJSON.MultiLineString | null;
  lines: Lnglat[][];
  /** Buffer (m); null = the API default (one fuel-grid cell) */
  bufferM: number | null;
  /** Time of the observed perimeter (ISO with offset) */
  startTime: string | null;
  burningPeriod: BurningPeriod | null;
  ffmcSpinUp: boolean;
  /** Setup's weather, FWI codes and schedule (hours from startTime); omitted = the source run's */
  weather?: WeatherParams | null;
  fwi?: FWIOverrides | null;
  schedule?: WeatherChange[] | null;
}

/** The perimeter-override request. Throws if "marked" is chosen with no active edge. */
export function buildOverrideRequest(i: OverrideInputs): PerimeterOverrideRequest {
  const req: PerimeterOverrideRequest = {
    simulation_id: i.simulationId,
    perimeter_geojson: i.perimeter,
    duration_hours: i.durationHours,
    snapshot_interval_minutes: i.snapshotMinutes,
  };
  if (i.mode === "marked") {
    const geom = activeEdgesGeometry(i.lines);
    if (!geom) throw new Error("Mark at least one active edge, or choose “Whole perimeter active”.");
    req.active_edges = geom;
    if (i.bufferM !== null) req.active_edge_buffer_m = i.bufferM;
  } else if (i.mode === "classified") {
    // The classification is the active-edge marking: the rest is treated as burned out
    if (!i.classifiedEdges || i.classifiedEdges.coordinates.length === 0) throw new Error(NO_ACTIVE_SEGMENT);
    req.active_edges = i.classifiedEdges;
    if (i.bufferM !== null) req.active_edge_buffer_m = i.bufferM;
  }
  if (i.startTime) req.start_time = i.startTime;
  if (i.burningPeriod) req.burning_period = i.burningPeriod;
  if (i.ffmcSpinUp) req.ffmc_spin_up = true;
  if (i.weather) req.weather = i.weather;
  if (i.fwi) req.fwi_overrides = i.fwi;
  if (i.schedule?.length) req.weather_schedule = i.schedule;
  return req;
}

/**
 * Standalone forecast from an observed perimeter (POST /simulations/observed-perimeter): Setup's
 * run request (`base`) with the perimeter, its observation time and the active edges of `i`. The ignition is left out (the API uses the perimeter's centroid) and the run starts at T.
 */
export function buildObservedRequest(base: SimulationCreate, i: Omit<OverrideInputs, "simulationId">, observationIso: string): ObservedPerimeterRequest {
  const override = buildOverrideRequest({ ...i, simulationId: "" });
  const rest: Partial<SimulationCreate> = { ...base };
  delete rest.ignition_lat;
  delete rest.ignition_lng;
  delete rest.start_time;
  delete rest.ensemble;
  const req: ObservedPerimeterRequest = {
    ...(rest as Omit<SimulationCreate, "ignition_lat" | "ignition_lng" | "start_time" | "ensemble">),
    perimeter_geojson: i.perimeter,
    observation_time: observationIso,
    duration_hours: i.durationHours,
    snapshot_interval_minutes: i.snapshotMinutes,
  };
  for (const k of ["active_edges", "active_edge_buffer_m"] as const) {
    if (override[k] !== undefined) (req as Record<string, unknown>)[k] = override[k];
  }
  if (i.burningPeriod) req.burning_period = i.burningPeriod;
  else delete req.burning_period;
  if (i.weather) req.weather = i.weather;
  if (i.fwi) req.fwi_overrides = i.fwi;
  if (i.schedule?.length) req.weather_schedule = i.schedule;
  // The run starts at T with constant weather (no forecast stream): no spin-up
  req.ffmc_spin_up = false;
  req.hourly_weather = null;
  return req;
}
