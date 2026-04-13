/**
 * Map Markup Panel — right-panel section for ICS annotation tools.
 *
 * Replaces the floating bottom-left toolbar + AnnotationSymbolPicker flyout.
 * All annotation state is owned by EOCConsole; this component is purely presentational.
 */

import type { AnnotationLayer, ICSSymbolKey, IncidentAnnotation } from "../types/incident";
import {
  ANNOTATION_LAYER_LABELS,
  SYMBOLS_BY_LAYER,
  DRAWING_TOOLS,
} from "../types/incident";
import { SymbolIcon } from "./AnnotationSymbolPicker";

// ── Role hint per layer ───────────────────────────────────────────────────────

const LAYER_ROLE_HINT: Record<AnnotationLayer, string> = {
  situation:  "IC / All Sections",
  ics204:     "Operations Section Chief",
  ics205:     "Logistics — Comms Unit Leader",
  ics206:     "Logistics — Medical Unit Leader",
  evac:       "IC / Operations Section",
};

// Layer accent colours (match AnnotationSymbolPicker)
const LAYER_COLORS: Record<AnnotationLayer, string> = {
  situation: "#4caf50",
  ics204:    "#ff9800",
  ics205:    "#9c27b0",
  ics206:    "#f44336",
  evac:      "#00bcd4",
};

const LAYERS: AnnotationLayer[] = ["situation", "ics204", "ics205", "ics206", "evac"];

const DRAWING_TOOL_KEYS = new Set(DRAWING_TOOLS.map(t => t.key));

const COLOR_SWATCHES: Array<{ color: string; label: string }> = [
  { color: "#f44336", label: "Red" },
  { color: "#ff9800", label: "Orange" },
  { color: "#ffeb3b", label: "Yellow" },
  { color: "#4caf50", label: "Green" },
  { color: "#00bcd4", label: "Cyan" },
  { color: "#2196f3", label: "Blue" },
  { color: "#9c27b0", label: "Purple" },
  { color: "#ffffff", label: "White" },
  { color: "#888888", label: "Grey" },
];

// ── Props ─────────────────────────────────────────────────────────────────────

interface MapMarkupPanelProps {
  activeLayer: AnnotationLayer;
  activeSymbolKey: ICSSymbolKey | null;
  activeColor: string | null;
  annotations: IncidentAnnotation[];
  isFetchingFacilities: boolean;
  canFetchFacilities: boolean;
  onLayerChange: (layer: AnnotationLayer) => void;
  onSymbolSelect: (key: ICSSymbolKey) => void;
  onColorChange: (color: string | null) => void;
  onFetchFacilities: () => void;
  onRemoveAnnotation: (id: string) => void;
  onClearLayer: (layer: AnnotationLayer) => void;
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function MapMarkupPanel({
  activeLayer,
  activeSymbolKey,
  activeColor,
  annotations,
  isFetchingFacilities,
  canFetchFacilities,
  onLayerChange,
  onSymbolSelect,
  onColorChange,
  onFetchFacilities,
  onRemoveAnnotation,
  onClearLayer,
}: MapMarkupPanelProps) {
  const layerColor = LAYER_COLORS[activeLayer];
  const symbols = SYMBOLS_BY_LAYER[activeLayer].filter(s => !DRAWING_TOOL_KEYS.has(s.key));
  const activeLayerAnns = annotations.filter(a => a.layer === activeLayer);
  const otherAnns = annotations.filter(a => a.layer !== activeLayer);
  const allAnns = [...activeLayerAnns, ...otherAnns];

  return (
    <div className="panel mmp-panel">
      <div className="mmp-header">
        <span className="mmp-title">Map Markup</span>
        <span className="mmp-ann-count">{annotations.length > 0 ? `${annotations.length} placed` : ""}</span>
      </div>

      {/* Layer tabs */}
      <div className="mmp-layer-tabs">
        {LAYERS.map(layer => (
          <button
            key={layer}
            className={`mmp-layer-tab${activeLayer === layer ? " active" : ""}`}
            style={activeLayer === layer ? { borderColor: LAYER_COLORS[layer], color: LAYER_COLORS[layer] } : {}}
            onClick={() => onLayerChange(layer)}
            title={ANNOTATION_LAYER_LABELS[layer]}
          >
            {ANNOTATION_LAYER_LABELS[layer]}
          </button>
        ))}
      </div>
      <div className="mmp-role-hint">Prepared by: {LAYER_ROLE_HINT[activeLayer]}</div>

      {/* ICS symbols for active layer */}
      <div className="mmp-section-label">Symbols</div>
      <div className="mmp-symbols">
        {symbols.map(sym => (
          <button
            key={sym.key}
            className={`mmp-sym-btn${activeSymbolKey === sym.key ? " active" : ""}`}
            style={activeSymbolKey === sym.key ? { borderColor: layerColor, background: `${layerColor}22` } : {}}
            onClick={() => onSymbolSelect(sym.key)}
            title={sym.label}
          >
            <SymbolIcon symbolKey={sym.key} color={sym.color} size={22} />
            <span className="mmp-sym-label">{sym.label}</span>
          </button>
        ))}
      </div>

      {/* Drawing tools */}
      <div className="mmp-section-label">Drawing Tools</div>
      <div className="mmp-symbols">
        {DRAWING_TOOLS.map(sym => (
          <button
            key={sym.key}
            className={`mmp-sym-btn${activeSymbolKey === sym.key ? " active" : ""}`}
            style={activeSymbolKey === sym.key ? { borderColor: "#aaa", background: "rgba(255,255,255,0.1)" } : {}}
            onClick={() => onSymbolSelect(sym.key)}
            title={sym.label}
          >
            <SymbolIcon symbolKey={sym.key} color={sym.color} size={22} />
            <span className="mmp-sym-label">{sym.label}</span>
          </button>
        ))}
      </div>

      {/* Color swatches */}
      <div className="mmp-color-row">
        <button
          className={`mmp-color-swatch mmp-color-swatch--auto${activeColor === null ? " active" : ""}`}
          onClick={() => onColorChange(null)}
          title="Default color (from symbol)"
        >auto</button>
        {COLOR_SWATCHES.map(({ color, label }) => (
          <button
            key={color}
            className={`mmp-color-swatch${activeColor === color ? " active" : ""}`}
            style={{ background: color, boxShadow: activeColor === color ? "0 0 0 2px #fff" : undefined }}
            onClick={() => onColorChange(activeColor === color ? null : color)}
            title={label}
          />
        ))}
      </div>

      {/* Annotation list */}
      {allAnns.length > 0 && (
        <>
          <div className="mmp-section-label" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span>Placed ({allAnns.length})</span>
            {activeLayerAnns.length > 0 && (
              <button className="mmp-clear-layer-btn" onClick={() => onClearLayer(activeLayer)}>
                Clear {ANNOTATION_LAYER_LABELS[activeLayer]}
              </button>
            )}
          </div>
          <div className="mmp-ann-list">
            {allAnns.map(ann => (
              <div key={ann.id} className="mmp-ann-row">
                <span
                  className="mmp-ann-layer-badge"
                  style={{ background: `${LAYER_COLORS[ann.layer]}22`, color: LAYER_COLORS[ann.layer] }}
                >
                  {ANNOTATION_LAYER_LABELS[ann.layer]}
                </span>
                <span className="mmp-ann-name" title={ann.label}>{ann.label}</span>
                <button className="mmp-ann-delete" onClick={() => onRemoveAnnotation(ann.id)} title="Remove">×</button>
              </div>
            ))}
          </div>
        </>
      )}

      {/* Fetch OSM facilities */}
      <button
        className={`mmp-fetch-btn${isFetchingFacilities ? " loading" : ""}`}
        onClick={onFetchFacilities}
        disabled={!canFetchFacilities || isFetchingFacilities}
        title={canFetchFacilities ? "Fetch nearby facilities from OpenStreetMap" : "Set incident location first"}
      >
        {isFetchingFacilities ? "Fetching…" : "📡 Fetch OSM Facilities"}
      </button>
    </div>
  );
}
