/**
 * Infrastructure overlay panel for EOC situational awareness.
 *
 * Allows loading GeoJSON layers (roads, communities, infrastructure points)
 * via file drop or URL fetch. Features intersecting ≥50% burn-probability
 * cells are flagged as "at-risk" and highlighted on the map.
 */

import { useState, useCallback, useRef } from "react";

export type LayerType = "roads" | "communities" | "infrastructure";

// Edmonton Open Data layers bundled in public/edmonton/
const EDMONTON_LAYERS: Record<LayerType, string> = {
  roads:          "./edmonton/roads.geojson",
  communities:    "./edmonton/neighbourhoods.geojson",
  infrastructure: "./edmonton/infrastructure.geojson",
};

interface LayerConfig {
  label: string;
  icon: string;
  hint: string;
}

const LAYER_CONFIG: Record<LayerType, LayerConfig> = {
  roads: {
    label: "Road Network",
    icon: "🛣",
    hint: "LineString — evacuation routes",
  },
  communities: {
    label: "Communities",
    icon: "🏘",
    hint: "Polygon — town / community footprints",
  },
  infrastructure: {
    label: "Infrastructure",
    icon: "⚡",
    hint: "Point — power, water, critical assets",
  },
};

// ── Per-layer sub-panel ──────────────────────────────────────────────────────

interface LayerPanelProps {
  type: LayerType;
  data: GeoJSON.FeatureCollection | null;
  visible: boolean;
  atRiskCount: number;
  onLoad: (data: GeoJSON.FeatureCollection) => void;
  onToggle: (visible: boolean) => void;
  onClear: () => void;
}

function LayerPanel({ type, data, visible, atRiskCount, onLoad, onToggle, onClear }: LayerPanelProps) {
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const cfg = LAYER_CONFIG[type];

  const parseGeoJSON = useCallback((raw: string) => {
    try {
      const parsed = JSON.parse(raw) as GeoJSON.GeoJsonObject;
      if (parsed.type !== "FeatureCollection" && parsed.type !== "Feature") {
        setError("File is not a GeoJSON FeatureCollection or Feature.");
        return;
      }
      const fc: GeoJSON.FeatureCollection =
        parsed.type === "Feature"
          ? { type: "FeatureCollection", features: [parsed as GeoJSON.Feature] }
          : (parsed as GeoJSON.FeatureCollection);
      onLoad(fc);
      setError(null);
    } catch {
      setError("Failed to parse GeoJSON.");
    }
  }, [onLoad]);

  const handleFile = useCallback((file: File) => {
    const reader = new FileReader();
    reader.onload = (e) => parseGeoJSON(e.target?.result as string);
    reader.readAsText(file);
  }, [parseGeoJSON]);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
  }, [handleFile]);

  const handleFetch = useCallback(async () => {
    const target = url.trim();
    if (!target) return;
    setLoading(true);
    setError(null);
    try {
      const resp = await fetch(target);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      parseGeoJSON(await resp.text());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Fetch failed.");
    } finally {
      setLoading(false);
    }
  }, [url, parseGeoJSON]);

  return (
    <div className="ov-layer">
      {/* Header row */}
      <div className="ov-layer-hdr">
        <span className="ov-icon">{cfg.icon}</span>
        <span className="ov-label">{cfg.label}</span>
        {data ? (
          <>
            <span className="ov-count">
              {data.features.length}
              {atRiskCount > 0 && (
                <span className="ov-at-risk"> · {atRiskCount} at-risk</span>
              )}
            </span>
            <button
              className={`ov-vis-btn ${visible ? "on" : "off"}`}
              onClick={() => onToggle(!visible)}
              title={visible ? "Hide layer" : "Show layer"}
            >
              {visible ? "ON" : "OFF"}
            </button>
            <button className="ov-clear-btn" onClick={onClear} title="Remove layer">
              ✕
            </button>
          </>
        ) : (
          <span className="ov-empty-hint">{cfg.hint}</span>
        )}
      </div>

      {/* Drop zone (only when no data) */}
      {!data && (
        <>
          <div
            className={`ov-drop${dragging ? " dragging" : ""}`}
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
            onClick={() => fileRef.current?.click()}
          >
            Drop GeoJSON or click to browse
            <input
              ref={fileRef}
              type="file"
              accept=".geojson,.json"
              style={{ display: "none" }}
              onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f); }}
            />
          </div>
          <div className="ov-url-row">
            <input
              type="text"
              className="ov-url-input"
              placeholder="…or GeoJSON URL"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") handleFetch(); }}
            />
            <button
              className="ov-fetch-btn"
              onClick={handleFetch}
              disabled={loading || !url.trim()}
            >
              {loading ? "…" : "Load"}
            </button>
          </div>
        </>
      )}

      {error && <div className="ov-error">{error}</div>}
    </div>
  );
}

// ── Public types ─────────────────────────────────────────────────────────────

export interface OverlayLayerData {
  data: GeoJSON.FeatureCollection | null;
  visible: boolean;
}

export interface OverlayLayers {
  roads: OverlayLayerData;
  communities: OverlayLayerData;
  infrastructure: OverlayLayerData;
}

// ── Main panel ───────────────────────────────────────────────────────────────

interface OverlayPanelProps {
  layers: OverlayLayers;
  atRiskCounts?: { roads: number; communities: number; infrastructure: number };
  onLayerLoad: (type: LayerType, data: GeoJSON.FeatureCollection) => void;
  onLayerToggle: (type: LayerType, visible: boolean) => void;
  onLayerClear: (type: LayerType) => void;
}

export default function OverlayPanel({
  layers,
  atRiskCounts = { roads: 0, communities: 0, infrastructure: 0 },
  onLayerLoad,
  onLayerToggle,
  onLayerClear,
}: OverlayPanelProps) {
  const hasAny = layers.roads.data || layers.communities.data || layers.infrastructure.data;
  const [edmontonLoading, setEdmontonLoading] = useState(false);
  const [edmontonError, setEdmontonError] = useState<string | null>(null);

  const loadEdmontonLayers = useCallback(async () => {
    setEdmontonLoading(true);
    setEdmontonError(null);
    try {
      await Promise.all(
        (["roads", "communities", "infrastructure"] as LayerType[]).map(async (type) => {
          const resp = await fetch(EDMONTON_LAYERS[type]);
          if (!resp.ok) throw new Error(`Failed to load ${type}: HTTP ${resp.status}`);
          const fc = await resp.json() as GeoJSON.FeatureCollection;
          onLayerLoad(type, fc);
        })
      );
    } catch (err) {
      setEdmontonError(err instanceof Error ? err.message : "Load failed.");
    } finally {
      setEdmontonLoading(false);
    }
  }, [onLayerLoad]);

  return (
    <div className="panel ov-panel">
      <div className="ov-panel-hdr">
        <h3>Infrastructure Overlay</h3>
        {hasAny && (
          <span className="ov-panel-hint">at-risk = P ≥ 50% burn zone</span>
        )}
      </div>
      {!hasAny && (
        <div className="ov-edmonton-row">
          <button
            className="ov-edmonton-btn"
            onClick={loadEdmontonLayers}
            disabled={edmontonLoading}
            title="Load Edmonton neighbourhoods, arterial roads, and critical infrastructure"
          >
            {edmontonLoading ? "Loading…" : "🏙 Load Edmonton Defaults"}
          </button>
          {edmontonError && <span className="ov-error">{edmontonError}</span>}
        </div>
      )}
      {(["roads", "communities", "infrastructure"] as LayerType[]).map((type) => (
        <LayerPanel
          key={type}
          type={type}
          data={layers[type].data}
          visible={layers[type].visible}
          atRiskCount={atRiskCounts[type]}
          onLoad={(d) => onLayerLoad(type, d)}
          onToggle={(v) => onLayerToggle(type, v)}
          onClear={() => onLayerClear(type)}
        />
      ))}
    </div>
  );
}
