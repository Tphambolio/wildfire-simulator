/**
 * ICS Canada Incident Status Summary, wildfire version (ICS Form 209-WF), print-ready HTML.
 *
 * Form: ICS Canada, "Incident Status Summary (ICS 209)", ICS Form 209-WF, 4 pages, PDF dated
 * May 2021 (https://icscanada.ca/wp-content/uploads/2023/07/Form-209-wf.pdf, listed at
 * https://icscanada.ca/resources/ics-forms/). Block numbers and titles below were checked
 * against that PDF on 2026-10-08. Block guidance: ICS Canada "ICS 209 Instructions" (Sept 2023,
 * https://icscanada.ca/wp-content/uploads/2023/11/209-Instructions.pdf), written for the
 * all-hazards Form 209, whose block numbers match 209-WF; 209-WF differs in block 9 (Status
 * OC/BH/UC/O), 28 (observed fire behaviour) and 29 (primary FBP fuel type).
 * Not verified: the expansions of the block 9 codes (the form prints only "OC BH UC O";
 * "Out of Control / Being Held / Under Control / Out" is Canadian stage-of-control usage),
 * and whether Alberta agencies file 209-WF in practice.
 *
 * Rules (docs/ics-canada-209.md):
 * - Blocks filled by FireSim are marked MODEL OUTPUT (or SCENARIO INPUT for what the user
 *   entered to start the run). Everything else is left for the incident to fill in.
 * - Observed fields are never filled by the model: block 9 (status) and block 28 (observed fire
 *   behaviour) are user-entered only.
 * - Projections (blocks 36, 38, 42) use the ensemble where one is available (P50 median and P10
 *   worst-credible), else the single run, at clock times from the run's start time.
 * - Every report is stamped with the run ID and the model version (/api/v1/version git_sha).
 */

import type { SimulationFrame, BurnProbabilityResponse } from "../types/simulation";
import type { RunParams } from "../components/WeatherPanel";
import type { PlanningEvacZone } from "./evacZones";
import { assetLabel, assetReachPhrases, roadReachPhrase, type CriticalReach } from "./assets";
import type { SuppressionAdvisory } from "./suppressionAdvisory";
import { areaByMinutes, type EnsembleGrids } from "./ensemble";
import { clockAt, formatClock, formatDate, formatElapsed, zoneAbbrev } from "./time";

export const FORM_209WF_URL = "https://icscanada.ca/wp-content/uploads/2023/07/Form-209-wf.pdf";
export const FORM_209_INSTRUCTIONS_URL = "https://icscanada.ca/wp-content/uploads/2023/11/209-Instructions.pdf";

/** Fixed horizons of blocks 36, 38 and 39 (hours after the start of the time period). */
export const HORIZONS_H = [12, 24, 48, 72] as const;

// ── Pure helpers (unit-tested) ──────────────────────────────────────────────────

const WIND_DIRS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
function windDirLabel(deg: number): string {
  return WIND_DIRS[Math.round(deg / 22.5) % 16];
}

/**
 * WGS 84 latitude/longitude to UTM (Snyder 1987, USGS Professional Paper 1395, eqs 8-9 to
 * 8-15; accurate to well under a metre within a zone). Zone from the longitude (no Norway or
 * Svalbard exceptions; not needed in Canada).
 */
export function latLngToUtm(lat: number, lng: number): { zone: number; band: string; easting: number; northing: number } {
  const a = 6378137.0;
  const f = 1 / 298.257223563;
  const k0 = 0.9996;
  const e2 = f * (2 - f);
  const ep2 = e2 / (1 - e2);
  const zone = Math.floor((lng + 180) / 6) + 1;
  const lng0 = ((zone - 1) * 6 - 180 + 3) * (Math.PI / 180);
  const phi = (lat * Math.PI) / 180;
  const lam = (lng * Math.PI) / 180;
  const N = a / Math.sqrt(1 - e2 * Math.sin(phi) ** 2);
  const T = Math.tan(phi) ** 2;
  const C = ep2 * Math.cos(phi) ** 2;
  const A = Math.cos(phi) * (lam - lng0);
  const e4 = e2 * e2, e6 = e4 * e2;
  const M = a * ((1 - e2 / 4 - (3 * e4) / 64 - (5 * e6) / 256) * phi
    - ((3 * e2) / 8 + (3 * e4) / 32 + (45 * e6) / 1024) * Math.sin(2 * phi)
    + ((15 * e4) / 256 + (45 * e6) / 1024) * Math.sin(4 * phi)
    - ((35 * e6) / 3072) * Math.sin(6 * phi));
  const easting = k0 * N * (A + ((1 - T + C) * A ** 3) / 6 + ((5 - 18 * T + T * T + 72 * C - 58 * ep2) * A ** 5) / 120) + 500000;
  let northing = k0 * (M + N * Math.tan(phi) * (A * A / 2 + ((5 - T + 9 * C + 4 * C * C) * A ** 4) / 24
    + ((61 - 58 * T + T * T + 600 * C - 330 * ep2) * A ** 6) / 720));
  if (lat < 0) northing += 10000000;
  const bands = "CDEFGHJKLMNPQRSTUVWX";
  const band = bands[Math.min(bands.length - 1, Math.max(0, Math.floor((lat + 80) / 8)))];
  return { zone, band, easting, northing };
}

/** Last frame at or before `hours` (null when the run has not reached that time). */
export function frameAt(frames: SimulationFrame[], hours: number): SimulationFrame | null {
  if (frames.length === 0 || hours > frames[frames.length - 1].time_hours + 1e-6) return null;
  let best: SimulationFrame | null = null;
  for (const f of frames) if (f.time_hours <= hours + 1e-6) best = f;
  return best;
}

/** Modelled area at one horizon. Null fields: not modelled that far (or no ensemble). */
export interface HorizonProjection {
  hours: number;
  /** Clock time of the horizon (null without a start time) */
  at: Date | null;
  /** Inside the modelled period */
  modelled: boolean;
  singleHa: number | null;
  p50Ha: number | null;
  p10Ha: number | null;
}

/** Projected area at 12/24/48/72 h from the single run and, if present, the ensemble. */
export function projectAreas(
  frames: SimulationFrame[],
  ensemble: EnsembleGrids | null,
  start: Date | null,
  horizons: readonly number[] = HORIZONS_H,
): HorizonProjection[] {
  const runEndH = frames.length > 0 ? frames[frames.length - 1].time_hours : 0;
  return horizons.map((h) => {
    const ensOk = !!ensemble && h * 60 <= ensemble.durationMinutes + 1e-6;
    const f = frameAt(frames, h);
    return {
      hours: h,
      at: start ? clockAt(start, h) : null,
      modelled: h <= runEndH + 1e-6,
      singleHa: f ? f.area_ha : null,
      p50Ha: ensOk ? areaByMinutes(ensemble!, "p50", h * 60) : null,
      p10Ha: ensOk ? areaByMinutes(ensemble!, "p10", h * 60) : null,
    };
  });
}

/** Single-run building exposure counts reached by `hours` (null: not modelled that far or no buildings). */
export function structuresBy(frames: SimulationFrame[], hours: number): { inside: number; within100: number } | null {
  const f = frameAt(frames, hours);
  if (!f) return null;
  if (f.building_exposure) return { inside: f.building_exposure.inside_perimeter, within100: f.building_exposure.within_100m };
  return null;
}

/** Fuel types in the modelled burned area, largest share first ("C2 62 %"). */
export function burnedFuelShares(frame: SimulationFrame | null): string[] {
  if (!frame) return [];
  return Object.entries(frame.fuel_breakdown ?? {})
    .filter(([, v]) => v > 0)
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `${k} ${Math.round(v * 100)} %`);
}

// ── Burn probability area (Monte Carlo) ───────────────────────────────────────

interface BurnAreaStats {
  p25Ha: number;
  p50Ha: number;
  p75Ha: number;
}

function extractBurnAreaStats(data: BurnProbabilityResponse): BurnAreaStats {
  const cellAreaHa = (data.cell_size_m * data.cell_size_m) / 10_000;
  let p25 = 0, p50 = 0, p75 = 0;
  for (let r = 0; r < data.rows; r++) {
    for (let c = 0; c < data.cols; c++) {
      const p = data.burn_probability[r]?.[c] ?? 0;
      if (p >= 0.25) p25++;
      if (p >= 0.50) p50++;
      if (p >= 0.75) p75++;
    }
  }
  return { p25Ha: p25 * cellAreaHa, p50Ha: p50 * cellAreaHa, p75Ha: p75 * cellAreaHa };
}

// ── CSS ───────────────────────────────────────────────────────────────────────

const PRINT_CSS = `
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: Arial, Helvetica, sans-serif; font-size: 9pt; color: #000; background: #fff; }
  .page { width: 216mm; min-height: 279mm; margin: 0 auto; padding: 8mm 10mm; }
  .page + .page { page-break-before: always; }
  .form-title { text-align: center; border: 2px solid #000; padding: 4px 0; margin-bottom: 4px; }
  .form-title h1 { font-size: 13pt; font-weight: bold; letter-spacing: 1px; }
  .form-title p  { font-size: 8pt; }
  .disclaimer { background: #fff3cd; border: 1px solid #f59e0b; padding: 4px 8px; font-size: 7.5pt; margin-bottom: 6px; }
  .stamp { border: 1px solid #000; padding: 3px 6px; font-size: 7.5pt; margin-bottom: 6px; font-family: "Courier New", monospace; }
  .legend { font-size: 7pt; margin-bottom: 4px; }
  .row { display: flex; border-left: 1px solid #000; border-top: 1px solid #000; }
  .row:last-child { border-bottom: 1px solid #000; }
  .block { border-right: 1px solid #000; padding: 2px 4px; min-height: 26px; display: flex; flex-direction: column; }
  .block.model { background: #eef5ff; }
  .block.input { background: #f3f3f3; }
  .block-label { font-size: 7pt; font-weight: bold; color: #222; text-transform: uppercase; margin-bottom: 1px; }
  .tag { font-size: 6pt; font-weight: bold; padding: 0 3px; border-radius: 2px; margin-left: 4px; vertical-align: 1px; }
  .tag.model { background: #1d4ed8; color: #fff; }
  .tag.input { background: #555; color: #fff; }
  .tag.user { background: #fff; color: #000; border: 1px solid #000; }
  .block-value { font-size: 9pt; line-height: 1.3; }
  .block-value.big { font-size: 11pt; font-weight: bold; }
  .block-value.mono { font-family: "Courier New", monospace; font-size: 8pt; }
  .entry { min-height: 18px; border-bottom: 1px dotted #888; outline: none; }
  .entry.tall { min-height: 48px; }
  .note { font-size: 7pt; color: #444; font-style: italic; margin-top: 2px; }
  .w1 { flex: 1; } .w2 { flex: 2; } .w3 { flex: 3; } .w4 { flex: 4; } .w6 { flex: 6; }
  table.inner { width: 100%; border-collapse: collapse; font-size: 8pt; margin-top: 2px; }
  table.inner th { background: #e0e0e0; border: 1px solid #999; padding: 1px 4px; text-align: left; }
  table.inner td { border: 1px solid #ccc; padding: 1px 4px; vertical-align: top; }
  .section-header { background: #000; color: #fff; font-size: 8pt; font-weight: bold; padding: 2px 4px; letter-spacing: 0.5px; margin-top: 4px; }
  .section-header.gray { background: #555; }
  ul.res { margin: 2px 0 0 14px; padding: 0; font-size: 8pt; }
  ul.res li { margin-bottom: 1px; }
  label.cb { margin-right: 8px; font-size: 8.5pt; }
  .form-footer { text-align: center; font-size: 7pt; color: #555; margin-top: 6px; border-top: 1px solid #ccc; padding-top: 3px; }
  @media print {
    body { margin: 0; }
    .page { margin: 0; padding: 6mm 8mm; width: 100%; }
    .no-print { display: none !important; }
    .block.model, .block.input { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  }
`;

// ── HTML builder ──────────────────────────────────────────────────────────────

/** The run the report describes. */
export interface ICS209RunContext {
  /** API simulation ID of the run */
  simulationId: string | null;
  /** Scenario start (ignition / start of the time period), clock time */
  start: Date | null;
  /** Completed ensemble for this run (P10/P50 arrival), if any */
  ensemble: EnsembleGrids | null;
}

/** GET /api/v1/version */
export interface ModelVersion {
  version: string;
  git_sha: string;
}

export interface ICS209Options {
  frames: SimulationFrame[];
  burnProbData: BurnProbabilityResponse | null;
  runParams: RunParams | null;
  ignitionPoint: { lat: number; lng: number } | null;
  fuelTypeLabel?: string;
  /** Assets and major roads reached by the modelled fire (model output, never an instruction) */
  criticalReach?: CriticalReach | null;
  /** Evacuation status set by Planning (never generated) */
  evacZones?: PlanningEvacZone[];
  suppAdvisory?: SuppressionAdvisory | null;
  /** Run ID, start time and ensemble */
  run?: ICS209RunContext | null;
  /** Model version from /api/v1/version (null: not reachable) */
  modelVersion?: ModelVersion | null;
  incidentName?: string;
  /** Report generation time (default now; fixed in tests) */
  generatedAt?: Date;
}

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

type Kind = "model" | "input" | "user";
const TAG: Record<Kind, string> = {
  model: '<span class="tag model">MODEL OUTPUT</span>',
  input: '<span class="tag input">SCENARIO INPUT</span>',
  user: '<span class="tag user">ENTER</span>',
};

/** One form block. `value` is HTML; user blocks get an editable entry line when value is empty. */
function block(label: string, kind: Kind, value = "", width = "w2", opts: { tall?: boolean; cls?: string } = {}): string {
  const body = kind === "user" && !value
    ? `<div class="entry${opts.tall ? " tall" : ""}" contenteditable="true"></div>`
    : `<div class="block-value ${opts.cls ?? ""}">${value}</div>`;
  return `<div class="block ${width} ${kind === "user" ? "" : kind}">
    <div class="block-label">${esc(label)}${TAG[kind]}</div>
    ${body}
  </div>`;
}

function row(...blocks: string[]): string {
  return `<div class="row">${blocks.join("")}</div>`;
}

function sectionHeader(title: string, gray = false): string {
  return `<div class="section-header${gray ? " gray" : ""}">${esc(title)}</div>`;
}

function fmtHa(ha: number | null): string {
  if (ha === null) return "—";
  return ha >= 100 ? `${ha.toFixed(0)} ha` : `${ha.toFixed(1)} ha`;
}

/** "Tue, 7 Oct 14:05 MDT", or "T+12:00" without a start time. */
function whenLabel(start: Date | null, hours: number): string {
  if (!start) return formatElapsed(hours);
  const d = clockAt(start, hours);
  return `${formatDate(d)} ${formatClock(d)} ${zoneAbbrev(d)}`;
}

function checkboxes(labels: string[]): string {
  return labels.map((l) => `<label class="cb"><input type="checkbox"> ${esc(l)}</label>`).join("");
}

export function buildICS209HTML(opts: ICS209Options): string {
  const { frames, burnProbData, runParams, ignitionPoint, fuelTypeLabel, criticalReach, evacZones, suppAdvisory } = opts;
  const run = opts.run ?? null;
  const start = run?.start ?? null;
  const ensemble = run?.ensemble ?? null;
  const generated = opts.generatedAt ?? new Date();
  const generatedStr = `${formatDate(generated)} ${formatClock(generated)} ${zoneAbbrev(generated)}`;

  const final = frames.length > 0 ? frames[frames.length - 1] : null;
  const runEndH = final?.time_hours ?? runParams?.duration_hours ?? 0;
  const projections = projectAreas(frames, ensemble, start);
  // Runs shorter than a horizon: also report the end of the modelled period
  const endProj = runEndH > 0 && !HORIZONS_H.includes(runEndH as (typeof HORIZONS_H)[number]) && runEndH < 72
    ? projectAreas(frames, ensemble, start, [runEndH])[0]
    : null;
  const hasEns = !!ensemble;

  let peakRos = 0, peakHfi = 0, spotCount = 0, maxSpotDist = 0;
  for (const f of frames) {
    if (f.head_ros_m_min > peakRos) peakRos = f.head_ros_m_min;
    if (f.max_hfi_kw_m > peakHfi) peakHfi = f.max_hfi_kw_m;
    for (const s of f.spot_fires ?? []) {
      if (s.distance_m > maxSpotDist) maxSpotDist = s.distance_m;
      spotCount++;
    }
  }
  const burnArea = burnProbData ? extractBurnAreaStats(burnProbData) : null;

  const runId = run?.simulationId ?? "not recorded";
  const sha = opts.modelVersion?.git_sha ?? "unknown (version endpoint not reachable)";
  const ver = opts.modelVersion?.version ?? "?";

  // ── Stamp ────────────────────────────────────────────────────────────────
  const stamp = `<div class="stamp">
    FireSim run ID: ${esc(runId)} &nbsp;·&nbsp; model version ${esc(ver)}, git ${esc(sha)} &nbsp;·&nbsp;
    generated ${esc(generatedStr)} &nbsp;·&nbsp; ${hasEns ? `ensemble of ${ensemble!.members.length || "?"} members (P10/P50)` : "single run (no ensemble)"}
  </div>`;

  // ── Page 1: blocks 1-30 ──────────────────────────────────────────────────
  const timeFrom = start ? whenLabel(start, 0) : "start time not set";
  const timeTo = start ? whenLabel(start, runEndH) : formatElapsed(runEndH);

  const hdr = [
    row(
      block("*1. Incident Name", "user", opts.incidentName ? esc(opts.incidentName) : "", "w3", { cls: "big" }),
      block("2. Incident No.", "user", "", "w2"),
    ),
    row(
      block("*3. Report Version", "user", checkboxes(["Initial", "Update", "Final"]) + ' &nbsp;Report # <span class="entry" contenteditable="true" style="display:inline-block;min-width:30px"></span>', "w3"),
      block("*4. Incident Commander(s) & Agency or Organization", "user", "", "w3"),
      block("5. Incident Management Organization", "user", "", "w2"),
      block("*6. Incident Start Date/Time", "input", start ? `${esc(whenLabel(start, 0))}<div class="note">Scenario start entered for the run</div>` : "Not set (elapsed times used)", "w2"),
    ),
    row(
      block("7. Current Incident Size or Area Involved", "model",
        `${fmtHa(final?.area_ha ?? null)}${hasEns ? ` <span style="font-size:8pt">(P50 ${fmtHa(ensemble!.areaHa.p50)})</span>` : ""}<div class="note">Modelled area at the end of the time period (${esc(timeTo)}), not a measured size</div>`, "w3", { cls: "big" }),
      block("8. % Contained / Completed", "user", "", "w1"),
      block("9. Status", "user", checkboxes(["OC", "BH", "UC", "O"]) + '<div class="note">Observed stage of control; never set by the model</div>', "w2"),
      block("10. Incident Complexity Level", "user", "", "w2"),
      block("*11. For Time Period", "input", `Fr. ${esc(timeFrom)}<br>To ${esc(timeTo)}<div class="note">The modelled period (${esc(formatElapsed(runEndH))})</div>`, "w3"),
    ),
  ].join("");

  const approval = [
    sectionHeader("APPROVAL & ROUTING INFORMATION"),
    row(block("*12. Prepared By (name, ICS position, signature, date/time)", "user", "", "w4"), block("*13. Date/Time Submitted", "user", "", "w2")),
    row(block("*14. Approved By (name, ICS position, signature, date/time)", "user", "", "w4"), block("*15. Primary Location, Organization, or Agency Sent To", "user", "", "w2")),
  ].join("");

  const utm = ignitionPoint ? latLngToUtm(ignitionPoint.lat, ignitionPoint.lng) : null;
  const location = [
    sectionHeader("INCIDENT LOCATION INFORMATION"),
    row(
      block("*16. Province/Territory", "user", "", "w2"),
      block("*17. County, Regional/Rural Municipality, Regional/Municipal District", "user", "", "w3"),
      block("*18. City", "user", "", "w2"),
    ),
    row(
      block("19. Unit or Other", "user", "", "w2"),
      block("*20. Incident Jurisdiction", "user", "", "w2"),
      block("21. Incident Location Ownership", "user", "", "w2"),
    ),
    row(
      block("22. Longitude / Latitude", "input", ignitionPoint ? `${ignitionPoint.lng.toFixed(5)}, ${ignitionPoint.lat.toFixed(5)}<div class="note">Ignition point of the run (decimal degrees)</div>` : "—", "w3", { cls: "mono" }),
      block("23. Datum", "input", ignitionPoint ? "WGS 84" : "—", "w1"),
      block("24. Legal Description (township, section, range)", "user", "", "w3"),
    ),
    row(
      block("*25. Short Location or Area Description", "user", "", "w4"),
      block("*26. UTM Coordinates", "input", utm ? `Zone ${utm.zone}${utm.band} ${utm.easting.toFixed(0)} E ${utm.northing.toFixed(0)} N<div class="note">Ignition point, WGS 84</div>` : "—", "w3", { cls: "mono" }),
    ),
    row(block("27. Note Any Electronic Geospatial Data Included or Attached", "model",
      `Model output, available from FireSim's GeoJSON and KML export buttons (not embedded in this page):
      <ul class="res">
        <li>GeoJSON (RFC 7946, WGS 84 lng/lat): modelled perimeter at the end of the period, burn probability, spot fires, assets reached; KML: perimeter and ignition point.</li>
        <li>Content time: modelled fire at ${esc(timeTo)}; generated ${esc(generatedStr)}; run ID ${esc(runId)}, git ${esc(sha)}.</li>
        <li>Label every copy "FireSim model output — not an observed perimeter".</li>
      </ul>`, "w6")),
  ].join("");

  const fuelShares = burnedFuelShares(final);
  const s72 = Math.min(72, runEndH);
  const struct = structuresBy(frames, s72);
  const summary = [
    sectionHeader("INCIDENT SUMMARY"),
    row(block("*28. Observed Fire Behaviour or Significant Events for the Time Period Reported", "user",
      '<div class="entry tall" contenteditable="true"></div><div class="note">Observed behaviour only (field or RPAS reports); FireSim does not fill this block. Modelled behaviour is in Attachment A.</div>', "w6")),
    row(block("29. Primary FBP Fuel Type, Materials or Hazards Involved", "model",
      `${fuelShares.length ? `FBP fuel types in the modelled burned area: ${esc(fuelShares.join(", "))}.` : `${fuelTypeLabel ? `Run fuel type: ${esc(fuelTypeLabel)}. ` : ""}No modelled burned area.`}<div class="note">From the fuel grid / fuel type used by the run; not a field fuel assessment</div>`, "w6")),
    row(block("30. Damage Assessment Information", "model",
      `<table class="inner">
        <thead><tr><th>A. Structural Summary</th><th>B. # Threatened (72 hrs)</th><th>C. # Damaged</th><th>D. # Destroyed</th></tr></thead>
        <tbody>
          <tr><td>All structures (FireSim does not classify E-H)</td><td>${struct ? `${struct.within100} within 100 m of the modelled fire (${struct.inside} inside)` : "Not available (no building footprints in this run)"}</td><td>enter</td><td>enter</td></tr>
          <tr><td>E. Single Residences · F. Multiple Residences · G. Mixed Commercial/Residential · H. Nonresidential Commercial Property · Other Minor Structures</td><td>enter</td><td>enter</td><td>enter</td></tr>
        </tbody>
      </table>
      <div class="note">30B is model output: single run, building footprints within 100 m of a burned cell by ${esc(whenLabel(start, s72))}${runEndH < 72 ? ` (end of the modelled period, ${esc(formatElapsed(runEndH))}, not 72 h)` : ""}. Exposure, not damage (docs/building-exposure.md). C and D are observed and never filled by the model.</div>`, "w6")),
  ].join("");

  // ── Page 2: blocks 31-37 ─────────────────────────────────────────────────
  const evacNote = evacZones && evacZones.length > 0
    ? `<div class="note">Evacuation status set by Planning (reported as entered, never generated):</div><ul class="res">${evacZones.map((z) => `<li><strong>${esc(z.tier)}</strong>: ${esc(z.neighbourhoods.join(", "))}</li>`).join("")}</ul>`
    : "";
  const weather = runParams
    ? `Inputs used by the run (not a forecast): wind ${runParams.weather.wind_speed} km/h from ${runParams.weather.wind_direction}° (${esc(windDirLabel(runParams.weather.wind_direction))}), ${runParams.weather.temperature} °C, RH ${runParams.weather.relative_humidity} %, precipitation ${runParams.weather.precipitation_24h ?? 0} mm; FFMC ${runParams.fwi.ffmc ?? "—"}, DMC ${runParams.fwi.dmc ?? "—"}, DC ${runParams.fwi.dc ?? "—"}, ISI ${runParams.isi.toFixed(1)}, BUI ${runParams.bui.toFixed(0)}, FWI ${runParams.fwi_value.toFixed(1)} (${esc(runParams.danger_rating)}, CWFIS FWI map class).<div class="note">Add the current and predicted weather synopsis from the forecast.</div>`
    : "";

  const projRows = projections.map((p) => {
    const when = esc(whenLabel(start, p.hours));
    if (!p.modelled) {
      return `<tr><td>${p.hours} hours</td><td>${when}</td><td colspan="3">Beyond the modelled period (run ends ${esc(timeTo)}); not projected</td></tr>`;
    }
    return `<tr><td>${p.hours} hours</td><td>${when}</td><td>${hasEns ? fmtHa(p.p50Ha) : "—"}</td><td>${hasEns ? fmtHa(p.p10Ha) : "—"}</td><td>${fmtHa(p.singleHa)}</td></tr>`;
  }).join("");
  const endRow36 = endProj
    ? `<tr><td>End of modelled period (${esc(formatElapsed(runEndH))})</td><td>${esc(whenLabel(start, runEndH))}</td><td>${hasEns ? fmtHa(endProj.p50Ha) : "—"}</td><td>${hasEns ? fmtHa(endProj.p10Ha) : "—"}</td><td>${fmtHa(endProj.singleHa)}</td></tr>`
    : "";
  const proj36 = `<table class="inner">
      <thead><tr><th>Horizon</th><th>Clock time</th><th>Area P50 (median)</th><th>Area P10 (worst-credible)</th><th>Area, single run</th></tr></thead>
      <tbody>${endRow36}${projRows}<tr><td>Anticipated after 72 hrs</td><td colspan="4">${runEndH > 72 ? `Modelled to ${esc(timeTo)}: ${fmtHa(final?.area_ha ?? null)} (single run)` : "Not modelled"}</td></tr></tbody>
    </table>
    <div class="note">Model output: modelled area burned since the start of the period, no suppression. ${hasEns ? "P50 and P10 from the ensemble's arrival rasters (P10 = reached by 1 in 10 members this early: worst-credible)." : "No ensemble for this run: single run only."} Head direction and rates: Attachment A. Influencing factors: enter.</div>`;

  const page2 = [
    sectionHeader("ADDITIONAL INCIDENT DECISION SUPPORT INFORMATION"),
    row(
      block("*31. Public Status Summary", "user", "", "w3", { tall: true }),
      block("*32. Responder Status Summary", "user", "", "w3", { tall: true }),
    ),
    row(
      block("33. Life, Safety, and Health Status/Threat Remarks", "user", evacNote, "w3"),
      block("*34. Life, Safety, and Health Threat Mgmt. (\"X\" if active)", "user",
        checkboxes(["A. No Likely Threat", "B. Potential Future Threat", "C. Mass Notifications in Progress", "D. Mass Notifications Completed", "E. No Evacuation(s) Imminent", "F. Planning for Evacuation", "G. Planning for Shelter-in-Place", "H. Evacuation(s) in Progress", "I. Shelter-in-Place in Progress", "J. Repopulation in Progress", "K. Mass Immunization in Progress", "L. Mass Immunization Complete", "M. Quarantine in Progress", "N. Area Restriction in Effect"]), "w3"),
    ),
    row(block("35. Weather Concerns", "input", weather || "No run parameters.", "w6")),
    row(block("36. Projected Incident Activity, Potential, Movement, Escalation, or Spread (12, 24, 48, 72 h)", "model", proj36, "w6")),
    row(block("37. Strategic Objectives", "user", "", "w6", { tall: true })),
  ].join("");

  // ── Page 3: blocks 38-47 ─────────────────────────────────────────────────
  const threatRow = (label: string, hours: number, modelled: boolean): string => {
    const when = esc(whenLabel(start, hours));
    if (!modelled) return `<tr><td>${label}</td><td>${when}</td><td colspan="2">Beyond the modelled period; not projected</td></tr>`;
    const st = structuresBy(frames, hours);
    const reached = (criticalReach?.assets ?? []).filter((r) => Math.min(r.worst?.near ?? Infinity, r.single?.near ?? Infinity) <= hours + 1e-6);
    const roads = (criticalReach?.roads ?? []).filter((r) => r.first <= hours + 1e-6);
    const items = [
      ...reached.map((r) => `${esc(assetLabel(r.asset))}: ${esc(assetReachPhrases(r, start, criticalReach!.hasEnsemble, criticalReach!.bufferM).join("; "))}`),
      ...roads.map((r) => `${esc(r.name)}: ${esc(roadReachPhrase(r, start, criticalReach!.hasEnsemble))}`),
    ];
    return `<tr><td>${label}</td><td>${when}</td><td>${st ? `${st.within100} within 100 m (${st.inside} inside)` : "—"}</td><td>${items.length ? `<ul class="res">${items.map((i) => `<li>${i}</li>`).join("")}</ul>` : "None within the asset buffer"}</td></tr>`;
  };
  const threatRows = (endProj ? threatRow(`End of modelled period (${esc(formatElapsed(runEndH))})`, runEndH, true) : "")
    + projections.map((p) => threatRow(`${p.hours} hours`, p.hours, p.modelled)).join("");
  const threats38 = `<table class="inner">
      <thead><tr><th>Horizon</th><th>Clock time</th><th>Structures (single run)</th><th>Critical assets and major roads reached${criticalReach?.hasEnsemble ? " (worst-credible P10 · single run)" : ""}</th></tr></thead>
      <tbody>${threatRows}<tr><td>Anticipated after 72 hours</td><td colspan="3">${runEndH > 72 ? "See the asset list in Attachment A" : "Not modelled"}</td></tr></tbody>
    </table>
    <div class="note">Model output for this run, not an instruction: modelled fire within ${criticalReach?.bufferM ?? 500} m of an asset, and inside it. Risk to people, health and economic or cascading impacts: enter.</div>`;

  const ensFinal = hasEns ? `P50 ${fmtHa(ensemble!.areaHa.p50)}, range across members ${fmtHa(ensemble!.areaHa.min)} to ${fmtHa(ensemble!.areaHa.max)}; ` : "";
  const page3 = [
    sectionHeader("ADDITIONAL INCIDENT DECISION SUPPORT INFORMATION (continued)"),
    row(block("38. Current Incident Threat Summary and Risk Information (12, 24, 48, 72 h and beyond)", "model", threats38, "w6")),
    row(block("39. Critical Resource Needs (12, 24, 48, 72 h and beyond)", "user", "", "w6", { tall: true })),
    row(block("40. Strategic Discussion", "user", "", "w6", { tall: true })),
    row(block("41. Planned Actions for Next Operational Period", "user", "", "w6", { tall: true })),
    row(
      block("42. Projected Final Incident Size/Area", "model",
        `${ensFinal}single run ${fmtHa(final?.area_ha ?? null)}<div class="note">Area at the end of the modelled period (${esc(timeTo)}), no suppression. FireSim does not model when the fire ends, so this is not a final size; replace with the incident's estimate.</div>`, "w4"),
      block("43. Anticipated Incident Management Completion Date", "user", "", "w2"),
    ),
    row(
      block("44. Projected Significant Resource Demobilization Start Date", "user", "", "w2"),
      block("45. Estimated Incident Costs to Date", "user", "", "w2"),
      block("46. Projected Final Incident Cost Estimate", "user", "", "w2"),
    ),
    row(block("47. Remarks", "user", "", "w6", { tall: true })),
    sectionHeader("INCIDENT RESOURCE COMMITMENT SUMMARY (blocks 48-53)", true),
    row(block("48-53. Agency or Organization, Resources, Additional Personnel, Totals, Cooperating and Assisting Organizations", "user", '<div class="note">Page 4 of the form: complete on the ICS Canada form; FireSim has no resource data.</div>', "w6")),
  ].join("");

  // ── Attachment A: model detail ─────────────────────────────────────────────
  let burnProbBlock = "";
  if (burnArea) {
    burnProbBlock = `
    ${sectionHeader("BURN PROBABILITY (MONTE CARLO/ENSEMBLE), MODEL OUTPUT", true)}
    ${row(
      block("P ≥ 75% (High Confidence)", "model", `${burnArea.p75Ha.toFixed(1)} ha`, "w2"),
      block("P ≥ 50% (Probable)", "model", `${burnArea.p50Ha.toFixed(1)} ha`, "w2"),
      block("P ≥ 25% (Possible)", "model", `${burnArea.p25Ha.toFixed(1)} ha`, "w2"),
      block("Iterations", "input", `${runParams?.n_iterations ?? "?"}`, "w1"),
      block("Basis", "input", "Monte Carlo wind perturbation · CFFDRS FBP", "w2"),
    )}`;
  }

  let suppBlock = "";
  if (suppAdvisory) {
    suppBlock = `
    ${sectionHeader("SUPPRESSION INTERPRETATION (head fire intensity class)")}
    ${row(
      block("Head Fire Intensity Class", "model", `${esc(suppAdvisory.intensityLabel)} — ${esc(suppAdvisory.strategy)}`, "w3"),
      block("Direct attack at head", "model", suppAdvisory.suppressionFeasible ? "Generally possible" : "Generally not possible", "w2"),
    )}
    ${row(block("Interpretation", "model", `${esc(suppAdvisory.strategyDetail)}<ul class="res">${suppAdvisory.resources.map((r) => `<li>${esc(r)}</li>`).join("")}</ul><div class="note">Source: ${esc(suppAdvisory.source)}</div>`, "w4"),
      block("RPAS", "model", `<ul class="res">${suppAdvisory.rpasNotes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>`, "w2"))}`;
  }

  const attachment = `
    ${sectionHeader("ATTACHMENT A — MODELLED FIRE BEHAVIOUR (FireSim, CFFDRS FBP); NOT OBSERVATIONS")}
    ${row(
      block("Peak head ROS", "model", `${peakRos.toFixed(1)} m/min (${((peakRos * 60) / 1000).toFixed(2)} km/h)`, "w2"),
      block("Peak HFI", "model", `${peakHfi.toFixed(0)} kW/m`, "w2"),
      block("Fire type (end of period)", "model", esc((final?.fire_type ?? "—").replace(/_/g, " ")), "w2"),
      block("Flame length", "model", final && final.flame_length_m > 0 ? `${final.flame_length_m.toFixed(1)} m` : "—", "w1"),
      block("Ember spotting", "model", spotCount > 0 ? `${spotCount} events · max ${maxSpotDist.toFixed(0)} m (illustrative)` : "None modelled", "w2"),
    )}
    ${burnProbBlock}
    ${suppBlock}`;

  const footer = `
  <div class="form-footer">
    ICS Form 209-WF (ICS Canada, May 2021) layout: ${FORM_209WF_URL} · block guidance: ${FORM_209_INSTRUCTIONS_URL}<br>
    Generated by FireSim · run ${esc(runId)} · git ${esc(sha)} · ${esc(generatedStr)} · FBP: Forestry Canada ST-X-3 (1992)<br>
    Projections are modelled estimates for planning; verify with field observations before operational decisions.
  </div>`;

  const legend = `<div class="legend">${TAG.model} filled by FireSim from the model run &nbsp; ${TAG.input} values entered to start the run &nbsp; ${TAG.user} for the incident to fill in (FireSim never fills observed blocks 9 and 28) &nbsp; * required when applicable</div>`;

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ICS 209-WF Incident Status Summary — FireSim</title>
  <style>${PRINT_CSS}</style>
</head>
<body>
  <div class="page">
    <div class="form-title">
      <h1>INCIDENT STATUS SUMMARY (ICS 209)</h1>
      <p>ICS Canada Form 209-WF (wildfire) · pre-filled with FireSim model output</p>
    </div>
    <div class="disclaimer">
      PLANNING TOOL — Blocks marked MODEL OUTPUT are simulated projections (CFFDRS FBP, no suppression),
      not observations. Verify with ground or air observation before operational or public-protection decisions.
      A first validation on 143 Alberta fire-days showed low one-day skill (F1 about 0.15-0.24) with growth
      over-predicted on most days (model card: github.com/Tphambolio/wildfire-simulator/blob/master/docs/model-card.md).
    </div>
    ${stamp}
    ${legend}
    ${hdr}
    ${approval}
    ${location}
    ${summary}
  </div>
  <div class="page">
    ${stamp}
    ${page2}
  </div>
  <div class="page">
    ${stamp}
    ${page3}
    ${attachment}
    ${footer}
  </div>
  <script>
    if (window.opener || document.referrer) {
      window.addEventListener('load', function() { setTimeout(function() { window.print(); }, 400); });
    }
  </script>
</body>
</html>`;
}

/**
 * Open the 209-WF in a new window. The window opens synchronously (popup blockers), then the
 * model version is fetched (`getVersion`, 3 s budget) and stamped before the page is written.
 */
export function openICS209Report(opts: ICS209Options, getVersion?: () => Promise<ModelVersion>): void {
  const win = window.open("", "_blank", "width=900,height=1100,menubar=yes,toolbar=yes");
  const write = (modelVersion: ModelVersion | null) => {
    const html = buildICS209HTML({ ...opts, modelVersion });
    if (!win) {
      const blob = new Blob([html], { type: "text/html;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `ICS209-WF_${new Date().toISOString().slice(0, 10)}.html`;
      a.click();
      URL.revokeObjectURL(url);
      return;
    }
    win.document.write(html);
    win.document.close();
  };
  if (!getVersion) return write(opts.modelVersion ?? null);
  const timeout = new Promise<null>((resolve) => setTimeout(() => resolve(null), 3000));
  Promise.race([getVersion().catch(() => null), timeout]).then((v) => write(v));
}
