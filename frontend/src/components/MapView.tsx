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
import type { EvacTier, EvacTierRecord, PlanningEvacZone } from "../utils/evacZones";
import { EVAC_COLOR, EVAC_TIERS, TIER_STYLE, featureName, labelPoint, planningZonesToGeoJSON } from "../utils/evacZones";
import type { Isochrone } from "../utils/isochrones";
import { isochronesToGeoJSON, isochroneLabelsGeoJSON } from "../utils/isochrones";
import { PROB_STOPS, probCss, ringsFeature, type EnsembleMapLayers } from "../utils/ensemble";

/** Ensemble line colour: ink, one colour for every arrival line (design spec §3.3, §6.2) */
const ENS_INK = "#1f2937";
/** Ensemble layers (bottom to top); the burn-probability raster goes below the first */
const ENS_LINE_LAYERS = [
  "ens-p90-casing", "ens-p90-line",
  "ens-lines-casing", "ens-lines-future", "ens-lines-past",
  "ens-now-casing", "ens-now-p50", "ens-now-p10",
];

/** Minimum spot fire HFI (kW/m) to render on the map. Weak spots below this are hidden. */
const SPOT_HFI_MIN = 300;
// Spot fires at or above this HFI (kW/m) get the pulsing ring
const PULSE_HFI_MIN = 1500;
// Fuel overlay opacity; dimmed once a fire is drawn so the fire reads on top
const FUEL_OPACITY = 0.55;
const FUEL_OPACITY_WITH_FIRE = 0.35;
const FUEL_NAMES: Record<string, string> = {
  C1: "Spruce-lichen", C2: "Boreal spruce", C3: "Mature pine", C4: "Immature pine",
  C5: "Red/white pine", C6: "Conifer plantation", C7: "Ponderosa/Douglas-fir",
  D1: "Aspen, leafless", D2: "Aspen, green", M1: "Mixedwood, leafless", M2: "Mixedwood, green",
  M3: "Dead fir mixedwood, leafless", M4: "Dead fir mixedwood, green",
  O1a: "Matted grass", O1b: "Standing grass", S1: "Pine slash", S2: "Spruce/fir slash",
  S3: "Cedar/hemlock slash",
};

/** A simple non-modal toast — disappears after 3 s */
function MapToast({ message, onDone }: { message: string; onDone: () => void }) {
  useEffect(() => {
    const t = setTimeout(onDone, 3000);
    return () => clearTimeout(t);
  }, [onDone]);
  return <div className="map-toast" role="status">{message}</div>;
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
    <div className="location-search" role="search">
      <input
        type="search"
        aria-label="Search for a place"
        placeholder="Search location..."
        value={query}
        onChange={e => handleInput(e.target.value)}
      />
      {loading && <div className="location-search-status" role="status">Searching...</div>}
      {results.length > 0 && (
        <ul className="location-search-results">
          {results.map((r, i) => (
            <li key={i}>
              <button
                type="button"
                className="location-search-result"
                onClick={() => {
                  onSelect(parseFloat(r.lat), parseFloat(r.lon), r.display_name);
                  setResults([]); setQuery(r.display_name.split(",")[0]);
                }}
              >
                {r.display_name}
              </button>
            </li>
          ))}
        </ul>
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

interface KbCursor {
  x: number;
  y: number;
  lat: number;
  lng: number;
}

/** Arrow-key step for the keyboard crosshair, px (Shift: larger steps). */
const KB_STEP = 10;
const KB_STEP_LARGE = 50;
/** The crosshair stays this far inside the map; beyond it the map pans instead. */
const KB_EDGE = 24;

interface MapViewProps {
  frames: SimulationFrame[];
  currentFrameIndex: number;
  onMapClick: (lat: number, lng: number) => void;
  onClearIgnition?: () => void;
  ignitionPoint: { lat: number; lng: number } | null;
  burnProbabilityData?: BurnProbabilityResponse | null;
  showBurnProbView?: boolean;
  /** Annotated overlay GeoJSON (features include _at_risk: 0|1 property) */
  overlayRoads?: GeoJSON.FeatureCollection | null;
  overlayRoadsVisible?: boolean;
  overlayCommunities?: GeoJSON.FeatureCollection | null;
  overlayCommunitiesVisible?: boolean;
  overlayInfrastructure?: GeoJSON.FeatureCollection | null;
  overlayInfrastructureVisible?: boolean;
  /** Evacuation status set by Planning (never generated): blue outlines by tier */
  evacZones?: PlanningEvacZone[];
  evacZonesVisible?: boolean;
  /** Modelled fire arrival near neighbourhoods: outlines labelled "Fire within 500 m by 15:32" */
  arrivalOutlines?: GeoJSON.FeatureCollection | null;
  arrivalOutlinesVisible?: boolean;
  /** Set a neighbourhood's evacuation status from its map popup (Planning) */
  onSetEvacTier?: (neighbourhood: string, tier: EvacTier | null) => void;
  evacTierRecords?: EvacTierRecord[];
  /** Fire arrival time isochrone contours */
  isochrones?: Isochrone[];
  isochronesVisible?: boolean;
  /** Fuel grid raster overlay — base64 PNG + WGS84 bounds */
  fuelGridImage?: { image: string; bounds: [number, number, number, number]; legend?: Array<{ fuel: string; color: string }> } | null;
  fuelGridVisible?: boolean;
  /** When true: disables click-to-ignite and hides the map controls panel */
  readOnly?: boolean;
  /** Called with the maplibregl.Map instance once the map has loaded */
  mapRefCallback?: (m: maplibregl.Map) => void;
  /** External control for spot fire layer visibility (used by EOC console) */
  spotFiresVisible?: boolean;
  /** Increment to fit the map to the final frame (e.g. when a run completes) */
  fitRequest?: number;
  /** Ensemble (range of outcomes): P10 arrival lines, P10/P50 extent now, P90, burn probability */
  ensemble?: EnsembleMapLayers | null;
}

export default function MapView({
  frames,
  currentFrameIndex,
  onMapClick,
  onClearIgnition,
  ignitionPoint,
  burnProbabilityData,
  showBurnProbView = false,
  overlayRoads = null,
  overlayRoadsVisible = true,
  overlayCommunities = null,
  overlayCommunitiesVisible = true,
  overlayInfrastructure = null,
  overlayInfrastructureVisible = true,
  evacZones = [],
  evacZonesVisible = true,
  arrivalOutlines = null,
  arrivalOutlinesVisible = true,
  onSetEvacTier,
  evacTierRecords = [],
  isochrones = [],
  isochronesVisible = false,
  fuelGridImage = null,
  fuelGridVisible = true,
  readOnly = false,
  mapRefCallback,
  spotFiresVisible: spotFiresVisibleProp,
  fitRequest = 0,
  ensemble = null,
}: MapViewProps) {
  const mapContainer = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const markerRef = useRef<maplibregl.Marker | null>(null);
  const [mapReady, setMapReady] = useState(false);
  const [basemap, setBasemap] = useState<BasemapId>("osm");
  const readOnlyRef = useRef(readOnly);
  // Ignition placement mode — true while operator is picking a start point
  const [ignitionMode, setIgnitionMode] = useState(!ignitionPoint);
  const ignitionModeRef = useRef(!ignitionPoint);
  // The click that placed the ignition point; feature popups ignore it
  const ignitionClickRef = useRef<MouseEvent | null>(null);
  // Keyboard ignition (design spec §7, 2.1.1): with the map focused, a crosshair moves with the
  // arrow keys and Enter sets the ignition there. Position in px within the map container.
  const [kbCursor, setKbCursor] = useState<KbCursor | null>(null);
  const kbCursorRef = useRef<KbCursor | null>(null);
  const [kbAnnounce, setKbAnnounce] = useState("");
  const onMapClickRef = useRef(onMapClick);
  useEffect(() => { onMapClickRef.current = onMapClick; }, [onMapClick]);
  const [mapZoom, setMapZoom] = useState(11);
  const [toast, setToast] = useState<string | null>(null);
  // Counter incremented each time fire layers are (re-)added to the map.
  // The perimeter-update effect depends on this so it re-runs after
  // basemap switches that destroy and recreate sources.
  const [fireLayersVersion, setFireLayersVersion] = useState(0);
  const prevBasemapRef = useRef<BasemapId>("osm");
  const [showSpotFires, setShowSpotFires] = useState(true);
  const pulseAnimRef = useRef<number | null>(null);

  // Start or stop the spot-fire pulse ring (only spots with HFI >= PULSE_HFI_MIN pulse)
  const setPulse = useCallback((on: boolean) => {
    const m = map.current;
    if (!on || !m) {
      if (pulseAnimRef.current) cancelAnimationFrame(pulseAnimRef.current);
      pulseAnimRef.current = null;
      return;
    }
    if (pulseAnimRef.current) return; // already running
    let animStart: number | null = null;
    const PULSE_CYCLE = 1400;
    const animatePulse = (ts: number) => {
      if (!animStart) animStart = ts;
      const t = ((ts - animStart) % PULSE_CYCLE) / PULSE_CYCLE;
      if (m.getLayer("spot-fires-pulse")) {
        m.setPaintProperty("spot-fires-pulse", "circle-radius", 8 + t * 18);
        m.setPaintProperty("spot-fires-pulse", "circle-stroke-opacity", (1 - t) * 0.85);
      }
      pulseAnimRef.current = requestAnimationFrame(animatePulse);
    };
    pulseAnimRef.current = requestAnimationFrame(animatePulse);
  }, []);
  const spotPopupRef = useRef<maplibregl.Popup | null>(null);
  // Latest evac status / arrival data and setter for the neighbourhood popup (built in addFireLayers)
  const evacPopupDataRef = useRef<{
    records: EvacTierRecord[];
    arrivals: Map<string, string>;
    onSet?: (neighbourhood: string, tier: EvacTier | null) => void;
  }>({ records: [], arrivals: new Map() });
  useEffect(() => {
    const arrivals = new Map<string, string>();
    for (const f of arrivalOutlines?.features ?? []) {
      arrivals.set(String(f.properties?.neighbourhood), String(f.properties?.arrival_label));
    }
    evacPopupDataRef.current = { records: evacTierRecords, arrivals, onSet: onSetEvacTier };
  }, [evacTierRecords, arrivalOutlines, onSetEvacTier]);

  const addFireLayers = useCallback((m: maplibregl.Map) => {
    // Remove stale sources if they somehow survived (defensive)
    if (m.getSource("fire-perimeter")) {
      if (m.getLayer("fire-outline")) m.removeLayer("fire-outline");
      if (m.getLayer("fire-fill")) m.removeLayer("fire-fill");
      m.removeSource("fire-perimeter");
    }
    if (m.getSource("fire-history")) {
      if (m.getLayer("fire-history-fill")) m.removeLayer("fire-history-fill");
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
        // Grid runs draw the burned area as a heatmap; their outline fill stays faint
        "fill-opacity": ["case", ["==", ["get", "mode"], "grid"], 0.12, 0.5],
        "fill-outline-color": "transparent",
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

    // Heat-scar fill: semi-transparent fills ordered oldest→newest.
    // Recency (0=oldest, 1=most-recent history) drives opacity and color.
    m.addLayer(
      {
        id: "fire-history-fill",
        type: "fill",
        source: "fire-history",
        paint: {
          "fill-color": [
            "interpolate", ["linear"], ["get", "recency"],
            0, "#e65100",
            1, "#ff3d00",
          ],
          "fill-opacity": [
            "interpolate", ["linear"], ["get", "recency"],
            0, 0.04,
            0.5, 0.09,
            1, 0.18,
          ],
          // Suppress the 1px auto-outline — the Huygens perimeter has many
          // self-intersecting vertices whose outline edges create a visual web.
          "fill-outline-color": "transparent",
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

    // Individual cell circles (visible at high zoom) — colored by crown fire state
    m.addLayer({
      id: "fire-cells-layer",
      type: "circle",
      source: "fire-heatmap",
      minzoom: 14,
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 14, 3, 17, 10],
        "circle-color": [
          "match", ["get", "fire_type"],
          "active_crown",        "#8B0000",  // dark red — active crown fire
          "passive_crown",       "#CC2200",  // deep red — passive crown fire
          "surface_with_torching", "#FF5500",// orange-red — torching
          "#FF9800",                         // default orange — surface fire
        ],
        "circle-opacity": 0.78,
        "circle-stroke-width": 0.5,
        "circle-stroke-color": "#222",
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
      if (m.getLayer("spot-fires-heatmap")) m.removeLayer("spot-fires-heatmap");
      if (m.getLayer("spot-fires-pulse")) m.removeLayer("spot-fires-pulse");
      if (m.getLayer("spot-fires-circle")) m.removeLayer("spot-fires-circle");
      m.removeSource("spot-fires");
    }
    m.addSource("spot-fires", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });

    // ① Landing density heatmap — primary "danger zone" visual (Stitch design)
    m.addLayer({
      id: "spot-fires-heatmap",
      type: "heatmap",
      source: "spot-fires",
      paint: {
        "heatmap-weight": [
          "interpolate", ["linear"], ["get", "hfi_kw_m"],
          0, 0, SPOT_HFI_MIN, 0.2, 2000, 0.7, 5000, 1.0,
        ],
        "heatmap-intensity": 1.0,
        "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 9, 20, 13, 45, 16, 70],
        "heatmap-color": [
          "interpolate", ["linear"], ["heatmap-density"],
          0,   "rgba(0,0,0,0)",
          0.15, "rgba(255,200,50,0.12)",
          0.35, "rgba(255,140,20,0.28)",
          0.55, "rgba(255,80,0,0.44)",
          0.75, "rgba(220,40,0,0.60)",
          1.0,  "rgba(180,10,0,0.75)",
        ],
        "heatmap-opacity": 0.85,
      },
    });

    // ② Outer pulsing ring — only on high-intensity spots (HFI >= 1500)
    m.addLayer({
      id: "spot-fires-pulse",
      type: "circle",
      source: "spot-fires",
      filter: [">=", ["get", "hfi_kw_m"], PULSE_HFI_MIN],
      paint: {
        "circle-radius": 10,
        "circle-color": "transparent",
        "circle-opacity": 0,
        "circle-stroke-width": 2,
        "circle-stroke-color": "#ff6600",
        "circle-stroke-opacity": 0.8,
      },
    });

    // ③ HFI-scaled landing circles — radius and color driven by fire intensity
    m.addLayer({
      id: "spot-fires-circle",
      type: "circle",
      source: "spot-fires",
      filter: [">=", ["get", "hfi_kw_m"], SPOT_HFI_MIN],
      paint: {
        "circle-radius": [
          "interpolate", ["linear"], ["get", "hfi_kw_m"],
          SPOT_HFI_MIN, 3,
          1500, 6,
          3500, 10,
          6000, 14,
        ],
        "circle-color": [
          "interpolate", ["linear"], ["get", "hfi_kw_m"],
          SPOT_HFI_MIN, "#ff9800",
          2000,         "#ff5722",
          5000,         "#e53935",
        ],
        "circle-stroke-width": 1.5,
        "circle-stroke-color": "rgba(255,255,255,0.55)",
        "circle-opacity": 0.92,
        "circle-stroke-opacity": 1,
      },
    });

    // Click handler: show popup with spot fire metadata
    m.on("click", "spot-fires-circle", (e) => {
      if (e.originalEvent === ignitionClickRef.current) return;
      if (!e.features || e.features.length === 0) return;
      const props = e.features[0].properties as { distance_m: number; hfi_kw_m: number };
      if (spotPopupRef.current) spotPopupRef.current.remove();
      spotPopupRef.current = new maplibregl.Popup({ closeButton: true, maxWidth: "220px" })
        .setLngLat(e.lngLat)
        .setHTML(
          `<div class="map-popup">
            <strong class="map-popup-title">Spot fire</strong><br/>
            <span class="map-popup-muted">Distance from front:</span> <b>${props.distance_m.toFixed(0)} m</b><br/>
            <span class="map-popup-muted">Head fire intensity:</span> <b>${props.hfi_kw_m.toFixed(0)} kW/m</b>
          </div>`
        )
        .addTo(m);
    });
    m.on("mouseenter", "spot-fires-circle", () => { m.getCanvas().style.cursor = "pointer"; });
    m.on("mouseleave", "spot-fires-circle", () => { m.getCanvas().style.cursor = ""; });

    // The pulse animation runs only while high-intensity spot fires are shown (setPulse):
    // each tick repaints the whole map, so an idle loop keeps the GPU busy permanently.
    if (pulseAnimRef.current) { cancelAnimationFrame(pulseAnimRef.current); pulseAnimRef.current = null; }

    // ember-trajectories / ember-lines intentionally not created — arcs add visual clutter.

    // ── Ember source dots — small deep-red circles at the origin on the fire front ──
    if (m.getSource("ember-sources")) {
      if (m.getLayer("ember-source-dots")) m.removeLayer("ember-source-dots");
      m.removeSource("ember-sources");
    }
    m.addSource("ember-sources", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });
    m.addLayer({
      id: "ember-source-dots",
      type: "circle",
      source: "ember-sources",
      paint: {
        "circle-radius": 3,
        "circle-color": "#b71c1c",
        "circle-opacity": 0.7,
        "circle-stroke-width": 0,
      },
    });

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
      if (e.originalEvent === ignitionClickRef.current) return;
      if (!e.features || !e.features.length) return;
      const props = e.features[0].properties as Record<string, unknown>;
      const name = (props.name ?? props.label ?? props.NAME ?? "Infrastructure point") as string;
      const type = (props.type ?? props.TYPE ?? "") as string;
      const atRisk = props._at_risk === 1;
      new maplibregl.Popup({ closeButton: true, maxWidth: "200px" })
        .setLngLat(e.lngLat)
        .setHTML(
          `<div class="map-popup">
            <strong class="map-popup-title">${name}</strong><br/>
            ${type ? `<span class="map-popup-muted">${type}</span><br/>` : ""}
            ${atRisk ? '<span class="map-popup-warn">⚠ At-risk (P ≥ 50%)</span>' : ""}
          </div>`
        )
        .addTo(m);
    });
    m.on("mouseenter", "overlay-infra-circle", () => { m.getCanvas().style.cursor = "pointer"; });
    m.on("mouseleave", "overlay-infra-circle", () => { m.getCanvas().style.cursor = ""; });

    // ── Neighbourhood layers: modelled arrival (ink) and evacuation status set by Planning (blue) ──
    for (const id of ["nbhd-arrival-line", "nbhd-arrival-casing",
      ...EVAC_TIERS.map((t) => `evac-${t.toLowerCase()}-line`), "evac-tier-casing"]) {
      if (m.getLayer(id)) m.removeLayer(id);
    }
    for (const src of ["nbhd-arrival", "evac-tiers"]) if (m.getSource(src)) m.removeSource(src);
    m.addSource("nbhd-arrival", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    m.addLayer({
      id: "nbhd-arrival-casing",
      type: "line",
      source: "nbhd-arrival",
      paint: { "line-color": "#ffffff", "line-width": 3.5, "line-opacity": 0.7 },
    });
    m.addLayer({
      id: "nbhd-arrival-line",
      type: "line",
      source: "nbhd-arrival",
      paint: { "line-color": "#1f2937", "line-width": 1.5 },
    });
    // Evacuation status (Planning): blue outlines, Order solid / Alert dashed / Watch dotted,
    // on a white casing, never a fill (spec §3.6, §6.2). Text labels are DOM markers (below).
    m.addSource("evac-tiers", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    m.addLayer({
      id: "evac-tier-casing",
      type: "line",
      source: "evac-tiers",
      paint: { "line-color": "#ffffff", "line-width": 6.5, "line-opacity": 0.85 },
    });
    for (const tier of [...EVAC_TIERS].reverse()) {
      const st = TIER_STYLE[tier];
      m.addLayer({
        id: `evac-${tier.toLowerCase()}-line`,
        type: "line",
        source: "evac-tiers",
        filter: ["==", ["get", "evac_tier"], tier],
        layout: { "line-cap": tier === "Watch" ? "round" : "butt" },
        paint: {
          "line-color": EVAC_COLOR,
          "line-width": st.width,
          ...(st.dash ? { "line-dasharray": st.dash } : {}),
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

    // ── Ensemble (range of outcomes): ink lines on a white casing, labels are DOM markers ──
    if (m.getLayer("ens-prob-layer")) m.removeLayer("ens-prob-layer");
    if (m.getSource("ens-prob")) m.removeSource("ens-prob");
    for (const id of ENS_LINE_LAYERS) if (m.getLayer(id)) m.removeLayer(id);
    for (const src of ["ens-p90", "ens-lines", "ens-now"]) if (m.getSource(src)) m.removeSource(src);
    const emptyFc: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
    m.addSource("ens-p90", { type: "geojson", data: emptyFc });
    m.addSource("ens-lines", { type: "geojson", data: emptyFc });
    m.addSource("ens-now", { type: "geojson", data: emptyFc });
    const casing = (id: string, source: string, width: number, filter?: maplibregl.FilterSpecification) =>
      m.addLayer({
        id, type: "line", source,
        ...(filter ? { filter } : {}),
        layout: { "line-join": "round" },
        paint: { "line-color": "#ffffff", "line-width": width, "line-opacity": 0.75 },
      });
    // P90 footprint (cells 9 in 10 members reach): dotted
    casing("ens-p90-casing", "ens-p90", 3.5);
    m.addLayer({
      id: "ens-p90-line", type: "line", source: "ens-p90",
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": ENS_INK, "line-width": 2, "line-dasharray": [0.1, 2] },
    });
    // P10 arrival lines: solid up to the selected time, dashed and lighter after it (projected)
    casing("ens-lines-casing", "ens-lines", 3.5, ["==", ["get", "phase"], "past"]);
    m.addLayer({
      id: "ens-lines-future", type: "line", source: "ens-lines",
      filter: ["==", ["get", "phase"], "future"],
      layout: { "line-join": "round" },
      paint: { "line-color": ENS_INK, "line-width": 1.2, "line-opacity": 0.5, "line-dasharray": [2, 3] },
    });
    m.addLayer({
      id: "ens-lines-past", type: "line", source: "ens-lines",
      filter: ["==", ["get", "phase"], "past"],
      layout: { "line-join": "round" },
      paint: { "line-color": ENS_INK, "line-width": 1.5 },
    });
    // Extent at the selected time: P10 bold, P50 long dashes
    casing("ens-now-casing", "ens-now", 6);
    m.addLayer({
      id: "ens-now-p50", type: "line", source: "ens-now",
      filter: ["==", ["get", "kind"], "p50"],
      layout: { "line-join": "round" },
      paint: { "line-color": ENS_INK, "line-width": 2.5, "line-dasharray": [4, 2] },
    });
    m.addLayer({
      id: "ens-now-p10", type: "line", source: "ens-now",
      filter: ["==", ["get", "kind"], "p10"],
      layout: { "line-join": "round" },
      paint: { "line-color": ENS_INK, "line-width": 3.5 },
    });

    // Click a neighbourhood: its name, the modelled arrival, and its evacuation status, which
    // Planning can set here (None / Watch / Alert / Order). FireSim never sets it.
    m.on("click", "overlay-communities-fill", (e) => {
      if (e.originalEvent === ignitionClickRef.current) return;
      if (!e.features || !e.features.length) return;
      const name = featureName(e.features[0] as unknown as GeoJSON.Feature);
      const data = evacPopupDataRef.current;
      const el = document.createElement("div");
      el.className = "map-popup evac-popup";
      const title = document.createElement("strong");
      title.className = "map-popup-title";
      title.textContent = name;
      el.appendChild(title);
      const arrival = document.createElement("div");
      arrival.className = "map-popup-muted";
      arrival.textContent = data.arrivals.get(name) ?? "No modelled fire within 500 m in this run";
      el.appendChild(arrival);
      if (data.onSet) {
        const fs = document.createElement("fieldset");
        fs.className = "evac-popup-status";
        const legend = document.createElement("legend");
        legend.textContent = "Evacuation status (set by Planning)";
        fs.appendChild(legend);
        const row = document.createElement("div");
        row.className = "evac-popup-buttons";
        const current = data.records.find((r) => r.neighbourhood === name)?.tier ?? null;
        const buttons: HTMLButtonElement[] = [];
        for (const tier of [null, "Watch", "Alert", "Order"] as Array<EvacTier | null>) {
          const b = document.createElement("button");
          b.type = "button";
          b.textContent = tier ?? "None";
          b.setAttribute("aria-pressed", String(tier === current));
          b.addEventListener("click", () => {
            evacPopupDataRef.current.onSet?.(name, tier);
            for (const x of buttons) x.setAttribute("aria-pressed", String(x === b));
          });
          buttons.push(b);
          row.appendChild(b);
        }
        fs.appendChild(row);
        el.appendChild(fs);
      }
      new maplibregl.Popup({ closeButton: true, maxWidth: "260px" })
        .setLngLat(e.lngLat)
        .setDOMContent(el)
        .addTo(m);
    });

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
      // @ts-expect-error: preserveDrawingBuffer is a valid WebGL option not yet in MapLibre v5 type definitions
      preserveDrawingBuffer: true,
    });

    m.addControl(new maplibregl.NavigationControl(), "top-right");
    m.on("zoomend", () => setMapZoom(m.getZoom()));
    // Current view as [[west, south], [east, north]] for tests and debugging
    const publishBounds = () =>
      mapContainer.current?.setAttribute("data-bounds", JSON.stringify(m.getBounds().toArray()));
    m.on("moveend", publishBounds);

    m.on("load", () => {
      addFireLayers(m);
      m.resize();
      setMapReady(true);
      publishBounds();
      mapRefCallback?.(m);
    });

    m.on("click", (e) => {
      if (readOnlyRef.current || !ignitionModeRef.current) return;
      ignitionClickRef.current = e.originalEvent;
      onMapClick(e.lngLat.lat, e.lngLat.lng);
      // Exit placement mode after setting ignition
      ignitionModeRef.current = false;
      setIgnitionMode(false);
      m.getCanvas().style.cursor = "";
    });

    // ── Keyboard ignition: crosshair + arrow keys + Enter (not in the read-only EOC map) ──
    const canvas = m.getCanvas();
    const setKb = (c: KbCursor | null) => {
      kbCursorRef.current = c;
      setKbCursor(c);
    };
    const kbAt = (x: number, y: number): KbCursor => {
      const ll = m.unproject([x, y]);
      return { x, y, lat: ll.lat, lng: ll.lng };
    };
    const centre = () => kbAt(canvas.clientWidth / 2, canvas.clientHeight / 2);
    const onKbFocus = () => {
      // Keyboard focus only: a mouse click on the map does not show the crosshair
      if (canvas.matches(":focus-visible")) setKb(kbCursorRef.current ?? centre());
    };
    const onKbBlur = () => {
      kbCursorRef.current = null;
      setKbCursor(null);
    };
    const onKbKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return; // Ctrl+Enter runs (handled by the Run bar)
      const step = e.shiftKey ? KB_STEP_LARGE : KB_STEP;
      let dx = 0;
      let dy = 0;
      switch (e.key) {
        case "ArrowLeft": dx = -step; break;
        case "ArrowRight": dx = step; break;
        case "ArrowUp": dy = -step; break;
        case "ArrowDown": dy = step; break;
        case "+":
        case "=":
          e.preventDefault();
          m.zoomIn();
          return;
        case "-":
        case "_":
          e.preventDefault();
          m.zoomOut();
          return;
        case "Escape":
          setKb(null);
          return;
        case "Enter": {
          e.preventDefault();
          const c = kbCursorRef.current;
          if (!c) {
            setKb(centre());
            setKbAnnounce("Crosshair at the map centre. Arrow keys move it, Enter sets the ignition.");
            return;
          }
          const at = kbAt(c.x, c.y);
          onMapClickRef.current(at.lat, at.lng);
          ignitionModeRef.current = false;
          setIgnitionMode(false);
          setKbAnnounce(`Ignition set ${at.lat.toFixed(4)}, ${at.lng.toFixed(4)}`);
          return;
        }
        default:
          return;
      }
      e.preventDefault();
      const p = kbCursorRef.current ?? centre();
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      let x = p.x + dx;
      let y = p.y + dy;
      // At the edge the map pans instead, so the crosshair can reach anywhere
      let panX = 0;
      let panY = 0;
      if (x < KB_EDGE) { panX = x - KB_EDGE; x = KB_EDGE; }
      else if (x > w - KB_EDGE) { panX = x - (w - KB_EDGE); x = w - KB_EDGE; }
      if (y < KB_EDGE) { panY = y - KB_EDGE; y = KB_EDGE; }
      else if (y > h - KB_EDGE) { panY = y - (h - KB_EDGE); y = h - KB_EDGE; }
      if (panX || panY) m.panBy([panX, panY], { duration: 0 });
      setKb(kbAt(x, y));
    };
    // Keep the crosshair's coordinates current when the map moves under it
    const onKbMove = () => {
      const c = kbCursorRef.current;
      if (c) setKb(kbAt(c.x, c.y));
    };
    if (!readOnlyRef.current) {
      m.keyboard.disable(); // arrows move the crosshair; +/- zoom is handled above
      canvas.setAttribute(
        "aria-label",
        "Map. Arrow keys move the ignition crosshair, Shift for larger steps; Enter sets the ignition; plus and minus zoom.",
      );
      canvas.addEventListener("focus", onKbFocus);
      canvas.addEventListener("blur", onKbBlur);
      canvas.addEventListener("keydown", onKbKey);
      m.on("move", onKbMove);
    }

    map.current = m;

    const resizeTimer = setTimeout(() => m.resize(), 200);

    return () => {
      clearTimeout(resizeTimer);
      canvas.removeEventListener("focus", onKbFocus);
      canvas.removeEventListener("blur", onKbBlur);
      canvas.removeEventListener("keydown", onKbKey);
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

    // T=0 synthetic frame: clear all fire layers and return
    if (
      (!currentFrame.burned_cells || currentFrame.burned_cells.length === 0) &&
      currentFrame.perimeter.length === 0
    ) {
      (map.current.getSource("fire-heatmap") as maplibregl.GeoJSONSource | undefined)
        ?.setData({ type: "FeatureCollection", features: [] });
      (map.current.getSource("fire-perimeter") as maplibregl.GeoJSONSource | undefined)
        ?.setData({ type: "FeatureCollection", features: [] });
      (map.current.getSource("fire-history") as maplibregl.GeoJSONSource | undefined)
        ?.setData({ type: "FeatureCollection", features: [] });
      (map.current.getSource("spot-fires") as maplibregl.GeoJSONSource | undefined)
        ?.setData({ type: "FeatureCollection", features: [] });
      (map.current.getSource("ember-sources") as maplibregl.GeoJSONSource | undefined)
        ?.setData({ type: "FeatureCollection", features: [] });
      setPulse(false);
      return;
    }

    // CA mode: burned_cells present → render as heatmap
    if (currentFrame.burned_cells && currentFrame.burned_cells.length > 0) {
      const heatSrc = map.current.getSource("fire-heatmap") as maplibregl.GeoJSONSource | undefined;
      if (!heatSrc) return;

      // Each frame already contains ALL cumulative cells up to that point.
      // Use the current frame directly — no cross-frame accumulation needed.
      const allCells = currentFrame.burned_cells as Array<{
        lat: number; lng: number; intensity: number; fire_type?: string;
      }>;

      const features: GeoJSON.Feature[] = allCells.map((c) => ({
        type: "Feature" as const,
        geometry: { type: "Point" as const, coordinates: [c.lng, c.lat] },
        properties: { intensity: c.intensity, fire_type: c.fire_type ?? "surface" },
      }));

      heatSrc.setData({ type: "FeatureCollection", features });

      // Outline of the burned area (the engine's perimeter polygon), so the fire's extent
      // stays visible at city zoom where the heatmap thins out
      const perimSrc = map.current.getSource("fire-perimeter") as maplibregl.GeoJSONSource | undefined;
      if (perimSrc) {
        const ring = currentFrame.perimeter.map(([lat, lng]) => [lng, lat]);
        if (ring.length >= 3 && (ring[0][0] !== ring[ring.length - 1][0] || ring[0][1] !== ring[ring.length - 1][1])) {
          ring.push(ring[0]);
        }
        perimSrc.setData({
          type: "FeatureCollection",
          features: ring.length >= 4 ? [{
            type: "Feature",
            geometry: { type: "Polygon", coordinates: [ring] },
            properties: { mode: "grid", hfi: currentFrame.max_hfi_kw_m },
          }] : [],
        });
      }
      const histSrc = map.current.getSource("fire-history") as maplibregl.GeoJSONSource | undefined;
      if (histSrc) histSrc.setData({ type: "FeatureCollection", features: [] });

      // Spot fire landing zones — accumulate across all frames for the heat blob
      const spotSrcCA = map.current.getSource("spot-fires") as maplibregl.GeoJSONSource | undefined;
      const allSpotFiresCA = frames.slice(0, currentFrameIndex + 1).flatMap((f) => f.spot_fires ?? []);
      setPulse(allSpotFiresCA.some((sf) => sf.hfi_kw_m >= PULSE_HFI_MIN));
      if (spotSrcCA) {
        spotSrcCA.setData({
          type: "FeatureCollection",
          features: allSpotFiresCA.map((sf) => ({
            type: "Feature" as const,
            geometry: { type: "Point" as const, coordinates: [sf.lng, sf.lat] },
            properties: { distance_m: sf.distance_m, hfi_kw_m: sf.hfi_kw_m },
          })),
        });
      }

      // Ember source dots — top 15 launch points by HFI from current frame
      const srcDotsSrcCA = map.current.getSource("ember-sources") as maplibregl.GeoJSONSource | undefined;
      const frameSpotsCA = (currentFrame.spot_fires ?? [])
        .filter((sf) => sf.source_lat != null && sf.source_lng != null && sf.hfi_kw_m >= SPOT_HFI_MIN)
        .sort((a, b) => b.hfi_kw_m - a.hfi_kw_m)
        .slice(0, 15);
      if (srcDotsSrcCA) {
        srcDotsSrcCA.setData({
          type: "FeatureCollection",
          features: frameSpotsCA.map((sf) => ({
            type: "Feature" as const,
            geometry: { type: "Point" as const, coordinates: [sf.source_lng!, sf.source_lat!] },
            properties: { hfi_kw_m: sf.hfi_kw_m },
          })),
        });
      }
      return;
    }

    // Huygens mode: polygon perimeter
    const src = map.current.getSource("fire-perimeter") as maplibregl.GeoJSONSource | undefined;
    if (!src) return;
    if (currentFrame.perimeter.length < 3) return;

    const rawCoords = currentFrame.perimeter.map(([lat, lng]) => [lng, lat]);

    // Convex hull (Andrew's monotone chain) — guarantees a valid simple polygon
    // with no self-intersections, preventing WebGL tessellator spike triangles.
    const convexHull = (pts: number[][]): number[][] => {
      if (pts.length < 3) return pts;
      const sorted = [...pts].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
      const cross = (o: number[], a: number[], b: number[]) =>
        (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
      const lower: number[][] = [];
      for (const p of sorted) {
        while (lower.length >= 2 && cross(lower[lower.length-2], lower[lower.length-1], p) <= 0) lower.pop();
        lower.push(p);
      }
      const upper: number[][] = [];
      for (let i = sorted.length - 1; i >= 0; i--) {
        const p = sorted[i];
        while (upper.length >= 2 && cross(upper[upper.length-2], upper[upper.length-1], p) <= 0) upper.pop();
        upper.push(p);
      }
      upper.pop(); lower.pop();
      return [...lower, ...upper];
    };

    const coords = convexHull(rawCoords);
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

    const historySlice = frames.slice(1, currentFrameIndex).filter((f) => f.perimeter.length >= 3);
    const historyFeatures: GeoJSON.Feature[] = historySlice.map((f, idx, arr) => {
      const raw = f.perimeter.map(([lat, lng]) => [lng, lat]);
      const c = convexHull(raw);
      if (c.length > 1 && (c[0][0] !== c[c.length - 1][0] || c[0][1] !== c[c.length - 1][1])) {
        c.push(c[0]);
      }
      return {
        type: "Feature" as const,
        geometry: { type: "Polygon" as const, coordinates: [c] },
        properties: {
          time_hours: f.time_hours,
          // recency: 0 = oldest ring, 1 = ring just before current — drives heat-scar opacity
          recency: arr.length > 1 ? idx / (arr.length - 1) : 1,
        },
      };
    });

    historySrc.setData({ type: "FeatureCollection", features: historyFeatures });

    // Clear heatmap (not used in Huygens mode)
    const heatSrc = map.current.getSource("fire-heatmap") as maplibregl.GeoJSONSource | undefined;
    if (heatSrc) heatSrc.setData({ type: "FeatureCollection", features: [] });

    // Spot fire landing zones — accumulate across all frames for the heat blob
    const spotSrc = map.current.getSource("spot-fires") as maplibregl.GeoJSONSource | undefined;
    const allSpotFires = frames.slice(0, currentFrameIndex + 1).flatMap((f) => f.spot_fires ?? []);
    setPulse(allSpotFires.some((sf) => sf.hfi_kw_m >= PULSE_HFI_MIN));
    if (spotSrc) {
      spotSrc.setData({
        type: "FeatureCollection",
        features: allSpotFires.map((sf) => ({
          type: "Feature" as const,
          geometry: { type: "Point" as const, coordinates: [sf.lng, sf.lat] },
          properties: { distance_m: sf.distance_m, hfi_kw_m: sf.hfi_kw_m },
        })),
      });
    }

    // Ember source dots — top 15 launch points by HFI from current frame
    // (arcs removed: heatmap + circles tell the landing story; arcs only add clutter)
    const srcDotsSrc = map.current.getSource("ember-sources") as maplibregl.GeoJSONSource | undefined;
    const frameSpots = (currentFrame.spot_fires ?? [])
      .filter((sf) => sf.source_lat != null && sf.source_lng != null && sf.hfi_kw_m >= SPOT_HFI_MIN)
      .sort((a, b) => b.hfi_kw_m - a.hfi_kw_m)
      .slice(0, 15);
    if (srcDotsSrc) {
      srcDotsSrc.setData({
        type: "FeatureCollection",
        features: frameSpots.map((sf) => ({
          type: "Feature" as const,
          geometry: { type: "Point" as const, coordinates: [sf.source_lng!, sf.source_lat!] },
          properties: { hfi_kw_m: sf.hfi_kw_m },
        })),
      });
    }
  }, [frames, currentFrameIndex, mapReady, fireLayersVersion, setPulse]);

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
    // Prop overrides internal toggle (used by EOC console read-only map)
    const visibility = (spotFiresVisibleProp ?? showSpotFires) ? "visible" : "none";
    for (const id of ["spot-fires-circle", "spot-fires-pulse", "spot-fires-heatmap", "ember-source-dots"]) {
      if (map.current.getLayer(id)) map.current.setLayoutProperty(id, "visibility", visibility);
    }
  }, [showSpotFires, spotFiresVisibleProp, mapReady]);

  // Toggle between burn probability view and fire spread view
  const ensProbOn = !!ensemble?.show.prob;
  useEffect(() => {
    if (!map.current || !mapReady) return;
    const m = map.current;
    const hasBurnData = !!burnProbabilityData && showBurnProbView;

    const burnLayers = ["burn-probability-layer", "burn-probability-circles"];
    const fireLayers = [
      "fire-fill", "fire-outline", "fire-history-fill",
      "fire-heatmap-layer", "fire-cells-layer",
      "spot-fires-heatmap", "spot-fires-circle", "spot-fires-pulse",
      "ember-source-dots",
    ];

    burnLayers.forEach((id) => {
      if (m.getLayer(id)) m.setLayoutProperty(id, "visibility", hasBurnData ? "visible" : "none");
    });
    // The single run's fire is also hidden while the ensemble burn probability is shown
    const hideFire = hasBurnData || ensProbOn;
    fireLayers.forEach((id) => {
      if (m.getLayer(id)) m.setLayoutProperty(id, "visibility", hideFire ? "none" : "visible");
    });
  }, [showBurnProbView, burnProbabilityData, mapReady, fireLayersVersion, ensProbOn]);

  const hasFire = frames.length > 0;

  // Zoom to a frame's fire (perimeter, else burned cells), with a margin
  const fitFrame = useCallback((f: SimulationFrame | undefined) => {
    const m = map.current;
    if (!m || !f) return;
    const pts: number[][] = f.perimeter.length >= 3
      ? f.perimeter
      : (f.burned_cells ?? []).map((c) => [c.lat, c.lng]);
    if (pts.length === 0) return;
    let s = Infinity, n = -Infinity, w = Infinity, e = -Infinity;
    for (const [lat, lng] of pts) {
      s = Math.min(s, lat); n = Math.max(n, lat); w = Math.min(w, lng); e = Math.max(e, lng);
    }
    m.fitBounds([[w, s], [e, n]], { padding: 60, maxZoom: 15, duration: 600 });
  }, []);
  const fitToFire = useCallback(() => {
    fitFrame(frames[currentFrameIndex] ?? frames[frames.length - 1]);
  }, [fitFrame, frames, currentFrameIndex]);

  // Auto-fit to the final frame when the parent asks (after a run completes)
  const framesRef = useRef(frames);
  useEffect(() => {
    framesRef.current = frames;
  });
  useEffect(() => {
    if (!fitRequest || !mapReady) return;
    const all = framesRef.current;
    fitFrame(all[all.length - 1]);
  }, [fitRequest, mapReady, fitFrame]);
  const fuelOpacity = fuelGridVisible ? (hasFire ? FUEL_OPACITY_WITH_FIRE : FUEL_OPACITY) : 0;

  // Fuel grid raster overlay
  useEffect(() => {
    if (!map.current || !mapReady) return;
    const m = map.current;

    // Remove any existing fuel layer/source before re-adding
    if (m.getLayer("fuel-grid-layer")) m.removeLayer("fuel-grid-layer");
    if (m.getSource("fuel-grid")) m.removeSource("fuel-grid");

    if (!fuelGridImage) return;

    const [west, south, east, north] = fuelGridImage.bounds;
    m.addSource("fuel-grid", {
      type: "image",
      url: fuelGridImage.image,
      coordinates: [
        [west, north], // top-left
        [east, north], // top-right
        [east, south], // bottom-right
        [west, south], // bottom-left
      ],
    });
    m.addLayer(
      {
        id: "fuel-grid-layer",
        type: "raster",
        source: "fuel-grid",
        paint: {
          "raster-opacity": fuelOpacity,
          "raster-resampling": "nearest",
        },
      },
      // Insert below fire layers so the fire renders on top
      "fire-fill",
    );
  }, [fuelGridImage, mapReady, fireLayersVersion]);

  // Toggle fuel grid visibility
  useEffect(() => {
    if (!map.current || !mapReady) return;
    if (map.current.getLayer("fuel-grid-layer")) {
      map.current.setPaintProperty(
        "fuel-grid-layer", "raster-opacity", fuelOpacity,
      );
    }
  }, [fuelOpacity, mapReady]);

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
  }, [overlayRoads, overlayCommunities, overlayInfrastructure, mapReady, fireLayersVersion]);

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
  }, [overlayRoadsVisible, overlayCommunitiesVisible, overlayInfrastructureVisible, mapReady, fireLayersVersion]);

  // Sync the evacuation status (Planning) and modelled arrival sources
  useEffect(() => {
    if (!map.current || !mapReady) return;
    const m = map.current;
    const fc = planningZonesToGeoJSON(evacZones ?? []);
    (m.getSource("evac-tiers") as maplibregl.GeoJSONSource | undefined)?.setData(fc);
    // What is drawn, for tests and debugging (the map itself is a canvas)
    mapContainer.current?.setAttribute(
      "data-evac-tiers",
      JSON.stringify(fc.features.map((f) => [f.properties?.neighbourhood, f.properties?.map_label])),
    );
  }, [evacZones, mapReady, fireLayersVersion]);

  useEffect(() => {
    if (!map.current || !mapReady) return;
    const m = map.current;
    const fc = arrivalOutlines ?? { type: "FeatureCollection" as const, features: [] };
    (m.getSource("nbhd-arrival") as maplibregl.GeoJSONSource | undefined)?.setData(fc);
    mapContainer.current?.setAttribute("data-arrival-count", String(fc.features.length));
  }, [arrivalOutlines, mapReady, fireLayersVersion]);

  // Visibility of the two neighbourhood layer groups
  useEffect(() => {
    if (!map.current || !mapReady) return;
    const m = map.current;
    const setVis = (ids: string[], v: boolean) => {
      for (const id of ids) if (m.getLayer(id)) m.setLayoutProperty(id, "visibility", v ? "visible" : "none");
    };
    setVis(["evac-tier-casing", ...EVAC_TIERS.map((t) => `evac-${t.toLowerCase()}-line`)], evacZonesVisible);
    setVis(["nbhd-arrival-casing", "nbhd-arrival-line"], arrivalOutlinesVisible);
  }, [evacZonesVisible, arrivalOutlinesVisible, mapReady, fireLayersVersion]);

  // On-map text labels for neighbourhoods (DOM markers, so they need no map glyphs and stay
  // >= 12 px): "ORDER · set by Planning" for a status Planning set, and the modelled arrival
  // "Fire within 500 m by 15:32". Decorative duplicates of the Neighbourhoods card (aria-hidden).
  const nbhdLabelMarkersRef = useRef<maplibregl.Marker[]>([]);
  useEffect(() => {
    const m = map.current;
    if (!m || !mapReady) return;
    for (const mk of nbhdLabelMarkersRef.current) mk.remove();
    nbhdLabelMarkersRef.current = [];
    const entries = new Map<string, { feature: GeoJSON.Feature; tier?: EvacTier; arrival?: string }>();
    if (evacZonesVisible) {
      for (const z of evacZones ?? []) for (const f of z.features) entries.set(featureName(f), { feature: f, tier: z.tier });
    }
    if (arrivalOutlinesVisible) {
      for (const f of arrivalOutlines?.features ?? []) {
        const name = String(f.properties?.neighbourhood);
        const e = entries.get(name) ?? { feature: f };
        e.arrival = String(f.properties?.arrival_label);
        entries.set(name, e);
      }
    }
    for (const [name, e] of entries) {
      const at = labelPoint(e.feature);
      if (!at) continue;
      const el = document.createElement("div");
      el.className = `map-nbhd-label${e.tier ? ` map-nbhd-label-${TIER_STYLE[e.tier].css}` : ""}`;
      el.setAttribute("aria-hidden", "true");
      if (e.tier) {
        const t = document.createElement("div");
        t.className = "map-nbhd-label-tier";
        t.textContent = `${TIER_STYLE[e.tier].mapLabel} · set by Planning`;
        el.appendChild(t);
      }
      const n = document.createElement("div");
      n.className = "map-nbhd-label-name";
      n.textContent = name;
      el.appendChild(n);
      if (e.arrival) {
        const a = document.createElement("div");
        a.className = "map-nbhd-label-arrival";
        a.textContent = e.arrival;
        el.appendChild(a);
      }
      nbhdLabelMarkersRef.current.push(new maplibregl.Marker({ element: el, anchor: "center" }).setLngLat(at).addTo(m));
    }
  }, [evacZones, arrivalOutlines, evacZonesVisible, arrivalOutlinesVisible, mapReady]);

  // Sync isochrone GeoJSON sources — only show isochrones up to current frame time
  useEffect(() => {
    if (!map.current || !mapReady) return;
    const m = map.current;
    const empty: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
    const lineSrc = m.getSource("fire-isochrones") as maplibregl.GeoJSONSource | undefined;
    const labelSrc = m.getSource("fire-isochrone-labels") as maplibregl.GeoJSONSource | undefined;
    if (!lineSrc || !labelSrc) return;
    const currentTime = frames[currentFrameIndex]?.time_hours ?? 0;
    const visible = (isochrones ?? []).filter((iso) => iso.timeHours <= currentTime);
    if (visible.length === 0) {
      lineSrc.setData(empty);
      labelSrc.setData(empty);
      return;
    }
    lineSrc.setData(isochronesToGeoJSON(visible));
    labelSrc.setData(isochroneLabelsGeoJSON(visible));
  }, [isochrones, mapReady, fireLayersVersion, frames, currentFrameIndex]);

  // Isochrone layer visibility
  useEffect(() => {
    if (!map.current || !mapReady) return;
    const m = map.current;
    const vis = isochronesVisible ? "visible" : "none";
    for (const id of ["fire-isochrone-lines", "fire-isochrone-label-text"]) {
      if (m.getLayer(id)) m.setLayoutProperty(id, "visibility", vis);
    }
  }, [isochronesVisible, mapReady, fireLayersVersion]);

  // ── Ensemble layers ───────────────────────────────────────────────────────
  useEffect(() => {
    const m = map.current;
    if (!m || !mapReady) return;
    const empty: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
    const set = (id: string, fc: GeoJSON.FeatureCollection) =>
      (m.getSource(id) as maplibregl.GeoJSONSource | undefined)?.setData(fc);
    if (!ensemble) {
      for (const id of ["ens-p90", "ens-lines", "ens-now"]) set(id, empty);
      mapContainer.current?.removeAttribute("data-ens-lines");
      return;
    }
    const t = ensemble.selectedMinutes;
    set("ens-lines", {
      type: "FeatureCollection",
      features: ensemble.lines.map((l) =>
        ringsFeature(l.rings, { minutes: l.minutes, label: l.label, phase: l.minutes <= t + 1e-6 ? "past" : "future" }),
      ),
    });
    set("ens-now", {
      type: "FeatureCollection",
      features: [
        ringsFeature(ensemble.nowP10, { kind: "p10" }),
        ...(ensemble.show.p50 ? [ringsFeature(ensemble.nowP50, { kind: "p50" })] : []),
      ],
    });
    set("ens-p90", { type: "FeatureCollection", features: [ringsFeature(ensemble.p90, { kind: "p90" })] });
    // What is drawn, for tests and debugging (the map itself is a canvas)
    mapContainer.current?.setAttribute(
      "data-ens-lines",
      JSON.stringify(ensemble.lines.map((l) => [l.label, l.minutes <= t + 1e-6 ? "past" : "future", l.rings.length])),
    );
  }, [ensemble, mapReady, fireLayersVersion]);

  // Burn probability raster (cells any member reached), as an image source under the lines
  const probUrl = useRef<{ key: unknown; url: string } | null>(null);
  const ensProb = ensemble?.prob ?? null;
  useEffect(() => {
    const m = map.current;
    if (!m || !mapReady) return;
    const prob = ensProb;
    if (!prob) {
      if (m.getLayer("ens-prob-layer")) m.removeLayer("ens-prob-layer");
      if (m.getSource("ens-prob")) m.removeSource("ens-prob");
      return;
    }
    if (probUrl.current?.key !== prob) {
      const canvas = document.createElement("canvas");
      canvas.width = prob.width;
      canvas.height = prob.height;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.putImageData(new ImageData(new Uint8ClampedArray(prob.data), prob.width, prob.height), 0, 0);
      probUrl.current = { key: prob, url: canvas.toDataURL("image/png") };
    }
    const url = probUrl.current.url;
    const src = m.getSource("ens-prob") as maplibregl.ImageSource | undefined;
    if (src) {
      src.updateImage({ url, coordinates: prob.coordinates });
    } else {
      m.addSource("ens-prob", { type: "image", url, coordinates: prob.coordinates });
      m.addLayer(
        { id: "ens-prob-layer", type: "raster", source: "ens-prob", paint: { "raster-opacity": 0.8, "raster-resampling": "nearest" } },
        m.getLayer("ens-p90-casing") ? "ens-p90-casing" : undefined,
      );
    }
  }, [ensProb, mapReady, fireLayersVersion]);

  // When an ensemble arrives, widen the view to its last P10 line if that is not in view
  // (the worst-credible extent is usually larger than the single run the map was fitted to)
  const ensFittedRef = useRef<unknown>(null);
  useEffect(() => {
    const m = map.current;
    if (!m || !mapReady || !ensemble || ensemble.lines.length === 0) return;
    if (ensFittedRef.current === ensemble.lines) return;
    ensFittedRef.current = ensemble.lines;
    let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity;
    for (const ring of ensemble.lines[ensemble.lines.length - 1].rings) {
      for (const [lng, lat] of ring) {
        if (lng < w) w = lng;
        if (lng > e) e = lng;
        if (lat < s) s = lat;
        if (lat > n) n = lat;
      }
    }
    if (!Number.isFinite(w)) return;
    const view = m.getBounds();
    if (view.contains([w, s]) && view.contains([e, n])) return;
    m.fitBounds([[w, s], [e, n]], { padding: 60, maxZoom: 15, duration: 800 });
  }, [ensemble, mapReady]);

  // Ensemble layer visibility
  useEffect(() => {
    const m = map.current;
    if (!m || !mapReady) return;
    const show = ensemble?.show;
    const setVis = (ids: string[], v: boolean) => {
      for (const id of ids) if (m.getLayer(id)) m.setLayoutProperty(id, "visibility", v ? "visible" : "none");
    };
    setVis(["ens-lines-casing", "ens-lines-future", "ens-lines-past", "ens-now-casing", "ens-now-p10", "ens-now-p50"], !!ensemble && !!show?.lines);
    setVis(["ens-p90-casing", "ens-p90-line"], !!ensemble && !!show?.p90);
    setVis(["ens-prob-layer"], !!ensemble && !!show?.prob);
  }, [ensemble, mapReady, fireLayersVersion]);

  // Clock-time labels on the P10 lines (DOM markers: the basemaps have no glyphs, and these
  // stay >= 12 px). Labels that would overlap an earlier one are hidden, re-checked on zoom.
  const ensLabelMarkersRef = useRef<maplibregl.Marker[]>([]);
  useEffect(() => {
    const m = map.current;
    if (!m || !mapReady) return;
    for (const mk of ensLabelMarkersRef.current) mk.remove();
    ensLabelMarkersRef.current = [];
    if (!ensemble || !ensemble.show.lines) return;
    const t = ensemble.selectedMinutes;
    for (const l of ensemble.lines) {
      if (!l.anchor) continue;
      const el = document.createElement("div");
      el.className = `map-iso-label${l.minutes <= t + 1e-6 ? "" : " map-iso-label-future"}`;
      el.setAttribute("aria-hidden", "true");
      el.textContent = l.label;
      ensLabelMarkersRef.current.push(new maplibregl.Marker({ element: el, anchor: "center" }).setLngLat(l.anchor).addTo(m));
    }
    const declutter = () => {
      const placed: Array<{ x: number; y: number }> = [];
      // Latest line first: the outermost labels win, inner ones give way when crowded
      for (const mk of [...ensLabelMarkersRef.current].reverse()) {
        const p = m.project(mk.getLngLat());
        const clash = placed.some((q) => Math.abs(q.x - p.x) < 46 && Math.abs(q.y - p.y) < 22);
        mk.getElement().style.visibility = clash ? "hidden" : "";
        if (!clash) placed.push(p);
      }
    };
    declutter();
    m.on("zoomend", declutter);
    return () => {
      m.off("zoomend", declutter);
    };
  }, [ensemble, mapReady]);

  const flyTo = useCallback((lat: number, lng: number, zoom = 12) => {
    map.current?.flyTo({ center: [lng, lat], zoom, duration: 1500 });
  }, []);

  return (
    <div className="map-view">
      <div ref={mapContainer} className="map-view-canvas" />
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
      {/* Ensemble legend: line styles, and the burn-probability ramp when shown */}
      {ensemble && (ensemble.show.lines || ensemble.show.p90 || ensemble.show.prob) && (
        <div className="burn-prob-legend ens-legend" data-testid="ensemble-legend">
          <div className="burn-prob-legend-title">Range of outcomes</div>
          <div className="burn-prob-legend-scale">
            {ensemble.show.lines && (
              <>
                <div className="burn-prob-legend-row"><span className="ens-swatch ens-swatch-line" aria-hidden="true" /> Worst-credible arrival (P10), clock time</div>
                <div className="burn-prob-legend-row"><span className="ens-swatch ens-swatch-now" aria-hidden="true" /> Worst-credible extent now</div>
                {ensemble.show.p50 && (
                  <div className="burn-prob-legend-row"><span className="ens-swatch ens-swatch-p50" aria-hidden="true" /> Median extent now (P50)</div>
                )}
                <div className="burn-prob-legend-row"><span className="ens-swatch ens-swatch-future" aria-hidden="true" /> Later (projected)</div>
              </>
            )}
            {ensemble.show.p90 && (
              <div className="burn-prob-legend-row"><span className="ens-swatch ens-swatch-p90" aria-hidden="true" /> Reached by 9 in 10 members (P90)</div>
            )}
          </div>
          {ensemble.show.prob && (
            <>
              <div className="burn-prob-legend-meta ens-legend-sub">Burn probability, share of members (single run hidden)</div>
              <div className="ens-ramp" aria-hidden="true" style={{
                background: `linear-gradient(to right, ${PROB_STOPS.map(([p]) => `${probCss(p)} ${p}%`).join(", ")})`,
              }} />
              <div className="ens-ramp-labels"><span>1%</span><span>50%</span><span>100%</span></div>
            </>
          )}
        </div>
      )}
      {/* Grid-run legend: the per-cell crown-state circles are drawn only from zoom 14;
          below that the heatmap shows intensity-weighted density */}
      {!ensProbOn && frames.length > 0 && frames[currentFrameIndex]?.burned_cells && frames[currentFrameIndex].burned_cells!.length > 0 && (
        <div className="burn-prob-legend fire-legend">
          <div className="burn-prob-legend-title">{mapZoom >= 14 ? "Crown Fire State" : "Fire Intensity"}</div>
          {mapZoom < 14 && (
            <div className="burn-prob-legend-meta">Burned cells weighted by HFI · zoom in for crown state</div>
          )}
          <div className="burn-prob-legend-scale">
            {(mapZoom >= 14
              ? [
                  { label: "Active crown",    color: "#8B0000" },
                  { label: "Passive crown",   color: "#CC2200" },
                  { label: "Torching",        color: "#FF5500" },
                  { label: "Surface fire",    color: "#FF9800" },
                ]
              : [
                  { label: "High",  color: "rgb(180,30,10)" },
                  { label: "",      color: "rgb(255,140,20)" },
                  { label: "Low",   color: "rgb(255,255,180)" },
                ]
            ).map(({ label, color }, i) => (
              <div key={label || i} className="burn-prob-legend-row">
                <div className="burn-prob-legend-swatch" style={{ background: color }} />
                <span>{label}</span>
              </div>
            ))}
            <div className="burn-prob-legend-row">
              <div className="burn-prob-legend-swatch perimeter-swatch" />
              <span>Fire perimeter</span>
            </div>
          </div>
        </div>
      )}
      {/* Fuel legend — the fuel types drawn in the overlay */}
      {fuelGridVisible && fuelGridImage?.legend && fuelGridImage.legend.length > 0 && (
        <div className="burn-prob-legend fuel-legend">
          <div className="burn-prob-legend-title">FBP Fuel Type</div>
          <div className="burn-prob-legend-scale">
            {fuelGridImage.legend.map(({ fuel, color }) => (
              <div key={fuel} className="burn-prob-legend-row">
                <div className="burn-prob-legend-swatch" style={{ background: color }} />
                <span>{fuel} {FUEL_NAMES[fuel] ?? ""}</span>
              </div>
            ))}
          </div>
        </div>
      )}
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

        {/* Ignition row */}
        <div className="mcp-row">
          <button
            className={`mcp-btn mcp-ignite${ignitionMode ? " active" : ""}`}
            onClick={() => setIgnitionMode((v) => !v)}
            title={ignitionMode ? "Click map to place ignition (ESC to cancel)" : ignitionPoint ? "Move ignition point" : "Place ignition point"}
          >
            <span className="mcp-icon">⊕</span>
            <span className="mcp-label">
              {ignitionMode ? "Arm" : ignitionPoint ? "Move" : "Ignite"}
            </span>
            {ignitionMode && <span className="mcp-active-dot" />}
          </button>
          {ignitionPoint && onClearIgnition && (
            <button
              className="mcp-btn mcp-clear"
              onClick={() => { onClearIgnition(); setIgnitionMode(true); }}
              title="Clear ignition point"
            >
              ✕
            </button>
          )}
        </div>

        {/* Fit the map to the current fire */}
        {frames.length > 0 && (
          <button className="mcp-btn" onClick={fitToFire} title="Zoom the map to the fire">
            <span className="mcp-icon">⤢</span>
            <span className="mcp-label">Fit to fire</span>
          </button>
        )}

        {/* Spot fires toggle */}
        <button
          className={`mcp-btn mcp-spotfire${showSpotFires ? " active" : ""}`}
          onClick={() => setShowSpotFires((v) => !v)}
          title={showSpotFires ? "Spotting ON — click to disable" : "Spotting OFF — click to enable"}
        >
          <span className="mcp-icon">✦</span>
          <span className="mcp-label">Spot Fires</span>
          <span className={`mcp-toggle-dot${showSpotFires ? " on" : ""}`} />
        </button>
      </div>}

      {/* Placement mode hint overlay (hidden in readOnly mode and while the keyboard crosshair is up) */}
      {!readOnly && ignitionMode && !kbCursor && (
        <div className="mcp-placement-hint">
          Click map to set ignition point
        </div>
      )}

      {/* Keyboard crosshair (map focused): arrows move it, Enter sets the ignition */}
      {!readOnly && kbCursor && (
        <>
          <div className="kb-crosshair" style={{ left: kbCursor.x, top: kbCursor.y }} aria-hidden="true" />
          <div className="kb-crosshair-label" style={{ left: kbCursor.x, top: kbCursor.y }} aria-hidden="true">
            {kbCursor.lat.toFixed(4)}, {kbCursor.lng.toFixed(4)}
          </div>
          <div className="kb-hint" aria-hidden="true">
            Arrow keys move · Shift: larger steps · Enter sets the ignition · +/− zoom · Esc hides
          </div>
        </>
      )}
      <div className="visually-hidden" role="status" aria-live="polite">{kbAnnounce}</div>

      {toast && (
        <MapToast message={toast} onDone={() => setToast(null)} />
      )}
    </div>
  );
}
