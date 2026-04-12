/**
 * Live infrastructure data fetch service.
 *
 * Queries Overpass (OSM) and Environment Canada weather alerts, scoped to a
 * bounding box derived from the incident location. Queries are incident-type
 * aware — different hazard types pull different OSM features.
 *
 * Rules (per research spec):
 *   - Always scope to bbox. No unbounded queries.
 *   - Prefer Canadian sources over HIFLD.
 *   - Fire all queries in parallel with Promise.all.
 *   - Overpass timeout minimum 25 seconds.
 *   - Primary endpoint: overpass-api.de; fallback: overpass.kumi.systems.
 *   - Environment Canada alerts run on every incident type.
 *   - Warn if bbox exceeds 0.5° in either dimension.
 */

import type { HazardType } from "../types/incident";

// ── Bbox ─────────────────────────────────────────────────────────────────────

/** [west, south, east, north] WGS84 */
export type Bbox = [number, number, number, number];

/** Build a bbox from a centre point and a half-size in degrees. */
export function bboxFromCenter(lat: number, lng: number, halfDeg = 0.18): Bbox {
  return [lng - halfDeg, lat - halfDeg, lng + halfDeg, lat + halfDeg];
}

// ── Overpass client ───────────────────────────────────────────────────────────

const OVERPASS_PRIMARY  = "https://overpass-api.de/api/interpreter";
const OVERPASS_FALLBACK = "https://overpass.kumi.systems/api/interpreter";

interface OverpassElement {
  type: "node" | "way" | "relation";
  id: number;
  lat?: number;
  lon?: number;
  center?: { lat: number; lon: number };
  geometry?: { lat: number; lon: number }[];
  tags?: Record<string, string>;
}

async function runOverpass(ql: string): Promise<OverpassElement[]> {
  const body = new URLSearchParams({ data: ql }).toString();
  const headers = { "Content-Type": "application/x-www-form-urlencoded" };

  async function attempt(url: string) {
    const res = await fetch(url, { method: "POST", headers, body });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const json = await res.json() as { elements: OverpassElement[] };
    return json.elements ?? [];
  }

  try {
    return await attempt(OVERPASS_PRIMARY);
  } catch {
    return await attempt(OVERPASS_FALLBACK);
  }
}

// ── Overpass → GeoJSON converters ─────────────────────────────────────────────

function elementsToPointFC(
  elements: OverpassElement[],
  labelFn: (tags: Record<string, string>) => string,
  typeFn: (tags: Record<string, string>) => string,
): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = elements
    .filter(el => {
      const lat = el.type === "node" ? el.lat : el.center?.lat;
      const lng = el.type === "node" ? el.lon : el.center?.lon;
      return lat !== undefined && lng !== undefined;
    })
    .map(el => {
      const lat = (el.type === "node" ? el.lat : el.center?.lat) as number;
      const lng = (el.type === "node" ? el.lon : el.center?.lon) as number;
      const tags = el.tags ?? {};
      return {
        type: "Feature" as const,
        geometry: { type: "Point" as const, coordinates: [lng, lat] },
        properties: {
          name: tags.name ?? tags["name:en"] ?? `${typeFn(tags)} (unnamed)`,
          type: typeFn(tags),
          label: labelFn(tags),
          osmId: `${el.type}/${el.id}`,
          ...Object.fromEntries(
            ["phone", "contact:phone", "operator", "amenity", "highway",
             "power", "man_made", "emergency", "aeroway"].map(k => [k, tags[k] ?? ""])
          ),
        },
      };
    });
  return { type: "FeatureCollection", features };
}

function elementsToLineFC(elements: OverpassElement[]): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = elements
    .filter(el => el.type === "way" && el.geometry && el.geometry.length >= 2)
    .map(el => {
      const tags = el.tags ?? {};
      return {
        type: "Feature" as const,
        geometry: {
          type: "LineString" as const,
          coordinates: (el.geometry!).map(pt => [pt.lon, pt.lat]),
        },
        properties: {
          name: tags.name ?? tags.ref ?? "Road",
          highway: tags.highway ?? "",
          ref: tags.ref ?? "",
          osmId: `way/${el.id}`,
        },
      };
    });
  return { type: "FeatureCollection", features };
}

// ── Incident-type Overpass queries ────────────────────────────────────────────
// Bbox for Overpass uses (south,west,north,east) order.

function ovBbox([w, s, e, n]: Bbox) {
  return `${s},${w},${n},${e}`;
}

function infraQuery(bbox: Bbox, hazard: HazardType): string {
  const b = ovBbox(bbox);
  const base = `[out:json][timeout:25];\n(\n`;
  const end  = `);\nout center tags;`;

  switch (hazard) {
    case "wildfire_smoke":
      return `${base}  node["amenity"~"hospital|fire_station"](${b});
  node["aeroway"="helipad"](${b});
  node["power"="substation"](${b});
  node["man_made"~"water_tower|water_works"](${b});
${end}`;

    case "flood":
      return `${base}  node["man_made"~"water_works|pumping_station|flood_control"](${b});
  node["amenity"~"hospital|shelter|community_centre"](${b});
  node["emergency"="shelter"](${b});
${end}`;

    case "hazmat":
      return `${base}  node["amenity"~"hospital|school|water_point"](${b});
  node["man_made"="storage_tank"](${b});
  node["amenity"~"fire_station|police"](${b});
${end}`;

    case "mass_casualty":
      return `${base}  node["amenity"~"hospital|clinic|doctors"](${b});
  node["aeroway"="helipad"](${b});
  node["amenity"~"fire_station|police"](${b});
${end}`;

    case "severe_weather":
      return `${base}  node["power"="substation"](${b});
  node["amenity"~"shelter|community_centre|hospital"](${b});
  node["emergency"="shelter"](${b});
${end}`;

    case "evacuation":
      return `${base}  node["amenity"~"fuel|hospital|shelter"](${b});
  node["emergency"~"shelter|assembly_point"](${b});
${end}`;

    default: // infrastructure, other, general
      return `${base}  node["amenity"~"hospital|fire_station|police|fuel"](${b});
  node["emergency"~"shelter|assembly_point"](${b});
  node["aeroway"="helipad"](${b});
${end}`;
  }
}

function roadsQuery(bbox: Bbox, hazard: HazardType): string {
  const b = ovBbox(bbox);
  // Mass evacuation and flood need full network; others use arterials only
  const classes = (hazard === "evacuation" || hazard === "flood")
    ? "primary|secondary|tertiary|residential"
    : "primary|secondary|tertiary";
  return `[out:json][timeout:25];
(
  way["highway"~"${classes}"](${b});
);
out geom tags;`;
}

// ── Infrastructure type labelling ─────────────────────────────────────────────

function infraTypeFromTags(tags: Record<string, string>): string {
  if (tags.amenity === "hospital")            return "Hospital";
  if (tags.amenity === "fire_station")        return "Fire Station";
  if (tags.amenity === "police")              return "Police";
  if (tags.aeroway === "helipad")             return "Helipad";
  if (tags.amenity === "fuel")                return "Fuel Station";
  if (tags.amenity === "shelter" || tags.social_facility === "shelter" || tags.emergency === "shelter")
                                              return "Emergency Shelter";
  if (tags.amenity === "community_centre")    return "Community Centre";
  if (tags.power === "substation")            return "Power Substation";
  if (tags.man_made === "water_tower")        return "Water Tower";
  if (tags.man_made === "water_works")        return "Water Works";
  if (tags.man_made === "pumping_station")    return "Pumping Station";
  if (tags.man_made === "flood_control")      return "Flood Control";
  if (tags.man_made === "storage_tank")       return "Storage Tank";
  if (tags.amenity === "school")              return "School";
  if (tags.amenity === "clinic")              return "Clinic";
  return "Infrastructure";
}

// ── Environment Canada weather alerts ─────────────────────────────────────────

export interface WeatherAlert {
  headline: string;
  severity: string;
  urgency: string;
  description: string;
  effective: string;
  expires: string;
  geometry?: GeoJSON.Geometry;
}

async function fetchWeatherAlerts(bbox: Bbox): Promise<WeatherAlert[]> {
  const [w, s, e, n] = bbox;
  const url = new URL("https://api.weather.gc.ca/collections/weather-alerts/items");
  url.searchParams.set("f", "json");
  url.searchParams.set("lang", "en");
  url.searchParams.set("bbox", `${w},${s},${e},${n}`);
  url.searchParams.set("limit", "20");

  try {
    const res = await fetch(url.toString(), { signal: AbortSignal.timeout(10_000) });
    if (!res.ok) return [];
    const fc = await res.json() as GeoJSON.FeatureCollection;
    return (fc.features ?? []).map(f => ({
      headline:    (f.properties?.headline    as string) ?? (f.properties?.event as string) ?? "Weather Alert",
      severity:    (f.properties?.severity    as string) ?? "Unknown",
      urgency:     (f.properties?.urgency     as string) ?? "Unknown",
      description: (f.properties?.description as string) ?? "",
      effective:   (f.properties?.effective   as string) ?? "",
      expires:     (f.properties?.expires     as string) ?? "",
      geometry:    f.geometry ?? undefined,
    }));
  } catch {
    return [];
  }
}

// ── Public API ────────────────────────────────────────────────────────────────

export interface LiveLayerResult {
  roads:         GeoJSON.FeatureCollection;
  infrastructure: GeoJSON.FeatureCollection;
  weatherAlerts: WeatherAlert[];
  warnings:      string[];
  source:        "live";
  fetchedAt:     string;
}

export async function fetchLiveLayers(
  lat: number,
  lng: number,
  hazardType: HazardType = "other",
  halfDeg = 0.18,
): Promise<LiveLayerResult> {
  const bbox = bboxFromCenter(lat, lng, halfDeg);
  const warnings: string[] = [];

  const bboxSpan = Math.max(bbox[2] - bbox[0], bbox[3] - bbox[1]);
  if (bboxSpan > 0.5) {
    warnings.push(`Bbox span ${bboxSpan.toFixed(2)}° is large — query may be slow.`);
  }

  // Fire all queries in parallel
  const [infraElements, roadElements, alerts] = await Promise.all([
    runOverpass(infraQuery(bbox, hazardType)).catch(() => [] as OverpassElement[]),
    runOverpass(roadsQuery(bbox, hazardType)).catch(() => [] as OverpassElement[]),
    fetchWeatherAlerts(bbox),
  ]);

  const infrastructure = elementsToPointFC(
    infraElements,
    tags => tags.name ?? tags["name:en"] ?? infraTypeFromTags(tags),
    tags => infraTypeFromTags(tags),
  );

  const roads = elementsToLineFC(roadElements);

  if (infrastructure.features.length === 0) {
    warnings.push("Infrastructure query returned 0 features — check bbox or Overpass status.");
  }

  return {
    roads,
    infrastructure,
    weatherAlerts: alerts,
    warnings,
    source: "live",
    fetchedAt: new Date().toISOString(),
  };
}
