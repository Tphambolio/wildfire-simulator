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

import type { WeatherParams, IncidentAnnotation } from "../types/incident";

// ── Types ─────────────────────────────────────────────────────────────────────

export interface ICSFormOptions {
  incidentName: string;
  incidentLocation?: { lat: number; lng: number } | null;
  weather?: WeatherParams;
  /** base64 PNG from maplibregl canvas.toDataURL() */
  mapSnapshotDataUrl?: string;
  /** ICS map annotations from the incident store — used to populate form tables */
  annotations?: IncidentAnnotation[];
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
  return `<ul>${items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>`;
}

function renderMapSnapshot(dataUrl: string | undefined, title: string): string {
  if (!dataUrl) return `<p class="muted">Map snapshot not captured. Use the Print button to capture the current map view.</p>`;
  return `<div class="map-block">
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

function wrapForm(title: string, sections: string[], opts: ICSFormOptions, orientation: "portrait" | "landscape" = "portrait"): string {
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
    .ics-block { border: 2px solid #0f172a; border-radius: 10px; margin-bottom: 16px; overflow: hidden; page-break-inside: avoid; break-inside: avoid; }
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
    @media print { .edit-hint { display: none; } [contenteditable] { outline: none !important; background: transparent !important; } }
  </style>
</head>
<body>
  <div class="ics-container">
    <div class="ics-header">
      <div class="ics-header__title">${esc(title)}</div>
      <div class="ics-header__meta">AIMS Console • Auto-generated ${dateStr}</div>
    </div>
    <p class="edit-hint">✎ Click any highlighted field to edit before printing.</p>
    ${sections.join("\n")}
    <p class="generated-note">Auto-generated by AIMS Console. Verify all operational fields before use in the field.</p>
  </div>
  <script>
    document.querySelectorAll('td').forEach(function(td) {
      td.contentEditable = 'true';
      td.spellcheck = false;
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

  const situationItems: string[] = [
    "Describe current incident situation here.",
    "Include nature of hazard, affected area, and population at risk.",
    "Update objectives and resources as situation develops.",
  ];

  const weatherRows: Array<[string, string]> = w ? [
    ["Wind Speed / Direction", `${w.wind_speed} km/h ${windDirLabel(w.wind_direction)} (${w.wind_direction}°)`],
    ["Temperature", `${w.temperature}°C`],
    ["Relative Humidity", `${w.relative_humidity}%`],
    ["Precipitation (24h)", `${w.precipitation_24h} mm`],
  ] : [["Status", "Weather parameters not yet entered."]];

  return wrapForm("ICS 201 – Incident Briefing", [
    icsBlock("A", "Incident Information", incidentInfoBlock(opts)),
    icsBlock("B", "Current Situation Summary", renderList(situationItems)),
    icsBlock("C", "Weather Outlook", kvTable(weatherRows)),
    icsBlock("D", "Incident Objectives", renderList([
      "Ensure life safety of all responders and public.",
      "Establish unified command with all responding agencies.",
      "Confirm scene perimeter and access control.",
      "Coordinate evacuation / shelter-in-place as required.",
    ])),
    icsBlock("E", "Operational Map", renderMapSnapshot(opts.mapSnapshotDataUrl, "Incident Map Overview")),
    icsBlock("F", "Resource Summary", kvTable([
      ["Incident Command", ""],
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
  ], opts, "landscape");
}

// ── ICS-202: Incident Objectives ──────────────────────────────────────────────

export function buildICS202HTML(opts: ICSFormOptions): string {
  const w = opts.weather;

  const objectiveItems = [
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
    ["Precipitation (24h)", `${w.precipitation_24h} mm`],
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
    icsBlock("8", "Attachments / References", renderList([
      "ICS-203 Organization Assignment List (attached)",
      "ICS-204 Assignment List per division (attached)",
      "ICS-205 Communications Plan (attached)",
      "ICS-206 Medical Plan (attached)",
    ])),
    icsBlock("9", "Operational Map", renderMapSnapshot(opts.mapSnapshotDataUrl, "Operational Map")),
  ], opts, "portrait");
}

// ── ICS-203: Organization Assignment List ─────────────────────────────────────

export function buildICS203HTML(opts: ICSFormOptions): string {
  const commandStaffRows = `
<table class="kv">
  <tr><th>Position</th><th>Name</th><th>Agency</th><th>Contact</th></tr>
  <tr><td>Incident Commander (IC)</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>Safety Officer</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>Liaison Officer</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>Public Information Officer (PIO)</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
</table>`;

  const generalStaffRows = `
<table class="kv">
  <tr><th>Section</th><th>Chief</th><th>Name</th><th>Agency</th><th>Contact</th></tr>
  <tr><td>Operations Section Chief</td><td>Ops Chief</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>Planning Section Chief</td><td>Plan Chief</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>Logistics Section Chief</td><td>Log Chief</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>Finance / Admin Section Chief</td><td>Fin Chief</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>Operations — Division A</td><td>Division Supervisor</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>Operations — Division B</td><td>Division Supervisor</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>Medical Unit Leader</td><td>MedUL</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>Communications Unit Leader</td><td>ComL</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>Resources Unit Leader</td><td>RESL</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>Situation Unit Leader</td><td>SITL</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
</table>`;

  return wrapForm("ICS 203 – Organization Assignment List", [
    icsBlock("1", "Incident Information", incidentInfoBlock(opts)),
    icsBlock("2", "Command Staff", commandStaffRows),
    icsBlock("3", "General Staff & Branch Assignments", generalStaffRows),
    icsBlock("4", "Agency Representatives", `<table class="kv">
      <tr><th>Agency</th><th>Representative</th><th>Contact</th><th>Role</th></tr>
      <tr><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>Fire / Rescue</td></tr>
      <tr><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>Police / Security</td></tr>
      <tr><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>Emergency Medical</td></tr>
      <tr><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>Public Works</td></tr>
      <tr><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>Emergency Management</td></tr>
    </table>`),
    icsBlock("5", "Technical Specialists", `<table class="kv">
      <tr><th>Specialty</th><th>Name</th><th>Agency</th><th>Contact</th></tr>
      <tr><td>RPAS / Drone Coordinator</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
      <tr><td>Hazard Specialist</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
      <tr><td>Situation Unit Leader (SITL)</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
    </table>`),
  ], opts, "portrait");
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
    icsBlock("4", "Operational Map", renderMapSnapshot(opts.mapSnapshotDataUrl, "Assignments Map")),
  ], opts, "portrait");
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
  ], opts, "portrait");
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

  const hospitalTable = hospitalAnns.length > 0 ? `
<table class="kv">
  <tr><th>Hospital</th><th>Phone</th><th>Address</th><th>Level</th></tr>
  ${hospitalAnns.map(a =>
    `<tr><td>${a.label}</td><td>${a.properties.phone ?? ""}</td><td>${a.properties.address ?? ""}</td><td>${a.properties.level ?? "—"}</td></tr>`
  ).join("")}
</table>` : `<table class="kv">
  <tr><th>Hospital</th><th>Phone</th><th>Address</th><th>Level</th></tr>
  <tr><td>Use 📡 Fetch OSM to auto-populate</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
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
    icsBlock("8", "Operational Map — Medical Resources", renderMapSnapshot(opts.mapSnapshotDataUrl, "Medical Resources Map")),
  ], opts, "portrait");
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
  ], opts, "portrait");
}

// ── Full IAP package ──────────────────────────────────────────────────────────

export function buildFullIAPHTML(opts: ICSFormOptions): string {
  const forms = [
    buildICS201HTML(opts),
    buildICS202HTML(opts),
    buildICS203HTML(opts),
    buildICS204HTML(opts),
    buildICS205HTML(opts),
    buildICS206HTML(opts),
  ];

  const FORM_CODES = ["ICS 201", "ICS 202", "ICS 203", "ICS 204", "ICS 205", "ICS 206"];
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
    <p class="iap-meta" style="margin-top:24px">${totalForms} forms &nbsp;|&nbsp; ICS 201–206</p>
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
