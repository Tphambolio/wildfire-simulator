/** Weather and simulation parameter controls. */

import { useEffect, useMemo, useRef, useState } from "react";
import type { SimulationCreate, MultiDaySimulationCreate, MultiDayWeatherParams, WeatherParams, FWIOverrides, BurnProbabilityRequest, ScenarioConfig } from "../types/simulation";
import { FUEL_TYPES } from "../types/simulation";
import { fetchCurrentWeather, calculateFWI } from "../services/api";
import MultiDayPanel from "./MultiDayPanel";

// ── Client-side CFFDRS FWI computation (Forestry Canada 1992, ST-X-3) ──────────
function computeISI(ffmc: number, windSpeedKmh: number): number {
  const m = 147.27723 * (101 - ffmc) / (59.5 + ffmc);
  const fW = Math.exp(0.05039 * windSpeedKmh);
  const fF = 91.9 * Math.exp(-0.1386 * m) * (1 + Math.pow(m, 5.31) / 49300000);
  return 0.208 * fW * fF;
}

function computeBUI(dmc: number, dc: number): number {
  if (dmc + 0.4 * dc === 0) return 0;
  if (dmc <= 0.4 * dc) {
    return 0.8 * dmc * dc / (dmc + 0.4 * dc);
  }
  return dmc - (1 - 0.8 * dc / (dmc + 0.4 * dc)) * (0.92 + Math.pow(0.0114 * dmc, 1.7));
}

function computeFWI(isi: number, bui: number): number {
  const fD = bui <= 80
    ? 0.626 * Math.pow(bui, 0.809) + 2
    : 1000 / (25 + 108.64 * Math.exp(-0.023 * bui));
  const B = 0.1 * isi * fD;
  return B > 1
    ? Math.exp(2.72 * Math.pow(0.434 * Math.log(B), 0.647))
    : B;
}

function dangerRating(fwi: number): string {
  if (fwi < 5) return "Low";
  if (fwi < 10) return "Moderate";
  if (fwi < 20) return "High";
  if (fwi < 30) return "Very High";
  return "Extreme";
}

// ── Validation ───────────────────────────────────────────────────────────────
interface ValidationErrors {
  wind_speed?: string;
  wind_direction?: string;
  temperature?: string;
  relative_humidity?: string;
  precipitation_24h?: string;
  ffmc?: string;
  dmc?: string;
  dc?: string;
}

function validateInputs(weather: WeatherParams, fwi: FWIOverrides): ValidationErrors {
  const errors: ValidationErrors = {};
  if (weather.wind_speed < 0 || weather.wind_speed > 100)
    errors.wind_speed = "Must be 0–100 km/h";
  if (weather.wind_direction < 0 || weather.wind_direction > 360)
    errors.wind_direction = "Must be 0–360°";
  if (weather.temperature < -40 || weather.temperature > 50)
    errors.temperature = "Must be −40 to 50°C";
  if (weather.relative_humidity < 1 || weather.relative_humidity > 100)
    errors.relative_humidity = "Must be 1–100%";
  if (weather.precipitation_24h < 0 || weather.precipitation_24h > 300)
    errors.precipitation_24h = "Must be 0–300 mm";
  if (fwi.ffmc !== null && (fwi.ffmc < 0 || fwi.ffmc > 101))
    errors.ffmc = "Must be 0–101";
  if (fwi.dmc !== null && (fwi.dmc < 0 || fwi.dmc > 999))
    errors.dmc = "Must be 0–999";
  if (fwi.dc !== null && (fwi.dc < 0 || fwi.dc > 1000))
    errors.dc = "Must be 0–1000";
  return errors;
}

// ── Props ─────────────────────────────────────────────────────────────────────
export interface RunParams {
  weather: WeatherParams;
  fwi: FWIOverrides;
  isi: number;
  bui: number;
  fwi_value: number;
  danger_rating: string;
  n_iterations: number;
  duration_hours: number;
  fuel_type?: string;
}

interface WeatherPanelProps {
  onStartSimulation: (params: SimulationCreate) => void;
  onStartMultiDaySimulation?: (params: MultiDaySimulationCreate) => void;
  onComputeBurnProbability?: (params: BurnProbabilityRequest) => void;
  onRunParams?: (params: RunParams) => void;
  ignitionPoint: { lat: number; lng: number } | null;
  isRunning: boolean;
  burnProbRunning?: boolean;
  /** When set, load this scenario config into the panel's local state. */
  scenarioToLoad?: ScenarioConfig | null;
  /** Called after a scenario config has been extracted for saving. */
  onConfigSnapshot?: (config: Omit<ScenarioConfig, "id" | "createdAt" | "name" | "description">) => void;
  /** Called when Edmonton fuel grid is toggled on/off, with the grid path or null */
  onEdmontonGridChange?: (fuelGridPath: string | null) => void;
}

export default function WeatherPanel({
  onStartSimulation,
  onStartMultiDaySimulation,
  onComputeBurnProbability,
  onRunParams,
  ignitionPoint,
  isRunning,
  burnProbRunning,
  scenarioToLoad,
  onConfigSnapshot,
  onEdmontonGridChange,
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
  const [useEdmontonGrid, setUseEdmontonGrid] = useState(true);
  const [useSyntheticCA, setUseSyntheticCA] = useState(false);
  const [enableSpotting, setEnableSpotting] = useState(false);
  const [spottingIntensity, setSpottingIntensity] = useState(1.0);
  const [includeWater, setIncludeWater] = useState(true);
  const [includeBuildings, setIncludeBuildings] = useState(true);
  const [includeWUI, setIncludeWUI] = useState(true);
  const [includeDEM, setIncludeDEM] = useState(true);
  const [durationHours, setDurationHours] = useState(4);
  const [snapshotMinutes, setSnapshotMinutes] = useState(30);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [weatherLoading, setWeatherLoading] = useState(false);
  const [weatherMessage, setWeatherMessage] = useState<string | null>(null);
  const [fwiLoading, setFwiLoading] = useState(false);
  const [mcIterations, setMcIterations] = useState(50);
  const [weatherSource, setWeatherSource] = useState<string | null>(null);
  const [weatherTimestamp, setWeatherTimestamp] = useState<string | null>(null);
  const [stationName, setStationName] = useState<string | null>(null);
  const [stationDistanceKm, setStationDistanceKm] = useState<number | null>(null);
  const [simMode, setSimMode] = useState<"single" | "multiday">("single");
  const [multiDayDays, setMultiDayDays] = useState<MultiDayWeatherParams[]>([
    { wind_speed: 20, wind_direction: 270, temperature: 25, relative_humidity: 30, precipitation_24h: 0 },
    { wind_speed: 25, wind_direction: 270, temperature: 28, relative_humidity: 25, precipitation_24h: 0 },
    { wind_speed: 30, wind_direction: 260, temperature: 30, relative_humidity: 20, precipitation_24h: 0 },
  ]);

  // ── Apply scenario config when requested ─────────────────────────────────
  const loadedScenarioIdRef = useRef<string | null>(null);
  useEffect(() => {
    if (!scenarioToLoad || scenarioToLoad.id === loadedScenarioIdRef.current) return;
    loadedScenarioIdRef.current = scenarioToLoad.id;
    setWeather(scenarioToLoad.weather);
    setFwi(scenarioToLoad.fwi);
    setFuelType(scenarioToLoad.fuelType);
    setUseEdmontonGrid(scenarioToLoad.useEdmontonGrid);
    setUseSyntheticCA(scenarioToLoad.useSyntheticCA);
    setEnableSpotting(scenarioToLoad.enableSpotting);
    setSpottingIntensity(scenarioToLoad.spottingIntensity);
    setIncludeWater(scenarioToLoad.includeWater);
    setIncludeBuildings(scenarioToLoad.includeBuildings);
    setIncludeWUI(scenarioToLoad.includeWUI);
    setIncludeDEM(scenarioToLoad.includeDEM);
    setDurationHours(scenarioToLoad.durationHours);
    setSnapshotMinutes(scenarioToLoad.snapshotMinutes);
    setSimMode(scenarioToLoad.simMode);
    setMultiDayDays(scenarioToLoad.multiDayDays);
    setMcIterations(scenarioToLoad.mcIterations);
  }, [scenarioToLoad]);

  // ── Provide config snapshot to parent for saving ──────────────────────────
  useEffect(() => {
    onConfigSnapshot?.({
      ignitionPoint,
      weather,
      fwi,
      fuelType,
      useEdmontonGrid,
      useSyntheticCA,
      enableSpotting,
      spottingIntensity,
      includeWater,
      includeBuildings,
      includeWUI,
      includeDEM,
      durationHours,
      snapshotMinutes,
      simMode,
      multiDayDays,
      mcIterations,
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    ignitionPoint, weather, fwi, fuelType, useEdmontonGrid, useSyntheticCA,
    enableSpotting, spottingIntensity, includeWater, includeBuildings, includeWUI,
    includeDEM, durationHours, snapshotMinutes, simMode, multiDayDays, mcIterations,
  ]);

  // ── Auto-fetch CWFIS weather when ignition point is first set ─────────────
  const autoFetchedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!ignitionPoint) return;
    const key = `${ignitionPoint.lat.toFixed(4)},${ignitionPoint.lng.toFixed(4)}`;
    if (autoFetchedRef.current === key) return; // already fetched for this point
    autoFetchedRef.current = key;
    setWeatherLoading(true);
    setWeatherMessage(null);
    fetchCurrentWeather(ignitionPoint.lat, ignitionPoint.lng)
      .then((w) => {
        if (w.available) {
          if (w.wind_speed !== null) setWeather((prev) => ({ ...prev, wind_speed: Math.round(w.wind_speed!) }));
          if (w.wind_direction !== null) setWeather((prev) => ({ ...prev, wind_direction: Math.round(w.wind_direction!) }));
          if (w.temperature !== null) setWeather((prev) => ({ ...prev, temperature: Math.round(w.temperature!) }));
          if (w.relative_humidity !== null) setWeather((prev) => ({ ...prev, relative_humidity: Math.round(w.relative_humidity!) }));
          if (w.ffmc !== null || w.dmc !== null || w.dc !== null) {
            setFwi({
              ffmc: w.ffmc ?? fwi.ffmc,
              dmc: w.dmc ?? fwi.dmc,
              dc: w.dc ?? fwi.dc,
            });
          }
          setWeatherSource(w.source);
          setWeatherTimestamp(w.data_timestamp ?? null);
          setStationName(w.station_name ?? null);
          setStationDistanceKm(w.distance_km ?? null);
          if (!showAdvanced) setShowAdvanced(true);
        }
        setWeatherMessage(w.message);
      })
      .catch(() => {
        setWeatherMessage("Could not reach CWFIS — check network");
      })
      .finally(() => setWeatherLoading(false));
  // fwi and showAdvanced intentionally omitted — only re-run when ignitionPoint changes
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ignitionPoint]);

  // ── Live ISI / BUI / FWI (reactive to slider changes) ────────────────────
  const liveISI = useMemo(
    () => computeISI(fwi.ffmc ?? 85, weather.wind_speed),
    [fwi.ffmc, weather.wind_speed]
  );
  const liveBUI = useMemo(
    () => computeBUI(fwi.dmc ?? 6, fwi.dc ?? 15),
    [fwi.dmc, fwi.dc]
  );
  const liveFWI = useMemo(
    () => computeFWI(liveISI, liveBUI),
    [liveISI, liveBUI]
  );
  const liveDanger = dangerRating(liveFWI);

  // ── Validation ────────────────────────────────────────────────────────────
  const validationErrors = useMemo(() => validateInputs(weather, fwi), [weather, fwi]);
  const hasErrors = Object.keys(validationErrors).length > 0;

  const EDMONTON_FUEL_GRID_PATH =
    import.meta.env.VITE_EDMONTON_FUEL_GRID_PATH ??
    "/app/data/Edmonton_FBP_FuelLayer_20251105_10m.tif";
  const EDMONTON_WATER_PATH =
    import.meta.env.VITE_EDMONTON_WATER_PATH ??
    "/app/data/edmonton_water_bodies.geojson.gz";
  const EDMONTON_BUILDINGS_PATH =
    import.meta.env.VITE_EDMONTON_BUILDINGS_PATH ??
    "/app/data/edmonton_buildings.geojson.gz";
  const EDMONTON_WUI_PATH =
    import.meta.env.VITE_EDMONTON_WUI_PATH ??
    "/app/data/wui_zones.geojson.gz";
  const EDMONTON_DEM_PATH =
    import.meta.env.VITE_EDMONTON_DEM_PATH ??
    "/app/data/edmonton_dem.tif";

  // Notify parent of the default fuel grid on mount
  useEffect(() => {
    onEdmontonGridChange?.(EDMONTON_FUEL_GRID_PATH);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleMonteCarlo = () => {
    if (!ignitionPoint || !onComputeBurnProbability || hasErrors) return;
    onRunParams?.({
      weather,
      fwi,
      isi: liveISI,
      bui: liveBUI,
      fwi_value: liveFWI,
      danger_rating: liveDanger,
      n_iterations: mcIterations,
      duration_hours: durationHours,
      fuel_type: fuelType,
    });
    onComputeBurnProbability({
      ignition_lat: ignitionPoint.lat,
      ignition_lng: ignitionPoint.lng,
      weather,
      fwi_overrides: fwi,
      duration_hours: durationHours,
      n_iterations: mcIterations,
      fuel_grid_path: useEdmontonGrid ? EDMONTON_FUEL_GRID_PATH : null,
      water_path: useEdmontonGrid && includeWater ? EDMONTON_WATER_PATH : null,
      buildings_path: useEdmontonGrid && includeBuildings ? EDMONTON_BUILDINGS_PATH : null,
      dem_path: useEdmontonGrid && includeDEM ? EDMONTON_DEM_PATH : null,
    });
  };

  const handleSubmit = () => {
    if (!ignitionPoint || hasErrors) return;
    onRunParams?.({
      weather,
      fwi,
      isi: liveISI,
      bui: liveBUI,
      fwi_value: liveFWI,
      danger_rating: liveDanger,
      n_iterations: 1,
      duration_hours: durationHours,
      fuel_type: fuelType,
    });
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
      dem_path: useEdmontonGrid && includeDEM ? EDMONTON_DEM_PATH : null,
      use_ca_mode: useSyntheticCA && !useEdmontonGrid,
      enable_spotting: enableSpotting,
      spotting_intensity: spottingIntensity,
    });
  };

  const handleMultiDaySubmit = () => {
    if (!ignitionPoint || !onStartMultiDaySimulation) return;
    onStartMultiDaySimulation({
      ignition_lat: ignitionPoint.lat,
      ignition_lng: ignitionPoint.lng,
      days: multiDayDays,
      fwi_overrides: fwi,
      month: new Date().getMonth() + 1,
      snapshot_interval_minutes: snapshotMinutes,
      fuel_type: fuelType,
      fuel_grid_path: useEdmontonGrid ? EDMONTON_FUEL_GRID_PATH : null,
      water_path: useEdmontonGrid && includeWater ? EDMONTON_WATER_PATH : null,
      buildings_path: useEdmontonGrid && includeBuildings ? EDMONTON_BUILDINGS_PATH : null,
      dem_path: useEdmontonGrid && includeDEM ? EDMONTON_DEM_PATH : null,
    });
  };

  const handleLoadWeather = async () => {
    if (!ignitionPoint) return;
    setWeatherLoading(true);
    setWeatherMessage(null);
    // Force re-fetch even if already auto-fetched for this point
    autoFetchedRef.current = null;
    try {
      const w = await fetchCurrentWeather(ignitionPoint.lat, ignitionPoint.lng);
      if (w.available) {
        if (w.wind_speed !== null) setWeather((prev) => ({ ...prev, wind_speed: Math.round(w.wind_speed!) }));
        if (w.wind_direction !== null) setWeather((prev) => ({ ...prev, wind_direction: Math.round(w.wind_direction!) }));
        if (w.temperature !== null) setWeather((prev) => ({ ...prev, temperature: Math.round(w.temperature!) }));
        if (w.relative_humidity !== null) setWeather((prev) => ({ ...prev, relative_humidity: Math.round(w.relative_humidity!) }));
        if (w.ffmc !== null || w.dmc !== null || w.dc !== null) {
          setFwi({
            ffmc: w.ffmc ?? fwi.ffmc,
            dmc: w.dmc ?? fwi.dmc,
            dc: w.dc ?? fwi.dc,
          });
        }
        setWeatherSource(w.source);
        setWeatherTimestamp(w.data_timestamp ?? null);
        setStationName(w.station_name ?? null);
        setStationDistanceKm(w.distance_km ?? null);
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
      if (!showAdvanced) setShowAdvanced(true);
    } catch {
      // silently fail — live computation still shown
    } finally {
      setFwiLoading(false);
    }
  };

  // Wind direction compass label
  const windLabel = (deg: number) => {
    const dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
    return dirs[Math.round(deg / 45) % 8];
  };

  const dangerColor = (fwiVal: number) =>
    fwiVal >= 30 ? "#b71c1c" :
    fwiVal >= 20 ? "#e65100" :
    fwiVal >= 10 ? "#f57f17" :
    fwiVal >= 5  ? "#558b2f" : "#2e7d32";

  return (
    <div className="panel weather-panel">
      <h3>Simulation Parameters</h3>

      {/* Mode toggle: single-event vs multi-day */}
      {onStartMultiDaySimulation && (
        <div className="sim-mode-tabs">
          <button
            className={`sim-mode-tab${simMode === "single" ? " active" : ""}`}
            onClick={() => setSimMode("single")}
          >
            Single Event
          </button>
          <button
            className={`sim-mode-tab${simMode === "multiday" ? " active" : ""}`}
            onClick={() => setSimMode("multiday")}
          >
            Multi-day
          </button>
        </div>
      )}

      {!ignitionPoint && (
        <div className="hint">Click the map to set ignition point</div>
      )}

      {ignitionPoint && (
        <div className="ignition-info">
          Ignition: {ignitionPoint.lat.toFixed(4)}, {ignitionPoint.lng.toFixed(4)}
        </div>
      )}

      {simMode === "single" && (<>

      <div className="section">
        <h4>Weather</h4>

        <label>
          Wind Speed: <strong>{weather.wind_speed} km/h</strong>
          <input
            type="range"
            min={0}
            max={100}
            value={weather.wind_speed}
            onChange={(e) =>
              setWeather({ ...weather, wind_speed: Number(e.target.value) })
            }
          />
        </label>
        {validationErrors.wind_speed && (
          <div className="input-error">{validationErrors.wind_speed}</div>
        )}

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
        {validationErrors.temperature && (
          <div className="input-error">{validationErrors.temperature}</div>
        )}

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
        {validationErrors.relative_humidity && (
          <div className="input-error">{validationErrors.relative_humidity}</div>
        )}

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
            onChange={(e) => {
              setUseEdmontonGrid(e.target.checked);
              onEdmontonGridChange?.(e.target.checked ? EDMONTON_FUEL_GRID_PATH : null);
            }}
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
        {(useEdmontonGrid || useSyntheticCA) && (
          <>
            <label style={{ display: "flex", alignItems: "center", gap: "8px", marginTop: "8px" }}>
              <input
                type="checkbox"
                checked={enableSpotting}
                onChange={(e) => setEnableSpotting(e.target.checked)}
              />
              Ember spotting (Albini 1979)
            </label>
            {enableSpotting && (
              <div style={{ paddingLeft: "20px", marginTop: "4px" }}>
                <label style={{ fontSize: "0.9em" }}>
                  Intensity: <strong>{spottingIntensity.toFixed(1)}×</strong>
                  <input
                    type="range"
                    min={0.1}
                    max={3.0}
                    step={0.1}
                    value={spottingIntensity}
                    onChange={(e) => setSpottingIntensity(Number(e.target.value))}
                    style={{ width: "100%", marginTop: "2px" }}
                  />
                </label>
                <div className="hint" style={{ fontSize: "0.8em", opacity: 0.7 }}>
                  Crown fires loft embers downwind — seeds secondary ignitions
                </div>
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
            <label style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "4px", paddingLeft: "20px" }}>
              <input
                type="checkbox"
                checked={includeDEM}
                onChange={(e) => setIncludeDEM(e.target.checked)}
              />
              Terrain slope (DEM — ISF/RSF correction)
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
        {showAdvanced ? "Hide" : "Show"} FWI Codes
      </button>

      {showAdvanced && (
        <div className="section">
          <h4>FWI Fuel Moisture Codes</h4>
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
          {validationErrors.ffmc && (
            <div className="input-error">{validationErrors.ffmc}</div>
          )}

          <label>
            DMC: <strong>{fwi.dmc}</strong>
            <input
              type="range"
              min={0}
              max={999}
              value={fwi.dmc ?? 40}
              onChange={(e) =>
                setFwi({ ...fwi, dmc: Number(e.target.value) })
              }
            />
          </label>
          {validationErrors.dmc && (
            <div className="input-error">{validationErrors.dmc}</div>
          )}

          <label>
            DC: <strong>{fwi.dc}</strong>
            <input
              type="range"
              min={0}
              max={1000}
              value={fwi.dc ?? 200}
              onChange={(e) =>
                setFwi({ ...fwi, dc: Number(e.target.value) })
              }
            />
          </label>
          {validationErrors.dc && (
            <div className="input-error">{validationErrors.dc}</div>
          )}

          <button
            className="toggle-advanced"
            onClick={handleComputeFWI}
            disabled={fwiLoading}
            title="Update FFMC/DMC/DC from today's weather inputs"
            style={{ marginTop: "8px" }}
          >
            {fwiLoading ? "Computing..." : "Update Codes from Weather"}
          </button>
        </div>
      )}

      {/* ── Live FWI indices — always visible ─────────────────────────────── */}
      <div className="fwi-live-row">
        <span className="fwi-live-item">
          <span className="fwi-live-label">ISI</span>
          <strong>{liveISI.toFixed(1)}</strong>
        </span>
        <span className="fwi-live-item">
          <span className="fwi-live-label">BUI</span>
          <strong>{liveBUI.toFixed(0)}</strong>
        </span>
        <span className="fwi-live-item">
          <span className="fwi-live-label">FWI</span>
          <strong>{liveFWI.toFixed(1)}</strong>
        </span>
        <span
          className="fwi-danger-badge"
          style={{ background: dangerColor(liveFWI) }}
        >
          {liveDanger}
        </span>
      </div>

      <button
        className="toggle-advanced"
        onClick={handleLoadWeather}
        disabled={!ignitionPoint || weatherLoading}
        title="Load current FWI indices from CWFIS for this location"
      >
        {weatherLoading ? "Loading..." : "Load Current Fire Weather"}
      </button>

      <a
        href="https://tphambolio.github.io/FWI/"
        target="_blank"
        rel="noopener noreferrer"
        className="hint"
        style={{ display: "inline-block", color: "#64b5f6", marginBottom: "4px" }}
      >
        Fire Weather Detail →
      </a>

      {weatherMessage && (
        <div
          className="hint"
          style={{
            marginBottom: "4px",
            color: weatherMessage.toLowerCase().includes("not available") ||
                   weatherMessage.toLowerCase().includes("could not")
              ? "#e57373"
              : "#81c784",
          }}
        >
          {weatherMessage}
        </div>
      )}

      {(weatherSource || stationName) && (
        <div className="hint" style={{ marginBottom: "8px", fontSize: "0.8em", opacity: 0.6 }}>
          {stationName && (
            <span>
              {stationName}
              {stationDistanceKm !== null && <span> · {stationDistanceKm} km away</span>}
              {" · "}
            </span>
          )}
          {weatherSource && !stationName && <span>{weatherSource} · </span>}
          {weatherTimestamp ?? ""}
        </div>
      )}

      </>)}

      {/* Multi-day scenario panel */}
      {simMode === "multiday" && (
        <>
          <MultiDayPanel
            days={multiDayDays}
            onChange={setMultiDayDays}
            disabled={isRunning}
          />
          <button
            className="btn-primary"
            onClick={handleMultiDaySubmit}
            disabled={!ignitionPoint || isRunning || !onStartMultiDaySimulation}
            title={!ignitionPoint ? "Set ignition point first" : undefined}
          >
            {isRunning ? "Simulating..." : `Run ${multiDayDays.length * 24}h Scenario`}
          </button>
        </>
      )}

      {/* Single-event run button */}
      {simMode === "single" && (
        <button
          className="btn-primary"
          onClick={handleSubmit}
          disabled={!ignitionPoint || isRunning || hasErrors}
          title={hasErrors ? "Fix validation errors before running" : undefined}
        >
          {isRunning ? "Simulating..." : "Run Simulation"}
        </button>
      )}

      {onComputeBurnProbability && (
        <div style={{ marginTop: 12, borderTop: "1px solid #2a3a5a", paddingTop: 12 }}>
          <div style={{ marginBottom: 8 }}>
            <label>
              Iterations: <strong>{mcIterations}</strong>
              <input
                type="range"
                min={10}
                max={200}
                step={10}
                value={mcIterations}
                onChange={e => setMcIterations(Number(e.target.value))}
              />
            </label>
            <div style={{ fontSize: 11, color: "#667", display: "flex", justifyContent: "space-between" }}>
              <span>10 (fast)</span><span>200 (accurate)</span>
            </div>
          </div>
          <button
            className="btn-secondary"
            style={{ width: "100%", background: "#1a3060", borderColor: "#3a60a0" }}
            onClick={handleMonteCarlo}
            disabled={!ignitionPoint || burnProbRunning || isRunning || (!useEdmontonGrid && !useSyntheticCA) || hasErrors}
            title={
              hasErrors
                ? "Fix validation errors before running"
                : !useEdmontonGrid && !useSyntheticCA
                  ? "Enable Edmonton Grid or Synthetic CA to run Monte Carlo"
                  : "Apply weather conditions & run Monte Carlo burn probability"
            }
          >
            {burnProbRunning ? `Running ${mcIterations} iterations...` : "Apply & Run Burn Probability"}
          </button>
          {burnProbRunning && (
            <div className="burn-prob-progress">
              <div className="burn-prob-progress-bar" />
            </div>
          )}
          {!useEdmontonGrid && !useSyntheticCA && !hasErrors && (
            <div style={{ fontSize: 11, color: "#778", marginTop: 4 }}>
              Enable Edmonton Grid or Synthetic CA to use Monte Carlo.
            </div>
          )}
          {hasErrors && (
            <div style={{ fontSize: 11, color: "#e57373", marginTop: 4 }}>
              Fix input errors before running.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
