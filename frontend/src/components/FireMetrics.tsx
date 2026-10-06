/** Fire metrics display panel showing current frame statistics. */

import type { SimulationFrame } from "../types/simulation";
import HfiClassChip from "./HfiClassChip";

interface FireMetricsProps {
  frame: SimulationFrame | null;
  status: string | null;
  totalFrames: number;
}


function formatFireType(ft: string): string {
  return ft
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export default function FireMetrics({ frame, status, totalFrames }: FireMetricsProps) {
  if (!frame) {
    return (
      <div className="panel metrics-panel">
        <h3>Fire Metrics</h3>
        <div className="hint">
          {status === "running" ? "Waiting for first frame..." : "Run a simulation to see metrics"}
        </div>
      </div>
    );
  }

  const isCAMode = Array.isArray(frame.burned_cells);

  return (
    <div className="panel metrics-panel">
      <h3>Fire Metrics</h3>

      {(frame.ignition_snapped_m ?? 0) > 0 && (
        <div className="notice notice-warning">
          Ignition snapped {Math.round(frame.ignition_snapped_m!)} m to nearest fuel cell
        </div>
      )}

      <div className="metrics-tags">
        <span className="tag" title={isCAMode ? "Grid (level-set) spread on the fuel grid" : "Huygens wavelet perimeter spread"}>
          {isCAMode ? "CA Grid" : "Huygens"}
        </span>
        {(frame.num_fronts ?? 1) > 1 && (
          <span className="tag">{frame.num_fronts} fronts</span>
        )}
      </div>

      <div className="metrics-rows">
        <div className="metric-row">
          <span className="metric-label">Time Elapsed</span>
          <span className="metric-value mono">T+{frame.time_hours.toFixed(1)}h</span>
        </div>
        <div className="metric-row">
          <span className="metric-label">Area Burned</span>
          <span className="metric-value">{frame.area_ha.toFixed(1)} ha</span>
        </div>
        <div className="metric-row">
          <span className="metric-label">Head ROS</span>
          <span className="metric-value">{frame.head_ros_m_min.toFixed(1)} m/min</span>
        </div>
        {frame.head && (
          <>
            <div className="metric-row">
              <span className="metric-label">Head spreading toward</span>
              <span className="metric-value">
                {["N", "NE", "E", "SE", "S", "SW", "W", "NW"][Math.round(frame.head.raz / 45) % 8]}{" "}
                ({frame.head.raz.toFixed(0)}°)
              </span>
            </div>
            <div className="metric-row" title="Albini maximum spotting distance from the head (surface or torching-tree model)">
              <span className="metric-label">Max spotting distance</span>
              <span className="metric-value">{frame.head.max_spot_distance_m.toFixed(0)} m</span>
            </div>
          </>
        )}
        <div className="metric-row">
          <span className="metric-label">Max HFI</span>
          <span className="metric-value">
            {frame.max_hfi_kw_m.toFixed(0)} kW/m <HfiClassChip hfi={frame.max_hfi_kw_m} />
          </span>
        </div>
        {!isCAMode && (
          <div className="metric-row">
            <span className="metric-label">Fire Type</span>
            <span className="metric-fire-type">{formatFireType(frame.fire_type)}</span>
          </div>
        )}
        {!isCAMode && (
          <div className="metric-row">
            <span className="metric-label">Flame Length</span>
            <span className="metric-value">{frame.flame_length_m.toFixed(1)} m</span>
          </div>
        )}
        {!frame.building_exposure && (frame.buildings_at_risk ?? 0) > 0 && (
          <div className="metric-row">
            <span className="metric-label">Buildings inside perimeter</span>
            <span className="metric-value text-warning">
              {frame.buildings_at_risk}
            </span>
          </div>
        )}
        <div className="metric-row">
          <span className="metric-label">Frames</span>
          <span className="metric-value">{totalFrames}</span>
        </div>
      </div>

      {frame.building_exposure && (
        <div className="fuel-breakdown">
          <h4>Building exposure</h4>
          {(
            [
              ["Inside perimeter", frame.building_exposure.inside_perimeter],
              ["Within 30 m of fire", frame.building_exposure.within_30m],
              ["Within 100 m", frame.building_exposure.within_100m],
              ["Within 500 m", frame.building_exposure.within_500m],
              ["Radiant \u2265 12.5 kW/m\u00b2", frame.building_exposure.flux_over_12_5],
              ["Flux-time criterion reached", frame.building_exposure.ftp_reached],
            ] as const
          ).map(([label, n]) => (
            <div key={label} className="metric-row">
              <span className="metric-label">{label}</span>
              <span className="metric-value">{n}</span>
            </div>
          ))}
          <div className="hint-sm">
            Exposure, not ignition probability. Radiant heat uses Cohen's worst-case flame model
            (overestimates measured flux); embers and building-to-building fire are not modelled.
          </div>
        </div>
      )}

      {Object.keys(frame.fuel_breakdown).length > 0 && (
        <div className="fuel-breakdown">
          <h4>Fuel Mix</h4>
          {Object.entries(frame.fuel_breakdown).map(([fuel, pct]) => (
            <div key={fuel} className="fuel-bar">
              <span className="fuel-label">{fuel}</span>
              <div className="fuel-bar-track">
                <div
                  className="fuel-bar-fill"
                  style={{ width: `${pct * 100}%` }}
                />
              </div>
              <span className="fuel-pct">{(pct * 100).toFixed(0)}%</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
