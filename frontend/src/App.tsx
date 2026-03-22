/** FireSim V3 — Canadian FBP Wildfire Spread Simulator */

import { useCallback, useState } from "react";
import MapView from "./components/MapView";
import WeatherPanel from "./components/WeatherPanel";
import type { RunParams } from "./components/WeatherPanel";
import FireMetrics from "./components/FireMetrics";
import TimeSlider from "./components/TimeSlider";
import { useSimulation } from "./hooks/useSimulation";
import { computeBurnProbability } from "./services/api";
import type { SimulationCreate, SimulationFrame, BurnProbabilityRequest, BurnProbabilityResponse } from "./types/simulation";

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

  const {
    status,
    frames,
    currentFrameIndex,
    currentFrame,
    isRunning,
    isPaused,
    startSimulation,
    setFrameIndex,
    pauseSimulation,
    resumeSimulation,
    cancelSimulation,
    error,
  } = useSimulation();

  const handleMapClick = useCallback((lat: number, lng: number) => {
    setIgnitionPoint({ lat, lng });
  }, []);

  const handleClearIgnition = useCallback(() => {
    setIgnitionPoint(null);
  }, []);

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
            onComputeBurnProbability={handleComputeBurnProbability}
            onRunParams={handleRunParams}
            ignitionPoint={ignitionPoint}
            isRunning={isRunning}
            burnProbRunning={burnProbRunning}
          />
          <FireMetrics
            frame={currentFrame}
            status={status}
            totalFrames={frames.length}
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
