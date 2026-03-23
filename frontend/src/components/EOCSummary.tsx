/**
 * EOC-ready fire behavior summary panel.
 *
 * Provides ICS-formatted statistics for commanders, including burned area at
 * probability thresholds, peak spread metrics, and a copy-to-clipboard/print
 * export for written reports.
 */

import type { SimulationFrame, BurnProbabilityResponse } from "../types/simulation";
import type { RunParams } from "./WeatherPanel";
import { buildGeoJSON, buildKML, downloadFile } from "../utils/geoExport";
import type { EvacZone } from "../utils/evacZones";

interface EOCSummaryProps {
  frames: SimulationFrame[];
  burnProbData: BurnProbabilityResponse | null;
  runParams: RunParams | null;
  ignitionPoint: { lat: number; lng: number } | null;
  /** Fuel type label shown in params recap */
  fuelTypeLabel?: string;
  /** At-risk feature counts from infrastructure overlay (P ≥ 50% intersection) */
  atRiskCounts?: { roads: number; communities: number; infrastructure: number };
  /** Annotated overlay GeoJSON for inclusion in GeoJSON export */
  overlayRoads?: GeoJSON.FeatureCollection | null;
  overlayCommunities?: GeoJSON.FeatureCollection | null;
  overlayInfrastructure?: GeoJSON.FeatureCollection | null;
  /** ICS evacuation zones for export and ICS report */
  evacZones?: EvacZone[];
}

// ── Geometry helpers ────────────────────────────────────────────────────────

/** Great-circle distance (km) between two lat/lng points (Haversine). */
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

/** Perimeter length in km from [[lat, lng], ...] polygon. */
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

// ── Stats extraction ─────────────────────────────────────────────────────────

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
  let peakRos = 0;
  let peakHfi = 0;
  let maxSpotDist = 0;
  let spotCount = 0;

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

interface BurnAreaStats {
  p25Ha: number;
  p50Ha: number;
  p75Ha: number;
  cellSizeM: number;
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
    cellSizeM: data.cell_size_m,
  };
}

// ── Suppression & RPAS advisory ─────────────────────────────────────────────

/**
 * FBP fire intensity class definitions and suppression guidance.
 *
 * Intensity thresholds from Alexander & de Groot (1988) as used in the
 * Canadian FBP System. Suppression feasibility based on CIFFC and BCWS
 * initial-attack decision matrices.
 *
 * Class I   HFI < 200 kW/m   — Direct ground attack feasible
 * Class II  HFI 200–500      — Flanking; aerial support beneficial
 * Class III HFI 500–2000     — Indirect attack; aerial primary
 * Class IV  HFI 2000–4000    — Defensive; structure protection
 * Class V   HFI > 4000       — Life safety only; no direct attack
 */
export interface SuppressionAdvisory {
  intensityClass: "I" | "II" | "III" | "IV" | "V";
  intensityLabel: string;
  color: string;
  strategy: string;
  strategyDetail: string;
  resources: string[];
  suppressionFeasible: boolean;
  rpasStandoffM: number;
  rpasNotes: string[];
}

export function buildSuppressionAdvisory(spread: SpreadStats): SuppressionAdvisory {
  const hfi = spread.peakHfiKwM;
  const isCrownFire = spread.fireType.toLowerCase().includes("crown");
  const hasSpotting = spread.spotCount > 0;
  const maxSpotM = spread.maxSpotDistM;

  // RPAS safe standoff: greater of 500 m buffer or 1.5× max spotfire throw.
  // Add 1 km if active crown fire (extreme ember cast, unpredictable blow-up).
  const baseStandoffM = hasSpotting ? Math.max(500, maxSpotM * 1.5) : 500;
  const rpasStandoffM = isCrownFire ? baseStandoffM + 1000 : baseStandoffM;

  const rpasNotes: string[] = [
    `Minimum safe standoff: ${rpasStandoffM.toFixed(0)} m from active perimeter`,
    "IC/ATGS authorization required before any RPAS flight near active fire",
    "Maintain visual line of sight; assign dedicated observer",
  ];
  if (isCrownFire) rpasNotes.push("Crown fire — extreme smoke column turbulence; increased loss-of-control risk");
  if (hasSpotting) rpasNotes.push(`Spotfire detected (max ${maxSpotM.toFixed(0)} m) — check for ember ignitions beyond perimeter before flying`);

  if (hfi < 200) {
    return {
      intensityClass: "I",
      intensityLabel: "Class I (Low)",
      color: "#4caf50",
      strategy: "Direct Attack",
      strategyDetail: "Ground crews can work fire edge directly. Hose lay and hand tools effective.",
      resources: [
        "1–2 Type 2 initial attack crews (IHC or CFS)",
        "1–2 Type 3 engines (water tender support)",
        "1 helicopter (sling-load water or crew transport)",
      ],
      suppressionFeasible: true,
      rpasStandoffM,
      rpasNotes,
    };
  }
  if (hfi < 500) {
    return {
      intensityClass: "II",
      intensityLabel: "Class II (Moderate)",
      color: "#8bc34a",
      strategy: "Flanking / Direct on Flanks",
      strategyDetail: "Direct attack on flanks; head too intense for direct. Single airtanker drop to slow head spread.",
      resources: [
        "2–3 Type 2 IA crews",
        "2–3 Type 3 engines",
        "1 airtanker (single engine AT) for head suppression",
        "1 helicopter (water bucket or logistics)",
      ],
      suppressionFeasible: true,
      rpasStandoffM,
      rpasNotes,
    };
  }
  if (hfi < 2000) {
    return {
      intensityClass: "III",
      intensityLabel: "Class III (High)",
      color: "#ff9800",
      strategy: "Indirect Attack",
      strategyDetail: "No direct attack on head. Build indirect lines ahead of fire; aerial water/retardant on flanks and head. Ground crews on black edge only.",
      resources: [
        "3–5 crews (indirect line construction)",
        "3+ Type 3 engines (structure/value protection)",
        "2 airtankers (SEAT or LAAT) for retardant line",
        "1–2 helicopters (water bucket, recon, crew insert)",
        "1 dozer (anchor point construction if terrain allows)",
      ],
      suppressionFeasible: true,
      rpasStandoffM,
      rpasNotes,
    };
  }
  if (hfi < 4000) {
    return {
      intensityClass: "IV",
      intensityLabel: "Class IV (Very High)",
      color: "#f44336",
      strategy: "Defensive — Structure & Value Protection",
      strategyDetail: "No direct or indirect attack. Focus on protecting communities, structures, and critical infrastructure. Evacuation support is primary mission.",
      resources: [
        "Structure protection units (engines assigned to defensible properties)",
        "Heavy airtanker (LAT/VLAT) for tactical holding if available",
        "Evacuation support resources (RCMP/municipal liaison)",
        "Bulldozers for emergency mineral soil guard",
      ],
      suppressionFeasible: false,
      rpasStandoffM,
      rpasNotes,
    };
  }
  // Class V: hfi >= 4000
  return {
    intensityClass: "V",
    intensityLabel: "Class V (Extreme)",
    color: "#b71c1c",
    strategy: "Life Safety Only — No Suppression",
    strategyDetail: "Ultra-extreme fire behavior. No suppression resources safe to deploy. Evacuate all personnel from fire zone. Activate mass evacuation protocols.",
    resources: [
      "All suppression resources withdrawn to safe zones",
      "VLAT (if available) for tactical pauses only when safe",
      "Emergency evacuation infrastructure (transit, traffic control)",
      "Mass casualty / evacuation reception planning",
    ],
    suppressionFeasible: false,
    rpasStandoffM,
    rpasNotes,
  };
}

// ── ICS text export ──────────────────────────────────────────────────────────

function buildICSText(
  spread: SpreadStats | null,
  burnArea: BurnAreaStats | null,
  params: RunParams | null,
  ignition: { lat: number; lng: number } | null,
  fuelTypeLabel?: string,
  atRiskCounts?: { roads: number; communities: number; infrastructure: number },
  dayStats?: DayStats[] | null,
  evacZones?: EvacZone[],
  suppAdvisory?: SuppressionAdvisory | null
): string {
  const now = new Date().toISOString().replace("T", " ").slice(0, 19) + " UTC";
  const lines: string[] = [
    "WILDFIRE SITUATION ANALYSIS — EOC FIRE BEHAVIOR PROJECTION",
    `Report date/time: ${now}`,
    "Model: FireSim V3 — CFFDRS/FBP Fire Spread Simulation (NRCan ST-X-3)",
    "NOTE: This report presents modeled projections for planning purposes only.",
    "      Actual fire behavior may differ. Verify with ground/air observation.",
    "",
  ];

  if (ignition) {
    lines.push("1. LOCATION");
    lines.push(`  Ignition point:  ${ignition.lat.toFixed(5)}°N  ${Math.abs(ignition.lng).toFixed(5)}°W`);
    lines.push("");
  }

  if (params) {
    lines.push("2. FIRE WEATHER CONDITIONS");
    lines.push(`  Wind speed:      ${params.weather.wind_speed} km/h`);
    lines.push(`  Wind direction:  ${params.weather.wind_direction}° (${windDirLabel(params.weather.wind_direction)})`);
    lines.push(`  Temperature:     ${params.weather.temperature}°C`);
    lines.push(`  Rel. humidity:   ${params.weather.relative_humidity}%`);
    lines.push(`  Precipitation:   ${params.weather.precipitation_24h ?? 0} mm/24h`);
    lines.push("");
    lines.push("3. CFFDRS FIRE WEATHER INDEX");
    lines.push(`  FFMC:  ${params.fwi.ffmc ?? "—"}    (Fine Fuel Moisture Code)`);
    lines.push(`  DMC:   ${params.fwi.dmc ?? "—"}    (Duff Moisture Code)`);
    lines.push(`  DC:    ${params.fwi.dc ?? "—"}    (Drought Code)`);
    lines.push(`  FWI:   ${params.fwi_value.toFixed(1)}  — ${params.danger_rating}`);
    if (fuelTypeLabel) lines.push(`  Fuel:  ${fuelTypeLabel}`);
    lines.push(`  Sim duration:    ${params.duration_hours}h`);
    lines.push("");
  }

  if (dayStats && dayStats.length > 0) {
    lines.push("4. MULTI-DAY FIRE PROGRESSION");
    lines.push("  Day   Area (ha)   Peak HFI (kW/m)   Fire Type");
    for (const d of dayStats) {
      const day = String(d.day).padEnd(5);
      const area = d.finalAreaHa.toFixed(0).padEnd(11);
      const hfi = d.peakHfi.toFixed(0).padEnd(18);
      const ftype = d.fireType.replace(/_/g, " ");
      lines.push(`  ${day} ${area} ${hfi} ${ftype}`);
    }
    lines.push("");
  }

  const sectionBase = dayStats && dayStats.length > 0 ? 5 : 4;

  if (spread) {
    lines.push(`${sectionBase}. PROJECTED FIRE BEHAVIOR`);
    lines.push(`  Projected area:    ${spread.finalAreaHa.toFixed(1)} ha`);
    if (spread.perimeterKm > 0) {
      lines.push(`  Est. perimeter:    ${spread.perimeterKm.toFixed(2)} km`);
    }
    lines.push(`  Peak ROS (head):   ${spread.peakRosMMmin.toFixed(1)} m/min`);
    lines.push(`  Peak HFI:          ${spread.peakHfiKwM.toFixed(0)} kW/m`);
    lines.push(`  Fire type:         ${spread.fireType.replace(/_/g, " ")}`);
    if (spread.flameLengthM > 0) {
      lines.push(`  Flame length:      ${spread.flameLengthM.toFixed(1)} m`);
    }
    lines.push(`  Ember spotting:    ${spread.spotCount > 0 ? `${spread.spotCount} events, max ${spread.maxSpotDistM.toFixed(0)} m` : "None projected"}`);
    lines.push("");
  }

  if (burnArea) {
    const n = sectionBase + (spread ? 1 : 0);
    lines.push(`${n}. BURN PROBABILITY (Monte Carlo — ${params?.n_iterations ?? "?"} iterations)`);
    lines.push(`  Area P ≥ 75%:  ${burnArea.p75Ha.toFixed(1)} ha  (high confidence burn zone)`);
    lines.push(`  Area P ≥ 50%:  ${burnArea.p50Ha.toFixed(1)} ha  (probable burn zone)`);
    lines.push(`  Area P ≥ 25%:  ${burnArea.p25Ha.toFixed(1)} ha  (possible burn zone)`);
    lines.push(`  Grid cell:     ${burnArea.cellSizeM.toFixed(0)} m`);
    lines.push("");
  }

  const hasAtRisk = atRiskCounts &&
    (atRiskCounts.roads + atRiskCounts.communities + atRiskCounts.infrastructure) > 0;
  if (hasAtRisk && atRiskCounts) {
    const n = sectionBase + (spread ? 1 : 0) + (burnArea ? 1 : 0);
    lines.push(`${n}. INFRASTRUCTURE AT RISK (within P ≥ 50% zone)`);
    if (atRiskCounts.communities > 0) lines.push(`  Communities:       ${atRiskCounts.communities}  — consider evacuation assessment`);
    if (atRiskCounts.roads > 0) lines.push(`  Road segments:     ${atRiskCounts.roads}  — assess route closures`);
    if (atRiskCounts.infrastructure > 0) lines.push(`  Critical infra:    ${atRiskCounts.infrastructure}  — coordinate with utilities`);
    lines.push("");
  }

  if (evacZones && evacZones.length > 0) {
    const n = sectionBase + (spread ? 1 : 0) + (burnArea ? 1 : 0) + (hasAtRisk ? 1 : 0);
    lines.push(`${n}. ICS EVACUATION TRIGGER ZONES`);
    lines.push("  Zone              Time Range   Area (ha)   Communities at Risk");
    for (const z of evacZones) {
      const zLabel = z.label.padEnd(17);
      const zTime = z.timeRangeLabel.padEnd(12);
      const zArea = z.areaHa.toFixed(0).padEnd(11);
      const zComm = z.communitiesAtRisk.length > 0
        ? z.communitiesAtRisk.slice(0, 3).join(", ") + (z.communitiesAtRisk.length > 3 ? ` +${z.communitiesAtRisk.length - 3} more` : "")
        : "None identified";
      lines.push(`  ${zLabel} ${zTime} ${zArea} ${zComm}`);
    }
    lines.push("  NOTE: Zone boundaries are modelled projections. Confirm with IC/Lookout.");
    lines.push("");
  }

  if (suppAdvisory) {
    const hasEvac = evacZones && evacZones.length > 0;
    const hasAtRisk = atRiskCounts &&
      (atRiskCounts.roads + atRiskCounts.communities + atRiskCounts.infrastructure) > 0;
    const nBase = (spread ? 1 : 0) + (burnArea ? 1 : 0) + (hasAtRisk ? 1 : 0) + (hasEvac ? 1 : 0);
    const nSupp = (dayStats && dayStats.length > 0 ? 5 : 4) + nBase;

    lines.push(`${nSupp}. SUPPRESSION ADVISORY — ${suppAdvisory.intensityLabel.toUpperCase()}`);
    lines.push(`  Strategy:     ${suppAdvisory.strategy}`);
    lines.push(`  Direct attack: ${suppAdvisory.suppressionFeasible ? "FEASIBLE" : "NOT SAFE — withdraw all crews from fire zone"}`);
    lines.push(`  Detail:       ${suppAdvisory.strategyDetail}`);
    lines.push("  Suggested initial attack resources:");
    for (const r of suppAdvisory.resources) {
      lines.push(`    • ${r}`);
    }
    lines.push("");

    lines.push(`${nSupp + 1}. RPAS OPERATIONAL ADVISORY`);
    for (const note of suppAdvisory.rpasNotes) {
      lines.push(`  • ${note}`);
    }
    lines.push("  Ref: TC RPAS Near Wildfire Guidance | IC authorization required for all RPAS ops at fire");
    lines.push("");
  }

  lines.push("─".repeat(60));
  lines.push("Prepared using CFFDRS FBP System (Forestry Canada ST-X-3, 1992)");
  lines.push("FireSim V3 | Albini 1979 spotfire | Van Wagner 1977 crown fire");
  lines.push("─".repeat(60));
  return lines.join("\n");
}

// ── Component ────────────────────────────────────────────────────────────────

const WIND_DIRS = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"];
function windDirLabel(deg: number): string {
  return WIND_DIRS[Math.round(deg / 22.5) % 16];
}

function intensityClass(hfi: number): { label: string; color: string } {
  if (hfi < 10) return { label: "Low", color: "#4caf50" };
  if (hfi < 500) return { label: "Moderate", color: "#ffeb3b" };
  if (hfi < 2000) return { label: "High", color: "#ff9800" };
  if (hfi < 4000) return { label: "Very High", color: "#f44336" };
  if (hfi < 10000) return { label: "Extreme", color: "#d32f2f" };
  return { label: "Ultra-Extreme", color: "#b71c1c" };
}

// ── Per-day stats (multi-day scenarios) ─────────────────────────────────────

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
  if (maxTime <= 24) return null; // Single-day — no breakdown needed

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

export default function EOCSummary({
  frames,
  burnProbData,
  runParams,
  ignitionPoint,
  fuelTypeLabel,
  atRiskCounts,
  overlayRoads,
  overlayCommunities,
  overlayInfrastructure,
  evacZones,
}: EOCSummaryProps) {
  const spread = extractSpreadStats(frames);
  const burnArea = burnProbData ? extractBurnAreaStats(burnProbData) : null;
  const dayStats = extractDayStats(frames);

  if (!spread && !burnArea && !runParams) return null;

  const suppAdvisoryEarly = spread ? buildSuppressionAdvisory(spread) : null;
  const icsText = buildICSText(spread, burnArea, runParams, ignitionPoint, fuelTypeLabel, atRiskCounts, dayStats, evacZones, suppAdvisoryEarly);

  const handleCopy = () => {
    navigator.clipboard.writeText(icsText).catch(() => {
      // Fallback for older browsers
      const ta = document.createElement("textarea");
      ta.value = icsText;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
    });
  };

  const handlePrint = () => window.print();

  const exportOpts = { frames, burnProbData, runParams, ignitionPoint, fuelTypeLabel, overlayRoads, overlayCommunities, overlayInfrastructure, evacZones };
  const timestamp = new Date().toISOString().slice(0, 10);

  const handleExportGeoJSON = () => {
    const geojson = buildGeoJSON(exportOpts);
    downloadFile(JSON.stringify(geojson, null, 2), `firesim_${timestamp}.geojson`, "application/geo+json");
  };

  const handleExportKML = () => {
    const kml = buildKML(exportOpts);
    downloadFile(kml, `firesim_${timestamp}.kml`, "application/vnd.google-earth.kml+xml");
  };

  const intClass = spread ? intensityClass(spread.peakHfiKwM) : null;
  const suppAdvisory = suppAdvisoryEarly;

  return (
    <div className="panel eoc-panel" id="eoc-summary">
      <div className="eoc-header">
        <h3>EOC Summary</h3>
        <div className="eoc-actions">
          <button className="ts-btn ts-speed" onClick={handleCopy} title="Copy situation report to clipboard">
            Copy Report
          </button>
          <button className="ts-btn ts-speed" onClick={handlePrint} title="Print report">
            Print
          </button>
          {frames.length > 0 && (
            <>
              <button
                className="ts-btn ts-speed"
                onClick={handleExportGeoJSON}
                title="Download GeoJSON — fire perimeter, burn probability, spot fires, at-risk infrastructure"
              >
                GeoJSON
              </button>
              <button
                className="ts-btn ts-speed"
                onClick={handleExportKML}
                title="Download KML — fire perimeter and ignition point for Google Earth / ArcGIS"
              >
                KML
              </button>
            </>
          )}
        </div>
      </div>

      {/* Input conditions recap */}
      {runParams && (
        <section className="eoc-section">
          <h4>Conditions</h4>
          <div className="eoc-grid">
            <span className="eoc-label">Wind</span>
            <span className="eoc-value">
              {runParams.weather.wind_speed} km/h {windDirLabel(runParams.weather.wind_direction)}
            </span>
            <span className="eoc-label">Temp / RH</span>
            <span className="eoc-value">
              {runParams.weather.temperature}°C / {runParams.weather.relative_humidity}%
            </span>
            <span className="eoc-label">FWI</span>
            <span className="eoc-value">
              {runParams.fwi_value.toFixed(1)}{" "}
              <span
                className="eoc-badge"
                style={{
                  background:
                    runParams.fwi_value >= 30 ? "#b71c1c" :
                    runParams.fwi_value >= 20 ? "#e65100" :
                    runParams.fwi_value >= 10 ? "#f57f17" : "#558b2f",
                }}
              >
                {runParams.danger_rating}
              </span>
            </span>
            <span className="eoc-label">FFMC</span>
            <span className="eoc-value">{runParams.fwi.ffmc ?? "—"}</span>
            {fuelTypeLabel && (
              <>
                <span className="eoc-label">Fuel</span>
                <span className="eoc-value eoc-fuel">{fuelTypeLabel}</span>
              </>
            )}
            {ignitionPoint && (
              <>
                <span className="eoc-label">Ignition</span>
                <span className="eoc-value eoc-coords">
                  {ignitionPoint.lat.toFixed(4)}°N {Math.abs(ignitionPoint.lng).toFixed(4)}°W
                </span>
              </>
            )}
          </div>
        </section>
      )}

      {/* Per-day stats (multi-day scenarios only) */}
      {dayStats && (
        <section className="eoc-section">
          <h4>Per-Day Progression</h4>
          <div className="eoc-bp-table">
            <div className="eoc-bp-row eoc-bp-header">
              <span>Day</span>
              <span>Area (ha)</span>
              <span>Peak HFI</span>
            </div>
            {dayStats.map(({ day, finalAreaHa, peakHfi }) => {
              const intClass = intensityClass(peakHfi);
              return (
                <div className="eoc-bp-row" key={day}>
                  <span className="eoc-bp-label">Day {day}</span>
                  <span className="eoc-bp-val">{finalAreaHa.toFixed(0)}</span>
                  <span className="eoc-bp-val" style={{ color: intClass.color }}>
                    {peakHfi.toFixed(0)}
                  </span>
                </div>
              );
            })}
          </div>
        </section>
      )}

      {/* Spread simulation stats */}
      {spread && (
        <section className="eoc-section">
          <h4>Fire Spread · {runParams?.duration_hours ?? "?"}h</h4>
          <div className="eoc-grid">
            <span className="eoc-label">Area</span>
            <span className="eoc-value eoc-highlight">{spread.finalAreaHa.toFixed(1)} ha</span>

            {spread.perimeterKm > 0 && (
              <>
                <span className="eoc-label">Perimeter</span>
                <span className="eoc-value">{spread.perimeterKm.toFixed(2)} km</span>
              </>
            )}

            <span className="eoc-label">Peak ROS</span>
            <span className="eoc-value">{spread.peakRosMMmin.toFixed(1)} m/min</span>

            <span className="eoc-label">Peak HFI</span>
            <span className="eoc-value" style={{ color: intClass?.color }}>
              {spread.peakHfiKwM.toFixed(0)} kW/m
              <span className="eoc-sublabel"> {intClass?.label}</span>
            </span>

            {spread.flameLengthM > 0 && (
              <>
                <span className="eoc-label">Flame Length</span>
                <span className="eoc-value">{spread.flameLengthM.toFixed(1)} m</span>
              </>
            )}

            <span className="eoc-label">Fire Type</span>
            <span className="eoc-value">{spread.fireType.replace(/_/g, " ")}</span>

            <span className="eoc-label">Spotting</span>
            <span className="eoc-value">
              {spread.spotCount > 0
                ? `${spread.spotCount} events · max ${spread.maxSpotDistM.toFixed(0)} m`
                : "None"}
            </span>
          </div>
        </section>
      )}

      {/* Burn probability area stats */}
      {burnArea && (
        <section className="eoc-section">
          <h4>Burn Probability · {runParams?.n_iterations ?? "?"} iter</h4>
          <div className="eoc-bp-table">
            <div className="eoc-bp-row eoc-bp-header">
              <span>Threshold</span>
              <span>Area (ha)</span>
            </div>
            {[
              { label: "P ≥ 75%", ha: burnArea.p75Ha, color: "#b71c1c" },
              { label: "P ≥ 50%", ha: burnArea.p50Ha, color: "#e65100" },
              { label: "P ≥ 25%", ha: burnArea.p25Ha, color: "#f57f17" },
            ].map(({ label, ha, color }) => (
              <div className="eoc-bp-row" key={label}>
                <span className="eoc-bp-label" style={{ color }}>{label}</span>
                <span className="eoc-bp-val">{ha.toFixed(1)}</span>
              </div>
            ))}
          </div>
          <div className="eoc-sublabel" style={{ marginTop: 4 }}>
            Cell size: {burnArea.cellSizeM.toFixed(0)} m
          </div>
        </section>
      )}

      {/* At-risk infrastructure (from overlay layers) */}
      {atRiskCounts &&
        (atRiskCounts.roads + atRiskCounts.communities + atRiskCounts.infrastructure) > 0 && (
        <section className="eoc-section eoc-at-risk-section">
          <h4 style={{ color: "#ff6600" }}>⚠ At-Risk Infrastructure</h4>
          <div className="eoc-sublabel" style={{ marginBottom: 6 }}>Features within P ≥ 50% burn zone</div>
          <div className="eoc-grid">
            {atRiskCounts.communities > 0 && (
              <>
                <span className="eoc-label">Communities</span>
                <span className="eoc-value eoc-highlight" style={{ color: "#ff6600" }}>
                  {atRiskCounts.communities}
                </span>
              </>
            )}
            {atRiskCounts.roads > 0 && (
              <>
                <span className="eoc-label">Road segments</span>
                <span className="eoc-value eoc-highlight" style={{ color: "#ff6600" }}>
                  {atRiskCounts.roads}
                </span>
              </>
            )}
            {atRiskCounts.infrastructure > 0 && (
              <>
                <span className="eoc-label">Infra points</span>
                <span className="eoc-value eoc-highlight" style={{ color: "#ff6600" }}>
                  {atRiskCounts.infrastructure}
                </span>
              </>
            )}
          </div>
        </section>
      )}

      {/* Suppression advisory */}
      {suppAdvisory && (
        <section className="eoc-section eoc-supp-section">
          <h4 style={{ color: suppAdvisory.color }}>
            Suppression Advisory · {suppAdvisory.intensityLabel}
          </h4>
          <div className="eoc-grid">
            <span className="eoc-label">Strategy</span>
            <span className="eoc-value" style={{ color: suppAdvisory.color, fontWeight: 600 }}>
              {suppAdvisory.strategy}
            </span>
            <span className="eoc-label">Direct attack</span>
            <span className="eoc-value">
              {suppAdvisory.suppressionFeasible ? "Feasible" : "NOT safe — withdraw crews"}
            </span>
          </div>
          <div className="eoc-sublabel" style={{ marginTop: 4, marginBottom: 6, fontStyle: "italic" }}>
            {suppAdvisory.strategyDetail}
          </div>
          <div className="eoc-sublabel" style={{ fontWeight: 600, marginBottom: 2 }}>
            Initial attack resources:
          </div>
          <ul className="eoc-resource-list">
            {suppAdvisory.resources.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </section>
      )}

      {/* RPAS operational advisory */}
      {suppAdvisory && (
        <section className="eoc-section eoc-rpas-section">
          <h4>RPAS Operational Advisory</h4>
          <div className="eoc-grid">
            <span className="eoc-label">Min standoff</span>
            <span className="eoc-value eoc-highlight">
              {suppAdvisory.rpasStandoffM.toFixed(0)} m
            </span>
          </div>
          <ul className="eoc-resource-list eoc-rpas-notes">
            {suppAdvisory.rpasNotes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
