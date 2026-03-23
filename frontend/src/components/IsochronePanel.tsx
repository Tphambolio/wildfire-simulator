/**
 * Isochrone panel — shows fire arrival time contours for evacuation planning.
 *
 * Displays configurable time intervals (default 1 / 2 / 4 / 8 h) as coloured
 * rings on the map so ICS commanders can assess how much time remains before
 * fire reaches specific roads, communities, or infrastructure.
 *
 * Integrates with EvacZonesPanel (TRA-177) — isochrones complement the
 * three-zone model with finer temporal resolution.
 */

import type { Isochrone } from "../utils/isochrones";
import { ISO_PRESETS, DEFAULT_ISO_HOURS } from "../utils/isochrones";

interface IsochronePanelProps {
  isochrones: Isochrone[];
  visible: boolean;
  targetHours: number[];
  onToggleVisible: (v: boolean) => void;
  onTargetHoursChange: (hours: number[]) => void;
}

/** Identify which preset key matches the current targetHours (if any). */
function activePreset(targetHours: number[]): string | null {
  const key = JSON.stringify(targetHours);
  for (const [label, hours] of Object.entries(ISO_PRESETS)) {
    if (JSON.stringify(hours) === key) return label;
  }
  return null;
}

export default function IsochronePanel({
  isochrones,
  visible,
  targetHours,
  onToggleVisible,
  onTargetHoursChange,
}: IsochronePanelProps) {
  if (isochrones.length === 0) return null;

  const preset = activePreset(targetHours);

  return (
    <div className="panel iso-panel">
      <div className="iso-header">
        <h3>Arrival Time Isochrones</h3>
        <button
          className={`ov-vis-btn ${visible ? "on" : "off"}`}
          onClick={() => onToggleVisible(!visible)}
          title={visible ? "Hide isochrones" : "Show isochrones"}
        >
          {visible ? "ON" : "OFF"}
        </button>
      </div>

      <div className="iso-subtitle">Time for fire front to reach each location</div>

      {/* Preset interval selector */}
      <div className="iso-preset-row">
        {Object.entries(ISO_PRESETS).map(([label, hours]) => (
          <button
            key={label}
            className={`iso-preset-btn${preset === label ? " active" : ""}`}
            onClick={() => onTargetHoursChange(hours)}
            title={`Intervals: ${hours.join(", ")} h`}
          >
            {label}
          </button>
        ))}
        {/* Reset to default */}
        {JSON.stringify(targetHours) !== JSON.stringify(DEFAULT_ISO_HOURS) && (
          <button
            className="iso-preset-btn"
            onClick={() => onTargetHoursChange(DEFAULT_ISO_HOURS)}
            title="Reset to default intervals"
          >
            Reset
          </button>
        )}
      </div>

      {/* Isochrone rows */}
      <div className="iso-list">
        {isochrones.map((iso) => (
          <div key={iso.timeHours} className="iso-row">
            <span
              className="iso-color-swatch"
              style={{ background: iso.color, boxShadow: `0 0 4px ${iso.color}88` }}
            />
            <span className="iso-label">{iso.label}</span>
            <span className="iso-time-val">
              {iso.timeHours < 1
                ? `${Math.round(iso.timeHours * 60)} min`
                : `${iso.timeHours.toFixed(iso.timeHours % 1 === 0 ? 0 : 1)} h elapsed`}
            </span>
          </div>
        ))}
      </div>

      <div className="iso-note">
        Rings show predicted fire boundary at each interval. Red = sooner, green = later.
      </div>
    </div>
  );
}
