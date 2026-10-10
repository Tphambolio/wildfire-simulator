/**
 * Run details under the Situation KPI tiles: what the tiles do not show (spread model, maximum
 * intensity anywhere on the front, Huygens fire type and flame length, building exposure, fuel
 * mix). Area, head ROS, head intensity, direction, spotting and elapsed time are in the tiles and
 * the panel header, so they are not repeated here.
 */

import type { SimulationFrame } from "../types/simulation";
import { BADGES, TIPS } from "../content/explanations";
import Badge from "./Badge";
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

export default function FireMetrics({ frame }: FireMetricsProps) {
  // Before the first frame the Situation header says what to do; no second empty state here
  if (!frame) return null;

  const isCAMode = Array.isArray(frame.burned_cells);
  // The KPI tile shows the head's intensity when the run has a head summary; the maximum
  // anywhere on the front is then extra information
  const showMaxHfi = !!frame.head;

  return (
    <div className="panel metrics-panel">
      <h3>Run details</h3>

      {(frame.ignition_snapped_m ?? 0) > 0 && (
        <div className="notice notice-warning">
          Ignition snapped {Math.round(frame.ignition_snapped_m!)} m to nearest fuel cell
        </div>
      )}

      <div className="metrics-tags">
        <Badge tone="info" className="tag" tip={isCAMode ? TIPS.gridModel : TIPS.huygensModel}>
          {isCAMode ? "Grid model" : "Huygens"}
        </Badge>
        {(frame.num_fronts ?? 1) > 1 && (
          <span className="tag">{frame.num_fronts} fronts</span>
        )}
      </div>

      <div className="metrics-rows">
        {showMaxHfi && (
          <div className="metric-row">
            <span className="metric-label">Max HFI on the front</span>
            <span className="metric-value">
              {frame.max_hfi_kw_m.toFixed(0)} kW/m <HfiClassChip hfi={frame.max_hfi_kw_m} />
            </span>
          </div>
        )}
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
      </div>

      {frame.building_exposure && (
        <div className="fuel-breakdown">
          <div className="with-tip card-h-row">
            <h4>Building exposure</h4>
            <Badge tone="info" tip={TIPS.exposure} testId="exposure-badge">{BADGES.exposureNotIgnition}</Badge>
          </div>
          {(
            [
              ["Inside perimeter", frame.building_exposure.inside_perimeter],
              ["Within 30 m of fire", frame.building_exposure.within_30m],
              ["Within 100 m", frame.building_exposure.within_100m],
              ["Within 500 m", frame.building_exposure.within_500m],
              ["Radiant ≥ 12.5 kW/m²", frame.building_exposure.flux_over_12_5],
              ["Flux-time criterion reached", frame.building_exposure.ftp_reached],
            ] as const
          ).map(([label, n]) => (
            <div key={label} className="metric-row">
              <span className="metric-label">{label}</span>
              <span className="metric-value">{n}</span>
            </div>
          ))}
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
