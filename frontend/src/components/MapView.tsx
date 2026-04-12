/** MapLibre GL map — AIMS Console all-hazards COP.
 *
 * Uses MapLibre GL (open-source, no token required) with
 * OpenStreetMap raster tiles by default. Supports switching between
 * OSM, topo, and satellite (when VITE_MAPBOX_TOKEN is set) basemaps.
 */

import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef, useState, useCallback } from "react";

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
  onMapClick: (lat: number, lng: number) => void;
  onClearIgnition?: () => void;
  ignitionPoint: { lat: number; lng: number } | null;
  overlayRoads?: GeoJSON.FeatureCollection | null;
  overlayRoadsVisible?: boolean;
  overlayCommunities?: GeoJSON.FeatureCollection | null;
  overlayCommunitiesVisible?: boolean;
  overlayInfrastructure?: GeoJSON.FeatureCollection | null;
  overlayInfrastructureVisible?: boolean;
  /** When true: disables click-to-set-location and hides the map controls panel */
  readOnly?: boolean;
  /** Called with the maplibregl.Map instance once the map has loaded */
  mapRefCallback?: (m: maplibregl.Map) => void;
}

export default function MapView({
  onMapClick,
  onClearIgnition,
  ignitionPoint,
  overlayRoads = null,
  overlayRoadsVisible = true,
  overlayCommunities = null,
  overlayCommunitiesVisible = true,
  overlayInfrastructure = null,
  overlayInfrastructureVisible = true,
  readOnly = false,
  mapRefCallback,
}: MapViewProps) {
  const mapContainer = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const markerRef = useRef<maplibregl.Marker | null>(null);
  const [mapReady, setMapReady] = useState(false);
  const [basemap, setBasemap] = useState<BasemapId>("osm");
  const readOnlyRef = useRef(readOnly);
  // Location placement mode — true while operator is picking an incident location
  const [ignitionMode, setIgnitionMode] = useState(!ignitionPoint);
  const ignitionModeRef = useRef(!ignitionPoint);
  const [toast, setToast] = useState<string | null>(null);
  const prevBasemapRef = useRef<BasemapId>("osm");

  const addMapLayers = useCallback((m: maplibregl.Map) => {
    // ── Infrastructure overlay layers ──────────────────────────────────────
    // Roads (LineString)
    if (m.getSource("overlay-roads")) {
      if (m.getLayer("overlay-roads-line")) m.removeLayer("overlay-roads-line");
      m.removeSource("overlay-roads");
    }
    m.addSource("overlay-roads", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    m.addLayer({
      id: "overlay-roads-line",
      type: "line",
      source: "overlay-roads",
      paint: {
        "line-color": ["case", ["==", ["get", "_at_risk"], 1], "#ff3d00", "#4fc3f7"],
        "line-width": ["case", ["==", ["get", "_at_risk"], 1], 3, 1.5],
        "line-opacity": ["case", ["==", ["get", "_at_risk"], 1], 0.95, 0.65],
      },
    });

    // Communities (Polygon)
    if (m.getSource("overlay-communities")) {
      if (m.getLayer("overlay-communities-fill")) m.removeLayer("overlay-communities-fill");
      if (m.getLayer("overlay-communities-outline")) m.removeLayer("overlay-communities-outline");
      m.removeSource("overlay-communities");
    }
    m.addSource("overlay-communities", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    m.addLayer({
      id: "overlay-communities-fill",
      type: "fill",
      source: "overlay-communities",
      paint: {
        "fill-color": ["case", ["==", ["get", "_at_risk"], 1], "#ff3d00", "#26c6da"],
        "fill-opacity": ["case", ["==", ["get", "_at_risk"], 1], 0.25, 0.12],
      },
    });
    m.addLayer({
      id: "overlay-communities-outline",
      type: "line",
      source: "overlay-communities",
      paint: {
        "line-color": ["case", ["==", ["get", "_at_risk"], 1], "#ff3d00", "#26c6da"],
        "line-width": ["case", ["==", ["get", "_at_risk"], 1], 2.5, 1.5],
        "line-opacity": 0.9,
        "line-dasharray": ["case", ["==", ["get", "_at_risk"], 1],
          ["literal", [1, 0]], ["literal", [3, 2]]],
      },
    });

    // Infrastructure points
    if (m.getSource("overlay-infra")) {
      if (m.getLayer("overlay-infra-circle")) m.removeLayer("overlay-infra-circle");
      m.removeSource("overlay-infra");
    }
    m.addSource("overlay-infra", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    m.addLayer({
      id: "overlay-infra-circle",
      type: "circle",
      source: "overlay-infra",
      paint: {
        "circle-radius": ["case", ["==", ["get", "_at_risk"], 1], 8, 6],
        "circle-color": ["case", ["==", ["get", "_at_risk"], 1], "#ff3d00", "#29b6f6"],
        "circle-stroke-width": 2,
        "circle-stroke-color": ["case", ["==", ["get", "_at_risk"], 1], "#ffcc00", "#ffffff"],
        "circle-opacity": 0.92,
      },
    });

    // Click handler for infrastructure points — show name/type popup
    m.on("click", "overlay-infra-circle", (e) => {
      if (!e.features || !e.features.length) return;
      const props = e.features[0].properties as Record<string, unknown>;
      const name = (props.name ?? props.label ?? props.NAME ?? "Infrastructure point") as string;
      const type = (props.type ?? props.TYPE ?? "") as string;
      const atRisk = props._at_risk === 1;
      new maplibregl.Popup({ closeButton: true, maxWidth: "200px" })
        .setLngLat(e.lngLat)
        .setHTML(
          `<div style="background:#1a2540;color:#e0e0e0;padding:8px 10px;border-radius:4px;font-size:12px">
            <strong style="color:${atRisk ? "#ff6600" : "#29b6f6"}">${name}</strong><br/>
            ${type ? `<span style="color:#aaa">${type}</span><br/>` : ""}
            ${atRisk ? '<span style="color:#ff6600;font-weight:700">⚠ At-risk (P ≥ 50%)</span>' : ""}
          </div>`
        )
        .addTo(m);
    });
    m.on("mouseenter", "overlay-infra-circle", () => { m.getCanvas().style.cursor = "pointer"; });
    m.on("mouseleave", "overlay-infra-circle", () => { m.getCanvas().style.cursor = ""; });

    // ── Evacuation zone layers ─────────────────────────────────────────────
    // Three zones rendered outermost→innermost so Order is on top.
    for (const [zoneId, color] of [
      ["evac-watch",  "#f9a825"],
      ["evac-alert",  "#f57c00"],
      ["evac-order",  "#d32f2f"],
    ] as Array<[string, string]>) {
      if (m.getSource(zoneId)) {
        if (m.getLayer(`${zoneId}-fill`))    m.removeLayer(`${zoneId}-fill`);
        if (m.getLayer(`${zoneId}-outline`)) m.removeLayer(`${zoneId}-outline`);
        m.removeSource(zoneId);
      }
      m.addSource(zoneId, { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      m.addLayer({
        id: `${zoneId}-fill`,
        type: "fill",
        source: zoneId,
        paint: {
          "fill-color": color,
          "fill-opacity": 0.28,
          // Suppress the auto 1px outline — self-intersecting Huygens vertices
          // cause it to render as a dense orange web across the map.
          "fill-outline-color": "transparent",
        },
      });
    }

    // ── Fire arrival time isochrone layers ─────────────────────────────────
    // Line rings for each time threshold, colored by time urgency
    if (m.getSource("fire-isochrones")) {
      if (m.getLayer("fire-isochrone-lines")) m.removeLayer("fire-isochrone-lines");
      m.removeSource("fire-isochrones");
    }
    m.addSource("fire-isochrones", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });
    m.addLayer({
      id: "fire-isochrone-lines",
      type: "line",
      source: "fire-isochrones",
      paint: {
        "line-color": ["get", "color"],
        "line-width": 2,
        "line-opacity": 0.85,
        "line-dasharray": [6, 3],
      },
    });

    // Label anchor points — text showing arrival time at northernmost point
    if (m.getSource("fire-isochrone-labels")) {
      if (m.getLayer("fire-isochrone-label-text")) m.removeLayer("fire-isochrone-label-text");
      m.removeSource("fire-isochrone-labels");
    }
    m.addSource("fire-isochrone-labels", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });
    m.addLayer({
      id: "fire-isochrone-label-text",
      type: "symbol",
      source: "fire-isochrone-labels",
      layout: {
        "text-field": ["get", "label"],
        "text-size": 11,
        "text-anchor": "bottom",
        "text-offset": [0, -0.3],
        "text-font": ["Open Sans Bold", "Arial Unicode MS Bold"],
        "text-allow-overlap": false,
        "text-ignore-placement": false,
      },
      paint: {
        "text-color": ["get", "color"],
        "text-halo-color": "rgba(5,10,20,0.85)",
        "text-halo-width": 1.5,
      },
    });

    // Click handler for community polygons
    m.on("click", "overlay-communities-fill", (e) => {
      if (!e.features || !e.features.length) return;
      const props = e.features[0].properties as Record<string, unknown>;
      const name = (props.name ?? props.NAME ?? props.label ?? "Community") as string;
      new maplibregl.Popup({ closeButton: true, maxWidth: "200px" })
        .setLngLat(e.lngLat)
        .setHTML(
          `<div style="background:#1a2540;color:#e0e0e0;padding:8px 10px;border-radius:4px;font-size:12px">
            <strong style="color:#26c6da">${name}</strong>
          </div>`
        )
        .addTo(m);
    });
  }, []);

  // Initialize map
  useEffect(() => {
    if (!mapContainer.current || map.current) return;

    const m = new maplibregl.Map({
      container: mapContainer.current,
      style: BASEMAPS.osm.style(),
      center: [-113.49, 53.55],
      zoom: 11,
      // @ts-expect-error: preserveDrawingBuffer is a valid WebGL option not yet in MapLibre v5 type definitions
      preserveDrawingBuffer: true,
    });

    m.addControl(new maplibregl.NavigationControl(), "top-right");

    m.on("load", () => {
      addMapLayers(m);
      m.resize();
      setMapReady(true);
      mapRefCallback?.(m);
    });

    m.on("click", (e) => {
      if (readOnlyRef.current || !ignitionModeRef.current) return;
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
      addMapLayers(map.current!);
    });
  }, [basemap, mapReady, addMapLayers]);

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
      el.innerHTML = `<svg width="24" height="28" viewBox="0 0 24 28" fill="none">
        <path d="M12 0C7.03 0 3 4.03 3 9c0 6.75 9 19 9 19s9-12.25 9-19c0-4.97-4.03-9-9-9z" fill="#3d5a80" stroke="white" stroke-width="1.5"/>
        <circle cx="12" cy="9" r="4" fill="white"/>
      </svg>`;

      markerRef.current = new maplibregl.Marker({ element: el })
        .setLngLat([ignitionPoint.lng, ignitionPoint.lat])
        .addTo(map.current);

      // Pan to incident location without changing zoom level
      map.current.panTo([ignitionPoint.lng, ignitionPoint.lat], { duration: 500 });
    }
  }, [ignitionPoint]);

  // Sync overlay GeoJSON sources
  useEffect(() => {
    if (!map.current || !mapReady) return;
    const m = map.current;
    const empty: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
    const roadsSrc = m.getSource("overlay-roads") as maplibregl.GeoJSONSource | undefined;
    if (roadsSrc) roadsSrc.setData(overlayRoads ?? empty);
    const commSrc = m.getSource("overlay-communities") as maplibregl.GeoJSONSource | undefined;
    if (commSrc) commSrc.setData(overlayCommunities ?? empty);
    const infraSrc = m.getSource("overlay-infra") as maplibregl.GeoJSONSource | undefined;
    if (infraSrc) infraSrc.setData(overlayInfrastructure ?? empty);
  }, [overlayRoads, overlayCommunities, overlayInfrastructure, mapReady]);

  // Overlay layer visibility
  useEffect(() => {
    if (!map.current || !mapReady) return;
    const m = map.current;
    const setVis = (id: string, v: boolean) => {
      if (m.getLayer(id)) m.setLayoutProperty(id, "visibility", v ? "visible" : "none");
    };
    setVis("overlay-roads-line", overlayRoadsVisible);
    setVis("overlay-communities-fill", overlayCommunitiesVisible);
    setVis("overlay-communities-outline", overlayCommunitiesVisible);
    setVis("overlay-infra-circle", overlayInfrastructureVisible);
  }, [overlayRoadsVisible, overlayCommunitiesVisible, overlayInfrastructureVisible, mapReady]);

  const flyTo = useCallback((lat: number, lng: number, zoom = 12) => {
    map.current?.flyTo({ center: [lng, lat], zoom, duration: 1500 });
  }, []);

  return (
    <div style={{ width: "100%", height: "100%", position: "relative" }}>
      <div ref={mapContainer} style={{ width: "100%", height: "100%" }} />
      <LocationSearch onSelect={(lat, lng) => flyTo(lat, lng)} />

      {/* ── Map controls panel — bottom-right glass panel (hidden in readOnly mode) ───── */}
      {!readOnly && <div className="map-controls-panel">
        {/* Basemap row */}
        <div className="mcp-basemap-row">
          {(Object.keys(BASEMAPS) as BasemapId[]).map((id) => (
            <button
              key={id}
              className={`mcp-basemap-btn${basemap === id ? " active" : ""}`}
              onClick={() => setBasemap(id)}
              title={BASEMAPS[id].label}
            >
              {BASEMAPS[id].label}
            </button>
          ))}
        </div>

        {/* Divider */}
        <div className="mcp-divider" />

        {/* Incident location row */}
        <div className="mcp-row">
          <button
            className={`mcp-btn mcp-ignite${ignitionMode ? " active" : ""}`}
            onClick={() => setIgnitionMode((v) => !v)}
            title={ignitionMode ? "Click map to set incident location" : ignitionPoint ? "Move incident location" : "Set incident location"}
          >
            <span className="mcp-icon">⊕</span>
            <span className="mcp-label">
              {ignitionMode ? "Placing" : ignitionPoint ? "Move" : "Set Loc"}
            </span>
            {ignitionMode && <span className="mcp-active-dot" />}
          </button>
          {ignitionPoint && onClearIgnition && (
            <button
              className="mcp-btn mcp-clear"
              onClick={() => { onClearIgnition(); setIgnitionMode(true); }}
              title="Clear incident location"
            >
              ✕
            </button>
          )}
        </div>
      </div>}

      {/* Placement mode hint overlay (hidden in readOnly mode) */}
      {!readOnly && ignitionMode && (
        <div className="mcp-placement-hint">
          Click map to set incident location
        </div>
      )}

      {toast && (
        <MapToast message={toast} onDone={() => setToast(null)} />
      )}
    </div>
  );
}
