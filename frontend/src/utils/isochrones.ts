/**
 * Fire arrival time isochrone utilities.
 *
 * Derives isochrone contours from simulation frame perimeters — each frame
 * represents the fire state at a discrete time, so the frame perimeter IS
 * the arrival-time boundary for that moment.
 *
 * Works for both CA and Huygens spread modes. In CA mode the engine also
 * tracks per-cell arrival time (the `t` field in burned_cells, in minutes),
 * but frame-based isochrones are consistent across modes and sufficient for
 * EOC / evacuation planning purposes.
 *
 * Perimeter convention: [[lat, lng], ...] (engine convention, not GeoJSON).
 */

import type { SimulationFrame } from "../types/simulation";

export interface Isochrone {
  /** Actual frame time (may differ slightly from requested target). */
  timeHours: number;
  /** Display label e.g. "T+2h" or "T+30min" */
  label: string;
  /** CSS / MapLibre color for this contour. */
  color: string;
  /** Perimeter in engine [[lat, lng]] format. */
  perimeter: number[][];
}

// ── Color scale (red = imminent, amber → green = distant) ─────────────────────

const ISO_COLOR_STOPS: Array<[number, string]> = [
  [0.5,  "#ff1744"],
  [1,    "#ff3d00"],
  [2,    "#ff6d00"],
  [3,    "#ff9100"],
  [4,    "#ffab00"],
  [6,    "#ffd600"],
  [8,    "#c6ff00"],
  [12,   "#00e676"],
  [16,   "#00b0ff"],
  [24,   "#7c4dff"],
];

function colorForHours(h: number): string {
  // Clamp to range
  if (h <= ISO_COLOR_STOPS[0][0]) return ISO_COLOR_STOPS[0][1];
  if (h >= ISO_COLOR_STOPS[ISO_COLOR_STOPS.length - 1][0]) {
    return ISO_COLOR_STOPS[ISO_COLOR_STOPS.length - 1][1];
  }
  // Find surrounding stops and interpolate (just pick nearest for simplicity)
  let best = ISO_COLOR_STOPS[0];
  let bestDist = Math.abs(h - best[0]);
  for (const stop of ISO_COLOR_STOPS) {
    const d = Math.abs(h - stop[0]);
    if (d < bestDist) { bestDist = d; best = stop; }
  }
  return best[1];
}

// ── Frame selection ───────────────────────────────────────────────────────────

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

// ── Label formatting ──────────────────────────────────────────────────────────

function formatLabel(targetHours: number): string {
  if (targetHours < 1) {
    return `T+${Math.round(targetHours * 60)}min`;
  }
  if (Number.isInteger(targetHours)) {
    return `T+${targetHours}h`;
  }
  return `T+${targetHours.toFixed(1)}h`;
}

// ── Public API ────────────────────────────────────────────────────────────────

export const DEFAULT_ISO_HOURS = [1, 2, 4, 8];

/** Preset interval configurations for the UI selector. */
export const ISO_PRESETS: Record<string, number[]> = {
  "30m/1h/2h/4h":  [0.5, 1, 2, 4],
  "1h/2h/4h/8h":   [1, 2, 4, 8],
  "2h/4h/8h/12h":  [2, 4, 8, 12],
  "1h/2h/4h/8h/16h": [1, 2, 4, 8, 16],
};

/**
 * Compute fire arrival time isochrones from simulation frames.
 *
 * @param frames  All received simulation frames (in order).
 * @param targetHours  Time thresholds to show (hours from ignition).
 * @returns  Array of isochrones in chronological order. Only includes targets
 *           reachable within the simulation duration; deduplicates frames that
 *           resolve to the same time bucket.
 */
export function computeIsochrones(
  frames: SimulationFrame[],
  targetHours: number[] = DEFAULT_ISO_HOURS,
): Isochrone[] {
  if (frames.length === 0) return [];

  const maxHours = frames[frames.length - 1].time_hours;
  const seenFrameTime = new Set<number>();
  const result: Isochrone[] = [];

  for (const t of [...targetHours].sort((a, b) => a - b)) {
    // Only include targets within (or just beyond) simulation duration
    if (t > maxHours + 0.5) continue;

    const f = closestFrame(frames, t);
    if (!f) continue;

    // Deduplicate: if two targets resolve to the same frame, skip second
    if (seenFrameTime.has(f.time_hours)) continue;
    seenFrameTime.add(f.time_hours);

    result.push({
      timeHours: f.time_hours,
      label: formatLabel(t),
      color: colorForHours(t),
      perimeter: f.perimeter,
    });
  }

  return result;
}

// ── GeoJSON conversion for MapLibre ──────────────────────────────────────────

/**
 * Convert isochrones to a GeoJSON FeatureCollection of LineStrings.
 * Coordinates in [lng, lat] order (GeoJSON convention).
 */
export function isochronesToGeoJSON(isochrones: Isochrone[]): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = [];
  for (const iso of isochrones) {
    if (iso.perimeter.length < 2) continue;
    const coords = iso.perimeter.map(([lat, lng]) => [lng, lat] as [number, number]);
    // Close the ring
    if (
      coords[0][0] !== coords[coords.length - 1][0] ||
      coords[0][1] !== coords[coords.length - 1][1]
    ) {
      coords.push(coords[0]);
    }
    features.push({
      type: "Feature" as const,
      properties: {
        time_hours: iso.timeHours,
        label: iso.label,
        color: iso.color,
      },
      geometry: {
        type: "LineString" as const,
        coordinates: coords,
      },
    });
  }
  return { type: "FeatureCollection", features };
}

/**
 * Generate label anchor points for each isochrone (northernmost point of ring).
 * Used for a MapLibre symbol layer.
 */
export function isochroneLabelsGeoJSON(isochrones: Isochrone[]): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = [];
  for (const iso of isochrones) {
    if (iso.perimeter.length === 0) continue;
    // Find northernmost point (highest latitude)
    let north = iso.perimeter[0];
    for (const pt of iso.perimeter) {
      if (pt[0] > north[0]) north = pt;
    }
    features.push({
      type: "Feature" as const,
      properties: {
        label: iso.label,
        color: iso.color,
        time_hours: iso.timeHours,
      },
      geometry: {
        type: "Point" as const,
        coordinates: [north[1], north[0]], // [lng, lat]
      },
    });
  }
  return { type: "FeatureCollection", features };
}
