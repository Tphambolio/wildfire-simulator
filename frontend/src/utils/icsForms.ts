/**
 * ICS form HTML generators for AIMS Console — All-Hazards Incident Management.
 *
 * Each function returns a complete, print-ready HTML document auto-populated
 * from incident data. Forms follow NIMS ICS structure.
 *
 * Forms included:
 *   ICS-201  Incident Briefing            (landscape)
 *   ICS-202  Incident Objectives          (portrait)
 *   ICS-203  Organization Assignment List (portrait)
 *   ICS-204  Assignment List              (portrait)
 *   ICS-205  Communications Plan          (portrait)
 *   ICS-206  Medical Plan                 (portrait)
 *   ICS-214  Activity Log                 (portrait)
 *
 * References:
 *   NIMS ICS-201 through ICS-209 (FEMA/NWCG 2021 revisions)
 */

import { buildMapSchematicSVG } from "./mapSchematic";

import type {
  WeatherParams,
  IncidentAnnotation,
  HazardType,
  HazardZone,
  IncidentResource,
  IncidentAgency,
  OperationalPeriod,
  ICSSection,
} from "../types/incident";
import { ICS_POSITIONS_BY_SECTION } from "../types/incident";

// ── Types ─────────────────────────────────────────────────────────────────────

export interface ICSFormOptions {
  incidentName: string;
  incidentLocation?: { lat: number; lng: number } | null;
  weather?: WeatherParams;
  /** base64 PNG from maplibregl canvas.toDataURL() */
  mapSnapshotDataUrl?: string;
  /** ICS map annotations from the incident store — used to populate form tables */
  annotations?: IncidentAnnotation[];
  hazardType?: HazardType;
  incidentComplexity?: 1 | 2 | 3 | 4 | 5;
  hazardZones?: HazardZone[];
  resources?: IncidentResource[];
  agencies?: IncidentAgency[];
  period?: OperationalPeriod;
  /** Initial briefing fields — populated from InitBriefingPanel */
  incidentCommanderName?: string;
  situationNarrative?: string;
  jurisdiction?: string;
  /** True when this form is being rendered as part of a full IAP package (enables "attached" references) */
  isPartOfFullIAP?: boolean;
  /** Infrastructure overlay — hospitals, EOC, power, water, etc. from Edmonton Open Data */
  overlayInfrastructure?: GeoJSON.FeatureCollection | null;
  /** Communities overlay — neighbourhood polygons */
  overlayCommunities?: GeoJSON.FeatureCollection | null;
}

// ── Wind direction label ──────────────────────────────────────────────────────

const WIND_DIRS = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"];
function windDirLabel(deg: number): string {
  return WIND_DIRS[Math.round(deg / 22.5) % 16];
}

// ── HTML helpers ──────────────────────────────────────────────────────────────

function esc(s: string | number | undefined | null): string {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function icsBlock(num: string, title: string, body: string): string {
  return `
<section class="ics-block">
  <header>
    <span class="ics-block__number">${esc(num)}</span>
    <span class="ics-block__title">${esc(title)}</span>
  </header>
  <div class="ics-block__body">${body}</div>
</section>`;
}

function kvTable(rows: Array<[string, string | number]>): string {
  return `<table class="kv">${rows.map(([k, v]) => `<tr><th>${esc(k)}</th><td contenteditable="true">${esc(v)}</td></tr>`).join("")}</table>`;
}

function renderList(items: string[]): string {
  return `<ul>${items.map((i) => `<li contenteditable="true" spellcheck="false">${esc(i)}</li>`).join("")}</ul>`;
}

function renderMapSnapshot(dataUrl: string | undefined, title: string): string {
  if (!dataUrl) return `<p class="muted">Map snapshot not captured. Use the Print button to capture the current map view.</p>`;
  return `<div class="map-block" style="break-before:page;page-break-before:always;">
  <p style="margin:0 0 8px;font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:0.05em">${esc(title)}</p>
  <img src="${dataUrl}" alt="${esc(title)}" />
  <ul class="map-legend">
    <li><span style="background:#2196f3"></span>Overlays</li>
    <li><span style="background:#4caf50"></span>Facilities</li>
    <li><span style="background:#ff9800"></span>Operations</li>
    <li><span style="background:#f44336"></span>Medical</li>
  </ul>
</div>`;
}

/** Renders a layer-specific schematic SVG map for use inside ICS form blocks. */
function renderSchematic(
  opts: ICSFormOptions,
  layerFilter: import("../types/incident").AnnotationLayer | import("../types/incident").AnnotationLayer[] | null,
  showHazardZones: boolean,
  noDataMessage: string
): string {
  return buildMapSchematicSVG({
    annotations: opts.annotations ?? [],
    hazardZones: opts.hazardZones ?? [],
    incidentLocation: opts.incidentLocation,
    layerFilter,
    showHazardZones,
    noDataMessage,
  });
}

function wrapForm(title: string, sections: string[], opts: ICSFormOptions, orientation: "portrait" | "landscape" = "portrait", formId = ""): string {
  const now = new Date();
  const dateStr = now.toISOString().slice(0, 10);
  const timeStr = now.toUTCString().slice(17, 22) + " UTC";
  const headerText = `${esc(opts.incidentName)}  •  ${dateStr} ${timeStr}`;
  const pageSize = orientation === "landscape" ? "Letter landscape" : "Letter portrait";

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>${esc(title)}</title>
  <style>
    @page {
      size: ${pageSize};
      margin: 0.75in;
    }
    @page {
      @top-left { content: "${headerText}"; font-size: 10px; color: #475569; font-family: "Helvetica", "Arial", sans-serif; }
      @bottom-right { content: string(icsFormTitle) " — pg " counter(page) " of " counter(pages); font-size: 10px; color: #475569; font-family: "Helvetica", "Arial", sans-serif; }
    }
    @page :first { @top-left { content: none; } @bottom-right { content: none; } }
    body { font-family: "Helvetica", "Arial", sans-serif; margin: 0; color: #111827; background: #f8fafc; font-size: 13px; line-height: 1.5; }
    .ics-container { background: #ffffff; border: 2px solid #0f172a; border-radius: 12px; padding: 24px 28px; }
    .ics-header { display: flex; justify-content: space-between; align-items: baseline; border-bottom: 2px solid #0f172a; padding-bottom: 12px; margin-bottom: 16px; }
    .ics-header__title { font-size: 20px; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase; string-set: icsFormTitle content(); }
    .ics-header__meta { font-size: 11px; color: #475569; text-transform: uppercase; letter-spacing: 0.1em; }
    table.kv { width: 100%; border-collapse: collapse; margin-top: 4px; }
    table.kv th, table.kv td { border: 1px solid #1f2937; padding: 6px 8px; vertical-align: top; }
    table.kv th { background: #e9efff; width: 30%; font-weight: 600; }
    ul { margin: 0; padding-left: 20px; }
    .muted { color: #6b7280; font-style: italic; }
    .map-block { margin-top: 12px; border: 2px solid #1d4ed8; border-radius: 12px; padding: 12px; background: #eff6ff; }
    .map-block img { width: 100%; max-width: 720px; border: 1px solid #93c5fd; border-radius: 6px; }
    .map-legend { list-style: none; margin: 8px 0 0; padding: 0; display: flex; flex-wrap: wrap; gap: 12px; font-size: 12px; }
    .map-legend li { display: flex; align-items: center; gap: 4px; }
    .map-legend span { display: inline-block; width: 12px; height: 12px; border-radius: 2px; border: 1px solid #0f172a; flex-shrink: 0; }
    .ics-block { border: 2px solid #0f172a; border-radius: 10px; margin-bottom: 16px; overflow: hidden; page-break-inside: avoid; break-inside: avoid; orphans: 3; widows: 3; }
    table { page-break-inside: avoid; break-inside: avoid; }
    tr { page-break-inside: avoid; break-inside: avoid; }
    .ics-block header { display: flex; align-items: center; gap: 12px; padding: 8px 12px; background: #0f172a; color: #f8fafc; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; }
    .ics-block__number { display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; border-radius: 50%; background: #1d4ed8; font-size: 13px; flex-shrink: 0; }
    .ics-block__title { font-size: 13px; }
    .ics-block__body { padding: 12px 14px 16px; background: #ffffff; }
    .page-break { page-break-before: always; }
    h4 { margin: 10px 0 4px; font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; color: #374151; }
    .generated-note { font-size: 11px; color: #6b7280; font-style: italic; margin-top: 16px; border-top: 1px solid #e5e7eb; padding-top: 8px; }
    [contenteditable="true"] { cursor: text; }
    [contenteditable="true"]:hover { background: #fefce8 !important; outline: 1px dashed #ca8a04; outline-offset: 1px; border-radius: 2px; }
    [contenteditable="true"]:focus { background: #fef9c3 !important; outline: 2px solid #d97706; outline-offset: 1px; border-radius: 2px; }
    .edit-hint { font-size: 11px; color: #374151; margin-bottom: 12px; padding: 5px 10px; background: #f0f9ff; border: 1px dashed #93c5fd; border-radius: 4px; }
    .print-toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; padding: 7px 12px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; }
    .print-toolbar button { padding: 5px 16px; background: #1e3a5f; color: #fff; border: none; border-radius: 4px; font-size: 13px; font-weight: 600; cursor: pointer; }
    .print-toolbar button:hover { background: #2d5080; }
    @media print { .edit-hint { display: none; } .print-toolbar { display: none; } [contenteditable] { outline: none !important; background: transparent !important; } }
  </style>
</head>
<body>
  <div class="ics-container">
    <div class="print-toolbar">
      <button onclick="window.print()">🖨 Print</button>
      <span style="font-size:12px;color:#64748b;">✎ Click any highlighted field to edit before printing.</span>
    </div>
    <div class="ics-header">
      <div class="ics-header__title">${esc(title)}</div>
      <div class="ics-header__meta">AIMS Console • Auto-generated ${dateStr}</div>
    </div>
    ${sections.join("\n")}
    <p class="generated-note">Auto-generated by AIMS Console. Verify all operational fields before use in the field.</p>
  </div>
  <script>
    // Make every content cell and list item editable (skip map image blocks)
    document.querySelectorAll('.ics-block__body td, .ics-block__body li, .ics-block__body p.placeholder').forEach(function(el) {
      if (!el.closest('.map-block')) {
        el.contentEditable = 'true';
        el.spellcheck = false;
      }
    });

    // Send edits to parent frame so React can persist them
    var _editTimer;
    document.addEventListener('input', function() {
      clearTimeout(_editTimer);
      _editTimer = setTimeout(function() {
        try {
          window.parent.postMessage({
            type: 'aims-form-edit',
            formId: '${formId}',
            html: document.documentElement.outerHTML
          }, '*');
        } catch(e) {}
      }, 1200);
    });
  </script>
</body>
</html>`;
}

// ── Shared incident header helper ─────────────────────────────────────────────

function incidentInfoBlock(opts: ICSFormOptions, extra?: Array<[string, string]>): string {
  const now = new Date();
  const rows: Array<[string, string]> = [
    ["Incident Name", opts.incidentName || "—"],
    ["Date Prepared", now.toISOString().slice(0, 10)],
    ["Time Prepared", now.toUTCString().slice(17, 22) + " UTC"],
    ["Jurisdiction", "—"],
  ];
  if (opts.incidentLocation) {
    rows.push(["Incident Location", `${opts.incidentLocation.lat.toFixed(5)}°N, ${Math.abs(opts.incidentLocation.lng).toFixed(5)}°W`]);
  }
  if (extra) rows.push(...extra);
  return kvTable(rows);
}

// ── ICS-201: Incident Briefing ────────────────────────────────────────────────

export function buildICS201HTML(opts: ICSFormOptions): string {
  const w = opts.weather;

  // Section B — use real narrative if provided, else placeholder
  const situationItems: string[] = opts.situationNarrative
    ? [opts.situationNarrative]
    : [
        "Describe current incident situation here.",
        "Include nature of hazard, affected area, and population at risk.",
        "Update objectives and resources as situation develops.",
      ];

  // Section D — use real objectives from the period if populated, else defaults
  const objectiveItems: string[] = (opts.period?.objectives?.length)
    ? opts.period.objectives
    : [
        "Ensure life safety of all responders and public.",
        "Establish unified command with all responding agencies.",
        "Confirm scene perimeter and access control.",
        "Coordinate evacuation / shelter-in-place as required.",
      ];

  // Section F — IC name from initial briefing
  const icName = opts.incidentCommanderName || "";

  const weatherRows: Array<[string, string]> = w ? [
    ["Wind Speed / Direction", `${w.wind_speed} km/h ${windDirLabel(w.wind_direction)} (${w.wind_direction}°)`],
    ["Temperature", `${w.temperature}°C`],
    ["Relative Humidity", `${w.relative_humidity}%`],
    ["Precipitation (24h)", `${w.precipitation} mm`],
  ] : [["Status", "Weather parameters not yet entered."]];

  // Section A — extend with jurisdiction if provided
  const infoBlock = opts.jurisdiction
    ? incidentInfoBlock(opts) + kvTable([["Jurisdiction / Authority", opts.jurisdiction]])
    : incidentInfoBlock(opts);

  // Section H — overlay-based affected area summary
  const infraFeatures = opts.overlayInfrastructure?.features ?? [];
  const communityCount = opts.overlayCommunities?.features?.length ?? 0;
  const critTypes = ["Power Generation", "Water Treatment", "Wastewater Treatment", "EOC", "Emergency Services", "Hospital"];
  const critInfraNames = infraFeatures
    .filter(f => critTypes.includes(f.properties?.type ?? ""))
    .map(f => `${esc(f.properties?.name as string)} (${esc(f.properties?.type as string)})`);
  const hasOverlay = infraFeatures.length > 0 || communityCount > 0;

  const affectedAreaBlock = hasOverlay ? kvTable([
    ["Communities loaded (map overlay)", communityCount > 0 ? `${communityCount} neighbourhoods` : "—"],
    ["Critical infrastructure assets", infraFeatures.length > 0 ? `${infraFeatures.length} total — see ICS-208 for detail` : "—"],
    ...(critInfraNames.length > 0 ? critInfraNames.slice(0, 6).map((n, i) => [`Asset ${i + 1}`, n] as [string, string]) : []),
  ]) : `<p class="muted">Load Infrastructure and Communities overlays on the Map tab to auto-populate affected area data.</p>`;

  return wrapForm("ICS 201 – Incident Briefing", [
    icsBlock("A", "Incident Information", infoBlock),
    icsBlock("B", "Current Situation Summary", renderList(situationItems)),
    icsBlock("C", "Weather Outlook", kvTable(weatherRows)),
    icsBlock("D", "Incident Objectives", renderList(objectiveItems)),
    icsBlock("E", "Operational Map", renderMapSnapshot(opts.mapSnapshotDataUrl, "Incident Map Overview")),
    icsBlock("F", "Resource Summary", kvTable([
      ["Incident Commander", icName],
      ["Operations", ""],
      ["Fire / Rescue", ""],
      ["Police / Security", ""],
      ["Emergency Medical", ""],
      ["Public Works", ""],
    ])),
    icsBlock("G", "Communications Overview", kvTable([
      ["Primary Channel", "Command net — confirm with Communications Unit Leader"],
      ["Tactical Channel", "Operations net — assign by division"],
      ["Air-to-Ground", "ATGS frequency — if aviation involved"],
      ["Public Information", "Municipal Emergency Alert System + media liaison"],
    ])),
    icsBlock("H", "Affected Area — Infrastructure & Communities", affectedAreaBlock),
  ], opts, "landscape", "ics201");
}

// ── ICS-202: Incident Objectives ──────────────────────────────────────────────

export function buildICS202HTML(opts: ICSFormOptions): string {
  const w = opts.weather;

  const objectiveItems = (opts.period?.objectives?.length)
    ? opts.period.objectives
    : [
        "Ensure life safety of all responders and public.",
        "Establish and maintain incident command structure.",
        "Contain and mitigate hazard to prevent expansion.",
        "Coordinate with all response agencies through unified command.",
        "Provide accurate and timely public information.",
      ];

  const safetyItems = [
    "Maintain LACES: Lookouts, Anchor points, Communications, Escape routes, Safety zones",
    "Monitor changing conditions — re-evaluate escape routes as situation evolves.",
    "All personnel must have assigned supervisor and communication before entry.",
    "Rehab required every 2 hours for all operational personnel.",
  ];

  const weatherRows: Array<[string, string]> = w ? [
    ["Wind", `${w.wind_speed} km/h ${windDirLabel(w.wind_direction)}`],
    ["Temp / RH", `${w.temperature}°C / ${w.relative_humidity}%`],
    ["Precipitation (24h)", `${w.precipitation} mm`],
  ] : [["Status", "Weather not yet entered."]];

  return wrapForm("ICS 202 – Incident Objectives", [
    icsBlock("1", "Incident Information", incidentInfoBlock(opts)),
    icsBlock("2", "Operational Period Objectives", renderList(objectiveItems)),
    icsBlock("3", "Command Emphasis", renderList([
      "All section chiefs to confirm resources before deployment.",
      "No personnel enter hazard zone without briefing and appropriate PPE.",
      "Establish clear accountability — all personnel signed in on ICS-214.",
    ])),
    icsBlock("4", "General Situational Awareness", renderList([
      "Incident perimeter to be verified by Situation Unit Leader.",
      "All at-risk populations to be identified and tracked.",
      "Infrastructure impacts to be documented and reported to Planning.",
    ])),
    icsBlock("5", "Safety Message / Analysis", renderList(safetyItems)),
    icsBlock("6", "Weather Outlook", kvTable(weatherRows)),
    icsBlock("7", "Control Measures", renderList([
      "Access control: checkpoints at all entry points.",
      "Evacuation triggers: per local authority order.",
      "Debrief at end of each operational period.",
    ])),
    icsBlock("8", "Attachments / References", opts.isPartOfFullIAP
      ? renderList([
          "ICS-203 Organization Assignment List (attached)",
          "ICS-204 Assignment List per division (attached)",
          "ICS-205 Communications Plan (attached)",
          "ICS-206 Medical Plan (attached)",
        ])
      : `<p style="color:#666;font-size:12px;margin:4px 0;">
           Attachments are included when printing the Full IAP package.<br>
           Use <strong>Full IAP</strong> in the EOC Console to generate all forms together.
         </p>`
    ),
    icsBlock("9", "Situation Overview Map",
      renderSchematic(opts, null, true,
        "No spatial data yet — add annotations or hazard zones on the Map tab.")),
  ], opts, "portrait", "ics202");
}

// ── ICS-203: Organization Assignment List ─────────────────────────────────────

export function buildICS203HTML(opts: ICSFormOptions): string {
  const resources = opts.resources ?? [];
  const agencies = opts.agencies ?? [];

  function staffRow(section: ICSSection, position: string): string {
    const r = resources.find(
      (res) => res.icsSection === section && res.icsPosition === position && res.kind === "person"
    ) ?? resources.find(
      (res) => res.icsSection === section && res.role === position && res.kind === "person"
    );
    return `<tr><td>${esc(position)}</td><td contenteditable="true">${esc(r?.name ?? "")}</td><td contenteditable="true">${esc(r?.agency ?? "")}</td><td contenteditable="true">&nbsp;</td></tr>`;
  }

  const commandStaffRows = `<table class="kv">
  <tr><th>Position</th><th>Name</th><th>Agency</th><th>Contact</th></tr>
  ${ICS_POSITIONS_BY_SECTION.command.map((pos) => staffRow("command", pos)).join("")}
</table>`;

  const chiefPositions: [ICSSection, string][] = [
    ["operations", "Operations Section Chief"],
    ["planning", "Planning Section Chief"],
    ["logistics", "Logistics Section Chief"],
    ["finance", "Finance/Admin Section Chief"],
  ];
  const generalStaffRows = `<table class="kv">
  <tr><th>Section Chief</th><th>Name</th><th>Agency</th><th>Contact</th></tr>
  ${chiefPositions.map(([sec, pos]) => staffRow(sec, pos)).join("")}
</table>`;

  const agencyRows = agencies.length > 0
    ? agencies.map((a) => `<tr><td>${esc(a.name)}</td><td>${esc(a.liaison)}</td><td contenteditable="true">&nbsp;</td><td>${esc(a.role)}</td></tr>`).join("")
    : `<tr><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>`.repeat(4);

  return wrapForm("ICS 203 – Organization Assignment List", [
    icsBlock("1", "Incident Information", incidentInfoBlock(opts)),
    icsBlock("2", "Command Staff", commandStaffRows),
    icsBlock("3", "General Staff — Section Chiefs", generalStaffRows),
    icsBlock("4", "Agency Representatives", `<table class="kv">
      <tr><th>Agency</th><th>Representative</th><th>Contact</th><th>Role</th></tr>
      ${agencyRows}
    </table>`),
    icsBlock("5", "Technical Specialists", `<table class="kv">
      <tr><th>Specialty</th><th>Name</th><th>Agency</th><th>Contact</th></tr>
      ${resources.filter((r) => r.icsPosition === "Technical Specialist").map((r) =>
        `<tr><td>${esc(r.role ?? "Technical Specialist")}</td><td>${esc(r.name)}</td><td>${esc(r.agency)}</td><td contenteditable="true">&nbsp;</td></tr>`
      ).join("") || `<tr><td contenteditable="true">&nbsp;</td><td contenteditable="true">&nbsp;</td><td contenteditable="true">&nbsp;</td><td contenteditable="true">&nbsp;</td></tr>`.repeat(3)}
    </table>`),
  ], opts, "portrait", "ics203");
}

// ── ICS-204: Assignment List ──────────────────────────────────────────────────

export function buildICS204HTML(opts: ICSFormOptions): string {
  // ── Annotation legend from ics204 layer ──────────────────────────────────
  const ann204 = (opts.annotations ?? []).filter(a => a.layer === "ics204");
  const supAnns = ann204.filter(a => a.symbolKey === "division_supervisor");
  const stagingAnns = ann204.filter(a => a.symbolKey === "staging_area");
  const dropAnns = ann204.filter(a => a.symbolKey === "drop_point");
  const campAnns = ann204.filter(a => a.symbolKey === "camp");
  const waterAnns = ann204.filter(a => a.symbolKey === "water_fill");
  const lineAnns = ann204.filter(a => a.symbolKey === "dozer_line" || a.symbolKey === "hand_line");
  const markerAnns = ann204.filter(a => a.symbolKey === "generic_point");
  const textAnns = ann204.filter(a => a.type === "text" || a.symbolKey === "text_label");
  const freehandAnns = ann204.filter(a => a.symbolKey === "freehand_path");
  const resourceStaging = ann204.filter(a => a.symbolKey === "resource_staging");
  const hasMapAnnotations = ann204.length > 0;

  const SYM_LABELS: Record<string, string> = {
    division_supervisor: "Div. Supervisor", staging_area: "Staging Area",
    drop_point: "Drop Point", camp: "Camp/Base", water_fill: "Water Fill",
    dozer_line: "Line", hand_line: "Hand Line",
    resource_staging: "Resource Staging",
    generic_point: "Marker", freehand_path: "Freehand", text_label: "Note",
  };

  const annotationLegend = hasMapAnnotations ? `
<table class="kv">
  <tr><th>Type</th><th>Label / Note</th><th>Location</th></tr>
  ${[...supAnns, ...stagingAnns, ...dropAnns, ...campAnns, ...waterAnns, ...markerAnns, ...resourceStaging].map(a => {
    const coord = a.coordinates[0];
    const loc = coord ? `${coord[1].toFixed(4)}°N, ${Math.abs(coord[0]).toFixed(4)}°W` : "See map";
    return `<tr><td>${SYM_LABELS[a.symbolKey] ?? a.symbolKey}</td><td>${a.label}</td><td>${loc}</td></tr>`;
  }).join("")}
  ${lineAnns.map(a =>
    `<tr><td>${SYM_LABELS[a.symbolKey]}</td><td>${a.label}</td><td>${a.coordinates.length} waypoints — see map</td></tr>`
  ).join("")}
  ${freehandAnns.map(a =>
    `<tr><td>Freehand</td><td>${a.label}</td><td>See map overlay</td></tr>`
  ).join("")}
  ${textAnns.map(a => {
    const coord = a.coordinates[0];
    const loc = coord ? `${coord[1].toFixed(4)}°N, ${Math.abs(coord[0]).toFixed(4)}°W` : "See map";
    return `<tr><td>Note</td><td><em>${a.label}</em></td><td>${loc}</td></tr>`;
  }).join("")}
</table>` : "";

  const divBlock = (name: string, objectives: string[], resources: string[], safety: string) => {
    const body = `
      ${kvTable([["Work Location", "Confirm with SITL"], ["Report Time", ""], ["Supervisor", ""]])}
      <h4>Tactical Objectives</h4>${renderList(objectives)}
      <h4>Resources</h4>${renderList(resources)}
      <h4>Safety Notes</h4>${renderList([safety])}
      <h4>Communications</h4>${kvTable([["Tactical Net", "Assign frequency — ICS-205"], ["Command Net", "Confirm with Comms Unit Leader"]])}`;
    return icsBlock("3", name, body);
  };

  return wrapForm("ICS 204 – Assignment List", [
    icsBlock("1", "Incident Information", incidentInfoBlock(opts)),
    icsBlock("2", "Operations Overview", kvTable([
      ["Operations Section Chief", ""],
      ["Active Strategy", ""],
      ["Active Divisions", ""],
    ])),
    divBlock("Division A — Hazard Control",
      ["Establish and maintain scene perimeter.", "Mitigate primary hazard.", "Coordinate with Safety Officer on PPE requirements."],
      ["Operations crews per ICS-203", "Equipment per resource request"],
      "LACES mandatory. Verify escape routes. No entry without IC briefing."
    ),
    divBlock("Division B — Evacuation / Population Protection",
      ["Coordinate evacuation of at-risk populations.", "Establish reception centres and shelter.", "Confirm evacuation complete before closing zone."],
      ["Police / Security", "Transportation resources", "Social services"],
      "Confirm civilian evacuation before any resource entry into evacuation zone."
    ),
    ...(hasMapAnnotations ? [icsBlock("Map Legend", "Annotated Resources", annotationLegend)] : []),
    icsBlock("4", "Assignments Map",
      renderSchematic(opts, "ics204", true,
        "No assignment annotations yet — use the Map tab › 204 Assignments layer to place division supervisors, staging areas, drop points, and control lines.")),
  ], opts, "portrait", "ics204");
}

// ── ICS-205: Communications Plan ─────────────────────────────────────────────

export function buildICS205HTML(opts: ICSFormOptions): string {
  // Pull ICS-205 layer annotations (radio repeaters, net control stations)
  const ann205 = (opts.annotations ?? []).filter(a => a.layer === "ics205");
  const repeaterAnns = ann205.filter(a => a.symbolKey === "radio_repeater");
  const ncsAnns = ann205.filter(a => a.symbolKey === "net_control");

  const repeaterTable = repeaterAnns.length > 0 ? `
<table class="kv">
  <tr><th>Repeater / Relay</th><th>Location</th><th>Frequency</th><th>Notes</th></tr>
  ${repeaterAnns.map(a => {
    const coord = a.coordinates[0];
    const loc = coord ? `${coord[1].toFixed(4)}°N, ${Math.abs(coord[0]).toFixed(4)}°W` : "See map";
    return `<tr><td>${a.label}</td><td>${loc}</td><td>${a.properties.frequency ?? ""}</td><td>${a.properties.notes ?? ""}</td></tr>`;
  }).join("")}
</table>` : "";

  const retsNets = `
<table class="kv">
  <tr><th>Net #</th><th>Function</th><th>Channel / Talkgroup</th><th>Frequency</th><th>Assignment</th></tr>
  <tr><td>1</td><td>Command</td><td>&nbsp;</td><td>&nbsp;</td><td>IC, Section Chiefs</td></tr>
  <tr><td>2</td><td>Tactical — Div A</td><td>&nbsp;</td><td>&nbsp;</td><td>Ops Crews</td></tr>
  <tr><td>3</td><td>Tactical — Div B</td><td>&nbsp;</td><td>&nbsp;</td><td>Evacuation / EPS</td></tr>
  <tr><td>4</td><td>Logistics</td><td>&nbsp;</td><td>&nbsp;</td><td>Supply, Ground Support</td></tr>
  <tr><td>5</td><td>Public Information / Media</td><td>&nbsp;</td><td>&nbsp;</td><td>PIO</td></tr>
</table>
<p class="muted" style="margin-top:8px">Assign frequencies/talkgroups from Communications Unit Leader. Confirm before distribution.</p>`;

  const contacts = `
<table class="kv">
  <tr><th>Name</th><th>Position</th><th>Radio Call Sign</th><th>Phone</th><th>Agency</th></tr>
  <tr><td>&nbsp;</td><td>Incident Commander</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>&nbsp;</td><td>Operations Section Chief</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>&nbsp;</td><td>Safety Officer</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>&nbsp;</td><td>Logistics Section Chief</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>&nbsp;</td><td>Medical Unit Leader</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>&nbsp;</td><td>Public Information Officer</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
</table>`;

  return wrapForm("ICS 205 – Incident Radio Communications Plan", [
    icsBlock("1", "Incident Information", incidentInfoBlock(opts)),
    icsBlock("2", "Radio Networks", retsNets),
    icsBlock("3", "Key Contacts", contacts),
    ...(repeaterAnns.length > 0 ? [icsBlock("4", "Radio Repeaters / Relay Sites", repeaterTable)] : []),
    ...(ncsAnns.length > 0 ? [icsBlock("5", "Net Control Stations", `<table class="kv">
      <tr><th>NCS</th><th>Location</th><th>Contact</th></tr>
      ${ncsAnns.map(a => {
        const coord = a.coordinates[0];
        const loc = coord ? `${coord[1].toFixed(4)}°N, ${Math.abs(coord[0]).toFixed(4)}°W` : "See map";
        return `<tr><td>${a.label}</td><td>${loc}</td><td>${a.properties.contact ?? ""}</td></tr>`;
      }).join("")}
    </table>`)] : []),
    icsBlock("6", "Alternative Communications", kvTable([
      ["Cellular Backup", "Confirm coverage with Comms Unit"],
      ["Satellite Phone", "Logistics — if cellular unavailable"],
      ["Runner Protocol", "Designated runners for radio blackout zones"],
    ])),
  ], opts, "portrait", "ics205");
}

// ── ICS-206: Medical Plan ─────────────────────────────────────────────────────

export function buildICS206HTML(opts: ICSFormOptions): string {
  // Pull ICS-206 layer annotations (hospitals, aid stations, LZ)
  const ann206 = (opts.annotations ?? []).filter(a => a.layer === "ics206");
  const hospitalAnns = ann206.filter(a => a.symbolKey === "hospital");
  const aidAnns = ann206.filter(a => a.symbolKey === "medical_aid_station");
  const lzAnns = ann206.filter(a => a.symbolKey === "medevac_lz");
  const ambAnns = ann206.filter(a => a.symbolKey === "ambulance_staging");
  const pharmacyAnns = ann206.filter(a => a.symbolKey === "pharmacy");

  // Auto-populate hospitals from infrastructure overlay layer if available
  const infraHospitals = (opts.overlayInfrastructure?.features ?? [])
    .filter(f => f.properties?.type === "Hospital")
    .map(f => f.properties?.name as string);

  const hospitalTable = hospitalAnns.length > 0 ? `
<table class="kv">
  <tr><th>Hospital</th><th>Phone</th><th>Address</th><th>Level</th></tr>
  ${hospitalAnns.map(a =>
    `<tr><td>${a.label}</td><td>${a.properties.phone ?? ""}</td><td>${a.properties.address ?? ""}</td><td>${a.properties.level ?? "—"}</td></tr>`
  ).join("")}
</table>` : infraHospitals.length > 0 ? `
<table class="kv">
  <tr><th>Hospital</th><th>Phone</th><th>Address</th><th>Level</th></tr>
  ${infraHospitals.map(name =>
    `<tr><td>${esc(name)}</td><td contenteditable="true">—</td><td contenteditable="true">—</td><td contenteditable="true">—</td></tr>`
  ).join("")}
</table>
<p class="muted" style="font-size:0.8em;margin-top:4px;">Auto-populated from Infrastructure Overlay. Edit phone/address before printing.</p>` : `<table class="kv">
  <tr><th>Hospital</th><th>Phone</th><th>Address</th><th>Level</th></tr>
  <tr><td>Use 📡 Fetch OSM or load Infrastructure overlay to auto-populate</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
</table>`;

  const medevacTable = lzAnns.length > 0 ? `
<table class="kv">
  <tr><th>LZ Name</th><th>Location</th><th>Notes</th></tr>
  ${lzAnns.map(a => {
    const coord = a.coordinates[0];
    const loc = coord ? `${coord[1].toFixed(4)}°N, ${Math.abs(coord[0]).toFixed(4)}°W` : "See map";
    return `<tr><td>${a.label}</td><td>${loc}</td><td>${a.properties.notes ?? ""}</td></tr>`;
  }).join("")}
</table>` : `<p class="muted">Mark Medevac LZ locations on the 206 map layer using the symbol palette.</p>`;

  return wrapForm("ICS 206 – Medical Plan", [
    icsBlock("1", "Incident Information", incidentInfoBlock(opts)),
    icsBlock("2", "Medical Aid Stations", aidAnns.length > 0 ? `<table class="kv">
      <tr><th>Station</th><th>Location</th><th>Paramedic</th><th>Contact</th></tr>
      ${aidAnns.map(a => {
        const coord = a.coordinates[0];
        const loc = coord ? `${coord[1].toFixed(4)}°N, ${Math.abs(coord[0]).toFixed(4)}°W` : "See map";
        return `<tr><td>${a.label}</td><td>${loc}</td><td>${a.properties.paramedic ?? ""}</td><td>${a.properties.phone ?? ""}</td></tr>`;
      }).join("")}
    </table>` : `<p class="muted">No aid stations placed. Use ICS-206 layer in symbol picker to add medical aid stations.</p>`),
    icsBlock("3", "Ambulance Staging", ambAnns.length > 0 ? `<table class="kv">
      <tr><th>Unit</th><th>Location</th><th>Contact</th></tr>
      ${ambAnns.map(a => {
        const coord = a.coordinates[0];
        const loc = coord ? `${coord[1].toFixed(4)}°N, ${Math.abs(coord[0]).toFixed(4)}°W` : "See map";
        return `<tr><td>${a.label}</td><td>${loc}</td><td>${a.properties.phone ?? ""}</td></tr>`;
      }).join("")}
    </table>` : `<p class="muted">Use 📡 Fetch OSM or add ambulance staging from ICS-206 layer.</p>`),
    icsBlock("4", "Receiving Hospitals", hospitalTable),
    icsBlock("5", "Medevac Landing Zones", medevacTable),
    ...(pharmacyAnns.length > 0 ? [icsBlock("6", "Pharmacy / Formulary", `<table class="kv">
      <tr><th>Pharmacy</th><th>Phone</th><th>Address</th></tr>
      ${pharmacyAnns.map(a =>
        `<tr><td>${a.label}</td><td>${a.properties.phone ?? ""}</td><td>${a.properties.address ?? ""}</td></tr>`
      ).join("")}
    </table>`)] : []),
    icsBlock("7", "Medical Emergency Procedures", renderList([
      "Activate EMS by radio on Command Net — report location, nature of injury, number of casualties.",
      "Maintain universal precautions. Use gloves and eye protection for all patient contact.",
      "Any injury or illness to be reported to Safety Officer and documented on ICS-214.",
      "Evacuate patient to nearest aid station or hospital per MedUL direction.",
      "Rehab required every 2 hours — medical assessment available at aid stations.",
    ])),
    icsBlock("8", "Medical Resources Map",
      renderSchematic(opts, "ics206", false,
        "No medical annotations yet — use the Map tab › 206 Medical layer to place hospitals, aid stations, medevac LZs, and treatment sites.")),
  ], opts, "portrait", "ics206");
}

// ── ICS-214: Activity Log ─────────────────────────────────────────────────────

export function buildICS214HTML(opts: ICSFormOptions): string {
  const logRows = Array.from({ length: 12 }, () =>
    `<tr><td style="width:20%">&nbsp;</td><td>&nbsp;</td></tr>`
  ).join("");

  return wrapForm("ICS 214 – Activity Log", [
    icsBlock("1", "Incident Information", incidentInfoBlock(opts)),
    icsBlock("2", "Assignment Information", kvTable([
      ["Unit / ICS Position", ""],
      ["Personnel on Duty", ""],
      ["Operational Period", ""],
    ])),
    icsBlock("3", "Activity Log", `<table class="kv"><tr><th style="width:20%">Time</th><th>Activity / Remarks</th></tr>${logRows}</table>`),
    icsBlock("4", "Notes", `<p class="muted">Record significant activities, resource changes, decisions, and contacts. Submit to Documentation Unit at end of shift.</p>`),
  ], opts, "portrait", "ics214");
}

// ── ICS-207: Organizational Chart ────────────────────────────────────────────

export function buildICS207HTML(opts: ICSFormOptions): string {
  const resources = opts.resources ?? [];

  /** Exact match: icsSection + icsPosition field. Falls back to role substring for legacy data. */
  function getAssignee(section: ICSSection, position: string): IncidentResource | undefined {
    return (
      resources.find((r) => r.icsSection === section && r.icsPosition === position && r.kind === "person") ??
      resources.find((r) => r.icsSection === section && r.role === position && r.kind === "person")
    );
  }

  function orgSection(section: ICSSection): string {
    const positions = ICS_POSITIONS_BY_SECTION[section];
    const rows = positions.map((pos) => {
      const match = getAssignee(section, pos);
      return `<tr><th style="width:45%;font-weight:600">${esc(pos)}</th><td>${esc(match?.name ?? "")}</td><td style="width:25%">${esc(match?.agency ?? "")}</td></tr>`;
    }).join("");
    return `<table class="kv"><tr><th>Position</th><th>Name</th><th>Agency</th></tr>${rows}</table>`;
  }

  const ucAgencies = (opts.agencies ?? []).filter((a) => a.isUnifiedCommand);
  const ucBlock = ucAgencies.length > 0
    ? `<h4>Unified Command</h4><p>${ucAgencies.map((a) => `${esc(a.name)} — ${esc(a.liaison)}`).join(" / ")}</p>`
    : "";

  return wrapForm("ICS 207 – Organizational Assignment Chart", [
    icsBlock("1", "Incident Information", incidentInfoBlock(opts)),
    icsBlock("2", "Command / Command Staff", ucBlock + orgSection("command")),
    icsBlock("3", "Operations Section", orgSection("operations")),
    icsBlock("4", "Planning Section", orgSection("planning")),
    icsBlock("5", "Logistics Section", orgSection("logistics")),
    icsBlock("6", "Finance / Administration Section", orgSection("finance")),
  ], opts, "portrait", "ics207");
}

// ── ICS-208: Safety Message / Plan ───────────────────────────────────────────

const HAZARD_SAFETY: Record<string, { concerns: string[]; ppe: string[]; medical: string[] }> = {
  flood: {
    concerns: ["Swift water and drowning hazard", "Electrical hazards from submerged infrastructure", "Contaminated floodwater (sewage, chemicals)", "Structural instability of flood-damaged buildings", "Hypothermia risk in cold water operations", "Vehicle entrapment in moving water"],
    ppe: ["PFD (personal flotation device) for all water-adjacent operations", "Rubber boots / waterproof waders", "High-visibility vest", "Gloves (nitrile for contaminated water)", "Eye protection"],
    medical: ["Establish warm/dry rehab area away from flood zone", "Monitor for hypothermia and wet foot injuries", "Water sampling before any direct contact operations", "AED and first aid kits at all sector staging areas"],
  },
  hazmat: {
    concerns: ["Chemical exposure from unknown or known hazardous materials", "Secondary contamination of responders and equipment", "Wind shift changing plume direction rapidly", "Detonation or fire risk if flammable materials involved", "Shelter-in-place failure if seal inadequate"],
    ppe: ["PPE level determined by Hazmat Branch Director (A/B/C/D)", "Buddy system mandatory in hot and warm zones", "Full decon required before crossing out of warm zone", "Continuous air monitoring in hot zone"],
    medical: ["Medical monitoring at decon line — vitals before entry and after exit", "Decon station established at warm/cold zone boundary", "Antidote kits staged for known chemical hazard types", "All personnel briefed on exposure symptoms and self-reporting"],
  },
  mass_casualty: {
    concerns: ["Secondary device protocol — scene not safe until cleared", "Infectious disease precautions for blood-borne pathogens", "Responder mental health — critical incident stress", "Resource surge overwhelming triage capacity", "Family reunification pressure on responders"],
    ppe: ["Nitrile gloves for all patient contact", "Eye protection and mask for aerosol-generating procedures", "High-visibility vest for ICS role identification", "Body substance isolation (BSI) equipment at all triage areas"],
    medical: ["Rehab sector mandatory for all responders after 1 hour on-scene", "Critical Incident Stress Debriefing (CISD) arranged for all crews", "EMS supervisor monitors responder welfare throughout operation", "Mass decon capability standing by"],
  },
  wildfire_smoke: {
    concerns: ["Poor air quality — AQI may exceed health thresholds", "Reduced visibility for vehicle and aircraft operations", "Ember transport and spot fire ignition", "Rapid fire behavior change with wind shift", "Carbon monoxide buildup in structures"],
    ppe: ["N95 or P100 respirator for smoke operations (NIOSH approved)", "Eye protection in heavy smoke", "Structural PPE for direct suppression", "Hearing protection near equipment", "Nomex / fire-resistant clothing"],
    medical: ["AQI monitoring at command post — suspend operations above index 200 unless life safety", "Rehab sector with clean air / air-conditioned space", "Hydration monitoring — 1L/hour for active suppression", "Carbon monoxide detectors in all occupied structures"],
  },
  severe_weather: {
    concerns: ["Lightning strike risk for personnel and vehicles in open terrain", "High winds creating projectile hazards", "Flash flooding in low-lying areas", "Downed power lines from wind damage", "Hypothermia in wet/cold conditions"],
    ppe: ["High-visibility vest in low-visibility conditions", "Hard hat for wind debris areas", "Waterproof outer layer", "Steel-toed boots in debris areas"],
    medical: ["Lightning safety: 30-30 rule (30 second flash-to-bang = seek shelter; wait 30 min after last thunder)", "Warm/dry rehab for hypothermia risk", "AED deployed at all sector staging areas", "Wind speed monitoring — suspend aerial ops above 50 km/h"],
  },
  infrastructure: {
    concerns: ["Downed power lines — assume energized until utility confirms de-energized", "Structural collapse risk in damaged buildings", "Underground utility hazards (gas, water, electrical)", "Equipment movement in constrained work zones", "Traffic management for road-adjacent work"],
    ppe: ["Arc-flash / electrical PPE as required", "Hard hat mandatory in work zones", "Hi-viz vest for all road-adjacent operations", "Steel-toed boots", "Cut-resistant gloves for debris handling"],
    medical: ["Electrical burns treated as trauma — internal injury possible without external signs", "Crush injury protocol for structural collapse operations", "Rest and hydration schedule for extended utility work", "First aid stations at each sector boundary"],
  },
  evacuation: {
    concerns: ["Crowd management and traffic control during mass movement", "Medical emergencies during evacuation (cardiac, mobility-impaired)", "Civil unrest or non-compliance with evacuation orders", "Route blockage from accidents or incidents", "Vulnerable population tracking (hospitals, care homes, schools)"],
    ppe: ["Hi-vis vest mandatory for all traffic and crowd control", "Communications headset for coordination in noisy environments", "Body armour for law enforcement elements if civil unrest risk"],
    medical: ["Ambulatory triage at evacuation points for individuals in distress", "Registry of vulnerable populations and medical transport needs", "Mental health support at reception centres", "AED and first aid at all evacuation collection points"],
  },
  other: {
    concerns: ["Hazards to be identified by Safety Officer prior to operations", "Conduct site-specific Job Hazard Analysis (JHA) for all tasks", "Maintain situational awareness of evolving hazard conditions"],
    ppe: ["PPE to be determined by Safety Officer based on site hazard assessment", "All personnel briefed before deployment"],
    medical: ["Medical plan developed by Medical Unit Leader", "AED and first aid staged at Command Post", "Nearest medical facility identified and route confirmed"],
  },
};

export function buildICS208HTML(opts: ICSFormOptions): string {
  const hazardKey = opts.hazardType ?? "other";
  const safety = HAZARD_SAFETY[hazardKey] ?? HAZARD_SAFETY["other"];

  const concernsList = renderList(safety.concerns);
  const ppeList = renderList(safety.ppe);
  const medicalList = renderList(safety.medical);

  const zones = opts.hazardZones ?? [];
  const zoneRows = zones.length > 0
    ? zones.map((z) => `<tr><td>${esc(z.name)}</td><td contenteditable="true">—</td><td contenteditable="true">—</td></tr>`).join("")
    : `<tr><td colspan="3" class="muted">No hazard zones defined</td></tr>`;

  // Pull nearest hospital from infrastructure overlay for EAP
  const infraFeatures = opts.overlayInfrastructure?.features ?? [];
  const nearestHospital = (infraFeatures.find(f => f.properties?.type === "Hospital")?.properties?.name as string | undefined) ?? "";

  // Critical infrastructure at risk section from overlay
  const CRITICAL_TYPES = ["Power Generation", "Water Treatment", "Wastewater Treatment", "EOC", "Emergency Services", "Hospital"];
  const critInfra = infraFeatures.filter(f => CRITICAL_TYPES.includes(f.properties?.type ?? ""));
  const critInfraBlock = critInfra.length > 0 ? `
<table class="kv">
  <tr><th>Asset</th><th>Type</th><th>Impact if Affected</th></tr>
  ${critInfra.map(f => {
    const type = f.properties?.type as string;
    const impact = type === "Power Generation" ? "Loss of grid power to affected sectors"
      : type === "Water Treatment" ? "Loss of potable water supply"
      : type === "Wastewater Treatment" ? "Sewage backup / environmental hazard"
      : type === "EOC" ? "Command and coordination disruption"
      : type === "Emergency Services" ? "Reduced emergency response capacity"
      : type === "Hospital" ? "Loss of medical receiving capacity"
      : "Assess and report";
    return `<tr><td>${esc(f.properties?.name as string)}</td><td>${esc(type)}</td><td>${esc(impact)}</td></tr>`;
  }).join("")}
</table>
<p class="muted" style="font-size:0.8em;margin-top:4px;">Auto-populated from Infrastructure Overlay. Notify Logistics and Planning if any asset is threatened.</p>` : `<p class="muted">Load Infrastructure overlay on the Map tab to auto-populate critical assets.</p>`;

  return wrapForm("ICS 208 – Safety Message / Plan", [
    icsBlock("1", "Incident Information", incidentInfoBlock(opts)),
    icsBlock("2", "Hazard-Specific Safety Concerns", `<p><strong>Hazard type: ${esc(hazardKey.replace("_", " ").toUpperCase())}</strong></p>${concernsList}`),
    icsBlock("3", "PPE Requirements", ppeList),
    icsBlock("4", "Medical Monitoring Plan", medicalList),
    icsBlock("5", "Hazard Zone Safety by Zone", `<table class="kv"><tr><th>Zone</th><th>Entry Restrictions</th><th>Emergency Action</th></tr>${zoneRows}</table>`),
    icsBlock("6", "Critical Infrastructure — Situational Awareness", critInfraBlock),
    icsBlock("7", "Emergency Action Plan (EAP)", `<table class="kv">${[
      ["Emergency signal / evacuation tone", "Air horn: 3 blasts"],
      ["Emergency rally point", ""],
      ["Nearest medical facility", nearestHospital],
      ["Emergency contact (Incident Safety Officer)", (opts.resources ?? []).find(r => r.icsPosition === "Safety Officer")?.name ?? ""],
      ["Emergency contact (Incident Commander)", opts.incidentCommanderName ?? ""],
    ].map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join("")}</table>`),
    icsBlock("8", "Safety / Hazard Zone Map",
      renderSchematic(opts, ["situation", "evac"], true,
        "No hazard zones or safety annotations yet — draw zones on the Map tab and place situation layer markers.")),
  ], opts, "portrait", "ics208");
}

// ── ICS-213: General Message ──────────────────────────────────────────────────

export function buildICS213HTML(opts: ICSFormOptions): string {
  return wrapForm("ICS 213 – General Message", [
    icsBlock("1", "Message Header", `<table class="kv">${[
      ["To (Name and Position)", ""],
      ["From (Name and Position)", ""],
      ["Subject", ""],
      ["Date / Time", new Date().toISOString().slice(0, 16).replace("T", "  ")],
      ["Incident Name", opts.incidentName],
    ].map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join("")}</table>`),
    icsBlock("2", "Message", `<div style="min-height:180px;border:1px dashed #9ca3af;padding:10px;border-radius:4px" contenteditable="true"><span class="muted">Enter message text here…</span></div>`),
    icsBlock("3", "Reply", `<div style="min-height:100px;border:1px dashed #9ca3af;padding:10px;border-radius:4px" contenteditable="true"><span class="muted">Reply / response here…</span></div>`),
    icsBlock("4", "Signature", `<table class="kv">${[
      ["Sender Signature", ""],
      ["Date / Time", ""],
      ["Reply Signature", ""],
      ["Date / Time", ""],
    ].map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join("")}</table>`),
  ], opts, "portrait", "ics213");
}

// ── ICS-215: Operational Planning Worksheet ───────────────────────────────────

export function buildICS215HTML(opts: ICSFormOptions): string {
  const resources = opts.resources ?? [];
  const assigned = resources.filter((r) => r.status === "assigned");

  const rows = assigned.length > 0
    ? assigned.map((r) => `<tr>
        <td>${esc(r.kind === "person" ? "Personnel" : r.kind === "equipment" ? "Equipment" : "Vehicle")}</td>
        <td>${esc(r.name)}</td>
        <td>${esc(r.role ?? "")}</td>
        <td>${esc(r.typeRating ?? "")}</td>
        <td>${esc(r.assignedDivision ?? "")}</td>
        <td>${esc(r.agency)}</td>
        <td contenteditable="true">&nbsp;</td>
      </tr>`).join("")
    : `<tr><td colspan="7" class="muted">No assigned resources. Add resources in the Resources panel and set status to "Assigned".</td></tr>`;

  const unassignedCount = resources.filter((r) => r.status === "available").length;

  return wrapForm("ICS 215 – Operational Planning Worksheet", [
    icsBlock("1", "Incident Information", incidentInfoBlock(opts, [
      ["Operational Period", opts.period?.date ?? ""],
      ["Total Assigned Resources", String(assigned.length)],
      ["Available Resources", String(unassignedCount)],
    ])),
    icsBlock("2", "Resource Assignments", `
      <table class="kv" style="font-size:12px">
        <tr>
          <th style="width:10%">Kind</th>
          <th style="width:18%">Name / Description</th>
          <th style="width:18%">Role</th>
          <th style="width:8%">Type</th>
          <th style="width:12%">Division</th>
          <th style="width:14%">Agency</th>
          <th style="width:20%">Special Instructions</th>
        </tr>
        ${rows}
      </table>`),
    icsBlock("3", "Resource Needs / Requests", `<table class="kv">
      <tr><th style="width:25%">Resource Type</th><th>Description</th><th style="width:15%">Quantity</th><th style="width:20%">Needed By</th></tr>
      ${Array.from({ length: 5 }, () => `<tr><td contenteditable="true">&nbsp;</td><td contenteditable="true">&nbsp;</td><td contenteditable="true">&nbsp;</td><td contenteditable="true">&nbsp;</td></tr>`).join("")}
    </table>`),
  ], opts, "portrait", "ics215");
}

// ── ICS-215A: Incident Action Plan Safety Analysis ────────────────────────────

export function buildICS215aHTML(opts: ICSFormOptions): string {
  const hazardKey = opts.hazardType ?? "other";
  const safety = HAZARD_SAFETY[hazardKey] ?? HAZARD_SAFETY["other"];

  const hazardRows = safety.concerns.map((concern) =>
    `<tr><td>${esc(concern)}</td><td contenteditable="true">&nbsp;</td><td contenteditable="true">&nbsp;</td><td contenteditable="true">&nbsp;</td></tr>`
  ).join("");

  return wrapForm("ICS 215A – Incident Action Plan Safety Analysis", [
    icsBlock("1", "Incident Information", incidentInfoBlock(opts, [
      ["Operational Period", opts.period?.date ?? ""],
      ["Incident Safety Officer", ""],
    ])),
    icsBlock("2", "Safety Analysis — Hazard Identification and Mitigation", `
      <p style="margin-bottom:8px"><strong>Hazard Type: ${esc(hazardKey.replace("_", " ").toUpperCase())}</strong></p>
      <table class="kv" style="font-size:12px">
        <tr>
          <th style="width:35%">Identified Hazard</th>
          <th style="width:20%">Affected Operations</th>
          <th style="width:25%">Mitigation Measures</th>
          <th style="width:20%">Residual Risk (H/M/L)</th>
        </tr>
        ${hazardRows}
        ${Array.from({ length: 3 }, () => `<tr><td contenteditable="true">&nbsp;</td><td contenteditable="true">&nbsp;</td><td contenteditable="true">&nbsp;</td><td contenteditable="true">&nbsp;</td></tr>`).join("")}
      </table>`),
    icsBlock("3", "PPE Requirements by Work Area", `<table class="kv">
      <tr><th style="width:30%">Work Area / Zone</th><th>Required PPE</th><th style="width:25%">Approval Required</th></tr>
      ${Array.from({ length: 5 }, () => `<tr><td contenteditable="true">&nbsp;</td><td contenteditable="true">&nbsp;</td><td contenteditable="true">&nbsp;</td></tr>`).join("")}
    </table>`),
    icsBlock("4", "Acknowledgements", `<p class="muted">Incident Safety Officer signature and date. Each Operations Section Branch / Division Supervisor acknowledges this safety analysis before beginning operations.</p>
      <table class="kv"><tr><th style="width:40%">Name / Position</th><th>Signature</th><th style="width:25%">Date / Time</th></tr>
      ${Array.from({ length: 6 }, () => `<tr><td contenteditable="true">&nbsp;</td><td contenteditable="true">&nbsp;</td><td contenteditable="true">&nbsp;</td></tr>`).join("")}
      </table>`),
  ], opts, "portrait", "ics215a");
}

// ── Full IAP package ──────────────────────────────────────────────────────────

export function buildFullIAPHTML(opts: ICSFormOptions): string {
  const fullOpts = { ...opts, isPartOfFullIAP: true };
  const forms = [
    buildICS201HTML(fullOpts),
    buildICS202HTML(fullOpts),
    buildICS203HTML(fullOpts),
    buildICS204HTML(fullOpts),
    buildICS205HTML(fullOpts),
    buildICS206HTML(fullOpts),
    buildICS207HTML(fullOpts),
    buildICS208HTML(fullOpts),
    buildICS213HTML(fullOpts),
    buildICS215HTML(fullOpts),
    buildICS215aHTML(fullOpts),
  ];

  const FORM_CODES = ["ICS 201", "ICS 202", "ICS 203", "ICS 204", "ICS 205", "ICS 206", "ICS 207", "ICS 208", "ICS 213", "ICS 215", "ICS 215A"];
  const totalForms = forms.length;

  const CONTAINER_OPEN = '<div class="ics-container">';
  const bodies = forms.map((html) => {
    const start = html.indexOf(CONTAINER_OPEN);
    if (start === -1) return "";
    const contentStart = start + CONTAINER_OPEN.length;
    const end = html.lastIndexOf("</div>");
    return end > contentStart ? html.slice(contentStart, end) : "";
  });

  const bodiesWithPagination = bodies.map((b, i) => {
    const withSheetNum = b.replace(
      'class="ics-header__meta">',
      `class="ics-header__meta"><strong class="iap-pg-label">${FORM_CODES[i]} &nbsp;&bull;&nbsp; IAP Sheet ${i + 1} of ${totalForms}</strong> &nbsp;&bull;&nbsp; `,
    );
    return (
      withSheetNum +
      `<div class="iap-notes" contenteditable="true" spellcheck="false"><span class="iap-notes-hint">Notes / additional information</span></div>`
    );
  });

  const now = new Date();
  const dateStr = now.toISOString().slice(0, 10);

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Incident Action Plan — ${esc(opts.incidentName)} — ${dateStr}</title>
  <style>
    @page {
      margin: 0.6in 0.7in;
      @top-right {
        content: string(icsFormTitle) " — pg " counter(page) " of " counter(pages);
        font-size: 10px; color: #475569;
        font-family: "Helvetica", "Arial", sans-serif;
      }
    }
    @page :first { @top-right { content: none; } }
    body { font-family: "Helvetica", "Arial", sans-serif; margin: 0; color: #111827; background: #ffffff; font-size: 13px; line-height: 1.5; }
    .iap-cover { text-align: center; padding: 80px 40px; page-break-after: always; break-after: page; border-bottom: 3px solid #0f172a; }
    .iap-cover h1 { font-size: 32px; font-weight: 800; text-transform: uppercase; letter-spacing: -0.01em; margin-bottom: 12px; }
    .iap-cover .iap-meta { font-size: 14px; color: #475569; }
    .iap-page { min-height: 100vh; display: flex; flex-direction: column; padding-bottom: 16px; }
    .page-break { page-break-before: always; break-before: page; }
    @media print { .iap-page { min-height: auto; padding-bottom: 0; } }
    .ics-container { background: #ffffff; padding: 0; flex: 1; display: flex; flex-direction: column; }
    .ics-header { display: flex; justify-content: space-between; align-items: baseline; border-bottom: 3px solid #0f172a; padding-bottom: 10px; margin-bottom: 14px; }
    .ics-header__title { font-size: 18px; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase; string-set: icsFormTitle content(); }
    .ics-header__meta { font-size: 11px; color: #475569; }
    table.kv { width: 100%; border-collapse: collapse; margin-top: 4px; }
    table.kv th, table.kv td { border: 1px solid #1f2937; padding: 6px 8px; vertical-align: top; }
    table.kv th { background: #e9efff; width: 30%; font-weight: 600; }
    ul { margin: 0; padding-left: 20px; }
    .muted { color: #6b7280; font-style: italic; }
    .map-block { margin-top: 12px; border: 2px solid #1d4ed8; border-radius: 12px; padding: 12px; background: #eff6ff; }
    .map-block img { width: 100%; max-width: 720px; border: 1px solid #93c5fd; border-radius: 6px; }
    .ics-block { border: 2px solid #0f172a; border-radius: 10px; margin-bottom: 14px; overflow: hidden; page-break-inside: avoid; break-inside: avoid; }
    .ics-block header { display: flex; align-items: center; gap: 12px; padding: 8px 12px; background: #0f172a; color: #f8fafc; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; }
    .ics-block__number { display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; border-radius: 50%; background: #1d4ed8; font-size: 13px; flex-shrink: 0; }
    .ics-block__title { font-size: 13px; }
    .ics-block__body { padding: 12px 14px 16px; background: #ffffff; }
    h4 { margin: 10px 0 4px; font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; color: #374151; }
    .iap-notes { min-height: 48px; border: 1px dashed #9ca3af; padding: 6px 10px; margin-top: 8px; border-radius: 4px; font-size: 11px; color: #6b7280; }
    .iap-notes-hint { pointer-events: none; }
    .iap-pg-label { font-size: 12px; font-weight: 700; color: #1d4ed8; }
    .generated-note { font-size: 11px; color: #6b7280; font-style: italic; margin-top: 10px; border-top: 1px solid #e5e7eb; padding-top: 6px; }
    [contenteditable="true"] { cursor: text; }
    [contenteditable="true"]:focus { outline: 2px solid #d97706; border-radius: 2px; }
    .edit-hint { display: none; }
    @media print { [contenteditable] { outline: none !important; } }
  </style>
</head>
<body>
  <div class="iap-cover">
    <h1>Incident Action Plan</h1>
    <p class="iap-meta">${esc(opts.incidentName)} &nbsp;•&nbsp; ${dateStr}</p>
    <p class="iap-meta">Generated by AIMS Console — All-Hazards Incident Management System</p>
    <p class="iap-meta" style="margin-top:24px">${totalForms} forms &nbsp;|&nbsp; ICS 201–215A</p>
  </div>
  ${bodiesWithPagination.map((body, i) => `
  <div class="iap-page${i > 0 ? " page-break" : ""}">
    <div class="ics-container">
      ${body}
      <p class="generated-note">AIMS Console — All-Hazards Incident Management System — ${dateStr}</p>
    </div>
  </div>`).join("")}
  <script>
    document.querySelectorAll('td, .iap-notes').forEach(function(el) {
      el.contentEditable = 'true';
      el.spellcheck = false;
    });
    document.querySelectorAll('.iap-notes').forEach(function(el) {
      el.addEventListener('focus', function() {
        var hint = el.querySelector('.iap-notes-hint');
        if (hint) hint.style.display = 'none';
      });
    });
  </script>
</body>
</html>`;
}
