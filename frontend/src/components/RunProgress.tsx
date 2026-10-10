/**
 * Progress of the main (single) run, in the look of the ensemble's member progress: a short
 * phase label, elapsed time and a bar. The server reports phases over the WebSocket
 * (`simulation.status`: loading, buildings, spread with its fraction, structures, finishing);
 * until the spread starts the bar is indeterminate. Shown only while a run is going, so people
 * do not take a long data load for a hung app.
 */
import { useEffect, useState } from "react";
import type { RunPhase } from "../types/simulation";

export const PHASE_LABEL: Record<RunPhase | "starting", string> = {
  starting: "Starting…",
  loading: "Loading fuel grid…",
  buildings: "Loading buildings…",
  spread: "Running spread…",
  structures: "House-to-house spread…",
  finishing: "Finishing…",
};

export function formatElapsedSeconds(s: number): string {
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
}

interface RunProgressProps {
  phase: RunPhase | null;
  /** Fraction of the spread computed, 0-1; null = not known yet (indeterminate bar) */
  progress: number | null;
  startedAt: number | null;
  paused?: boolean;
  onCancel?: () => void;
}

export default function RunProgress({ phase, progress, startedAt, paused = false, onCancel }: RunProgressProps) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const label = paused ? "Paused" : PHASE_LABEL[phase ?? "starting"];
  const determinate = progress !== null && (phase === "spread" || phase === "structures" || phase === "finishing");
  const pct = determinate ? Math.round(100 * (phase === "spread" ? progress! : 1)) : null;
  const elapsed = startedAt ? formatElapsedSeconds(Math.max(0, (now - startedAt) / 1000)) : null;

  return (
    <div className="ens-progress run-progress" data-testid="run-progress">
      <div className="run-progress-row">
        <span className="ens-progress-text">
          {label}
          {pct !== null && phase === "spread" ? ` ${pct} %` : ""}
        </span>
        {elapsed && <span className="run-progress-elapsed" aria-label={`Elapsed ${elapsed}`}>{elapsed}</span>}
        {onCancel && (
          <button type="button" className="btn-secondary btn-inline run-progress-cancel" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
      <div
        className={`ens-progress-track${pct === null ? " run-progress-indeterminate" : ""}`}
        role="progressbar"
        aria-label="Simulation run"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct ?? undefined}
        aria-valuetext={pct === null ? label : `${label} ${pct} %`}
      >
        <div className="ens-progress-fill" style={pct === null ? undefined : { width: `${pct}%` }} />
      </div>
      {/* Announce phase changes only (not every percent) */}
      <span className="visually-hidden" aria-live="polite">{label}</span>
    </div>
  );
}
