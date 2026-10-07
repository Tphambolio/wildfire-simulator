/** Top bar (design spec §2.1): product, incident, tabs, limits badge, run status and clock. */

import { useEffect, useState, type ReactNode } from "react";
import { formatClock, formatDate, zoneAbbrev } from "../utils/time";

const LIMITS_URL = "https://github.com/Tphambolio/wildfire-simulator/blob/master/docs/verification.md";

/** Wall clock in America/Edmonton, updated every 15 s. */
export function EdmontonClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 15_000);
    return () => clearInterval(id);
  }, []);
  return (
    <div className="top-bar-clock" aria-label={`Edmonton time ${formatClock(now)} ${zoneAbbrev(now)}`}>
      <span className="top-bar-clock-time">
        {formatClock(now)}
        <small>{zoneAbbrev(now)}</small>
      </span>
      <span className="top-bar-clock-date">{formatDate(now)}</span>
    </div>
  );
}

export type AppTab = "simulation" | "eoc";

interface TopBarProps {
  incidentName: string | null;
  incidentSub: string;
  activeTab: AppTab;
  onTabChange: (tab: AppTab) => void;
  status: string | null;
  /** Run controls and exports, shown before the status badge */
  actions?: ReactNode;
}

export default function TopBar({ incidentName, incidentSub, activeTab, onTabChange, status, actions }: TopBarProps) {
  return (
    <header className="top-bar">
      <div className="top-bar-brand">
        <span className="top-bar-brand-mark" aria-hidden="true" />
        FireSim
      </div>
      <div className="top-bar-incident">
        <span className={`top-bar-incident-name${incidentName ? "" : " placeholder"}`}>
          {incidentName ?? "No incident open"}
        </span>
        <span className="top-bar-incident-sub">{incidentSub}</span>
      </div>
      <nav className="top-bar-nav" aria-label="Workspace">
        <button
          className={`nav-link${activeTab === "simulation" ? " active" : ""}`}
          aria-current={activeTab === "simulation" ? "page" : undefined}
          onClick={() => onTabChange("simulation")}
        >
          <span className="nav-link-num">1</span> Simulation
        </button>
        <button
          className={`nav-link${activeTab === "eoc" ? " active" : ""}`}
          aria-current={activeTab === "eoc" ? "page" : undefined}
          onClick={() => onTabChange("eoc")}
        >
          <span className="nav-link-num">2</span> EOC Console
        </button>
      </nav>
      <div className="top-bar-right">
        {actions}
        <span role="status" aria-live="polite" className="top-bar-status">
          {status && <span className={`status-badge status-${status}`}>{status}</span>}
        </span>
        <a
          className="limits-badge"
          href={LIMITS_URL}
          target="_blank"
          rel="noreferrer"
          title="FBP equations match the cffdrs reference implementation; spread has not been validated against observed fires or Prometheus/WISE. Use for planning, training and what-if analysis, not as an operational forecast."
        >
          Planning tool · not validated against observed fires
        </a>
        <EdmontonClock />
      </div>
    </header>
  );
}
