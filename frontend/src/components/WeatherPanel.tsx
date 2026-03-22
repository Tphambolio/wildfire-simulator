/** Weather and simulation parameter controls. */

import { useState } from "react";
import type { SimulationCreate, WeatherParams, FWIOverrides } from "../types/simulation";
import { FUEL_TYPES } from "../types/simulation";
import { fetchCurrentWeather, calculateFWI } from "../services/api";

interface WeatherPanelProps {
  onStartSimulation: (params: SimulationCreate) => void;
  ignitionPoint: { lat: number; lng: number } | null;
  isRunning: boolean;
}

export default function WeatherPanel({
  onStartSimulation,
  ignitionPoint,
  isRunning,
}: WeatherPanelProps) {
  const [weather, setWeather] = useState<WeatherParams>({
    wind_speed: 20,
    wind_direction: 270,
    temperature: 25,
    relative_humidity: 30,
    precipitation_24h: 0,
  });
  const [fwi, setFwi] = useState<FWIOverrides>({
    ffmc: 90,
    dmc: 45,
    dc: 300,
  });
  const [fuelType, setFuelType] = useState("C2");
  const [useEdmontonGrid, setUseEdmontonGrid] = useState(false);
  const [useSyntheticCA, setUseSyntheticCA] = useState(false);
  const [includeWater, setIncludeWater] = useState(true);
  const [includeBuildings, setIncludeBuildings] = useState(true);
  const [includeWUI, setIncludeWUI] = useState(true);
  const [durationHours, setDurationHours] = useState(4);
  const [snapshotMinutes, setSnapshotMinutes] = useState(30);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [weatherLoading, setWeatherLoading] = useState(false);
  const [weatherMessage, setWeatherMessage] = useState<string | null>(null);
  const [fwiLoading, setFwiLoading] = useState(false);
  const [computedFWI, setComputedFWI] = useState<{ isi: number; bui: number; fwi: number; danger_rating: string } | null>(null);

  const EDMONTON_FUEL_GRID_PATH =
    "/home/rpas/dev/wildfire/wildfire-self-learning/data/fuel_maps/Edmonton_FBP_FuelLayer_20251105_10m.tif";
  const EDMONTON_WATER_PATH =
    "/home/rpas/dev/wildfire/wildfire-self-learning/data/edmonton_water_bodies.geojson.gz";
  const EDMONTON_BUILDINGS_PATH =
    "/home/rpas/dev/wildfire/wildfire-self-learning/data/edmonton_buildings.geojson.gz";
  const EDMONTON_WUI_PATH =
    "/home/rpas/dev/wildfire/wildfire-self-learning/data/wui_zones.geojson.gz";

  const handleSubmit = () => {
    if (!ignitionPoint) return;
    onStartSimulation({
      ignition_lat: ignitionPoint.lat,
      ignition_lng: ignitionPoint.lng,
      weather,
      fwi_overrides: fwi,
      duration_hours: durationHours,
      snapshot_interval_minutes: snapshotMinutes,
      fuel_type: fuelType,
      fuel_grid_path: useEdmontonGrid ? EDMONTON_FUEL_GRID_PATH : null,
      water_path: useEdmontonGrid && includeWater ? EDMONTON_WATER_PATH : null,
      buildings_path: useEdmontonGrid && includeBuildings ? EDMONTON_BUILDINGS_PATH : null,
      wui_zones_path: useEdmontonGrid && includeWUI ? EDMONTON_WUI_PATH : null,
      use_ca_mode: useSyntheticCA && !useEdmontonGrid,
    });
  };

  const handleLoadWeather = async () => {
    if (!ignitionPoint) return;
    setWeatherLoading(true);
    setWeatherMessage(null);
    try {
      const w = await fetchCurrentWeather(ignitionPoint.lat, ignitionPoint.lng);
      if (w.available) {
        if (w.wind_speed !== null) setWeather((prev) => ({ ...prev, wind_speed: Math.round(w.wind_speed!) }));
        if (w.wind_direction !== null) setWeather((prev) => ({ ...prev, wind_direction: Math.round(w.wind_direction!) }));
        if (w.temperature !== null) setWeather((prev) => ({ ...prev, temperature: Math.round(w.temperature!) }));
        if (w.relative_humidity !== null) setWeather((prev) => ({ ...prev, relative_humidity: Math.round(w.relative_humidity!) }));
        setFwi({
          ffmc: w.ffmc ?? fwi.ffmc,
          dmc: w.dmc ?? fwi.dmc,
          dc: w.dc ?? fwi.dc,
        });
        if (!showAdvanced) setShowAdvanced(true);
      }
      setWeatherMessage(w.message);
    } catch {
      setWeatherMessage("Could not reach CWFIS — check network");
    } finally {
      setWeatherLoading(false);
    }
  };

  const handleComputeFWI = async () => {
    setFwiLoading(true);
    setComputedFWI(null);
    try {
      const result = await calculateFWI({
        temperature: weather.temperature,
        relative_humidity: weather.relative_humidity,
        wind_speed: weather.wind_speed,
        precipitation_24h: weather.precipitation_24h,
        ffmc_prev: fwi.ffmc ?? 85,
        dmc_prev: fwi.dmc ?? 6,
        dc_prev: fwi.dc ?? 15,
      });
      setFwi({ ffmc: result.ffmc, dmc: result.dmc, dc: result.dc });
      setComputedFWI({ isi: result.isi, bui: result.bui, fwi: result.fwi, danger_rating: result.danger_rating });
      if (!showAdvanced) setShowAdvanced(true);
    } catch {
      setComputedFWI(null);
    } finally {
      setFwiLoading(false);
    }
  };

  // Wind direction compass label
  const windLabel = (deg: number) => {
    const dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
    return dirs[Math.round(deg / 45) % 8];
  };

  return (
    <div className="panel weather-panel">
      <h3>Simulation Parameters</h3>

      {!ignitionPoint && (
        <div className="hint">Click the map to set ignition point</div>
      )}

      {ignitionPoint && (
        <div className="ignition-info">
          Ignition: {ignitionPoint.lat.toFixed(4)}, {ignitionPoint.lng.toFixed(4)}
        </div>
      )}

      <div className="section">
        <h4>Weather</h4>

        <label>
          Wind Speed: <strong>{weather.wind_speed} km/h</strong>
          <input
            type="range"
            min={0}
            max={80}
            value={weather.wind_speed}
            onChange={(e) =>
              setWeather({ ...weather, wind_speed: Number(e.target.value) })
            }
          />
        </label>

        <label>
          Wind Direction: <strong>{weather.wind_direction}° ({windLabel(weather.wind_direction)})</strong>
          <input
            type="range"
            min={0}
            max={359}
            value={weather.wind_direction}
            onChange={(e) =>
              setWeather({ ...weather, wind_direction: Number(e.target.value) })
            }
          />
        </label>

        <label>
          Temperature: <strong>{weather.temperature}°C</strong>
          <input
            type="range"
            min={-10}
            max={45}
            value={weather.temperature}
            onChange={(e) =>
              setWeather({ ...weather, temperature: Number(e.target.value) })
            }
          />
        </label>

        <label>
          Relative Humidity: <strong>{weather.relative_humidity}%</strong>
          <input
            type="range"
            min={5}
            max={100}
            value={weather.relative_humidity}
            onChange={(e) =>
              setWeather({
                ...weather,
                relative_humidity: Number(e.target.value),
              })
            }
          />
        </label>

        <label>
          24h Precipitation: <strong>{weather.precipitation_24h} mm</strong>
          <input
            type="range"
            min={0}
            max={50}
            step={0.5}
            value={weather.precipitation_24h}
            onChange={(e) =>
              setWeather({ ...weather, precipitation_24h: Number(e.target.value) })
            }
          />
        </label>
      </div>

      <div className="section">
        <h4>Fuel Type</h4>
        <label style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "8px" }}>
          <input
            type="checkbox"
            checked={useEdmontonGrid}
            onChange={(e) => setUseEdmontonGrid(e.target.checked)}
          />
          Use Edmonton Fuel Grid (FBP 10m)
        </label>
        {!useEdmontonGrid && (
          <>
            <select
              value={fuelType}
              onChange={(e) => setFuelType(e.target.value)}
            >
              {Object.entries(FUEL_TYPES).map(([code, name]) => (
                <option key={code} value={code}>
                  {code} — {name}
                </option>
              ))}
            </select>
            <label style={{ display: "flex", alignItems: "center", gap: "8px", marginTop: "8px" }}>
              <input
                type="checkbox"
                checked={useSyntheticCA}
                onChange={(e) => setUseSyntheticCA(e.target.checked)}
              />
              Cellular automaton (synthetic fuel mosaic)
            </label>
            {useSyntheticCA && (
              <div className="hint" style={{ fontSize: "0.85em", opacity: 0.7 }}>
                Generates a 5km mixed-fuel grid around the ignition point — shows heatmap spread
              </div>
            )}
          </>
        )}
        {useEdmontonGrid && (
          <>
            <label style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "4px", paddingLeft: "20px" }}>
              <input
                type="checkbox"
                checked={includeWater}
                onChange={(e) => setIncludeWater(e.target.checked)}
              />
              Water bodies (rivers, lakes)
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "4px", paddingLeft: "20px" }}>
              <input
                type="checkbox"
                checked={includeBuildings}
                onChange={(e) => setIncludeBuildings(e.target.checked)}
              />
              Buildings (341K footprints)
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "4px", paddingLeft: "20px" }}>
              <input
                type="checkbox"
                checked={includeWUI}
                onChange={(e) => setIncludeWUI(e.target.checked)}
              />
              WUI zone modifiers (425 zones)
            </label>
            <div className="hint" style={{ fontSize: "0.85em", opacity: 0.7 }}>
              Spatial fuel types: D2, O1a, O1b, S2, C1. Fallback: {fuelType}
            </div>
          </>
        )}
      </div>

      <div className="section">
        <h4>Duration</h4>
        <label>
          Simulation: <strong>{durationHours}h</strong>
          <input
            type="range"
            min={1}
            max={24}
            value={durationHours}
            onChange={(e) => setDurationHours(Number(e.target.value))}
          />
        </label>
        <label>
          Snapshots every: <strong>{snapshotMinutes} min</strong>
          <input
            type="range"
            min={5}
            max={60}
            step={5}
            value={snapshotMinutes}
            onChange={(e) => setSnapshotMinutes(Number(e.target.value))}
          />
        </label>
      </div>

      <button
        className="toggle-advanced"
        onClick={() => setShowAdvanced(!showAdvanced)}
      >
        {showAdvanced ? "Hide" : "Show"} FWI Overrides
      </button>

      {showAdvanced && (
        <div className="section">
          <h4>FWI Components</h4>
          <label>
            FFMC: <strong>{fwi.ffmc}</strong>
            <input
              type="range"
              min={0}
              max={101}
              value={fwi.ffmc ?? 85}
              onChange={(e) =>
                setFwi({ ...fwi, ffmc: Number(e.target.value) })
              }
            />
          </label>
          <label>
            DMC: <strong>{fwi.dmc}</strong>
            <input
              type="range"
              min={0}
              max={200}
              value={fwi.dmc ?? 40}
              onChange={(e) =>
                setFwi({ ...fwi, dmc: Number(e.target.value) })
              }
            />
          </label>
          <label>
            DC: <strong>{fwi.dc}</strong>
            <input
              type="range"
              min={0}
              max={800}
              value={fwi.dc ?? 200}
              onChange={(e) =>
                setFwi({ ...fwi, dc: Number(e.target.value) })
              }
            />
          </label>

          <button
            className="toggle-advanced"
            onClick={handleComputeFWI}
            disabled={fwiLoading}
            title="Compute today's FFMC/DMC/DC from the weather inputs above, using current sliders as yesterday's starting codes"
            style={{ marginTop: "8px" }}
          >
            {fwiLoading ? "Computing..." : "Compute FWI from Weather"}
          </button>

          {computedFWI && (
            <div style={{ marginTop: "8px", fontSize: "0.9em", lineHeight: "1.6" }}>
              <span style={{ opacity: 0.7 }}>ISI </span><strong>{computedFWI.isi.toFixed(1)}</strong>
              {"  "}
              <span style={{ opacity: 0.7 }}>BUI </span><strong>{computedFWI.bui.toFixed(0)}</strong>
              {"  "}
              <span style={{ opacity: 0.7 }}>FWI </span><strong>{computedFWI.fwi.toFixed(1)}</strong>
              {"  "}
              <span style={{
                padding: "1px 6px",
                borderRadius: "3px",
                fontSize: "0.85em",
                background: computedFWI.fwi >= 30 ? "#b71c1c" :
                            computedFWI.fwi >= 19 ? "#e65100" :
                            computedFWI.fwi >= 10 ? "#f57f17" : "#2e7d32",
                color: "#fff",
              }}>
                {computedFWI.danger_rating}
              </span>
            </div>
          )}
        </div>
      )}

      <button
        className="toggle-advanced"
        onClick={handleLoadWeather}
        disabled={!ignitionPoint || weatherLoading}
        title="Load current FWI indices from CWFIS for this location"
      >
        {weatherLoading ? "Loading..." : "Load Current Fire Weather"}
      </button>

      {weatherMessage && (
        <div
          className="hint"
          style={{
            marginBottom: "8px",
            color: weatherMessage.toLowerCase().includes("not available") ||
                   weatherMessage.toLowerCase().includes("could not")
              ? "#e57373"
              : "#81c784",
          }}
        >
          {weatherMessage}
        </div>
      )}

      <button
        className="btn-primary"
        onClick={handleSubmit}
        disabled={!ignitionPoint || isRunning}
      >
        {isRunning ? "Simulating..." : "Run Simulation"}
      </button>
    </div>
  );
}
