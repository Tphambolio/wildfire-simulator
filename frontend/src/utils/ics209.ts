/**
 * ICS-209 Incident Status Summary — print-ready HTML generator.
 *
 * Generates an HTML document matching the NIMS ICS-209 form layout,
 * auto-populated from FireSim V3 simulation outputs. Designed for
 * EOC/ICS briefing use; opens in a new browser window with print dialog.
 *
 * Form reference: NIMS ICS-209 (Rev. 2021-08)
 */

import type { SimulationFrame, BurnProbabilityResponse } from "../types/simulation";
import type { RunParams } from "../components/WeatherPanel";
import type { EvacZone } from "./evacZones";
import type { SuppressionAdvisory } from "../components/EOCSummary";

// ── Geometry helpers (duplicated from EOCSummary to keep utility self-contained) ──

function haversineKm(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const R = 6371.0;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLng = ((lng2 - lng1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) *
      Math.cos((lat2 * Math.PI) / 180) *
      Math.sin(dLng / 2) ** 2;
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

const WIND_DIRS = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"];
function windDirLabel(deg: number): string {
  return WIND_DIRS[Math.round(deg / 22.5) % 16];
}

// ICS complexity level derived from FBP intensity class
function complexityLevel(hfi: number): string {
  if (hfi < 200)  return "Type 5 — Single resource / small crew";
  if (hfi < 500)  return "Type 4 — Initial attack, small IMT";
  if (hfi < 2000) return "Type 3 — Extended attack IMT";
  if (hfi < 4000) return "Type 2 — Multi-agency / multi-division";
  return "Type 1 — National/Regional IMT";
}

function dangerColor(rating: string): string {
  const r = rating.toLowerCase();
  if (r.includes("extreme"))    return "#b71c1c";
  if (r.includes("very high"))  return "#e65100";
  if (r.includes("high"))       return "#f57f17";
  if (r.includes("moderate"))   return "#558b2f";
  return "#1a237e";
}

// ── Per-day stats extraction ──────────────────────────────────────────────────

interface DayStats {
  day: number;
  peakRos: number;
  peakHfi: number;
  finalAreaHa: number;
  fireType: string;
}

function extractDayStats(frames: SimulationFrame[]): DayStats[] | null {
  if (frames.length === 0) return null;
  const maxTime = frames[frames.length - 1].time_hours;
  if (maxTime <= 24) return null;
  const dayMap = new Map<number, DayStats>();
  for (const f of frames) {
    const day = f.day ?? (Math.ceil(f.time_hours / 24) || 1);
    const existing = dayMap.get(day);
    if (!existing) {
      dayMap.set(day, { day, peakRos: f.head_ros_m_min, peakHfi: f.max_hfi_kw_m, finalAreaHa: f.area_ha, fireType: f.fire_type });
    } else {
      if (f.head_ros_m_min > existing.peakRos) existing.peakRos = f.head_ros_m_min;
      if (f.max_hfi_kw_m > existing.peakHfi) existing.peakHfi = f.max_hfi_kw_m;
      existing.finalAreaHa = f.area_ha;
      existing.fireType = f.fire_type;
    }
  }
  return Array.from(dayMap.values()).sort((a, b) => a.day - b.day);
}

// ── Burn probability area ─────────────────────────────────────────────────────

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
  return {
    p25Ha: p25 * cellAreaHa,
    p50Ha: p50 * cellAreaHa,
    p75Ha: p75 * cellAreaHa,
  };
}

// ── CSS ───────────────────────────────────────────────────────────────────────

const PRINT_CSS = `
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: Arial, Helvetica, sans-serif;
    font-size: 9pt;
    color: #000;
    background: #fff;
  }
  .page {
    width: 216mm;
    min-height: 279mm;
    margin: 0 auto;
    padding: 8mm 10mm;
  }
  .form-title {
    text-align: center;
    border: 2px solid #000;
    padding: 4px 0;
    margin-bottom: 4px;
  }
  .form-title h1 { font-size: 13pt; font-weight: bold; letter-spacing: 1px; }
  .form-title p  { font-size: 8pt; }

  .disclaimer {
    background: #fff3cd;
    border: 1px solid #f59e0b;
    padding: 4px 8px;
    font-size: 7.5pt;
    margin-bottom: 6px;
    font-style: italic;
  }

  /* Block grid */
  .blocks { display: grid; gap: 0; }
  .row { display: flex; border-left: 1px solid #000; border-top: 1px solid #000; }
  .row:last-child { border-bottom: 1px solid #000; }
  .block {
    border-right: 1px solid #000;
    padding: 2px 4px;
    min-height: 22px;
    display: flex;
    flex-direction: column;
    justify-content: flex-start;
  }
  .block-label {
    font-size: 7pt;
    font-weight: bold;
    color: #333;
    text-transform: uppercase;
    margin-bottom: 1px;
    white-space: nowrap;
  }
  .block-value {
    font-size: 9pt;
    line-height: 1.3;
  }
  .block-value.big {
    font-size: 11pt;
    font-weight: bold;
  }
  .block-value.mono {
    font-family: "Courier New", monospace;
    font-size: 8pt;
  }

  /* Column widths expressed as flex-grow ratios */
  .w1 { flex: 1; }
  .w2 { flex: 2; }
  .w3 { flex: 3; }
  .w4 { flex: 4; }
  .w5 { flex: 5; }
  .w6 { flex: 6; }

  /* Tall blocks */
  .tall { min-height: 50px; }
  .xtall { min-height: 80px; }

  /* Status badge */
  .badge {
    display: inline-block;
    padding: 1px 6px;
    border-radius: 3px;
    color: #fff;
    font-weight: bold;
    font-size: 8pt;
  }

  /* Tables inside blocks */
  table.inner {
    width: 100%;
    border-collapse: collapse;
    font-size: 8pt;
    margin-top: 2px;
  }
  table.inner th {
    background: #e0e0e0;
    border: 1px solid #999;
    padding: 1px 4px;
    text-align: left;
  }
  table.inner td {
    border: 1px solid #ccc;
    padding: 1px 4px;
  }
  table.inner tr:nth-child(even) td { background: #f9f9f9; }

  /* Section headers */
  .section-header {
    background: #000;
    color: #fff;
    font-size: 8pt;
    font-weight: bold;
    padding: 2px 4px;
    letter-spacing: 0.5px;
    margin-top: 4px;
  }
  .section-header.gray {
    background: #555;
  }

  /* Resource list */
  ul.res { margin: 2px 0 0 14px; padding: 0; font-size: 8pt; }
  ul.res li { margin-bottom: 1px; }

  /* Signature line */
  .sig-line {
    border-bottom: 1px solid #000;
    min-height: 30px;
    margin-top: 4px;
  }

  /* Footer */
  .form-footer {
    text-align: center;
    font-size: 7pt;
    color: #555;
    margin-top: 6px;
    border-top: 1px solid #ccc;
    padding-top: 3px;
  }

  @media print {
    body { margin: 0; }
    .page { margin: 0; padding: 6mm 8mm; width: 100%; }
    .no-print { display: none !important; }
  }
`;

// ── HTML builder ──────────────────────────────────────────────────────────────

export interface ICS209Options {
  frames: SimulationFrame[];
  burnProbData: BurnProbabilityResponse | null;
  runParams: RunParams | null;
  ignitionPoint: { lat: number; lng: number } | null;
  fuelTypeLabel?: string;
  atRiskCounts?: { roads: number; communities: number; infrastructure: number };
  evacZones?: EvacZone[];
  suppAdvisory?: SuppressionAdvisory | null;
}

function esc(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function block(label: string, value: string, widthClass = "w2", extraClass = "", tall = false): string {
  return `<div class="block ${widthClass}${tall ? " tall" : ""}">
    <div class="block-label">${esc(label)}</div>
    <div class="block-value ${extraClass}">${value}</div>
  </div>`;
}

function sectionHeader(title: string, gray = false): string {
  return `<div class="section-header${gray ? " gray" : ""}">${esc(title)}</div>`;
}

export function buildICS209HTML(opts: ICS209Options): string {
  const { frames, burnProbData, runParams, ignitionPoint, fuelTypeLabel, atRiskCounts, evacZones, suppAdvisory } = opts;

  const now = new Date();
  const nowStr = now.toISOString().replace("T", " ").slice(0, 16) + " UTC";
  const dateOnly = now.toISOString().slice(0, 10);

  const final = frames.length > 0 ? frames[frames.length - 1] : null;
  const areaHa = final?.area_ha ?? 0;
  const perimKm = final ? perimeterLengthKm(final.perimeter ?? []) : 0;

  let peakRos = 0, peakHfi = 0, spotCount = 0, maxSpotDist = 0;
  let fireType = "Surface";
  let flameLengthM = 0;
  for (const f of frames) {
    if (f.head_ros_m_min > peakRos) peakRos = f.head_ros_m_min;
    if (f.max_hfi_kw_m > peakHfi) peakHfi = f.max_hfi_kw_m;
    for (const s of f.spot_fires ?? []) {
      if (s.distance_m > maxSpotDist) maxSpotDist = s.distance_m;
      spotCount++;
    }
  }
  if (final) {
    fireType = final.fire_type;
    flameLengthM = final.flame_length_m;
  }

  const dayStats = extractDayStats(frames);
  const burnArea = burnProbData ? extractBurnAreaStats(burnProbData) : null;
  const durationH = runParams?.duration_hours ?? (final ? final.time_hours : 0);
  const complexLevel = peakHfi > 0 ? complexityLevel(peakHfi) : "—";
  const dangerRating = runParams?.danger_rating ?? "—";

  // ── Block 1–6: Header row ────────────────────────────────────────────────

  const hdrRow1 = `<div class="row">
    ${block("1. Incident Name", "FIRESIM WILDFIRE INCIDENT", "w3", "big")}
    ${block("2. Incident Number", `${dateOnly}-FSIM-001`, "w2")}
    ${block("3. Report Version", "1 — INITIAL", "w1")}
    ${block("4. Report Date / Time", esc(nowStr), "w2")}
  </div>`;

  const hdrRow2 = `<div class="row">
    ${block("5. Incident Commander", "(Not Set)", "w3")}
    ${block("6. Incident Management Organization", "(Local Agency)", "w3")}
    ${block("7. Operational Period", `Day 1 of ${dayStats ? dayStats.length : 1}  •  ${durationH}h sim`, "w2")}
    ${block("8. Incident Jurisdiction", "Municipal / Provincial", "w2")}
  </div>`;

  // ── Block 9–12: Size / complexity ────────────────────────────────────────

  const sizeRow = `<div class="row">
    ${block("9. Incident Size / Area", `<span class="big">${areaHa.toFixed(1)} ha</span>`, "w2")}
    ${block("10. Perimeter", `${perimKm > 0 ? perimKm.toFixed(2) + " km" : "—"}`, "w2")}
    ${block("11. % Contained", "0%&nbsp;&nbsp;<em style='font-size:7pt'>(not modelled)</em>", "w2")}
    ${block("12. Incident Complexity", esc(complexLevel), "w4")}
  </div>`;

  const causeRow = `<div class="row">
    ${block("13. Cause", "Unknown / Simulation", "w2")}
    ${block("14. Fire Type", esc(fireType.replace(/_/g, " ")), "w2")}
    ${block("15. Fuel Type", esc(fuelTypeLabel ?? "—"), "w2")}
    ${block("16. Incident Definition", "Wildfire — CFFDRS FBP Projection", "w4")}
  </div>`;

  // ── Block 17: Location ───────────────────────────────────────────────────

  const latStr = ignitionPoint ? `${ignitionPoint.lat.toFixed(5)}° N` : "—";
  const lngStr = ignitionPoint ? `${Math.abs(ignitionPoint.lng).toFixed(5)}° W` : "—";

  const locationRow = `<div class="row">
    ${block("17. Ignition Lat / Lng", `${esc(latStr)} &nbsp; ${esc(lngStr)}`, "w4", "mono")}
    ${block("18. Start Date / Time", `${dateOnly} &nbsp; (simulated)`, "w3")}
    ${block("19. Declared Controlled", "—", "w3")}
  </div>`;

  // ── Section: Fire Weather ────────────────────────────────────────────────

  let weatherBlock = "";
  if (runParams) {
    const { weather, fwi } = runParams;
    const dCol = dangerColor(dangerRating);
    weatherBlock = `
    ${sectionHeader("SECTION A — FIRE WEATHER")}
    <div class="row">
      ${block("20. Wind Speed", `${weather.wind_speed} km/h`, "w2")}
      ${block("21. Wind Direction", `${weather.wind_direction}° (${esc(windDirLabel(weather.wind_direction))})`, "w2")}
      ${block("22. Temperature", `${weather.temperature}°C`, "w2")}
      ${block("23. Relative Humidity", `${weather.relative_humidity}%`, "w2")}
      ${block("24. Precip (24h)", `${weather.precipitation_24h ?? 0} mm`, "w2")}
    </div>
    <div class="row">
      ${block("25. FFMC", `${fwi.ffmc ?? "—"}`, "w1")}
      ${block("26. DMC", `${fwi.dmc ?? "—"}`, "w1")}
      ${block("27. DC", `${fwi.dc ?? "—"}`, "w1")}
      ${block("28. FWI", `${runParams.fwi_value.toFixed(1)}`, "w1")}
      ${block("29. Danger Rating", `<span class="badge" style="background:${dCol}">${esc(dangerRating)}</span>`, "w2")}
      ${block("30. Weather Concerns", "Verify with CWFIS forecast before operational deployment.", "w4")}
    </div>`;
  }

  // ── Section: Fire Behavior ───────────────────────────────────────────────

  let behaviorBlock = "";
  if (frames.length > 0) {
    const rosKmH = (peakRos * 60) / 1000;
    behaviorBlock = `
    ${sectionHeader("SECTION B — FIRE BEHAVIOR")}
    <div class="row">
      ${block("31. Peak ROS (head)", `${peakRos.toFixed(1)} m/min &nbsp; (${rosKmH.toFixed(2)} km/h)`, "w3")}
      ${block("32. Peak HFI", `${peakHfi.toFixed(0)} kW/m`, "w2", "big")}
      ${block("33. Flame Length", `${flameLengthM > 0 ? flameLengthM.toFixed(1) + " m" : "—"}`, "w2")}
      ${block("34. Ember Spotting", spotCount > 0 ? `${spotCount} events · max ${maxSpotDist.toFixed(0)} m` : "None projected", "w3")}
    </div>`;
  }

  // ── Section: Multi-day progression ──────────────────────────────────────

  let multiDayBlock = "";
  if (dayStats && dayStats.length > 0) {
    const rows = dayStats.map(d =>
      `<tr>
        <td>Day ${d.day}</td>
        <td>${d.finalAreaHa.toFixed(0)}</td>
        <td>${d.peakRos.toFixed(1)}</td>
        <td>${d.peakHfi.toFixed(0)}</td>
        <td>${d.fireType.replace(/_/g, " ")}</td>
      </tr>`
    ).join("");

    multiDayBlock = `
    ${sectionHeader("SECTION C — MULTI-DAY PROJECTION (24h / 48h / 72h)", true)}
    <div class="row">
      <div class="block w10" style="flex:1; padding:4px 6px;">
        <table class="inner">
          <thead>
            <tr>
              <th>Day</th>
              <th>Area (ha)</th>
              <th>Peak ROS (m/min)</th>
              <th>Peak HFI (kW/m)</th>
              <th>Fire Type</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
        <div style="font-size:7pt; margin-top:3px; font-style:italic; color:#555;">
          Each day seeded from prior day perimeter. Weather inputs per-day from MultiDay panel.
        </div>
      </div>
    </div>`;
  }

  // ── Section: Burn probability ────────────────────────────────────────────

  let burnProbBlock = "";
  if (burnArea) {
    burnProbBlock = `
    ${sectionHeader("SECTION D — BURN PROBABILITY (Monte Carlo)", true)}
    <div class="row">
      ${block("P ≥ 75% (High Confidence)", `${burnArea.p75Ha.toFixed(1)} ha`, "w2")}
      ${block("P ≥ 50% (Probable)", `${burnArea.p50Ha.toFixed(1)} ha`, "w2")}
      ${block("P ≥ 25% (Possible)", `${burnArea.p25Ha.toFixed(1)} ha`, "w2")}
      ${block("Iterations", `${runParams?.n_iterations ?? "?"}`, "w2")}
      <div class="block w2">
        <div class="block-label">Basis</div>
        <div class="block-value" style="font-size:7.5pt;">Monte Carlo wind perturbation · CFFDRS FBP</div>
      </div>
    </div>`;
  }

  // ── Section: Evacuations ─────────────────────────────────────────────────

  let evacBlock = "";
  if (evacZones && evacZones.length > 0) {
    const zRows = evacZones.map(z => {
      const comm = z.communitiesAtRisk.length > 0
        ? z.communitiesAtRisk.slice(0, 4).join(", ") + (z.communitiesAtRisk.length > 4 ? ` +${z.communitiesAtRisk.length - 4} more` : "")
        : "None identified";
      return `<tr>
        <td><strong>${esc(z.label)}</strong></td>
        <td>${esc(z.timeRangeLabel)}</td>
        <td>${z.areaHa.toFixed(0)}</td>
        <td>${esc(comm)}</td>
      </tr>`;
    }).join("");

    evacBlock = `
    ${sectionHeader("SECTION E — ICS EVACUATION TRIGGER ZONES")}
    <div class="row">
      <div class="block" style="flex:1; padding:4px 6px;">
        <table class="inner">
          <thead>
            <tr>
              <th>Zone</th>
              <th>Time Window</th>
              <th>Area (ha)</th>
              <th>Communities at Risk</th>
            </tr>
          </thead>
          <tbody>${zRows}</tbody>
        </table>
        <div style="font-size:7pt; margin-top:3px; color:#b71c1c; font-style:italic;">
          Zone boundaries are modelled projections. Confirm with IC/Lookout before issuing public orders.
        </div>
      </div>
    </div>`;
  }

  // ── Section: At-risk infrastructure ──────────────────────────────────────

  let infraBlock = "";
  if (atRiskCounts && (atRiskCounts.roads + atRiskCounts.communities + atRiskCounts.infrastructure) > 0) {
    infraBlock = `
    ${sectionHeader("SECTION F — INFRASTRUCTURE AT RISK (within P ≥ 50% zone)")}
    <div class="row">
      ${block("Communities", `<span class="big" style="color:#e65100">${atRiskCounts.communities}</span>`, "w2")}
      ${block("Road Segments", `<span class="big" style="color:#e65100">${atRiskCounts.roads}</span>`, "w2")}
      ${block("Critical Infrastructure", `<span class="big" style="color:#e65100">${atRiskCounts.infrastructure}</span>`, "w2")}
      ${block("Actions Required", "Assess evacuation routes. Coordinate with utilities. Confirm defensible space.", "w4", "", false)}
    </div>`;
  }

  // ── Section: Suppression advisory ────────────────────────────────────────

  let suppBlock = "";
  if (suppAdvisory) {
    const resList = suppAdvisory.resources.map(r => `<li>${esc(r)}</li>`).join("");
    const rpasNotesList = suppAdvisory.rpasNotes.map(n => `<li>${esc(n)}</li>`).join("");
    suppBlock = `
    ${sectionHeader("SECTION G — SUPPRESSION ADVISORY")}
    <div class="row">
      ${block("Intensity Class", `<span class="badge" style="background:${suppAdvisory.color}">${esc(suppAdvisory.intensityLabel)}</span>`, "w3")}
      ${block("Strategy", `<strong style="color:${suppAdvisory.color}">${esc(suppAdvisory.strategy)}</strong>`, "w3")}
      ${block("Direct Attack", suppAdvisory.suppressionFeasible
        ? '<span style="color:#2e7d32;font-weight:bold;">FEASIBLE</span>'
        : '<span style="color:#b71c1c;font-weight:bold;">NOT SAFE — withdraw all crews</span>',
        "w2")}
      ${block("RPAS Min Standoff", `${suppAdvisory.rpasStandoffM.toFixed(0)} m`, "w2", "big")}
    </div>
    <div class="row">
      <div class="block w6" style="flex:3; padding:4px 6px;">
        <div class="block-label">Strategy Detail</div>
        <div class="block-value" style="font-size:8pt; font-style:italic;">${esc(suppAdvisory.strategyDetail)}</div>
        <div class="block-label" style="margin-top:4px;">Initial Attack Resources</div>
        <ul class="res">${resList}</ul>
      </div>
      <div class="block w4" style="flex:2; padding:4px 6px;">
        <div class="block-label">RPAS Operational Advisory</div>
        <ul class="res">${rpasNotesList}</ul>
        <div style="font-size:7pt; margin-top:4px; color:#555;">
          Ref: TC RPAS Near Wildfire Guidance · IC authorization required for all RPAS ops at fire
        </div>
      </div>
    </div>`;
  }

  // ── Sections: Narrative / Remarks / Signature ─────────────────────────────

  const narrativeBlock = `
  ${sectionHeader("SECTION H — SIGNIFICANT EVENTS / NARRATIVE")}
  <div class="row">
    <div class="block xtall" style="flex:1; padding:4px 6px;">
      <div class="block-label">Significant Events &amp; Current Threats (auto-populated)</div>
      <div class="block-value" style="font-size:8pt;">
        ${peakHfi >= 4000
          ? "⚠ EXTREME fire behavior — Class IV/V intensity. Life-safety only response. Immediate evacuation of all operational personnel from fire zone."
          : peakHfi >= 2000
            ? "⚠ VERY HIGH fire behavior — Class IV intensity. No direct attack. Structure protection and evacuation support are primary missions."
            : "Fire behavior within operational suppression range. Monitor continuously for escalation."}
        ${spotCount > 0 ? `<br>Ember spotting detected (${spotCount} events, max ${maxSpotDist.toFixed(0)} m). Scout for ignitions ahead of main perimeter.` : ""}
        ${evacZones && evacZones.some(z => z.label === "Order") ? "<br>Evacuation Order zone active. Confirm public notification through EM/EOC." : ""}
      </div>
    </div>
  </div>
  <div class="row">
    <div class="block xtall" style="flex:1; padding:4px 6px;">
      <div class="block-label">Remarks / Additional Information</div>
      <div class="sig-line" style="min-height:60px;"></div>
    </div>
  </div>`;

  const signatureBlock = `
  ${sectionHeader("SECTION I — PREPARED / APPROVED BY")}
  <div class="row">
    ${block("Prepared By (Situation Unit Leader)", "(signature)", "w3", "", true)}
    ${block("Position / ICS Role", "SITL", "w2")}
    ${block("Date / Time", "", "w2")}
    ${block("Approved By (Planning Section Chief)", "(signature)", "w3", "", true)}
  </div>`;

  const footer = `
  <div class="form-footer">
    ICS-209 Incident Status Summary · Generated by FireSim V3 (CFFDRS/FBP) · ${nowStr}<br>
    Fire behavior projections are modelled estimates for planning purposes only. Verify with field observations before operational decisions.<br>
    References: Forestry Canada ST-X-3 (1992) · Van Wagner (1977) crown fire · Albini (1979) spotfire · NIMS ICS-209 Rev. 2021-08
  </div>`;

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ICS-209 Incident Status Summary — FireSim V3</title>
  <style>${PRINT_CSS}</style>
</head>
<body>
  <div class="page">

    <div class="form-title">
      <h1>ICS-209 — INCIDENT STATUS SUMMARY</h1>
      <p>National Incident Management System (NIMS) · Generated by FireSim V3 — Canadian FBP System</p>
    </div>

    <div class="disclaimer">
      ⚠ PLANNING TOOL — This report is auto-populated from fire spread model outputs using CFFDRS/FBP equations.
      All values are simulated projections for planning purposes only. Verify with ground/air observation before making
      operational or public-protection decisions. Model uncertainty increases beyond 24h.
    </div>

    <div class="blocks">
      ${hdrRow1}
      ${hdrRow2}
      ${sizeRow}
      ${causeRow}
      ${locationRow}
    </div>

    ${weatherBlock}
    ${behaviorBlock}
    ${multiDayBlock}
    ${burnProbBlock}
    ${evacBlock}
    ${infraBlock}
    ${suppBlock}
    ${narrativeBlock}
    ${signatureBlock}
    ${footer}

  </div>

  <script>
    // Auto-print when opened from FireSim
    if (window.opener || document.referrer) {
      window.addEventListener('load', function() {
        setTimeout(function() { window.print(); }, 400);
      });
    }
  </script>
</body>
</html>`;
}

/**
 * Open the ICS-209 in a new browser window with auto-print.
 */
export function openICS209Report(opts: ICS209Options): void {
  const html = buildICS209HTML(opts);
  const win = window.open("", "_blank", "width=900,height=1100,menubar=yes,toolbar=yes");
  if (!win) {
    // Blocked by popup blocker — fall back to download
    const blob = new Blob([html], { type: "text/html;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `ICS209_${new Date().toISOString().slice(0, 10)}.html`;
    a.click();
    URL.revokeObjectURL(url);
    return;
  }
  win.document.write(html);
  win.document.close();
}
