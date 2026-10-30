/**
 * Inject log panel (docs/exercise-mode-spec.md §6, PR0 skeleton): lists the controller's injects
 * in exercise time with their status. PR0 has no way to add one from the UI yet (PR3), so the
 * panel is usually empty and says so. Shown only while exercise mode is on.
 */

import type { InjectLog } from "../types/exercise";
import { INJECT_KIND_LABELS } from "../types/exercise";
import { formatClock, zoneAbbrev } from "../utils/time";

interface InjectLogPanelProps {
  log: InjectLog;
  /** Current exercise time (ms); injects after it are still to come */
  exerciseMs: number | null;
}

export default function InjectLogPanel({ log, exerciseMs }: InjectLogPanelProps) {
  if (!log.injects.length) {
    return (
      <div className="inject-log inject-log-empty" data-testid="inject-log">
        <p className="hint-sm">No injects yet. The controller's perimeter, spot-fire, wind-shift and resource injects will be listed here in exercise time.</p>
      </div>
    );
  }
  return (
    <ol className="inject-log" data-testid="inject-log" aria-label="Inject log">
      {log.injects.map((i) => {
        const t = new Date(i.exerciseTimeMs);
        const pending = i.status === "scheduled" && exerciseMs !== null && i.exerciseTimeMs > exerciseMs;
        return (
          <li key={i.id} className={`inject-row inject-${i.status}${pending ? " inject-pending" : ""}`}>
            <span className="inject-time">{formatClock(t)} <small>{zoneAbbrev(t)}</small></span>
            <span className="inject-kind">{INJECT_KIND_LABELS[i.kind]}</span>
            <span className="inject-title">{i.title}</span>
            <span className="inject-status">{pending ? "scheduled" : i.status}</span>
          </li>
        );
      })}
    </ol>
  );
}
