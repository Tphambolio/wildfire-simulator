/**
 * Evacuation Trigger Zones panel — Alberta Emergency Management Act model.
 *
 * Displays neighbourhood-level evacuation tiers and allows operators to:
 *  - Toggle zone overlay visibility
 *  - Adjust zone perimeter expansion (pulls in more/fewer neighbourhoods)
 */

import type { EvacZone, EvacZoneLabel } from "../utils/evacZones";

interface EvacZonesPanelProps {
  zones: EvacZone[];
  visible: boolean;
  scales: Record<EvacZoneLabel, number>;
  onToggleVisible: (v: boolean) => void;
  onScaleChange: (label: EvacZoneLabel, scale: number) => void;
}

const ZONE_ORDER: EvacZoneLabel[] = ["Order", "Alert", "Watch"];

const ZONE_BG: Record<EvacZoneLabel, string> = {
  Order: "rgba(211,47,47,0.15)",
  Alert: "rgba(245,124,0,0.15)",
  Watch: "rgba(249,168,37,0.12)",
};

const ZONE_BORDER: Record<EvacZoneLabel, string> = {
  Order: "#d32f2f",
  Alert: "#f57c00",
  Watch: "#f9a825",
};

export default function EvacZonesPanel({
  zones,
  visible,
  scales,
  onToggleVisible,
  onScaleChange,
}: EvacZonesPanelProps) {
  if (zones.length === 0) return null;

  const totalNeighbourhoods = zones.reduce((s, z) => s + z.communitiesAtRisk.length, 0);

  return (
    <div className="panel evac-panel">
      <div className="evac-header">
        <div>
          <h3>Evacuation Zones</h3>
          {totalNeighbourhoods > 0 && (
            <span className="evac-total-count">{totalNeighbourhoods} neighbourhood{totalNeighbourhoods !== 1 ? "s" : ""} affected</span>
          )}
        </div>
        <button
          className={`ov-vis-btn ${visible ? "on" : "off"}`}
          onClick={() => onToggleVisible(!visible)}
          title={visible ? "Hide zones" : "Show zones"}
        >
          {visible ? "ON" : "OFF"}
        </button>
      </div>

      {ZONE_ORDER.filter((l) => zones.some((z) => z.label === l)).map((label) => {
        const zone = zones.find((z) => z.label === label)!;
        const scale = scales[label];
        const hasNeighbourhoods = zone.communitiesAtRisk.length > 0;

        return (
          <div
            key={label}
            className="evac-zone-card"
            style={{
              background: ZONE_BG[label],
              borderLeft: `4px solid ${ZONE_BORDER[label]}`,
            }}
          >
            {/* Tier header */}
            <div className="evac-zone-title">
              <span className="evac-zone-dot" style={{ background: zone.color }} />
              <div className="evac-zone-title-text">
                <strong>Evacuation {label}</strong>
                <span className="evac-zone-action">{zone.action}</span>
              </div>
              <span className="evac-zone-time">{zone.timeRangeLabel}</span>
            </div>

            {/* Neighbourhood list */}
            {hasNeighbourhoods ? (
              <ul className="evac-nbhd-list">
                {zone.communitiesAtRisk.map((name) => (
                  <li key={name} className="evac-nbhd-item">{name}</li>
                ))}
              </ul>
            ) : (
              <p className="evac-no-nbhd">
                No neighbourhoods in range
                {!hasNeighbourhoods && zone.communitiesAtRisk.length === 0
                  ? " — load communities layer to show affected areas"
                  : ""}
              </p>
            )}

            {/* Zone expansion */}
            <div className="evac-zone-scale-row">
              <span className="evac-scale-label">Zone expansion</span>
              <input
                type="range"
                min={0.5}
                max={2.0}
                step={0.05}
                value={scale}
                className="evac-scale-slider"
                aria-label={`Evacuation ${label} zone expansion`}
                onChange={(e) => onScaleChange(label, parseFloat(e.target.value))}
              />
              <span className="evac-scale-value">{scale.toFixed(2)}×</span>
              {scale !== 1 && (
                <button
                  className="evac-scale-reset"
                  onClick={() => onScaleChange(label, 1)}
                  title="Reset to model boundary"
                >
                  ↺
                </button>
              )}
            </div>
          </div>
        );
      })}

      <div className="evac-legend">
        <span style={{ color: "#d32f2f" }}>■</span> Order — leave now &nbsp;
        <span style={{ color: "#f57c00" }}>■</span> Alert — be ready &nbsp;
        <span style={{ color: "#f9a825" }}>■</span> Watch — monitor
      </div>
      <div className="evac-authority">Alberta Emergency Management Act</div>
    </div>
  );
}
