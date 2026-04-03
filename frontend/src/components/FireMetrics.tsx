/** Fire metrics display panel showing current frame statistics. */

import type { SimulationFrame } from "../types/simulation";

interface FireMetricsProps {
  frame: SimulationFrame | null;
  status: string | null;
  totalFrames: number;
}

function classifyIntensity(hfi: number): { label: string; color: string } {
  if (hfi < 10) return { label: "Low", color: "#4caf50" };
  if (hfi < 500) return { label: "Moderate", color: "#ffeb3b" };
  if (hfi < 2000) return { label: "High", color: "#ff9800" };
  if (hfi < 4000) return { label: "Very High", color: "#f44336" };
  if (hfi < 10000) return { label: "Extreme", color: "#d32f2f" };
  return { label: "Ultra-Extreme", color: "#b71c1c" };
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

  const intensity = classifyIntensity(frame.max_hfi_kw_m);
  const isCAMode = Array.isArray(frame.burned_cells);

  return (
    <div className="panel metrics-panel">
      <h3>Fire Metrics</h3>

      <div style={{ marginBottom: 8 }}>
        <span style={{
          display: "inline-block",
          padding: "2px 8px",
          borderRadius: 3,
          fontSize: 11,
          fontWeight: 600,
          background: isCAMode ? "rgba(100, 180, 255, 0.15)" : "rgba(255, 150, 50, 0.15)",
          color: isCAMode ? "#64b4ff" : "#ff9632",
          border: `1px solid ${isCAMode ? "#3a6080" : "#804020"}`,
        }}>
          {isCAMode ? "CA Grid" : "Huygens"}
        </span>
        {(frame.num_fronts ?? 1) > 1 && (
          <span style={{
            display: "inline-block",
            marginLeft: 6,
            padding: "2px 8px",
            borderRadius: 3,
            fontSize: 11,
            fontWeight: 600,
            background: "rgba(255, 100, 0, 0.15)",
            color: "#ff6400",
            border: "1px solid #803200",
          }}>
            {frame.num_fronts} fronts
          </span>
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
          <span className="metric-label">{isCAMode ? "Mean ROS" : "Head ROS"}</span>
          <span className="metric-value">{frame.head_ros_m_min.toFixed(1)} m/min</span>
        </div>
        <div className="metric-row">
          <span className="metric-label">Max HFI</span>
          <span className="metric-value" style={{ color: intensity.color }}>
            {frame.max_hfi_kw_m.toFixed(0)} kW/m
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
        <div className="metric-row">
          <span className="metric-label">Frames</span>
          <span className="metric-value">{totalFrames}</span>
        </div>
      </div>

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
