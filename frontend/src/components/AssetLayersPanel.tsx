/**
 * Asset and road layers (Setup, More). The Edmonton layers load automatically with the
 * Edmonton fuel grid; "Add your own layer" (GeoJSON file or URL) is the advanced path for other
 * jurisdictions. Points and polygons become assets, lines become roads.
 */

import { memo, useCallback, useRef, useState } from "react";
import InfoTip from "./InfoTip";
import { TIPS } from "../content/explanations";

export interface UserLayer {
  id: string;
  name: string;
  data: GeoJSON.FeatureCollection;
}

interface AssetLayersPanelProps {
  /** Bundled Edmonton layers: counts, or null when not loaded (grid off, or loading) */
  edmonton: { assets: number; roads: number } | null;
  edmontonNote: string;
  userLayers: UserLayer[];
  onAddLayer: (name: string, data: GeoJSON.FeatureCollection) => void;
  onRemoveLayer: (id: string) => void;
}

function parseGeoJSON(raw: string): GeoJSON.FeatureCollection {
  const parsed = JSON.parse(raw) as GeoJSON.GeoJsonObject;
  if (parsed.type === "FeatureCollection") return parsed as GeoJSON.FeatureCollection;
  if (parsed.type === "Feature") return { type: "FeatureCollection", features: [parsed as GeoJSON.Feature] };
  throw new Error("Not a GeoJSON FeatureCollection or Feature.");
}

function summary(fc: GeoJSON.FeatureCollection): string {
  let assets = 0;
  let lines = 0;
  for (const f of fc.features) {
    const t = f.geometry?.type;
    if (t === "LineString" || t === "MultiLineString") lines++;
    else if (t) assets++;
  }
  return `${assets} asset${assets === 1 ? "" : "s"} · ${lines} road line${lines === 1 ? "" : "s"}`;
}

function AssetLayersPanel({ edmonton, edmontonNote, userLayers, onAddLayer, onRemoveLayer }: AssetLayersPanelProps) {
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const add = useCallback(
    (name: string, raw: string) => {
      try {
        onAddLayer(name, parseGeoJSON(raw));
        setError(null);
      } catch (e) {
        setError(e instanceof Error && e.message.startsWith("Not a") ? e.message : "Could not read that file as GeoJSON.");
      }
    },
    [onAddLayer],
  );

  const handleFile = useCallback(
    (file: File) => {
      const reader = new FileReader();
      reader.onload = (e) => add(file.name, String(e.target?.result ?? ""));
      reader.readAsText(file);
    },
    [add],
  );

  const handleFetch = useCallback(async () => {
    const target = url.trim();
    if (!target) return;
    setLoading(true);
    setError(null);
    try {
      const resp = await fetch(target);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      add(target.split("/").pop() || target, await resp.text());
      setUrl("");
    } catch (err) {
      setError(err instanceof Error ? `Could not load the URL (${err.message}).` : "Could not load the URL.");
    } finally {
      setLoading(false);
    }
  }, [url, add]);

  return (
    <div className="asset-layers">
      {/* The section summary gives the asset count; only a load problem needs a line here */}
      {!edmonton && <p className="hint-sm">{edmontonNote}</p>}
      <details className="asset-layers-own">
        <summary>Add your own layer</summary>
        <div className="status-line">
          GeoJSON (WGS84) <InfoTip label="About the layer format" text={TIPS.ownLayer} />
        </div>
        <div className="field-row">
          <button type="button" className="btn-secondary btn-inline" onClick={() => fileRef.current?.click()}>
            Choose a file…
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".geojson,.json,application/geo+json,application/json"
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) handleFile(f);
              e.target.value = "";
            }}
          />
        </div>
        <div className="field-row">
          <label className="field">
            or a GeoJSON URL
            <input
              type="url"
              value={url}
              placeholder="https://…/layer.geojson"
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleFetch();
              }}
            />
          </label>
          <button type="button" className="btn-secondary btn-inline" onClick={handleFetch} disabled={loading || !url.trim()}>
            {loading ? "Loading…" : "Load"}
          </button>
        </div>
        {error && (
          <p className="hint-sm asset-layers-error" role="alert">
            {error}
          </p>
        )}
        {userLayers.length > 0 && (
          <ul className="asset-layers-list" aria-label="Your layers">
            {userLayers.map((l) => (
              <li key={l.id}>
                <span className="asset-layers-name">{l.name}</span>
                <span className="hint-sm">{summary(l.data)}</span>
                <button type="button" className="btn-small" onClick={() => onRemoveLayer(l.id)} aria-label={`Remove layer ${l.name}`}>
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
      </details>
    </div>
  );
}

export default memo(AssetLayersPanel);
