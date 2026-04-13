/**
 * mapSchematic.ts — SVG schematic map generator for ICS forms.
 *
 * Renders a coordinate-accurate (but not metrically scaled) schematic showing
 * hazard zones and layer-filtered annotations. Used in the IAP package for
 * ICS-202, ICS-204, ICS-206, and ICS-208 maps so each form shows only the
 * spatial data relevant to that section.
 *
 * Coordinate order throughout: GeoJSON standard — [lng, lat].
 */

import type { AnnotationLayer, IncidentAnnotation, HazardZone } from "../types/incident";
import { SYMBOL_DEFS } from "../types/incident";

// ── Canvas dimensions ─────────────────────────────────────────────────────────

const W = 700;
const H = 440;
const PAD = 44;

// ── Colour lookup from SYMBOL_DEFS ────────────────────────────────────────────

const SYMBOL_COLOR: Record<string, string> = Object.fromEntries(
  SYMBOL_DEFS.map((s) => [s.key, s.color])
);

// ── Bounding box ──────────────────────────────────────────────────────────────

interface Bbox { minLat: number; maxLat: number; minLng: number; maxLng: number; }

function expandBbox(bb: Bbox, frac = 0.28): Bbox {
  const dlat = (bb.maxLat - bb.minLat) * frac || 0.006;
  const dlng = (bb.maxLng - bb.minLng) * frac || 0.006;
  return { minLat: bb.minLat - dlat, maxLat: bb.maxLat + dlat, minLng: bb.minLng - dlng, maxLng: bb.maxLng + dlng };
}

// ── Coordinate → SVG transform ────────────────────────────────────────────────
// Corrects for longitude compression at higher latitudes (Mercator-like).
// Returns [x, y] in SVG pixel space.

function toXY(lat: number, lng: number, bb: Bbox): [number, number] {
  const midLat = (bb.minLat + bb.maxLat) / 2;
  const cosLat = Math.cos((midLat * Math.PI) / 180);
  const lngSpan = (bb.maxLng - bb.minLng) * cosLat || 0.001;
  const latSpan = bb.maxLat - bb.minLat || 0.001;
  const drawW = W - PAD * 2;
  const drawH = H - PAD * 2;
  const scale = Math.min(drawW / lngSpan, drawH / latSpan);
  const usedW = lngSpan * scale;
  const usedH = latSpan * scale;
  const ox = PAD + (drawW - usedW) / 2;
  const oy = PAD + (drawH - usedH) / 2;
  return [ox + (lng - bb.minLng) * cosLat * scale, oy + (bb.maxLat - lat) * scale];
}

// ── Symbol renderer ───────────────────────────────────────────────────────────

function symbolSVG(cx: number, cy: number, key: string, r = 9): string {
  const fill = SYMBOL_COLOR[key] ?? "#78909C";
  const s = `stroke="#333" stroke-width="1.2"`;
  switch (key) {
    case "command_post":
    case "incident_cp":
      return `<polygon points="${cx},${cy - r} ${cx + r * 0.87},${cy + r * 0.5} ${cx - r * 0.87},${cy + r * 0.5}" fill="${fill}" ${s}/>`;
    case "hospital":
    case "medical_aid_station": {
      const h = r * 0.55;
      return `<rect x="${cx - h}" y="${cy - r}" width="${h * 2}" height="${r * 2}" fill="${fill}" ${s}/>`
           + `<rect x="${cx - r}" y="${cy - h}" width="${r * 2}" height="${h * 2}" fill="${fill}" ${s}/>`;
    }
    case "medevac_lz":
      return `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${fill}" ${s}/>`
           + `<text x="${cx}" y="${cy + 4}" text-anchor="middle" font-size="10" font-weight="800" fill="white">H</text>`;
    case "ambulance_staging":
      return `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${fill}" ${s}/>`
           + `<text x="${cx}" y="${cy + 4}" text-anchor="middle" font-size="9" font-weight="800" fill="white">A</text>`;
    case "assembly_point":
      return `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${fill}" ${s}/>`
           + `<text x="${cx}" y="${cy + 4}" text-anchor="middle" font-size="9" font-weight="800" fill="white">A</text>`;
    case "checkpoint":
      return `<polygon points="${cx - r},${cy} ${cx},${cy - r} ${cx + r},${cy} ${cx},${cy + r}" fill="${fill}" ${s}/>`;
    case "fire_station":
      return `<rect x="${cx - r}" y="${cy - r}" width="${r * 2}" height="${r * 2}" fill="${fill}" ${s}/>`;
    case "police_station":
      return `<polygon points="${cx},${cy-r} ${cx+r*0.95},${cy-r*0.31} ${cx+r*0.59},${cy+r*0.81} ${cx-r*0.59},${cy+r*0.81} ${cx-r*0.95},${cy-r*0.31}" fill="${fill}" ${s}/>`;
    case "fuel_station":
      return `<rect x="${cx - r * 0.8}" y="${cy - r * 0.8}" width="${r * 1.6}" height="${r * 1.6}" fill="${fill}" ${s}/>`;
    case "water_fill":
      return `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${fill}" ${s}/>`
           + `<text x="${cx}" y="${cy + 4}" text-anchor="middle" font-size="9" font-weight="800" fill="white">W</text>`;
    case "staging_area":
      return `<rect x="${cx - r}" y="${cy - r}" width="${r * 2}" height="${r * 2}" fill="${fill}" fill-opacity="0.4" ${s} stroke-dasharray="3 2"/>`;
    case "drop_point":
      return `<polygon points="${cx},${cy-r} ${cx+r},${cy+r} ${cx-r},${cy+r}" fill="${fill}" ${s}/>`;
    case "camp":
      return `<polygon points="${cx-r},${cy+r} ${cx},${cy-r} ${cx+r},${cy+r}" fill="${fill}" fill-opacity="0.6" ${s}/>`;
    case "division_supervisor":
      return `<rect x="${cx-r}" y="${cy-r}" width="${r*2}" height="${r*2}" fill="${fill}" fill-opacity="0.7" ${s}/>`
           + `<text x="${cx}" y="${cy+4}" text-anchor="middle" font-size="9" font-weight="800" fill="white">D</text>`;
    case "radio_repeater":
    case "net_control":
      return `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${fill}" fill-opacity="0.8" ${s}/>`
           + `<text x="${cx}" y="${cy+4}" text-anchor="middle" font-size="9" font-weight="800" fill="white">R</text>`;
    case "triage_immediate":
      return `<rect x="${cx-r}" y="${cy-r}" width="${r*2}" height="${r*2}" fill="#f44336" ${s}/>`
           + `<text x="${cx}" y="${cy+4}" text-anchor="middle" font-size="8" font-weight="800" fill="white">T-I</text>`;
    case "triage_delayed":
      return `<rect x="${cx-r}" y="${cy-r}" width="${r*2}" height="${r*2}" fill="#FF9800" ${s}/>`
           + `<text x="${cx}" y="${cy+4}" text-anchor="middle" font-size="8" font-weight="800" fill="white">T-D</text>`;
    case "triage_minor":
      return `<rect x="${cx-r}" y="${cy-r}" width="${r*2}" height="${r*2}" fill="#4CAF50" ${s}/>`
           + `<text x="${cx}" y="${cy+4}" text-anchor="middle" font-size="8" font-weight="800" fill="white">T-M</text>`;
    case "casualty_collection":
      return `<circle cx="${cx}" cy="${cy}" r="${r}" fill="#f44336" ${s}/>`
           + `<text x="${cx}" y="${cy+4}" text-anchor="middle" font-size="8" font-weight="800" fill="white">CCP</text>`;
    case "shelter_in_place":
      return `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${fill}" ${s}/>`
           + `<text x="${cx}" y="${cy+4}" text-anchor="middle" font-size="8" font-weight="800" fill="white">S</text>`;
    default:
      return `<circle cx="${cx}" cy="${cy}" r="${r * 0.8}" fill="${fill}" ${s}/>`;
  }
}

// ── Incident pin ──────────────────────────────────────────────────────────────

function incidentPin(cx: number, cy: number): string {
  return `<g>
    <circle cx="${cx}" cy="${cy}" r="15" fill="rgba(255,80,0,0.12)" stroke="#f44336" stroke-width="1.5" stroke-dasharray="3 2"/>
    <line x1="${cx}" y1="${cy}" x2="${cx}" y2="${cy - 22}" stroke="#c62828" stroke-width="2"/>
    <circle cx="${cx}" cy="${cy - 22}" r="7" fill="#f44336" stroke="#c62828" stroke-width="1.5"/>
    <circle cx="${cx}" cy="${cy - 22}" r="3" fill="white"/>
    <text x="${cx}" y="${cy + 12}" text-anchor="middle" font-size="8" font-weight="700" fill="#c62828" letter-spacing="0.5">INCIDENT</text>
  </g>`;
}

// ── Hazard zone polygon ───────────────────────────────────────────────────────

function zonePolygon(polygon: [number, number][], color: string, name: string, bb: Bbox): string {
  if (polygon.length < 3) return "";
  // polygon coords are [lng, lat]
  const pts = polygon.map(([lng, lat]) => toXY(lat, lng, bb));
  const ptsStr = pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const cx = pts.reduce((s, [x]) => s + x, 0) / pts.length;
  const cy = pts.reduce((s, [, y]) => s + y, 0) / pts.length;
  return `<polygon points="${ptsStr}" fill="${color}22" stroke="${color}" stroke-width="2" stroke-dasharray="6 3"/>
          <text x="${cx.toFixed(1)}" y="${cy.toFixed(1)}" text-anchor="middle" dominant-baseline="middle" font-size="10" font-weight="700" fill="${color}" paint-order="stroke" stroke="white" stroke-width="2">${name}</text>`;
}

// ── Path annotation (hand line, dozer line, etc.) ────────────────────────────

function pathLine(coords: [number, number][], color: string, bb: Bbox, dashed = false): string {
  if (coords.length < 2) return "";
  const pts = coords.map(([lng, lat]) => toXY(lat, lng, bb));
  const d = "M " + pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" L ");
  return `<path d="${d}" fill="none" stroke="${color}" stroke-width="2" ${dashed ? 'stroke-dasharray="6 3"' : ""}/>`;
}

// ── Legend ────────────────────────────────────────────────────────────────────

function buildLegend(
  annotations: IncidentAnnotation[],
  hasIncident: boolean,
  zoneCount: number
): string {
  const uniqueKeys = [...new Set(annotations.filter(a => a.type === "symbol").map(a => a.symbolKey))];
  const items: { icon: string; label: string }[] = [];

  if (hasIncident) {
    items.push({
      icon: `<circle cx="8" cy="8" r="5" fill="#f44336"/>`,
      label: "Incident Location",
    });
  }
  if (zoneCount > 0) {
    items.push({
      icon: `<rect x="2" y="4" width="12" height="8" fill="rgba(244,67,54,0.15)" stroke="#f44336" stroke-width="1.2" stroke-dasharray="4 2"/>`,
      label: "Hazard Zone",
    });
  }
  for (const key of uniqueKeys.slice(0, 8)) {
    const def = SYMBOL_DEFS.find(s => s.key === key);
    if (!def) continue;
    items.push({
      icon: `<circle cx="8" cy="8" r="5" fill="${def.color}"/>`,
      label: def.label,
    });
  }
  if (items.length === 0) return "";

  const rowH = 17;
  const h = items.length * rowH + 14;
  const bx = W - 168;
  const by = H - h - 6;

  const rows = items.map(({ icon, label }, i) =>
    `<g transform="translate(6,${6 + i * rowH})">${icon}<text x="20" y="12" font-size="9" fill="#333">${label}</text></g>`
  ).join("");

  return `<g>
    <rect x="${bx}" y="${by}" width="162" height="${h}" rx="3" fill="white" fill-opacity="0.88" stroke="#ccc" stroke-width="0.8"/>
    <g transform="translate(${bx},${by})">${rows}</g>
  </g>`;
}

// ── North arrow ───────────────────────────────────────────────────────────────

function northArrow(): string {
  const x = W - PAD + 4;
  const y = PAD + 4;
  return `<g transform="translate(${x},${y})">
    <polygon points="0,-14 5,2 0,-2 -5,2" fill="#333"/>
    <polygon points="0,16 5,2 0,6 -5,2" fill="#bbb"/>
    <text x="0" y="-18" text-anchor="middle" font-size="9" font-weight="700" fill="#333">N</text>
  </g>`;
}

// ── Main export ───────────────────────────────────────────────────────────────

export interface SchematicOpts {
  annotations: IncidentAnnotation[];
  hazardZones?: HazardZone[];
  incidentLocation?: { lat: number; lng: number } | null;
  /** Filter to specific annotation layer(s). Null/undefined = show all layers. */
  layerFilter?: AnnotationLayer | AnnotationLayer[] | null;
  showHazardZones?: boolean;
  noDataMessage?: string;
}

export function buildMapSchematicSVG(opts: SchematicOpts): string {
  const {
    annotations = [],
    hazardZones = [],
    incidentLocation,
    layerFilter,
    showHazardZones = true,
    noDataMessage = "No spatial data for this form — add annotations or hazard zones on the Map tab.",
  } = opts;

  // Filter annotations
  const visible = layerFilter == null
    ? annotations
    : annotations.filter(a =>
        Array.isArray(layerFilter)
          ? (layerFilter as string[]).includes(a.layer)
          : a.layer === layerFilter
      );

  const activeZones = showHazardZones ? hazardZones : [];

  // Collect all coord points for bbox: [lat, lng]
  const allPts: [number, number][] = [];
  if (incidentLocation) allPts.push([incidentLocation.lat, incidentLocation.lng]);
  for (const ann of visible) {
    for (const [lng, lat] of ann.coordinates ?? []) allPts.push([lat, lng]);
  }
  for (const z of activeZones) {
    for (const [lng, lat] of z.polygon) allPts.push([lat, lng]);
  }

  if (allPts.length === 0) {
    return `<p style="color:#888;font-style:italic;font-size:11px;margin:4px 0;">${noDataMessage}</p>`;
  }

  const rawBbox: Bbox = {
    minLat: Math.min(...allPts.map(p => p[0])),
    maxLat: Math.max(...allPts.map(p => p[0])),
    minLng: Math.min(...allPts.map(p => p[1])),
    maxLng: Math.max(...allPts.map(p => p[1])),
  };
  const bb = expandBbox(rawBbox, 0.28);

  // Render elements
  const zoneEls = activeZones.map(z => zonePolygon(z.polygon, z.color, z.name, bb)).join("\n");

  const annEls = visible.map(ann => {
    if (!ann.coordinates?.length) return "";
    if (ann.type === "path") {
      const color = ann.color ?? SYMBOL_COLOR[ann.symbolKey] ?? "#555";
      const dashed = ["hand_line", "dozer_line", "observed_perimeter"].includes(ann.symbolKey);
      return pathLine(ann.coordinates, color, bb, dashed);
    }
    // symbol or text
    const [lng, lat] = ann.coordinates[0];
    const [cx, cy] = toXY(lat, lng, bb);
    const sym = symbolSVG(cx, cy, ann.symbolKey ?? "generic_point");
    const label = ann.label
      ? `<text x="${cx.toFixed(1)}" y="${(cy + 19).toFixed(1)}" text-anchor="middle" font-size="9" fill="#333" font-weight="600" paint-order="stroke" stroke="white" stroke-width="2">${ann.label.slice(0, 24)}</text>`
      : "";
    return `<g>${sym}${label}</g>`;
  }).join("\n");

  const pinEl = incidentLocation
    ? incidentPin(...toXY(incidentLocation.lat, incidentLocation.lng, bb))
    : "";

  const legendEl = buildLegend(visible, !!incidentLocation, activeZones.length);

  // Approx lat label for context
  const midLat = ((rawBbox.minLat + rawBbox.maxLat) / 2).toFixed(4);
  const midLng = ((rawBbox.minLng + rawBbox.maxLng) / 2).toFixed(4);

  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="100%" style="max-width:${W}px;border:1px solid #ccc;border-radius:3px;background:#f5f0e8;display:block;margin:0 auto;">
  <rect width="${W}" height="${H}" fill="#f5f0e8"/>
  <rect x="1" y="1" width="${W-2}" height="${H-2}" fill="none" stroke="#aaa" stroke-width="0.8"/>
  <g stroke="#d8d0c0" stroke-width="0.5" stroke-dasharray="4 4">
    <line x1="${PAD}" y1="${H/2}" x2="${W-PAD}" y2="${H/2}"/>
    <line x1="${W/2}" y1="${PAD}" x2="${W/2}" y2="${H-PAD}"/>
    <line x1="${PAD}" y1="${H/3}" x2="${W-PAD}" y2="${H/3}"/>
    <line x1="${PAD}" y1="${2*H/3}" x2="${W-PAD}" y2="${2*H/3}"/>
    <line x1="${W/3}" y1="${PAD}" x2="${W/3}" y2="${H-PAD}"/>
    <line x1="${2*W/3}" y1="${PAD}" x2="${2*W/3}" y2="${H-PAD}"/>
  </g>
  ${zoneEls}
  ${annEls}
  ${pinEl}
  ${northArrow()}
  ${legendEl}
  <text x="${PAD}" y="${H - 8}" font-size="8" fill="#999">Schematic — not to scale  •  Centre: ${midLat}°N, ${midLng}°W  •  AIMS Console ${new Date().toISOString().slice(0,10)}</text>
</svg>`;
}
