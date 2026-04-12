/** Hazard zone drawing panel — click-to-draw polygon zones on the map. */

import { useState } from "react";
import type { HazardType, HazardZone } from "../types/incident";
import { HAZARD_DEFS } from "../types/incident";

/** Approximate polygon area in km² using spherical shoelace formula. */
function polygonAreaKm2(coords: [number, number][]): number {
  if (coords.length < 3) return 0;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const R = 6371;
  let area = 0;
  for (let i = 0; i < coords.length; i++) {
    const [lng1, lat1] = coords[i];
    const [lng2, lat2] = coords[(i + 1) % coords.length];
    area +=
      toRad(lng2 - lng1) *
      (2 + Math.sin(toRad(lat1)) + Math.sin(toRad(lat2)));
  }
  return Math.abs((area * R * R) / 2);
}

interface HazardZonePanelProps {
  hazardType: HazardType;
  zones: HazardZone[];
  isDrawing: boolean;
  drawingPoints: [number, number][];
  onDrawStart: (zoneName: string, color: string) => void;
  onDrawCancel: () => void;
  onDrawClose: () => void;
  onRemoveZone: (id: string) => void;
  onClearAll: () => void;
}

export default function HazardZonePanel({
  hazardType,
  zones,
  isDrawing,
  drawingPoints,
  onDrawStart,
  onDrawCancel,
  onDrawClose,
  onRemoveZone,
  onClearAll,
}: HazardZonePanelProps) {
  const [open, setOpen] = useState(true);
  const [selectedZoneIndex, setSelectedZoneIndex] = useState(0);

  const def = HAZARD_DEFS.find((d) => d.key === hazardType) ?? HAZARD_DEFS[0];
  const zoneNames = def.zoneNames;
  const zoneColors = def.zoneColors;

  const selectedName = zoneNames[selectedZoneIndex] ?? zoneNames[0];
  const selectedColor = zoneColors[selectedZoneIndex] ?? zoneColors[0];

  const handleDrawStart = () => {
    onDrawStart(selectedName, selectedColor);
  };

  return (
    <div className="panel hazard-zone-panel">
      <button
        className="panel-collapse-btn"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span>Hazard Zones</span>
        <span className="collapse-icon">{open ? "▲" : "▼"}</span>
        {zones.length > 0 && (
          <span className="scenario-count-badge">{zones.length}</span>
        )}
        {isDrawing && (
          <span className="scenario-count-badge" style={{ background: "#3d5a80" }}>Drawing</span>
        )}
      </button>

      {open && (
        <div className="scenario-body">
          {/* Zone type selector */}
          {!isDrawing && (
            <div className="section" style={{ paddingTop: 0 }}>
              <h4>Zone Type</h4>
              <div className="zone-type-row">
                {zoneNames.map((name, idx) => (
                  <button
                    key={name}
                    className={`zone-type-btn${selectedZoneIndex === idx ? " active" : ""}`}
                    style={selectedZoneIndex === idx ? { borderColor: zoneColors[idx], background: `${zoneColors[idx]}22`, color: zoneColors[idx] } : {}}
                    onClick={() => setSelectedZoneIndex(idx)}
                  >
                    <span
                      className="zone-type-swatch"
                      style={{ background: zoneColors[idx] }}
                    />
                    {name}
                  </button>
                ))}
              </div>

              <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
                <button
                  className="btn-primary"
                  style={{ flex: 1, padding: "6px 0", fontSize: "0.85em", background: selectedColor + "cc", borderColor: selectedColor }}
                  onClick={handleDrawStart}
                >
                  ◉ Draw Zone
                </button>
                {zones.length > 0 && (
                  <button
                    className="btn-secondary"
                    style={{ padding: "6px 10px", fontSize: "0.85em", borderColor: "#8b2020", color: "#e57373" }}
                    onClick={() => {
                      if (confirm("Clear all hazard zones?")) onClearAll();
                    }}
                    title="Clear all zones"
                  >
                    ✕ All
                  </button>
                )}
              </div>
            </div>
          )}

          {/* Drawing mode */}
          {isDrawing && (
            <div className="section zone-drawing-controls" style={{ paddingTop: 0 }}>
              <div className="hint" style={{ marginBottom: 8 }}>
                Drawing: <strong style={{ color: selectedColor }}>{selectedName}</strong>
                <br />
                {drawingPoints.length} point{drawingPoints.length !== 1 ? "s" : ""} placed
                {drawingPoints.length >= 3 && (
                  <span> · {polygonAreaKm2(drawingPoints).toFixed(2)} km²</span>
                )}
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <button
                  className="btn-primary"
                  style={{ flex: 1, padding: "6px 0", fontSize: "0.85em" }}
                  onClick={onDrawClose}
                  disabled={drawingPoints.length < 3}
                >
                  Close Zone
                </button>
                <button
                  className="btn-secondary"
                  style={{ padding: "6px 10px", fontSize: "0.85em" }}
                  onClick={onDrawCancel}
                >
                  Cancel
                </button>
              </div>
              {drawingPoints.length < 3 && (
                <div className="hint" style={{ marginTop: 6 }}>
                  Need at least 3 points to close.
                </div>
              )}
            </div>
          )}

          {/* Zones list */}
          {zones.length === 0 && !isDrawing ? (
            <div className="hint">No zones drawn yet.</div>
          ) : (
            <div className="zone-list">
              {zones.map((z) => {
                const area = polygonAreaKm2(z.polygon);
                return (
                  <div key={z.id} className="zone-item">
                    <div className="zone-item-header">
                      <span
                        className="zone-item-swatch"
                        style={{ background: z.color }}
                      />
                      <span className="zone-item-name">{z.name}</span>
                      <span className="zone-item-area">
                        {area >= 1 ? `${area.toFixed(1)} km²` : `${(area * 100).toFixed(0)} ha`}
                      </span>
                      <button
                        className="btn-secondary"
                        style={{ fontSize: "0.76em", padding: "2px 7px", borderColor: "#8b2020", color: "#e57373" }}
                        onClick={() => onRemoveZone(z.id)}
                        title="Remove this zone"
                      >
                        ✕
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
