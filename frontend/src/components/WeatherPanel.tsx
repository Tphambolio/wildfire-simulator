/** Weather and simulation parameter controls. */

import { memo, useEffect, useId, useMemo, useRef, useState, type ClipboardEvent, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { createPortal } from "react-dom";
import type { SimulationCreate, MultiDaySimulationCreate, MultiDayWeatherParams, WeatherParams, FWIOverrides, BurnProbabilityRequest, ScenarioConfig, FuelModifiers, WeatherChange } from "../types/simulation";
import { FUEL_TYPES } from "../types/simulation";
import { fetchCurrentWeather, calculateFWI, fetchHourlyForecast, FORECAST_MODEL_LABEL } from "../services/api";
import { isPyraSource } from "../utils/weatherSource";
import MultiDayPanel from "./MultiDayPanel";
import SetupSection from "./SetupSection";
import { edmontonDayOfYear, formatClock, formatDate, roundToMinute, toDateTimeInputs, toEdmontonIso, zonedWallTimeToMs, zoneAbbrev } from "../utils/time";
import { formatDecimal, parseCanadaCoordinate, parseCoordinatePair, splitPair } from "../utils/coords";
import { fwiClass } from "../utils/fwiClass";
import {
  DEFAULT_BURNING_PERIOD,
  VALIDATION_DOC_URL,
  formatBurningPeriod,
  formatHour,
  formatWait,
  hoursUntilBurning,
  skillRequestFields,
  spinupFromMs,
  validateBurningHours,
} from "../utils/skillOptions";
import type { BurningPeriod } from "../types/simulation";
import { BADGES, TIPS } from "../content/explanations";
import Badge from "./Badge";
import InfoTip, { TipButton } from "./InfoTip";
import FwiClassChip from "./FwiClassChip";
import { SPRING_WINDOW_LABEL, curingFactor, defaultGrassCure } from "../utils/curing";
import WeatherSchedulePanel from "./WeatherSchedulePanel";
import { computeBUI, computeFWI, computeISI, fmtCode, runParamsFor } from "../utils/fwiCalc";
import { DEFAULT_FUEL_GRID_YEAR, edmontonYear, fuelVintageWarning } from "../utils/fuelVintage";
import { multiDaySummary, scheduleErrors, scheduleRequest, timedSteps, type ScheduleRow, type TimedStep } from "../utils/weatherSchedule";

/** Moving the ignition this far from where the weather was set shows a notice. */
const FAR_KM = 25;

/** Great-circle distance in km. */
function distanceKm(a: { lat: number; lng: number }, b: { lat: number; lng: number }): number {
  const r = Math.PI / 180;
  const dLat = (b.lat - a.lat) * r;
  const dLng = (b.lng - a.lng) * r;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin(dLng / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.sqrt(h));
}

/** Short visible status for a weather message; the full message goes in its tooltip. */
export function shortWeatherStatus(msg: string): { text: string; ok: boolean } {
  const m = msg.toLowerCase();
  if (m.includes("unavailable") || m.includes("not available") || m.includes("could not") || m.includes("failed")) {
    return { text: m.includes("hourly") ? "Hourly forecast unavailable" : "Weather not loaded", ok: false };
  }
  const spin = m.includes("spin-up skipped") ? " · spin-up skipped" : "";
  if (m.startsWith("hourly forecast")) return { text: `Hourly forecast loaded${spin}`, ok: true };
  return { text: `Fire weather loaded${spin}`, ok: true };
}

/** Last curing the user entered (a suggestion outside the spring window; per browser). */
const LAST_CURE_KEY = "firesim.lastGrassCure";
function readLastCure(): number | null {
  try {
    const v = localStorage.getItem(LAST_CURE_KEY);
    return v === null || !Number.isFinite(Number(v)) ? null : Number(v);
  } catch {
    return null;
  }
}

/** Scenario ids of a setup restored on reload (App; utils/setupStore.ts) start with this. */
export const SETUP_RESTORE_PREFIX = "setup-restore:";

/** Whether a scenario to load is a setup restored on reload (keeps its weather source). */
export function isSetupRestore(s: { id: string }): boolean {
  return s.id.startsWith(SETUP_RESTORE_PREFIX);
}

/** An FWI code from a station rounded to one decimal (CWFIS gives e.g. 71.7629123990164; R10). */
export function roundCode(v: number): number {
  return Math.round(v * 10) / 10;
}

/** Weather and codes an automatic station fill replaced, and where the new values came from. */
export interface ReplacedWeather {
  weather: WeatherParams;
  fwi: FWIOverrides;
  manual: boolean;
  from: string;
}

/** "W 20 km/h · RH 30 % · FFMC 90 DMC 45 DC 300": the values a notice says were replaced. */
export function weatherBrief(w: WeatherParams, f: FWIOverrides): string {
  const c = (v: number | null) => (v === null ? "—" : String(roundCode(v)));
  return `W ${w.wind_speed} km/h · RH ${w.relative_humidity} % · ${w.temperature} °C · FFMC ${c(f.ffmc)} DMC ${c(f.dmc)} DC ${c(f.dc)}`;
}

/** Setup's weather as a restart from an observed perimeter uses it (owner field test
 * 2026-10-12: restarts used the earlier run's weather) */
export interface SetupWeather {
  weather: WeatherParams;
  fwi: FWIOverrides;
  /** Weather changes at absolute times (Setup's table against Setup's start); [] = none */
  steps: TimedStep[];
  /** Hourly forecast weather is on (the restart keeps the earlier run's forecast stream) */
  forecastOn: boolean;
}

/** Setup's skill options as they apply to a restart from an observed perimeter */
export interface SkillOptionsState {
  /** Burning period to apply (null = off or invalid) */
  burningPeriod: BurningPeriod | null;
  /** Evening FFMC spin-up wanted (only effective with hourly forecast weather) */
  spinUp: boolean;
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
  /** null = the date-aware default (95 % from 1 Mar to 29 May, none outside) */
  grassCure: number | null;
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
    grassCure: null,
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
  /** Manual weather changes the run used (hours after its start); null/absent = none */
  weather_schedule?: WeatherChange[] | null;
  /** The run used hourly forecast weather */
  hourly_forecast?: boolean;
  /** Run start (ISO with offset), for clock times of the weather changes */
  start_time?: string | null;
  /** The scenario is dated before the fuel grid's year (exports, ICS 209 remarks) */
  fuel_vintage_warning?: string | null;
  /** Multi-day runs: each day's weather (`weather` is Day 1's; `fwi` the starting codes) */
  multi_day_days?: MultiDayWeatherParams[] | null;
  /** Ember spotting was on (false: not modelled; multi-day runs have none) */
  spotting?: boolean;
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
  /** Current burning period / spin-up settings (for the observed-perimeter restart) */
  onSkillOptions?: (opts: SkillOptionsState) => void;
  /** Current weather, FWI codes and weather changes (for the observed-perimeter restart) */
  onSetupWeather?: (w: SetupWeather) => void;
  /** After a run: arm a new ignition on the map ("New ignition" in the run bar) */
  onNewIgnition?: () => void;
  /** Single run pressed: called before the hourly forecast is fetched, so the previous run
   * is cleared at once (the request is sent through onStartSimulation once ready) */
  onRunRequested?: (durationHours: number) => void;
  /** Incremented by the collapsed Setup rail's Run button: run as the Run bar would */
  runRequest?: number;
  /** Whether Run is possible now, and why not (for the collapsed Setup rail) */
  onRunState?: (state: { canRun: boolean; reason: string | null; label: string }) => void;
}

/** Roof scenario shares (pre-specified, docs/structure-spread-spec.md §6.2) */
const ROOF_SHARES = [0, 0.05, 0.15, 0.3];

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
  onSkillOptions,
  onSetupWeather,
  onNewIgnition,
  onRunRequested,
  runRequest = 0,
  onRunState,
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
  // Latest weather and codes for the station fill (it runs after an await)
  const weatherRef = useRef(weather);
  weatherRef.current = weather;
  const fwiRef = useRef(fwi);
  fwiRef.current = fwi;
  const [fuelType, setFuelType] = useState("C2");
  // Grass curing as entered (null = not entered: the date-aware default applies, decision M1)
  const [grassCure, setGrassCure] = useState<number | null>(null);
  const [lastCure, setLastCure] = useState<number | null>(readLastCure);
  const [percentConifer, setPercentConifer] = useState(50);
  const [useHourlyForecast, setUseHourlyForecast] = useState(false);
  // Manual weather changes during the run (rows at local clock times; empty = none)
  const [scheduleRows, setScheduleRows] = useState<ScheduleRow[]>([]);
  const [useEdmontonGrid, setUseEdmontonGrid] = useState(true);
  const [useSyntheticCA, setUseSyntheticCA] = useState(false);
  const [enableSpotting, setEnableSpotting] = useState(false);
  const [spottingIntensity, setSpottingIntensity] = useState(1.0);
  // Off by default: the OSM water layer covers ~13,900 ha of the city incl. ~3,700 ha of
  // LiDAR-mapped vegetation (429 invalid polygons) and masks 17-21 % of forest cells; the
  // Edmonton LiDAR fuel grid already maps water as non-fuel (docs/verification.md).
  const [includeWater, setIncludeWater] = useState(false);
  const [includeBuildings, setIncludeBuildings] = useState(true);
  // Opt-in, off by default: Hamada house-to-house spread (illustrative; needs the buildings)
  const [structureSpread, setStructureSpread] = useState(false);
  // Opt-in under house-to-house spread: ember ignition of buildings (illustrative)
  const [structureEmbers, setStructureEmbers] = useState(false);
  // Opt-in under house-to-house spread (structure step 3): vegetation-bridged cutoff, and the
  // combustible-roof scenario (needs embers; a scenario, not observed roofs)
  const [structureVegBridge, setStructureVegBridge] = useState(false);
  const [structureRoofShare, setStructureRoofShare] = useState(0);
  const structureAvailable = useEdmontonGrid && includeBuildings;
  // Off by default: the bundled WUI multipliers have no documented source (see docs/verification.md)
  const [includeWUI, setIncludeWUI] = useState(false);
  const [includeDEM, setIncludeDEM] = useState(true);
  const [durationHours, setDurationHours] = useState(4);
  const [snapshotMinutes, setSnapshotMinutes] = useState(30);
  const [weatherLoading, setWeatherLoading] = useState(false);
  const [weatherMessage, setWeatherMessage] = useState<string | null>(null);
  const [fwiLoading, setFwiLoading] = useState(false);
  const [mcIterations, setMcIterations] = useState(50);
  // Range of outcomes (ensemble) after grid runs: on by default, 30 members
  const [ensembleOn, setEnsembleOn] = useState(true);
  const [ensembleMembers, setEnsembleMembers] = useState(30);
  // Spread-skill options (docs/validation.md): on by default in the UI (the API defaults off)
  const [burningOn, setBurningOn] = useState(true);
  const [bpStart, setBpStart] = useState(DEFAULT_BURNING_PERIOD.start_hour);
  const [bpEnd, setBpEnd] = useState(DEFAULT_BURNING_PERIOD.end_hour);
  const [spinUpOn, setSpinUpOn] = useState(true);
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

  // Weather source state (used by the scenario restore below; see "Station weather")
  const [weatherManual, setWeatherManual] = useState(false);
  const weatherManualRef = useRef(false);
  // Where the current weather applies (the point it was loaded for), for the distance notice
  const weatherAnchorRef = useRef<{ lat: number; lng: number } | null>(null);
  // The ignition (rounded key) the station weather was last handled for
  const autoFetchedRef = useRef<string | null>(null);
  // Values an automatic station fill replaced, shown with Undo until acknowledged (R9)
  const [replaced, setReplaced] = useState<ReplacedWeather | null>(null);
  const startIsoSnap = startFollowsNow || startMs === null ? null : toEdmontonIso(startMs);

  // ── Apply scenario config when requested ─────────────────────────────────
  const loadedScenarioIdRef = useRef<string | null>(null);
  useEffect(() => {
    if (!scenarioToLoad || scenarioToLoad.id === loadedScenarioIdRef.current) return;
    loadedScenarioIdRef.current = scenarioToLoad.id;
    setWeather(scenarioToLoad.weather);
    setFwi(scenarioToLoad.fwi);
    setReplaced(null);
    const origin = isSetupRestore(scenarioToLoad) ? scenarioToLoad.weatherOrigin : undefined;
    if (origin) {
      // A reload restoring this incident's setup: the weather keeps its source and is not
      // fetched again for the restored ignition (R8, R9)
      weatherManualRef.current = origin.manual;
      setWeatherManual(origin.manual);
      setWeatherSource(origin.source);
      setStationName(origin.station);
      setStationDistanceKm(origin.distanceKm);
      setWeatherTimestamp(origin.timestamp);
      const ip = scenarioToLoad.ignitionPoint;
      if (ip) autoFetchedRef.current = `${ip.lat.toFixed(4)},${ip.lng.toFixed(4)}`;
    } else {
      weatherManualRef.current = true;
      setWeatherManual(true);
    }
    weatherAnchorRef.current = scenarioToLoad.ignitionPoint ?? null;
    if (scenarioToLoad.startIso !== undefined) {
      const at = scenarioToLoad.startIso ? Date.parse(scenarioToLoad.startIso) : NaN;
      if (Number.isFinite(at)) {
        setStartInputs(toDateTimeInputs(at));
        setStartFollowsNow(false);
      } else {
        setNowMs(roundToMinute());
        setStartFollowsNow(true);
      }
    }
    if (scenarioToLoad.grassCure !== undefined) setGrassCure(scenarioToLoad.grassCure);
    if (scenarioToLoad.percentConifer !== undefined) setPercentConifer(scenarioToLoad.percentConifer);
    if (scenarioToLoad.useHourlyForecast !== undefined) setUseHourlyForecast(scenarioToLoad.useHourlyForecast);
    if (scenarioToLoad.structure) {
      setStructureSpread(scenarioToLoad.structure.spread);
      setStructureEmbers(scenarioToLoad.structure.embers);
      setStructureVegBridge(scenarioToLoad.structure.vegBridge);
      setStructureRoofShare(scenarioToLoad.structure.roofShare);
    }
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
    if (scenarioToLoad.ensembleMembers !== undefined) {
      setEnsembleOn(scenarioToLoad.ensembleOn ?? scenarioToLoad.ensembleMembers !== null);
      if (scenarioToLoad.ensembleMembers) setEnsembleMembers(scenarioToLoad.ensembleMembers);
    }
    if (scenarioToLoad.burningPeriod !== undefined) {
      setBurningOn(scenarioToLoad.burningPeriod !== null);
      if (scenarioToLoad.burningPeriod) {
        setBpStart(scenarioToLoad.burningPeriod.start_hour);
        setBpEnd(scenarioToLoad.burningPeriod.end_hour);
      }
    }
    if (scenarioToLoad.ffmcSpinUp !== undefined) setSpinUpOn(scenarioToLoad.ffmcSpinUp);
    setScheduleRows(scenarioToLoad.weatherSchedule ?? []);
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
      ensembleMembers: ensembleOn ? ensembleMembers : null,
      burningPeriod: burningOn ? { start_hour: bpStart, end_hour: bpEnd } : null,
      ffmcSpinUp: spinUpOn,
      weatherSchedule: scheduleRows,
      startIso: startIsoSnap,
      grassCure,
      percentConifer,
      useHourlyForecast,
      weatherOrigin: {
        manual: weatherManual,
        source: weatherSource,
        station: stationName,
        distanceKm: stationDistanceKm,
        timestamp: weatherTimestamp,
      },
      structure: { spread: structureSpread, embers: structureEmbers, vegBridge: structureVegBridge, roofShare: structureRoofShare },
      ensembleOn,
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    ignitionPoint, weather, fwi, fuelType, useEdmontonGrid, useSyntheticCA,
    enableSpotting, spottingIntensity, includeWater, includeBuildings, includeWUI,
    includeDEM, durationHours, snapshotMinutes, simMode, multiDayDays, mcIterations,
    ensembleOn, ensembleMembers, burningOn, bpStart, bpEnd, spinUpOn, scheduleRows,
    startIsoSnap, grassCure, percentConifer, useHourlyForecast, weatherManual,
    weatherSource, stationName, stationDistanceKm, weatherTimestamp, structureSpread,
    structureEmbers, structureVegBridge, structureRoofShare,
  ]);

  // ── Skill options: errors, and the settings a perimeter restart reuses ────
  const burningError = burningOn ? validateBurningHours(bpStart, bpEnd) : null;
  // A start outside the burning period: no spread until it opens (R3)
  const waitH = burningOn && !burningError && startMs !== null
    ? hoursUntilBurning(startMs, { start_hour: bpStart, end_hour: bpEnd })
    : null;
  const outsideBurning = waitH !== null && startDate
    ? `Starts ${formatClock(startDate)}, outside the burning period (${formatBurningPeriod({ start_hour: bpStart, end_hour: bpEnd })}): ` +
      `no spread is modelled until ${formatHour(bpStart)}, ${formatWait(waitH)} after the start.`
    : null;
  const spinUpAvailable = simMode === "single" && useHourlyForecast;
  useEffect(() => {
    onSkillOptions?.({
      burningPeriod: burningOn && !burningError ? { start_hour: bpStart, end_hour: bpEnd } : null,
      spinUp: spinUpOn && spinUpAvailable,
    });
  }, [onSkillOptions, burningOn, burningError, bpStart, bpEnd, spinUpOn, spinUpAvailable]);

  // ── Manual weather changes: errors, and Setup's weather for a restart ─────
  const scheduleOn = simMode === "single" && !useHourlyForecast && scheduleRows.length > 0;
  // Fuel-grid vintage: a scenario dated before the grid's year (issue 6; date check only)
  const vintageWarning = useEdmontonGrid && startMs !== null
    ? fuelVintageWarning(DEFAULT_FUEL_GRID_YEAR, edmontonYear(startMs))
    : null;
  const schedErrors = useMemo(
    () => (scheduleOn ? scheduleErrors(scheduleRows, startMs, durationHours) : []),
    [scheduleOn, scheduleRows, startMs, durationHours],
  );
  useEffect(() => {
    onSetupWeather?.({
      weather,
      fwi,
      steps: scheduleOn && startMs !== null ? timedSteps(scheduleRows, startMs) : [],
      forecastOn: useHourlyForecast,
    });
  }, [onSetupWeather, weather, fwi, scheduleOn, scheduleRows, startMs, useHourlyForecast]);

  // ── Station weather (CWFIS) for the ignition point ───────────────────────
  // Weather and FWI are filled from the nearest station only while they are still "auto":
  // never edited, not from a preset or a loaded scenario. Once the user has set them, moving
  // the ignition keeps them (owner bug 2026-10-10: a move used to overwrite every input);
  // "Update weather for this location" loads the station values on request.
  const markWeatherManual = () => {
    weatherManualRef.current = true;
    setWeatherManual(true);
    setReplaced(null);
  };
  // Undo an automatic station fill: the values before it, as they were (manual or not)
  const undoReplaced = () => {
    if (!replaced) return;
    setWeather(replaced.weather);
    setFwi(replaced.fwi);
    weatherManualRef.current = true;
    setWeatherManual(true);
    setReplaced(null);
  };
  const [farKm, setFarKm] = useState<number | null>(null);

  const loadStationWeather = async (point: { lat: number; lng: number }, onRequest: boolean) => {
    setWeatherLoading(true);
    setWeatherMessage(null);
    try {
      const w = await fetchCurrentWeather(point.lat, point.lng);
      // An edit made while the request was out wins over an automatic fill
      if (!onRequest && weatherManualRef.current) return;
      if (w.available) {
        const prevWeather = weatherRef.current;
        const prevFwi = fwiRef.current;
        const nextWeather: WeatherParams = {
          ...prevWeather,
          ...(w.wind_speed !== null ? { wind_speed: Math.round(w.wind_speed) } : {}),
          ...(w.wind_direction !== null ? { wind_direction: Math.round(w.wind_direction) } : {}),
          ...(w.temperature !== null ? { temperature: Math.round(w.temperature) } : {}),
          ...(w.relative_humidity !== null ? { relative_humidity: Math.round(w.relative_humidity) } : {}),
        };
        // Codes rounded to one decimal (R10: the summary showed "FFMC 71.7629123990164")
        const nextFwi: FWIOverrides = {
          ffmc: w.ffmc !== null ? roundCode(w.ffmc) : prevFwi.ffmc,
          dmc: w.dmc !== null ? roundCode(w.dmc) : prevFwi.dmc,
          dc: w.dc !== null ? roundCode(w.dc) : prevFwi.dc,
        };
        setWeather(nextWeather);
        setFwi(nextFwi);
        // An automatic fill says what it replaced and from where (round-3 field test R9: the
        // weather changed silently from W 20 km/h, RH 30 %, FWI 28.5 to W 8, RH 63, FWI 3.1)
        const changed = weatherBrief(prevWeather, prevFwi) !== weatherBrief(nextWeather, nextFwi);
        if (!onRequest && changed) {
          const from = [
            w.station_name ?? w.source ?? "CWFIS",
            w.distance_km != null ? `${w.distance_km} km away` : null,
            w.data_timestamp ? `as of ${w.data_timestamp.slice(0, 10)}` : null,
          ].filter(Boolean).join(", ");
          setReplaced({ weather: prevWeather, fwi: prevFwi, manual: weatherManualRef.current, from });
        } else {
          setReplaced(null);
        }
        setWeatherSource(w.source);
        setWeatherTimestamp(w.data_timestamp ?? null);
        setStationName(w.station_name ?? null);
        setStationDistanceKm(w.distance_km ?? null);
        weatherAnchorRef.current = point;
        weatherManualRef.current = false;
        setWeatherManual(false);
        setFarKm(null);
      }
      setWeatherMessage(w.message);
    } catch {
      setWeatherMessage("Could not reach CWFIS — check network");
    } finally {
      setWeatherLoading(false);
    }
  };

  useEffect(() => {
    if (!ignitionPoint) return;
    const key = `${ignitionPoint.lat.toFixed(4)},${ignitionPoint.lng.toFixed(4)}`;
    if (autoFetchedRef.current === key) return; // already handled for this point
    autoFetchedRef.current = key;
    if (weatherManualRef.current) {
      // Keep the user's weather; say so when the new point is far from where it was set
      const a = weatherAnchorRef.current;
      const km = a ? distanceKm(a, ignitionPoint) : null;
      setFarKm(km !== null && km > FAR_KM ? km : null);
      return;
    }
    void loadStationWeather(ignitionPoint, false);
  // loadStationWeather is stable in behaviour; only re-run when the ignition point changes
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

  // Grass curing: the entry, else 95 % before green-up (1 Mar-29 May), else required when
  // grass can burn (decision M1; utils/curing.ts, engine firesim/fbp/curing.py)
  const cureDefault = defaultGrassCure(startMs !== null ? edmontonDayOfYear(startMs) : null);
  const cure = grassCure ?? cureDefault;
  const grassInPlay = useEdmontonGrid || useSyntheticCA || fuelType === "O1a" || fuelType === "O1b";
  const curingRequired = grassInPlay && cureDefault === null;
  const curingError = grassInPlay && cure === null
    ? `Enter grass curing (no default outside ${SPRING_WINDOW_LABEL})`
    : null;
  const setCure = (raw: string) => {
    if (raw.trim() === "") {
      setGrassCure(null);
      return;
    }
    const v = Math.min(100, Math.max(0, Number(raw)));
    if (!Number.isFinite(v)) return;
    setGrassCure(v);
    setLastCure(v);
    try {
      localStorage.setItem(LAST_CURE_KEY, String(v));
    } catch {
      // storage unavailable: the suggestion is a convenience only
    }
  };

  // FBP fuel modifiers; foliar moisture is computed server-side from the scenario start's
  // Edmonton calendar date (ST-X-3 eqs 1-8)
  const fuelModifiers = (atMs: number): FuelModifiers => {
    const doy = edmontonDayOfYear(atMs);
    const c = grassCure ?? defaultGrassCure(doy);
    return {
      ...(c !== null ? { grass_cure: c } : {}),
      percent_conifer: percentConifer,
      day_of_year: doy,
    };
  };

  const handleMonteCarlo = () => {
    const atMs = runStartMs();
    if (!ignitionPoint || !onComputeBurnProbability || hasErrors || curingError || atMs === null) return;
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
    if (!ignitionPoint || hasErrors || curingError || atMs === null) return;
    let hourly = null;
    const spinWanted = spinUpOn && useHourlyForecast;
    if (useHourlyForecast) {
      onRunRequested?.(durationHours);
      try {
        // Forecast hours aligned to the scenario start, not to now (design spec §2.3); with
        // the spin-up, also the hours from 17:00 before the start
        hourly = await fetchHourlyForecast(
          ignitionPoint.lat, ignitionPoint.lng, durationHours, atMs,
          spinWanted ? spinupFromMs(atMs) : undefined,
        );
        const at = new Date(atMs);
        const nRun = hourly.filter((r) => r.hours_from_start > -1).length;
        setWeatherMessage(`Hourly forecast: ${nRun} h from ${FORECAST_MODEL_LABEL}, from ${formatClock(at)} ${zoneAbbrev(at)}`);
      } catch (err) {
        setWeatherMessage(`Hourly forecast unavailable (${(err as Error).message}); using constant weather`);
      }
    }
    const skill = skillRequestFields(
      { burningOn, startHour: bpStart, endHour: bpEnd, spinUpOn: spinWanted }, hourly, atMs,
    );
    if (spinWanted && skill.spinUpSkipped) {
      setWeatherMessage((m) => `${m ?? ""}${m ? " · " : ""}FFMC spin-up skipped: ${skill.spinUpSkipped}`);
    }
    const weatherSchedule = scheduleOn ? scheduleRequest(scheduleRows, atMs) : null;
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
      weather_schedule: weatherSchedule,
      hourly_forecast: !!hourly,
      start_time: toEdmontonIso(atMs),
      fuel_vintage_warning: useEdmontonGrid ? fuelVintageWarning(DEFAULT_FUEL_GRID_YEAR, edmontonYear(atMs)) : null,
      spotting: enableSpotting && (useEdmontonGrid || useSyntheticCA),
    });
    onStartSimulation({
      ignition_lat: ignitionPoint.lat,
      ignition_lng: ignitionPoint.lng,
      weather,
      start_time: toEdmontonIso(atMs),
      fwi_overrides: fwi,
      fuel_modifiers: fuelModifiers(atMs),
      hourly_weather: hourly,
      weather_schedule: weatherSchedule,
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
      // The ensemble needs a fuel grid (grid runs only)
      ensemble: ensembleOn && useEdmontonGrid ? { n_members: ensembleMembers } : null,
      burning_period: skill.burning_period,
      ffmc_spin_up: skill.ffmc_spin_up,
      structure_spread: structureAvailable && structureSpread,
      structure_embers: structureAvailable && structureSpread && structureEmbers,
      structure_vegetation_bridge: structureAvailable && structureSpread && structureVegBridge,
      structure_combustible_roof_share:
        structureAvailable && structureSpread && structureEmbers ? structureRoofShare : 0,
    });
  };

  const handleMultiDaySubmit = () => {
    const atMs = runStartMs();
    if (!ignitionPoint || !onStartMultiDaySimulation || curingError || atMs === null) return;
    // Run parameters for the header chips, the EOC summary and the ICS forms: Day 1's weather,
    // the starting codes and every day's weather (R5, R11: "FIRE SPREAD · ?H" and the previous
    // single run's weather were shown for a multi-day run)
    onRunParams?.({
      ...runParamsFor(multiDayDays[0], fwi, multiDayDays.length * 24, fuelType, null, false, toEdmontonIso(atMs)),
      fuel_vintage_warning: useEdmontonGrid ? fuelVintageWarning(DEFAULT_FUEL_GRID_YEAR, edmontonYear(atMs)) : null,
      multi_day_days: multiDayDays,
      spotting: false,
    });
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
      start_time: toEdmontonIso(atMs),
      burning_period: skillRequestFields(
        { burningOn, startHour: bpStart, endHour: bpEnd, spinUpOn: false }, null, atMs,
      ).burning_period,
    }, atMs);
  };

  // User edits of the weather and FWI inputs
  const editWeather = (w: WeatherParams) => {
    setWeather(w);
    markWeatherManual();
  };
  const editFwi = (f: FWIOverrides) => {
    setFwi(f);
    markWeatherManual();
  };

  const handleLoadWeather = async () => {
    if (!ignitionPoint) return;
    autoFetchedRef.current = `${ignitionPoint.lat.toFixed(4)},${ignitionPoint.lng.toFixed(4)}`;
    await loadStationWeather(ignitionPoint, true);
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
      markWeatherManual();
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
    markWeatherManual();
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
        : curingError
          ? curingError
        : simMode === "single" && hasErrors
          ? `Fix the inputs: ${errorList.join("; ")}`
          : burningError
            ? `Fix the burning period: ${burningError}`
            : schedErrors.length
              ? `Fix the weather changes: ${schedErrors[0]}`
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
  // The collapsed Setup rail's Run button
  const lastRunRequest = useRef(runRequest);
  useEffect(() => {
    if (runRequest === lastRunRequest.current) return;
    lastRunRequest.current = runRequest;
    runRef.current();
  }, [runRequest]);
  const runLabel = isRunning ? "Simulating..." : simMode === "single" ? "Run Simulation" : `Run ${multiDayDays.length * 24}h Scenario`;
  useEffect(() => {
    onRunState?.({ canRun, reason: disabledReason, label: runLabel });
  }, [onRunState, canRun, disabledReason, runLabel]);
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
      {onNewIgnition && (
        <TipButton className="btn-secondary btn-inline run-bar-new-ignition" tip={TIPS.newIgnition} onClick={onNewIgnition}>
          New ignition
        </TipButton>
      )}
      <div id="run-bar-reason" className={`run-bar-reason${disabledReason && !isRunning ? " attention" : ""}`}>
        {disabledReason ?? `${simMode === "single" ? `${durationHours} h` : `${multiDayDays.length * 24} h (${multiDayDays.length} days)`} from ${startDate ? (startFollowsNow ? `now (${formatClock(startDate)} ${zoneAbbrev(startDate)})` : `${formatClock(startDate)} ${zoneAbbrev(startDate)}`) : "the start"}`}
      </div>
      {!disabledReason && outsideBurning && (
        <div className="run-bar-warn" role="note" data-testid="run-bar-burning-warning">
          <Badge tone="warn" tip={outsideBurning}>{`No spread until ${formatHour(bpStart)}`}</Badge>
        </div>
      )}
    </div>
  );

  return (
    <div className="weather-panel">
      {replaced && (
        <div className="status-line weather-replaced" role="status" data-testid="weather-replaced">
          <Badge tone="warn" tip={`Was ${weatherBrief(replaced.weather, replaced.fwi)}. Undo puts those values back.`}>
            Weather replaced
          </Badge>
          <span className="hint-sm"> by station values for this ignition: {replaced.from}. Was {weatherBrief(replaced.weather, replaced.fwi)}.</span>
          {simMode === "multiday" && (
            <span className="hint-sm" data-testid="weather-replaced-multiday"> {TIPS.multiDayReplaced}</span>
          )}
          <span className="weather-replaced-actions">
            <button type="button" className="btn-secondary btn-inline" onClick={undoReplaced}>Undo</button>
            <button type="button" className="btn-secondary btn-inline" onClick={() => setReplaced(null)}>Keep</button>
          </span>
        </div>
      )}
      <div className="setup-preset with-tip">
        <label className="setup-preset-label">
          Start from a preset
          <select value={presetId} onChange={(e) => applyPreset(e.target.value)}>
            <option value="">Keep current inputs</option>
            {PRESETS.map((p) => (
              <option key={p.id} value={p.id}>{p.label}</option>
            ))}
          </select>
        </label>
        <InfoTip
          label="About the presets"
          text={`${TIPS.preset}${presetId ? ` ${PRESETS.find((p) => p.id === presetId)?.note ?? ""}` : ""}`}
        />
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
        <legend className="with-tip">Ignition point <InfoTip label="How to set the ignition" text={TIPS.ignitionHowTo} /></legend>
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
          <span className="visually-hidden" id={`${fieldId}-coord-hint`}>{TIPS.coords}</span>
          <InfoTip label="Coordinate formats" text={TIPS.coords} />
        </div>
        </fieldset>

        <fieldset className="field-group">
        <legend className="with-tip">Scenario start <InfoTip label="About the scenario start" text={TIPS.startNow} /></legend>
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
        {vintageWarning && (
          <div className="status-line" role="note" data-testid="fuel-vintage-start">
            <Badge tone="warn" tip={vintageWarning}>Before the fuel grid's year ({DEFAULT_FUEL_GRID_YEAR})</Badge>
          </div>
        )}
        {outsideBurning && (
          <div className="status-line" role="note" data-testid="start-outside-burning">
            <Badge tone="warn" tip={outsideBurning}>Outside the burning period</Badge>
            <span className="hint-sm"> No spread until {formatHour(bpStart)} ({formatWait(waitH!)})</span>
          </div>
        )}
        <span className="visually-hidden" id={`${fieldId}-start-hint`}>{TIPS.startNow}</span>
        {useHourlyForecast && simMode === "single" && startMs !== null && startMs < nowMs - 3_600_000 && (
          <div className="status-line">
            <Badge tone="info" tip={TIPS.hindcast}>{BADGES.pastStart}</Badge>
          </div>
        )}
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
              ? multiDaySummary(multiDayDays, fwi)
              : `${windLabel(weather.wind_direction)} ${weather.wind_speed} km/h · RH ${weather.relative_humidity}% · FWI ${liveFWI.toFixed(1)} ${liveDanger}${useHourlyForecast ? " · hourly forecast" : scheduleOn ? ` · ${scheduleRows.length} weather change${scheduleRows.length === 1 ? "" : "s"}` : ""}`
        }
        attention={weatherErrors || schedErrors.length > 0}
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
                onChange={(e) => editWeather({ ...weather, wind_speed: Number(e.target.value) })}
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
                onChange={(e) => editWeather({ ...weather, wind_direction: Number(e.target.value) })}
              />
            </label>

            <label>
              Temperature: <strong>{weather.temperature}°C</strong>
              <input
                type="range"
                min={-10}
                max={45}
                value={weather.temperature}
                onChange={(e) => editWeather({ ...weather, temperature: Number(e.target.value) })}
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
                onChange={(e) => editWeather({ ...weather, relative_humidity: Number(e.target.value) })}
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
                onChange={(e) => editWeather({ ...weather, precipitation_24h: Number(e.target.value) })}
              />
            </label>

            <div className="with-tip">
              <label>
                <input
                  type="checkbox"
                  checked={useHourlyForecast}
                  onChange={(e) => setUseHourlyForecast(e.target.checked)}
                />
                Use hourly forecast weather
              </label>
              <InfoTip label="About hourly forecast weather" text={TIPS.hourlyForecast} />
            </div>
            <WeatherSchedulePanel
              rows={scheduleRows}
              onChange={(rows) => { setScheduleRows(rows); markWeatherManual(); }}
              base={weather}
              startMs={startMs}
              forecastOn={useHourlyForecast}
              errors={schedErrors}
            />
          </div>
        )}
        {simMode === "multiday" && (
          <div className="status-line">
            <Badge tone="neutral" tip={TIPS.multiDayWeather}>Set per day in 4</Badge>
          </div>
        )}

        <div className="section">
          <h4>FWI fuel moisture codes</h4>
          <label>
            FFMC: <strong>{fmtCode(fwi.ffmc)}</strong>
            <input
              type="range"
              min={0}
              max={101}
              value={fwi.ffmc ?? 85}
              onChange={(e) => editFwi({ ...fwi, ffmc: Number(e.target.value) })}
            />
          </label>
          {validationErrors.ffmc && <div className="input-error">{validationErrors.ffmc}</div>}

          <label>
            DMC: <strong>{fmtCode(fwi.dmc)}</strong>
            <input
              type="range"
              min={0}
              max={999}
              value={fwi.dmc ?? 40}
              onChange={(e) => editFwi({ ...fwi, dmc: Number(e.target.value) })}
            />
          </label>
          {validationErrors.dmc && <div className="input-error">{validationErrors.dmc}</div>}

          <label>
            DC: <strong>{fmtCode(fwi.dc)}</strong>
            <input
              type="range"
              min={0}
              max={1000}
              value={fwi.dc ?? 200}
              onChange={(e) => editFwi({ ...fwi, dc: Number(e.target.value) })}
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
          <FwiClassChip className="fwi-danger-badge" fwi={liveFWI} label={liveDanger} />
        </div>
        {liveBUI < 80 && (
          <div className="status-line" role="note">
            <Badge tone="warn" tip={TIPS.d2Bui(liveBUI)} testId="d2-bui-badge">{BADGES.d2NoSpread}</Badge>
          </div>
        )}

        <div className="setup-links">
          <TipButton
            className="toggle-advanced"
            onClick={handleComputeFWI}
            disabled={fwiLoading}
            tip={TIPS.updateCodes}
          >
            {fwiLoading ? "Computing..." : "Update codes from weather"}
          </TipButton>
          <TipButton
            className="toggle-advanced"
            onClick={handleLoadWeather}
            disabled={!ignitionPoint || weatherLoading}
            tip={TIPS.loadWeather}
          >
            {weatherLoading ? "Loading..." : "Update weather for this location"}
          </TipButton>
          <a
            href="https://tphambolio.github.io/FWI/"
            target="_blank"
            rel="noopener noreferrer"
            className="hint-link"
          >
            Fire weather detail →
          </a>
        </div>

        {weatherMessage && (() => {
          const st = shortWeatherStatus(weatherMessage);
          return (
            <div className={`status-line weather-message ${st.ok ? "text-success" : "text-danger"}`} data-testid="weather-status">
              <span>{st.text}</span>
              <InfoTip label="Weather details" text={weatherMessage} />
            </div>
          );
        })()}

        {farKm !== null && (
          <div className="status-line" role="status" data-testid="weather-far">
            <Badge tone="warn" tip={TIPS.weatherFar(farKm)}>Weather set {Math.round(farKm)} km away</Badge>
          </div>
        )}
        {weatherManual ? (
          <div className="hint-sm weather-message" data-testid="weather-source">
            Manual <InfoTip label="About manual weather" text={TIPS.weatherManual} />
          </div>
        ) : isPyraSource(weatherSource) ? (
          <div className="hint-sm weather-message" data-testid="weather-source" data-source-tier="pyra">
            {weatherSource}
          </div>
        ) : (weatherSource || stationName) && (
          <div className="hint-sm weather-message" data-testid="weather-source">
            {stationName && (
              <span>
                {stationName}
                {stationDistanceKm !== null && <span> · {stationDistanceKm} km away</span>}
                {" · "}
              </span>
            )}
            {weatherSource && !stationName && <span>{weatherSource} · </span>}
            {weatherTimestamp ? `as of noon LST ${weatherTimestamp.slice(0, 10)}` : ""}
          </div>
        )}
      </SetupSection>

      {/* 3 ── Fuel & landscape ──────────────────────────────────────────── */}
      <SetupSection
        num={3}
        title="Fuel & landscape"
        summary={
          useEdmontonGrid
            ? `Edmonton grid · curing ${cure !== null ? `${cure}%` : "required"}${enableSpotting ? " · spotting on" : ""}`
            : `${fuelType} uniform${useSyntheticCA ? " · synthetic mosaic" : ""} · curing ${cure !== null ? `${cure}%` : "required"}`
        }
        attention={curingError !== null || vintageWarning !== null}
      >
        {vintageWarning && (
          <div className="status-line fuel-vintage-warning" role="note" data-testid="fuel-vintage-setup">
            <Badge tone="warn">{vintageWarning}</Badge>
          </div>
        )}
        <div className="with-tip">
          <label>
            <input
              type="checkbox"
              checked={useEdmontonGrid}
              onChange={(e) => {
                setUseEdmontonGrid(e.target.checked);
                onEdmontonGridChange?.(e.target.checked ? EDMONTON_FUEL_GRID_PATH : null);
              }}
            />
            Use Edmonton fuel grid
          </label>
          <InfoTip label="About the Edmonton fuel grid" text={TIPS.edmontonGrid(fuelType)} />
          <InfoTip label="About the Alberta Wildfire fuel grid" text={TIPS.albertaGrid} />
        </div>
        <label>
          Grass curing (%){curingRequired ? " · required" : grassCure === null && cureDefault !== null ? " · spring default" : ""}
          <input
            type="number"
            min={0}
            max={100}
            step={5}
            value={cure ?? ""}
            placeholder={lastCure !== null ? `last ${lastCure}` : undefined}
            required={curingRequired}
            aria-invalid={curingError !== null}
            onChange={(e) => setCure(e.target.value)}
            title={
              `Degree of curing for O-1a/O-1b grass; 100 = fully cured. Curing factor ${cure !== null ? curingFactor(cure).toFixed(2) : "–"} ` +
              `(Wotton et al. 2009, eq 35b). Default 95 % from ${SPRING_WINDOW_LABEL}, before green-up; ` +
              `outside that window enter the observed value.`
            }
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
            <div className="with-tip">
              <label>
                <input
                  type="checkbox"
                  checked={useSyntheticCA}
                  onChange={(e) => setUseSyntheticCA(e.target.checked)}
                />
                Synthetic fuel mosaic (demo grid)
              </label>
              <InfoTip label="About the synthetic fuel mosaic" text={TIPS.syntheticCA} />
            </div>
          </>
        )}
        {(useEdmontonGrid || useSyntheticCA) && (
          <>
            <div className="with-tip">
              <label>
                <input
                  type="checkbox"
                  checked={enableSpotting}
                  onChange={(e) => setEnableSpotting(e.target.checked)}
                />
                Ember spotting
              </label>
              <Badge tone="warn" tip={TIPS.spotting}>{BADGES.illustrative}</Badge>
            </div>
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
              </div>
            )}
          </>
        )}
        {useEdmontonGrid && (
          <>
            <div className="with-tip check-row-indent">
              <label>
                <input
                  type="checkbox"
                  checked={includeWater}
                  onChange={(e) => setIncludeWater(e.target.checked)}
                />
                Extra water mask (OSM)
              </label>
              <InfoTip label="About the extra water mask" text={TIPS.waterMask} />
            </div>
            <div className="with-tip check-row-indent">
              <label>
                <input
                  type="checkbox"
                  checked={includeBuildings}
                  onChange={(e) => setIncludeBuildings(e.target.checked)}
                />
                Buildings (Alberta footprints)
              </label>
              <InfoTip label="About the building footprints" text={TIPS.buildings} />
            </div>
            <div className="with-tip check-row-indent">
              <label className="struct-option">
                <input
                  type="checkbox"
                  checked={structureAvailable && structureSpread}
                  disabled={!structureAvailable}
                  onChange={(e) => setStructureSpread(e.target.checked)}
                />
                House-to-house spread
              </label>
              <Badge tone="warn" tip={TIPS.structureOption}>{BADGES.illustrative}</Badge>
            </div>
            <div className="with-tip check-row-indent struct-suboption">
              <label className="struct-option">
                <input
                  type="checkbox"
                  checked={structureAvailable && structureSpread && structureEmbers}
                  disabled={!(structureAvailable && structureSpread)}
                  onChange={(e) => setStructureEmbers(e.target.checked)}
                />
                Ember ignition
              </label>
              <InfoTip label="About embers between buildings" text={TIPS.structureEmbers} />
            </div>
            <div className="with-tip check-row-indent struct-suboption">
              <label className="struct-option">
                <input
                  type="checkbox"
                  checked={structureAvailable && structureSpread && structureVegBridge}
                  disabled={!(structureAvailable && structureSpread)}
                  onChange={(e) => setStructureVegBridge(e.target.checked)}
                  data-testid="structure-veg-bridge"
                />
                Tree-bridged gaps (20–45 m)
              </label>
              <InfoTip label="About tree-bridged gaps" text={TIPS.structureVegBridge} />
            </div>
            <div className="with-tip check-row-indent struct-suboption">
              <label className="struct-option">
                Combustible roofs
                <select
                  value={structureAvailable && structureSpread && structureEmbers ? structureRoofShare : 0}
                  disabled={!(structureAvailable && structureSpread && structureEmbers)}
                  onChange={(e) => setStructureRoofShare(Number(e.target.value))}
                  data-testid="structure-roof-share"
                >
                  {ROOF_SHARES.map((v) => (
                    <option key={v} value={v}>
                      {v === 0 ? "None" : `${Math.round(v * 100)} %`}
                    </option>
                  ))}
                </select>
              </label>
              <Badge tone="warn" tip={TIPS.structureRoofs}>{BADGES.scenario}</Badge>
            </div>
            <div className="with-tip check-row-indent">
              <label>
                <input
                  type="checkbox"
                  checked={includeWUI}
                  onChange={(e) => setIncludeWUI(e.target.checked)}
                />
                WUI zone modifiers
              </label>
              <Badge tone="warn" tip={TIPS.wui} testId="wui-unsourced">{BADGES.unsourced}</Badge>
            </div>
            <div className="with-tip check-row-indent">
              <label>
                <input
                  type="checkbox"
                  checked={includeDEM}
                  onChange={(e) => setIncludeDEM(e.target.checked)}
                />
                Terrain slope (DEM)
              </label>
              <InfoTip label="About terrain slope" text={TIPS.dem} />
            </div>
          </>
        )}
      </SetupSection>

      {/* 4 ── Run options ───────────────────────────────────────────────── */}
      <SetupSection
        num={4}
        title="Run options"
        summary={
          (simMode === "single"
            ? `Single event · ${durationHours} h · ${snapshotMinutes} min snapshots${
                useEdmontonGrid ? (ensembleOn ? ` · range of outcomes (${ensembleMembers})` : " · single run only") : ""}`
            : `Multi-day · ${multiDayDays.length} days · ${snapshotMinutes} min snapshots`) +
          (burningError
            ? " · check the burning period"
            : burningOn
              ? ` · burns ${formatBurningPeriod({ start_hour: bpStart, end_hour: bpEnd })}`
              : " · burns all day")
        }
        attention={!!burningError}
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
            <InfoTip label="About multi-day runs" text={TIPS.multiDay} />
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
        {simMode === "single" && (
          <div className="ensemble-option" role="group" aria-label="Range of outcomes">
            <div className="with-tip">
              <label>
                <input
                  type="checkbox"
                  checked={ensembleOn && useEdmontonGrid}
                  disabled={!useEdmontonGrid}
                  onChange={(e) => setEnsembleOn(e.target.checked)}
                />
                Range of outcomes (ensemble)
              </label>
              <InfoTip
                label="About the range of outcomes"
                text={useEdmontonGrid ? TIPS.ensemble(ensembleMembers) : TIPS.ensembleNeedsGrid}
              />
            </div>
            {ensembleOn && useEdmontonGrid && (
              <label>
                Members: <strong>{ensembleMembers}</strong>
                <input
                  type="range"
                  min={10}
                  max={100}
                  step={10}
                  value={ensembleMembers}
                  onChange={(e) => setEnsembleMembers(Number(e.target.value))}
                />
              </label>
            )}
            {!useEdmontonGrid && <p className="status-line">{BADGES.needsGrid}</p>}
          </div>
        )}
        <fieldset className="field-group skill-options">
          <legend>Diurnal burning</legend>
          <div className="with-tip">
            <label className="check-row">
              <input
                type="checkbox"
                checked={burningOn}
                onChange={(e) => setBurningOn(e.target.checked)}
              />
              <span>
                Burning period{" "}
                {burningError ? "" : formatBurningPeriod({ start_hour: bpStart, end_hour: bpEnd })}
              </span>
            </label>
            <InfoTip label="About the burning period" interactive>
              {TIPS.burningPeriod}
              {bpStart === DEFAULT_BURNING_PERIOD.start_hour && bpEnd === DEFAULT_BURNING_PERIOD.end_hour
                ? ""
                : ` These hours differ from the validated ${formatBurningPeriod(DEFAULT_BURNING_PERIOD)}.`}{" "}
              <a href={VALIDATION_DOC_URL} target="_blank" rel="noopener noreferrer">
                Evidence: held-out validation
              </a>
            </InfoTip>
          </div>
          {burningOn && (
            <div className="field-row field-row-nowrap check-row-indent">
              <label className="field field-hour">
                From (h)
                <input
                  type="number"
                  min={0}
                  max={24}
                  step={1}
                  value={Number.isNaN(bpStart) ? "" : bpStart}
                  onChange={(e) => setBpStart(e.target.valueAsNumber)}
                  aria-invalid={!!burningError}
                  aria-describedby={burningError ? `${fieldId}-bp-err` : undefined}
                />
              </label>
              <label className="field field-hour">
                To (h)
                <input
                  type="number"
                  min={0}
                  max={24}
                  step={1}
                  value={Number.isNaN(bpEnd) ? "" : bpEnd}
                  onChange={(e) => setBpEnd(e.target.valueAsNumber)}
                  aria-invalid={!!burningError}
                  aria-describedby={burningError ? `${fieldId}-bp-err` : undefined}
                />
              </label>
            </div>
          )}
          {burningError && <div className="input-error" id={`${fieldId}-bp-err`} role="alert">{burningError}</div>}
          <div className="with-tip">
            <label className="check-row">
              <input
                type="checkbox"
                checked={spinUpOn && spinUpAvailable}
                disabled={!spinUpAvailable}
                onChange={(e) => setSpinUpOn(e.target.checked)}
              />
              <span>Evening FFMC spin-up</span>
            </label>
            <InfoTip label="About the evening FFMC spin-up" text={TIPS.spinUp} />
          </div>
          {!spinUpAvailable && (
            <p className="status-line" data-testid="spinup-reason">
              {simMode === "multiday" ? BADGES.notMultiDay : BADGES.needsHourly}
            </p>
          )}
        </fieldset>
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
          <TipButton
            className="btn-secondary"
            onClick={handleMonteCarlo}
            disabled={!ignitionPoint || burnProbRunning || isRunning || (!useEdmontonGrid && !useSyntheticCA) || hasErrors || curingError !== null}
            tip={TIPS.burnProbability}
          >
            {burnProbRunning ? `Running ${mcIterations} iterations...` : "Apply & Run Burn Probability"}
          </TipButton>
          {burnProbRunning && (
            <div className="burn-prob-progress">
              <div className="burn-prob-progress-bar" />
            </div>
          )}
          {(hasErrors || curingError !== null || !ignitionPoint || (!useEdmontonGrid && !useSyntheticCA)) && (
            <div className={`status-line${hasErrors || curingError !== null ? " text-danger" : ""}`} data-testid="burnprob-reason">
              {hasErrors ? "Fix input errors" : curingError !== null ? curingError : !ignitionPoint ? "Set an ignition point" : "Needs a fuel grid"}
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
