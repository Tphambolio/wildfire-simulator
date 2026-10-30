/** Top bar (design spec §2.1): product, incident, tabs, limits badge, run status and clock. */

import { useEffect, useState, type ReactNode } from "react";
import { formatClock, formatDate, zoneAbbrev } from "../utils/time";
import { BADGES, TIPS } from "../content/explanations";
import Badge from "./Badge";

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

export type AppTab = "simulation" | "eoc" | "about";

interface TopBarProps {
  incidentName: string | null;
  incidentSub: ReactNode;
  activeTab: AppTab;
  onTabChange: (tab: AppTab) => void;
  status: string | null;
  /** Run controls and exports, shown before the status badge */
  actions?: ReactNode;
  /** Open About & sources at its Limits section (the low-skill badge) */
  onOpenLimits?: () => void;
  /** Open the validation scorecard dialog (ValidationScorecard.tsx) */
  onOpenValidation?: () => void;
  /** Opens the feedback dialog (components/FeedbackDialog.tsx) */
  onOpenFeedback?: () => void;
  /**
   * Exercise mode toggle (docs/exercise-mode-spec.md, PR0). Passed only when the feature flag
   * is on; without it nothing of exercise mode is rendered.
   */
  exercise?: { on: boolean; onToggle: () => void };
}

const TABS: Array<[AppTab, string]> = [
  ["simulation", "Simulation"],
  ["eoc", "EOC Console"],
  ["about", "About & sources"],
];

export default function TopBar({ incidentName, incidentSub, activeTab, onTabChange, status, actions, onOpenLimits, onOpenValidation, onOpenFeedback, exercise }: TopBarProps) {
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
        {TABS.map(([tab, label], i) => (
          <button
            key={tab}
            className={`nav-link${activeTab === tab ? " active" : ""}`}
            aria-current={activeTab === tab ? "page" : undefined}
            onClick={() => onTabChange(tab)}
          >
            <span className="nav-link-num">{i + 1}</span> {label}
          </button>
        ))}
      </nav>
      <div className="top-bar-right">
        {exercise && (
          <button
            type="button"
            className={`btn-control btn-exercise-toggle${exercise.on ? " active" : ""}`}
            aria-pressed={exercise.on}
            onClick={exercise.onToggle}
            title={exercise.on ? "Leave exercise mode" : "Exercise mode: a controller-driven exercise clock, EXERCISE banner and inject log"}
            data-testid="exercise-toggle"
          >
            Exercise mode
          </button>
        )}
        {actions}
        <span role="status" aria-live="polite" className="top-bar-status">
          {status && <span className={`status-badge status-${status}`}>{status}</span>}
        </span>
        <Badge
          tone="warn"
          className="limits-badge"
          tip={TIPS.lowSkill}
          onActivate={onOpenLimits ?? (() => onTabChange("about"))}
          testId="limits-badge"
        >
          {BADGES.lowSkill}
        </Badge>
        {onOpenValidation && (
          <button
            type="button"
            className="ui-badge ui-badge-neutral validation-badge"
            onClick={onOpenValidation}
            aria-haspopup="dialog"
            title="Validation scorecard: the latest held-out skill numbers, with sources"
            data-testid="validation-badge"
          >
            Validation
          </button>
        )}
        {onOpenFeedback && (
          <button
            type="button"
            className="ui-badge ui-badge-neutral validation-badge feedback-badge"
            onClick={onOpenFeedback}
            aria-haspopup="dialog"
            title="Feedback: was this useful? Opens a prefilled GitHub issue"
            data-testid="feedback-badge"
          >
            Feedback
          </button>
        )}
        <EdmontonClock />
      </div>
    </header>
  );
}
