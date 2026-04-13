/**
 * Maps infrastructure overlay feature types to ICS annotation layer + symbol key.
 * Used by the "Auto-place ICS Symbols" button in the Infrastructure Overlay panel.
 */

import type { IncidentAnnotation, ICSSymbolKey, AnnotationLayer } from "../types/incident";

interface SymbolMapping {
  layer: AnnotationLayer;
  symbolKey: ICSSymbolKey;
}

const TYPE_MAP: Record<string, SymbolMapping> = {
  "Hospital":            { layer: "ics206", symbolKey: "hospital" },
  "Medical Aid Station": { layer: "ics206", symbolKey: "medical_aid_station" },
  "Clinic":              { layer: "ics206", symbolKey: "medical_aid_station" },
  "Helipad":             { layer: "ics206", symbolKey: "medevac_lz" },
  "Fire Station":        { layer: "ics204", symbolKey: "fire_station" },
  "Police":              { layer: "ics204", symbolKey: "police_station" },
  "Fuel Station":        { layer: "ics204", symbolKey: "fuel_station" },
  "EOC":                 { layer: "situation", symbolKey: "command_post" },
  "Emergency Shelter":   { layer: "evac",      symbolKey: "assembly_point" },
  "Community Centre":    { layer: "evac",      symbolKey: "reception_centre" },
  "Water Tower":         { layer: "situation", symbolKey: "water_fill" },
  "Water Works":         { layer: "situation", symbolKey: "water_fill" },
  "Pumping Station":     { layer: "situation", symbolKey: "water_fill" },
  "Power Substation":    { layer: "situation", symbolKey: "generic_point" },
  "Power Generation":    { layer: "situation", symbolKey: "generic_point" },
  "Storage Tank":        { layer: "situation", symbolKey: "generic_point" },
  "Flood Control":       { layer: "situation", symbolKey: "generic_point" },
};

/**
 * Convert infrastructure GeoJSON features into ICS annotation objects.
 * Only features with a recognised `type` property are converted.
 * Caller should deduplicate against existing annotations by `properties.osmId`.
 */
export function autoSymbolsFromInfra(
  features: GeoJSON.Feature[],
  operationalDay: number,
): IncidentAnnotation[] {
  const results: IncidentAnnotation[] = [];

  for (const f of features) {
    const type = f.properties?.type as string | undefined;
    if (!type || !TYPE_MAP[type]) continue;
    if (f.geometry?.type !== "Point") continue;

    const { layer, symbolKey } = TYPE_MAP[type];
    const coords = (f.geometry as GeoJSON.Point).coordinates as [number, number];
    const name = (f.properties?.name as string | undefined) ?? type;

    results.push({
      id: crypto.randomUUID(),
      layer,
      type: "symbol",
      symbolKey,
      coordinates: [coords],
      label: name,
      color: undefined,
      properties: {
        osmId: (f.properties?.osmId as string | undefined) ?? "",
        type,
      },
      operationalDay,
      createdAt: new Date().toISOString(),
    });
  }

  return results;
}
