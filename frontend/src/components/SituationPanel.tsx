/**
 * Fixed right-hand Situation panel (design spec §2.1, §3): run status, the head summary at the
 * selected time, then the detailed metrics, building exposure and EOC summary (children).
 */

import { forwardRef, type ReactNode } from "react";
import type { BurningPeriod, SimulationFrame } from "../types/simulation";
import { clockAt, formatClock, formatElapsed, formatRunElapsed, zoneAbbrev } from "../utils/time";
import { formatBurningPeriod, formatHour, inBurningPeriod } from "../utils/skillOptions";
import HfiClassChip from "./HfiClassChip";
import Badge from "./Badge";
import InfoTip from "./InfoTip";
import { TIPS } from "../content/explanations";

interface SituationPanelProps {
  frame: SimulationFrame | null;
  frameIndex: number;
  totalFrames: number;
  status: string | null;
  scenarioStart: Date | null;
  /** A multi-day run: the elapsed time carries the operational day ("D3 · T+72:00") */
  multiDay?: boolean;
  /** Shown above the single-run KPIs (the ensemble "Range of outcomes" card) */
  headline?: ReactNode;
  /** Caption over the KPIs, e.g. "Single run (P50-like)" when an ensemble is the headline */
  kpiCaption?: string | null;
  /** Burning period of the run (null = fire spreads at any hour) */
  burningPeriod?: BurningPeriod | null;
  /** Main-run progress bar while a run is going (RunProgress) */
  progress?: ReactNode;
  /** Actions after a run (New ignition, Clear results) */
  actions?: ReactNode;
  children?: ReactNode;
  /** Collapsed to its rail (the content stays mounted, hidden) */
  collapsed?: boolean;
  /** id of the content element (aria-controls of the collapse and rail buttons) */
  contentId?: string;
  /** Collapse chevron for the header */
  collapseButton?: ReactNode;
  /** The rail shown while collapsed */
  rail?: ReactNode;
  /** Narrow screens: open as an overlay drawer */
  drawer?: boolean;
}

const DIRS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];

function clockLabel(start: Date | null, hours: number): string {
  if (!start) return formatElapsed(hours);
  const t = clockAt(start, hours);
  return `${formatClock(t)} ${zoneAbbrev(t)}`;
}

function statusLine(status: string | null, totalFrames: number, last: SimulationFrame | null, start: Date | null): string {
  switch (status) {
    case "pending":
      return "Starting the run…";
    case "running":
      return last
        ? `Running · ${totalFrames} frames received, up to ${clockLabel(start, last.time_hours)}`
        : "Running · waiting for the first frame";
    case "paused":
      return "Paused";
    case "completed":
      return last
        ? `Run complete · ${totalFrames} frames, ${formatElapsed(last.time_hours)} to ${clockLabel(start, last.time_hours)}`
        : "Run complete";
    case "failed":
      return "Run failed. See the message at the bottom of the screen.";
    case "cancelled":
      return "Run cancelled";
    default:
      return "No run yet. Set an ignition point and press Run.";
  }
}

const SituationPanel = forwardRef<HTMLElement, SituationPanelProps>(function SituationPanel(
  { frame, frameIndex, totalFrames, status, scenarioStart, multiDay = false, headline, kpiCaption, burningPeriod = null, progress = null, actions = null, children,
    collapsed = false, contentId, collapseButton = null, rail = null, drawer = false },
  ref,
) {
  // The "last" frame for progress is the newest one; the KPIs follow the timeline selection
  const head = frame?.head ?? null;
  const ros = head?.ros ?? frame?.head_ros_m_min ?? null;
  const hfi = head?.hfi ?? frame?.max_hfi_kw_m ?? null;

  return (
    <aside
      ref={ref}
      className={`situation-panel${collapsed ? " collapsed" : ""}${drawer ? " is-drawer" : ""}`}
      aria-labelledby="situation-title"
      tabIndex={-1}
    >
      {collapsed && rail}
      {/* Run status stays announced while the panel is collapsed */}
      {collapsed && (
        <div className="visually-hidden" role="status" aria-live="polite">
          {statusLine(status, totalFrames, frame, scenarioStart)}
        </div>
      )}
      <div id={contentId} className="situation-content" hidden={collapsed}>
      <header className="situation-header">
        <div className="situation-title-row">
          <h2 id="situation-title" className="situation-title">
            {frame ? `At ${clockLabel(scenarioStart, frame.time_hours)}` : "Situation"}
          </h2>
          {collapseButton}
        </div>
        {frame && (
          <div className="situation-sub">
            {formatRunElapsed(frame.time_hours, multiDay ? frame.day : null)} · frame {frameIndex + 1} of {totalFrames}
            {scenarioStart && ` · started ${formatClock(scenarioStart)} ${zoneAbbrev(scenarioStart)}`}
          </div>
        )}
        <div className="situation-status" role="status" aria-live="polite">
          {statusLine(status, totalFrames, frame, scenarioStart)}
        </div>
        {progress}
        {actions}
      </header>

      {/* Only the outside-the-burning-period state needs a banner (operationally essential) */}
      {frame && scenarioStart && burningPeriod && !inBurningPeriod(scenarioStart.getTime(), frame.time_hours, burningPeriod) && (
        <div className="situation-burning off" data-testid="outside-burning">
          <span>Outside burning period: no spread until {formatHour(burningPeriod.start_hour)}</span>
          <InfoTip
            label="About the burning period"
            text={`${TIPS.outsideBurning(formatBurningPeriod(burningPeriod), formatHour(burningPeriod.start_hour))} ${TIPS.burningPeriod}`}
          />
        </div>
      )}

      {headline}

      {frame && kpiCaption && (
        <div className="situation-kpi-caption">
          <Badge tone="neutral" tip={TIPS.singleRun}>{kpiCaption}</Badge>
        </div>
      )}
      {frame && (
        <div className="situation-kpis">
          <div className="kpi">
            <span className="kpi-label">Area</span>
            <span className="kpi-value">{frame.area_ha.toFixed(1)}<small> ha</small></span>
          </div>
          <div className="kpi">
            <span className="kpi-label">{head ? "Head ROS" : "Head ROS (mean)"}</span>
            <span className="kpi-value">{ros !== null ? ros.toFixed(1) : "—"}<small> m/min</small></span>
          </div>
          <div className="kpi">
            <span className="kpi-label">{head ? "Head intensity" : "Max intensity"}</span>
            <span className="kpi-value kpi-value-sm">
              {hfi !== null && <HfiClassChip hfi={hfi} />} {hfi !== null ? `${Math.round(hfi).toLocaleString("en-CA")} kW/m` : "—"}
            </span>
          </div>
          <div className="kpi">
            <span className="kpi-label">{head ? "Head toward · spotting" : "Fire type"}</span>
            <span className="kpi-value kpi-value-sm">
              {head
                ? `${DIRS[Math.round(head.raz / 45) % 8]} (${head.raz.toFixed(0)}°) · ${head.max_spot_distance_m.toFixed(0)} m`
                : frame.fire_type.replace(/_/g, " ")}
            </span>
          </div>
        </div>
      )}

      <div className="situation-body">{children}</div>
      </div>
    </aside>
  );
});

export default SituationPanel;
