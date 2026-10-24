/** Operational period (day) selector and advance-period controls for multi-day incidents. */

import { useState } from "react";
import type { IncidentSession, OperationalPeriod } from "../types/incident";
import { nextPeriodDate } from "../utils/time";
import { normaliseIncidentName } from "../utils/storage";

/** How the current run relates to the active period (App decides; this panel shows it) */
export interface PeriodRunInfo {
  /** The EOC console shows the current run for this period */
  liveRunShown: boolean;
  /** Day of the period the current run is filed under, if another one */
  liveRunDay: number | null;
  /** A completed run not filed under any period can be attached to this one */
  canAttach: boolean;
  onAttach: () => void;
  /** This period has a saved run that is not loaded: load it from the server */
  onLoad?: () => void;
  /** The run shown was loaded from the server's run archive (not its memory) */
  fromArchive?: boolean;
  /** Span the shown run models, e.g. "Tue, 13 Oct 04:57 to Fri, 16 Oct 04:57 MDT (72 h)";
   * a 3-day run under a Day 1 08:00-20:00 period read as if it covered that period only (R11) */
  span?: string | null;
  /** The run models beyond this operational period's end */
  beyondPeriod?: boolean;
}

interface OperationalPeriodPanelProps {
  incident: IncidentSession;
  activePeriod: OperationalPeriod | null;
  onPeriodSelect: (index: number) => void;
  onAdvancePeriod: (opts: { date: string; opPeriodStart: string; opPeriodEnd: string }) => void;
  onUpdateName: (name: string) => void;
  /** The last save to browser storage failed (storage full): changes are not kept */
  saveFailed?: boolean;
  run?: PeriodRunInfo;
}

export default function OperationalPeriodPanel({
  incident,
  activePeriod,
  onPeriodSelect,
  onAdvancePeriod,
  onUpdateName,
  saveFailed = false,
  run,
}: OperationalPeriodPanelProps) {
  const [showAdvance, setShowAdvance] = useState(false);
  // The name being edited (null = not editing). It may be empty while typing; on blur or Enter
  // an empty name is stored as "Untitled incident" (E9), never snapped back to the old name.
  // Esc cancels the edit.
  const [nameDraft, setNameDraft] = useState<string | null>(null);
  const commitName = () => {
    if (nameDraft === null) return;
    const next = normaliseIncidentName(nameDraft);
    if (next !== incident.name) onUpdateName(next);
    setNameDraft(null);
  };
  const [nextDate, setNextDate] = useState(() => nextPeriodDate(activePeriod?.date));
  const [nextStart, setNextStart] = useState("08:00");
  const [nextEnd, setNextEnd] = useState("20:00");

  // Incidents persist in localStorage across app versions: read stored fields defensively
  const periods = Array.isArray(incident.operationalPeriods) ? incident.operationalPeriods : [];
  const lastArea = activePeriod?.frameSummaries?.at(-1)?.areaHa;

  const handleAdvance = () => {
    onAdvancePeriod({ date: nextDate, opPeriodStart: nextStart, opPeriodEnd: nextEnd });
    setShowAdvance(false);
  };

  return (
    <div className="op-period-panel">
      {/* Incident name */}
      <div className="op-period-header">
        {nameDraft !== null ? (
          <input
            className="op-period-name-input"
            value={nameDraft}
            placeholder="Incident name"
            aria-label="Incident name"
            autoFocus
            onChange={(e) => setNameDraft(e.target.value)}
            onBlur={commitName}
            onKeyDown={(e) => {
              if (e.key === "Enter") commitName();
              else if (e.key === "Escape") setNameDraft(null);
            }}
          />
        ) : (
          <button className="op-period-name-btn" onClick={() => setNameDraft(incident.name)}>
            {incident.name} <span className="eoc-edit-icon">✎</span>
          </button>
        )}
        <span className="eoc-status-badge">● ACTIVE</span>
      </div>

      {/* Day tabs */}
      <div className="op-period-days">
        {periods.map((p, idx) => (
          <button
            key={idx}
            className={`op-period-day-btn${incident.activePeriodIndex === idx ? " active" : ""}`}
            onClick={() => onPeriodSelect(idx)}
            title={`Day ${p.day}: ${p.date} ${p.opPeriodStart}–${p.opPeriodEnd}`}
          >
            Day {p.day}
            <span className="op-period-day-date">{typeof p.date === "string" ? p.date.slice(5) : ""}</span>
            {p.simulationStatus === "completed" && <span className="op-period-day-done">✓</span>}
          </button>
        ))}

        {/* Advance to next period */}
        {!showAdvance ? (
          <button
            className="op-period-advance-btn"
            onClick={() => setShowAdvance(true)}
            title="Start next operational period (Day N+1)"
          >
            + New Period
          </button>
        ) : (
          <div className="op-period-advance-form">
            <label>Date
              <input type="date" value={nextDate} onChange={(e) => setNextDate(e.target.value)} />
            </label>
            <label>Op Period
              <input type="time" value={nextStart} onChange={(e) => setNextStart(e.target.value)} />
              <span>–</span>
              <input type="time" value={nextEnd} onChange={(e) => setNextEnd(e.target.value)} />
            </label>
            <div className="op-period-advance-actions">
              <button className="op-period-confirm-btn" onClick={handleAdvance}>Advance</button>
              <button className="op-period-cancel-btn" onClick={() => setShowAdvance(false)}>Cancel</button>
            </div>
          </div>
        )}
      </div>

      {/* Active period info strip */}
      {activePeriod && (
        <div className="op-period-info">
          <span>Day {activePeriod.day} · {activePeriod.date} · {activePeriod.opPeriodStart}–{activePeriod.opPeriodEnd}</span>
          {typeof lastArea === "number" && Number.isFinite(lastArea) && (
            <span className="op-period-stats">{lastArea.toFixed(1)} ha burned</span>
          )}
          {run?.liveRunShown && run.span && (
            <span className="op-period-span" data-testid="period-run-span">
              Modelled {run.span}{run.beyondPeriod ? " (beyond this operational period)" : ""}
            </span>
          )}
          {run && (
            <span className="op-period-run" data-testid="period-run">
              {activePeriod.simulationId
                ? <>Run {activePeriod.simulationId}{run.liveRunShown ? (run.fromArchive ? " · Loaded from archive" : "") : " · not loaded"}</>
                : run.liveRunShown
                  ? "Current run · not yet filed under this period"
                  : run.liveRunDay != null
                    ? `No run for this period (current run is Day ${run.liveRunDay}'s)`
                    : "No run for this period"}
              {run.onLoad && (
                <button className="op-period-run-btn" onClick={run.onLoad}>Load Day {activePeriod.day} run</button>
              )}
              {run.canAttach && (
                <button
                  className="op-period-run-btn"
                  onClick={run.onAttach}
                  title={activePeriod.simulationId ? `Replaces run ${activePeriod.simulationId}` : undefined}
                >
                  Attach current run to Day {activePeriod.day}
                </button>
              )}
            </span>
          )}
        </div>
      )}
      {saveFailed && (
        <div className="notice notice-warning" role="alert">
          Not saved: this browser's storage is full or blocked. Export the incident
          (Setup → Incidents) to keep it.
        </div>
      )}
    </div>
  );
}
