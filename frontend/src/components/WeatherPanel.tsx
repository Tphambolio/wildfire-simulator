/** Weather and simulation parameter controls. */

import { memo, useEffect, useId, useMemo, useRef, useState, type ClipboardEvent, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { createPortal } from "react-dom";
import type { SimulationCreate, MultiDaySimulationCreate, MultiDayWeatherParams, WeatherParams, FWIOverrides, BurnProbabilityRequest, ScenarioConfig, FuelModifiers } from "../types/simulation";
import { FUEL_TYPES } from "../types/simulation";
import { fetchCurrentWeather, calculateFWI, fetchHourlyForecast } from "../services/api";
import MultiDayPanel from "./MultiDayPanel";
import SetupSection from "./SetupSection";
import { edmontonDayOfYear, formatClock, formatDate, roundToMinute, toDateTimeInputs, toEdmontonIso, zonedWallTimeToMs, zoneAbbrev } from "../utils/time";
import { formatDecimal, parseCanadaCoordinate, parseCoordinatePair, splitPair } from "../utils/coords";
import { fwiClass, fwiClassColor, fwiClassTextColor } from "../utils/fwiClass";

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

// ── Presets (design spec §2.2) ─────────────────────────────────────────────
// Example starting points for exercises, not climatology: check them against the day's
// observations or forecast before briefing.
interface Preset {
  id: string;
  label: string;
  note: string;
  weather: WeatherParams;
  fwi: FWIOverrides;
  grassCure: number;
  percentConifer: number;
  durationHours: number;
  snapshotMinutes: number;
}

const PRESETS: Preset[] = [
  {
    id: "spring-grass",
    label: "Edmonton spring grass (cured, pre-green-up)",
    note: "Example spring values: W 20 km/h, RH 22 %, FFMC 92, DMC 25, DC 120, grass 95 % cured. Check against today's weather.",
    weather: { wind_speed: 20, wind_direction: 270, temperature: 21, relative_humidity: 22, precipitation_24h: 0 },
    fwi: { ffmc: 92, dmc: 25, dc: 120 },
    grassCure: 95,
    percentConifer: 50,
    durationHours: 4,
    snapshotMinutes: 15,
  },
  {
    id: "summer-mixedwood",
    label: "Edmonton summer mixedwood",
    note: "Example summer values: W 15 km/h, RH 30 %, FFMC 90, DMC 50, DC 350, grass 50 % cured, 50 % conifer. Check against today's weather.",
    weather: { wind_speed: 15, wind_direction: 270, temperature: 27, relative_humidity: 30, precipitation_24h: 0 },
    fwi: { ffmc: 90, dmc: 50, dc: 350 },
    grassCure: 50,
    percentConifer: 50,
    durationHours: 6,
    snapshotMinutes: 30,
  },
  {
    id: "defaults",
    label: "FireSim defaults",
    note: "The values FireSim opens with.",
    weather: { wind_speed: 20, wind_direction: 270, temperature: 25, relative_humidity: 30, precipitation_24h: 0 },
    fwi: { ffmc: 90, dmc: 45, dc: 300 },
    grassCure: 60,
    percentConifer: 50,
    durationHours: 4,
    snapshotMinutes: 30,
  },
];

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
  /** Multi-day runs: the API has no start_time, so the scenario start is passed alongside */
  onStartMultiDaySimulation?: (params: MultiDaySimulationCreate, startMs: number) => void;
  onComputeBurnProbability?: (params: BurnProbabilityRequest) => void;
  onRunParams?: (params: RunParams) => void;
  ignitionPoint: { lat: number; lng: number } | null;
  /** Set the ignition from the typed or pasted coordinates */
  onIgnitionChange?: (point: { lat: number; lng: number }) => void;
  isRunning: boolean;
  burnProbRunning?: boolean;
  /** When set, load this scenario config into the panel's local state. */
  scenarioToLoad?: ScenarioConfig | null;
  /** Called after a scenario config has been extracted for saving. */
  onConfigSnapshot?: (config: Omit<ScenarioConfig, "id" | "createdAt" | "name" | "description">) => void;
  /** Called when Edmonton fuel grid is toggled on/off, with the grid path or null */
  onEdmontonGridChange?: (fuelGridPath: string | null) => void;
  /** Element at the bottom of the Setup column that holds the sticky Run bar */
  runBarTarget?: HTMLElement | null;
}

function WeatherPanel({
  onStartSimulation,
  onStartMultiDaySimulation,
  onComputeBurnProbability,
  onRunParams,
  ignitionPoint,
  onIgnitionChange,
  isRunning,
  burnProbRunning,
  scenarioToLoad,
  onConfigSnapshot,
  onEdmontonGridChange,
  runBarTarget,
}: WeatherPanelProps) {
  const fieldId = useId();

  // ── Ignition coordinates typed or pasted (DD or DMS), synced from map clicks ──
  const [latText, setLatText] = useState(ignitionPoint ? formatDecimal(ignitionPoint.lat) : "");
  const [lngText, setLngText] = useState(ignitionPoint ? formatDecimal(ignitionPoint.lng) : "");
  const [coordErrors, setCoordErrors] = useState<{ lat?: string; lng?: string }>({});
  const [syncedPoint, setSyncedPoint] = useState(ignitionPoint);
  if (ignitionPoint !== syncedPoint) {
    // The ignition moved (map click, keyboard crosshair, scenario load): show it in the fields
    setSyncedPoint(ignitionPoint);
    setLatText(ignitionPoint ? formatDecimal(ignitionPoint.lat) : "");
    setLngText(ignitionPoint ? formatDecimal(ignitionPoint.lng) : "");
    setCoordErrors({});
  }

  const applyCoords = (latRaw: string, lngRaw: string) => {
    const lat = parseCanadaCoordinate(latRaw, "lat");
    const lng = parseCanadaCoordinate(lngRaw, "lng");
    setCoordErrors({ lat: lat.ok ? undefined : lat.error, lng: lng.ok ? undefined : lng.error });
    if (lat.ok && lng.ok) onIgnitionChange?.({ lat: lat.value, lng: lng.value });
  };

  const onCoordKey = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      applyCoords(latText, lngText);
    }
  };

  // A pasted pair ("53°27'38\"N 113°39'35\"W", "53.46, -113.66") fills both fields and applies
  const onCoordPaste = (e: ClipboardEvent<HTMLInputElement>) => {
    const text = e.clipboardData.getData("text");
    const parts = splitPair(text);
    if (!parts) return; // a single value: the field takes it as typed
    e.preventDefault();
    const pair = parseCoordinatePair(text);
    if (pair.ok) {
      setLatText(formatDecimal(pair.lat));
      setLngText(formatDecimal(pair.lng));
      applyCoords(String(pair.lat), String(pair.lng));
    } else {
      const lngFirst = /[EWew]/.test(parts[0]) && /[NSns]/.test(parts[1]);
      const [a, b] = lngFirst ? [parts[1], parts[0]] : parts;
      setLatText(a);
      setLngText(b);
      applyCoords(a, b);
    }
  };

  // ── Scenario start (design spec §2.3): wall clock in America/Edmonton ─────
  // Follows the clock (now, to the minute) until the user sets a date or time; "Now" resets.
  const [startFollowsNow, setStartFollowsNow] = useState(true);
  const [nowMs, setNowMs] = useState(() => roundToMinute());
  useEffect(() => {
    if (!startFollowsNow) return;
    const t = setInterval(() => setNowMs(roundToMinute()), 15_000);
    return () => clearInterval(t);
  }, [startFollowsNow]);
  const [startInputs, setStartInputs] = useState(() => toDateTimeInputs(nowMs));
  const shownStart = startFollowsNow ? toDateTimeInputs(nowMs) : startInputs;
  const startMs = startFollowsNow ? nowMs : zonedWallTimeToMs(startInputs.date, startInputs.time);
  const startDate = startMs !== null ? new Date(startMs) : null;
  const startError = startMs === null ? "Enter a valid start date and time" : null;
  const setStartField = (field: "date" | "time", value: string) => {
    setStartInputs({ ...shownStart, [field]: value });
    setStartFollowsNow(false);
  };
  const startNow = () => {
    setNowMs(roundToMinute());
    setStartFollowsNow(true);
  };
  /** The start used for a run: when following the clock, the minute Run is pressed. */
  const runStartMs = () => (startFollowsNow ? roundToMinute() : startMs);
  const startLabel = startDate ? `${formatDate(startDate)} ${formatClock(startDate)} ${zoneAbbrev(startDate)}` : "start time not set";

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
  const [grassCure, setGrassCure] = useState(60);
  const [percentConifer, setPercentConifer] = useState(50);
  const [useHourlyForecast, setUseHourlyForecast] = useState(false);
  const [useEdmontonGrid, setUseEdmontonGrid] = useState(true);
  const [useSyntheticCA, setUseSyntheticCA] = useState(false);
  const [enableSpotting, setEnableSpotting] = useState(false);
  const [spottingIntensity, setSpottingIntensity] = useState(1.0);
  // Off by default: the OSM water layer covers ~13,900 ha of the city incl. ~3,700 ha of
  // LiDAR-mapped vegetation (429 invalid polygons) and masks 17-21 % of forest cells; the
  // Edmonton LiDAR fuel grid already maps water as non-fuel (docs/verification.md).
  const [includeWater, setIncludeWater] = useState(false);
  const [includeBuildings, setIncludeBuildings] = useState(true);
  // Off by default: the bundled WUI multipliers have no documented source (see docs/verification.md)
  const [includeWUI, setIncludeWUI] = useState(false);
  const [includeDEM, setIncludeDEM] = useState(true);
  const [durationHours, setDurationHours] = useState(4);
  const [snapshotMinutes, setSnapshotMinutes] = useState(30);
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
        }
        setWeatherMessage(w.message);
      })
      .catch(() => {
        setWeatherMessage("Could not reach CWFIS — check network");
      })
      .finally(() => setWeatherLoading(false));
  // fwi intentionally omitted — only re-run when ignitionPoint changes
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
  const liveDanger = fwiClass(liveFWI);

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

  // FBP fuel modifiers; foliar moisture is computed server-side from the scenario start's
  // Edmonton calendar date (ST-X-3 eqs 1-8)
  const fuelModifiers = (atMs: number): FuelModifiers => ({
    grass_cure: grassCure,
    percent_conifer: percentConifer,
    day_of_year: edmontonDayOfYear(atMs),
  });

  const handleMonteCarlo = () => {
    const atMs = runStartMs();
    if (!ignitionPoint || !onComputeBurnProbability || hasErrors || atMs === null) return;
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
      fuel_modifiers: fuelModifiers(atMs),
      duration_hours: durationHours,
      n_iterations: mcIterations,
      fuel_grid_path: useEdmontonGrid ? EDMONTON_FUEL_GRID_PATH : null,
      water_path: useEdmontonGrid && includeWater ? EDMONTON_WATER_PATH : null,
      buildings_path: useEdmontonGrid && includeBuildings ? EDMONTON_BUILDINGS_PATH : null,
      dem_path: useEdmontonGrid && includeDEM ? EDMONTON_DEM_PATH : null,
    });
  };

  const handleSubmit = async () => {
    const atMs = runStartMs();
    if (!ignitionPoint || hasErrors || atMs === null) return;
    let hourly = null;
    if (useHourlyForecast) {
      try {
        // Forecast hours aligned to the scenario start, not to now (design spec §2.3)
        hourly = await fetchHourlyForecast(ignitionPoint.lat, ignitionPoint.lng, durationHours, atMs);
        const at = new Date(atMs);
        setWeatherMessage(`Hourly forecast: ${hourly.length} h from Open-Meteo, from ${formatClock(at)} ${zoneAbbrev(at)}`);
      } catch (err) {
        setWeatherMessage(`Hourly forecast unavailable (${(err as Error).message}); using constant weather`);
      }
    }
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
      start_time: toEdmontonIso(atMs),
      fwi_overrides: fwi,
      fuel_modifiers: fuelModifiers(atMs),
      hourly_weather: hourly,
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
    const atMs = runStartMs();
    if (!ignitionPoint || !onStartMultiDaySimulation || atMs === null) return;
    onStartMultiDaySimulation({
      ignition_lat: ignitionPoint.lat,
      ignition_lng: ignitionPoint.lng,
      days: multiDayDays,
      fwi_overrides: fwi,
      fuel_modifiers: fuelModifiers(atMs),
      month: Number(toDateTimeInputs(atMs).date.slice(5, 7)),
      snapshot_interval_minutes: snapshotMinutes,
      fuel_type: fuelType,
      fuel_grid_path: useEdmontonGrid ? EDMONTON_FUEL_GRID_PATH : null,
      water_path: useEdmontonGrid && includeWater ? EDMONTON_WATER_PATH : null,
      buildings_path: useEdmontonGrid && includeBuildings ? EDMONTON_BUILDINGS_PATH : null,
      dem_path: useEdmontonGrid && includeDEM ? EDMONTON_DEM_PATH : null,
    }, atMs);
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

  // ── Presets (design spec §2.2): one step sets every input ─────────────────
  const [presetId, setPresetId] = useState("");
  const applyPreset = (id: string) => {
    setPresetId(id);
    const p = PRESETS.find((x) => x.id === id);
    if (!p) return;
    setWeather(p.weather);
    setFwi(p.fwi);
    setGrassCure(p.grassCure);
    setPercentConifer(p.percentConifer);
    setDurationHours(p.durationHours);
    setSnapshotMinutes(p.snapshotMinutes);
    setSimMode("single");
  };

  // ── Run bar: why the Run button is disabled ───────────────────────────────
  const errorList = Object.values(validationErrors);
  const disabledReason = isRunning
    ? "A run is in progress; see the Situation panel."
    : !ignitionPoint
      ? "Set an ignition point: click the map or enter coordinates."
      : startError
        ? `Fix the start time: ${startError}`
        : simMode === "single" && hasErrors
          ? `Fix the inputs: ${errorList.join("; ")}`
          : null;
  const canRun = disabledReason === null && (simMode === "single" || !!onStartMultiDaySimulation);

  // Ctrl+Enter runs from anywhere (spec §2.2)
  const runRef = useRef<() => void>(() => {});
  useEffect(() => {
    runRef.current = () => {
      if (!canRun) return;
      if (simMode === "single") void handleSubmit();
      else handleMultiDaySubmit();
    };
  });
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        runRef.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const weatherErrors = ["wind_speed", "wind_direction", "temperature", "relative_humidity", "precipitation_24h", "ffmc", "dmc", "dc"]
    .some((k) => k in validationErrors);

  const runBar = (
    <div className="run-bar-inner">
      {simMode === "single" ? (
        <button
          className="btn-primary run-button"
          onClick={() => void handleSubmit()}
          disabled={!canRun}
          aria-describedby="run-bar-reason"
          aria-keyshortcuts="Control+Enter"
        >
          {isRunning ? "Simulating..." : "Run Simulation"}
        </button>
      ) : (
        <button
          className="btn-primary run-button"
          onClick={handleMultiDaySubmit}
          disabled={!canRun}
          aria-describedby="run-bar-reason"
          aria-keyshortcuts="Control+Enter"
        >
          {isRunning ? "Simulating..." : `Run ${multiDayDays.length * 24}h Scenario`}
        </button>
      )}
      <div id="run-bar-reason" className={`run-bar-reason${disabledReason && !isRunning ? " attention" : ""}`}>
        {disabledReason ?? `${simMode === "single" ? `${durationHours} h` : `${multiDayDays.length} days`} from ${startFollowsNow ? "now" : startDate ? `${formatClock(startDate)} ${zoneAbbrev(startDate)}` : "the start"} · Ctrl+Enter`}
      </div>
    </div>
  );

  return (
    <div className="weather-panel">
      <div className="setup-preset">
        <label className="setup-preset-label">
          Start from a preset
          <select value={presetId} onChange={(e) => applyPreset(e.target.value)}>
            <option value="">Keep current inputs</option>
            {PRESETS.map((p) => (
              <option key={p.id} value={p.id}>{p.label}</option>
            ))}
          </select>
        </label>
        {presetId && (
          <div className="hint-sm">{PRESETS.find((p) => p.id === presetId)?.note}</div>
        )}
      </div>

      {/* 1 ── Ignition & time ───────────────────────────────────────────── */}
      <SetupSection
        num={1}
        title="Ignition & time"
        summary={
          ignitionPoint
            ? `${formatDecimal(ignitionPoint.lat)}, ${formatDecimal(ignitionPoint.lng)} · ${startLabel}`
            : `Not set · click the map or enter coordinates · ${startLabel}`
        }
        attention={!ignitionPoint || !!startError}
        defaultOpen
      >
        <fieldset className="field-group">
        <legend>Ignition point</legend>
        <div className="field-row">
          <label className="field">
            Latitude
            <input
              type="text"
              inputMode="decimal"
              autoComplete="off"
              spellCheck={false}
              placeholder="53.4606"
              value={latText}
              onChange={(e) => setLatText(e.target.value)}
              onKeyDown={onCoordKey}
              onPaste={onCoordPaste}
              aria-invalid={!!coordErrors.lat}
              aria-describedby={`${fieldId}-coord-hint${coordErrors.lat ? ` ${fieldId}-lat-err` : ""}`}
            />
          </label>
          <label className="field">
            Longitude
            <input
              type="text"
              inputMode="decimal"
              autoComplete="off"
              spellCheck={false}
              placeholder="-113.6597"
              value={lngText}
              onChange={(e) => setLngText(e.target.value)}
              onKeyDown={onCoordKey}
              onPaste={onCoordPaste}
              aria-invalid={!!coordErrors.lng}
              aria-describedby={`${fieldId}-coord-hint${coordErrors.lng ? ` ${fieldId}-lng-err` : ""}`}
            />
          </label>
        </div>
        {coordErrors.lat && <div className="input-error" id={`${fieldId}-lat-err`} role="alert">{coordErrors.lat}</div>}
        {coordErrors.lng && <div className="input-error" id={`${fieldId}-lng-err`} role="alert">{coordErrors.lng}</div>}
        <div className="field-row field-row-nowrap">
          <button type="button" className="btn-secondary btn-inline" onClick={() => applyCoords(latText, lngText)}>
            Set ignition
          </button>
          <span className="hint-sm" id={`${fieldId}-coord-hint`}>
            Decimal (53.4606, -113.6597) or DMS (53°27&apos;38&quot;N 113°39&apos;35&quot;W); paste a pair into either field.
          </span>
        </div>
        <div className="hint-sm">
          Or click the map; or Tab to the map, move the crosshair with the arrow keys (Shift: larger
          steps) and press Enter. Ctrl+Enter runs.
        </div>
        </fieldset>

        <fieldset className="field-group">
        <legend>Scenario start <span className="legend-sub">America/Edmonton</span></legend>
        <div className="field-row field-row-nowrap">
          <label className="field field-date">
            Date
            <input
              type="date"
              value={shownStart.date}
              onChange={(e) => setStartField("date", e.target.value)}
              aria-invalid={!!startError}
              aria-describedby={`${fieldId}-start-hint${startError ? ` ${fieldId}-start-err` : ""}`}
            />
          </label>
          <label className="field field-time">
            Time ({startDate ? zoneAbbrev(startDate) : "local"})
            <input
              type="time"
              value={shownStart.time}
              onChange={(e) => setStartField("time", e.target.value)}
              aria-invalid={!!startError}
              aria-describedby={`${fieldId}-start-hint${startError ? ` ${fieldId}-start-err` : ""}`}
            />
          </label>
          <button
            type="button"
            className="btn-secondary btn-inline"
            onClick={startNow}
            aria-pressed={startFollowsNow}
            title="Start at the current time (follows the clock until you change the date or time)"
          >
            Now
          </button>
        </div>
        {startError && <div className="input-error" id={`${fieldId}-start-err`} role="alert">{startError}</div>}
        <div className="hint-sm" id={`${fieldId}-start-hint`}>
          Ignition time; the timeline and Situation times count from it.{" "}
          {startFollowsNow ? "Now follows the clock until you set a date or time." : ""}
          {useHourlyForecast && simMode === "single" && startMs !== null && startMs < nowMs - 3_600_000 && (
            <> The start is in the past, so the hourly forecast is used as a hindcast (Open-Meteo keeps one past day).</>
          )}
        </div>
        </fieldset>
      </SetupSection>

      {/* 2 ── Weather & FWI ─────────────────────────────────────────────── */}
      <SetupSection
        num={2}
        title="Weather & FWI"
        summary={
          weatherErrors
            ? "Check the inputs"
            : simMode === "multiday"
              ? `Multi-day weather · FFMC ${fwi.ffmc} DMC ${fwi.dmc} DC ${fwi.dc}`
              : `${windLabel(weather.wind_direction)} ${weather.wind_speed} km/h · RH ${weather.relative_humidity}% · FWI ${liveFWI.toFixed(1)} ${liveDanger}${useHourlyForecast ? " · hourly forecast" : ""}`
        }
        attention={weatherErrors}
      >
        {simMode === "single" && (
          <div className="section">
            <label>
              Wind Speed: <strong>{weather.wind_speed} km/h</strong>
              <input
                type="range"
                min={0}
                max={100}
                value={weather.wind_speed}
                onChange={(e) => setWeather({ ...weather, wind_speed: Number(e.target.value) })}
              />
            </label>
            {validationErrors.wind_speed && <div className="input-error">{validationErrors.wind_speed}</div>}

            <label>
              Wind Direction: <strong>{weather.wind_direction}° ({windLabel(weather.wind_direction)})</strong>
              <input
                type="range"
                min={0}
                max={359}
                value={weather.wind_direction}
                onChange={(e) => setWeather({ ...weather, wind_direction: Number(e.target.value) })}
              />
            </label>

            <label>
              Temperature: <strong>{weather.temperature}°C</strong>
              <input
                type="range"
                min={-10}
                max={45}
                value={weather.temperature}
                onChange={(e) => setWeather({ ...weather, temperature: Number(e.target.value) })}
              />
            </label>
            {validationErrors.temperature && <div className="input-error">{validationErrors.temperature}</div>}

            <label>
              Relative Humidity: <strong>{weather.relative_humidity}%</strong>
              <input
                type="range"
                min={5}
                max={100}
                value={weather.relative_humidity}
                onChange={(e) => setWeather({ ...weather, relative_humidity: Number(e.target.value) })}
              />
            </label>
            {validationErrors.relative_humidity && <div className="input-error">{validationErrors.relative_humidity}</div>}

            <label>
              24h Precipitation: <strong>{weather.precipitation_24h} mm</strong>
              <input
                type="range"
                min={0}
                max={50}
                step={0.5}
                value={weather.precipitation_24h}
                onChange={(e) => setWeather({ ...weather, precipitation_24h: Number(e.target.value) })}
              />
            </label>

            <label
              title="Wind, temperature, RH and rain change hour by hour (Open-Meteo forecast for the ignition point); FFMC follows the hourly FFMC model from the FFMC above."
            >
              <input
                type="checkbox"
                checked={useHourlyForecast}
                onChange={(e) => setUseHourlyForecast(e.target.checked)}
              />
              Use hourly forecast weather
            </label>
          </div>
        )}
        {simMode === "multiday" && (
          <div className="hint-sm">Daily weather is entered per day under 4 Run options.</div>
        )}

        <div className="section">
          <h4>FWI fuel moisture codes</h4>
          <label>
            FFMC: <strong>{fwi.ffmc}</strong>
            <input
              type="range"
              min={0}
              max={101}
              value={fwi.ffmc ?? 85}
              onChange={(e) => setFwi({ ...fwi, ffmc: Number(e.target.value) })}
            />
          </label>
          {validationErrors.ffmc && <div className="input-error">{validationErrors.ffmc}</div>}

          <label>
            DMC: <strong>{fwi.dmc}</strong>
            <input
              type="range"
              min={0}
              max={999}
              value={fwi.dmc ?? 40}
              onChange={(e) => setFwi({ ...fwi, dmc: Number(e.target.value) })}
            />
          </label>
          {validationErrors.dmc && <div className="input-error">{validationErrors.dmc}</div>}

          <label>
            DC: <strong>{fwi.dc}</strong>
            <input
              type="range"
              min={0}
              max={1000}
              value={fwi.dc ?? 200}
              onChange={(e) => setFwi({ ...fwi, dc: Number(e.target.value) })}
            />
          </label>
          {validationErrors.dc && <div className="input-error">{validationErrors.dc}</div>}
        </div>

        {/* Live FWI indices for these inputs */}
        <div className="fwi-live-row" aria-label="FWI for these inputs">
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
            style={{ background: fwiClassColor(liveFWI), color: fwiClassTextColor(liveFWI) }}
            title="CWFIS FWI map class (not an official fire danger rating)"
          >
            {liveDanger}
          </span>
        </div>
        {liveBUI < 80 && (
          <div className="hint-sm" role="note">
            BUI {liveBUI.toFixed(0)} is below 80: green aspen (D-2) does not carry fire in the FBP
            System (Alexander 2010). Raise DMC/DC for a drier scenario, or use D-1 (leafless) for
            spring.
          </div>
        )}

        <div className="setup-links">
          <button
            className="toggle-advanced"
            onClick={handleComputeFWI}
            disabled={fwiLoading}
            title="Update FFMC/DMC/DC from today's weather inputs"
          >
            {fwiLoading ? "Computing..." : "Update codes from weather"}
          </button>
          <button
            className="toggle-advanced"
            onClick={handleLoadWeather}
            disabled={!ignitionPoint || weatherLoading}
            title="Load current FWI indices from CWFIS for this location"
          >
            {weatherLoading ? "Loading..." : "Load current fire weather"}
          </button>
          <a
            href="https://tphambolio.github.io/FWI/"
            target="_blank"
            rel="noopener noreferrer"
            className="hint-link"
          >
            Fire weather detail →
          </a>
        </div>

        {weatherMessage && (
          <div
            className={`hint-sm weather-message ${
              weatherMessage.toLowerCase().includes("not available") ||
              weatherMessage.toLowerCase().includes("could not")
                ? "text-danger"
                : "text-success"
            }`}
          >
            {weatherMessage}
          </div>
        )}

        {(weatherSource || stationName) && (
          <div className="hint-sm weather-message">
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
      </SetupSection>

      {/* 3 ── Fuel & landscape ──────────────────────────────────────────── */}
      <SetupSection
        num={3}
        title="Fuel & landscape"
        summary={
          useEdmontonGrid
            ? `Edmonton grid (FBP 10 m) · curing ${grassCure}%${enableSpotting ? " · spotting on" : ""}`
            : `${fuelType} uniform${useSyntheticCA ? " · synthetic mosaic" : ""} · curing ${grassCure}%`
        }
      >
        <label>
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
        <label>
          Grass curing (%)
          <input
            type="number"
            min={0}
            max={100}
            step={5}
            value={grassCure}
            onChange={(e) => setGrassCure(Math.min(100, Math.max(0, Number(e.target.value))))}
            title="Degree of curing for O-1a/O-1b grass (Wotton et al. 2009). 100 = fully cured."
          />
        </label>
        <label>
          Percent conifer (M-1/M-2)
          <input
            type="number"
            min={0}
            max={100}
            step={5}
            value={percentConifer}
            onChange={(e) => setPercentConifer(Math.min(100, Math.max(0, Number(e.target.value))))}
          />
        </label>
        {!useEdmontonGrid && (
          <>
            <label>
              Fuel type
              <select value={fuelType} onChange={(e) => setFuelType(e.target.value)}>
                {Object.entries(FUEL_TYPES).map(([code, name]) => (
                  <option key={code} value={code}>
                    {code} — {name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <input
                type="checkbox"
                checked={useSyntheticCA}
                onChange={(e) => setUseSyntheticCA(e.target.checked)}
              />
              Cellular automaton (synthetic fuel mosaic)
            </label>
            {useSyntheticCA && (
              <div className="hint-sm">
                Generates a 5km mixed-fuel grid around the ignition point — shows heatmap spread
              </div>
            )}
          </>
        )}
        {(useEdmontonGrid || useSyntheticCA) && (
          <>
            <label>
              <input
                type="checkbox"
                checked={enableSpotting}
                onChange={(e) => setEnableSpotting(e.target.checked)}
              />
              Ember spotting (Albini 1979)
            </label>
            {enableSpotting && (
              <div className="check-row-indent">
                <label>
                  Intensity: <strong>{spottingIntensity.toFixed(1)}×</strong>
                  <input
                    type="range"
                    min={0.1}
                    max={3.0}
                    step={0.1}
                    value={spottingIntensity}
                    onChange={(e) => setSpottingIntensity(Number(e.target.value))}
                  />
                </label>
                <div className="hint-sm">
                  Crown fires loft embers downwind — seeds secondary ignitions
                </div>
              </div>
            )}
          </>
        )}
        {useEdmontonGrid && (
          <>
            <label className="check-row-indent">
              <input
                type="checkbox"
                checked={includeWater}
                onChange={(e) => setIncludeWater(e.target.checked)}
              />
              Extra water mask (OpenStreetMap; known to cover land, off by default)
            </label>
            <label className="check-row-indent">
              <input
                type="checkbox"
                checked={includeBuildings}
                onChange={(e) => setIncludeBuildings(e.target.checked)}
              />
              Buildings (341K footprints)
            </label>
            <label
              className="check-row-indent"
              title="425 park-buffer zones with spread x0.7, intensity x1.2, embers x3.0. These values have no documented source; leave off unless testing."
            >
              <input
                type="checkbox"
                checked={includeWUI}
                onChange={(e) => setIncludeWUI(e.target.checked)}
              />
              WUI zone modifiers (unsourced test values)
            </label>
            <label className="check-row-indent">
              <input
                type="checkbox"
                checked={includeDEM}
                onChange={(e) => setIncludeDEM(e.target.checked)}
              />
              Terrain slope (DEM — FBP net effective wind)
            </label>
            <div className="hint-sm">
              Edmonton grid fuels: C-2, D-2, M-2, O-1a, O-1b. Cells without fuel data use {fuelType}.
            </div>
          </>
        )}
      </SetupSection>

      {/* 4 ── Run options ───────────────────────────────────────────────── */}
      <SetupSection
        num={4}
        title="Run options"
        summary={
          simMode === "single"
            ? `Single event · ${durationHours} h · ${snapshotMinutes} min snapshots`
            : `Multi-day · ${multiDayDays.length} days · ${snapshotMinutes} min snapshots`
        }
      >
        {onStartMultiDaySimulation && (
          <div className="sim-mode-tabs" role="group" aria-label="Run mode">
            <button
              className={`sim-mode-tab${simMode === "single" ? " active" : ""}`}
              aria-pressed={simMode === "single"}
              onClick={() => setSimMode("single")}
            >
              Single Event
            </button>
            <button
              className={`sim-mode-tab${simMode === "multiday" ? " active" : ""}`}
              aria-pressed={simMode === "multiday"}
              onClick={() => setSimMode("multiday")}
            >
              Multi-day
            </button>
          </div>
        )}
        {simMode === "single" && (
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
        )}
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
        {simMode === "multiday" && (
          <MultiDayPanel
            days={multiDayDays}
            onChange={setMultiDayDays}
            disabled={isRunning}
          />
        )}
      </SetupSection>

      {/* 5 ── Burn probability (Monte Carlo) ────────────────────────────── */}
      {onComputeBurnProbability && (
        <SetupSection
          num={5}
          title="Burn probability"
          summary={burnProbRunning ? `Running ${mcIterations} iterations…` : `Monte Carlo · ${mcIterations} iterations`}
        >
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
          <div className="range-scale hint-sm">
            <span>10 (fast)</span><span>200 (accurate)</span>
          </div>
          <button
            className="btn-secondary"
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
            <div className="hint-sm">
              Enable Edmonton Grid or Synthetic CA to use Monte Carlo.
            </div>
          )}
          {!ignitionPoint && <div className="hint-sm">Set an ignition point first.</div>}
          {hasErrors && (
            <div className="hint-sm text-danger">
              Fix input errors before running.
            </div>
          )}
        </SetupSection>
      )}

      {/* Sticky Run bar lives at the bottom of the Setup column (outside the scroll area) */}
      {runBarTarget ? createPortal(runBar, runBarTarget) : runBar}
    </div>
  );
}

// Re-render only when props change, not on every streamed frame
export default memo(WeatherPanel);
