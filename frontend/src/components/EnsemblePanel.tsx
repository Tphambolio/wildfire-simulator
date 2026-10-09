/**
 * "Range of outcomes" card, the headline of the Situation panel when a run has an ensemble
 * (EOC best-practice review, rank 2: ensemble first, the single deterministic run secondary).
 *
 * Shows ensemble progress, then the P10 (reached by 1 in 10 members) and median (P50) extent
 * at the selected time, the area range across members at the end of the run, the map layer
 * toggles, and the caveat that the range is narrower than the real uncertainty (held-out
 * Alberta fires, docs/validation.md "Ensemble calibration").
 */

import { memo, useId, useMemo } from "react";
import type { EnsembleState } from "../hooks/useEnsemble";
import { areaByMinutes, clockLabel } from "../utils/ensemble";
import { zoneAbbrev, clockAt } from "../utils/time";

export interface EnsembleToggles {
  lines: boolean;
  p50: boolean;
  p90: boolean;
  prob: boolean;
}

interface EnsemblePanelProps {
  state: EnsembleState;
  /** Selected timeline time, minutes after ignition (null before a run) */
  selectedMinutes: number | null;
  scenarioStart: Date | null;
  toggles: EnsembleToggles;
  onToggle: (key: keyof EnsembleToggles, value: boolean) => void;
}

const fmtHa = (ha: number) =>
  ha >= 100 ? Math.round(ha).toLocaleString("en-CA") : ha >= 10 ? ha.toFixed(0) : ha.toFixed(1);

function timeWithZone(minutes: number, start: Date | null): string {
  return start ? `${clockLabel(minutes, start)} ${zoneAbbrev(clockAt(start, minutes / 60))}` : clockLabel(minutes, null);
}

function EnsemblePanel({ state, selectedMinutes, scenarioStart, toggles, onToggle }: EnsemblePanelProps) {
  const id = useId();
  const g = state.grids;
  const at = selectedMinutes ?? g?.durationMinutes ?? 0;
  const areas = useMemo(
    () => (g ? { p10: areaByMinutes(g, "p10", at), p50: areaByMinutes(g, "p50", at) } : null),
    [g, at],
  );
  if (state.phase === "off") return null;

  return (
    <section className="ens-card" aria-labelledby={`${id}-h`} data-testid="ensemble-card">
      <h3 id={`${id}-h`} className="ens-h">Range of outcomes</h3>

      {(state.phase === "waiting" || state.phase === "running") && (
        <div className="ens-progress">
          <div className="ens-progress-text">
            {state.phase === "waiting"
              ? `Ensemble of ${state.total} starts when the single run finishes`
              : `Ensemble ${state.done}/${state.total}`}
          </div>
          <div
            className="ens-progress-track"
            role="progressbar"
            aria-label="Ensemble members run"
            aria-valuemin={0}
            aria-valuemax={state.total}
            aria-valuenow={state.done}
          >
            <div className="ens-progress-fill" style={{ width: `${state.total ? (100 * state.done) / state.total : 0}%` }} />
          </div>
        </div>
      )}

      {state.phase === "failed" && (
        <p className="ens-error" role="alert">
          The ensemble did not finish ({state.error ?? "unknown error"}). The single run below is shown alone.
        </p>
      )}

      {state.phase === "completed" && g && areas && (
        <>
          <div className="ens-headline">
            <div className="ens-kicker">P10 extent (1 in 10 members) by {timeWithZone(at, scenarioStart)}</div>
            <div className="ens-big" data-testid="ensemble-p10-area">
              {fmtHa(areas.p10)}<small> ha</small>
            </div>
            <div className="ens-sub">
              Median {fmtHa(areas.p50)} ha (P50) · {g.members.length || state.total} members
            </div>
          </div>
          <dl className="ens-range" data-testid="ensemble-area-range">
            <dt>Area at {timeWithZone(g.durationMinutes, scenarioStart)} (end of run), across members</dt>
            <dd>
              <span><span className="ens-range-k">min</span> {fmtHa(g.areaHa.min)}</span>
              <span><span className="ens-range-k">median</span> {fmtHa(g.areaHa.p50)}</span>
              <span><span className="ens-range-k">max</span> {fmtHa(g.areaHa.max)} ha</span>
            </dd>
          </dl>
          <p className="hint-sm ens-def">
            P10 arrival: at least one member in ten brings the fire to a place this early. It is the
            early end of the modelled range, not a worst case: on held-out Alberta fires part of the
            observed growth fell outside the P10 footprint on most days. Lines on the map are P10
            arrival times in clock time.
          </p>
          <fieldset className="ens-toggles">
            <legend>On the map</legend>
            <label>
              <input type="checkbox" checked={toggles.lines} onChange={(e) => onToggle("lines", e.target.checked)} />
              P10 arrival lines (early end of the range)
            </label>
            <label>
              <input type="checkbox" checked={toggles.p50} onChange={(e) => onToggle("p50", e.target.checked)} />
              Median extent at the selected time (P50)
            </label>
            <label>
              <input type="checkbox" checked={toggles.p90} onChange={(e) => onToggle("p90", e.target.checked)} />
              Footprint 9 in 10 members reach (P90)
            </label>
            <label>
              <input type="checkbox" checked={toggles.prob} onChange={(e) => onToggle("prob", e.target.checked)} />
              Burn probability
            </label>
          </fieldset>
        </>
      )}

      <p className="ens-caveat" data-testid="ensemble-caveat">
        <strong>Range narrower than the real uncertainty.</strong> Members vary wind, fuel moisture,
        curing and spread rate by amounts set from Alberta forecast errors and observed fires, but on
        held-out fires the observed one-day area fell inside the members' 10-90 % range on only about
        half the days, and FireSim usually over-predicts one-day growth. Use the P10 line as a
        planning margin, not a forecast or a worst case.
      </p>
    </section>
  );
}

export default memo(EnsemblePanel);
