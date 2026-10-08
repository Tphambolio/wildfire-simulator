/**
 * Neighbourhoods: modelled fire arrival, and evacuation status set by Planning.
 *
 * FireSim does not suggest evacuation tiers (Travis, 2026-10-06: tier decisions belong to
 * Planning and the Director of Emergency Management). It reports, for each neighbourhood, the
 * modelled earliest time the fire is within 500 m of it (or inside it), as a fact the
 * planner can use. Order / Alert / Watch exist only as values a person sets; they are stored
 * with the incident and shown as blue outlines with distinct line styles and text labels
 * (design spec §3.6, §6.2, §7 1.4.1).
 *
 * Engine coordinates are [lat, lng]; GeoJSON coordinates are [lng, lat].
 */

import type { SimulationFrame } from "../types/simulation";
import { formatClockAt, formatElapsed } from "./time";

// ── Evacuation status (set by Planning) ─────────────────────────────────────

export type EvacTier = "Order" | "Alert" | "Watch";

/** Most to least severe. */
export const EVAC_TIERS: EvacTier[] = ["Order", "Alert", "Watch"];

/** Map colour for evacuation outlines: blue, outside the fire ramp (spec §6.2, --evac). */
export const EVAC_COLOR = "#1d4ed8";

/** Line style per tier, so tiers differ without colour: Order solid, Alert dashed, Watch dotted. */
export const TIER_STYLE: Record<EvacTier, { mapLabel: string; css: "solid" | "dashed" | "dotted"; dash: number[] | null; width: number }> = {
  Order: { mapLabel: "ORDER", css: "solid", dash: null, width: 3.5 },
  Alert: { mapLabel: "ALERT", css: "dashed", dash: [3, 1.5], width: 3 },
  Watch: { mapLabel: "WATCH", css: "dotted", dash: [0.6, 1.6], width: 3 },
};

/** One neighbourhood's status as set by a person in Planning. */
export interface EvacTierRecord {
  neighbourhood: string;
  tier: EvacTier;
  /** ISO time it was set */
  setAt: string;
}

/** Set (or with `tier` null, clear) a neighbourhood's status. Returns a new array. */
export function upsertTier(
  records: EvacTierRecord[],
  neighbourhood: string,
  tier: EvacTier | null,
  now: Date = new Date(),
): EvacTierRecord[] {
  const rest = records.filter((r) => r.neighbourhood !== neighbourhood);
  if (!tier) return rest;
  return [...rest, { neighbourhood, tier, setAt: now.toISOString() }];
}

/** User-set statuses grouped by tier (Order, Alert, Watch; empty tiers omitted). */
export interface PlanningEvacZone {
  tier: EvacTier;
  neighbourhoods: string[];
  /** Neighbourhood polygons (from the communities layer), when loaded */
  features: GeoJSON.Feature[];
  records: EvacTierRecord[];
}

export function planningZones(
  records: EvacTierRecord[],
  communities: GeoJSON.FeatureCollection | null | undefined,
): PlanningEvacZone[] {
  const byName = new Map<string, GeoJSON.Feature>();
  for (const f of communities?.features ?? []) byName.set(featureName(f), f);
  const zones: PlanningEvacZone[] = [];
  for (const tier of EVAC_TIERS) {
    const recs = records
      .filter((r) => r.tier === tier)
      .sort((a, b) => a.neighbourhood.localeCompare(b.neighbourhood));
    if (recs.length === 0) continue;
    zones.push({
      tier,
      neighbourhoods: recs.map((r) => r.neighbourhood),
      features: recs.map((r) => byName.get(r.neighbourhood)).filter((f): f is GeoJSON.Feature => !!f),
      records: recs,
    });
  }
  return zones;
}

/**
 * User-set statuses as GeoJSON (map layers and export). Each feature is a neighbourhood
 * polygon with `evac_tier`, `map_label` ("ORDER"), `set_by: "Planning"` and `set_at`.
 */
export function planningZonesToGeoJSON(zones: PlanningEvacZone[]): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = [];
  for (const z of zones) {
    for (const f of z.features) {
      const name = featureName(f);
      const rec = z.records.find((r) => r.neighbourhood === name);
      features.push({
        type: "Feature",
        geometry: f.geometry,
        properties: {
          neighbourhood: name,
          evac_tier: z.tier,
          map_label: TIER_STYLE[z.tier].mapLabel,
          set_by: "Planning",
          set_at: rec?.setAt ?? null,
        },
      });
    }
  }
  return { type: "FeatureCollection", features };
}

// ── Modelled fire arrival near neighbourhoods ───────────────────────────────

/** Distance from a neighbourhood within which fire counts as "near" it. */
export const ARRIVAL_BUFFER_M = 500;

export interface NeighbourhoodArrival {
  name: string;
  /** Hours after the scenario start the modelled fire is first within the buffer (or inside) */
  arrivalHours: number;
  feature: GeoJSON.Feature;
}

/** Feature name from common GeoJSON property conventions. */
export function featureName(f: GeoJSON.Feature): string {
  const p = f.properties as Record<string, unknown> | null;
  if (!p) return "Community";
  return String(p.name ?? p.NAME ?? p.label ?? p.community ?? "Community");
}

const M_PER_DEG = 111_320;
/** Perimeter runs: spacing of the points sampled along each perimeter edge (error ≤ half). */
const PERIMETER_STEP_M = 40;

/** Polygon rings ([lng, lat]) of a Polygon or MultiPolygon, grouped per polygon. */
function polygonsOf(g: GeoJSON.Geometry | null): number[][][][] {
  if (!g) return [];
  if (g.type === "Polygon") return [g.coordinates as number[][][]];
  if (g.type === "MultiPolygon") return g.coordinates as number[][][][];
  return [];
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

/**
 * A point inside the neighbourhood for its on-map label, [lng, lat]: the centroid of its
 * largest polygon, or, when that falls outside (a crescent or L shape), the middle of the
 * widest inside span on the horizontal line through it.
 */
export function labelPoint(f: GeoJSON.Feature): [number, number] | null {
  const polys = polygonsOf(f.geometry);
  let best: number[][][] | null = null;
  let bestArea = 0;
  let cx = 0, cy = 0;
  for (const poly of polys) {
    const ring = poly[0];
    if (!ring || ring.length < 3) continue;
    let a2 = 0, sx = 0, sy = 0;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const cross = ring[j][0] * ring[i][1] - ring[i][0] * ring[j][1];
      a2 += cross;
      sx += (ring[j][0] + ring[i][0]) * cross;
      sy += (ring[j][1] + ring[i][1]) * cross;
    }
    if (Math.abs(a2) > bestArea) {
      bestArea = Math.abs(a2);
      best = poly;
      cx = sx / (3 * a2);
      cy = sy / (3 * a2);
    }
  }
  if (!best) return null;
  const inside = (x: number, y: number) => ringContains(best![0], x, y) && !best!.slice(1).some((h) => ringContains(h, x, y));
  if (inside(cx, cy)) return [cx, cy];
  // Widest inside span on the line y = cy
  const xs: number[] = [];
  for (const ring of best) {
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [x1, y1] = ring[j];
      const [x2, y2] = ring[i];
      if (y1 > cy !== y2 > cy) xs.push(x1 + ((cy - y1) * (x2 - x1)) / (y2 - y1));
    }
  }
  xs.sort((a, b) => a - b);
  let span: [number, number] | null = null;
  for (let i = 0; i + 1 < xs.length; i += 2) {
    if (!span || xs[i + 1] - xs[i] > span[1] - span[0]) span = [xs[i], xs[i + 1]];
  }
  return span ? [(span[0] + span[1]) / 2, cy] : [best[0][0][0], best[0][0][1]];
}

/** A neighbourhood prepared for distance tests in a local metric frame. */
interface Prepared {
  name: string;
  feature: GeoJSON.Feature;
  lat0: number;
  kx: number; // metres per degree of longitude at lat0
  /** polygons as rings of [x, y] metres (relative to lng 0 / lat0) */
  polys: number[][][][];
  bbox: [number, number, number, number]; // [minLng, minLat, maxLng, maxLat] degrees
}

function prepare(f: GeoJSON.Feature): Prepared | null {
  const polys = polygonsOf(f.geometry);
  if (polys.length === 0) return null;
  let minLng = Infinity, minLat = Infinity, maxLng = -Infinity, maxLat = -Infinity;
  for (const poly of polys) for (const ring of poly) for (const [lng, lat] of ring) {
    if (lng < minLng) minLng = lng;
    if (lng > maxLng) maxLng = lng;
    if (lat < minLat) minLat = lat;
    if (lat > maxLat) maxLat = lat;
  }
  if (!Number.isFinite(minLng)) return null;
  const lat0 = (minLat + maxLat) / 2;
  const kx = M_PER_DEG * Math.cos((lat0 * Math.PI) / 180);
  const toXY = ([lng, lat]: number[]) => [lng * kx, (lat - lat0) * M_PER_DEG];
  return {
    name: featureName(f),
    feature: f,
    lat0,
    kx,
    polys: polys.map((poly) => poly.map((ring) => ring.map(toXY))),
    bbox: [minLng, minLat, maxLng, maxLat],
  };
}

/** Distance in metres from (lat, lng) to the neighbourhood (0 inside it). */
function distanceM(p: Prepared, lat: number, lng: number): number {
  const x = lng * p.kx;
  const y = (lat - p.lat0) * M_PER_DEG;
  let best = Infinity;
  for (const poly of p.polys) {
    // Inside the outer ring and not in a hole
    if (ringContains(poly[0], x, y) && !poly.slice(1).some((h) => ringContains(h, x, y))) return 0;
    for (const ring of poly) {
      for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        const [x1, y1] = ring[j];
        const [x2, y2] = ring[i];
        const dx = x2 - x1;
        const dy = y2 - y1;
        const len2 = dx * dx + dy * dy;
        const t = len2 > 0 ? Math.max(0, Math.min(1, ((x - x1) * dx + (y - y1) * dy) / len2)) : 0;
        const d = Math.hypot(x - (x1 + t * dx), y - (y1 + t * dy));
        if (d < best) best = d;
      }
    }
  }
  return best;
}

export function perimeterContains(perimeter: number[][], lat: number, lng: number): boolean {
  let inside = false;
  for (let i = 0, j = perimeter.length - 1; i < perimeter.length; j = i++) {
    const [latI, lngI] = perimeter[i];
    const [latJ, lngJ] = perimeter[j];
    if (lngI > lng !== lngJ > lng && lat < ((latJ - latI) * (lng - lngI)) / (lngJ - lngI) + latI) inside = !inside;
  }
  return inside;
}

/**
 * Fire locations with the time (hours after the start) each was first burning.
 * Grid runs: burned cells, timed by their arrival `t` (minutes; the frame time if absent).
 * Frames may be cumulative (each frame repeats earlier cells, as the app holds them) or
 * incremental (`cells_offset` > 0, as streamed): both give the same set. Runs without cells
 * (Huygens): perimeter vertices at each frame's time.
 */
export function firePoints(frames: SimulationFrame[]): { lat: number; lng: number; h: number }[] {
  const hasCells = frames.some((f) => (f.burned_cells?.length ?? 0) > 0);
  const out: { lat: number; lng: number; h: number }[] = [];
  if (hasCells) {
    const incremental = frames.some((f) => (f.cells_offset ?? 0) > 0);
    const last = frames[frames.length - 1];
    const allTimed = !incremental && (last.burned_cells ?? []).every((c) => c.t !== undefined && c.t !== null);
    if (allTimed) {
      // Cumulative frames with arrival times: the last frame holds every cell once
      for (const c of last.burned_cells ?? []) out.push({ lat: c.lat, lng: c.lng, h: (c.t as number) / 60 });
      return out.sort((a, b) => a.h - b.h);
    }
    const sources = frames;
    const seen = new Map<string, number>();
    for (const f of sources) {
      for (const c of f.burned_cells ?? []) {
        const h = c.t !== undefined && c.t !== null ? c.t / 60 : f.time_hours;
        const key = `${c.lat.toFixed(6)},${c.lng.toFixed(6)}`;
        const prev = seen.get(key);
        if (prev === undefined || h < prev) seen.set(key, h);
      }
    }
    for (const [key, h] of seen) {
      const [lat, lng] = key.split(",").map(Number);
      out.push({ lat, lng, h });
    }
  } else {
    // Perimeter edges sampled every PERIMETER_STEP_M (vertices alone can be far apart)
    for (const f of frames) {
      const ring = f.perimeter;
      for (let i = 0; i < ring.length; i++) {
        const [lat1, lng1] = ring[i];
        const [lat2, lng2] = ring[(i + 1) % ring.length];
        const kx = M_PER_DEG * Math.cos((lat1 * Math.PI) / 180);
        const len = Math.hypot((lat2 - lat1) * M_PER_DEG, (lng2 - lng1) * kx);
        const n = Math.max(1, Math.ceil(len / PERIMETER_STEP_M));
        for (let k = 0; k < n; k++) {
          out.push({ lat: lat1 + ((lat2 - lat1) * k) / n, lng: lng1 + ((lng2 - lng1) * k) / n, h: f.time_hours });
        }
      }
    }
  }
  return out.sort((a, b) => a.h - b.h);
}

/**
 * For each neighbourhood the modelled fire comes within `bufferM` of (or into), the earliest
 * such time in hours after the start. Sorted by time, then name. A modelled fact for
 * Planning, not an evacuation recommendation.
 */
export function neighbourhoodArrivals(
  frames: SimulationFrame[],
  communities: GeoJSON.FeatureCollection | null | undefined,
  bufferM: number = ARRIVAL_BUFFER_M,
): NeighbourhoodArrival[] {
  if (!communities || frames.length === 0) return [];
  const pts = firePoints(frames);
  const perimeterMode = !frames.some((f) => (f.burned_cells?.length ?? 0) > 0);
  return arrivalsFromPoints(pts, communities, bufferM, perimeterMode ? frames : null);
}

/**
 * The same, from fire points sorted by time (e.g. the cells of the ensemble's P10 arrival
 * raster, utils/ensemble.ts arrivalPoints): the earliest point within `bufferM`.
 */
export function neighbourhoodArrivalsFromPoints(
  pts: { lat: number; lng: number; h: number }[],
  communities: GeoJSON.FeatureCollection | null | undefined,
  bufferM: number = ARRIVAL_BUFFER_M,
): NeighbourhoodArrival[] {
  if (!communities) return [];
  return arrivalsFromPoints(pts, communities, bufferM, null);
}

function arrivalsFromPoints(
  pts: { lat: number; lng: number; h: number }[],
  communities: GeoJSON.FeatureCollection,
  bufferM: number,
  /** Perimeter runs: frames, to catch a neighbourhood swept over with no vertex near it */
  perimeterFrames: SimulationFrame[] | null,
): NeighbourhoodArrival[] {
  if (pts.length === 0) return [];
  let fMinLat = Infinity, fMaxLat = -Infinity, fMinLng = Infinity, fMaxLng = -Infinity;
  for (const p of pts) {
    if (p.lat < fMinLat) fMinLat = p.lat;
    if (p.lat > fMaxLat) fMaxLat = p.lat;
    if (p.lng < fMinLng) fMinLng = p.lng;
    if (p.lng > fMaxLng) fMaxLng = p.lng;
  }
  const dLat = bufferM / M_PER_DEG;
  const out: NeighbourhoodArrival[] = [];
  for (const feat of communities.features) {
    const p = prepare(feat);
    if (!p) continue;
    const dLng = bufferM / p.kx;
    const [minLng, minLat, maxLng, maxLat] = p.bbox;
    const bx0 = minLng - dLng, bx1 = maxLng + dLng, by0 = minLat - dLat, by1 = maxLat + dLat;
    if (fMaxLng < bx0 || fMinLng > bx1 || fMaxLat < by0 || fMinLat > by1) continue;
    let arrival = Infinity;
    for (const q of pts) {
      if (q.lng < bx0 || q.lng > bx1 || q.lat < by0 || q.lat > by1) continue;
      if (distanceM(p, q.lat, q.lng) <= bufferM) { arrival = q.h; break; }
    }
    if (perimeterFrames) {
      const frames = perimeterFrames;
      // A neighbourhood the perimeter has swept over, with no vertex near it
      const ring = (polygonsOf(feat.geometry)[0]?.[0]) ?? [];
      for (const f of frames) {
        if (f.time_hours >= arrival) break;
        if (f.perimeter.length >= 3 && ring.some(([lng, lat]) => perimeterContains(f.perimeter, lat, lng))) {
          arrival = f.time_hours;
          break;
        }
      }
    }
    if (Number.isFinite(arrival)) out.push({ name: p.name, arrivalHours: arrival, feature: feat });
  }
  return out.sort((a, b) => a.arrivalHours - b.arrivalHours || a.name.localeCompare(b.name));
}

/** "Fire within 500 m by 15:32" (or "by T+1:05" without a start time). */
export function arrivalLabel(arrivalHours: number, start: Date | null, bufferM: number = ARRIVAL_BUFFER_M): string {
  return `Fire within ${bufferM} m by ${arrivalTime(arrivalHours, start)}`;
}

/** "15:32" (or "T+1:05" without a start time). */
export function arrivalTime(arrivalHours: number, start: Date | null): string {
  return start ? formatClockAt(start, arrivalHours) : formatElapsed(arrivalHours);
}

/**
 * Arrival outlines for the map: neighbourhood polygons with their label. With `worstCredible`
 * the times are the ensemble's P10 arrival and the label says so.
 */
export function arrivalsToGeoJSON(
  arrivals: NeighbourhoodArrival[],
  start: Date | null,
  worstCredible = false,
): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: arrivals.map((a) => ({
      type: "Feature",
      geometry: a.feature.geometry,
      properties: {
        neighbourhood: a.name,
        arrival_hours: +a.arrivalHours.toFixed(3),
        arrival_label: worstCredible
          ? `Worst-credible: fire within ${ARRIVAL_BUFFER_M} m by ${arrivalTime(a.arrivalHours, start)}`
          : arrivalLabel(a.arrivalHours, start),
      },
    })),
  };
}
