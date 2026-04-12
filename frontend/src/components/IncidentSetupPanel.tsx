/** Hazard type selector, incident complexity, weather conditions, and incident location. */

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
  const [open, setOpen] = useState(true);
  const [fetchStatus, setFetchStatus] = useState<string | null>(null);
  const [fetchLoading, setFetchLoading] = useState(false);

  const activeDef = HAZARD_DEFS.find((d) => d.key === hazardType) ?? HAZARD_DEFS[0];

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
    <div className="panel incident-setup-panel">
      <button
        className="panel-collapse-btn"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span>Incident Setup</span>
        <span className="collapse-icon">{open ? "▲" : "▼"}</span>
        <span
          className="scenario-count-badge"
          style={{ background: activeDef.color, fontSize: "0.75em" }}
        >
          {activeDef.icon}
        </span>
      </button>

      {open && (
        <div className="scenario-body">
          {/* ── Hazard type grid ── */}
          <div className="section" style={{ paddingTop: 0 }}>
            <h4>Incident Type</h4>
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

          {/* ── Complexity ── */}
          <div className="section">
            <h4>Incident Complexity</h4>
            <div className="complexity-row">
              {([5, 4, 3, 2, 1] as const).map((c) => (
                <button
                  key={c}
                  className={`complexity-btn${incidentComplexity === c ? " active" : ""}`}
                  onClick={() => onComplexityChange(c)}
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

          {/* ── Conditions ── */}
          <div className="section">
            <h4>Conditions</h4>
            <div className="weather-grid">
              <label>
                <span>Wind (km/h)</span>
                <input
                  type="number"
                  min={0}
                  max={200}
                  value={weather.wind_speed ?? ""}
                  onChange={(e) => setWeather({ wind_speed: parseFloat(e.target.value) || 0 })}
                />
              </label>
              <label>
                <span>Direction (°)</span>
                <input
                  type="number"
                  min={0}
                  max={360}
                  value={weather.wind_direction ?? ""}
                  onChange={(e) => setWeather({ wind_direction: parseFloat(e.target.value) || 0 })}
                />
              </label>
              <label>
                <span>Temp (°C)</span>
                <input
                  type="number"
                  min={-40}
                  max={50}
                  value={weather.temperature ?? ""}
                  onChange={(e) => setWeather({ temperature: parseFloat(e.target.value) || 0 })}
                />
              </label>
              <label>
                <span>RH (%)</span>
                <input
                  type="number"
                  min={0}
                  max={100}
                  value={weather.relative_humidity ?? ""}
                  onChange={(e) => setWeather({ relative_humidity: parseFloat(e.target.value) || 0 })}
                />
              </label>
              <label style={{ gridColumn: "span 2" }}>
                <span>Visibility (km)</span>
                <input
                  type="number"
                  min={0}
                  max={50}
                  step={0.5}
                  value={weather.visibility_km ?? ""}
                  onChange={(e) => setWeather({ visibility_km: parseFloat(e.target.value) || undefined })}
                />
              </label>
              <label style={{ gridColumn: "span 2" }}>
                <span>Precip (mm)</span>
                <input
                  type="number"
                  min={0}
                  max={500}
                  step={0.1}
                  value={weather.precipitation ?? ""}
                  onChange={(e) => setWeather({ precipitation: parseFloat(e.target.value) || 0 })}
                />
              </label>
            </div>
          </div>

          {/* ── Incident location ── */}
          <div className="section">
            <h4>Incident Location</h4>
            {incidentLocation ? (
              <div className="hint">
                {incidentLocation.lat.toFixed(5)}°N, {Math.abs(incidentLocation.lng).toFixed(5)}°W
              </div>
            ) : (
              <div className="hint">Click map or use ⊕ to set location</div>
            )}

            {incidentLocation && onFetchFacilities && (
              <div style={{ marginTop: 8 }}>
                <button
                  className="btn-secondary"
                  style={{ width: "100%", padding: "6px 0", fontSize: "0.85em" }}
                  onClick={handleFetch}
                  disabled={fetchLoading}
                  title={`Fetch OSM resources within ${activeDef.defaultRadius} km`}
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
