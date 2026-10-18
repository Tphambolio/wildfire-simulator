/**
 * Progress of the main (single) run, in the look of the ensemble's member progress: a short
 * phase label, elapsed time and a bar. The server reports phases over the WebSocket
 * (`simulation.status`: loading, buildings, spread with its fraction, structures, finishing);
 * until the spread starts the bar is indeterminate. Shown only while a run is going, so people
 * do not take a long data load for a hung app. Multi-day runs show the day being computed
 * ("Day 2 of 3") and, once the run is a little way in, a rough time left (round-3 field
 * test R12). The time left comes from the progress made in the last two minutes, not since
 * the start: later days of a multi-day run are slower (a larger fire), so the average from
 * the start said "under a minute left" for minutes (field test 2026-10-18, Issue 4).
 */
import { useEffect, useRef, useState } from "react";
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

/** Progress samples older than this are not used for the rate (the recent rate counts). */
const ETA_WINDOW_S = 120;

/** A progress reading: wall-clock time (ms) and fraction done (0-1). */
export interface ProgressSample {
  t: number;
  f: number;
}

/**
 * Rough time left from the recent rate of progress: the fraction gained since the reading
 * about ETA_WINDOW_S ago (or the first reading, if newer), over the time since then up to
 * `now`. While progress stalls the rate falls, so the estimate grows rather than sticking at
 * "under a minute". Null early on (under 5 %, under 20 s of readings), when no progress was
 * made in the window, and at the end. The wording is hedged ("about", "roughly").
 */
export function etaFromSamples(samples: ReadonlyArray<ProgressSample>, now: number): string | null {
  if (samples.length === 0) return null;
  const last = samples[samples.length - 1];
  if (last.f < 0.05 || last.f >= 1) return null;
  const cutoff = now - ETA_WINDOW_S * 1000;
  let base = samples[0];
  for (const s of samples) {
    if (s.t <= cutoff) base = s;
    else break;
  }
  const spanS = (now - base.t) / 1000;
  const gained = last.f - base.f;
  if (spanS < 20 || gained <= 0) return null;
  const left = ((1 - last.f) * spanS) / gained;
  if (left < 90) return "about a minute left";
  const min = Math.round(left / 60);
  return min <= 10 ? `about ${min} min left` : `roughly ${Math.round(min / 5) * 5} min left`;
}

/**
 * Time left from the elapsed time alone (linear from the start), for a single reading, e.g.
 * "about 12 min left"; null early on (under 5 % or 20 s) and at the end.
 */
export function etaText(elapsedS: number, fraction: number | null): string | null {
  if (fraction === null || fraction < 0.05 || fraction >= 1 || elapsedS < 20) return null;
  const now = elapsedS * 1000;
  return etaFromSamples([{ t: 0, f: 0 }, { t: now, f: fraction }], now);
}

interface RunProgressProps {
  phase: RunPhase | null;
  /** Fraction of the spread computed, 0-1; null = not known yet (indeterminate bar) */
  progress: number | null;
  startedAt: number | null;
  paused?: boolean;
  onCancel?: () => void;
  /** Multi-day runs: the day being computed (1-based) and the number of days */
  day?: number | null;
  nDays?: number | null;
}

export default function RunProgress({ phase, progress, startedAt, paused = false, onCancel, day = null, nDays = null }: RunProgressProps) {
  const [now, setNow] = useState(() => Date.now());
  // Progress readings for the rate of progress: the run start (0 %) and each new fraction
  const samplesRef = useRef<ProgressSample[]>([]);
  useEffect(() => {
    samplesRef.current = startedAt ? [{ t: startedAt, f: 0 }] : [];
  }, [startedAt]);
  useEffect(() => {
    if (progress === null || phase !== "spread") return;
    const s = samplesRef.current;
    if (s.length === 0 || progress > s[s.length - 1].f) s.push({ t: Date.now(), f: progress });
  }, [progress, phase]);
  const [recentEta, setRecentEta] = useState<string | null>(null);
  useEffect(() => {
    const id = setInterval(() => {
      const t = Date.now();
      setNow(t);
      setRecentEta(etaFromSamples(samplesRef.current, t));
    }, 1000);
    return () => clearInterval(id);
  }, []);
  const dayLabel = nDays && nDays > 1 && day ? `Day ${day} of ${nDays} · ` : "";
  const label = `${dayLabel}${paused ? "Paused" : PHASE_LABEL[phase ?? "starting"]}`;
  const determinate = progress !== null && (phase === "spread" || phase === "structures" || phase === "finishing");
  const pct = determinate ? Math.round(100 * (phase === "spread" ? progress! : 1)) : null;
  const elapsedS = startedAt ? Math.max(0, (now - startedAt) / 1000) : null;
  const elapsed = elapsedS !== null ? formatElapsedSeconds(elapsedS) : null;
  const eta = !paused && phase === "spread" && elapsedS !== null ? recentEta : null;

  return (
    <div className="ens-progress run-progress" data-testid="run-progress">
      <div className="run-progress-row">
        <span className="ens-progress-text">
          {label}
          {pct !== null && phase === "spread" ? ` ${pct} %` : ""}
          {eta && <span className="run-progress-eta" data-testid="run-progress-eta"> · {eta}</span>}
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
