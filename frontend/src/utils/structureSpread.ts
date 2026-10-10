/**
 * House-to-house (structure) spread: series for the chart, GeoJSON for the map layer, and the
 * caveat text. Illustrative — not validated in Canada (docs/structure-spread-spec.md §9).
 * Per-building output is map display only (owner decision 2026-10-10): never exported.
 */
import type { SimulationFrame, StructureUnitDetail } from "../types/simulation";

/** Map and legend colours: magenta / aqua, outside the fire, fuel, evac and isochrone families
 * (tokens.css --struct-front / --struct-b2b) */
export const STRUCT_FRONT = "#e94ccf";
export const STRUCT_B2B = "#2fd9c5";

export const MECHANISM_LABEL: Record<StructureUnitDetail["mechanism"], string> = {
  front: "Front contact",
  b2b: "Building to building",
};

/** Full caveat (tooltip text). `cutoffM` / `cellM` come from the run when known. */
export function structureCaveat(label: string, cutoffM = 30, cellM = 50): string {
  const l = label.charAt(0).toUpperCase() + label.slice(1);
  return (
    `${l}. Shows modelled involvement, not a prediction of which buildings will burn. ` +
    `Fire passes between buildings up to ${cutoffM} m apart; front contact is measured on the ` +
    `~${cellM} m fire grid.`
  );
}

export interface StructurePoint {
  t: number; // hours
  front: number;
  b2b: number;
}

/** Involved units per frame (frames without computed counts are skipped). */
export function structureSeries(frames: SimulationFrame[]): StructurePoint[] {
  const out: StructurePoint[] = [];
  for (const f of frames) {
    const s = f.structure_spread;
    if (!s || s.computed === false || s.units_involved == null) continue;
    out.push({ t: f.time_hours, front: s.units_front_contact ?? 0, b2b: s.units_structure_to_structure ?? 0 });
  }
  return out;
}

/** The involved-unit detail of the run (carried by the final frame). */
export function structureDetail(frames: SimulationFrame[]): StructureUnitDetail[] | null {
  for (let i = frames.length - 1; i >= 0; i--) {
    const d = frames[i].structure_spread_detail;
    if (d) return d;
  }
  return null;
}

/** Involved footprints as GeoJSON polygons with t_h and mechanism (map layer source). */
export function structureUnitsGeoJSON(detail: StructureUnitDetail[] | null): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: (detail ?? [])
      .filter((u) => u.polygon?.[0]?.length >= 4)
      .map((u) => ({
        type: "Feature",
        id: u.id,
        geometry: { type: "Polygon", coordinates: u.polygon },
        properties: { id: u.id, t_h: u.t_h, mechanism: u.mechanism },
      })),
  };
}

/** Tooltip lines for one unit: involvement time and mechanism only. */
export function describeUnit(tH: number, mechanism: string, clock?: string): string {
  const h = Math.floor(tH);
  const m = Math.round((tH - h) * 60);
  const elapsed = `${h} h ${String(m).padStart(2, "0")} min`;
  const mech = MECHANISM_LABEL[mechanism as StructureUnitDetail["mechanism"]] ?? mechanism;
  return `${mech} · involved at ${clock ? `${clock} (+${elapsed})` : `+${elapsed}`}`;
}
