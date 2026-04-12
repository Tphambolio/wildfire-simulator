/**
 * GeoJSON and KML export utilities — AIMS Console all-hazards.
 *
 * GeoJSON spec: RFC 7946 — coordinates are [lng, lat] order.
 * KML 2.2 — coordinates are lng,lat,alt.
 */

// ── Geometry helpers ────────────────────────────────────────────────────────

/** Escape XML special characters. */
function escapeXml(s: string | number): string {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ── Overlay GeoJSON export ───────────────────────────────────────────────────

export interface OverlayExportOptions {
  incidentName?: string;
  incidentLocation?: { lat: number; lng: number } | null;
  overlayRoads?: GeoJSON.FeatureCollection | null;
  overlayCommunities?: GeoJSON.FeatureCollection | null;
  overlayInfrastructure?: GeoJSON.FeatureCollection | null;
}

export function buildOverlayGeoJSON(opts: OverlayExportOptions): object {
  const features: GeoJSON.Feature[] = [];

  if (opts.incidentLocation) {
    features.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: [opts.incidentLocation.lng, opts.incidentLocation.lat] },
      properties: { type: "incident_location", name: opts.incidentName ?? "Incident Location" },
    });
  }

  const addLayer = (fc: GeoJSON.FeatureCollection | null | undefined, layerName: string) => {
    if (!fc) return;
    for (const f of fc.features) {
      features.push({ ...f, properties: { ...f.properties, _layer: layerName } });
    }
  };
  addLayer(opts.overlayRoads, "roads");
  addLayer(opts.overlayCommunities, "communities");
  addLayer(opts.overlayInfrastructure, "infrastructure");

  return {
    type: "FeatureCollection",
    properties: {
      source: "AIMS Console",
      exported_at: new Date().toISOString(),
      incident_name: opts.incidentName ?? null,
    },
    features,
  };
}

// ── KML overlay export ────────────────────────────────────────────────────────

export function buildOverlayKML(opts: OverlayExportOptions): string {
  const lines: string[] = [];
  lines.push('<?xml version="1.0" encoding="UTF-8"?>');
  lines.push('<kml xmlns="http://www.opengis.net/kml/2.2">');
  lines.push('  <Document>');
  lines.push(`    <name>${escapeXml(opts.incidentName ?? "AIMS Console Export")}</name>`);

  if (opts.incidentLocation) {
    lines.push('    <Placemark>');
    lines.push('      <name>Incident Location</name>');
    lines.push('      <Point>');
    lines.push(`        <coordinates>${opts.incidentLocation.lng},${opts.incidentLocation.lat},0</coordinates>`);
    lines.push('      </Point>');
    lines.push('    </Placemark>');
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
