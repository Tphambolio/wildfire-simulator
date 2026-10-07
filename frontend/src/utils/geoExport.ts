/**
 * GeoJSON and KML export utilities for FireSim V3.
 *
 * GeoJSON spec: RFC 7946 — coordinates are [lng, lat] order.
 * KML 2.2 — coordinates are lng,lat,alt.
 */

import type { SimulationFrame, BurnProbabilityResponse } from "../types/simulation";
import type { RunParams } from "../components/WeatherPanel";
import type { PlanningEvacZone } from "./evacZones";
import { planningZonesToGeoJSON } from "./evacZones";

// ── Geometry helpers ────────────────────────────────────────────────────────

/** Convert [[lat, lng], ...] perimeter to closed GeoJSON ring [[lng, lat], ...]. */
function perimeterToGeoJSONRing(perimeter: number[][]): number[][] {
  const ring = perimeter.map(([lat, lng]) => [lng, lat]);
  if (ring.length > 0 && (ring[0][0] !== ring[ring.length - 1][0] || ring[0][1] !== ring[ring.length - 1][1])) {
    ring.push(ring[0]); // close ring
  }
  return ring;
}

/** Escape XML special characters. */
function escapeXml(s: string | number): string {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ── Shared types ─────────────────────────────────────────────────────────────

export interface ExportOptions {
  frames: SimulationFrame[];
  burnProbData: BurnProbabilityResponse | null;
  runParams: RunParams | null;
  ignitionPoint: { lat: number; lng: number } | null;
  fuelTypeLabel?: string;
  overlayRoads?: GeoJSON.FeatureCollection | null;
  overlayCommunities?: GeoJSON.FeatureCollection | null;
  overlayInfrastructure?: GeoJSON.FeatureCollection | null;
  /** Evacuation status set by Planning (Order / Alert / Watch), as entered */
  evacZones?: PlanningEvacZone[];
}

function scenarioMeta(runParams: RunParams | null, fuelTypeLabel?: string): Record<string, unknown> {
  if (!runParams) return { source: "FireSim V3 — Canadian FBP", exported_at: new Date().toISOString() };
  return {
    source: "FireSim V3 — Canadian FBP",
    exported_at: new Date().toISOString(),
    wind_speed_kmh: runParams.weather.wind_speed,
    wind_direction_deg: runParams.weather.wind_direction,
    temperature_c: runParams.weather.temperature,
    relative_humidity_pct: runParams.weather.relative_humidity,
    ffmc: runParams.fwi.ffmc,
    dmc: runParams.fwi.dmc,
    dc: runParams.fwi.dc,
    fwi: runParams.fwi_value,
    danger_rating: runParams.danger_rating,
    duration_hours: runParams.duration_hours,
    fuel_type: fuelTypeLabel ?? null,
  };
}

// ── GeoJSON builder ──────────────────────────────────────────────────────────

export function buildGeoJSON(opts: ExportOptions): object {
  const { frames, burnProbData, runParams, ignitionPoint, fuelTypeLabel } = opts;
  const features: object[] = [];
  const meta = scenarioMeta(runParams, fuelTypeLabel);

  // 1. Fire perimeter — final frame (or last frame per day for multi-day)
  if (frames.length > 0) {
    const finalFrame = frames[frames.length - 1];
    const isMultiDay = finalFrame.day != null && finalFrame.day > 1;

    // For multi-day: emit one polygon per day boundary
    if (isMultiDay) {
      const dayEndFrames = new Map<number, SimulationFrame>();
      for (const f of frames) {
        const day = f.day ?? 1;
        dayEndFrames.set(day, f); // last frame for each day
      }
      dayEndFrames.forEach((f, day) => {
        if (f.perimeter.length >= 3) {
          features.push({
            type: "Feature",
            geometry: { type: "Polygon", coordinates: [perimeterToGeoJSONRing(f.perimeter)] },
            properties: {
              layer: "fire_perimeter",
              day,
              is_final: day === finalFrame.day,
              area_ha: f.area_ha,
              time_hours: f.time_hours,
              fire_type: f.fire_type,
              head_ros_m_min: f.head_ros_m_min,
              max_hfi_kw_m: f.max_hfi_kw_m,
              flame_length_m: f.flame_length_m,
              ...meta,
            },
          });
        }
      });
    } else if (finalFrame.perimeter.length >= 3) {
      // Single-day: emit the single final perimeter
      features.push({
        type: "Feature",
        geometry: { type: "Polygon", coordinates: [perimeterToGeoJSONRing(finalFrame.perimeter)] },
        properties: {
          layer: "fire_perimeter",
          is_final: true,
          area_ha: finalFrame.area_ha,
          time_hours: finalFrame.time_hours,
          fire_type: finalFrame.fire_type,
          head_ros_m_min: finalFrame.head_ros_m_min,
          max_hfi_kw_m: finalFrame.max_hfi_kw_m,
          flame_length_m: finalFrame.flame_length_m,
          ...meta,
        },
      });
    }
  }

  // 2. Ignition point
  if (ignitionPoint) {
    features.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: [ignitionPoint.lng, ignitionPoint.lat] },
      properties: { layer: "ignition_point", ...meta },
    });
  }

  // 3. Spot fires (deduplicated by position)
  const seenSpots = new Set<string>();
  for (const f of frames) {
    for (const s of f.spot_fires ?? []) {
      const key = `${s.lat.toFixed(5)},${s.lng.toFixed(5)}`;
      if (seenSpots.has(key)) continue;
      seenSpots.add(key);
      features.push({
        type: "Feature",
        geometry: { type: "Point", coordinates: [s.lng, s.lat] },
        properties: {
          layer: "spot_fire",
          distance_m: s.distance_m,
          hfi_kw_m: s.hfi_kw_m,
          time_hours: f.time_hours,
        },
      });
    }
  }

  // 4. Burn probability grid — one polygon per cell at P ≥ 0.05
  //    Properties include burn_probability so QGIS can classify/filter.
  if (burnProbData) {
    const { burn_probability, rows, cols, lat_min, lat_max, lng_min, lng_max, cell_size_m } = burnProbData;
    const latStep = (lat_max - lat_min) / rows;
    const lngStep = (lng_max - lng_min) / cols;

    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const p = burn_probability[r]?.[c] ?? 0;
        if (p < 0.05) continue; // Skip near-zero cells to keep file size manageable

        const cellLatMin = lat_min + r * latStep;
        const cellLatMax = cellLatMin + latStep;
        const cellLngMin = lng_min + c * lngStep;
        const cellLngMax = cellLngMin + lngStep;

        features.push({
          type: "Feature",
          geometry: {
            type: "Polygon",
            coordinates: [[
              [cellLngMin, cellLatMin],
              [cellLngMax, cellLatMin],
              [cellLngMax, cellLatMax],
              [cellLngMin, cellLatMax],
              [cellLngMin, cellLatMin],
            ]],
          },
          properties: {
            layer: "burn_probability",
            burn_probability: Math.round(p * 1000) / 1000,
            cell_size_m,
          },
        });
      }
    }
  }

  // 5. At-risk infrastructure (from overlay layers — only _at_risk features)
  const overlayLayerMap: [string, GeoJSON.FeatureCollection | null | undefined][] = [
    ["community", opts.overlayCommunities],
    ["road", opts.overlayRoads],
    ["infrastructure", opts.overlayInfrastructure],
  ];
  for (const [layerName, fc] of overlayLayerMap) {
    if (!fc) continue;
    for (const f of fc.features) {
      if (f.properties?._at_risk) {
        const { _at_risk, ...rest } = f.properties ?? {};
        void _at_risk; // intentionally unused — removing internal flag from export
        features.push({
          ...f,
          properties: { ...rest, layer: `at_risk_${layerName}` },
        });
      }
    }
  }

  // 6. Evacuation status set by Planning (neighbourhood polygons tagged with the tier)
  if (opts.evacZones && opts.evacZones.length > 0) {
    const zoneFc = planningZonesToGeoJSON(opts.evacZones);
    for (const feat of zoneFc.features) {
      features.push({ ...feat, properties: { ...feat.properties, layer: "evacuation_status_planning" } });
    }
  }

  return { type: "FeatureCollection", features };
}

// ── KML builder ──────────────────────────────────────────────────────────────

export function buildKML(opts: ExportOptions): string {
  const { frames, runParams, ignitionPoint, fuelTypeLabel } = opts;
  const now = new Date().toISOString();
  const windDir = runParams?.weather.wind_direction ?? "—";
  const windSpd = runParams?.weather.wind_speed ?? "—";
  const fwi = runParams ? runParams.fwi_value.toFixed(1) : "—";
  const rating = runParams?.danger_rating ?? "—";
  const fuel = fuelTypeLabel ?? "—";

  const lines: string[] = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<kml xmlns="http://www.opengis.net/kml/2.2">',
    '  <Document>',
    `    <name>FireSim Export — ${escapeXml(now)}</name>`,
    '    <description>FireSim V3 — Canadian FBP Wildfire Spread Simulator</description>',
    '',
    '    <Style id="style_fire_perimeter">',
    '      <LineStyle><color>ff0000ff</color><width>2</width></LineStyle>',
    '      <PolyStyle><color>660000ff</color><fill>1</fill><outline>1</outline></PolyStyle>',
    '    </Style>',
    '    <Style id="style_fire_perimeter_day">',
    '      <LineStyle><color>ff0080ff</color><width>1</width></LineStyle>',
    '      <PolyStyle><color>220080ff</color><fill>1</fill><outline>1</outline></PolyStyle>',
    '    </Style>',
    '    <Style id="style_ignition">',
    '      <IconStyle><color>ff0000ff</color><scale>1.2</scale>',
    '        <Icon><href>http://maps.google.com/mapfiles/kml/paddle/red-circle.png</href></Icon>',
    '      </IconStyle>',
    '    </Style>',
    '    <Style id="style_spot_fire">',
    '      <IconStyle><color>ff00ffff</color><scale>0.8</scale>',
    '        <Icon><href>http://maps.google.com/mapfiles/kml/paddle/ylw-circle.png</href></Icon>',
    '      </IconStyle>',
    '    </Style>',
    // Evacuation status (Planning): blue outlines, no fill; width differs by tier (KML has no dashes)
    '    <Style id="style_evac_order">',
    '      <LineStyle><color>ffd84e1d</color><width>4</width></LineStyle>',
    '      <PolyStyle><fill>0</fill><outline>1</outline></PolyStyle>',
    '    </Style>',
    '    <Style id="style_evac_alert">',
    '      <LineStyle><color>ffd84e1d</color><width>3</width></LineStyle>',
    '      <PolyStyle><fill>0</fill><outline>1</outline></PolyStyle>',
    '    </Style>',
    '    <Style id="style_evac_watch">',
    '      <LineStyle><color>ffd84e1d</color><width>2</width></LineStyle>',
    '      <PolyStyle><fill>0</fill><outline>1</outline></PolyStyle>',
    '    </Style>',
  ];

  // Fire perimeter(s)
  if (frames.length > 0) {
    const finalFrame = frames[frames.length - 1];
    const isMultiDay = finalFrame.day != null && finalFrame.day > 1;

    if (isMultiDay) {
      lines.push('    <Folder><name>Fire Perimeters by Day</name>');
      const dayEndFrames = new Map<number, SimulationFrame>();
      for (const f of frames) {
        const day = f.day ?? 1;
        dayEndFrames.set(day, f);
      }
      dayEndFrames.forEach((f, day) => {
        if (f.perimeter.length < 3) return;
        const coordStr = perimeterToGeoJSONRing(f.perimeter)
          .map(([lng, lat]) => `${lng},${lat},0`)
          .join(" ");
        const isFinal = day === finalFrame.day;
        lines.push('      <Placemark>');
        lines.push(`        <name>Fire Perimeter — Day ${day}${isFinal ? " (Final)" : ""}</name>`);
        lines.push(`        <styleUrl>#style_${isFinal ? "fire_perimeter" : "fire_perimeter_day"}</styleUrl>`);
        lines.push(`        <description>${escapeXml(
          `Day ${day} | Area: ${f.area_ha.toFixed(1)} ha | ` +
          `Fire Type: ${f.fire_type.replace(/_/g, " ")} | ` +
          `Peak HFI: ${f.max_hfi_kw_m.toFixed(0)} kW/m`
        )}</description>`);
        lines.push('        <Polygon><outerBoundaryIs><LinearRing>');
        lines.push(`          <coordinates>${coordStr}</coordinates>`);
        lines.push('        </LinearRing></outerBoundaryIs></Polygon>');
        lines.push('      </Placemark>');
      });
      lines.push('    </Folder>');
    } else if (finalFrame.perimeter.length >= 3) {
      const coordStr = perimeterToGeoJSONRing(finalFrame.perimeter)
        .map(([lng, lat]) => `${lng},${lat},0`)
        .join(" ");
      lines.push('    <Placemark>');
      lines.push('      <name>Fire Perimeter</name>');
      lines.push('      <styleUrl>#style_fire_perimeter</styleUrl>');
      lines.push(`      <description>${escapeXml(
        `Area: ${finalFrame.area_ha.toFixed(1)} ha | ` +
        `Fire Type: ${finalFrame.fire_type.replace(/_/g, " ")} | ` +
        `Peak HFI: ${finalFrame.max_hfi_kw_m.toFixed(0)} kW/m | ` +
        `FWI: ${fwi} (${rating}) | Wind: ${windSpd} km/h @ ${windDir}° | Fuel: ${fuel}`
      )}</description>`);
      lines.push('      <Polygon><outerBoundaryIs><LinearRing>');
      lines.push(`        <coordinates>${coordStr}</coordinates>`);
      lines.push('      </LinearRing></outerBoundaryIs></Polygon>');
      lines.push('    </Placemark>');
    }
  }

  // Ignition point
  if (ignitionPoint) {
    lines.push('    <Placemark>');
    lines.push('      <name>Ignition Point</name>');
    lines.push('      <styleUrl>#style_ignition</styleUrl>');
    lines.push(`      <description>${escapeXml(
      `Lat: ${ignitionPoint.lat.toFixed(5)}  Lng: ${ignitionPoint.lng.toFixed(5)} | ` +
      `Wind: ${windSpd} km/h @ ${windDir}° | FWI: ${fwi} (${rating}) | Fuel: ${fuel}`
    )}</description>`);
    lines.push('      <Point>');
    lines.push(`        <coordinates>${ignitionPoint.lng},${ignitionPoint.lat},0</coordinates>`);
    lines.push('      </Point>');
    lines.push('    </Placemark>');
  }

  // Spot fires
  const seenSpots = new Set<string>();
  const spotPlacemarks: string[] = [];
  for (const f of frames) {
    for (const s of f.spot_fires ?? []) {
      const key = `${s.lat.toFixed(5)},${s.lng.toFixed(5)}`;
      if (seenSpots.has(key)) continue;
      seenSpots.add(key);
      spotPlacemarks.push(
        `    <Placemark>`,
        `      <name>Spot Fire (t=${f.time_hours}h)</name>`,
        `      <styleUrl>#style_spot_fire</styleUrl>`,
        `      <description>${escapeXml(`Distance: ${s.distance_m.toFixed(0)} m | HFI: ${s.hfi_kw_m.toFixed(0)} kW/m`)}</description>`,
        `      <Point><coordinates>${s.lng},${s.lat},0</coordinates></Point>`,
        `    </Placemark>`,
      );
    }
  }
  if (spotPlacemarks.length > 0) {
    lines.push('    <Folder><name>Spot Fires</name>');
    lines.push(...spotPlacemarks);
    lines.push('    </Folder>');
  }

  // Evacuation status set by Planning: one placemark per neighbourhood polygon
  if (opts.evacZones && opts.evacZones.length > 0) {
    lines.push('    <Folder><name>Evacuation status (set by Planning)</name>');
    for (const feat of planningZonesToGeoJSON(opts.evacZones).features) {
      const p = feat.properties as { neighbourhood: string; evac_tier: string; set_at: string | null };
      const g = feat.geometry;
      const polys = g.type === "Polygon" ? [g.coordinates] : g.type === "MultiPolygon" ? g.coordinates : [];
      if (polys.length === 0) continue;
      lines.push('      <Placemark>');
      lines.push(`        <name>${escapeXml(`Evacuation ${p.evac_tier}: ${p.neighbourhood}`)}</name>`);
      lines.push(`        <styleUrl>#style_evac_${p.evac_tier.toLowerCase()}</styleUrl>`);
      lines.push(`        <description>${escapeXml(
        `Set by Planning${p.set_at ? ` at ${p.set_at}` : ""}. FireSim does not recommend evacuation tiers.`
      )}</description>`);
      lines.push('        <MultiGeometry>');
      for (const poly of polys) {
        const coordStr = poly[0].map(([lng, lat]) => `${lng},${lat},0`).join(" ");
        lines.push('          <Polygon><outerBoundaryIs><LinearRing>');
        lines.push(`            <coordinates>${coordStr}</coordinates>`);
        lines.push('          </LinearRing></outerBoundaryIs></Polygon>');
      }
      lines.push('        </MultiGeometry>');
      lines.push('      </Placemark>');
    }
    lines.push('    </Folder>');
  }

  lines.push('  </Document>');
  lines.push('</kml>');
  return lines.join("\n");
}

// ── Download helper ───────────────────────────────────────────────────────────

export function downloadFile(content: string, filename: string, mimeType: string): void {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
