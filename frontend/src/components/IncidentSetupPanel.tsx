/** Hazard type selector, incident complexity, weather conditions — step-by-step flow. */

import { useState } from "react";
import type { HazardType, WeatherParams } from "../types/incident";
import { HAZARD_DEFS } from "../types/incident";

interface IncidentSetupPanelProps {
  hazardType: HazardType;
  onHazardTypeChange: (h: HazardType) => void;
  incidentComplexity: 1 | 2 | 3 | 4 | 5;
  onComplexityChange: (c: 1 | 2 | 3 | 4 | 5) => void;
  weather: WeatherParams;
  onWeatherChange: (w: WeatherParams) => void;
  incidentLocation: { lat: number; lng: number } | null;
  onFetchFacilities?: () => Promise<number>;
}

const COMPLEXITY_GUIDE: Record<number, string> = {
  5: "Verbal briefing only — ICS-201 optional",
  4: "Written IAP: ICS-201, 202, 204 recommended",
  3: "Full IAP required — all standard forms",
  2: "Comprehensive IAP — daily updates each OP",
  1: "Complex incident — Unified Command, all forms",
};

// A type is "confirmed" once the user has picked anything other than the
// blank default ("other"). Complexity is always considered confirmed once
// the user has explicitly clicked a button (tracked via local state).
function isTypeConfirmed(h: HazardType) {
  return h !== "other";
}

export default function IncidentSetupPanel({
  hazardType,
  onHazardTypeChange,
  incidentComplexity,
  onComplexityChange,
  weather,
  onWeatherChange,
  incidentLocation,
  onFetchFacilities,
}: IncidentSetupPanelProps) {
  // Track whether the user has explicitly confirmed complexity so we can
  // unlock the Conditions step without requiring them to change the default.
  const [complexityConfirmed, setComplexityConfirmed] = useState(false);
  const [fetchStatus, setFetchStatus] = useState<string | null>(null);
  const [fetchLoading, setFetchLoading] = useState(false);

  const activeDef = HAZARD_DEFS.find((d) => d.key === hazardType) ?? HAZARD_DEFS[0];
  const typeConfirmed = isTypeConfirmed(hazardType);
  const showComplexity = typeConfirmed;
  const showConditions = typeConfirmed && complexityConfirmed;

  const handleFetch = async () => {
    if (!onFetchFacilities) return;
    setFetchLoading(true);
    setFetchStatus(null);
    try {
      const count = await onFetchFacilities();
      setFetchStatus(count > 0 ? `Added ${count} facilities to map` : "No new facilities found nearby");
    } catch {
      setFetchStatus("OSM fetch failed — check connection");
    } finally {
      setFetchLoading(false);
    }
  };

  const setWeather = (partial: Partial<WeatherParams>) =>
    onWeatherChange({ ...weather, ...partial });

  return (
    <div className="isp-root">

      {/* ── Step 1: Incident Type ──────────────────────────────── */}
      <div className="isp-step">
        <div className="isp-step-header">
          <span className={`isp-step-num${typeConfirmed ? " isp-step-num--done" : " isp-step-num--active"}`}>
            {typeConfirmed ? "✓" : "1"}
          </span>
          <span className="isp-step-label">Incident Type</span>
          {typeConfirmed && (
            <span className="isp-step-value" style={{ color: activeDef.color }}>
              {activeDef.icon} {activeDef.label}
            </span>
          )}
        </div>

        {/* Always show the type grid — it's step 1, always accessible */}
        <div className="isp-step-body">
          <div className="hazard-type-grid">
            {HAZARD_DEFS.map((def) => (
              <button
                key={def.key}
                className={`hazard-type-card${hazardType === def.key ? " active" : ""}`}
                style={hazardType === def.key ? { borderColor: def.color, background: `${def.color}22` } : {}}
                onClick={() => onHazardTypeChange(def.key)}
                title={def.label}
              >
                <span className="hazard-type-icon">{def.icon}</span>
                <span className="hazard-type-label">{def.label}</span>
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* ── Step 2: Complexity ────────────────────────────────── */}
      {showComplexity && (
        <div className="isp-step">
          <div className="isp-step-header">
            <span className={`isp-step-num${complexityConfirmed ? " isp-step-num--done" : " isp-step-num--active"}`}>
              {complexityConfirmed ? "✓" : "2"}
            </span>
            <span className="isp-step-label">Complexity</span>
            {complexityConfirmed && (
              <span className="isp-step-value">T{incidentComplexity}</span>
            )}
          </div>
          <div className="isp-step-body">
            <div className="complexity-row">
              {([5, 4, 3, 2, 1] as const).map((c) => (
                <button
                  key={c}
                  className={`complexity-btn${incidentComplexity === c ? " active" : ""}`}
                  onClick={() => {
                    onComplexityChange(c);
                    setComplexityConfirmed(true);
                  }}
                  title={COMPLEXITY_GUIDE[c]}
                >
                  T{c}
                </button>
              ))}
            </div>
            <div className="hint" style={{ marginTop: 6 }}>
              {COMPLEXITY_GUIDE[incidentComplexity]}
            </div>
          </div>
        </div>
      )}

      {/* ── Step 3: Conditions ───────────────────────────────── */}
      {showConditions && (
        <div className="isp-step">
          <div className="isp-step-header">
            <span className="isp-step-num isp-step-num--active">3</span>
            <span className="isp-step-label">Conditions</span>
          </div>
          <div className="isp-step-body">
            <div className="weather-grid">
              <label>
                <span>Wind (km/h)</span>
                <input
                  type="number" min={0} max={200}
                  value={weather.wind_speed ?? ""}
                  onChange={(e) => setWeather({ wind_speed: parseFloat(e.target.value) || 0 })}
                />
              </label>
              <label>
                <span>Direction (°)</span>
                <input
                  type="number" min={0} max={360}
                  value={weather.wind_direction ?? ""}
                  onChange={(e) => setWeather({ wind_direction: parseFloat(e.target.value) || 0 })}
                />
              </label>
              <label>
                <span>Temp (°C)</span>
                <input
                  type="number" min={-40} max={50}
                  value={weather.temperature ?? ""}
                  onChange={(e) => setWeather({ temperature: parseFloat(e.target.value) || 0 })}
                />
              </label>
              <label>
                <span>RH (%)</span>
                <input
                  type="number" min={0} max={100}
                  value={weather.relative_humidity ?? ""}
                  onChange={(e) => setWeather({ relative_humidity: parseFloat(e.target.value) || 0 })}
                />
              </label>
              <label style={{ gridColumn: "span 2" }}>
                <span>Precip (mm)</span>
                <input
                  type="number" min={0} max={500} step={0.1}
                  value={weather.precipitation ?? ""}
                  onChange={(e) => setWeather({ precipitation: parseFloat(e.target.value) || 0 })}
                />
              </label>
            </div>

            {/* OSM fetch once location is set */}
            {incidentLocation && onFetchFacilities && (
              <div style={{ marginTop: 10 }}>
                <button
                  className="btn-secondary"
                  style={{ width: "100%", padding: "6px 0", fontSize: "0.85em" }}
                  onClick={handleFetch}
                  disabled={fetchLoading}
                >
                  {fetchLoading ? "Fetching…" : `📡 Fetch OSM Resources (${activeDef.defaultRadius} km)`}
                </button>
                {fetchStatus && (
                  <div className="hint" style={{ marginTop: 4, color: fetchStatus.includes("failed") ? "#e57373" : "#81c784" }}>
                    {fetchStatus}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

    </div>
  );
}
