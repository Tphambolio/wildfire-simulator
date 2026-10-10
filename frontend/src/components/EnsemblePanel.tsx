/**
 * "Range of outcomes" card, the headline of the Situation panel when a run has an ensemble
 * (EOC best-practice review, rank 2: ensemble first, the single deterministic run secondary).
 *
 * Shows ensemble progress, then the P10 (reached by 1 in 10 members) and median (P50) extent
 * at the selected time, the area range across members at the end of the run, the map layer
 * toggles, and the "Range too narrow" badge: the range is narrower than the real uncertainty
 * (held-out Alberta fires, docs/validation.md "Ensemble calibration"); its explanation and the
 * P10/P50/P90 definitions are in tooltips (content/explanations.ts).
 */

import { memo, useId, useMemo } from "react";
import { BADGES, TIPS } from "../content/explanations";
import Badge from "./Badge";
import InfoTip from "./InfoTip";
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
      <div className="ens-h-row">
        <h3 id={`${id}-h`} className="ens-h">Range of outcomes</h3>
        <Badge tone="warn" tip={TIPS.rangeTooNarrow} testId="ensemble-caveat">{BADGES.rangeTooNarrow}</Badge>
      </div>

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
            <div className="ens-kicker with-tip">
              <span>P10 extent by {timeWithZone(at, scenarioStart)}</span>
              <InfoTip label="About P10" text={TIPS.p10} />
            </div>
            <div className="ens-big" data-testid="ensemble-p10-area">
              {fmtHa(areas.p10)}<small> ha</small>
            </div>
            <div className="ens-sub">
              Median {fmtHa(areas.p50)} ha (P50) · {g.members.length || state.total} members
            </div>
            {(g.membersFailed ?? 0) > 0 && (
              <div className="ens-sub" data-testid="ensemble-failed-note">
                {g.membersFailed} of {state.total} members failed and are left out
              </div>
            )}
          </div>
          <dl className="ens-range" data-testid="ensemble-area-range">
            <dt className="with-tip">
              <span>End-of-run range ({timeWithZone(g.durationMinutes, scenarioStart)})</span>
              <InfoTip label="About the end-of-run range" text={TIPS.endRange} />
            </dt>
            <dd>
              <span><span className="ens-range-k">min</span> {fmtHa(g.areaHa.min)}</span>
              <span><span className="ens-range-k">median</span> {fmtHa(g.areaHa.p50)}</span>
              <span><span className="ens-range-k">max</span> {fmtHa(g.areaHa.max)} ha</span>
            </dd>
          </dl>
          <fieldset className="ens-toggles">
            <legend>On the map</legend>
            {(
              [
                ["lines", "P10 arrival lines", TIPS.p10, "About P10 arrival lines"],
                ["p50", "Median extent (P50)", TIPS.p50, "About the median extent"],
                ["p90", "P90 footprint", TIPS.p90, "About the P90 footprint"],
                ["prob", "Burn probability", TIPS.probability, "About burn probability"],
              ] as const
            ).map(([key, label, tip, tipLabel]) => (
              <div key={key} className="with-tip">
                <label>
                  <input type="checkbox" checked={toggles[key]} onChange={(e) => onToggle(key, e.target.checked)} />
                  {label}
                </label>
                <InfoTip label={tipLabel} text={tip} />
              </div>
            ))}
          </fieldset>
        </>
      )}
    </section>
  );
}

export default memo(EnsemblePanel);
