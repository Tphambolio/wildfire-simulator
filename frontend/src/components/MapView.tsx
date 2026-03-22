/** MapLibre GL map with fire perimeter rendering and basemap toggle.
 *
 * Uses MapLibre GL (open-source, no token required) with
 * OpenStreetMap raster tiles by default. Supports switching between
 * OSM, topo, and satellite (when VITE_MAPBOX_TOKEN is set) basemaps.
 */

import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef, useState, useCallback } from "react";
import type { SimulationFrame, BurnProbabilityResponse } from "../types/simulation";

/** A simple non-modal toast — disappears after 3 s */
function MapToast({ message, onDone }: { message: string; onDone: () => void }) {
  useEffect(() => {
    const t = setTimeout(onDone, 3000);
    return () => clearTimeout(t);
  }, [onDone]);
  return (
    <div style={{
      position: "absolute", bottom: 80, left: "50%", transform: "translateX(-50%)",
      zIndex: 20, background: "rgba(20,30,50,0.92)", color: "#e0e0e0",
      padding: "8px 18px", borderRadius: 20, fontSize: 13, fontWeight: 500,
      border: "1px solid #3a60a0", pointerEvents: "none", whiteSpace: "nowrap",
    }}>
      {message}
    </div>
  );
}

function LocationSearch({ onSelect }: { onSelect: (lat: number, lng: number, name: string) => void }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Array<{ display_name: string; lat: string; lon: string }>>([]);
  const [loading, setLoading] = useState(false);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const search = (q: string) => {
    if (q.length < 3) { setResults([]); return; }
    setLoading(true);
    fetch(`https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(q)}&limit=5`)
      .then(r => r.json())
      .then(data => { setResults(data); setLoading(false); })
      .catch(() => setLoading(false));
  };

  const handleInput = (value: string) => {
    setQuery(value);
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    timeoutRef.current = setTimeout(() => search(value), 400);
  };

  return (
    <div className="location-search" style={{
      position: "absolute", top: 10, left: 10, zIndex: 10,
      background: "rgba(20, 30, 50, 0.92)", borderRadius: 6, padding: "6px",
      minWidth: 260, maxWidth: 320,
    }}>
      <input
        type="text"
        placeholder="Search location..."
        value={query}
        onChange={e => handleInput(e.target.value)}
        style={{
          width: "100%", padding: "6px 10px", border: "1px solid #445",
          borderRadius: 4, background: "#1a2540", color: "#e0e0e0",
          fontSize: 13, outline: "none", boxSizing: "border-box",
        }}
      />
      {loading && <div style={{ color: "#888", fontSize: 12, padding: "4px 6px" }}>Searching...</div>}
      {results.length > 0 && (
        <div style={{ maxHeight: 180, overflowY: "auto" }}>
          {results.map((r, i) => (
            <div key={i} onClick={() => {
              onSelect(parseFloat(r.lat), parseFloat(r.lon), r.display_name);
              setResults([]); setQuery(r.display_name.split(",")[0]);
            }} style={{
              padding: "5px 8px", cursor: "pointer", fontSize: 12,
              color: "#ccc", borderTop: "1px solid #334",
            }}
            onMouseEnter={e => (e.currentTarget.style.background = "#2a3a5a")}
            onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
            >
              {r.display_name}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_TOKEN || "";

type BasemapId = string;

type BasemapConfig = { label: string; style: () => maplibregl.StyleSpecification };

const BASEMAPS: Record<string, BasemapConfig> = {
  osm: {
    label: "Street",
    style: () => ({
      version: 8 as const,
      name: "OSM",
      sources: {
        osm: {
          type: "raster" as const,
          tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
          tileSize: 256,
          attribution: "&copy; OpenStreetMap contributors",
        },
      },
      layers: [{ id: "osm-tiles", type: "raster" as const, source: "osm", minzoom: 0, maxzoom: 19 }],
    }),
  },
  topo: {
    label: "Topo",
    style: () => ({
      version: 8 as const,
      name: "Topo",
      sources: {
        topo: {
          type: "raster" as const,
          tiles: ["https://tile.opentopomap.org/{z}/{x}/{y}.png"],
          tileSize: 256,
          attribution: "&copy; OpenTopoMap &copy; OpenStreetMap",
          maxzoom: 17,
        },
      },
      layers: [{ id: "topo-tiles", type: "raster" as const, source: "topo", minzoom: 0, maxzoom: 17 }],
    }),
  },
};

// Add satellite option only when Mapbox token is available
if (MAPBOX_TOKEN) {
  BASEMAPS.satellite = {
    label: "Satellite",
    style: () => ({
      version: 8 as const,
      name: "Satellite",
      sources: {
        mapbox: {
          type: "raster" as const,
          tiles: [
            `https://api.mapbox.com/styles/v1/mapbox/satellite-streets-v12/tiles/{z}/{x}/{y}?access_token=${MAPBOX_TOKEN}`,
          ],
          tileSize: 512,
          attribution: "&copy; Mapbox &copy; OpenStreetMap",
        },
      },
      layers: [{ id: "mapbox-tiles", type: "raster" as const, source: "mapbox" }],
    }),
  };
}

interface MapViewProps {
  frames: SimulationFrame[];
  currentFrameIndex: number;
  onMapClick: (lat: number, lng: number) => void;
  onClearIgnition?: () => void;
  ignitionPoint: { lat: number; lng: number } | null;
  burnProbabilityData?: BurnProbabilityResponse | null;
  showBurnProbView?: boolean;
}

export default function MapView({
  frames,
  currentFrameIndex,
  onMapClick,
  onClearIgnition,
  ignitionPoint,
  burnProbabilityData,
  showBurnProbView = false,
}: MapViewProps) {
  const mapContainer = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const markerRef = useRef<maplibregl.Marker | null>(null);
  const [mapReady, setMapReady] = useState(false);
  const [basemap, setBasemap] = useState<BasemapId>("osm");
  // Ignition placement mode — true while operator is picking a start point
  const [ignitionMode, setIgnitionMode] = useState(!ignitionPoint);
  const ignitionModeRef = useRef(!ignitionPoint);
  const [toast, setToast] = useState<string | null>(null);
  // Counter incremented each time fire layers are (re-)added to the map.
  // The perimeter-update effect depends on this so it re-runs after
  // basemap switches that destroy and recreate sources.
  const [fireLayersVersion, setFireLayersVersion] = useState(0);
  const prevBasemapRef = useRef<BasemapId>("osm");
  const [showSpotFires, setShowSpotFires] = useState(true);
  const pulseAnimRef = useRef<number | null>(null);
  const spotPopupRef = useRef<maplibregl.Popup | null>(null);

  const addFireLayers = useCallback((m: maplibregl.Map) => {
    // Remove stale sources if they somehow survived (defensive)
    if (m.getSource("fire-perimeter")) {
      if (m.getLayer("fire-outline")) m.removeLayer("fire-outline");
      if (m.getLayer("fire-fill")) m.removeLayer("fire-fill");
      m.removeSource("fire-perimeter");
    }
    if (m.getSource("fire-history")) {
      if (m.getLayer("fire-history-outline")) m.removeLayer("fire-history-outline");
      m.removeSource("fire-history");
    }
    // Remove CA heatmap layers if present
    if (m.getSource("fire-heatmap")) {
      if (m.getLayer("fire-heatmap-layer")) m.removeLayer("fire-heatmap-layer");
      if (m.getLayer("fire-cells-layer")) m.removeLayer("fire-cells-layer");
      m.removeSource("fire-heatmap");
    }
    // Remove burn probability layer if present
    if (m.getSource("burn-probability")) {
      if (m.getLayer("burn-probability-layer")) m.removeLayer("burn-probability-layer");
      m.removeSource("burn-probability");
    }

    m.addSource("fire-perimeter", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });

    m.addLayer({
      id: "fire-fill",
      type: "fill",
      source: "fire-perimeter",
      paint: {
        "fill-color": [
          "interpolate",
          ["linear"],
          ["get", "hfi"],
          0, "#ffeb3b",
          2000, "#ff9800",
          4000, "#f44336",
          10000, "#b71c1c",
        ],
        "fill-opacity": 0.5,
      },
    });

    m.addLayer({
      id: "fire-outline",
      type: "line",
      source: "fire-perimeter",
      paint: {
        "line-color": "#ff3d00",
        "line-width": 2.5,
      },
    });

    m.addSource("fire-history", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });

    m.addLayer(
      {
        id: "fire-history-outline",
        type: "line",
        source: "fire-history",
        paint: {
          "line-color": "#ff9800",
          "line-width": 1,
          "line-opacity": 0.5,
          "line-dasharray": [2, 2],
        },
      },
      "fire-fill"
    );

    // CA heatmap source + layers (for cellular automaton mode)
    m.addSource("fire-heatmap", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });

    m.addLayer({
      id: "fire-heatmap-layer",
      type: "heatmap",
      source: "fire-heatmap",
      paint: {
        "heatmap-weight": ["interpolate", ["linear"], ["get", "intensity"], 0, 0, 15000, 1],
        "heatmap-intensity": 1.5,
        "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 10, 8, 14, 25, 16, 40],
        "heatmap-color": [
          "interpolate", ["linear"], ["heatmap-density"],
          0, "rgba(0,0,0,0)",
          0.1, "rgba(255,255,180,0.4)",
          0.3, "rgba(255,200,50,0.6)",
          0.5, "rgba(255,140,20,0.7)",
          0.7, "rgba(220,60,20,0.8)",
          0.9, "rgba(180,30,10,0.9)",
          1.0, "rgba(120,10,5,1.0)",
        ],
        "heatmap-opacity": 0.85,
      },
    });

    // Individual cell circles (visible at high zoom)
    m.addLayer({
      id: "fire-cells-layer",
      type: "circle",
      source: "fire-heatmap",
      minzoom: 14,
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 14, 3, 17, 10],
        "circle-color": [
          "interpolate", ["linear"], ["get", "intensity"],
          0, "#ffeb3b",
          2000, "#ff9800",
          4000, "#f44336",
          8000, "#b71c1c",
          15000, "#4a0000",
        ],
        "circle-opacity": 0.7,
        "circle-stroke-width": 0.5,
        "circle-stroke-color": "#333",
      },
    });

    // Burn probability heatmap source + layer (Monte Carlo mode)
    // Color scale: white (0%) → yellow (30%) → orange (60%) → red (90%+)
    if (m.getSource("burn-probability")) {
      if (m.getLayer("burn-probability-layer")) m.removeLayer("burn-probability-layer");
      if (m.getLayer("burn-probability-circles")) m.removeLayer("burn-probability-circles");
      m.removeSource("burn-probability");
    }
    m.addSource("burn-probability", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });
    m.addLayer({
      id: "burn-probability-layer",
      type: "heatmap",
      source: "burn-probability",
      maxzoom: 14,
      paint: {
        "heatmap-weight": ["interpolate", ["linear"], ["get", "probability"], 0, 0, 1, 1],
        "heatmap-intensity": 1.8,
        "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 8, 12, 11, 22, 14, 40],
        "heatmap-color": [
          "interpolate", ["linear"], ["heatmap-density"],
          0,    "rgba(255,255,255,0)",
          0.15, "rgba(255,255,200,0.5)",
          0.35, "rgba(255,230,50,0.7)",
          0.55, "rgba(255,150,0,0.82)",
          0.75, "rgba(230,60,10,0.90)",
          1.0,  "rgba(170,10,5,0.95)",
        ],
        "heatmap-opacity": 0.9,
      },
    });
    // Per-cell circle layer at high zoom for exact probability display
    m.addLayer({
      id: "burn-probability-circles",
      type: "circle",
      source: "burn-probability",
      minzoom: 13,
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 13, 4, 16, 14],
        "circle-color": [
          "interpolate", ["linear"], ["get", "probability"],
          0,    "#ffffff",
          0.1,  "#ffffc0",
          0.3,  "#ffdd00",
          0.6,  "#ff8800",
          0.9,  "#dd2200",
          1.0,  "#880000",
        ],
        "circle-opacity": 0.82,
        "circle-stroke-width": 0.5,
        "circle-stroke-color": "rgba(0,0,0,0.2)",
      },
    });

    // Spot fires source and layers
    if (m.getSource("spot-fires")) {
      if (m.getLayer("spot-fires-pulse")) m.removeLayer("spot-fires-pulse");
      if (m.getLayer("spot-fires-circle")) m.removeLayer("spot-fires-circle");
      m.removeSource("spot-fires");
    }
    m.addSource("spot-fires", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });
    // Outer pulsing ring — radius/opacity animated via requestAnimationFrame
    m.addLayer({
      id: "spot-fires-pulse",
      type: "circle",
      source: "spot-fires",
      paint: {
        "circle-radius": 10,
        "circle-color": "transparent",
        "circle-opacity": 0,
        "circle-stroke-width": 2,
        "circle-stroke-color": "#ff6600",
        "circle-stroke-opacity": 0.8,
      },
    });
    // Inner solid circle
    m.addLayer({
      id: "spot-fires-circle",
      type: "circle",
      source: "spot-fires",
      paint: {
        "circle-radius": 7,
        "circle-color": "#ff4500",
        "circle-stroke-width": 2,
        "circle-stroke-color": "#ffcc00",
        "circle-opacity": 0.95,
        "circle-stroke-opacity": 1,
      },
    });

    // Click handler: show popup with spot fire metadata
    m.on("click", "spot-fires-circle", (e) => {
      if (!e.features || e.features.length === 0) return;
      const props = e.features[0].properties as { distance_m: number; hfi_kw_m: number };
      if (spotPopupRef.current) spotPopupRef.current.remove();
      spotPopupRef.current = new maplibregl.Popup({ closeButton: true, maxWidth: "220px" })
        .setLngLat(e.lngLat)
        .setHTML(
          `<div style="background:#1a2540;color:#e0e0e0;padding:8px 10px;border-radius:4px;font-size:12px;line-height:1.6">
            <strong style="color:#ff9800;font-size:13px">&#x1F525; Spot Fire</strong><br/>
            <span style="color:#aaa">Distance from front:</span> <b>${props.distance_m.toFixed(0)} m</b><br/>
            <span style="color:#aaa">Head fire intensity:</span> <b>${props.hfi_kw_m.toFixed(0)} kW/m</b>
          </div>`
        )
        .addTo(m);
    });
    m.on("mouseenter", "spot-fires-circle", () => { m.getCanvas().style.cursor = "pointer"; });
    m.on("mouseleave", "spot-fires-circle", () => { m.getCanvas().style.cursor = ""; });

    // Start pulse animation
    if (pulseAnimRef.current) cancelAnimationFrame(pulseAnimRef.current);
    let animStart: number | null = null;
    const PULSE_CYCLE = 1400;
    const animatePulse = (ts: number) => {
      if (!animStart) animStart = ts;
      const t = ((ts - animStart) % PULSE_CYCLE) / PULSE_CYCLE;
      const radius = 8 + t * 18;
      const strokeOpacity = (1 - t) * 0.85;
      if (m.getLayer("spot-fires-pulse")) {
        m.setPaintProperty("spot-fires-pulse", "circle-radius", radius);
        m.setPaintProperty("spot-fires-pulse", "circle-stroke-opacity", strokeOpacity);
      }
      pulseAnimRef.current = requestAnimationFrame(animatePulse);
    };
    pulseAnimRef.current = requestAnimationFrame(animatePulse);

    // Signal that fire layers are ready — triggers perimeter data re-apply
    setFireLayersVersion((v) => v + 1);
  }, []);

  // Initialize map
  useEffect(() => {
    if (!mapContainer.current || map.current) return;

    const m = new maplibregl.Map({
      container: mapContainer.current,
      style: BASEMAPS.osm.style(),
      center: [-113.49, 53.55],
      zoom: 11,
    });

    m.addControl(new maplibregl.NavigationControl(), "top-right");

    m.on("load", () => {
      addFireLayers(m);
      m.resize();
      setMapReady(true);
    });

    m.on("click", (e) => {
      if (!ignitionModeRef.current) return;
      onMapClick(e.lngLat.lat, e.lngLat.lng);
      // Exit placement mode after setting ignition
      ignitionModeRef.current = false;
      setIgnitionMode(false);
      m.getCanvas().style.cursor = "";
    });

    map.current = m;

    const resizeTimer = setTimeout(() => m.resize(), 200);

    return () => {
      clearTimeout(resizeTimer);
      if (pulseAnimRef.current) { cancelAnimationFrame(pulseAnimRef.current); pulseAnimRef.current = null; }
      if (spotPopupRef.current) { spotPopupRef.current.remove(); spotPopupRef.current = null; }
      m.remove();
      map.current = null;
    };
  }, []);

  // Switch basemap — only when the user actually changes the basemap
  useEffect(() => {
    if (!map.current || !mapReady) return;
    // Skip on initial render (style already set in constructor)
    if (basemap === prevBasemapRef.current) return;
    prevBasemapRef.current = basemap;

    const entry = BASEMAPS[basemap];
    if (!entry) return;
    map.current.setStyle(entry.style());
    map.current.once("style.load", () => {
      addFireLayers(map.current!);
    });
  }, [basemap, mapReady, addFireLayers]);

  // Sync ignitionMode state → ref (used in map click handler) + cursor
  useEffect(() => {
    ignitionModeRef.current = ignitionMode;
    if (map.current && mapReady) {
      map.current.getCanvas().style.cursor = ignitionMode ? "crosshair" : "";
    }
  }, [ignitionMode, mapReady]);

  // Update ignition marker
  useEffect(() => {
    if (!map.current) return;

    if (markerRef.current) {
      markerRef.current.remove();
      markerRef.current = null;
    }

    if (ignitionPoint) {
      const el = document.createElement("div");
      el.className = "ignition-marker";
      el.innerHTML = `<svg width="24" height="24" viewBox="0 0 24 24" fill="none">
        <circle cx="12" cy="12" r="10" fill="#ff3d00" stroke="white" stroke-width="2"/>
        <text x="12" y="16" text-anchor="middle" fill="white" font-size="12" font-weight="bold">&#x1F525;</text>
      </svg>`;

      markerRef.current = new maplibregl.Marker({ element: el })
        .setLngLat([ignitionPoint.lng, ignitionPoint.lat])
        .addTo(map.current);

      // Pan to ignition without changing zoom level
      map.current.panTo([ignitionPoint.lng, ignitionPoint.lat], { duration: 500 });
    }
  }, [ignitionPoint]);

  // Update fire visualization — auto-selects heatmap (CA) or polygon (Huygens)
  useEffect(() => {
    if (!map.current || !mapReady || frames.length === 0) return;

    const currentFrame = frames[currentFrameIndex];
    if (!currentFrame) return;

    // CA mode: burned_cells present → render as heatmap
    if (currentFrame.burned_cells && currentFrame.burned_cells.length > 0) {
      const heatSrc = map.current.getSource("fire-heatmap") as maplibregl.GeoJSONSource | undefined;
      if (!heatSrc) return;

      // Each frame already contains ALL cumulative cells up to that point.
      // Use the current frame directly — no cross-frame accumulation needed.
      const allCells = currentFrame.burned_cells as Array<{ lat: number; lng: number; intensity: number }>;

      const features: GeoJSON.Feature[] = allCells.map((c) => ({
        type: "Feature" as const,
        geometry: { type: "Point" as const, coordinates: [c.lng, c.lat] },
        properties: { intensity: c.intensity },
      }));

      heatSrc.setData({ type: "FeatureCollection", features });

      // Clear polygon layers (not used in CA mode)
      const perimSrc = map.current.getSource("fire-perimeter") as maplibregl.GeoJSONSource | undefined;
      if (perimSrc) perimSrc.setData({ type: "FeatureCollection", features: [] });
      const histSrc = map.current.getSource("fire-history") as maplibregl.GeoJSONSource | undefined;
      if (histSrc) histSrc.setData({ type: "FeatureCollection", features: [] });

      // Update spot fires for current frame
      const spotSrcCA = map.current.getSource("spot-fires") as maplibregl.GeoJSONSource | undefined;
      if (spotSrcCA) {
        const sfFeatures: GeoJSON.Feature[] = (currentFrame.spot_fires ?? []).map((sf) => ({
          type: "Feature" as const,
          geometry: { type: "Point" as const, coordinates: [sf.lng, sf.lat] },
          properties: { distance_m: sf.distance_m, hfi_kw_m: sf.hfi_kw_m },
        }));
        spotSrcCA.setData({ type: "FeatureCollection", features: sfFeatures });
      }
      return;
    }

    // Huygens mode: polygon perimeter
    const src = map.current.getSource("fire-perimeter") as maplibregl.GeoJSONSource | undefined;
    if (!src) return;
    if (currentFrame.perimeter.length < 3) return;

    const coords = currentFrame.perimeter.map(([lat, lng]) => [lng, lat]);
    if (
      coords.length > 1 &&
      (coords[0][0] !== coords[coords.length - 1][0] ||
        coords[0][1] !== coords[coords.length - 1][1])
    ) {
      coords.push(coords[0]);
    }

    src.setData({
      type: "FeatureCollection",
      features: [{
        type: "Feature",
        geometry: { type: "Polygon", coordinates: [coords] },
        properties: { time_hours: currentFrame.time_hours, area_ha: currentFrame.area_ha, hfi: currentFrame.max_hfi_kw_m },
      }],
    });

    const historySrc = map.current.getSource("fire-history") as maplibregl.GeoJSONSource | undefined;
    if (!historySrc) return;

    const historyFeatures: GeoJSON.Feature[] = frames
      .slice(1, currentFrameIndex)
      .filter((f) => f.perimeter.length >= 3)
      .map((f) => {
        const c = f.perimeter.map(([lat, lng]) => [lng, lat]);
        if (c.length > 1 && (c[0][0] !== c[c.length - 1][0] || c[0][1] !== c[c.length - 1][1])) {
          c.push(c[0]);
        }
        return {
          type: "Feature" as const,
          geometry: { type: "Polygon" as const, coordinates: [c] },
          properties: { time_hours: f.time_hours },
        };
      });

    historySrc.setData({ type: "FeatureCollection", features: historyFeatures });

    // Clear heatmap (not used in Huygens mode)
    const heatSrc = map.current.getSource("fire-heatmap") as maplibregl.GeoJSONSource | undefined;
    if (heatSrc) heatSrc.setData({ type: "FeatureCollection", features: [] });

    // Update spot fires for current frame
    const spotSrc = map.current.getSource("spot-fires") as maplibregl.GeoJSONSource | undefined;
    if (spotSrc) {
      const sfFeatures: GeoJSON.Feature[] = (currentFrame.spot_fires ?? []).map((sf) => ({
        type: "Feature" as const,
        geometry: { type: "Point" as const, coordinates: [sf.lng, sf.lat] },
        properties: { distance_m: sf.distance_m, hfi_kw_m: sf.hfi_kw_m },
      }));
      spotSrc.setData({ type: "FeatureCollection", features: sfFeatures });
    }
  }, [frames, currentFrameIndex, mapReady, fireLayersVersion]);

  // Render burn probability heatmap when Monte Carlo result arrives
  useEffect(() => {
    if (!map.current || !mapReady) return;
    const src = map.current.getSource("burn-probability") as maplibregl.GeoJSONSource | undefined;
    if (!src) return;

    if (!burnProbabilityData) {
      src.setData({ type: "FeatureCollection", features: [] });
      return;
    }

    const { burn_probability, rows, cols, lat_min, lat_max, lng_min, lng_max } = burnProbabilityData;
    const cellLat = (lat_max - lat_min) / rows;
    const cellLng = (lng_max - lng_min) / cols;

    // Convert 2D probability array to GeoJSON points (skip P=0 cells for performance)
    const features: GeoJSON.Feature[] = [];
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const p = burn_probability[r]?.[c] ?? 0;
        if (p <= 0) continue;
        const lat = lat_max - (r + 0.5) * cellLat;
        const lng = lng_min + (c + 0.5) * cellLng;
        features.push({
          type: "Feature",
          geometry: { type: "Point", coordinates: [lng, lat] },
          properties: { probability: p },
        });
      }
    }
    src.setData({ type: "FeatureCollection", features });

    // Auto-zoom to show the burn probability extent
    if (features.length > 0) {
      map.current.fitBounds(
        [[lng_min, lat_min], [lng_max, lat_max]],
        { padding: 40, maxZoom: 12, duration: 800 },
      );
    }
  }, [burnProbabilityData, mapReady, fireLayersVersion]);

  // Toggle spot fire layer visibility
  useEffect(() => {
    if (!map.current || !mapReady) return;
    const visibility = showSpotFires ? "visible" : "none";
    if (map.current.getLayer("spot-fires-circle")) {
      map.current.setLayoutProperty("spot-fires-circle", "visibility", visibility);
      map.current.setLayoutProperty("spot-fires-pulse", "visibility", visibility);
    }
  }, [showSpotFires, mapReady]);

  // Toggle between burn probability view and fire spread view
  useEffect(() => {
    if (!map.current || !mapReady) return;
    const m = map.current;
    const hasBurnData = !!burnProbabilityData && showBurnProbView;

    const burnLayers = ["burn-probability-layer", "burn-probability-circles"];
    const fireLayers = [
      "fire-fill", "fire-outline", "fire-history-outline",
      "fire-heatmap-layer", "fire-cells-layer",
      "spot-fires-circle", "spot-fires-pulse",
    ];

    burnLayers.forEach((id) => {
      if (m.getLayer(id)) m.setLayoutProperty(id, "visibility", hasBurnData ? "visible" : "none");
    });
    fireLayers.forEach((id) => {
      if (m.getLayer(id)) m.setLayoutProperty(id, "visibility", hasBurnData ? "none" : "visible");
    });
  }, [showBurnProbView, burnProbabilityData, mapReady, fireLayersVersion]);

  const flyTo = useCallback((lat: number, lng: number, zoom = 12) => {
    map.current?.flyTo({ center: [lng, lat], zoom, duration: 1500 });
  }, []);

  return (
    <div style={{ width: "100%", height: "100%", position: "relative" }}>
      <div ref={mapContainer} style={{ width: "100%", height: "100%" }} />
      <LocationSearch onSelect={(lat, lng) => flyTo(lat, lng)} />

      {/* Burn Probability Legend */}
      {burnProbabilityData && showBurnProbView && (
        <div className="burn-prob-legend">
          <div className="burn-prob-legend-title">Burn Probability</div>
          <div className="burn-prob-legend-meta">
            {burnProbabilityData.iterations_completed} iterations · {burnProbabilityData.cell_size_m.toFixed(0)} m cells
          </div>
          <div className="burn-prob-legend-scale">
            {[
              { label: "≥90%", color: "#aa0000" },
              { label: "60%",  color: "#ff8800" },
              { label: "30%",  color: "#ffdd00" },
              { label: "10%",  color: "#ffffcc" },
              { label: "0%",   color: "rgba(255,255,255,0.15)" },
            ].map(({ label, color }) => (
              <div key={label} className="burn-prob-legend-row">
                <div className="burn-prob-legend-swatch" style={{ background: color }} />
                <span>{label}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      <div className="basemap-toggle">
        {(Object.keys(BASEMAPS) as BasemapId[]).map((id) => (
          <button
            key={id}
            className={`basemap-btn${basemap === id ? " active" : ""}`}
            onClick={() => setBasemap(id)}
          >
            {BASEMAPS[id].label}
          </button>
        ))}
      </div>
      {/* Ignition placement mode button */}
      <button
        onClick={() => {
          const next = !ignitionMode;
          setIgnitionMode(next);
        }}
        title={ignitionMode ? "Click map to place ignition point (ESC to cancel)" : "Click to enter ignition placement mode"}
        style={{
          position: "absolute",
          bottom: ignitionPoint ? 72 : 72,
          right: 10,
          zIndex: 10,
          padding: "5px 10px",
          background: ignitionMode
            ? "rgba(255, 61, 0, 0.85)"
            : "rgba(20, 30, 50, 0.85)",
          color: "#fff",
          border: ignitionMode ? "2px solid #ff3d00" : "1px solid #445",
          borderRadius: 4,
          cursor: "pointer",
          fontSize: 12,
          fontWeight: 600,
          transition: "all 0.15s",
        }}
      >
        {ignitionMode ? "🔥 Click map to ignite" : ignitionPoint ? "🔥 Move ignition" : "🔥 Place ignition"}
      </button>

      {/* Clear ignition button — shown when ignition is set */}
      {ignitionPoint && onClearIgnition && (
        <button
          onClick={() => {
            onClearIgnition();
            setIgnitionMode(true); // Re-enter placement mode so they can set a new point
          }}
          title="Clear ignition point"
          style={{
            position: "absolute",
            bottom: 108,
            right: 10,
            zIndex: 10,
            padding: "5px 10px",
            background: "rgba(20, 30, 50, 0.85)",
            color: "#aaa",
            border: "1px solid #445",
            borderRadius: 4,
            cursor: "pointer",
            fontSize: 12,
          }}
        >
          ✕ Clear
        </button>
      )}

      <button
        onClick={() => setShowSpotFires((v) => !v)}
        style={{
          position: "absolute",
          bottom: 36,
          right: 10,
          zIndex: 10,
          padding: "5px 10px",
          background: showSpotFires ? "rgba(255, 100, 0, 0.85)" : "rgba(20, 30, 50, 0.85)",
          color: "#fff",
          border: "1px solid #ff6600",
          borderRadius: 4,
          cursor: "pointer",
          fontSize: 12,
          fontWeight: 600,
        }}
      >
        {showSpotFires ? "🔥 Spot Fires ON" : "🔥 Spot Fires OFF"}
      </button>

      {/* Placement mode hint overlay */}
      {ignitionMode && (
        <div style={{
          position: "absolute",
          top: "50%",
          left: "50%",
          transform: "translate(-50%, -50%)",
          pointerEvents: "none",
          zIndex: 5,
          textAlign: "center",
        }}>
          <div style={{
            background: "rgba(20,30,50,0.75)",
            color: "#e0e0e0",
            padding: "10px 20px",
            borderRadius: 8,
            fontSize: 14,
            border: "1px dashed #ff3d00",
          }}>
            Click map to set ignition point
          </div>
        </div>
      )}

      {toast && (
        <MapToast message={toast} onDone={() => setToast(null)} />
      )}
    </div>
  );
}
