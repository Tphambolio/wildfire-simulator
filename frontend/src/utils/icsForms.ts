/**
 * ICS form HTML generators — ported and adapted from CrisisKit AI (forms.py).
 *
 * Each function returns a complete, print-ready HTML document auto-populated
 * from FireSim V3 simulation outputs, for EOC / ICS use; open in new window or render in iframe.
 *
 * Forms included, with their ICS Canada counterparts (https://icscanada.ca/resources/ics-forms/,
 * checked 2026-10-08; every form here has one):
 *   ICS 201  Incident Briefing            (landscape)  ICS Canada Form 201
 *   ICS 202  Incident Objectives          (portrait)   ICS Canada Form 202
 *   ICS 203  Organization Assignment List (portrait)   ICS Canada Form 203
 *   ICS 204  Assignment List              (portrait)   ICS Canada Form 204-WF (wildfire) / 204-AH
 *   ICS 205  Communications Plan          (portrait)   ICS Canada Form 205
 *   ICS 206  Medical Plan                 (portrait)   ICS Canada Form 206
 *   ICS 214  Activity Log                 (portrait)   ICS Canada Form 214
 * The ICS 209 is ICS Canada Form 209-WF, built block by block in ics209.ts.
 *
 * These layouts are FireSim adaptations with lettered sections that carry each form's purpose;
 * they are not block-by-block reproductions of the ICS Canada forms (block numbering not
 * checked). Transcribe onto the ICS Canada forms for official use.
 *
 * References:
 *   CrisisKit AI — github.com/Tphambolio/crisiskitAI (forms.py)
 *   ICS Canada forms and instructions — icscanada.ca/resources/ics-forms/
 *   Head fire intensity classes: utils/fireClasses.ts (Cole & Alexander 1995; CWFIS HFI map
 *   limits). An earlier "Alexander & de Groot (1988)" reference here described no code in use.
 */

import type { SimulationFrame } from "../types/simulation";
import type { RunParams } from "../components/WeatherPanel";
import type { PlanningEvacZone } from "./evacZones";
import type { CriticalReach } from "./assets";
import { assetLabel, clock } from "./assets";
import type { IncidentAnnotation } from "../types/incident";
import { RPAS_NOTE, buildSuppressionAdvisory } from "./suppressionAdvisory";

// ── Types ─────────────────────────────────────────────────────────────────────

export interface ICSFormOptions {
  incidentName: string;
  frames: SimulationFrame[];
  runParams: RunParams | null;
  ignitionPoint: { lat: number; lng: number } | null;
  fuelTypeLabel?: string;
  /** Assets and major roads reached by the modelled fire (model output, never an instruction) */
  criticalReach?: CriticalReach | null;
  /** Evacuation status set by Planning (reported as entered, never generated) */
  evacZones?: PlanningEvacZone[];
  /** base64 PNG from maplibregl canvas.toDataURL() */
  mapSnapshotDataUrl?: string;
  /** ICS map annotations from the incident store — used to populate form tables */
  annotations?: IncidentAnnotation[];
}

// ── Geometry helpers ──────────────────────────────────────────────────────────

function haversineKm(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const R = 6371.0;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLng = ((lng2 - lng1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLng / 2) ** 2;
  return R * 2 * Math.asin(Math.sqrt(a));
}

function perimeterLengthKm(perimeter: number[][]): number {
  if (perimeter.length < 2) return 0;
  let total = 0;
  for (let i = 0; i < perimeter.length; i++) {
    const a = perimeter[i];
    const b = perimeter[(i + 1) % perimeter.length];
    total += haversineKm(a[0], a[1], b[0], b[1]);
  }
  return total;
}

// ── Fire behavior extraction ──────────────────────────────────────────────────

interface SpreadStats {
  peakRosMMmin: number;
  peakHfiKwM: number;
  finalAreaHa: number;
  perimeterKm: number;
  maxSpotDistM: number;
  spotCount: number;
  fireType: string;
  flameLengthM: number;
}

function extractSpreadStats(frames: SimulationFrame[]): SpreadStats | null {
  if (frames.length === 0) return null;
  const final = frames[frames.length - 1];
  let peakRos = 0, peakHfi = 0, maxSpotDist = 0, spotCount = 0;
  for (const f of frames) {
    if (f.head_ros_m_min > peakRos) peakRos = f.head_ros_m_min;
    if (f.max_hfi_kw_m > peakHfi) peakHfi = f.max_hfi_kw_m;
    for (const s of f.spot_fires ?? []) {
      if (s.distance_m > maxSpotDist) maxSpotDist = s.distance_m;
      spotCount++;
    }
  }
  return {
    peakRosMMmin: peakRos,
    peakHfiKwM: peakHfi,
    finalAreaHa: final.area_ha,
    perimeterKm: perimeterLengthKm(final.perimeter ?? []),
    maxSpotDistM: maxSpotDist,
    spotCount,
    fireType: final.fire_type,
    flameLengthM: final.flame_length_m,
  };
}

// ── Suppression advisory logic ────────────────────────────────────────────────

// One source for intensity classes and their meaning (utils/fireClasses.ts).
interface SuppressionSummary {
  strategy: string;
  strategyDetail: string;
  resources: string[];
  intensityClass: string;
  feasible: boolean;
}

function buildSuppressionSummary(spread: SpreadStats): SuppressionSummary {
  const adv = buildSuppressionAdvisory(spread);
  return {
    strategy: adv.strategy,
    strategyDetail: adv.strategyDetail,
    resources: adv.resources,
    intensityClass: String(adv.intensityClass),
    feasible: adv.suppressionFeasible,
  };
}

// ── Wind direction label ──────────────────────────────────────────────────────

const WIND_DIRS = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"];
function windDirLabel(deg: number): string {
  return WIND_DIRS[Math.round(deg / 22.5) % 16];
}

// ── HTML helpers (CrisisKit _ics_block / _render_table / _wrap_form pattern) ──

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
  const trs = rows.map(([k, v]) => `<tr><th>${esc(k)}</th><td contenteditable="true" spellcheck="false">${esc(v)}</td></tr>`).join("");
  return `<table class="kv">${trs}</table>`;
}

function renderList(items: string[]): string {
  if (items.length === 0) return `<p class="muted" contenteditable="true" spellcheck="false">No data provided.</p>`;
  return `<ul>${items.map((i) => `<li contenteditable="true" spellcheck="false">${esc(i)}</li>`).join("")}</ul>`;
}

function renderMapSnapshot(dataUrl: string | undefined, title: string): string {
  if (!dataUrl) return `<p class="muted">${esc(title)} — map snapshot not yet captured. Use the "Print" button in the EOC Console to embed the map.</p>`;
  return `
<div class="map-block">
  <h3 style="margin-top:0">${esc(title)}</h3>
  <img src="${dataUrl}" alt="${esc(title)}" />
  <ul class="map-legend">
    <li><span style="background:none;border-top:3px solid #1d4ed8;height:0"></span>Evacuation Order, set by Planning (solid blue outline)</li>
    <li><span style="background:none;border-top:3px dashed #1d4ed8;height:0"></span>Evacuation Alert, set by Planning (dashed)</li>
    <li><span style="background:none;border-top:3px dotted #1d4ed8;height:0"></span>Evacuation Watch, set by Planning (dotted)</li>
    <li><span style="background:#ff3d00"></span>Fire Perimeter</li>
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
    /* Running header: form title tracks across all printed pages via string-set */
    @page {
      @top-left { content: "${headerText}"; font-size: 10px; color: #475569; font-family: "Helvetica", "Arial", sans-serif; }
      @bottom-right { content: string(icsFormTitle) " — pg " counter(page) " of " counter(pages); font-size: 10px; color: #475569; font-family: "Helvetica", "Arial", sans-serif; }
    }
    @page :first { @top-left { content: none; } @bottom-right { content: none; } }
    body { font-family: "Helvetica", "Arial", sans-serif; margin: 0; color: #111827; background: #f8fafc; font-size: 13px; line-height: 1.5; }
    .ics-container { background: #ffffff; border: 2px solid #0f172a; border-radius: 12px; padding: 24px 28px; }
    .ics-header { display: flex; justify-content: space-between; align-items: baseline; border-bottom: 2px solid #0f172a; padding-bottom: 12px; margin-bottom: 16px; }
    /* string-set captures the form title so it repeats in @page margin on every page */
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
    /* Editable field styles */
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
      <div class="ics-header__meta">FireSim V3 • Auto-generated ${dateStr}</div>
    </div>
    <p class="edit-hint">✎ Click any highlighted field to edit before printing.</p>
    ${sections.join("\n")}
    <p class="generated-note">Auto-generated by FireSim V3 from Canadian FBP simulation results. Verify all operational fields before use.</p>
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
    ["Jurisdiction", "Edmonton, Alberta, Canada"],
  ];
  if (opts.ignitionPoint) {
    rows.push(["Ignition Coordinates", `${opts.ignitionPoint.lat.toFixed(5)}°N, ${Math.abs(opts.ignitionPoint.lng).toFixed(5)}°W`]);
  }
  if (opts.fuelTypeLabel) rows.push(["Primary Fuel Type", opts.fuelTypeLabel]);
  if (extra) rows.push(...extra);
  return kvTable(rows);
}

// ── ICS-201: Incident Briefing ────────────────────────────────────────────────

export function buildICS201HTML(opts: ICSFormOptions): string {
  const spread = extractSpreadStats(opts.frames);
  const suppression = spread ? buildSuppressionSummary(spread) : null;
  const rp = opts.runParams;

  const situationItems: string[] = [];
  if (spread) {
    situationItems.push(`Fire type: ${spread.fireType.replace(/_/g, " ")}`);
    situationItems.push(`Burned area: ${spread.finalAreaHa.toFixed(1)} ha (${(spread.finalAreaHa / 100).toFixed(2)} km²)`);
    situationItems.push(`Perimeter length: ${spread.perimeterKm.toFixed(1)} km`);
    situationItems.push(`Peak head ROS: ${spread.peakRosMMmin.toFixed(1)} m/min`);
    situationItems.push(`Peak HFI: ${spread.peakHfiKwM.toFixed(0)} kW/m — Intensity Class ${suppression?.intensityClass ?? "?"}`);
    if (spread.spotCount > 0) situationItems.push(`Spotting: ${spread.spotCount} events detected, max throw ${spread.maxSpotDistM.toFixed(0)} m`);
    if (spread.flameLengthM > 0) situationItems.push(`Flame length: ${spread.flameLengthM.toFixed(1)} m`);
  } else {
    situationItems.push("Simulation results not yet available.");
  }
  situationItems.push(...evacStatusItems(opts));

  const weatherRows: Array<[string, string]> = rp ? [
    ["Wind Speed", `${rp.weather.wind_speed} km/h ${windDirLabel(rp.weather.wind_direction)}`],
    ["Temperature", `${rp.weather.temperature}°C`],
    ["Relative Humidity", `${rp.weather.relative_humidity}%`],
    ["FFMC / DMC / DC", `${rp.fwi.ffmc} / ${rp.fwi.dmc} / ${rp.fwi.dc}`],
    ["FWI / Danger Rating", `${rp.fwi_value.toFixed(1)} — ${rp.danger_rating}`],
  ] : [["Status", "Weather parameters not yet entered."]];

  const objectiveItems = buildObjectives(opts, spread, suppression);

  const resourceRows: Array<[string, string]> = suppression
    ? suppression.resources.map((r) => ["Resource", r])
    : [["Status", "Run simulation to generate resource advisory."]];

  return wrapForm("ICS 201 – Incident Briefing", [
    icsBlock("A", "Incident Information", incidentInfoBlock(opts)),
    icsBlock("B", "Current Situation Summary", renderList(situationItems)),
    icsBlock("C", "Weather Outlook", kvTable(weatherRows)),
    icsBlock("D", "Incident Objectives", renderList(objectiveItems)),
    icsBlock("E", "Operational Map", renderMapSnapshot(opts.mapSnapshotDataUrl, "Incident Map Overview")),
    icsBlock("F", "Resource Summary", kvTable(resourceRows)),
    icsBlock("G", "Communications Overview", kvTable([
      ["Primary Channel", "Command net — confirm with Communications Unit Leader"],
      ["Tactical Channel", "Operations net — assign by division"],
      ["Air-to-Ground", "ATGS frequency — confirm with Air Tactical Group Supervisor"],
      ["Public Information", "Municipal Emergency Alert System + media liaison"],
    ])),
  ], opts, "landscape");
}

// ── ICS-202: Incident Objectives ──────────────────────────────────────────────

export function buildICS202HTML(opts: ICSFormOptions): string {
  const spread = extractSpreadStats(opts.frames);
  const suppression = spread ? buildSuppressionSummary(spread) : null;
  const rp = opts.runParams;

  const objectiveItems = buildObjectives(opts, spread, suppression);

  const commandItems = suppression ? [
    `Strategy: ${suppression.strategy}`,
    suppression.strategyDetail,
    suppression.feasible
      ? "Aggressive initial attack authorized where tactically safe."
      : "No direct attack. Defensive posture — structure protection and life safety only.",
  ] : ["Command emphasis not yet determined — run simulation to generate advisory."];

  const awarenessItems: string[] = [];
  if (spread) {
    if (!suppression!.feasible) awarenessItems.push("EXTREME fire behaviour — withdraw all resources from danger zone immediately");
    if (spread.spotCount > 0) awarenessItems.push(`Active spotting — ${spread.spotCount} events detected, max ${spread.maxSpotDistM.toFixed(0)} m throw`);
    if (spread.fireType.toLowerCase().includes("crown")) awarenessItems.push("Crown fire conditions — unpredictable rate of spread, extreme ember cast");
  }
  awarenessItems.push(...evacStatusItems(opts));
  awarenessItems.push(...reachedItems(opts));
  if (awarenessItems.length === 0) awarenessItems.push("No additional situational awareness items. Maintain LACES protocol.");

  const safetyItems = [
    "Maintain LACES: Lookouts, Anchor points, Communications, Escape routes, Safety zones",
    "Monitor changing wind conditions — re-evaluate escape routes if wind shifts > 20°",
  ];
  if (spread?.spotCount) safetyItems.push(RPAS_NOTE);
  // No distance rule here: crew separation from a crown fire head is set by the IC / Operations
  if (spread?.fireType.toLowerCase().includes("crown")) safetyItems.push("Crown fire modelled: crew positioning near the head per IC / Operations");

  const weatherRows: Array<[string, string]> = rp ? [
    ["Wind", `${rp.weather.wind_speed} km/h ${windDirLabel(rp.weather.wind_direction)}`],
    ["Temp / RH", `${rp.weather.temperature}°C / ${rp.weather.relative_humidity}%`],
    ["FWI", `${rp.fwi_value.toFixed(1)} — ${rp.danger_rating}`],
  ] : [["Status", "Weather not yet entered."]];

  const controlItems = evacStatusItems(opts);
  if (controlItems.length === 0) controlItems.push("Evacuation status: none set by Planning.");

  return wrapForm("ICS 202 – Incident Objectives", [
    icsBlock("1", "Incident Information", incidentInfoBlock(opts)),
    icsBlock("2", "Operational Period Objectives", renderList(objectiveItems)),
    icsBlock("3", "Command Emphasis", renderList(commandItems)),
    icsBlock("4", "General Situational Awareness", renderList(awarenessItems)),
    icsBlock("5", "Safety Message / Analysis", renderList(safetyItems)),
    icsBlock("6", "Weather Outlook", kvTable(weatherRows)),
    icsBlock("7", "Control Measures & Evacuation Status (set by Planning)", renderList(controlItems)),
    icsBlock("8", "Attachments / References", renderList([
      "ICS 209-WF Incident Status Summary (attached)",
      "FireSim V3 GeoJSON perimeter export",
      "Burn probability GeoJSON (if Monte Carlo run completed)",
      "Alberta Emergency Management Act — evacuation zone authority",
    ])),
    icsBlock("9", "Operational Map", renderMapSnapshot(opts.mapSnapshotDataUrl, "Operational Perimeter Map")),
  ], opts, "portrait");
}

// ── ICS-203: Organization Assignment List ─────────────────────────────────────

export function buildICS203HTML(opts: ICSFormOptions): string {
  const spread = extractSpreadStats(opts.frames);
  // No organization sizing from the modelled intensity. The branch that listed Logistics and
  // Finance/Admin for "complex" fires (TRA-222, 4dde0fe, 2026-04-04) keyed on the old I-V
  // intensity classes; since the HFI classes became 1-6 (Cole & Alexander 1995, PR #12) it
  // never matched, and no source ties an HFI class to ICS organization size (the unsourced
  // HFI→complexity mapping was removed from the 209 on 2026-10-08). Planning fills the
  // organization; the form lists the positions every 203 has.

  const commandStaffRows = `
<table class="kv">
  <tr><th>Position</th><th>Name</th><th>Agency</th><th>Contact</th></tr>
  <tr><td>Incident Commander (IC)</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>Safety Officer</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>Liaison Officer</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>Public Information Officer (PIO)</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>Information Technology Officer</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
</table>`;

  const generalStaffSections = ["Operations Section Chief", "Planning (SITL)"];

  const generalStaffRows = `
<table class="kv">
  <tr><th>Section / Branch</th><th>Chief / Position</th><th>Name</th><th>Agency</th><th>Contact</th></tr>
  ${generalStaffSections.map((s) => `<tr><td>${esc(s)}</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>`).join("")}
  <tr><td>Operations — Div A (Structure Protection)</td><td>Division Supervisor</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>Operations — Div B (Evac Support)</td><td>Division Supervisor</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  ${spread?.spotCount ? `<tr><td>Air Operations Branch</td><td>ATGS</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>` : ""}
</table>
<p class="muted" style="margin-top:8px">Add Logistics, Finance / Admin and other positions as the incident's organization requires. Section Chiefs and names require manual entry.</p>`;

  return wrapForm("ICS 203 – Organization Assignment List", [
    icsBlock("1", "Incident Information", incidentInfoBlock(opts)),
    icsBlock("2", "Command Staff", commandStaffRows),
    icsBlock("3", "General Staff & Branch Assignments", generalStaffRows),
    icsBlock("4", "Agency Representatives", `<table class="kv">
      <tr><th>Agency</th><th>Representative</th><th>Contact</th><th>Location</th></tr>
      <tr><td>City of Edmonton (COE)</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
      <tr><td>Alberta Wildfire</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
      <tr><td>Edmonton Fire Rescue Services</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
      <tr><td>Edmonton Police Service</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
      <tr><td>Alberta Health Services (EMS)</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
    </table>`),
    icsBlock("5", "Technical Specialists", `<table class="kv">
      <tr><th>Specialty</th><th>Name</th><th>Agency</th><th>Contact</th></tr>
      <tr><td>Fire Behaviour Analyst (FBAN)</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
      <tr><td>RPAS / Drone Coordinator</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
      <tr><td>Situation Unit Leader (SITL)</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
    </table>`),
  ], opts, "portrait");
}

// ── ICS-204: Assignment List ──────────────────────────────────────────────────

export function buildICS204HTML(opts: ICSFormOptions): string {
  const spread = extractSpreadStats(opts.frames);
  const suppression = spread ? buildSuppressionSummary(spread) : null;
  type Division = { name: string; objectives: string[]; resources: string[]; safety: string };
  // Divisions are Operations' to define; FireSim does not derive them from evacuation status
  const divisions: Division[] = [];

  if (divisions.length === 0) {
    divisions.push({
      name: "Division A — General Operations",
      objectives: ["Division assignments are entered by Operations."],
      resources: suppression?.resources ?? ["Resources to be determined"],
      safety: "LACES protocol mandatory. Verify escape routes and safety zones before deployment.",
    });
  }

  const divBlocks = divisions.map((div, i) => {
    const body = `
      ${kvTable([["Work Location", "Confirm with SITL"], ["Report Time", "Operational briefing time"], ["Supervisor", "TBD — manual entry required"]])}
      <h4>Tactical Objectives</h4>${renderList(div.objectives)}
      <h4>Resources</h4>${renderList(div.resources)}
      <h4>Safety Notes</h4>${renderList([div.safety])}
      <h4>Communications</h4>${kvTable([["Tactical Net", "Assign frequency — ICS-205"], ["Command Net", "Confirm with Comms Unit Leader"]])}`;
    return icsBlock(`3.${i + 1}`, div.name, body);
  });

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

  const hasMapAnnotations = ann204.length > 0;
  const fireStationAnns = ann204.filter(a => a.symbolKey === "fire_station");
  const fireStationTable = `
<table class="kv">
  <tr><th>Fire Station / Mutual Aid</th><th>Contact</th><th>Address</th></tr>
  ${fireStationAnns.map(a =>
    `<tr><td>${a.label}</td><td>${a.properties.phone ?? ""}</td><td>${a.properties.address ?? ""}</td></tr>`
  ).join("")}
</table>`;

  const SYM_LABELS: Record<string, string> = {
    division_supervisor: "Div. Supervisor", staging_area: "Staging Area",
    drop_point: "Drop Point", camp: "Camp/Base", water_fill: "Water Fill",
    dozer_line: "Dozer Line", hand_line: "Hand Line",
    generic_point: "Marker", freehand_path: "Freehand", text_label: "Note",
  };

  const annotationLegend = hasMapAnnotations ? `
<table class="kv">
  <tr><th>Type</th><th>Label / Note</th><th>Location</th></tr>
  ${[...supAnns, ...stagingAnns, ...dropAnns, ...campAnns, ...waterAnns, ...markerAnns].map(a => {
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

  return wrapForm("ICS 204 – Assignment List", [
    icsBlock("1", "Incident Information", incidentInfoBlock(opts)),
    icsBlock("2", "Operations Overview", kvTable([
      ["Operations Section Chief", "TBD — manual entry"],
      ["Suppression Strategy", suppression?.strategy ?? "Pending simulation"],
      ["Active Divisions", divisions.map((d) => d.name).join("; ")],
    ])),
    ...divBlocks,
    ...(fireStationAnns.length > 0 ? [icsBlock("Resources", "Fire Stations / Mutual Aid", fireStationTable)] : []),
    ...(hasMapAnnotations ? [icsBlock("Map Legend", "Annotated Resources", annotationLegend)] : []),
    icsBlock("4", "Operational Map", renderMapSnapshot(opts.mapSnapshotDataUrl, "Assignments Map")),
  ], opts, "portrait");
}

// ── ICS-205: Communications Plan ─────────────────────────────────────────────

export function buildICS205HTML(opts: ICSFormOptions): string {
  const retsNets = `
<table class="kv">
  <tr><th>Net #</th><th>Function</th><th>Channel / Talkgroup</th><th>Frequency</th><th>Assignment</th></tr>
  <tr><td>1</td><td>Command</td><td>&nbsp;</td><td>&nbsp;</td><td>IC, Section Chiefs</td></tr>
  <tr><td>2</td><td>Tactical — Div A (Structure)</td><td>&nbsp;</td><td>&nbsp;</td><td>Ops / Div A crews</td></tr>
  <tr><td>3</td><td>Tactical — Div B (Evac Support)</td><td>&nbsp;</td><td>&nbsp;</td><td>EPS / Transit / AHS</td></tr>
  <tr><td>4</td><td>Tactical — Div C (Monitoring)</td><td>&nbsp;</td><td>&nbsp;</td><td>Patrol / RPAS</td></tr>
  <tr><td>5</td><td>Air-to-Ground</td><td>&nbsp;</td><td>&nbsp;</td><td>ATGS / Aircraft</td></tr>
  <tr><td>6</td><td>Logistics</td><td>&nbsp;</td><td>&nbsp;</td><td>Supply, Ground Support</td></tr>
  <tr><td>7</td><td>Public Information / Media</td><td>&nbsp;</td><td>&nbsp;</td><td>PIO</td></tr>
</table>
<p class="muted" style="margin-top:8px">Assign frequencies/talkgroups from Edmonton Fire Rescue Services / AEMA communications plan. Confirm with Comms Unit Leader before distribution.</p>`;

  const contacts = `
<table class="kv">
  <tr><th>Name</th><th>Position</th><th>Radio Call Sign</th><th>Phone</th><th>Agency</th></tr>
  <tr><td>&nbsp;</td><td>Incident Commander</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>&nbsp;</td><td>Operations Section Chief</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>&nbsp;</td><td>Planning / SITL</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>&nbsp;</td><td>Logistics Section Chief</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>&nbsp;</td><td>Safety Officer</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>&nbsp;</td><td>Public Information Officer</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>&nbsp;</td><td>Air Tactical Group Supervisor (ATGS)</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>&nbsp;</td><td>RPAS Coordinator</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>&nbsp;</td><td>Alberta Emergency Alert Coordinator</td><td>&nbsp;</td><td>&nbsp;</td><td>Alberta AEMA</td></tr>
</table>`;

  return wrapForm("ICS 205 – Communications Plan", [
    icsBlock("1", "Incident Information", incidentInfoBlock(opts)),
    icsBlock("2", "Radio Nets", retsNets),
    icsBlock("3", "Contact Directory", contacts),
    icsBlock("4", "Prepared / Approved", kvTable([
      ["Prepared By", "SITL — manual entry required"],
      ["Approved By / IC", "IC — manual entry required"],
    ])),
  ], opts, "portrait");
}

// ── ICS-206: Medical Plan ─────────────────────────────────────────────────────

export function buildICS206HTML(opts: ICSFormOptions): string {
  // Populate from ics206 layer annotations if available
  const medAnnotations = (opts.annotations ?? []).filter(a => a.layer === "ics206");
  const aidStationAnns = medAnnotations.filter(a => a.symbolKey === "medical_aid_station");
  const lzAnns        = medAnnotations.filter(a => a.symbolKey === "medevac_lz");
  const hospitalAnns  = medAnnotations.filter(a => a.symbolKey === "hospital");
  const ambulanceAnns = medAnnotations.filter(a => a.symbolKey === "ambulance_staging");
  const pharmacyAnns  = medAnnotations.filter(a => a.symbolKey === "pharmacy");

  const aidStationRows = aidStationAnns.length > 0
    ? aidStationAnns.map(a => {
        const coord = a.coordinates[0];
        const locStr = coord ? `${coord[1].toFixed(4)}°N, ${Math.abs(coord[0]).toFixed(4)}°W` : "";
        return `<tr><td>${a.label}</td><td>${locStr}</td><td>${a.properties.contact ?? ""}</td><td>&nbsp;</td><td>${a.properties.capacity ?? ""}</td></tr>`;
      }).join("")
    : `<tr><td>Station 1 — ICP (Primary)</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>Station 2 — Forward (Div A)</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>`;

  const lzNote = lzAnns.length > 0
    ? `<p style="margin-top:6px"><strong>Medevac LZ${lzAnns.length > 1 ? "s" : ""}:</strong> ${lzAnns.map(a => {
        const coord = a.coordinates[0];
        return `${a.label} (${coord ? `${coord[1].toFixed(4)}°N, ${Math.abs(coord[0]).toFixed(4)}°W` : "see map"})`;
      }).join("; ")}</p>`
    : "";

  const aidStations = `
<table class="kv">
  <tr><th>Aid Station</th><th>Location</th><th>Contact</th><th>Paramedics</th><th>Capacity</th></tr>
  ${aidStationRows}
</table>${lzNote}`;

  // ── Transportation Resources ──────────────────────────────────────────────
  const transportRows = ambulanceAnns.length > 0
    ? ambulanceAnns.map(a =>
        `<tr><td>${a.label}</td><td>${a.properties.phone ?? ""}</td><td>${a.properties.address ?? ""}</td><td>&nbsp;</td></tr>`
      ).join("")
    : `<tr><td>Ground Ambulance (Primary)</td><td>AHS EMS: 911 / dispatch</td><td>&nbsp;</td><td>ALS / BLS</td></tr>
  <tr><td>Ground Ambulance (Backup)</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>Air Ambulance (STARS)</td><td>STARS: 1-800-387-7772</td><td>&nbsp;</td><td>Critical care transport</td></tr>`;

  const transport = `
<table class="kv">
  <tr><th>Resource</th><th>Contact</th><th>Location / Staging</th><th>Capability</th></tr>
  ${transportRows}
</table>`;

  // ── Hospitals ─────────────────────────────────────────────────────────────
  const hospitalRows = hospitalAnns.length > 0
    ? hospitalAnns.map(a => {
        const phone = a.properties.phone ?? "";
        const specialty = a.properties["healthcare:speciality"] ?? a.properties.speciality ?? "";
        const distKm = opts.ignitionPoint && a.coordinates[0]
          ? haversineKm(opts.ignitionPoint.lat, opts.ignitionPoint.lng,
              a.coordinates[0][1], a.coordinates[0][0]).toFixed(0) + " km"
          : "&nbsp;";
        return `<tr><td>${a.label}</td><td>${phone}</td><td>${distKm}</td><td>&nbsp;</td><td>${specialty || a.properties.address || "&nbsp;"}</td></tr>`;
      }).join("")
    : `<tr><td>Royal Alexandra Hospital</td><td>(780) 477-4111</td><td>&nbsp;</td><td>&nbsp;</td><td>Level I Trauma, Burn Unit</td></tr>
  <tr><td>University of Alberta Hospital</td><td>(780) 407-8822</td><td>&nbsp;</td><td>&nbsp;</td><td>Level I Trauma</td></tr>
  <tr><td>Grey Nuns Community Hospital</td><td>(780) 735-7000</td><td>&nbsp;</td><td>&nbsp;</td><td>Emergency, Surgery</td></tr>`;

  const hospitalNote = hospitalAnns.length > 0
    ? `<p class="muted" style="margin-top:6px">Sources include OpenStreetMap data — verify all details at time of incident.</p>`
    : `<p class="muted" style="margin-top:6px"><em>Default — Edmonton region hospitals. Click 📡 on the map to fetch facilities for your incident location.</em></p>`;

  const hospitals = `
<table class="kv">
  <tr><th>Hospital</th><th>Phone</th><th>Distance</th><th>Air Travel</th><th>Capabilities</th></tr>
  ${hospitalRows}
</table>${hospitalNote}`;

  // ── Pharmacies ────────────────────────────────────────────────────────────
  const pharmacySec = pharmacyAnns.length > 0 ? `
<table class="kv">
  <tr><th>Pharmacy</th><th>Phone</th><th>Address</th></tr>
  ${pharmacyAnns.map(a =>
    `<tr><td>${a.label}</td><td>${a.properties.phone ?? ""}</td><td>${a.properties.address ?? ""}</td></tr>`
  ).join("")}
</table>` : `<p class="muted">No pharmacies on ICS-206 layer. Use 📡 Fetch or place markers manually.</p>`;

  return wrapForm("ICS 206 – Medical Plan", [
    icsBlock("1", "Incident Information", incidentInfoBlock(opts)),
    icsBlock("2", "Medical Aid Stations", aidStations),
    icsBlock("3", "Transportation Resources", transport),
    icsBlock("4", "Hospitals", hospitals),
    icsBlock("5", "Pharmacies", pharmacySec),
    icsBlock("7", "Medical Emergency Procedures", renderList([
      "Any injury or illness: notify Safety Officer immediately",
      "Life-threatening emergency: call 911 and notify IC",
      "Dehydration/heat stress: remove from work area, transport to aid station",
      "Smoke inhalation: immediate medical assessment; evacuate to fresh air",
      "Burns: cool with water, cover with sterile dressing, transport to hospital",
    ])),
    icsBlock("8", "Responder Safety Notes", renderList([
      "Assign Medical Unit Leader before incident personnel exceed 25",
      "Pre-position ALS unit at ICP during active firefighting operations",
      "RPAS pilots: eye protection mandatory; rotor strike first aid kit required at LZ",
    ])),
    icsBlock("9", "Operational Map", renderMapSnapshot(opts.mapSnapshotDataUrl, "Medical Support Map")),
  ], opts, "portrait");
}

// ── ICS-214: Activity Log (blank template) ───────────────────────────────────

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

/**
 * Build a combined ICS-201 through ICS-206 document with page breaks.
 * Used for "Generate Full IAP" — prints as a single PDF.
 */
export function buildFullIAPHTML(opts: ICSFormOptions): string {
  // Strip outer HTML from each form and concatenate with page breaks
  const forms = [
    buildICS201HTML(opts),
    buildICS202HTML(opts),
    buildICS203HTML(opts),
    buildICS204HTML(opts),
    buildICS205HTML(opts),
    buildICS206HTML(opts),
  ];

  // Form codes for page numbering (index-aligned with forms array)
  const FORM_CODES = ["ICS 201", "ICS 202", "ICS 203", "ICS 204", "ICS 205", "ICS 206"];
  const totalForms = forms.length;

  // Extract body content from each form — use string slicing instead of regex so
  // injected <script> tags after the container don't break the match.
  const CONTAINER_OPEN = '<div class="ics-container">';
  const bodies = forms.map((html) => {
    const start = html.indexOf(CONTAINER_OPEN);
    if (start === -1) return "";
    const contentStart = start + CONTAINER_OPEN.length;
    const end = html.lastIndexOf("</div>"); // last </div> = closing ics-container
    return end > contentStart ? html.slice(contentStart, end) : "";
  });

  // Inject IAP sheet position into each form's header meta, and append a notes fill zone
  const bodiesWithPagination = bodies.map((b, i) => {
    // "IAP Sheet X of Y" = this form's position in the package (not printed page number)
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
      /* Running form title — picks up string-set from .ics-header__title */
      @top-right {
        content: string(icsFormTitle) " — pg " counter(page) " of " counter(pages);
        font-size: 10px; color: #475569;
        font-family: "Helvetica", "Arial", sans-serif;
      }
    }
    /* Cover page and first form page: suppress the running header */
    @page :first { @top-right { content: none; } }
    body { font-family: "Helvetica", "Arial", sans-serif; margin: 0; color: #111827; background: #ffffff; font-size: 13px; line-height: 1.5; }
    /* Cover page */
    .iap-cover { text-align: center; padding: 80px 40px; page-break-after: always; break-after: page; border-bottom: 3px solid #0f172a; }
    .iap-cover h1 { font-size: 32px; font-weight: 800; text-transform: uppercase; letter-spacing: -0.01em; margin-bottom: 12px; }
    .iap-cover .iap-meta { font-size: 14px; color: #475569; }
    /* Each form fills a page — no outer border, just page margins */
    .iap-page {
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      padding-bottom: 16px;
    }
    .page-break { page-break-before: always; break-before: page; }
    @media print { .iap-page { min-height: auto; padding-bottom: 0; } }
    /* Container: no card border — page boundary is the margin */
    .ics-container { background: #ffffff; padding: 0; flex: 1; display: flex; flex-direction: column; }
    .ics-header { display: flex; justify-content: space-between; align-items: baseline; border-bottom: 3px solid #0f172a; padding-bottom: 10px; margin-bottom: 14px; }
    /* string-set captures form title so @page top-right repeats it on every printed page */
    .ics-header__title { font-size: 20px; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase; string-set: icsFormTitle content(); }
    .ics-header__meta { font-size: 11px; color: #475569; text-transform: uppercase; letter-spacing: 0.08em; }
    .iap-pg-label { color: #1d4ed8; font-weight: 700; letter-spacing: 0.06em; }
    table.kv { width: 100%; border-collapse: collapse; margin-top: 4px; }
    table.kv th, table.kv td { border: 1px solid #1f2937; padding: 6px 8px; vertical-align: top; }
    table.kv th { background: #e9efff; width: 30%; font-weight: 600; }
    ul { margin: 0; padding-left: 20px; }
    .muted { color: #6b7280; font-style: italic; }
    .map-block { margin-top: 12px; border: 2px solid #1d4ed8; border-radius: 8px; padding: 12px; background: #eff6ff; }
    .map-block img { width: 100%; max-width: 720px; border: 1px solid #93c5fd; border-radius: 4px; }
    .map-legend { list-style: none; margin: 8px 0 0; padding: 0; display: flex; flex-wrap: wrap; gap: 12px; font-size: 12px; }
    .map-legend li { display: flex; align-items: center; gap: 4px; }
    .map-legend span { display: inline-block; width: 12px; height: 12px; border-radius: 2px; border: 1px solid #0f172a; flex-shrink: 0; }
    .ics-block { border: 1px solid #1f2937; border-radius: 6px; margin-bottom: 12px; overflow: hidden; page-break-inside: avoid; break-inside: avoid; }
    .ics-block header { display: flex; align-items: center; gap: 12px; padding: 7px 12px; background: #0f172a; color: #f8fafc; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; }
    .ics-block__number { display: inline-flex; align-items: center; justify-content: center; width: 26px; height: 26px; border-radius: 50%; background: #1d4ed8; font-size: 12px; flex-shrink: 0; }
    .ics-block__title { font-size: 13px; }
    .ics-block__body { padding: 10px 12px 14px; background: #ffffff; }
    h4 { margin: 10px 0 4px; font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; color: #374151; }
    .generated-note { font-size: 11px; color: #6b7280; font-style: italic; margin-top: 12px; border-top: 1px solid #e5e7eb; padding-top: 8px; }
    /* Edit-hint hidden in IAP (redundant with notes zone) */
    .edit-hint { display: none; }
    /* Editable notes fill — expands to fill remaining page space */
    .iap-notes {
      flex: 1;
      min-height: 60px;
      margin-top: 8px;
      padding: 8px 10px;
      border: 1px dashed #d1d5db;
      border-radius: 4px;
      color: #9ca3af;
      font-size: 12px;
      cursor: text;
    }
    .iap-notes:focus { outline: 1px solid #93c5fd; color: #111827; }
    .iap-notes-hint { font-style: italic; pointer-events: none; }
    /* Editable cell styles */
    [contenteditable="true"]:hover { background: #fefce8 !important; outline: 1px dashed #ca8a04; outline-offset: 1px; border-radius: 2px; }
    [contenteditable="true"]:focus { background: #fef9c3 !important; outline: 2px solid #d97706; outline-offset: 1px; border-radius: 2px; }
    @media print {
      .iap-notes { border: none; min-height: 0; color: #111827; }
      .iap-notes-hint { display: none; }
      [contenteditable] { outline: none !important; background: transparent !important; }
    }
  </style>
</head>
<body>
  <div class="iap-cover">
    <h1>Incident Action Plan</h1>
    <div class="iap-meta">${esc(opts.incidentName)} &bull; ${dateStr} &bull; Generated by FireSim V3</div>
    <div class="iap-meta" style="margin-top:8px">Contains: ICS-201 &bull; ICS-202 &bull; ICS-203 &bull; ICS-204 &bull; ICS-205 &bull; ICS-206</div>
  </div>
  ${bodiesWithPagination.map((b, i) =>
    `<div class="${i > 0 ? "page-break " : ""}iap-page"><div class="ics-container">${b}</div></div>`
  ).join("\n")}
  <script>
    document.querySelectorAll('td').forEach(function(td) {
      td.contentEditable = 'true';
      td.spellcheck = false;
    });
  </script>
</body>
</html>`;
}

// ── Evacuation status (set by Planning) ───────────────────────────────────────

/** "Evacuation Order (set by Planning): A, B" lines, as entered; nothing when none is set. */
function evacStatusItems(opts: ICSFormOptions): string[] {
  return (opts.evacZones ?? []).map((z) => `Evacuation ${z.tier} (set by Planning): ${z.neighbourhoods.join(", ")}`);
}

// ── Assets reached by the modelled fire (model output, never an objective) ────

/** Situational-awareness lines: assets and roads the modelled fire reaches, in clock time. */
export function reachedItems(opts: Pick<ICSFormOptions, "criticalReach">): string[] {
  const r = opts.criticalReach;
  if (!r || r.assets.length + r.roads.length === 0) return [];
  const t = (h: number | null | undefined) => (h == null ? "not reached" : clock(h, r.start));
  const when = (worst: number | null | undefined, single: number | null | undefined) =>
    r.hasEnsemble ? `${t(worst)} / ${t(single)}` : t(single);
  const items: string[] = [];
  const label = r.hasEnsemble ? "ensemble P10 / single run" : "single run";
  if (r.assets.length > 0) {
    const list = r.assets
      .slice(0, 12)
      .map((a) => `${assetLabel(a.asset)} (${when(a.worst?.near, a.single?.near)})`)
      .join("; ");
    const more = r.assets.length > 12 ? `; and ${r.assets.length - 12} more` : "";
    items.push(`Assets reached by the modelled fire, within ${r.bufferM} m (model output, ${label}): ${list}${more}`);
  }
  if (r.roads.length > 0) {
    items.push(
      `Major roads reached by the modelled fire (model output, ${label}): ${r.roads.map((x) => `${x.name} (${when(x.worst, x.single)})`).join("; ")}`,
    );
  }
  return items;
}

// ── Shared objectives builder ─────────────────────────────────────────────────

function buildObjectives(
  _opts: ICSFormOptions,
  spread: SpreadStats | null,
  suppression: SuppressionSummary | null,
): string[] {
  const objectives: string[] = [];
  if (spread) {
    objectives.push(`Contain fire within ${(spread.finalAreaHa * 1.2).toFixed(0)} ha by end of operational period`);
  }
  if (suppression) {
    objectives.push(`Execute ${suppression.strategy} tactics per suppression advisory (Intensity Class ${suppression.intensityClass})`);
  }
  if (objectives.length === 0) {
    objectives.push("Run simulation to generate specific measurable objectives.");
  }
  return objectives;
}
