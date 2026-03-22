/** FireSim V3 — Canadian FBP Wildfire Spread Simulator */

import { useCallback, useState } from "react";
import MapView from "./components/MapView";
import WeatherPanel from "./components/WeatherPanel";
import FireMetrics from "./components/FireMetrics";
import TimeSlider from "./components/TimeSlider";
import { useSimulation } from "./hooks/useSimulation";
import { computeBurnProbability } from "./services/api";
import type { SimulationCreate, SimulationFrame, BurnProbabilityResponse } from "./types/simulation";

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

  const handleStartSimulation = useCallback(
    (params: SimulationCreate) => {
      startSimulation(params);
    },
    [startSimulation]
  );

  const handleComputeBurnProbability = useCallback(
    async (params: import("./types/simulation").BurnProbabilityRequest) => {
      setBurnProbRunning(true);
      setBurnProbError(null);
      setBurnProbabilityData(null);
      try {
        const result = await computeBurnProbability(params);
        setBurnProbabilityData(result);
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
            ignitionPoint={ignitionPoint}
            burnProbabilityData={burnProbabilityData}
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
