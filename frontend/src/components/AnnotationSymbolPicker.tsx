/** Form-aware symbol palette for EOC map annotation. */

import type { AnnotationLayer, ICSSymbolKey } from "../types/incident";
import { SYMBOLS_BY_LAYER, ANNOTATION_LAYER_LABELS, DRAWING_TOOLS } from "../types/incident";

const DRAWING_TOOL_KEYS = new Set(DRAWING_TOOLS.map(t => t.key));

interface AnnotationSymbolPickerProps {
  activeLayer: AnnotationLayer;
  activeSymbol: ICSSymbolKey | null;
  onLayerChange: (layer: AnnotationLayer) => void;
  onSymbolSelect: (key: ICSSymbolKey) => void;
}

const LAYERS: AnnotationLayer[] = ["situation", "ics204", "ics205", "ics206", "evac"];

const LAYER_COLORS: Record<AnnotationLayer, string> = {
  situation: "#4caf50",
  ics204: "#ff9800",
  ics205: "#9c27b0",
  ics206: "#f44336",
  evac: "#00bcd4",
};

export default function AnnotationSymbolPicker({
  activeLayer,
  activeSymbol,
  onLayerChange,
  onSymbolSelect,
}: AnnotationSymbolPickerProps) {
  const symbols = SYMBOLS_BY_LAYER[activeLayer];
  const layerColor = LAYER_COLORS[activeLayer];

  return (
    <div className="annotation-picker">
      {/* Layer selector */}
      <div className="annotation-picker-layers">
        {LAYERS.map((layer) => (
          <button
            key={layer}
            className={`annotation-layer-btn${activeLayer === layer ? " active" : ""}`}
            style={activeLayer === layer ? { borderColor: LAYER_COLORS[layer], color: LAYER_COLORS[layer] } : {}}
            onClick={() => onLayerChange(layer)}
            title={ANNOTATION_LAYER_LABELS[layer]}
          >
            {ANNOTATION_LAYER_LABELS[layer]}
          </button>
        ))}
      </div>

      {/* ICS symbols */}
      <div className="annotation-picker-symbols">
        {symbols.filter(s => !DRAWING_TOOL_KEYS.has(s.key)).map((sym) => (
          <button
            key={sym.key}
            className={`annotation-symbol-btn${activeSymbol === sym.key ? " active" : ""}`}
            style={activeSymbol === sym.key ? { borderColor: layerColor, background: `${layerColor}22` } : {}}
            onClick={() => onSymbolSelect(sym.key)}
            title={sym.label}
          >
            <SymbolIcon symbolKey={sym.key} color={sym.color} />
            <span className="annotation-symbol-label">{sym.label}</span>
          </button>
        ))}
      </div>

      {/* Universal drawing tools — always visible, separated */}
      <div className="annotation-picker-divider" />
      <div className="annotation-picker-symbols">
        {DRAWING_TOOLS.map((sym) => (
          <button
            key={sym.key}
            className={`annotation-symbol-btn${activeSymbol === sym.key ? " active" : ""}`}
            style={activeSymbol === sym.key ? { borderColor: "#aaa", background: "rgba(255,255,255,0.1)" } : {}}
            onClick={() => onSymbolSelect(sym.key)}
            title={sym.label}
          >
            <SymbolIcon symbolKey={sym.key} color={sym.color} />
            <span className="annotation-symbol-label">{sym.label}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

/** Inline SVG icon for each symbol type. Falls back to short code label. */
export function SymbolIcon({ symbolKey, color, size = 20 }: { symbolKey: ICSSymbolKey; color: string; size?: number }) {
  const s = size;
  const h = s / 2;

  switch (symbolKey) {
    case "medical_aid_station":
      return (
        <svg width={s} height={s} viewBox={`0 0 ${s} ${s}`}>
          <rect x={h - 2} y={2} width={4} height={s - 4} fill={color} />
          <rect x={2} y={h - 2} width={s - 4} height={4} fill={color} />
        </svg>
      );
    case "hospital":
      return (
        <svg width={s} height={s} viewBox={`0 0 ${s} ${s}`}>
          <rect x={1} y={1} width={s - 2} height={s - 2} fill="none" stroke={color} strokeWidth={2} />
          <text x={h} y={h + 4} textAnchor="middle" fontSize={s * 0.6} fontWeight="bold" fill={color}>H</text>
        </svg>
      );
    case "medevac_lz":
      return (
        <svg width={s} height={s} viewBox={`0 0 ${s} ${s}`}>
          <circle cx={h} cy={h} r={h - 1} fill="none" stroke={color} strokeWidth={2} />
          <text x={h} y={h + 4} textAnchor="middle" fontSize={s * 0.55} fontWeight="bold" fill={color}>H</text>
        </svg>
      );
    case "command_post":
    case "icp":
      return (
        <svg width={s} height={s} viewBox={`0 0 ${s} ${s}`}>
          <polygon points={`${h},2 ${h+4},${h} ${h},${s-2} ${h-4},${h}`} fill={color} />
        </svg>
      );
    case "checkpoint":
      return (
        <svg width={s} height={s} viewBox={`0 0 ${s} ${s}`}>
          <circle cx={h} cy={h} r={h - 1} fill="none" stroke={color} strokeWidth={2} />
          <text x={h} y={h + 4} textAnchor="middle" fontSize={s * 0.5} fontWeight="bold" fill={color}>C</text>
        </svg>
      );
    case "staging_area":
      return (
        <svg width={s} height={s} viewBox={`0 0 ${s} ${s}`}>
          <rect x={1} y={1} width={s - 2} height={s - 2} fill="none" stroke={color} strokeWidth={2} />
          <text x={h} y={h + 4} textAnchor="middle" fontSize={s * 0.5} fontWeight="bold" fill={color}>S</text>
        </svg>
      );
    case "water_fill":
      return (
        <svg width={s} height={s} viewBox={`0 0 ${s} ${s}`}>
          <circle cx={h} cy={h} r={h - 1} fill={color} />
          <text x={h} y={h + 4} textAnchor="middle" fontSize={s * 0.5} fontWeight="bold" fill="#000">W</text>
        </svg>
      );
    case "reception_centre":
      return (
        <svg width={s} height={s} viewBox={`0 0 ${s} ${s}`}>
          <polygon points={`${h},2 ${s-2},${h} ${s-3},${s-2} 3,${s-2} 2,${h}`} fill="none" stroke={color} strokeWidth={2} />
        </svg>
      );
    case "vulnerable_pop":
      return (
        <svg width={s} height={s} viewBox={`0 0 ${s} ${s}`}>
          <polygon points={`${h},2 ${s-2},${s-2} 2,${s-2}`} fill="none" stroke={color} strokeWidth={2} />
          <text x={h} y={s - 5} textAnchor="middle" fontSize={s * 0.4} fontWeight="bold" fill={color}>!</text>
        </svg>
      );
    case "radio_repeater":
      return (
        <svg width={s} height={s} viewBox={`0 0 ${s} ${s}`}>
          <circle cx={h} cy={h} r={h - 1} fill="none" stroke={color} strokeWidth={2} />
          <line x1={h} y1={h} x2={h} y2={2} stroke={color} strokeWidth={2} />
          <line x1={h} y1={2} x2={h - 4} y2={6} stroke={color} strokeWidth={1.5} />
          <line x1={h} y1={2} x2={h + 4} y2={6} stroke={color} strokeWidth={1.5} />
        </svg>
      );
    case "generic_point":
      return (
        <svg width={s} height={s} viewBox={`0 0 ${s} ${s}`}>
          <circle cx={h} cy={h} r={h - 2} fill={color} opacity={0.85} />
          <text x={h} y={h + 3} textAnchor="middle" fontSize={s * 0.4} fontWeight="bold" fill="#000">●</text>
        </svg>
      );
    case "freehand_path":
      return <span style={{ fontSize: s * 0.7, color }}>{"\u270F"}</span>;
    case "text_label":
      return <span style={{ fontSize: s * 0.7, fontWeight: "bold", color }}>T</span>;
    default: {
      // Generic: short code in circle
      const def = [symbolKey].map((k) => k.replace(/_/g, " ").slice(0, 3).toUpperCase()).join("");
      return (
        <svg width={s} height={s} viewBox={`0 0 ${s} ${s}`}>
          <circle cx={h} cy={h} r={h - 1} fill="none" stroke={color} strokeWidth={2} />
          <text x={h} y={h + 3} textAnchor="middle" fontSize={s * 0.35} fill={color}>{def}</text>
        </svg>
      );
    }
  }
}
