/**
 * EOC-ready fire behavior summary panel.
 *
 * Provides ICS-formatted statistics for commanders, including burned area at
 * probability thresholds, peak spread metrics, and a copy-to-clipboard/print
 * export for written reports.
 */

import type { SimulationFrame, BurnProbabilityResponse } from "../types/simulation";
import type { RunParams } from "./WeatherPanel";

interface EOCSummaryProps {
  frames: SimulationFrame[];
  burnProbData: BurnProbabilityResponse | null;
  runParams: RunParams | null;
  ignitionPoint: { lat: number; lng: number } | null;
  /** Fuel type label shown in params recap */
  fuelTypeLabel?: string;
  /** At-risk feature counts from infrastructure overlay (P ≥ 50% intersection) */
  atRiskCounts?: { roads: number; communities: number; infrastructure: number };
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

// ── ICS text export ──────────────────────────────────────────────────────────

function buildICSText(
  spread: SpreadStats | null,
  burnArea: BurnAreaStats | null,
  params: RunParams | null,
  ignition: { lat: number; lng: number } | null,
  fuelTypeLabel?: string,
  atRiskCounts?: { roads: number; communities: number; infrastructure: number }
): string {
  const now = new Date().toISOString().replace("T", " ").slice(0, 19) + " UTC";
  const lines: string[] = [
    "FIRE BEHAVIOR SUMMARY — ICS 204 / EOC REPORT",
    `Generated: ${now}`,
    "Source: FireSim V3 — Canadian FBP Wildfire Spread Simulator",
    "",
  ];

  if (ignition) {
    lines.push("IGNITION POINT");
    lines.push(`  Lat: ${ignition.lat.toFixed(5)}°  Lng: ${ignition.lng.toFixed(5)}°`);
    lines.push("");
  }

  if (params) {
    lines.push("INPUT CONDITIONS");
    lines.push(`  Wind:         ${params.weather.wind_speed} km/h @ ${params.weather.wind_direction}°`);
    lines.push(`  Temp / RH:    ${params.weather.temperature}°C / ${params.weather.relative_humidity}%`);
    lines.push(`  FFMC:         ${params.fwi.ffmc ?? "—"}`);
    lines.push(`  DMC / DC:     ${params.fwi.dmc ?? "—"} / ${params.fwi.dc ?? "—"}`);
    lines.push(`  FWI:          ${params.fwi_value.toFixed(1)} (${params.danger_rating})`);
    if (fuelTypeLabel) lines.push(`  Fuel:         ${fuelTypeLabel}`);
    lines.push(`  Duration:     ${params.duration_hours}h`);
    lines.push("");
  }

  if (spread) {
    lines.push("FIRE SPREAD SIMULATION");
    lines.push(`  Final area:       ${spread.finalAreaHa.toFixed(1)} ha`);
    if (spread.perimeterKm > 0) {
      lines.push(`  Perimeter:        ${spread.perimeterKm.toFixed(2)} km`);
    }
    lines.push(`  Peak ROS:         ${spread.peakRosMMmin.toFixed(1)} m/min`);
    lines.push(`  Peak HFI:         ${spread.peakHfiKwM.toFixed(0)} kW/m`);
    lines.push(`  Fire type:        ${spread.fireType.replace(/_/g, " ")}`);
    if (spread.flameLengthM > 0) {
      lines.push(`  Flame length:     ${spread.flameLengthM.toFixed(1)} m`);
    }
    if (spread.spotCount > 0) {
      lines.push(`  Spotting:         ${spread.spotCount} events, max ${spread.maxSpotDistM.toFixed(0)} m`);
    } else {
      lines.push(`  Spotting:         None detected`);
    }
    lines.push("");
  }

  if (burnArea) {
    lines.push("BURN PROBABILITY ANALYSIS");
    lines.push(`  Area P ≥ 25%:   ${burnArea.p25Ha.toFixed(1)} ha`);
    lines.push(`  Area P ≥ 50%:   ${burnArea.p50Ha.toFixed(1)} ha`);
    lines.push(`  Area P ≥ 75%:   ${burnArea.p75Ha.toFixed(1)} ha`);
    lines.push(`  Cell size:      ${burnArea.cellSizeM.toFixed(0)} m`);
    if (params) {
      lines.push(`  Iterations:     ${params.n_iterations}`);
    }
    lines.push("");
  }

  const hasAtRisk = atRiskCounts &&
    (atRiskCounts.roads + atRiskCounts.communities + atRiskCounts.infrastructure) > 0;
  if (hasAtRisk && atRiskCounts) {
    lines.push("AT-RISK INFRASTRUCTURE (P ≥ 50% burn zone)");
    if (atRiskCounts.communities > 0) lines.push(`  Communities:    ${atRiskCounts.communities}`);
    if (atRiskCounts.roads > 0) lines.push(`  Road segments:  ${atRiskCounts.roads}`);
    if (atRiskCounts.infrastructure > 0) lines.push(`  Infra points:   ${atRiskCounts.infrastructure}`);
    lines.push("");
  }

  lines.push("— END REPORT —");
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

export default function EOCSummary({
  frames,
  burnProbData,
  runParams,
  ignitionPoint,
  fuelTypeLabel,
  atRiskCounts,
}: EOCSummaryProps) {
  const spread = extractSpreadStats(frames);
  const burnArea = burnProbData ? extractBurnAreaStats(burnProbData) : null;

  if (!spread && !burnArea && !runParams) return null;

  const icsText = buildICSText(spread, burnArea, runParams, ignitionPoint, fuelTypeLabel, atRiskCounts);

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

  const intClass = spread ? intensityClass(spread.peakHfiKwM) : null;

  return (
    <div className="panel eoc-panel" id="eoc-summary">
      <div className="eoc-header">
        <h3>EOC Summary</h3>
        <div className="eoc-actions">
          <button className="ts-btn ts-speed" onClick={handleCopy} title="Copy ICS report to clipboard">
            Copy ICS
          </button>
          <button className="ts-btn ts-speed" onClick={handlePrint} title="Print report">
            Print
          </button>
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
    </div>
  );
}
