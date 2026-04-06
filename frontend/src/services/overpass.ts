/**
 * Overpass API client — fetches OSM community assets near a point and
 * normalises them into IncidentAnnotation-compatible records.
 *
 * Single combined POST query; CORS-enabled on the public endpoint.
 */

import type { ICSSymbolKey, AnnotationLayer } from "../types/incident";

export interface NormalizedFacility {
  osmId: string;       // "node/12345" | "way/67890"
  name: string;
  phone: string;       // phone || contact:phone || ""
  address: string;     // housenumber + street + city
  lat: number;
  lng: number;
  symbolKey: ICSSymbolKey;
  layer: AnnotationLayer;
  properties: Record<string, string>; // speciality, operator, capacity, etc.
}

// ── OSM tag → ICS symbol mapping (first match wins) ──────────────────────────

type TagMap = Record<string, string>;

const OSM_TO_SYMBOL: Array<{
  test: (t: TagMap) => boolean;
  symbolKey: ICSSymbolKey;
  layer: AnnotationLayer;
}> = [
  { test: t => t.amenity === "hospital",                          symbolKey: "hospital",           layer: "ics206"    },
  { test: t => t.emergency === "ambulance_station",               symbolKey: "ambulance_staging",  layer: "ics206"    },
  { test: t => t.aeroway === "helipad",                           symbolKey: "medevac_lz",         layer: "ics206"    },
  { test: t => t.amenity === "clinic" && t.emergency === "yes",   symbolKey: "medical_aid_station",layer: "ics206"    },
  { test: t => t.amenity === "pharmacy",                          symbolKey: "pharmacy",           layer: "ics206"    },
  { test: t => t.amenity === "fire_station",                      symbolKey: "fire_station",       layer: "ics204"    },
  { test: t => t.emergency === "assembly_point",                  symbolKey: "assembly_point",     layer: "evac"      },
  { test: t => t.amenity === "community_centre",                  symbolKey: "reception_centre",   layer: "evac"      },
  { test: t => t.social_facility === "shelter",                   symbolKey: "shelter",            layer: "evac"      },
  { test: t => t.amenity === "fuel",                              symbolKey: "fuel_station",       layer: "evac"      },
  { test: t => t.amenity === "police",                            symbolKey: "police_station",     layer: "situation" },
];

// ── Overpass QL query ─────────────────────────────────────────────────────────

function buildQuery(lat: number, lng: number): string {
  const a = (radius: number, tag: string) =>
    `node[${tag}](around:${radius},${lat},${lng});\n  way[${tag}](around:${radius},${lat},${lng});`;
  const n = (radius: number, tag: string) =>
    `node[${tag}](around:${radius},${lat},${lng});`;

  return `[out:json][timeout:60];
(
  ${a(80000, '"amenity"="hospital"')}
  ${a(60000, '"emergency"="ambulance_station"')}
  ${n(80000, '"aeroway"="helipad"')}
  ${n(30000, '"amenity"="clinic"]["emergency"="yes"')}
  ${n(20000, '"amenity"="pharmacy"')}
  ${a(60000, '"amenity"="fire_station"')}
  ${n(30000, '"emergency"="assembly_point"')}
  ${a(20000, '"amenity"="community_centre"')}
  ${n(20000, '"social_facility"="shelter"')}
  ${n(30000, '"amenity"="fuel"')}
  ${a(50000, '"amenity"="police"')}
);
out center tags;`;
}

// ── Normalisation ─────────────────────────────────────────────────────────────

interface OverpassElement {
  type: "node" | "way" | "relation";
  id: number;
  lat?: number;
  lon?: number;
  center?: { lat: number; lon: number };
  tags?: TagMap;
}

function normalise(el: OverpassElement): NormalizedFacility | null {
  const tags = el.tags ?? {};

  // Resolve lat/lng
  const lat = el.type === "node" ? el.lat : el.center?.lat;
  const lng = el.type === "node" ? el.lon : el.center?.lon;
  if (lat === undefined || lng === undefined) return null;

  // Find first matching symbol rule
  const match = OSM_TO_SYMBOL.find(r => r.test(tags));
  if (!match) return null;

  const name = tags.name ?? tags["name:en"] ?? "";
  if (!name) return null; // skip unnamed features

  const phone = tags.phone ?? tags["contact:phone"] ?? tags["emergency:phone"] ?? "";
  const address = [tags["addr:housenumber"], tags["addr:street"], tags["addr:city"]]
    .filter(Boolean).join(" ");

  // Collect extra properties
  const extra: Record<string, string> = {};
  for (const k of ["operator", "healthcare:speciality", "beds", "capacity", "opening_hours", "emergency"]) {
    if (tags[k]) extra[k] = tags[k];
  }

  return {
    osmId: `${el.type}/${el.id}`,
    name,
    phone,
    address,
    lat,
    lng,
    symbolKey: match.symbolKey,
    layer: match.layer,
    properties: extra,
  };
}

// ── Public API ────────────────────────────────────────────────────────────────

export async function fetchNearbyFacilities(
  lat: number,
  lng: number,
): Promise<NormalizedFacility[]> {
  try {
    const query = buildQuery(lat, lng);
    const res = await fetch("https://overpass-api.de/api/interpreter", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ data: query }).toString(),
    });
    if (!res.ok) {
      console.warn("Overpass API error:", res.status, res.statusText);
      return [];
    }
    const json = await res.json() as { elements: OverpassElement[] };
    return (json.elements ?? []).map(normalise).filter((f): f is NormalizedFacility => f !== null);
  } catch (err) {
    console.warn("Overpass fetch failed:", err);
    return [];
  }
}
