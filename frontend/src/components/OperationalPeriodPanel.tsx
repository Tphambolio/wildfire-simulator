/** Operational period (day) selector and advance-period controls for multi-day incidents. */

import { useState } from "react";
import type { IncidentSession, OperationalPeriod } from "../types/incident";

interface OperationalPeriodPanelProps {
  incident: IncidentSession;
  activePeriod: OperationalPeriod | null;
  onPeriodSelect: (index: number) => void;
  onAdvancePeriod: (opts: { date: string; opPeriodStart: string; opPeriodEnd: string }) => void;
  onUpdateName: (name: string) => void;
}

export default function OperationalPeriodPanel({
  incident,
  activePeriod,
  onPeriodSelect,
  onAdvancePeriod,
  onUpdateName,
}: OperationalPeriodPanelProps) {
  const [showAdvance, setShowAdvance] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [nextDate, setNextDate] = useState(() => {
    // Default to day after current period's date
    if (activePeriod?.date) {
      const d = new Date(activePeriod.date + "T00:00:00");
      d.setDate(d.getDate() + 1);
      return d.toISOString().slice(0, 10);
    }
    return new Date().toISOString().slice(0, 10);
  });
  const [nextStart, setNextStart] = useState("08:00");
  const [nextEnd, setNextEnd] = useState("20:00");

  const handleAdvance = () => {
    onAdvancePeriod({ date: nextDate, opPeriodStart: nextStart, opPeriodEnd: nextEnd });
    setShowAdvance(false);
  };

  return (
    <div className="op-period-panel">
      {/* Incident name */}
      <div className="op-period-header">
        {editingName ? (
          <input
            className="op-period-name-input"
            value={incident.name}
            autoFocus
            onChange={(e) => onUpdateName(e.target.value)}
            onBlur={() => setEditingName(false)}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === "Escape") setEditingName(false); }}
          />
        ) : (
          <button className="op-period-name-btn" onClick={() => setEditingName(true)}>
            {incident.name} <span className="eoc-edit-icon">✎</span>
          </button>
        )}
        <span className="eoc-status-badge">● ACTIVE</span>
      </div>

      {/* Day tabs */}
      <div className="op-period-days">
        {incident.operationalPeriods.map((p, idx) => (
          <button
            key={idx}
            className={`op-period-day-btn${incident.activePeriodIndex === idx ? " active" : ""}`}
            onClick={() => onPeriodSelect(idx)}
            title={`Day ${p.day}: ${p.date} ${p.opPeriodStart}–${p.opPeriodEnd}`}
          >
            Day {p.day}
            <span className="op-period-day-date">{(p.date ?? "").slice(5)}</span>
            {(p.annotations?.length ?? 0) > 0 && <span className="op-period-day-done">✓</span>}
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
          {activePeriod.annotations.length > 0 && (
            <span className="op-period-stats">
              {activePeriod.annotations.length} annotation{activePeriod.annotations.length !== 1 ? "s" : ""}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
