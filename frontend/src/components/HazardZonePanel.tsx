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
  const [selectedZoneIndex, setSelectedZoneIndex] = useState(0);
  const [confirmClear, setConfirmClear] = useState(false);

  const def = HAZARD_DEFS.find((d) => d.key === hazardType) ?? HAZARD_DEFS[0];
  const zoneNames = def.zoneNames;
  const zoneColors = def.zoneColors;

  const selectedName = zoneNames[selectedZoneIndex] ?? zoneNames[0];
  const selectedColor = zoneColors[selectedZoneIndex] ?? zoneColors[0];

  return (
    <div className="panel hz-panel">
      {/* Header */}
      <div className="hz-header">
        <span className="hz-title">Hazard Zones</span>
        {zones.length > 0 && (
          <span className="hz-count">{zones.length}</span>
        )}
        {isDrawing && (
          <span className="hz-drawing-badge">Drawing</span>
        )}
      </div>
      <div className="hz-criticality">
        Define zones first — drives infrastructure at-risk classification and evacuation triggers.
      </div>

      {/* Zone type selector */}
      {!isDrawing && (
        <>
          <div className="hz-section-label">Zone Type</div>
          <div className="hz-type-tabs">
            {zoneNames.map((name, idx) => (
              <button
                key={name}
                className={`hz-type-tab${selectedZoneIndex === idx ? " active" : ""}`}
                style={selectedZoneIndex === idx ? { borderColor: zoneColors[idx], color: zoneColors[idx] } : {}}
                onClick={() => setSelectedZoneIndex(idx)}
              >
                <span className="hz-swatch" style={{ background: zoneColors[idx] }} />
                {name}
              </button>
            ))}
          </div>

          <button
            className="hz-draw-btn"
            style={{ borderColor: selectedColor, color: selectedColor }}
            onClick={() => onDrawStart(selectedName, selectedColor)}
          >
            ◉ Draw {selectedName}
          </button>
        </>
      )}

      {/* Drawing mode */}
      {isDrawing && (
        <div className="hz-drawing-state">
          <div className="hz-drawing-hint">
            Drawing: <strong style={{ color: selectedColor }}>{selectedName}</strong>
            {"  ·  "}
            {drawingPoints.length} pt{drawingPoints.length !== 1 ? "s" : ""}
            {drawingPoints.length >= 3 && (
              <> · {polygonAreaKm2(drawingPoints).toFixed(1)} km²</>
            )}
          </div>
          {drawingPoints.length < 3 && (
            <div className="hz-drawing-hint" style={{ opacity: 0.6 }}>Need ≥3 points to close</div>
          )}
          <div className="hz-drawing-btns">
            <button
              className="hz-close-btn"
              onClick={onDrawClose}
              disabled={drawingPoints.length < 3}
            >
              ✓ Close Zone
            </button>
            <button className="hz-cancel-btn" onClick={onDrawCancel}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Zone list */}
      {zones.length > 0 && (
        <div className="hz-zone-list">
          {zones.map((z) => {
            const area = polygonAreaKm2(z.polygon);
            return (
              <div key={z.id} className="hz-zone-row">
                <span className="hz-swatch" style={{ background: z.color }} />
                <span className="hz-zone-name">{z.name}</span>
                <span className="hz-zone-area">
                  {area >= 1 ? `${area.toFixed(1)} km²` : `${(area * 100).toFixed(0)} ha`}
                </span>
                <button
                  className="hz-delete-btn"
                  onClick={() => onRemoveZone(z.id)}
                  title="Remove zone"
                >×</button>
              </div>
            );
          })}

          {/* Clear all */}
          {!isDrawing && (
            confirmClear ? (
              <div className="hz-confirm-row">
                <span className="hz-confirm-label">Clear all zones?</span>
                <button className="hz-confirm-yes" onClick={() => { onClearAll(); setConfirmClear(false); }}>Yes</button>
                <button className="hz-confirm-no" onClick={() => setConfirmClear(false)}>No</button>
              </div>
            ) : (
              <button className="hz-clear-all-btn" onClick={() => setConfirmClear(true)}>
                ✕ Clear all zones
              </button>
            )
          )}
        </div>
      )}

      {zones.length === 0 && !isDrawing && (
        <div className="hz-empty">No zones drawn yet.</div>
      )}
    </div>
  );
}
