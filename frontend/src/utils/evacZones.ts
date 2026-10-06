/**
 * Evacuation trigger zone utilities — Alberta Emergency Management Act model.
 *
 * Three-tier ICS/EOC structure (standard in AB municipalities):
 *   Order  (red)    — 0–2 h perimeter  — LEAVE NOW
 *   Alert  (orange) — 2–6 h perimeter  — BE READY (~30 min)
 *   Watch  (yellow) — 6–12 h perimeter — MONITOR SITUATION
 *
 * Zones are neighbourhood-based (Option A): each tier highlights the complete
 * neighbourhood polygons whose centroids fall inside the projected fire perimeter
 * at that time horizon. Tiers are exclusive — a neighbourhood assigned to Order
 * does not reappear in Alert or Watch.
 *
 * All engine perimeter coords are [[lat, lng], ...].
 * GeoJSON coords are [lng, lat].
 */

import type { SimulationFrame } from "../types/simulation";

export type EvacZoneLabel = "Order" | "Alert" | "Watch";

export interface EvacZone {
  label: EvacZoneLabel;
  /** Display colour (CSS) */
  color: string;
  /** AEMA action description */
  action: string;
  timeRangeLabel: string;
  /** Perimeter used for intersection test (engine [[lat, lng]] format, scaled) */
  perimeter: number[][];
  areaHa: number;
  /** Neighbourhood names in this tier (for panel display) */
  communitiesAtRisk: string[];
  /** Actual GeoJSON features for this tier (for map rendering) */
  communitiesFeatures: GeoJSON.Feature[];
  /** Scale factor applied by the operator (1.0 = no change) */
  scale: number;
}

const ZONE_DEFS: Array<{
  label: EvacZoneLabel;
  targetHours: number;
  color: string;
  action: string;
  timeRangeLabel: string;
}> = [
  { label: "Order", targetHours: 2,  color: "#d32f2f", action: "LEAVE NOW",          timeRangeLabel: "0–2 h" },
  { label: "Alert", targetHours: 6,  color: "#f57c00", action: "BE READY (~30 min)", timeRangeLabel: "2–6 h" },
  { label: "Watch", targetHours: 12, color: "#f9a825", action: "MONITOR SITUATION",  timeRangeLabel: "6–12 h" },
];

/** Pick the frame whose time_hours is closest to target, with at least 3 perimeter points. */
function closestFrame(frames: SimulationFrame[], targetHours: number): SimulationFrame | null {
  let best: SimulationFrame | null = null;
  let bestDist = Infinity;
  for (const f of frames) {
    if (f.perimeter.length < 3) continue;
    const d = Math.abs(f.time_hours - targetHours);
    if (d < bestDist) { bestDist = d; best = f; }
  }
  return best;
}

/**
 * Scale a polygon outward or inward around its centroid.
 * factor > 1 expands the perimeter (pulls in more neighbourhoods).
 */
function scalePolygon(perimeter: number[][], factor: number): number[][] {
  if (factor === 1 || perimeter.length === 0) return perimeter;
  let sumLat = 0, sumLng = 0;
  for (const [lat, lng] of perimeter) { sumLat += lat; sumLng += lng; }
  const cLat = sumLat / perimeter.length;
  const cLng = sumLng / perimeter.length;
  return perimeter.map(([lat, lng]) => [
    cLat + (lat - cLat) * factor,
    cLng + (lng - cLng) * factor,
  ]);
}

// ── Point-in-polygon (ray casting, WGS84 approximation) ─────────────────────

function pointInPolygon(latPt: number, lngPt: number, ring: number[][]): boolean {
  let inside = false;
  const n = ring.length;
  for (let i = 0, j = n - 1; i < n; j = i++) {
    const [latI, lngI] = ring[i];
    const [latJ, lngJ] = ring[j];
    const cross =
      lngI > lngPt !== lngJ > lngPt &&
      latPt < ((latJ - latI) * (lngPt - lngI)) / (lngJ - lngI) + latI;
    if (cross) inside = !inside;
  }
  return inside;
}

/** Extract a representative lat/lng centroid from a GeoJSON geometry. */
function geometryCentroid(g: GeoJSON.Geometry): [number, number] | null {
  if (g.type === "Point") return [g.coordinates[1], g.coordinates[0]];
  if (g.type === "MultiPoint") { const [lng, lat] = g.coordinates[0]; return [lat, lng]; }
  if (g.type === "Polygon" && g.coordinates[0].length > 0) {
    let sLat = 0, sLng = 0;
    const ring = g.coordinates[0];
    for (const [lng, lat] of ring) { sLat += lat; sLng += lng; }
    return [sLat / ring.length, sLng / ring.length];
  }
  if (g.type === "MultiPolygon" && g.coordinates[0]?.[0]?.length) {
    const [lng, lat] = g.coordinates[0][0][0];
    return [lat, lng];
  }
  if (g.type === "LineString" && g.coordinates.length > 0) {
    const mid = g.coordinates[Math.floor(g.coordinates.length / 2)];
    return [mid[1], mid[0]];
  }
  return null;
}

/** Feature name from common GeoJSON property conventions. */
function featureName(f: GeoJSON.Feature): string {
  const p = f.properties as Record<string, unknown> | null;
  if (!p) return "Community";
  return String(p.name ?? p.NAME ?? p.label ?? p.community ?? "Community");
}

/**
 * Find community features whose centroid falls inside the zone perimeter.
 * Returns actual GeoJSON features (not just names) for map rendering.
 */
// Community centroids, computed once per communities layer
const centroidCache = new WeakMap<GeoJSON.FeatureCollection, Array<[number, number] | null>>();

function communitiesInZone(
  perimeter: number[][],
  communities: GeoJSON.FeatureCollection | null | undefined,
): GeoJSON.Feature[] {
  if (!communities || perimeter.length < 3) return [];
  let centroids = centroidCache.get(communities);
  if (!centroids) {
    centroids = communities.features.map((f) => geometryCentroid(f.geometry));
    centroidCache.set(communities, centroids);
  }
  const result: GeoJSON.Feature[] = [];
  communities.features.forEach((feat, i) => {
    const c = centroids![i];
    if (c && pointInPolygon(c[0], c[1], perimeter)) result.push(feat);
  });
  return result;
}

// ── Polygon area via shoelace (approximate, degrees²→ ha) ───────────────────

function polygonAreaHa(perimeter: number[][]): number {
  if (perimeter.length < 3) return 0;
  const latScale = 111320;
  const midLat = perimeter.reduce((s, [lat]) => s + lat, 0) / perimeter.length;
  const lngScale = 111320 * Math.cos((midLat * Math.PI) / 180);
  let area = 0;
  const n = perimeter.length;
  for (let i = 0, j = n - 1; i < n; j = i++) {
    area += (perimeter[j][1] * lngScale) * (perimeter[i][0] * latScale);
    area -= (perimeter[i][1] * lngScale) * (perimeter[j][0] * latScale);
  }
  return Math.abs(area) / 2 / 10_000;
}

// ── Public API ────────────────────────────────────────────────────────────────

/**
 * Derive evacuation zones from simulation frames.
 *
 * Zones are neighbourhood-based and exclusive: each neighbourhood appears in
 * at most one tier (the highest-urgency tier whose perimeter contains it).
 * Returns up to 3 zones; a zone is omitted if beyond the simulation duration.
 */
export function computeEvacZones(
  frames: SimulationFrame[],
  communities: GeoJSON.FeatureCollection | null | undefined,
  scales: Record<EvacZoneLabel, number> = { Order: 1, Alert: 1, Watch: 1 },
): EvacZone[] {
  if (frames.length === 0) return [];

  // The zones depend only on the frame chosen for each tier, so scrubbing between frames
  // that pick the same tier frames returns the previous result (same array identity, so
  // the map does not redraw the zones).
  const maxHours = frames[frames.length - 1].time_hours;
  const chosen = ZONE_DEFS.map((def) =>
    def.targetHours > maxHours + 1 ? null : closestFrame(frames, def.targetHours) ?? null,
  );
  if (
    lastResult &&
    lastResult.communities === communities &&
    lastResult.scales === scales &&
    lastResult.chosen.length === chosen.length &&
    lastResult.chosen.every((f, i) => f === chosen[i])
  ) {
    return lastResult.zones;
  }

  const zones: EvacZone[] = [];
  // Track which neighbourhood names have been assigned to a closer tier
  const assignedNames = new Set<string>();

  ZONE_DEFS.forEach((def, i) => {
    const frame = chosen[i];
    if (!frame) return;

    const scale = scales[def.label] ?? 1;
    const scaled = scalePolygon(frame.perimeter, scale);

    // All features inside this perimeter
    const allFeatures = communitiesInZone(scaled, communities);
    // Exclusive: exclude any already assigned to a higher-urgency tier
    const exclusiveFeatures = allFeatures.filter((f) => !assignedNames.has(featureName(f)));
    exclusiveFeatures.forEach((f) => assignedNames.add(featureName(f)));

    zones.push({
      label: def.label,
      color: def.color,
      action: def.action,
      timeRangeLabel: def.timeRangeLabel,
      perimeter: scaled,
      areaHa: polygonAreaHa(scaled),
      communitiesAtRisk: exclusiveFeatures.map(featureName),
      communitiesFeatures: exclusiveFeatures,
      scale,
    });
  });

  lastResult = { communities, scales, chosen, zones };
  return zones;
}

let lastResult: {
  communities: GeoJSON.FeatureCollection | null | undefined;
  scales: Record<EvacZoneLabel, number>;
  chosen: Array<SimulationFrame | null>;
  zones: EvacZone[];
} | null = null;

/**
 * Convert evac zones to a GeoJSON FeatureCollection for map rendering and export.
 *
 * Each feature is an actual neighbourhood polygon tagged with its zone tier.
 * If a zone has no community features (communities layer not loaded), it is
 * omitted from the output.
 */
export function evacZonesToGeoJSON(zones: EvacZone[]): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = [];
  for (const zone of zones) {
    for (const f of zone.communitiesFeatures) {
      features.push({
        type: "Feature" as const,
        geometry: f.geometry,
        properties: {
          ...(f.properties ?? {}),
          zone_label: zone.label,
          zone_color: zone.color,
          zone_action: zone.action,
          time_range: zone.timeRangeLabel,
          neighbourhood: featureName(f),
        },
      });
    }
  }
  return { type: "FeatureCollection", features };
}

// ── Tier ranking helper ───────────────────────────────────────────────────────

const TIER_RANK: Record<EvacZoneLabel, number> = { Order: 3, Alert: 2, Watch: 1 };

const ZONE_META: Record<EvacZoneLabel, { color: string; action: string; timeRangeLabel: string }> = {
  Order: { color: "#d32f2f", action: "LEAVE NOW",          timeRangeLabel: "0–2 h"  },
  Alert: { color: "#f57c00", action: "BE READY (~30 min)", timeRangeLabel: "2–6 h"  },
  Watch: { color: "#f9a825", action: "MONITOR SITUATION",  timeRangeLabel: "6–12 h" },
};

/**
 * Merge committed zone history (Order + Alert) into the current live zone output.
 *
 * Persistence model:
 *   Order  — hard persist: once issued, a neighbourhood never leaves Order
 *   Alert  — soft persist: stays at minimum Alert; can upgrade to Order
 *   Watch  — dynamic: never persisted; reflects current 6–12 h horizon only
 *
 * If a historically-committed neighbourhood is absent from the current zones
 * (e.g. the scrubber moved backward), it is re-inserted into the appropriate
 * tier using community features from the communities layer.  If the neighbourhood
 * is already in a higher tier, it stays there.
 */
export function applyZoneHistory(
  current: EvacZone[],
  history: Map<string, EvacZoneLabel>,
  communities: GeoJSON.FeatureCollection | null | undefined,
): EvacZone[] {
  if (history.size === 0 || !communities) return current;

  // Build what is currently assigned at each tier
  const currentlyAssigned = new Map<string, EvacZoneLabel>();
  for (const zone of current) {
    for (const name of zone.communitiesAtRisk) {
      const prev = currentlyAssigned.get(name);
      if (!prev || TIER_RANK[zone.label] > TIER_RANK[prev]) {
        currentlyAssigned.set(name, zone.label);
      }
    }
  }

  // Find history entries that need injection or upgrade
  const toAdd = new Map<EvacZoneLabel, Array<{ name: string; feature: GeoJSON.Feature }>>();
  const toRemoveFromLower = new Set<string>();

  for (const [name, histTier] of history) {
    if (histTier === "Watch") continue; // Watch is never committed
    const curTier = currentlyAssigned.get(name);
    if (curTier && TIER_RANK[curTier] >= TIER_RANK[histTier]) continue; // already at correct tier or higher

    const feature = communities.features.find((f) => featureName(f) === name);
    if (!feature) continue;

    if (!toAdd.has(histTier)) toAdd.set(histTier, []);
    toAdd.get(histTier)!.push({ name, feature });

    // If currently in a lower tier, remove it from there (it belongs in histTier now)
    if (curTier && TIER_RANK[curTier] < TIER_RANK[histTier]) {
      toRemoveFromLower.add(name);
    }
  }

  if (toAdd.size === 0) return current;

  // Strip upgraded names from lower-tier zones
  let result: EvacZone[] = current.map((zone) => {
    if (toRemoveFromLower.size === 0) return zone;
    const kept = zone.communitiesAtRisk.filter((n) => !toRemoveFromLower.has(n));
    if (kept.length === zone.communitiesAtRisk.length) return zone;
    return {
      ...zone,
      communitiesAtRisk: kept,
      communitiesFeatures: zone.communitiesFeatures.filter((f) => !toRemoveFromLower.has(featureName(f))),
    };
  });

  // Inject historical communities into existing zones (or create synthetic zones)
  for (const [label, entries] of toAdd) {
    const names = entries.map((e) => e.name);
    const features = entries.map((e) => e.feature);
    const idx = result.findIndex((z) => z.label === label);
    if (idx >= 0) {
      result = result.map((z, i) =>
        i === idx
          ? {
              ...z,
              communitiesAtRisk: [...z.communitiesAtRisk, ...names],
              communitiesFeatures: [...z.communitiesFeatures, ...features],
            }
          : z
      );
    } else {
      const meta = ZONE_META[label];
      result.push({
        label,
        color: meta.color,
        action: meta.action,
        timeRangeLabel: meta.timeRangeLabel,
        perimeter: [],
        areaHa: 0,
        communitiesAtRisk: names,
        communitiesFeatures: features,
        scale: 1,
      });
    }
  }

  // Maintain Order > Alert > Watch sort order
  return result.sort((a, b) => TIER_RANK[b.label] - TIER_RANK[a.label]);
}
