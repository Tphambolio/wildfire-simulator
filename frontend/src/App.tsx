/** FireSim V3 — Canadian FBP Wildfire Spread Simulator */

import { useCallback, useState, useMemo, useRef } from "react";
import MapView from "./components/MapView";
import WeatherPanel from "./components/WeatherPanel";
import type { RunParams } from "./components/WeatherPanel";
import FireMetrics from "./components/FireMetrics";
import EOCSummary from "./components/EOCSummary";
import TimeSlider from "./components/TimeSlider";
import OverlayPanel from "./components/OverlayPanel";
import type { OverlayLayers, LayerType } from "./components/OverlayPanel";
import ScenarioPanel from "./components/ScenarioPanel";
import EvacZonesPanel from "./components/EvacZonesPanel";
import { FUEL_TYPES } from "./types/simulation";
import { useSimulation } from "./hooks/useSimulation";
import { useScenarios } from "./hooks/useScenarios";
import { computeBurnProbability } from "./services/api";
import type { SimulationCreate, SimulationFrame, BurnProbabilityRequest, BurnProbabilityResponse, ScenarioConfig } from "./types/simulation";
import { computeEvacZones } from "./utils/evacZones";
import type { EvacZoneLabel } from "./utils/evacZones";
import IsochronePanel from "./components/IsochronePanel";
import { computeIsochrones, DEFAULT_ISO_HOURS } from "./utils/isochrones";

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

// ── At-risk computation ──────────────────────────────────────────────────────

/** Returns true if the coordinate [lng, lat] falls in a cell with P ≥ threshold. */
function isAtRisk(
  lng: number,
  lat: number,
  data: BurnProbabilityResponse,
  threshold: number,
): boolean {
  const { burn_probability, rows, cols, lat_min, lat_max, lng_min, lng_max } = data;
  if (lat < lat_min || lat > lat_max || lng < lng_min || lng > lng_max) return false;
  const cellLat = (lat_max - lat_min) / rows;
  const cellLng = (lng_max - lng_min) / cols;
  const r = Math.max(0, Math.min(rows - 1, Math.floor((lat_max - lat) / cellLat)));
  const c = Math.max(0, Math.min(cols - 1, Math.floor((lng - lng_min) / cellLng)));
  return (burn_probability[r]?.[c] ?? 0) >= threshold;
}

/** Check if any coordinate in a coordinate array is at-risk. */
function coordsAtRisk(coords: number[][], data: BurnProbabilityResponse, threshold: number): boolean {
  return coords.some(([lng, lat]) => isAtRisk(lng, lat, data, threshold));
}

/**
 * Annotate each feature with `_at_risk: 1 | 0` based on whether any of its
 * vertices/coordinates fall in a burn-probability cell at or above `threshold`.
 * Returns a new FeatureCollection with counts.
 */
function annotateAndCount(
  fc: GeoJSON.FeatureCollection,
  data: BurnProbabilityResponse,
  threshold = 0.5,
): { annotated: GeoJSON.FeatureCollection; count: number } {
  let count = 0;
  const features = fc.features.map((f) => {
    let atRisk = false;
    const g = f.geometry;
    if (g.type === "Point") {
      atRisk = isAtRisk(g.coordinates[0], g.coordinates[1], data, threshold);
    } else if (g.type === "MultiPoint") {
      atRisk = g.coordinates.some(([lng, lat]) => isAtRisk(lng, lat, data, threshold));
    } else if (g.type === "LineString") {
      atRisk = coordsAtRisk(g.coordinates as number[][], data, threshold);
    } else if (g.type === "MultiLineString") {
      atRisk = g.coordinates.some((line) => coordsAtRisk(line as number[][], data, threshold));
    } else if (g.type === "Polygon") {
      atRisk = coordsAtRisk(g.coordinates[0] as number[][], data, threshold);
    } else if (g.type === "MultiPolygon") {
      atRisk = g.coordinates.some((poly) =>
        coordsAtRisk(poly[0] as number[][], data, threshold)
      );
    }
    if (atRisk) count++;
    return { ...f, properties: { ...(f.properties ?? {}), _at_risk: atRisk ? 1 : 0 } };
  });
  return { annotated: { ...fc, features }, count };
}

// ── Default overlay state ────────────────────────────────────────────────────

const DEFAULT_OVERLAY_LAYERS: OverlayLayers = {
  roads: { data: null, visible: true },
  communities: { data: null, visible: true },
  infrastructure: { data: null, visible: true },
};

export default function App() {
  const [ignitionPoint, setIgnitionPoint] = useState<{
    lat: number;
    lng: number;
  } | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [burnProbabilityData, setBurnProbabilityData] = useState<BurnProbabilityResponse | null>(null);
  const [burnProbRunning, setBurnProbRunning] = useState(false);
  const [burnProbError, setBurnProbError] = useState<string | null>(null);
  const [showBurnProbView, setShowBurnProbView] = useState(false);
  const [lastRunParams, setLastRunParams] = useState<RunParams | null>(null);
  const [overlayLayers, setOverlayLayers] = useState<OverlayLayers>(DEFAULT_OVERLAY_LAYERS);
  const [evacZonesVisible, setEvacZonesVisible] = useState(true);
  const [evacZoneScales, setEvacZoneScales] = useState<Record<EvacZoneLabel, number>>({
    Order: 1, Alert: 1, Watch: 1,
  });
  const [isochronesVisible, setIsochronesVisible] = useState(true);
  const [isoTargetHours, setIsoTargetHours] = useState<number[]>(DEFAULT_ISO_HOURS);

  // ── Scenario management ───────────────────────────────────────────────────
  const { scenarios, saveScenario, deleteScenario, exportScenario, importScenario } = useScenarios();
  const [scenarioToLoad, setScenarioToLoad] = useState<ScenarioConfig | null>(null);
  const currentConfigRef = useRef<Omit<ScenarioConfig, "id" | "createdAt" | "name" | "description"> | null>(null);

  // Compute at-risk annotations whenever burn prob data or overlay data changes
  const overlayAnnotated = useMemo(() => {
    const annotate = (data: GeoJSON.FeatureCollection | null) =>
      data && burnProbabilityData
        ? annotateAndCount(data, burnProbabilityData)
        : { annotated: data, count: 0 };
    return {
      roads: annotate(overlayLayers.roads.data),
      communities: annotate(overlayLayers.communities.data),
      infrastructure: annotate(overlayLayers.infrastructure.data),
    };
  }, [burnProbabilityData, overlayLayers]);

  const overlayAtRiskCounts = useMemo(() => ({
    roads: overlayAnnotated.roads.count,
    communities: overlayAnnotated.communities.count,
    infrastructure: overlayAnnotated.infrastructure.count,
  }), [overlayAnnotated]);

  const handleEvacScaleChange = useCallback((label: EvacZoneLabel, scale: number) => {
    setEvacZoneScales((prev) => ({ ...prev, [label]: scale }));
  }, []);

  const handleOverlayLoad = useCallback((type: LayerType, data: GeoJSON.FeatureCollection) => {
    setOverlayLayers((prev) => ({ ...prev, [type]: { ...prev[type], data } }));
  }, []);

  const handleOverlayToggle = useCallback((type: LayerType, visible: boolean) => {
    setOverlayLayers((prev) => ({ ...prev, [type]: { ...prev[type], visible } }));
  }, []);

  const handleOverlayClear = useCallback((type: LayerType) => {
    setOverlayLayers((prev) => ({ ...prev, [type]: { data: null, visible: true } }));
  }, []);

  const {
    status,
    frames,
    currentFrameIndex,
    currentFrame,
    isRunning,
    isPaused,
    startSimulation,
    startMultiDaySimulation,
    setFrameIndex,
    pauseSimulation,
    resumeSimulation,
    cancelSimulation,
    error,
  } = useSimulation();

  // Compute evac zones from simulation frames (updates live as frames arrive)
  const evacZones = useMemo(
    () => computeEvacZones(frames, overlayLayers.communities.data, evacZoneScales),
    [frames, overlayLayers.communities.data, evacZoneScales],
  );

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

  const handleStartSimulation = useCallback(
    (params: SimulationCreate) => {
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
    <div className="app">
      <header className="app-header">
        <h1>FireSim V3</h1>
        <span className="subtitle">Canadian FBP Wildfire Spread Simulator</span>
        <button
          className="sidebar-toggle"
          onClick={() => setSidebarOpen(!sidebarOpen)}
          aria-label="Toggle sidebar"
        >
          {sidebarOpen ? "\u2715" : "\u2630"}
        </button>
        {isRunning && !isPaused && (
          <button className="btn-control btn-pause" onClick={pauseSimulation} title="Pause simulation">
            &#9646;&#9646; Pause
          </button>
        )}
        {isPaused && (
          <>
            <button className="btn-control btn-resume" onClick={resumeSimulation} title="Resume simulation">
              &#9654; Resume
            </button>
            <button className="btn-control btn-cancel" onClick={cancelSimulation} title="Cancel simulation">
              &#9632; Cancel
            </button>
          </>
        )}
        {status === "completed" && frames.length > 0 && (
          <button
            className="btn-control btn-export"
            onClick={() => exportPerimeterGeoJSON(frames, ignitionPoint)}
            title="Export all perimeter frames as GeoJSON FeatureCollection"
          >
            Export GeoJSON
          </button>
        )}
        {burnProbabilityData && (
          <button
            className={`btn-control btn-view-toggle${showBurnProbView ? " active" : ""}`}
            onClick={() => setShowBurnProbView((v) => !v)}
            title="Toggle between burn probability heatmap and fire spread view"
          >
            {showBurnProbView ? "Prob View" : "Spread View"}
          </button>
        )}
        {burnProbabilityData && !burnProbRunning && (
          <button
            className="btn-control btn-export"
            onClick={() => exportBurnProbGeoJSON(burnProbabilityData, lastRunParams, ignitionPoint)}
            title="Export burn probability contours (25/50/75%) as GeoJSON — compatible with QGIS, ArcGIS, GPS units"
          >
            Export BP GeoJSON
          </button>
        )}
        {showBurnProbView && lastRunParams && (
          <div className="run-params-badge" title="Weather conditions used for this burn probability run">
            <span>{lastRunParams.weather.wind_speed} km/h {["N","NE","E","SE","S","SW","W","NW"][Math.round(lastRunParams.weather.wind_direction / 45) % 8]}</span>
            <span>·</span>
            <span>FFMC {lastRunParams.fwi.ffmc}</span>
            <span>·</span>
            <span>FWI {lastRunParams.fwi_value.toFixed(1)}</span>
            <span
              className="run-params-danger"
              style={{
                background:
                  lastRunParams.fwi_value >= 30 ? "#b71c1c" :
                  lastRunParams.fwi_value >= 20 ? "#e65100" :
                  lastRunParams.fwi_value >= 10 ? "#f57f17" :
                  lastRunParams.fwi_value >= 5  ? "#558b2f" : "#2e7d32",
              }}
            >
              {lastRunParams.danger_rating}
            </span>
            <span>· {lastRunParams.n_iterations} iter · {lastRunParams.duration_hours}h</span>
          </div>
        )}
        {status && (
          <span className={`status-badge status-${status}`}>
            {status}
          </span>
        )}
      </header>

      <div className="app-content">
        <aside className={`sidebar${sidebarOpen ? "" : " collapsed"}`}>
          <WeatherPanel
            onStartSimulation={handleStartSimulation}
            onStartMultiDaySimulation={startMultiDaySimulation}
            onComputeBurnProbability={handleComputeBurnProbability}
            onRunParams={handleRunParams}
            ignitionPoint={ignitionPoint}
            isRunning={isRunning}
            burnProbRunning={burnProbRunning}
            scenarioToLoad={scenarioToLoad}
            onConfigSnapshot={handleConfigSnapshot}
          />
          <FireMetrics
            frame={currentFrame}
            status={status}
            totalFrames={frames.length}
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
            atRiskCounts={overlayAtRiskCounts}
            overlayRoads={overlayAnnotated.roads.annotated as GeoJSON.FeatureCollection | null}
            overlayCommunities={overlayAnnotated.communities.annotated as GeoJSON.FeatureCollection | null}
            overlayInfrastructure={overlayAnnotated.infrastructure.annotated as GeoJSON.FeatureCollection | null}
            evacZones={evacZones}
          />
          <OverlayPanel
            layers={overlayLayers}
            atRiskCounts={overlayAtRiskCounts}
            onLayerLoad={handleOverlayLoad}
            onLayerToggle={handleOverlayToggle}
            onLayerClear={handleOverlayClear}
          />
          <EvacZonesPanel
            zones={evacZones}
            visible={evacZonesVisible}
            scales={evacZoneScales}
            onToggleVisible={setEvacZonesVisible}
            onScaleChange={handleEvacScaleChange}
          />
          <IsochronePanel
            isochrones={isochrones}
            visible={isochronesVisible}
            targetHours={isoTargetHours}
            onToggleVisible={setIsochronesVisible}
            onTargetHoursChange={setIsoTargetHours}
          />
          <ScenarioPanel
            scenarios={scenarios}
            currentConfig={currentConfigRef.current ?? {
              ignitionPoint,
              weather: { wind_speed: 20, wind_direction: 270, temperature: 25, relative_humidity: 30, precipitation_24h: 0 },
              fwi: { ffmc: 90, dmc: 45, dc: 300 },
              fuelType: "C2",
              useEdmontonGrid: false,
              useSyntheticCA: false,
              enableSpotting: false,
              spottingIntensity: 1.0,
              includeWater: true,
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
        </aside>

        <main className="map-area">
          <MapView
            frames={frames}
            currentFrameIndex={currentFrameIndex}
            onMapClick={handleMapClick}
            onClearIgnition={handleClearIgnition}
            ignitionPoint={ignitionPoint}
            burnProbabilityData={burnProbabilityData}
            showBurnProbView={showBurnProbView}
            overlayRoads={overlayAnnotated.roads.annotated as GeoJSON.FeatureCollection | null}
            overlayRoadsVisible={overlayLayers.roads.visible}
            overlayCommunities={overlayAnnotated.communities.annotated as GeoJSON.FeatureCollection | null}
            overlayCommunitiesVisible={overlayLayers.communities.visible}
            overlayInfrastructure={overlayAnnotated.infrastructure.annotated as GeoJSON.FeatureCollection | null}
            overlayInfrastructureVisible={overlayLayers.infrastructure.visible}
            evacZones={evacZones}
            evacZonesVisible={evacZonesVisible}
            isochrones={isochrones}
            isochronesVisible={isochronesVisible}
          />
          <TimeSlider
            frames={frames}
            currentIndex={currentFrameIndex}
            onIndexChange={setFrameIndex}
          />
        </main>
      </div>

      {error && (
        <div className="error-toast">
          {error}
        </div>
      )}
      {burnProbError && (
        <div className="error-toast">
          Burn probability: {burnProbError}
        </div>
      )}
    </div>
  );
}
