/** FireSim V3 — Canadian FBP Wildfire Spread Simulator */

import { Suspense, useCallback, useState, useMemo, useRef, useEffect } from "react";
import { lazyChunk } from "./utils/lazyChunk";
import MapView from "./components/MapView";
import WeatherPanel from "./components/WeatherPanel";
import type { RunParams, SetupWeather, SkillOptionsState } from "./components/WeatherPanel";
import { runParamsFor } from "./utils/fwiCalc";
import FireMetrics from "./components/FireMetrics";
import EOCSummary from "./components/EOCSummary";
import { runFuelLabel, type ICS209RunContext } from "./utils/ics209";
import TimeSlider from "./components/TimeSlider";
import AssetLayersPanel, { type UserLayer } from "./components/AssetLayersPanel";
import CriticalAssetsPanel from "./components/CriticalAssetsPanel";
import ScenarioPanel from "./components/ScenarioPanel";
import EvacStatusPanel from "./components/EvacStatusPanel";
import { FUEL_TYPES } from "./types/simulation";
import { useSimulation } from "./hooks/useSimulation";
import { useScenarios } from "./hooks/useScenarios";
import { cancelEnsemble, computeBurnProbability, fetchAreaLayer, fetchFuelGridImage } from "./services/api";
import type { BurningPeriod, SimulationCreate, SimulationFrame, BurnProbabilityRequest, BurnProbabilityResponse, ScenarioConfig, PerimeterOverrideRequest } from "./types/simulation";
import { useRecon } from "./hooks/useRecon";
import { clockAt, formatClock, formatElapsed, formatRunSpan, isMultiDayRun, roundToMinute, zoneAbbrev, zonedWallTimeToMs } from "./utils/time";
import { loadSetup, saveSetup, type SetupSnapshot } from "./utils/setupStore";
import { clearLiveRun, LIVE_RUN_LOST, readLiveRun, saveLiveRun } from "./utils/liveRun";
import { SETUP_RESTORE_PREFIX } from "./components/WeatherPanel";
import { weatherAt } from "./utils/weatherSchedule";
import StructureSpreadPanel from "./components/StructureSpreadPanel";
import { caveatFor, roofScenarioLabel, structureDetail, structureUnitsGeoJSON } from "./utils/structureSpread";
import {
  ARRIVAL_BUFFER_M,
  arrivalsToGeoJSON,
  featureName,
  firePoints,
  neighbourhoodArrivals,
  neighbourhoodArrivalsFromPoints,
  planningZones,
  evacStatusExport,
  upsertTier,
  mergeCommunities,
  type EvacTier,
  type EvacTierRecord,
} from "./utils/evacZones";
// The EOC console (and its ICS forms) loads only when its tab is opened
const EOCConsole = lazyChunk(() => import("./components/EOCConsole"));
// The About & sources tab is static text, loaded when opened
const AboutPanel = lazyChunk(() => import("./components/AboutPanel"));
import type { AboutSectionId } from "./components/AboutPanel";
import OperationalPeriodPanel from "./components/OperationalPeriodPanel";
import IncidentPanel from "./components/IncidentPanel";
import IsochronePanel from "./components/IsochronePanel";
import { useIncident } from "./hooks/useIncident";
import { computeIsochrones, DEFAULT_ISO_HOURS } from "./utils/isochrones";
import PerimeterOverridePanel from "./components/PerimeterOverridePanel";
import MapErrorBoundary from "./components/MapErrorBoundary";
import AppErrorBoundary from "./components/AppErrorBoundary";
import TopBar, { type AppTab } from "./components/TopBar";
import ValidationScorecardDialog from "./components/ValidationScorecard";
// Exercise mode (docs/exercise-mode-spec.md, PR0): behind a feature flag, off by default
import { isExerciseFlagOn } from "./exercise/flag";
import { useExerciseClock } from "./exercise/useExerciseClock";
import { DEFAULT_EXERCISE_HOURS, frameIndexForExerciseTime, regularBoundaries } from "./exercise/clock";
import { EMPTY_INJECT_LOG } from "./exercise/injects";
import type { InjectLog } from "./types/exercise";
import ExerciseBanner from "./components/ExerciseBanner";
import InjectLogPanel from "./components/InjectLogPanel";
import Badge from "./components/Badge";
import InfoTip from "./components/InfoTip";
import FwiClassChip from "./components/FwiClassChip";
import { BADGES, TIPS } from "./content/explanations";
import RunProgress from "./components/RunProgress";
import { TipButton } from "./components/InfoTip";
import { fallbackScenarioConfig } from "./utils/scenarioDefaults";
import SetupSection from "./components/SetupSection";
import SavedRunsPanel from "./components/SavedRunsPanel";
import RunLabelField from "./components/RunLabelField";
import { withRunLabel, type SavedRun } from "./services/savedRuns";
import { buildObservedRequest, type OverrideInputs } from "./utils/activeEdges";
import SituationPanel from "./components/SituationPanel";
import { CollapseButton, SideRail } from "./components/SideRail";
import { isTypingTarget, useLayout } from "./hooks/useLayout";
import EnsemblePanel, { type EnsembleToggles } from "./components/EnsemblePanel";
import { useEnsemble } from "./hooks/useEnsemble";
import {
  arrivalLevels,
  arrivalLines,
  arrivalPoints,
  arrivalRings,
  probabilityPixels,
  type EnsembleMapLayers,
} from "./utils/ensemble";
import {
  ALBERTA_ATTRIBUTION,
  ALBERTA_MAP_ATTRIBUTION,
  CATEGORY_ORDER,
  EDMONTON_ATTRIBUTION,
  EDMONTON_MAP_ATTRIBUTION,
  areaBox,
  outsideEdmonton,
  assetReach,
  assetRows,
  assetsFromGeoJSON,
  assetsToMapGeoJSON,
  fireFromEnsemble,
  fireFromFrames,
  roadPiecesToGeoJSON,
  roadReach,
  roadRows,
  type Asset,
  type AssetCategory,
  type CriticalReach,
} from "./utils/assets";

/**
 * Export burn probability contour polygons as GeoJSON.
 * Three MultiPolygon features at 25 / 50 / 75 % thresholds.
 * Each cell in the probability grid becomes a rectangular polygon ring.
 */
function exportBurnProbGeoJSON(
  data: BurnProbabilityResponse,
  lastRunParams: RunParams | null,
  ignitionPoint: { lat: number; lng: number } | null
) {
  const { burn_probability, rows, cols, lat_min, lat_max, lng_min, lng_max } = data;
  const cellLat = (lat_max - lat_min) / rows;
  const cellLng = (lng_max - lng_min) / cols;

  const thresholds = [
    { probability: 0.25, label: "25%" },
    { probability: 0.50, label: "50%" },
    { probability: 0.75, label: "75%" },
  ];

  const sharedProps = {
    run_date: new Date().toISOString(),
    wind_speed: lastRunParams?.weather.wind_speed ?? null,
    wind_dir: lastRunParams?.weather.wind_direction ?? null,
    fwi: lastRunParams?.fwi_value != null ? +lastRunParams.fwi_value.toFixed(1) : null,
    danger_rating: lastRunParams?.danger_rating ?? null,
    n_iterations: data.n_iterations,
    iterations_completed: data.iterations_completed,
    ignition_lat: ignitionPoint?.lat ?? null,
    ignition_lon: ignitionPoint?.lng ?? null,
    cell_size_m: data.cell_size_m,
  };

  const features = thresholds.map(({ probability, label }) => {
    // Collect all cell rings that meet the threshold
    const rings: number[][][] = [];
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const p = burn_probability[r]?.[c] ?? 0;
        if (p < probability) continue;
        const latTop = lat_max - r * cellLat;
        const latBot = lat_max - (r + 1) * cellLat;
        const lngL = lng_min + c * cellLng;
        const lngR = lng_min + (c + 1) * cellLng;
        rings.push([[lngL, latBot], [lngR, latBot], [lngR, latTop], [lngL, latTop], [lngL, latBot]]);
      }
    }
    return {
      type: "Feature" as const,
      properties: { probability, label, ...sharedProps },
      geometry: {
        type: "MultiPolygon" as const,
        coordinates: rings.map((ring) => [ring]),
      },
    };
  });

  const geojson = {
    type: "FeatureCollection" as const,
    crs: { type: "name", properties: { name: "urn:ogc:def:crs:OGC:1.3:CRS84" } },
    features,
  };

  const blob = new Blob([JSON.stringify(geojson, null, 2)], { type: "application/geo+json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  const ts = new Date().toISOString().slice(0, 16).replace(/[T:]/g, "-");
  a.href = url;
  a.download = `burn-probability-${ts}.geojson`;
  a.click();
  URL.revokeObjectURL(url);
}

function exportPerimeterGeoJSON(
  frames: SimulationFrame[],
  ignitionPoint: { lat: number; lng: number } | null
) {
  // Build a GeoJSON FeatureCollection: one Polygon per time step.
  // Perimeter coords are [lat, lng] — GeoJSON requires [lng, lat].
  const features = frames
    .filter((f) => f.perimeter && f.perimeter.length >= 3)
    .map((f) => ({
      type: "Feature" as const,
      properties: {
        time_hours: f.time_hours,
        area_ha: f.area_ha,
        head_ros_m_min: f.head_ros_m_min,
        max_hfi_kw_m: f.max_hfi_kw_m,
        fire_type: f.fire_type,
        flame_length_m: f.flame_length_m,
      },
      geometry: {
        type: "Polygon" as const,
        // Close the ring by repeating the first coordinate
        coordinates: [
          [
            ...f.perimeter.map(([lat, lng]) => [lng, lat]),
            [f.perimeter[0][1], f.perimeter[0][0]],
          ],
        ],
      },
    }));

  const geojson = {
    type: "FeatureCollection" as const,
    crs: { type: "name", properties: { name: "urn:ogc:def:crs:OGC:1.3:CRS84" } },
    metadata: {
      source: "FireSim V3 — Canadian FBP Wildfire Spread Simulator",
      ignition_lat: ignitionPoint?.lat ?? null,
      ignition_lng: ignitionPoint?.lng ?? null,
      exported_at: new Date().toISOString(),
      total_frames: features.length,
    },
    features,
  };

  const blob = new Blob([JSON.stringify(geojson, null, 2)], {
    type: "application/geo+json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  const ts = new Date().toISOString().slice(0, 16).replace(/[T:]/g, "-");
  a.href = url;
  a.download = `firesim_perimeter_${ts}.geojson`;
  a.click();
  URL.revokeObjectURL(url);
}

// ── EOC start screen — shown when no incident is active ──────────────────────

function EocStartScreen({ onCreate }: { onCreate: (name: string) => void }) {
  const [name, setName] = useState("");
  const submit = () => {
    const n = name.trim();
    if (n) { onCreate(n); setName(""); }
  };
  return (
    <div className="eoc-start-screen">
      <div className="eoc-start-card">
        <div className="eoc-start-icon">🔥</div>
        <h2 className="eoc-start-title">Start a New Incident</h2>
        <p className="eoc-start-hint">
          Name the incident before opening the EOC Console. <InfoTip label="About incidents" text={TIPS.eocResume} />
        </p>
        <input
          className="eoc-start-input"
          type="text"
          placeholder="e.g. River Valley Fire"
          aria-label="Incident name"
          value={name}
          autoFocus
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
          maxLength={60}
        />
        <button className="eoc-start-btn" onClick={submit} disabled={!name.trim()}>
          Open EOC Console
        </button>
      </div>
    </div>
  );
}

// ── Bundled Edmonton layers (loaded with the Edmonton fuel grid) ─────────────

const EDMONTON_ASSETS_URL = "./edmonton/assets.geojson";
const EDMONTON_ROADS_URL = "./edmonton/roads.geojson";

export default function App() {
  const [ignitionPoint, setIgnitionPoint] = useState<{
    lat: number;
    lng: number;
  } | null>(null);
  const [burnProbabilityData, setBurnProbabilityData] = useState<BurnProbabilityResponse | null>(null);
  const [burnProbRunning, setBurnProbRunning] = useState(false);
  const [burnProbError, setBurnProbError] = useState<string | null>(null);
  const [showBurnProbView, setShowBurnProbView] = useState(false);
  const [lastRunParams, setLastRunParams] = useState<RunParams | null>(null);
  // Neighbourhood polygons (arrival outlines, evacuation status), loaded on startup
  const [communities, setCommunities] = useState<GeoJSON.FeatureCollection | null>(null);
  // Critical assets and major roads: Edmonton layers (automatic with the Edmonton grid), user layers
  const [edmontonAssets, setEdmontonAssets] = useState<GeoJSON.FeatureCollection | null>(null);
  const [edmontonRoads, setEdmontonRoads] = useState<GeoJSON.FeatureCollection | null>(null);
  const [edmontonNote, setEdmontonNote] = useState("Loading with the Edmonton fuel grid…");
  // Alberta layers for the area around the ignition outside Edmonton (served by the API)
  const [albertaAssets, setAlbertaAssets] = useState<GeoJSON.FeatureCollection | null>(null);
  const [albertaRoads, setAlbertaRoads] = useState<GeoJSON.FeatureCollection | null>(null);
  const [albertaNote, setAlbertaNote] = useState<string | null>(null);
  const [userLayers, setUserLayers] = useState<UserLayer[]>([]);
  // Asset symbols stay off the map until a run has frames (no clutter while setting up); the
  // card's checkbox can still show them before a run, and once chosen the choice is kept
  const [assetsVisible, setAssetsVisible] = useState(false);
  const assetsChosenRef = useRef(false);
  const chooseAssetsVisible = useCallback((v: boolean) => {
    assetsChosenRef.current = true;
    setAssetsVisible(v);
  }, []);
  const [roadsReachedVisible, setRoadsReachedVisible] = useState(true);
  const [shownCategories, setShownCategories] = useState<ReadonlySet<AssetCategory>>(() => new Set(CATEGORY_ORDER));
  const [focusRequest, setFocusRequest] = useState<{ lngLat: [number, number]; n: number } | null>(null);
  // Evacuation status outlines (set by Planning) and modelled-arrival outlines on the map
  const [evacZonesVisible, setEvacZonesVisible] = useState(true);
  const [arrivalOutlinesVisible, setArrivalOutlinesVisible] = useState(true);
  // Evacuation status set by Planning when no incident is open (kept in this browser)
  const [scratchEvacTiers, setScratchEvacTiers] = useState<EvacTierRecord[]>(loadScratchEvacTiers);
  // Active top-level tab
  const [activeTab, setActiveTab] = useState<AppTab>("simulation");
  // Exercise mode (PR0): the flag is read once per page load; with it off the clock never enables
  const [exerciseFlag] = useState(() => isExerciseFlagOn());
  const exercise = useExerciseClock(exerciseFlag);
  const exerciseOn = exercise.state.enabled;
  // Inject log: in memory for PR0 (no way to add one from the UI yet; spec PR3)
  const [injectLog] = useState<InjectLog>(EMPTY_INJECT_LOG);
  // Section of the About & sources tab to show when it opens (the low-skill badge opens Limits)
  const [aboutSection, setAboutSection] = useState<AboutSectionId | null>(null);
  // Validation scorecard dialog (top bar "Validation"; docs/validation-scorecard.md)
  const [validationOpen, setValidationOpen] = useState(false);
  const openLimits = useCallback(() => {
    setAboutSection("about-limits");
    setActiveTab("about");
  }, []);
  // "Reset this browser's FireSim data" (About ▸ Data kept in this browser; E9, 2026-10-24)
  const openBrowserData = useCallback(() => {
    setAboutSection("about-browser-data");
    setActiveTab("about");
  }, []);
  const [isochronesVisible, setIsochronesVisible] = useState(false);
  // House-to-house spread map layer: on by default when the run has it (opt-in run option)
  const [structureVisible, setStructureVisible] = useState(true);
  // Roof scenario runs: outline the buildings the scenario gave combustible roofs (not observed)
  const [structureRoofOutline, setStructureRoofOutline] = useState(true);
  const [isoTargetHours, setIsoTargetHours] = useState<number[]>(DEFAULT_ISO_HOURS);
  const [fuelGridImage, setFuelGridImage] = useState<{ image: string; bounds: [number, number, number, number]; legend?: Array<{ fuel: string; color: string }> } | null>(null);
  const [fuelGridVisible, setFuelGridVisible] = useState(true);

  // ── Scenario management ───────────────────────────────────────────────────
  const { scenarios, saveScenario, deleteScenario, exportScenario, importScenario } = useScenarios();
  const [scenarioToLoad, setScenarioToLoad] = useState<ScenarioConfig | null>(null);

  // ── Incident store (multi-day operational periods) ────────
  const {
    incident,
    activeIncidentId,
    incidents,
    activePeriod,
    createIncident,
    loadIncident,
    closeIncident,
    deleteIncident,
    advancePeriod,
    setActivePeriodIndex,
    addAnnotation,
    removeAnnotation,
    clearLayerAnnotations,
    fetchAndPlaceFacilities,
    updateIncidentField,
    saveFrameData,
    exportIncident,
    importIncident,
    setEvacTier,
    saveFailed: incidentSaveFailed,
  } = useIncident();
  // The incident and operational period a run is started for (set when any run starts), so
  // the completed run is filed there even if the user switches periods while it computes
  const incidentRef = useRef(incident);
  useEffect(() => {
    incidentRef.current = incident;
  }, [incident]);
  const runTargetRef = useRef<{ incidentId: string; periodIndex: number } | null>(null);
  const currentConfigRef = useRef<Omit<ScenarioConfig, "id" | "createdAt" | "name" | "description"> | null>(null);

  // Edmonton assets and major roads load with the Edmonton grid (no upload needed)
  const loadEdmontonLayers = useCallback(async () => {
    try {
      const [a, r] = await Promise.all([EDMONTON_ASSETS_URL, EDMONTON_ROADS_URL].map(async (u) => {
        const resp = await fetch(u);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        return (await resp.json()) as GeoJSON.FeatureCollection;
      }));
      setEdmontonAssets(a);
      setEdmontonRoads(r);
    } catch {
      setEdmontonNote("The Edmonton asset layers could not be loaded. Reload the page to try again.");
    }
  }, []);

  const handleEdmontonGridChange = useCallback(async (path: string | null) => {
    if (!path) {
      setFuelGridImage(null);
      setEdmontonAssets(null);
      setEdmontonRoads(null);
      setEdmontonNote("Not loaded: the Edmonton fuel grid is off. Add your own layer under Setup.");
      return;
    }
    setEdmontonNote("Loading with the Edmonton fuel grid…");
    void loadEdmontonLayers();
    // Retry up to 3 times — the Fly.io machine may be suspended on first load
    // and needs a few seconds to resume before the image endpoint responds.
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        const img = await fetchFuelGridImage(path);
        setFuelGridImage(img);
        setFuelGridVisible(true);
        return;
      } catch {
        if (attempt < 2) await new Promise((r) => setTimeout(r, 3000 * (attempt + 1)));
      }
    }
    setFuelGridImage(null);
  }, [loadEdmontonLayers]);

  const handleAddLayer = useCallback((name: string, data: GeoJSON.FeatureCollection) => {
    setUserLayers((prev) => [...prev, { id: `${Date.now().toString(36)}${prev.length}`, name, data }]);
  }, []);
  const handleRemoveLayer = useCallback((id: string) => {
    setUserLayers((prev) => prev.filter((l) => l.id !== id));
  }, []);

  // Pre-load community units on startup (arrival outlines and evacuation status): Edmonton
  // neighbourhoods, and Statistics Canada census subdivisions elsewhere in Alberta, with the
  // hamlets and localities inside them (scripts/build_ab_places.py).
  useEffect(() => {
    const get = (url: string) =>
      fetch(url)
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => null) as Promise<GeoJSON.FeatureCollection | null>;
    Promise.all([
      get("./edmonton/neighbourhoods.geojson"),
      get("./alberta/census_subdivisions.geojson"),
      get("./alberta/places.geojson"),
    ])
      .then(([edm, csd, places]) => {
        const merged = mergeCommunities(edm, csd, places);
        if (merged) setCommunities(merged);
      })
      .catch(() => {});
  }, []);

  // Alberta assets and major roads around an ignition outside the Edmonton layers' box,
  // fetched by area (snapped box, so nearby ignitions reuse the request)
  const areaKey = useMemo(() => {
    if (!ignitionPoint) return null;
    const box = areaBox(ignitionPoint.lat, ignitionPoint.lng);
    return box ? JSON.stringify(box) : null;
  }, [ignitionPoint]);
  useEffect(() => {
    if (!areaKey) {
      setAlbertaAssets(null);
      setAlbertaRoads(null);
      setAlbertaNote(null);
      return;
    }
    const box = JSON.parse(areaKey) as { south: number; west: number; north: number; east: number };
    const ctl = new AbortController();
    setAlbertaNote("Loading Alberta assets for this area…");
    Promise.all([fetchAreaLayer("assets", box, ctl.signal), fetchAreaLayer("roads", box, ctl.signal)])
      .then(([a, r]) => {
        const ok = a.metadata?.available !== false;
        setAlbertaAssets(ok ? outsideEdmonton(a) : null);
        setAlbertaRoads(r.metadata?.available !== false ? outsideEdmonton(r) : null);
        setAlbertaNote(ok ? null : "Alberta asset layer not installed on this server: Edmonton only.");
      })
      .catch((e: unknown) => {
        if ((e as { name?: string })?.name === "AbortError") return;
        setAlbertaAssets(null);
        setAlbertaRoads(null);
        setAlbertaNote("Alberta asset layers could not be loaded for this area.");
      });
    return () => ctl.abort();
  }, [areaKey]);

  const {
    status,
    frames,
    simulationId,
    currentFrameIndex,
    currentFrame,
    isRunning,
    isPaused,
    startSimulation,
    startMultiDaySimulation,
    startPerimeterOverride,
    startObservedPerimeter,
    activeSegments: runActiveSegments,
    prepareRun,
    restoreRun,
    setFrameIndex,
    pauseSimulation,
    resumeSimulation,
    cancelSimulation,
    clearResults,
    error,
    archived: runFromArchive,
    phase: runPhase,
    progress: runFraction,
    fuelVintageWarning,
    startedAt: runStartedAt,
    day: runDayNow,
    nDays: runNDays,
    durationHours: runDurationHours,
  } = useSimulation();

  // Scenario start of the current run (design spec §2.3): the start date and time set in
  // Setup ("now" by default), sent as start_time; the engine stays time-agnostic (hours after it).
  const [scenarioStart, setScenarioStart] = useState<Date | null>(null);

  // Ensemble size requested with the current run (null = none: multi-day, perimeter restart)
  const [ensembleMembers, setEnsembleMembers] = useState<number | null>(null);
  // The run whose ensemble the server may still be computing (pending or running), if any
  const activeEnsembleIdRef = useRef<string | null>(null);

  /**
   * Leave the current run before any new one (single, multi-day, perimeter restart) or
   * "Clear results": stop polling its ensemble and ask the server to stop computing it, so
   * the next run neither shows the old range of outcomes nor competes with it for the CPU.
   * Frames, structure layers, arrivals and the timeline all derive from useSimulation's
   * frames, which the run start resets.
   */
  const leaveCurrentRun = useCallback(() => {
    const inc = incidentRef.current;
    runTargetRef.current = inc ? { incidentId: inc.id, periodIndex: inc.activePeriodIndex } : null;
    const old = activeEnsembleIdRef.current;
    activeEnsembleIdRef.current = null;
    if (old) cancelEnsemble(old);
    setEnsembleMembers(null);
  }, []);

  // Spread-skill options: Setup's current settings (reused by the perimeter restart), and the
  // burning period of the current run (timeline shading, Situation note)
  const [skillOptions, setSkillOptions] = useState<SkillOptionsState>({ burningPeriod: null, spinUp: false });
  // Setup's current weather, FWI codes and weather changes: a perimeter restart uses them
  // (owner field test 2026-10-12: restarts used the earlier run's weather)
  const [setupWeather, setSetupWeather] = useState<SetupWeather | null>(null);
  // Fire points of the earlier runs in a perimeter-restart chain, up to each restart's time
  // (absolute times), so community and asset arrivals keep the chain's earliest time (bug 3)
  const [carriedFire, setCarriedFire] = useState<{ lat: number; lng: number; atMs: number }[]>([]);
  const [runBurningPeriod, setRunBurningPeriod] = useState<BurningPeriod | null>(null);
  // Optional label sent with the next run (saved-runs list)
  const [runLabel, setRunLabel] = useState("");
  const [lastRunSpinUp, setLastRunSpinUp] = useState(false);

  // RPAS perimeter restart: observed perimeter, active edges, drawing on the map
  const recon = useRecon();
  const { cancelDrawing: cancelReconDrawing, clear: clearRecon } = recon;

  const handlePerimeterOverride = useCallback(
    (req: PerimeterOverrideRequest) => {
      const at = req.start_time ? Date.parse(req.start_time) : NaN;
      const restartMs = Number.isFinite(at) ? at : Date.now();
      // Carry the chain's fire history up to the restart time (this run's, and what it carried)
      if (scenarioStart) {
        const t0 = scenarioStart.getTime();
        const own = firePoints(frames)
          .map((p) => ({ lat: p.lat, lng: p.lng, atMs: t0 + p.h * 3_600_000 }))
          .filter((p) => p.atMs <= restartMs);
        setCarriedFire((prev) => [...prev.filter((p) => p.atMs <= restartMs), ...own]);
      }
      leaveCurrentRun();
      setScenarioStart(new Date(restartMs));
      setRunBurningPeriod(req.burning_period ?? null);
      setLastRunSpinUp(false);
      // Header chips and reports: the weather the restart runs with
      if (req.weather && req.fwi_overrides) {
        setLastRunParams((prev) => runParamsFor(req.weather!, req.fwi_overrides!, req.duration_hours ?? 4,
          prev?.fuel_type, req.weather_schedule ?? null, prev?.hourly_forecast ?? false, req.start_time ?? null));
      }
      cancelReconDrawing();
      startPerimeterOverride(req);
    },
    [startPerimeterOverride, cancelReconDrawing, leaveCurrentRun, scenarioStart, frames]
  );

  // Standalone forecast from an observed perimeter at T (A1, owner items R13/R14): Setup's
  // run request (WeatherPanel's builder) with the perimeter instead of the ignition
  const observedBuilderRef = useRef<((atMs: number) => SimulationCreate) | null>(null);
  const [canForecastObserved, setCanForecastObserved] = useState(false);
  const handleRequestBuilder = useCallback((b: ((atMs: number) => SimulationCreate) | null) => {
    observedBuilderRef.current = b;
    setCanForecastObserved(!!b);
  }, []);
  const handleObservedStart = useCallback(
    (inputs: Omit<OverrideInputs, "simulationId">, observationIso: string) => {
      const build = observedBuilderRef.current;
      const t = Date.parse(observationIso);
      if (!build || !Number.isFinite(t)) return;
      const req = buildObservedRequest(build(t), inputs, observationIso);
      leaveCurrentRun();
      setCarriedFire([]);
      setScenarioStart(new Date(t));
      setRunBurningPeriod(req.burning_period ?? null);
      setLastRunSpinUp(false);
      cancelReconDrawing();
      startObservedPerimeter(withRunLabel(req, runLabel));
    },
    [startObservedPerimeter, cancelReconDrawing, leaveCurrentRun, runLabel]
  );

  const handleStartMultiDay = useCallback(
    (req: Parameters<typeof startMultiDaySimulation>[0], startMs: number) => {
      leaveCurrentRun();
      setCarriedFire([]);
      recon.clear();
      setScenarioStart(new Date(startMs));
      setRunBurningPeriod(req.burning_period ?? null);
      setLastRunSpinUp(false);
      startMultiDaySimulation(withRunLabel(req, runLabel));
    },
    [startMultiDaySimulation, leaveCurrentRun, recon, runLabel]
  );

  // Run pressed but the request is still being prepared (hourly forecast fetch): leave the
  // previous run now rather than after the fetch
  const handleRunRequested = useCallback(
    (durationHours: number) => {
      leaveCurrentRun();
      prepareRun(durationHours);
    },
    [leaveCurrentRun, prepareRun]
  );

  // After the first completed run: fit the map to the fire; later runs keep the user's view
  // (re-running at a new spot, e.g. the south bank) — "Fit to fire" on the map fits on request.
  // Focus moves to the Situation panel after every run.
  const [fitRequest, setFitRequest] = useState(0);
  const fittedOnceRef = useRef(false);
  // "New ignition": arm placement on the map (the next click sets the ignition)
  const [armIgnition, setArmIgnition] = useState(0);
  const armNewIgnition = useCallback(() => setArmIgnition((n) => n + 1), []);
  const situationRef = useRef<HTMLElement | null>(null);
  const [runBarEl, setRunBarEl] = useState<HTMLDivElement | null>(null);

  // ── Collapsible side columns, map focus mode, narrow-screen drawers (hooks/useLayout) ──
  const layout = useLayout();
  const { runCompleted: layoutRunCompleted, toggle: toggleSide, toggleFocus, escape: layoutEscape, dismissHint } = layout;
  // The collapsed Setup rail runs through WeatherPanel (same checks as the Run bar)
  const [runState, setRunState] = useState<{ canRun: boolean; reason: string | null; label: string }>({
    canRun: false,
    reason: null,
    label: "Run Simulation",
  });
  const [runRequest, setRunRequest] = useState(0);
  const setupPanelRef = useRef<HTMLElement | null>(null);
  const setupCollapseRef = useRef<HTMLButtonElement | null>(null);
  const setupRailRef = useRef<HTMLButtonElement | null>(null);
  const situationCollapseRef = useRef<HTMLButtonElement | null>(null);
  const situationRailRef = useRef<HTMLButtonElement | null>(null);

  // Keyboard: F focus mode, [ / ] toggle Setup / Situation, Esc leaves focus mode (or closes
  // the drawer on narrow screens). Not while typing, not with modifiers, Simulation tab only.
  useEffect(() => {
    if (activeTab !== "simulation") return;
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || e.repeat) return;
      if (e.key === "Escape") {
        if (!e.defaultPrevented && layoutEscape()) e.preventDefault();
        return;
      }
      if (isTypingTarget(e.target)) return;
      if (e.key === "f" || e.key === "F") toggleFocus();
      else if (e.key === "[") toggleSide("setup");
      else if (e.key === "]") toggleSide("situation");
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [activeTab, layoutEscape, toggleFocus, toggleSide]);

  // Keep keyboard focus visible: when a panel collapses or opens under the focused control,
  // move focus to that panel's other toggle (rail button <-> header chevron)
  const expandFocusRef = useRef<"setup" | "situation" | null>(null);
  const expandFromRail = useCallback(
    (side: "setup" | "situation") => {
      expandFocusRef.current = side; // the rail button unmounts: focus the header chevron
      toggleSide(side);
    },
    [toggleSide],
  );
  useEffect(() => {
    const pending = expandFocusRef.current;
    expandFocusRef.current = null;
    if (pending === "setup" && !layout.setupCollapsed) setupCollapseRef.current?.focus({ preventScroll: true });
    if (pending === "situation" && !layout.situationCollapsed) situationCollapseRef.current?.focus({ preventScroll: true });
    const el = document.activeElement as HTMLElement | null;
    const pairs: Array<[HTMLElement | null, boolean, HTMLButtonElement | null, HTMLButtonElement | null]> = [
      [setupPanelRef.current, layout.setupCollapsed, setupRailRef.current, setupCollapseRef.current],
      [situationRef.current, layout.situationCollapsed, situationRailRef.current, situationCollapseRef.current],
    ];
    for (const [panel, collapsed, rail, chevron] of pairs) {
      if (!panel || !el || !panel.contains(el) || el === panel) continue;
      if (el.offsetParent === null) (collapsed ? rail : chevron)?.focus({ preventScroll: true });
    }
  }, [layout.setupCollapsed, layout.situationCollapsed]);

  // Auto-hide the one-time "Setup minimized" hint
  useEffect(() => {
    if (!layout.hint) return;
    const t = setTimeout(dismissHint, 15_000);
    return () => clearTimeout(t);
  }, [layout.hint, dismissHint]);

  const prevStatusRef = useRef<string | null>(null);
  useEffect(() => {
    const prev = prevStatusRef.current;
    prevStatusRef.current = status;
    if (status === "completed" && prev !== "completed" && frames.length > 0) {
      if (!fittedOnceRef.current) {
        fittedOnceRef.current = true;
        setFitRequest((n) => n + 1);
      }
      if (activeTab === "simulation") {
        // Give the map the room once the run is done (once, never over a saved preference)
        layoutRunCompleted();
        situationRef.current?.focus({ preventScroll: true });
      }
    }
  }, [status, frames.length, activeTab, layoutRunCompleted]);

  // Auto-save a completed simulation into the operational period it was started for (else,
  // a run started before the incident was opened, the active period)
  useEffect(() => {
    if (status === "completed" && frames.length > 0 && simulationId && incident) {
      saveFrameData(frames, simulationId, runTargetRef.current, scenarioStart?.toISOString() ?? null, lastRunParams);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  // Run <-> operational period (EOC console): the live run is this period's run, another
  // period's run, or not filed under any period (run before the incident was opened)
  const periodRunId = activePeriod?.simulationId ?? null;
  const liveRunPeriod = useMemo(() => {
    if (!incident || !simulationId) return -1;
    return (incident.operationalPeriods ?? []).findIndex((p) => p.simulationId === simulationId);
  }, [incident, simulationId]);
  const liveRunIsThisPeriods = !!simulationId && periodRunId === simulationId;
  // The EOC forms describe the active period: its own run, or a run not yet filed anywhere when
  // the period has none. Never another period's run (QA 2026-10-11: Day 2's ICS 209 showed
  // Day 1's run after "+ New Period").
  const eocShowsLiveRun = liveRunIsThisPeriods || (!periodRunId && liveRunPeriod < 0);
  const canAttachLiveRun =
    !!incident && !!activePeriod && status === "completed" && frames.length > 0 && liveRunPeriod < 0;
  const handleAttachLiveRun = useCallback(() => {
    if (!incident || !simulationId) return;
    saveFrameData(frames, simulationId, { incidentId: incident.id, periodIndex: incident.activePeriodIndex },
      scenarioStart?.toISOString() ?? null, lastRunParams);
  }, [incident, simulationId, frames, scenarioStart, saveFrameData, lastRunParams]);
  // Reload the active period's run from the server (after a page reload or another run)
  const handleLoadPeriodRun = useCallback(() => {
    if (!incident || !activePeriod?.simulationId) return;
    leaveCurrentRun();
    const at = activePeriod.runStartIso ? Date.parse(activePeriod.runStartIso) : NaN;
    setScenarioStart(Number.isFinite(at) ? new Date(at) : null);
    setRunBurningPeriod(null);
    setCarriedFire([]);
    // The run's inputs, kept with the period: the forms' weather blocks and the header chips (R5)
    setLastRunParams(activePeriod.runParams ?? null);
    restoreRun(activePeriod.simulationId);
  }, [incident, activePeriod, leaveCurrentRun, restoreRun]);

  // Saved runs list: reload a run from the server (not filed under any operational period;
  // "Attach" in the EOC console files it), with its ensemble, and fit the map to it
  const handleOpenSavedRun = useCallback((run: SavedRun) => {
    leaveCurrentRun();
    runTargetRef.current = { incidentId: "", periodIndex: -1 };
    const at = run.start_time ? Date.parse(run.start_time) : NaN;
    setScenarioStart(Number.isFinite(at) ? new Date(at) : null);
    setRunBurningPeriod(null);
    setEnsembleMembers(run.ensemble ? (run.ensemble_members ?? 1) : null);
    fittedOnceRef.current = false;
    restoreRun(run.id);
  }, [leaveCurrentRun, restoreRun]);

  // ── The run on screen survives a reload of the tab (field test 2026-10-18, Issue 2) ──
  // The run's ID and inputs are kept in sessionStorage (utils/liveRun.ts) while it computes and
  // once it has finished; after a reload the app reads it back by ID and, if it is still
  // computing, follows it to the end. Only this tab's own run: no passphrase, no run list.
  const [reattachedRun, setReattachedRun] = useState<{ id: string; startedAt: number | null } | null>(null);
  useEffect(() => {
    if (!simulationId) return;
    if (status === "failed" || status === "cancelled") {
      clearLiveRun();
      return;
    }
    if (status !== "running" && status !== "pending" && status !== "paused" && status !== "completed") return;
    saveLiveRun({
      id: simulationId,
      startIso: scenarioStart ? scenarioStart.toISOString() : null,
      durationHours: runDurationHours,
      startedAt: runStartedAt,
      ensembleMembers,
      burningPeriod: runBurningPeriod,
      runParams: lastRunParams,
      runTarget: runTargetRef.current,
      savedAt: Date.now(),
    });
  }, [simulationId, status, scenarioStart, runDurationHours, runStartedAt, ensembleMembers, runBurningPeriod, lastRunParams]);
  const reattachTriedRef = useRef(false);
  useEffect(() => {
    if (reattachTriedRef.current) return;
    reattachTriedRef.current = true;
    const rec = readLiveRun();
    if (!rec) return;
    const at = rec.startIso ? Date.parse(rec.startIso) : NaN;
    setScenarioStart(Number.isFinite(at) ? new Date(at) : null);
    setLastRunParams(rec.runParams);
    setRunBurningPeriod(rec.burningPeriod);
    setLastRunSpinUp(false);
    setEnsembleMembers(rec.ensembleMembers);
    runTargetRef.current = rec.runTarget;
    fittedOnceRef.current = false;
    setReattachedRun({ id: rec.id, startedAt: rec.startedAt });
    restoreRun(rec.id, rec.durationHours, rec.startedAt, LIVE_RUN_LOST);
  }, [restoreRun]);
  // The notice goes with the run: another run, Clear results or a failed reload removes it
  useEffect(() => {
    if (reattachedRun && simulationId !== reattachedRun.id) setReattachedRun(null);
  }, [simulationId, reattachedRun]);
  // The server no longer has the run (it restarted, or the run was deleted): say so once in
  // the Situation panel and start clean (not "Run restored" over a failed run, E1 2026-10-24).
  // The record was already forgotten when the run read as failed (above).
  const [reattachLost, setReattachLost] = useState(false);
  useEffect(() => {
    if (!reattachedRun || simulationId !== reattachedRun.id || status !== "failed" || error !== LIVE_RUN_LOST) return;
    setReattachedRun(null);
    setReattachLost(true);
    clearResults();
    setLastRunParams(null);
    setRunBurningPeriod(null);
    setEnsembleMembers(null);
  }, [reattachedRun, simulationId, status, error, clearResults]);
  useEffect(() => {
    if (simulationId) setReattachLost(false);
  }, [simulationId]);

  // ── Ensemble (range of outcomes): polled after the single run, the headline once in ──
  const ensemble = useEnsemble(simulationId, ensembleMembers, status);
  // Remember which run's ensemble may still be computing on the server (leaveCurrentRun)
  useEffect(() => {
    activeEnsembleIdRef.current =
      simulationId && (ensemble.phase === "waiting" || ensemble.phase === "running") ? simulationId : null;
  }, [simulationId, ensemble.phase]);
  const ensGrids = ensemble.grids;
  const [ensToggles, setEnsToggles] = useState<EnsembleToggles>({ lines: true, p50: true, p90: false, prob: false });
  const handleEnsToggle = useCallback(
    (key: keyof EnsembleToggles, value: boolean) => setEnsToggles((t) => ({ ...t, [key]: value })),
    [],
  );
  const selectedMinutes = currentFrame ? currentFrame.time_hours * 60 : null;
  const ensLines = useMemo(
    () => (ensGrids ? arrivalLines(ensGrids, arrivalLevels(ensGrids.durationMinutes, scenarioStart), scenarioStart) : []),
    [ensGrids, scenarioStart],
  );
  const ensStatic = useMemo(
    () => (ensGrids ? { p90: arrivalRings(ensGrids, "p90", ensGrids.durationMinutes), prob: probabilityPixels(ensGrids) } : null),
    [ensGrids],
  );
  const ensAt = ensGrids ? (selectedMinutes ?? ensGrids.durationMinutes) : 0;
  const ensNow = useMemo(
    () => (ensGrids ? { p10: arrivalRings(ensGrids, "p10", ensAt), p50: arrivalRings(ensGrids, "p50", ensAt) } : null),
    [ensGrids, ensAt],
  );
  const ensembleMap = useMemo<EnsembleMapLayers | null>(
    () =>
      ensGrids && ensStatic && ensNow
        ? {
            lines: ensLines,
            selectedMinutes: ensAt,
            nowP10: ensNow.p10,
            nowP50: ensNow.p50,
            p90: ensStatic.p90,
            prob: ensStatic.prob,
            show: ensToggles,
          }
        : null,
    [ensGrids, ensStatic, ensNow, ensLines, ensAt, ensToggles],
  );

  // Neighbourhoods: modelled earliest fire arrival within 500 m (a model fact, whole run).
  // With an ensemble, the P10 (early) arrival leads and the single run is secondary.
  // Earlier runs of a restart chain: hours relative to this run's start (negative = before it)
  const carriedPts = useMemo(
    () => (scenarioStart
      ? carriedFire.map((p) => ({ lat: p.lat, lng: p.lng, h: (p.atMs - scenarioStart.getTime()) / 3_600_000 }))
      : []),
    [carriedFire, scenarioStart],
  );
  const arrivals = useMemo(
    () => neighbourhoodArrivals(frames, communities, ARRIVAL_BUFFER_M, carriedPts),
    [frames, communities, carriedPts],
  );
  // House-to-house spread (illustrative): involved footprints for the map, display only
  const structureSummary = useMemo(() => frames.find((f) => f.structure_spread)?.structure_spread ?? null, [frames]);
  const structureUnits = useMemo(() => {
    const d = structureDetail(frames);
    return d && d.length > 0 ? structureUnitsGeoJSON(d) : null;
  }, [frames]);
  const structureClock = useCallback(
    (tH: number) => {
      if (!scenarioStart) return "";
      const t = clockAt(scenarioStart, tH);
      return `${formatClock(t)} ${zoneAbbrev(t)}`;
    },
    [scenarioStart],
  );
  // A multi-day run (its parameters, or frames past day 1): times then carry the day, day 1 too
  const multiDayRun = useMemo(
    () => !!lastRunParams?.multi_day_days?.length || isMultiDayRun(frames),
    [lastRunParams, frames],
  );
  const worstArrivals = useMemo(
    () => (ensGrids ? neighbourhoodArrivalsFromPoints(arrivalPoints(ensGrids, "p10"), communities) : null),
    [ensGrids, communities],
  );
  const arrivalOutlines = useMemo(() => {
    if (worstArrivals?.length) return arrivalsToGeoJSON(worstArrivals, scenarioStart, true, multiDayRun);
    return arrivals.length ? arrivalsToGeoJSON(arrivals, scenarioStart, false, multiDayRun) : null;
  }, [arrivals, worstArrivals, scenarioStart, multiDayRun]);
  const neighbourhoodNames = useMemo(
    () => [...new Set((communities?.features ?? []).map(featureName))].sort((a, b) => a.localeCompare(b)),
    [communities],
  );

  // Evacuation status: only what Planning sets, saved with the incident (or in this browser)
  const evacTierRecords = useMemo(
    () => (incident ? incident.evacTiers ?? [] : scratchEvacTiers),
    [incident, scratchEvacTiers],
  );
  const handleSetEvacTier = useCallback(
    (neighbourhood: string, tier: EvacTier | null) => {
      if (incident) {
        setEvacTier(neighbourhood, tier);
        return;
      }
      setScratchEvacTiers((prev) => {
        const next = upsertTier(prev, neighbourhood, tier);
        saveScratchEvacTiers(next);
        return next;
      });
    },
    [incident, setEvacTier],
  );
  const evacZones = useMemo(() => planningZones(evacTierRecords, communities), [evacTierRecords, communities]);
  const exportEvacStatus = useCallback(() => {
    const geojson = evacStatusExport(evacZones, incident?.name ?? null);
    const blob = new Blob([JSON.stringify(geojson, null, 2)], { type: "application/geo+json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `evacuation-status-${new Date().toISOString().slice(0, 16).replace(/[T:]/g, "-")}.geojson`;
    a.click();
    URL.revokeObjectURL(url);
  }, [evacZones, incident]);

  // ── Critical assets and major roads: modelled arrival (single run, ensemble P10) ──
  const assets = useMemo<Asset[]>(
    () => [
      ...assetsFromGeoJSON(edmontonAssets, { idPrefix: "edm:" }),
      ...assetsFromGeoJSON(albertaAssets, { idPrefix: "ab:" }),
      ...userLayers.flatMap((l) => assetsFromGeoJSON(l.data, { idPrefix: `${l.id}:`, category: "custom", source: l.name })),
    ],
    [edmontonAssets, albertaAssets, userLayers],
  );
  const roads = useMemo<GeoJSON.FeatureCollection | null>(() => {
    const lines = userLayers.flatMap((l) =>
      l.data.features.filter((f) => f.geometry?.type === "LineString" || f.geometry?.type === "MultiLineString"),
    );
    if (!edmontonRoads && !albertaRoads && lines.length === 0) return null;
    return {
      type: "FeatureCollection",
      features: [...(edmontonRoads?.features ?? []), ...(albertaRoads?.features ?? []), ...lines],
    };
  }, [edmontonRoads, albertaRoads, userLayers]);
  const singleFire = useMemo(() => fireFromFrames(frames, carriedPts), [frames, carriedPts]);
  const worstFire = useMemo(() => (ensGrids ? fireFromEnsemble(ensGrids, "p10") : null), [ensGrids]);
  const singleAssetReach = useMemo(() => assetReach(singleFire, assets), [singleFire, assets]);
  const worstAssetReach = useMemo(() => (worstFire ? assetReach(worstFire, assets) : null), [worstFire, assets]);
  const singleRoadReach = useMemo(() => roadReach(singleFire, roads), [singleFire, roads]);
  const worstRoadReach = useMemo(() => (worstFire ? roadReach(worstFire, roads) : null), [worstFire, roads]);
  const hasEnsemble = !!worstFire;
  // Run context for the ICS 209-WF: run ID, clock start, ensemble projections
  const run209 = useMemo<ICS209RunContext>(
    () => ({ simulationId, start: scenarioStart, ensemble: ensGrids }),
    [simulationId, scenarioStart, ensGrids],
  );
  const criticalReach = useMemo<CriticalReach>(
    () => ({
      assets: assetRows(assets, singleAssetReach, worstAssetReach),
      roads: roadRows(singleRoadReach.first, worstRoadReach?.first ?? null),
      hasEnsemble,
      start: scenarioStart,
      bufferM: ARRIVAL_BUFFER_M,
      multiDay: multiDayRun,
    }),
    [assets, singleAssetReach, worstAssetReach, singleRoadReach, worstRoadReach, hasEnsemble, scenarioStart, multiDayRun],
  );
  const assetPoints = useMemo(
    () => (assets.length ? assetsToMapGeoJSON(assets, criticalReach.assets, shownCategories, scenarioStart, hasEnsemble, multiDayRun) : null),
    [assets, criticalReach, shownCategories, scenarioStart, hasEnsemble, multiDayRun],
  );
  const hasFrames = frames.length > 0;
  // The span the shown run models, beside the operational period (EOC console, forms; R11)
  const lastFrameHours = hasFrames ? frames[frames.length - 1].time_hours : null;
  const runSpan = useMemo(
    () => (scenarioStart && lastFrameHours !== null && lastFrameHours > 0 ? formatRunSpan(scenarioStart, lastFrameHours) : null),
    [scenarioStart, lastFrameHours],
  );

  // ── Exercise mode (docs/exercise-mode-spec.md §4; PR0) ──────────────────────
  // The exercise clock spans the run on screen (its start to its last frame), else 72 h from
  // now; "Next period" jumps to the incident's operational-period ends when one is open, else
  // every 12 h. The map and timeline show the last frame at or before the exercise time.
  const exerciseBounds = useCallback((): { startMs: number; endMs: number; boundaries: number[] } => {
    const startMs = scenarioStart ? scenarioStart.getTime() : roundToMinute(Date.now());
    const hours = lastFrameHours !== null && lastFrameHours > 0 ? lastFrameHours : DEFAULT_EXERCISE_HOURS;
    const endMs = startMs + hours * 3_600_000;
    const fromIncident = (incident?.operationalPeriods ?? [])
      .flatMap((p) => {
        const s = zonedWallTimeToMs(p.date, p.opPeriodStart);
        const e = periodEndMs(p.date, p.opPeriodStart, p.opPeriodEnd);
        return [s ?? NaN, e];
      })
      .filter((t) => Number.isFinite(t));
    return { startMs, endMs, boundaries: fromIncident.length ? fromIncident : regularBoundaries(startMs, endMs, 12) };
  }, [scenarioStart, lastFrameHours, incident]);
  const { enable: enableExercise, disable: disableExercise, setBounds: setExerciseBounds, seek: seekExercise } = exercise;
  const toggleExercise = useCallback(() => {
    if (exerciseOn) {
      disableExercise();
      return;
    }
    const b = exerciseBounds();
    enableExercise(b.startMs, b.endMs, b.boundaries);
  }, [exerciseOn, enableExercise, disableExercise, exerciseBounds]);
  // A new run while the exercise is on: the clock keeps its time, clamped to the new span
  useEffect(() => {
    if (!exerciseOn) return;
    const b = exerciseBounds();
    setExerciseBounds(b.startMs, b.endMs, b.boundaries);
  }, [exerciseOn, exerciseBounds, setExerciseBounds]);
  // The exercise clock drives the frame shown (map, Situation panel, timeline)
  const exerciseMs = exercise.state.exerciseMs;
  const exerciseFrameIndex = useMemo(
    () => (exerciseOn ? frameIndexForExerciseTime(frames, scenarioStart?.getTime() ?? null, exerciseMs) : -1),
    [exerciseOn, frames, scenarioStart, exerciseMs],
  );
  useEffect(() => {
    if (exerciseFrameIndex >= 0 && exerciseFrameIndex !== currentFrameIndex) setFrameIndex(exerciseFrameIndex);
  }, [exerciseFrameIndex, currentFrameIndex, setFrameIndex]);
  // Scrubbing the timeline in exercise mode moves the exercise clock to that frame's time
  const seekExerciseToFrame = useCallback(
    (index: number) => {
      const f = frames[index];
      if (f && scenarioStart) seekExercise(scenarioStart.getTime() + f.time_hours * 3_600_000);
    },
    [frames, scenarioStart, seekExercise],
  );
  // A finished run whose last frame is short of the hours asked for: the engine always models
  // to the end, so frames were lost on the way. Say so and offer to reload the run from the
  // server (round-3 field test R6: a 72 h run ended at T+67:30 with no explanation).
  // Fuel line of the EOC summary and forms: a grid run's burned fuel mix, else the Setup fuel
  const runFuel = useMemo(
    () => runFuelLabel(lastRunParams?.fuel_type, frames.length ? frames[frames.length - 1] : null, FUEL_TYPES),
    [lastRunParams?.fuel_type, frames],
  );
  const runShortOf =
    status === "completed" && lastFrameHours !== null && runDurationHours !== null &&
    lastFrameHours < runDurationHours - 1e-6
      ? runDurationHours
      : null;
  useEffect(() => {
    if (hasFrames && !assetsChosenRef.current) setAssetsVisible(true);
  }, [hasFrames]);
  const roadsReached = useMemo(
    () => roadPiecesToGeoJSON(singleRoadReach.pieces, worstRoadReach?.pieces ?? null),
    [singleRoadReach, worstRoadReach],
  );
  const assetSources = useMemo(() => {
    const out: string[] = [];
    if (edmontonAssets) out.push(EDMONTON_ATTRIBUTION);
    if (albertaAssets?.features.length || albertaRoads?.features.length) out.push(ALBERTA_ATTRIBUTION);
    for (const l of userLayers) out.push(`Your layer: ${l.name}`);
    return out;
  }, [edmontonAssets, albertaAssets, albertaRoads, userLayers]);
  const assetAttribution =
    [edmontonAssets ? EDMONTON_MAP_ATTRIBUTION : null, albertaAssets?.features.length ? ALBERTA_MAP_ATTRIBUTION : null]
      .filter(Boolean)
      .join(" · ") || null;
  const toggleCategory = useCallback((c: AssetCategory) => {
    setShownCategories((prev) => {
      const next = new Set(prev);
      if (next.has(c)) next.delete(c);
      else next.add(c);
      return next;
    });
  }, []);
  const showAllCategories = useCallback(() => setShownCategories(new Set(CATEGORY_ORDER)), []);
  const focusAsset = useCallback((a: Asset) => {
    setAssetsVisible(true);
    setShownCategories((prev) => (prev.has(a.category) ? prev : new Set([...prev, a.category])));
    setFocusRequest((prev) => ({ lngLat: a.anchor, n: (prev?.n ?? 0) + 1 }));
  }, []);

  // Compute arrival time isochrones from simulation frames
  const isochrones = useMemo(
    () => computeIsochrones(frames, isoTargetHours),
    [frames, isoTargetHours],
  );

  const handleMapClick = useCallback((lat: number, lng: number) => {
    setIgnitionPoint({ lat, lng });
  }, []);

  const handleClearIgnition = useCallback(() => {
    setIgnitionPoint(null);
  }, []);

  const handleLoadScenario = useCallback((scenario: ScenarioConfig) => {
    // Restore ignition point first
    if (scenario.ignitionPoint) setIgnitionPoint(scenario.ignitionPoint);
    // Signal WeatherPanel to restore its state
    setScenarioToLoad(scenario);
  }, []);

  // ── Setup kept across a reload, per incident (round-3 field test R8; utils/setupStore.ts) ──
  // The incident id the Setup is saved under; undefined until the stored setup was read, so
  // the panel's first (default) snapshot never overwrites it
  const setupKeyRef = useRef<string | null | undefined>(undefined);
  const pendingSetupRef = useRef<{ key: string | null; config: SetupSnapshot } | null>(null);
  const setupTimerRef = useRef<number | null>(null);
  const flushSetup = useCallback(() => {
    if (setupTimerRef.current !== null) window.clearTimeout(setupTimerRef.current);
    setupTimerRef.current = null;
    const p = pendingSetupRef.current;
    pendingSetupRef.current = null;
    if (p) saveSetup(p.key, p.config);
  }, []);
  const handleConfigSnapshot = useCallback(
    (config: Omit<ScenarioConfig, "id" | "createdAt" | "name" | "description">) => {
      currentConfigRef.current = config;
      const key = setupKeyRef.current;
      if (key === undefined) return;
      pendingSetupRef.current = { key, config };
      if (setupTimerRef.current !== null) window.clearTimeout(setupTimerRef.current);
      setupTimerRef.current = window.setTimeout(flushSetup, 400);
    },
    [flushSetup]
  );
  useEffect(() => {
    window.addEventListener("pagehide", flushSetup);
    return () => window.removeEventListener("pagehide", flushSetup);
  }, [flushSetup]);
  // After the browser-data reset: drop the pending Setup write (pagehide would otherwise write
  // it back) and reload, so every module (incidents, Setup, evacuation statuses, layout,
  // legends, the run on screen) starts from its defaults. A controlled reload is simpler and
  // safer than resetting each store in place.
  const handleBrowserDataCleared = useCallback(() => {
    if (setupTimerRef.current !== null) window.clearTimeout(setupTimerRef.current);
    setupTimerRef.current = null;
    pendingSetupRef.current = null;
    window.removeEventListener("pagehide", flushSetup);
    setupKeyRef.current = undefined;
    window.location.reload();
  }, [flushSetup]);
  // On load, and when another incident is opened: restore that incident's setup (ignition,
  // start, weather and its source, curing, options). A new incident takes the current setup;
  // closing one keeps what is on screen.
  const restoreSeqRef = useRef(0);
  useEffect(() => {
    const id = activeIncidentId ?? null;
    const first = setupKeyRef.current === undefined;
    flushSetup();
    setupKeyRef.current = id;
    const stored = first || id !== null ? loadSetup(id) : null;
    if (stored) {
      restoreSeqRef.current += 1;
      setIgnitionPoint(stored.ignitionPoint ?? null);
      setScenarioToLoad({
        ...stored,
        id: `${SETUP_RESTORE_PREFIX}${id ?? "scratch"}:${restoreSeqRef.current}`,
        name: "Restored setup",
        createdAt: new Date().toISOString(),
      });
    } else if (currentConfigRef.current) {
      saveSetup(id, currentConfigRef.current);
    }
  }, [activeIncidentId, flushSetup]);

  const handleSaveScenario = useCallback(
    (config: Omit<ScenarioConfig, "id" | "createdAt">) => {
      saveScenario(config);
    },
    [saveScenario]
  );

  // Last single-event request, for "Retry" on the error toast
  const lastStartRef = useRef<SimulationCreate | null>(null);
  const [dismissedError, setDismissedError] = useState<string | null>(null);
  const handleStartSimulation = useCallback(
    (params: SimulationCreate) => {
      lastStartRef.current = params;
      setDismissedError(null);
      leaveCurrentRun();
      // A new fire: no restart chain, and the observed perimeter of the old run goes (bug 9)
      setCarriedFire([]);
      clearRecon();
      const start = params.start_time ? Date.parse(params.start_time) : NaN;
      setScenarioStart(new Date(Number.isFinite(start) ? start : Date.now()));
      setEnsembleMembers(params.ensemble?.n_members ?? null);
      setRunBurningPeriod(params.burning_period ?? null);
      setLastRunSpinUp(!!params.ffmc_spin_up);
      startSimulation(withRunLabel(params, runLabel));
    },
    [startSimulation, leaveCurrentRun, clearRecon, runLabel]
  );


  const handleRunParams = useCallback((params: RunParams) => {
    setLastRunParams(params);
  }, []);

  // Fuel-grid vintage (issue 6): the server's flag for the grid the run used goes into the
  // run parameters, so the summary, exports and ICS 209 remarks carry it
  useEffect(() => {
    if (status !== "completed") return;
    setLastRunParams((prev) => (prev && prev.fuel_vintage_warning !== fuelVintageWarning
      ? { ...prev, fuel_vintage_warning: fuelVintageWarning }
      : prev));
  }, [status, fuelVintageWarning]);

  const handleComputeBurnProbability = useCallback(
    async (params: BurnProbabilityRequest) => {
      setBurnProbRunning(true);
      setBurnProbError(null);
      setBurnProbabilityData(null);
      try {
        const result = await computeBurnProbability(params);
        setBurnProbabilityData(result);
        setShowBurnProbView(true);
      } catch (err) {
        setBurnProbError(err instanceof Error ? err.message : "Burn probability failed");
      } finally {
        setBurnProbRunning(false);
      }
    },
    []
  );

  return (
    <div
      className={`app${activeTab === "eoc" ? " app-eoc" : activeTab === "about" ? " app-about" : ""}${layout.setupCollapsed ? " setup-collapsed" : ""}${layout.situationCollapsed ? " situation-collapsed" : ""}${layout.narrow ? " layout-narrow" : ""}${layout.focusMode ? " focus-mode" : ""}${exerciseOn ? " app-exercise" : ""}`}
    >
      {/* ── Top bar: incident, tabs, run status, limits badge, clock ── */}
      <TopBar
        incidentName={incident?.name ?? null}
        incidentSub={
          incident && activePeriod
            ? `Operational period ${incident.activePeriodIndex + 1}`
            : <Badge tone="neutral" tip={TIPS.notSaved}>{BADGES.notSaved}</Badge>
        }
        activeTab={activeTab}
        onTabChange={(tab) => {
          if (tab === "about") setAboutSection(null);
          setActiveTab(tab);
        }}
        onOpenLimits={openLimits}
        onOpenValidation={() => setValidationOpen(true)}
        status={status}
        exercise={exerciseFlag ? { on: exerciseOn, onToggle: toggleExercise } : undefined}
        actions={<>
          {isRunning && !isPaused && (
            <button className="btn-control btn-pause" onClick={pauseSimulation}>Pause</button>
          )}
          {isPaused && (
            <>
              <button className="btn-control btn-resume" onClick={resumeSimulation}>Resume</button>
              <button className="btn-control btn-cancel" onClick={cancelSimulation}>Cancel</button>
            </>
          )}
          {status === "completed" && frames.length > 0 && (
            <button className="btn-control btn-export" onClick={() => exportPerimeterGeoJSON(frames, ignitionPoint)}>
              Export GeoJSON
            </button>
          )}
          {burnProbabilityData && (
            <button
              className={`btn-control btn-view-toggle${showBurnProbView ? " active" : ""}`}
              aria-pressed={showBurnProbView}
              onClick={() => setShowBurnProbView((v) => !v)}
            >
              {showBurnProbView ? "Prob view" : "Spread view"}
            </button>
          )}
          {burnProbabilityData && !burnProbRunning && (
            <button className="btn-control btn-export" onClick={() => exportBurnProbGeoJSON(burnProbabilityData, lastRunParams, ignitionPoint)}>
              Export BP GeoJSON
            </button>
          )}
          {showBurnProbView && lastRunParams && (
            <div className="run-params-badge">
              <span>{lastRunParams.weather.wind_speed} km/h {["N","NE","E","SE","S","SW","W","NW"][Math.round(lastRunParams.weather.wind_direction / 45) % 8]}</span>
              <span>·</span>
              <span>FWI {lastRunParams.fwi_value.toFixed(1)}</span>
              <FwiClassChip className="run-params-danger" fwi={lastRunParams.fwi_value} label={lastRunParams.danger_rating} />
            </div>
          )}
        </>}
      />

      {/* ── Exercise banner (exercise mode only): EXERCISE mark, exercise clock, controls ── */}
      {exerciseOn && (
        <ExerciseBanner
          state={exercise.state}
          onTogglePlay={exercise.togglePlay}
          onSetRate={exercise.setRate}
          onNextPeriod={exercise.nextPeriod}
          onExit={exercise.disable}
        />
      )}

      {/* ── Setup column: sections with summaries, More, sticky Run bar ── */}
      <aside
        ref={setupPanelRef}
        className={`setup-panel${layout.setupCollapsed ? " collapsed" : ""}${layout.narrow && !layout.setupCollapsed ? " is-drawer" : ""}`}
        aria-label="Setup"
      >
        {layout.setupCollapsed && (
          <SideRail ref={setupRailRef} side="setup" controls="setup-content" onExpand={() => expandFromRail("setup")}>
            <button
              type="button"
              className="rail-action rail-run"
              aria-label={runState.label}
              aria-describedby={runState.reason ? "setup-rail-reason" : undefined}
              aria-keyshortcuts="Control+Enter"
              title={runState.reason ?? `${runState.label} (Ctrl+Enter)`}
              disabled={!runState.canRun}
              onClick={() => setRunRequest((n) => n + 1)}
            >
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M4.5 2.8v10.4L13 8z" fill="currentColor" /></svg>
            </button>
            {runState.reason && <span id="setup-rail-reason" className="visually-hidden">{runState.reason}</span>}
            {!isRunning && frames.length > 0 && (
              <button
                type="button"
                className="rail-action rail-new-ignition"
                aria-label="New ignition"
                title="New ignition: click the map to place it"
                onClick={armNewIgnition}
              >
                <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
                  <g fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
                    <circle cx="8" cy="8" r="4.5" />
                    <path d="M8 1v2.5M8 12.5V15M1 8h2.5M12.5 8H15" />
                  </g>
                </svg>
              </button>
            )}
          </SideRail>
        )}
        {layout.hint && layout.setupCollapsed && (
          <div className="layout-hint" role="status">
            <span>Setup is minimized so the map has more room. Reopen it from this rail or press <kbd>[</kbd>. <kbd>F</kbd> toggles map focus.</span>
            <button type="button" className="btn-secondary btn-inline" onClick={dismissHint}>Got it</button>
          </div>
        )}
        <div id="setup-content" className="setup-content" hidden={layout.setupCollapsed}>
        <div className="setup-header">
          <h2 className="setup-heading">Setup</h2>
          <CollapseButton ref={setupCollapseRef} side="setup" controls="setup-content" onCollapse={() => toggleSide("setup")} />
        </div>
        <div className="setup-scroll">
          <WeatherPanel
            onStartSimulation={handleStartSimulation}
            onStartMultiDaySimulation={handleStartMultiDay}
            onComputeBurnProbability={handleComputeBurnProbability}
            onRunParams={handleRunParams}
            ignitionPoint={ignitionPoint}
            onIgnitionChange={setIgnitionPoint}
            isRunning={isRunning}
            burnProbRunning={burnProbRunning}
            scenarioToLoad={scenarioToLoad}
            onConfigSnapshot={handleConfigSnapshot}
            onEdmontonGridChange={handleEdmontonGridChange}
            runBarTarget={runBarEl}
            onSkillOptions={setSkillOptions}
            onSetupWeather={setSetupWeather}
            onRequestBuilder={handleRequestBuilder}
            onNewIgnition={!isRunning && frames.length > 0 ? armNewIgnition : undefined}
            onRunRequested={handleRunRequested}
            runRequest={runRequest}
            onRunState={setRunState}
          />
          <RunLabelField value={runLabel} onChange={setRunLabel} disabled={isRunning} />
          <div className="setup-more-label">More</div>
          <SetupSection
            title="Assets, roads & isochrones"
            summary={`${assets.length} asset${assets.length === 1 ? "" : "s"}${userLayers.length ? ` (${userLayers.length} own layer${userLayers.length === 1 ? "" : "s"})` : ""} · isochrones ${isochronesVisible ? "on" : "off"}`}
          >
            <AssetLayersPanel
              edmonton={edmontonAssets ? { assets: edmontonAssets.features.length, roads: edmontonRoads?.features.length ?? 0 } : null}
              edmontonNote={edmontonNote}
              userLayers={userLayers}
              onAddLayer={handleAddLayer}
              onRemoveLayer={handleRemoveLayer}
            />
            <IsochronePanel
              isochrones={isochrones}
              visible={isochronesVisible}
              targetHours={isoTargetHours}
              onToggleVisible={setIsochronesVisible}
              onTargetHoursChange={setIsoTargetHours}
            />
          </SetupSection>
          <SetupSection
            title="Observed perimeter (RPAS)"
            summary={
              !simulationId
                ? (canForecastObserved ? (recon.state.perimeter ? "Perimeter loaded · forecast from it" : "Forecast from an observed perimeter") : "Available after a run")
                : recon.state.perimeter
                  ? `Perimeter loaded · ${recon.state.mode === "whole" ? "whole perimeter active" : `${recon.lines.length} active edge${recon.lines.length === 1 ? "" : "s"}`}`
                  : "Restart this run from an observed perimeter"
            }
          >
            <PerimeterOverridePanel
              simulationId={simulationId}
              onOverrideStart={handlePerimeterOverride}
              isRunning={isRunning}
              recon={recon}
              modelledPerimeter={currentFrame?.perimeter ?? null}
              observedAtMs={scenarioStart && currentFrame ? clockAt(scenarioStart, currentFrame.time_hours).getTime() : null}
              burningPeriod={skillOptions.burningPeriod}
              spinUp={skillOptions.spinUp && lastRunSpinUp}
              setupWeather={setupWeather}
              onObservedStart={canForecastObserved ? handleObservedStart : undefined}
              runActiveSegments={runActiveSegments}
            />
          </SetupSection>
          <SetupSection
            title="Incidents & saved scenarios"
            summary={`${incidents.length} incident${incidents.length === 1 ? "" : "s"} · ${scenarios.length} scenario${scenarios.length === 1 ? "" : "s"}`}
          >
            <IncidentPanel
              incidents={incidents}
              activeIncidentId={activeIncidentId}
              onCreate={createIncident}
              onLoad={loadIncident}
              onClose={closeIncident}
              onDelete={deleteIncident}
              onExport={exportIncident}
              onImport={importIncident}
              onOpenBrowserData={openBrowserData}
            />
            <ScenarioPanel
              scenarios={scenarios}
              currentConfig={currentConfigRef.current ?? fallbackScenarioConfig(ignitionPoint)}
              onSave={handleSaveScenario}
              onLoad={handleLoadScenario}
              onDelete={deleteScenario}
              onExport={exportScenario}
              onImport={importScenario}
            />
          </SetupSection>
          {exerciseOn && (
            <SetupSection
              title="Exercise injects"
              summary={injectLog.injects.length ? `${injectLog.injects.length} inject${injectLog.injects.length === 1 ? "" : "s"}` : "No injects yet"}
              defaultOpen
            >
              <InjectLogPanel log={injectLog} exerciseMs={exercise.state.exerciseMs} />
            </SetupSection>
          )}
          <SetupSection title="Saved runs" summary="Runs kept on the server (passphrase)">
            <SavedRunsPanel onOpen={handleOpenSavedRun} currentRunId={simulationId} currentFromArchive={runFromArchive} />
          </SetupSection>
        </div>
        <div className="run-bar" ref={setRunBarEl} />
        </div>
      </aside>

      {/* ── EOC Console tab (replaces map area + bottom bar) ─────── */}
      {activeTab === "eoc" && !incident && (
        <div className="eoc-tab-wrapper">
          <EocStartScreen onCreate={createIncident} />
        </div>
      )}
      {activeTab === "eoc" && incident && (
        <div className="eoc-tab-wrapper">
          <AppErrorBoundary area="EOC console" resetKey={activeTab} onReset={() => setActiveTab("simulation")}>
          <OperationalPeriodPanel
            incident={incident}
            activePeriod={activePeriod}
            onPeriodSelect={setActivePeriodIndex}
            onAdvancePeriod={advancePeriod}
            onUpdateName={(name) => updateIncidentField("name", name)}
            saveFailed={incidentSaveFailed}
            run={{
              liveRunShown: eocShowsLiveRun && frames.length > 0,
              span: runSpan,
              beyondPeriod: !!(activePeriod && scenarioStart && lastFrameHours !== null &&
                clockAt(scenarioStart, lastFrameHours).getTime() > periodEndMs(activePeriod.date, activePeriod.opPeriodStart, activePeriod.opPeriodEnd)),
              liveRunDay: liveRunPeriod >= 0 ? incident.operationalPeriods[liveRunPeriod]?.day ?? null : null,
              canAttach: canAttachLiveRun,
              onAttach: handleAttachLiveRun,
              onLoad: activePeriod?.simulationId && !liveRunIsThisPeriods && !isRunning ? handleLoadPeriodRun : undefined,
              fromArchive: runFromArchive,
            }}
          />
          <Suspense fallback={<div className="hint eoc-loading">Loading EOC console…</div>}>
          <EOCConsole
            frames={eocShowsLiveRun ? frames : NO_FRAMES}
            currentFrameIndex={eocShowsLiveRun ? currentFrameIndex : 0}
            burnProbabilityData={eocShowsLiveRun ? burnProbabilityData : null}
            showBurnProbView={showBurnProbView}
            runParams={eocShowsLiveRun ? lastRunParams : null}
            ignitionPoint={ignitionPoint}
            fuelTypeLabel={eocShowsLiveRun ? runFuel : undefined}
            overlayCommunities={communities}
            assetPoints={assetPoints}
            assetsVisible={assetsVisible}
            roadsReached={eocShowsLiveRun ? roadsReached : null}
            roadsReachedVisible={roadsReachedVisible}
            assetAttribution={assetAttribution}
            criticalReach={eocShowsLiveRun ? criticalReach : null}
            evacZones={evacZones}
            evacZonesVisible={evacZonesVisible}
            isochrones={isochrones}
            isochronesVisible={eocShowsLiveRun && isochronesVisible}
            fuelGridImage={fuelGridImage}
            fuelGridVisible={fuelGridVisible}
            incidentAnnotations={activePeriod?.annotations ?? []}
            onAddAnnotation={addAnnotation}
            onRemoveAnnotation={removeAnnotation}
            onClearLayer={clearLayerAnnotations}
            onFetchFacilities={ignitionPoint
              ? async () => fetchAndPlaceFacilities(ignitionPoint.lat, ignitionPoint.lng)
              : undefined}
            ghostPerimeter={
              incident && incident.activePeriodIndex > 0
                ? (incident.operationalPeriods[incident.activePeriodIndex - 1]?.finalPerimeter ?? null)
                : null
            }
            incidentName={incident?.name}
            run209={eocShowsLiveRun ? run209 : null}
            operationalPeriod={activePeriod ? `Day ${activePeriod.day} · ${activePeriod.date} ${activePeriod.opPeriodStart}–${activePeriod.opPeriodEnd}` : undefined}
            runSpan={eocShowsLiveRun ? runSpan : null}
            operationalDay={activePeriod?.day ?? 1}
            periodRunId={activePeriod?.simulationId ?? null}
            onIncidentNameChange={(name) => updateIncidentField("name", name)}
          />
          </Suspense>
          </AppErrorBoundary>
        </div>
      )}

      {/* ── About & sources tab: intended use, limits, methods and references, data, help ── */}
      {validationOpen && <ValidationScorecardDialog onClose={() => setValidationOpen(false)} />}

      {activeTab === "about" && (
        <Suspense fallback={<div className="hint eoc-loading">Loading…</div>}>
          <AboutPanel
            section={aboutSection}
            onExportIncident={exportIncident}
            onBrowserDataCleared={handleBrowserDataCleared}
          />
        </Suspense>
      )}

      {/* ── Map area — always mounted so MapLibre doesn't reinitialize on tab switch ─── */}
      <main className="map-area" style={activeTab !== "simulation" ? { display: "none" } : {}}>
        {/* Map focus mode: both side columns collapse; F toggles, Esc leaves */}
        {!layout.narrow && (
          <button
            type="button"
            className={`map-focus-btn${layout.focusMode ? " active" : ""}`}
            aria-pressed={layout.focusMode}
            aria-keyshortcuts="F"
            title={layout.focusMode ? "Leave focus mode (F or Esc)" : "Focus on the map: collapse both side panels (F)"}
            onClick={toggleFocus}
          >
            <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
              <path
                d={layout.focusMode
                  ? "M6 2v4H2M10 2v4h4M6 14v-4H2M10 14v-4h4"
                  : "M2 6V2h4M14 6V2h-4M2 10v4h4M14 10v4h-4"}
                fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"
              />
            </svg>
            <span>{layout.focusMode ? "Exit focus" : "Focus map"}</span>
          </button>
        )}
        {/* Telemetry strip — floating glass chips over the map: the shown run's weather
            (with weather changes, the values in force at the timeline's time), labelled as run
            values so a Setup edit is not mistaken for them (owner field test, bug 10) */}
        {lastRunParams && (() => {
          const scheduled = !!lastRunParams.weather_schedule?.length;
          // Multi-day runs: the shown day's weather and FWI (the days' codes carry over)
          const mdDays = lastRunParams.multi_day_days;
          const mdDay = mdDays?.length ? Math.min(mdDays.length, Math.max(1, currentFrame?.day ?? 1)) : null;
          const w = mdDays && mdDay ? mdDays[mdDay - 1] : weatherAt(lastRunParams.weather, lastRunParams.weather_schedule, currentFrame?.time_hours ?? 0);
          const fwiShown = mdDay && currentFrame?.day_fwi ? currentFrame.day_fwi.fwi : lastRunParams.fwi_value;
          const dir = ["N","NE","E","SE","S","SW","W","NW"][Math.round(w.wind_direction / 45) % 8];
          return (
            <div className="telemetry-strip" role="group" aria-label="Run weather (the shown run's inputs)" data-testid="run-chips">
              <div className="tel-chip tel-chip-caption">
                <span className="tel-label">Run</span>
                <InfoTip label="About the run values" text={TIPS.runChips} />
              </div>
              <div className="tel-chip">
                <span className="tel-label">Run wind{scheduled ? " now" : mdDay ? ` day ${mdDay}` : ""}</span>
                <span className="tel-value">{w.wind_speed}<span className="tel-unit"> km/h</span></span>
                <span className="tel-dir">{dir}</span>
              </div>
              <div className="tel-chip">
                <span className="tel-label">Run RH</span>
                <span className="tel-value">{w.relative_humidity}<span className="tel-unit">%</span></span>
              </div>
              <div className="tel-chip">
                <span className="tel-label">Run temp</span>
                <span className="tel-value">{w.temperature}<span className="tel-unit">°C</span></span>
              </div>
              <div className="tel-chip tel-chip-danger">
                <span className="tel-label">Run FWI{scheduled ? " at start" : mdDay ? ` day ${mdDay}` : ""}</span>
                <span className="tel-value">{fwiShown.toFixed(0)}</span>
                <FwiClassChip className="tel-danger" fwi={fwiShown} label={mdDay ? undefined : lastRunParams.danger_rating} />
              </div>
            </div>
          );
        })()}
        <MapErrorBoundary>
        <MapView
            frames={frames}
            currentFrameIndex={currentFrameIndex}
            onMapClick={handleMapClick}
            onClearIgnition={handleClearIgnition}
            ignitionPoint={ignitionPoint}
            burnProbabilityData={burnProbabilityData}
            showBurnProbView={showBurnProbView}
            overlayCommunities={communities}
            assetPoints={assetPoints}
            assetsVisible={assetsVisible}
            roadsReached={roadsReached}
            roadsReachedVisible={roadsReachedVisible}
            assetAttribution={assetAttribution}
            focusRequest={focusRequest}
            evacZones={evacZones}
            evacZonesVisible={evacZonesVisible}
            isochrones={isochrones}
            isochronesVisible={isochronesVisible}
            fuelGridImage={fuelGridImage}
            fuelGridVisible={fuelGridVisible}
            fitRequest={fitRequest}
            armIgnitionRequest={armIgnition}
            arrivalOutlines={arrivalOutlines}
            arrivalOutlinesVisible={arrivalOutlinesVisible}
            onSetEvacTier={handleSetEvacTier}
            evacTierRecords={evacTierRecords}
            ensemble={ensembleMap}
            recon={recon.mapLayers}
            onReconDrawPoint={recon.addPoint}
            onReconDrawFinish={recon.finishDrawing}
            structureUnits={structureUnits}
            structureVisible={structureVisible}
            structureLabel={structureSummary?.label}
            structureCaveat={caveatFor(structureSummary)}
            structureRoofLabel={roofScenarioLabel(structureSummary)}
            structureRoofOutline={structureRoofOutline}
            structureClock={scenarioStart ? structureClock : undefined}
          />
        </MapErrorBoundary>
      </main>

      {/* ── Situation panel (fixed right; hidden in the EOC tab) ── */}
      {activeTab === "simulation" && (
        <SituationPanel
          ref={situationRef}
          frame={currentFrame}
          frameIndex={currentFrameIndex}
          totalFrames={frames.length}
          status={status}
          scenarioStart={scenarioStart}
          multiDay={multiDayRun}
          headline={
            <EnsemblePanel
              state={ensemble}
              selectedMinutes={selectedMinutes}
              scenarioStart={scenarioStart}
              toggles={ensToggles}
              onToggle={handleEnsToggle}
            />
          }
          kpiCaption={ensemble.phase !== "off" ? BADGES.singleRun : null}
          progress={
            isRunning ? (
              <RunProgress
                phase={runPhase}
                progress={runFraction}
                startedAt={runStartedAt}
                paused={isPaused}
                day={runDayNow}
                nDays={runNDays}
                onCancel={simulationId ? cancelSimulation : undefined}
              />
            ) : null
          }
          actions={
            !isRunning && (frames.length > 0 || status === "cancelled" || status === "failed") ? (
              <div className="situation-actions">
                <TipButton
                  className="btn-secondary btn-inline"
                  tip={TIPS.clearResults}
                  onClick={() => {
                    leaveCurrentRun();
                    clearResults();
                    clearLiveRun();
                    // The header chips describe the run: they go with it (field test 2026-10-18)
                    setLastRunParams(null);
                    setRunBurningPeriod(null);
                    setCarriedFire([]);
                    clearRecon();
                  }}
                >
                  Clear results
                </TipButton>
              </div>
            ) : null
          }
          burningPeriod={runBurningPeriod}
          collapsed={layout.situationCollapsed}
          drawer={layout.narrow && !layout.situationCollapsed}
          contentId="situation-content"
          collapseButton={
            <CollapseButton ref={situationCollapseRef} side="situation" controls="situation-content" onCollapse={() => toggleSide("situation")} />
          }
          rail={
            <SideRail ref={situationRailRef} side="situation" controls="situation-content" onExpand={() => expandFromRail("situation")}>
              {currentFrame && (
                <span className="rail-stat" title={`Area at the selected time: ${currentFrame.area_ha.toFixed(1)} ha`}>
                  {currentFrame.area_ha >= 1000 ? `${(currentFrame.area_ha / 1000).toFixed(1)}k` : Math.round(currentFrame.area_ha)}
                  <small>ha</small>
                </span>
              )}
              {isRunning && <span className="rail-running" title="Run in progress" />}
            </SideRail>
          }
        >
          {reattachLost && !simulationId && (
            <div className="status-line run-reattach-lost" role="status">
              <span data-testid="run-reattach-lost">{LIVE_RUN_LOST}</span>{" "}
              <button type="button" className="btn-secondary btn-inline" onClick={() => setReattachLost(false)}>
                OK
              </button>
            </div>
          )}
          {reattachedRun && reattachedRun.id === simulationId && (
            <div className="status-line run-reattached" role="status" data-testid="run-reattached">
              <Badge tone="info" tip={TIPS.runReattached}>
                {isRunning ? "Run continued after reload" : "Run restored after reload"}
              </Badge>
              {reattachedRun.startedAt !== null && (
                <span className="hint-sm"> started {formatClock(new Date(reattachedRun.startedAt))}</span>
              )}
              <button type="button" className="btn-secondary btn-inline" onClick={() => setReattachedRun(null)}>
                OK
              </button>
            </div>
          )}
          {lastRunParams?.fuel_vintage_warning && frames.length > 0 && (
            <div className="status-line fuel-vintage-warning" role="note" data-testid="fuel-vintage-result">
              <Badge tone="warn">{lastRunParams.fuel_vintage_warning}</Badge>
            </div>
          )}
          {runShortOf !== null && lastFrameHours !== null && (
            <div className="status-line run-short" role="note" data-testid="run-short">
              <Badge tone="warn" tip={TIPS.runShort(formatElapsed(lastFrameHours), runShortOf)}>
                {`Shown to ${formatElapsed(lastFrameHours)} of ${runShortOf} h`}
              </Badge>
              {simulationId && (
                <button type="button" className="btn-secondary btn-inline" onClick={() => restoreRun(simulationId, runDurationHours)}>
                  Reload run
                </button>
              )}
            </div>
          )}
          <FireMetrics
            frame={currentFrame}
            status={status}
            totalFrames={frames.length}
          />
          {structureSummary && (
            <StructureSpreadPanel
              frames={frames}
              frameIndex={currentFrameIndex}
              mapVisible={structureVisible}
              onMapVisible={setStructureVisible}
              mapAvailable={structureUnits !== null}
              roofOutline={structureRoofOutline}
              onRoofOutline={setStructureRoofOutline}
            />
          )}
          <EvacStatusPanel
            arrivals={arrivals}
            worstArrivals={worstArrivals}
            scenarioStart={scenarioStart}
            multiDay={multiDayRun}
            hasRun={frames.length > 0}
            records={evacTierRecords}
            neighbourhoodNames={neighbourhoodNames}
            onSetTier={handleSetEvacTier}
            arrivalsVisible={arrivalOutlinesVisible}
            onArrivalsVisible={setArrivalOutlinesVisible}
            tiersVisible={evacZonesVisible}
            onTiersVisible={setEvacZonesVisible}
            onExport={exportEvacStatus}
            incidentName={incident?.name ?? null}
          />
          <CriticalAssetsPanel
            assets={assets}
            rows={criticalReach.assets}
            roads={criticalReach.roads}
            hasEnsemble={hasEnsemble}
            scenarioStart={scenarioStart}
            multiDay={multiDayRun}
            hasRun={frames.length > 0}
            shown={shownCategories}
            onToggleCategory={toggleCategory}
            onShowAll={showAllCategories}
            onFocus={focusAsset}
            assetsVisible={assetsVisible}
            onAssetsVisible={chooseAssetsVisible}
            roadsVisible={roadsReachedVisible}
            onRoadsVisible={setRoadsReachedVisible}
            sources={assetSources}
            loadNote={albertaNote ?? edmontonNote}
            albertaArea={!!albertaAssets}
          />
          <EOCSummary
            frames={frames}
            burnProbData={burnProbabilityData}
            runParams={lastRunParams}
            ignitionPoint={ignitionPoint}
            fuelTypeLabel={runFuel}
            criticalReach={criticalReach}
            evacZones={evacZones}
            run209={run209}
            incidentName={incident?.name}
            compact
          />
        </SituationPanel>
      )}

      {/* ── Timeline along the bottom (hidden in the EOC tab) ── */}
      {activeTab === "simulation" && (
        <div className="bottom-bar">
          <TimeSlider
            frames={frames}
            currentIndex={currentFrameIndex}
            onIndexChange={exerciseOn ? seekExerciseToFrame : setFrameIndex}
            scenarioStart={scenarioStart}
            burningPeriod={runBurningPeriod}
            multiDay={multiDayRun}
            exerciseTimeMs={exerciseOn ? exercise.state.exerciseMs : null}
          />
        </div>
      )}

      {error && error !== dismissedError && (
        <div className="error-toast" role="alert">
          <span>{plainError(error)}</span>
          {plainError(error) !== error && <span className="error-toast-detail">{error}</span>}
          <span className="error-toast-actions">
            {lastStartRef.current && (
              <button onClick={() => handleStartSimulation(lastStartRef.current!)}>Retry</button>
            )}
            <button onClick={() => setDismissedError(error)} aria-label="Dismiss">Dismiss</button>
          </span>
        </div>
      )}
      {burnProbError && (
        <div className="error-toast" role="alert">
          <span>Burn probability: {plainError(burnProbError)}</span>
          <span className="error-toast-actions">
            <button onClick={() => setBurnProbError(null)} aria-label="Dismiss">Dismiss</button>
          </span>
        </div>
      )}
    </div>
  );
}

/** End of an operational period in Edmonton time (date "YYYY-MM-DD", "HH:MM" start and end;
 * an end at or before the start is the next day); Infinity when malformed. */
function periodEndMs(date: string, start: string, end: string): number {
  const ms = zonedWallTimeToMs(date, end);
  if (ms === null) return Infinity;
  return end <= start ? ms + 86_400_000 : ms;
}

const SCRATCH_EVAC_KEY = "firesim-v3-evac-tiers";
const NO_FRAMES: SimulationFrame[] = [];

function loadScratchEvacTiers(): EvacTierRecord[] {
  try {
    const raw = localStorage.getItem(SCRATCH_EVAC_KEY);
    const parsed = raw ? (JSON.parse(raw) as EvacTierRecord[]) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveScratchEvacTiers(records: EvacTierRecord[]): void {
  try {
    localStorage.setItem(SCRATCH_EVAC_KEY, JSON.stringify(records));
  } catch {
    // storage full or blocked: the statuses stay for this session
  }
}

/** A readable message for common server and network failures (raw text kept as detail). */
function plainError(raw: string): string {
  if (/internal server error|status 500|\b500\b/i.test(raw)) {
    return "The simulation server hit an error. Try again; if it repeats, change the inputs.";
  }
  if (/failed to fetch|networkerror|load failed/i.test(raw)) {
    return "Cannot reach the simulation server. Check the connection and try again.";
  }
  return raw;
}
