/** FireSim V3 — Canadian FBP Wildfire Spread Simulator */

import { lazy, Suspense, useCallback, useState, useMemo, useRef, useEffect } from "react";
import MapView from "./components/MapView";
import WeatherPanel from "./components/WeatherPanel";
import type { RunParams, SkillOptionsState } from "./components/WeatherPanel";
import FireMetrics from "./components/FireMetrics";
import EOCSummary from "./components/EOCSummary";
import type { ICS209RunContext } from "./utils/ics209";
import TimeSlider from "./components/TimeSlider";
import AssetLayersPanel, { type UserLayer } from "./components/AssetLayersPanel";
import CriticalAssetsPanel from "./components/CriticalAssetsPanel";
import ScenarioPanel from "./components/ScenarioPanel";
import EvacStatusPanel from "./components/EvacStatusPanel";
import { FUEL_TYPES } from "./types/simulation";
import { useSimulation } from "./hooks/useSimulation";
import { useScenarios } from "./hooks/useScenarios";
import { computeBurnProbability, fetchFuelGridImage } from "./services/api";
import type { BurningPeriod, SimulationCreate, SimulationFrame, BurnProbabilityRequest, BurnProbabilityResponse, ScenarioConfig, PerimeterOverrideRequest } from "./types/simulation";
import { useRecon } from "./hooks/useRecon";
import { clockAt } from "./utils/time";
import {
  ARRIVAL_BUFFER_M,
  arrivalsToGeoJSON,
  featureName,
  neighbourhoodArrivals,
  neighbourhoodArrivalsFromPoints,
  planningZones,
  planningZonesToGeoJSON,
  upsertTier,
  type EvacTier,
  type EvacTierRecord,
} from "./utils/evacZones";
// The EOC console (and its ICS forms) loads only when its tab is opened
const EOCConsole = lazy(() => import("./components/EOCConsole"));
import OperationalPeriodPanel from "./components/OperationalPeriodPanel";
import IncidentPanel from "./components/IncidentPanel";
import IsochronePanel from "./components/IsochronePanel";
import { useIncident } from "./hooks/useIncident";
import { computeIsochrones, DEFAULT_ISO_HOURS } from "./utils/isochrones";
import PerimeterOverridePanel from "./components/PerimeterOverridePanel";
import MapErrorBoundary from "./components/MapErrorBoundary";
import { fwiClassColor, fwiClassTextColor } from "./utils/fwiClass";
import TopBar from "./components/TopBar";
import SetupSection from "./components/SetupSection";
import SituationPanel from "./components/SituationPanel";
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
  CATEGORY_ORDER,
  EDMONTON_ATTRIBUTION,
  EDMONTON_MAP_ATTRIBUTION,
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
          Name the incident before opening the EOC Console.<br />
          You can rename it at any time from the period strip.
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
        <p className="eoc-start-hint">
          Or resume an existing incident under <strong>Incidents &amp; saved scenarios</strong> in the Setup column.
        </p>
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
  const [userLayers, setUserLayers] = useState<UserLayer[]>([]);
  const [assetsVisible, setAssetsVisible] = useState(true);
  const [roadsReachedVisible, setRoadsReachedVisible] = useState(true);
  const [shownCategories, setShownCategories] = useState<ReadonlySet<AssetCategory>>(() => new Set(CATEGORY_ORDER));
  const [focusRequest, setFocusRequest] = useState<{ lngLat: [number, number]; n: number } | null>(null);
  // Evacuation status outlines (set by Planning) and modelled-arrival outlines on the map
  const [evacZonesVisible, setEvacZonesVisible] = useState(true);
  const [arrivalOutlinesVisible, setArrivalOutlinesVisible] = useState(true);
  // Evacuation status set by Planning when no incident is open (kept in this browser)
  const [scratchEvacTiers, setScratchEvacTiers] = useState<EvacTierRecord[]>(loadScratchEvacTiers);
  // Active top-level tab
  const [activeTab, setActiveTab] = useState<"simulation" | "eoc">("simulation");
  const [isochronesVisible, setIsochronesVisible] = useState(false);
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
  } = useIncident();
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

  // Pre-load Edmonton neighbourhoods on startup (arrival outlines and evacuation status).
  useEffect(() => {
    fetch("./edmonton/neighbourhoods.geojson")
      .then((r) => (r.ok ? r.json() : null))
      .then((fc) => { if (fc) setCommunities(fc as GeoJSON.FeatureCollection); })
      .catch(() => {});
  }, []);

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
    setFrameIndex,
    pauseSimulation,
    resumeSimulation,
    cancelSimulation,
    error,
  } = useSimulation();

  // Scenario start of the current run (design spec §2.3): the start date and time set in
  // Setup ("now" by default), sent as start_time; the engine stays time-agnostic (hours after it).
  const [scenarioStart, setScenarioStart] = useState<Date | null>(null);

  // Ensemble size requested with the current run (null = none: multi-day, perimeter restart)
  const [ensembleMembers, setEnsembleMembers] = useState<number | null>(null);

  // Spread-skill options: Setup's current settings (reused by the perimeter restart), and the
  // burning period of the current run (timeline shading, Situation note)
  const [skillOptions, setSkillOptions] = useState<SkillOptionsState>({ burningPeriod: null, spinUp: false });
  const [runBurningPeriod, setRunBurningPeriod] = useState<BurningPeriod | null>(null);
  const [lastRunSpinUp, setLastRunSpinUp] = useState(false);

  // RPAS perimeter restart: observed perimeter, active edges, drawing on the map
  const recon = useRecon();
  const { cancelDrawing: cancelReconDrawing } = recon;

  const handlePerimeterOverride = useCallback(
    (req: PerimeterOverrideRequest) => {
      const at = req.start_time ? Date.parse(req.start_time) : NaN;
      setScenarioStart(new Date(Number.isFinite(at) ? at : Date.now()));
      setEnsembleMembers(null);
      setRunBurningPeriod(req.burning_period ?? null);
      cancelReconDrawing();
      startPerimeterOverride(req);
    },
    [startPerimeterOverride, cancelReconDrawing]
  );

  const handleStartMultiDay = useCallback(
    (req: Parameters<typeof startMultiDaySimulation>[0], startMs: number) => {
      setScenarioStart(new Date(startMs));
      setEnsembleMembers(null);
      setRunBurningPeriod(req.burning_period ?? null);
      setLastRunSpinUp(false);
      startMultiDaySimulation(req);
    },
    [startMultiDaySimulation]
  );

  // After a run completes: fit the map to the fire and move focus to the Situation panel
  const [fitRequest, setFitRequest] = useState(0);
  const situationRef = useRef<HTMLElement | null>(null);
  const [runBarEl, setRunBarEl] = useState<HTMLDivElement | null>(null);
  const prevStatusRef = useRef<string | null>(null);
  useEffect(() => {
    const prev = prevStatusRef.current;
    prevStatusRef.current = status;
    if (status === "completed" && prev !== "completed" && frames.length > 0) {
      setFitRequest((n) => n + 1);
      if (activeTab === "simulation") situationRef.current?.focus({ preventScroll: true });
    }
  }, [status, frames.length, activeTab]);

  // Auto-save completed simulation into active incident period
  useEffect(() => {
    if (status === "completed" && frames.length > 0 && simulationId && incident) {
      saveFrameData(frames, simulationId);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  // ── Ensemble (range of outcomes): polled after the single run, the headline once in ──
  const ensemble = useEnsemble(simulationId, ensembleMembers, status);
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
  // With an ensemble, the worst-credible (P10) arrival leads and the single run is secondary.
  const arrivals = useMemo(() => neighbourhoodArrivals(frames, communities), [frames, communities]);
  const worstArrivals = useMemo(
    () => (ensGrids ? neighbourhoodArrivalsFromPoints(arrivalPoints(ensGrids, "p10"), communities) : null),
    [ensGrids, communities],
  );
  const arrivalOutlines = useMemo(() => {
    if (worstArrivals?.length) return arrivalsToGeoJSON(worstArrivals, scenarioStart, true);
    return arrivals.length ? arrivalsToGeoJSON(arrivals, scenarioStart) : null;
  }, [arrivals, worstArrivals, scenarioStart]);
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
    const fc = planningZonesToGeoJSON(evacZones);
    const geojson = {
      ...fc,
      metadata: {
        source: "FireSim V3: evacuation status set by Planning (not generated by the model)",
        incident: incident?.name ?? null,
        exported_at: new Date().toISOString(),
      },
    };
    const blob = new Blob([JSON.stringify(geojson, null, 2)], { type: "application/geo+json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `evacuation-status-${new Date().toISOString().slice(0, 16).replace(/[T:]/g, "-")}.geojson`;
    a.click();
    URL.revokeObjectURL(url);
  }, [evacZones, incident]);

  // ── Critical assets and major roads: modelled arrival (single run, worst-credible P10) ──
  const assets = useMemo<Asset[]>(
    () => [
      ...assetsFromGeoJSON(edmontonAssets, { idPrefix: "edm:" }),
      ...userLayers.flatMap((l) => assetsFromGeoJSON(l.data, { idPrefix: `${l.id}:`, category: "custom", source: l.name })),
    ],
    [edmontonAssets, userLayers],
  );
  const roads = useMemo<GeoJSON.FeatureCollection | null>(() => {
    const lines = userLayers.flatMap((l) =>
      l.data.features.filter((f) => f.geometry?.type === "LineString" || f.geometry?.type === "MultiLineString"),
    );
    if (!edmontonRoads && lines.length === 0) return null;
    return { type: "FeatureCollection", features: [...(edmontonRoads?.features ?? []), ...lines] };
  }, [edmontonRoads, userLayers]);
  const singleFire = useMemo(() => fireFromFrames(frames), [frames]);
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
    }),
    [assets, singleAssetReach, worstAssetReach, singleRoadReach, worstRoadReach, hasEnsemble, scenarioStart],
  );
  const assetPoints = useMemo(
    () => (assets.length ? assetsToMapGeoJSON(assets, criticalReach.assets, shownCategories, scenarioStart, hasEnsemble) : null),
    [assets, criticalReach, shownCategories, scenarioStart, hasEnsemble],
  );
  const roadsReached = useMemo(
    () => roadPiecesToGeoJSON(singleRoadReach.pieces, worstRoadReach?.pieces ?? null),
    [singleRoadReach, worstRoadReach],
  );
  const assetSources = useMemo(() => {
    const out: string[] = [];
    if (edmontonAssets) out.push(EDMONTON_ATTRIBUTION);
    for (const l of userLayers) out.push(`Your layer: ${l.name}`);
    return out;
  }, [edmontonAssets, userLayers]);
  const assetAttribution = edmontonAssets ? EDMONTON_MAP_ATTRIBUTION : null;
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

  const handleConfigSnapshot = useCallback(
    (config: Omit<ScenarioConfig, "id" | "createdAt" | "name" | "description">) => {
      currentConfigRef.current = config;
    },
    []
  );

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
      const start = params.start_time ? Date.parse(params.start_time) : NaN;
      setScenarioStart(new Date(Number.isFinite(start) ? start : Date.now()));
      setEnsembleMembers(params.ensemble?.n_members ?? null);
      setRunBurningPeriod(params.burning_period ?? null);
      setLastRunSpinUp(!!params.ffmc_spin_up);
      startSimulation(params);
    },
    [startSimulation]
  );


  const handleRunParams = useCallback((params: RunParams) => {
    setLastRunParams(params);
  }, []);

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
    <div className={`app${activeTab === "eoc" ? " app-eoc" : ""}`}>
      {/* ── Top bar: incident, tabs, run status, limits badge, clock ── */}
      <TopBar
        incidentName={incident?.name ?? null}
        incidentSub={
          incident && activePeriod
            ? `Operational period ${incident.activePeriodIndex + 1}`
            : "Scenario not saved · open an incident in the EOC Console"
        }
        activeTab={activeTab}
        onTabChange={setActiveTab}
        status={status}
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
              <span className="run-params-danger" style={{
                background: fwiClassColor(lastRunParams.fwi_value),
                color: fwiClassTextColor(lastRunParams.fwi_value),
              }}>{lastRunParams.danger_rating}</span>
            </div>
          )}
        </>}
      />

      {/* ── Setup column: sections with summaries, More, sticky Run bar ── */}
      <aside className="setup-panel" aria-label="Setup">
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
          />
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
                ? "Available after a run"
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
            />
            <ScenarioPanel
              scenarios={scenarios}
              currentConfig={currentConfigRef.current ?? {
                ignitionPoint,
                weather: { wind_speed: 20, wind_direction: 270, temperature: 25, relative_humidity: 30, precipitation_24h: 0 },
                fwi: { ffmc: 90, dmc: 45, dc: 300 },
                fuelType: "C2",
                useEdmontonGrid: true,
                useSyntheticCA: false,
                enableSpotting: false,
                spottingIntensity: 1.0,
                includeWater: false,
                includeBuildings: true,
                includeWUI: true,
                includeDEM: true,
                durationHours: 4,
                snapshotMinutes: 30,
                simMode: "single",
                multiDayDays: [],
                mcIterations: 50,
              }}
              onSave={handleSaveScenario}
              onLoad={handleLoadScenario}
              onDelete={deleteScenario}
              onExport={exportScenario}
              onImport={importScenario}
            />
          </SetupSection>
        </div>
        <div className="run-bar" ref={setRunBarEl} />
      </aside>

      {/* ── EOC Console tab (replaces map area + bottom bar) ─────── */}
      {activeTab === "eoc" && !incident && (
        <div className="eoc-tab-wrapper">
          <EocStartScreen onCreate={createIncident} />
        </div>
      )}
      {activeTab === "eoc" && incident && (
        <div className="eoc-tab-wrapper">
          <OperationalPeriodPanel
            incident={incident}
            activePeriod={activePeriod}
            onPeriodSelect={setActivePeriodIndex}
            onAdvancePeriod={advancePeriod}
            onUpdateName={(name) => updateIncidentField("name", name)}
          />
          <Suspense fallback={<div className="hint eoc-loading">Loading EOC console…</div>}>
          <EOCConsole
            frames={frames}
            currentFrameIndex={currentFrameIndex}
            burnProbabilityData={burnProbabilityData}
            showBurnProbView={showBurnProbView}
            runParams={lastRunParams}
            ignitionPoint={ignitionPoint}
            fuelTypeLabel={lastRunParams?.fuel_type ? `${lastRunParams.fuel_type} — ${FUEL_TYPES[lastRunParams.fuel_type] ?? ""}` : undefined}
            overlayCommunities={communities}
            assetPoints={assetPoints}
            assetsVisible={assetsVisible}
            roadsReached={roadsReached}
            roadsReachedVisible={roadsReachedVisible}
            assetAttribution={assetAttribution}
            criticalReach={criticalReach}
            evacZones={evacZones}
            evacZonesVisible={evacZonesVisible}
            isochrones={isochrones}
            isochronesVisible={isochronesVisible}
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
            run209={run209}
            onIncidentNameChange={(name) => updateIncidentField("name", name)}
          />
          </Suspense>
        </div>
      )}

      {/* ── Map area — always mounted so MapLibre doesn't reinitialize on tab switch ─── */}
      <main className="map-area" style={activeTab === "eoc" ? { display: "none" } : {}}>
        {/* Telemetry strip — floating glass chips over the map */}
        {lastRunParams && (
          <div className="telemetry-strip">
            <div className="tel-chip">
              <span className="tel-label">Wind</span>
              <span className="tel-value">{lastRunParams.weather.wind_speed}<span className="tel-unit"> km/h</span></span>
              <span className="tel-dir">{["N","NE","E","SE","S","SW","W","NW"][Math.round(lastRunParams.weather.wind_direction / 45) % 8]}</span>
            </div>
            <div className="tel-chip">
              <span className="tel-label">Humidity</span>
              <span className="tel-value">{lastRunParams.weather.relative_humidity}<span className="tel-unit">%</span></span>
            </div>
            <div className="tel-chip">
              <span className="tel-label">Temp</span>
              <span className="tel-value">{lastRunParams.weather.temperature}<span className="tel-unit">°C</span></span>
            </div>
            <div className="tel-chip tel-chip-danger">
              <span className="tel-label">FWI</span>
              <span className="tel-value">{lastRunParams.fwi_value.toFixed(0)}</span>
              <span
                className="tel-danger"
                style={{ background: fwiClassColor(lastRunParams.fwi_value), color: fwiClassTextColor(lastRunParams.fwi_value) }}
              >
                {lastRunParams.danger_rating}
              </span>
            </div>
          </div>
        )}
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
            arrivalOutlines={arrivalOutlines}
            arrivalOutlinesVisible={arrivalOutlinesVisible}
            onSetEvacTier={handleSetEvacTier}
            evacTierRecords={evacTierRecords}
            ensemble={ensembleMap}
            recon={recon.mapLayers}
            onReconDrawPoint={recon.addPoint}
            onReconDrawFinish={recon.finishDrawing}
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
          headline={
            <EnsemblePanel
              state={ensemble}
              selectedMinutes={selectedMinutes}
              scenarioStart={scenarioStart}
              toggles={ensToggles}
              onToggle={handleEnsToggle}
            />
          }
          kpiCaption={ensemble.phase !== "off" ? "Single run (P50-like)" : null}
          burningPeriod={runBurningPeriod}
        >
          <FireMetrics
            frame={currentFrame}
            status={status}
            totalFrames={frames.length}
          />
          <EvacStatusPanel
            arrivals={arrivals}
            worstArrivals={worstArrivals}
            scenarioStart={scenarioStart}
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
            hasRun={frames.length > 0}
            shown={shownCategories}
            onToggleCategory={toggleCategory}
            onShowAll={showAllCategories}
            onFocus={focusAsset}
            assetsVisible={assetsVisible}
            onAssetsVisible={setAssetsVisible}
            roadsVisible={roadsReachedVisible}
            onRoadsVisible={setRoadsReachedVisible}
            sources={assetSources}
            loadNote={edmontonNote}
          />
          <EOCSummary
            frames={frames}
            burnProbData={burnProbabilityData}
            runParams={lastRunParams}
            ignitionPoint={ignitionPoint}
            fuelTypeLabel={
              lastRunParams?.fuel_type
                ? `${lastRunParams.fuel_type} — ${FUEL_TYPES[lastRunParams.fuel_type] ?? ""}`
                : undefined
            }
            criticalReach={criticalReach}
            evacZones={evacZones}
            run209={run209}
            incidentName={incident?.name}
          />
        </SituationPanel>
      )}

      {/* ── Timeline along the bottom (hidden in the EOC tab) ── */}
      {activeTab === "simulation" && (
        <div className="bottom-bar">
          <TimeSlider
            frames={frames}
            currentIndex={currentFrameIndex}
            onIndexChange={setFrameIndex}
            scenarioStart={scenarioStart}
            burningPeriod={runBurningPeriod}
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

const SCRATCH_EVAC_KEY = "firesim-v3-evac-tiers";

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
