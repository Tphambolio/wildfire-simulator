/**
 * Neighbourhoods card (Situation panel): modelled fire arrival near each neighbourhood, and
 * the evacuation status Planning has set.
 *
 * FireSim does not suggest evacuation tiers. The arrival list is a model output ("fire within
 * 500 m by 15:32"); Order / Alert / Watch are only ever set here or by clicking a
 * neighbourhood on the map, and are labelled "set by Planning" wherever they appear.
 */

import { memo, useId, useState } from "react";
import {
  ARRIVAL_BUFFER_M,
  EVAC_TIERS,
  TIER_STYLE,
  arrivalTime,
  type EvacTier,
  type EvacTierRecord,
  type NeighbourhoodArrival,
} from "../utils/evacZones";
import { formatClock } from "../utils/time";

interface EvacStatusPanelProps {
  /** Single (deterministic) run */
  arrivals: NeighbourhoodArrival[];
  /** Ensemble P10 (early, 1 in 10 members) arrivals; when present they lead and the single run is secondary */
  worstArrivals?: NeighbourhoodArrival[] | null;
  scenarioStart: Date | null;
  hasRun: boolean;
  records: EvacTierRecord[];
  /** All neighbourhood names (for setting a status without the map) */
  neighbourhoodNames: string[];
  onSetTier: (neighbourhood: string, tier: EvacTier | null) => void;
  arrivalsVisible: boolean;
  onArrivalsVisible: (v: boolean) => void;
  tiersVisible: boolean;
  onTiersVisible: (v: boolean) => void;
  onExport: () => void;
  /** Where statuses are saved, e.g. the incident name; null = this browser only */
  incidentName: string | null;
}

export function TierSwatch({ tier }: { tier: EvacTier }) {
  return <span className={`evac-swatch evac-swatch-${TIER_STYLE[tier].css}`} aria-hidden="true" />;
}

function TierSelect({
  name,
  value,
  onChange,
  label,
}: {
  name: string;
  value: EvacTier | null;
  onChange: (tier: EvacTier | null) => void;
  label: string;
}) {
  return (
    <select
      className="evac-tier-select"
      aria-label={label}
      value={value ?? ""}
      onChange={(e) => onChange((e.target.value || null) as EvacTier | null)}
      data-neighbourhood={name}
    >
      <option value="">None</option>
      {[...EVAC_TIERS].reverse().map((t) => (
        <option key={t} value={t}>{t}</option>
      ))}
    </select>
  );
}

function EvacStatusPanel({
  arrivals,
  worstArrivals = null,
  scenarioStart,
  hasRun,
  records,
  neighbourhoodNames,
  onSetTier,
  arrivalsVisible,
  onArrivalsVisible,
  tiersVisible,
  onTiersVisible,
  onExport,
  incidentName,
}: EvacStatusPanelProps) {
  const id = useId();
  const tierOf = new Map(records.map((r) => [r.neighbourhood, r.tier]));
  const [pickName, setPickName] = useState("");
  const [pickTier, setPickTier] = useState<EvacTier | "">("Watch");
  // Rows: P10 first when the ensemble is in, with the single-run time beside it
  const single = new Map(arrivals.map((a) => [a.name, a.arrivalHours]));
  const rows: Array<{ name: string; worst: number | null; single: number | null }> = worstArrivals
    ? [
        ...worstArrivals.map((a) => ({ name: a.name, worst: a.arrivalHours, single: single.get(a.name) ?? null })),
        ...arrivals
          .filter((a) => !worstArrivals.some((w) => w.name === a.name))
          .map((a) => ({ name: a.name, worst: null, single: a.arrivalHours })),
      ]
    : arrivals.map((a) => ({ name: a.name, worst: null, single: a.arrivalHours }));
  const sorted = [...records].sort(
    (a, b) => EVAC_TIERS.indexOf(a.tier) - EVAC_TIERS.indexOf(b.tier) || a.neighbourhood.localeCompare(b.neighbourhood),
  );

  return (
    <section className="evac-status" aria-labelledby={`${id}-h`}>
      <h3 id={`${id}-h`} className="evac-status-h">Neighbourhoods</h3>
      <p className="hint-sm">
        Fire arrival is modelled for this run{worstArrivals ? ": the ensemble's P10 (early end of the range, 1 in 10 members) first, the single run beside it" : ""}.
        Evacuation status is set by Planning: FireSim does not recommend evacuation tiers.
      </p>

      <h4 className="evac-status-sub">Modelled fire within {ARRIVAL_BUFFER_M} m</h4>
      {rows.length === 0 ? (
        <p className="hint-sm evac-empty">
          {hasRun
            ? `No neighbourhood within ${ARRIVAL_BUFFER_M} m of the modelled fire.`
            : "Run a simulation to see when the modelled fire comes near each neighbourhood."}
        </p>
      ) : (
        <table className="evac-arrival-table">
          <thead>
            <tr>
              <th scope="col">Neighbourhood</th>
              {worstArrivals ? (
                <>
                  <th scope="col">Fire within {ARRIVAL_BUFFER_M} m by, P10 (early)</th>
                  <th scope="col">Single run</th>
                </>
              ) : (
                <th scope="col">Fire within {ARRIVAL_BUFFER_M} m by</th>
              )}
              <th scope="col">Status (Planning)</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((a) => (
              <tr key={a.name}>
                <th scope="row">{a.name}</th>
                {worstArrivals && (
                  <td className="evac-arrival-time evac-arrival-worst">
                    {a.worst !== null ? arrivalTime(a.worst, scenarioStart) : "not reached"}
                  </td>
                )}
                <td className={`evac-arrival-time${worstArrivals ? " evac-arrival-single" : ""}`}>
                  {a.single !== null ? arrivalTime(a.single, scenarioStart) : "not reached"}
                </td>
                <td>
                  <TierSelect
                    name={a.name}
                    value={tierOf.get(a.name) ?? null}
                    onChange={(t) => onSetTier(a.name, t)}
                    label={`Evacuation status for ${a.name}`}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h4 className="evac-status-sub">Evacuation status, set by Planning</h4>
      {sorted.length === 0 ? (
        <p className="hint-sm evac-empty" data-testid="evac-none">
          None set. Click a neighbourhood on the map, or choose one here.
        </p>
      ) : (
        <ul className="evac-set-list" aria-label="Evacuation status set by Planning">
          {sorted.map((r) => (
            <li key={r.neighbourhood} className="evac-set-item">
              <TierSwatch tier={r.tier} />
              <span className="evac-set-tier">{TIER_STYLE[r.tier].mapLabel}</span>
              <span className="evac-set-name">{r.neighbourhood}</span>
              <span className="evac-set-when">set {formatClock(new Date(r.setAt))}</span>
              <button
                type="button"
                className="btn-small evac-clear"
                onClick={() => onSetTier(r.neighbourhood, null)}
                aria-label={`Clear evacuation status for ${r.neighbourhood}`}
              >
                Clear
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="field-row evac-pick">
        <label className="field">
          Neighbourhood
          <select value={pickName} onChange={(e) => setPickName(e.target.value)}>
            <option value="">Choose…</option>
            {neighbourhoodNames.map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </label>
        <label className="field evac-pick-tier">
          Status
          <select value={pickTier} onChange={(e) => setPickTier(e.target.value as EvacTier | "")}>
            <option value="">None</option>
            {[...EVAC_TIERS].reverse().map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="btn-secondary btn-inline"
          disabled={!pickName}
          onClick={() => onSetTier(pickName, pickTier || null)}
        >
          Set
        </button>
      </div>

      <div className="evac-legend-rows" aria-label="Map key">
        {EVAC_TIERS.map((t) => (
          <span key={t} className="evac-legend-row">
            <TierSwatch tier={t} /> {TIER_STYLE[t].mapLabel} ({TIER_STYLE[t].css})
          </span>
        ))}
        <span className="evac-legend-row">
          <span className="evac-swatch evac-swatch-arrival" aria-hidden="true" /> modelled arrival
        </span>
      </div>
      <label>
        <input type="checkbox" checked={tiersVisible} onChange={(e) => onTiersVisible(e.target.checked)} />
        Show evacuation status on the map
      </label>
      <label>
        <input type="checkbox" checked={arrivalsVisible} onChange={(e) => onArrivalsVisible(e.target.checked)} />
        Show modelled arrival outlines on the map
      </label>
      <button type="button" className="btn-secondary" onClick={onExport} disabled={records.length === 0}>
        Export evacuation status (GeoJSON)
      </button>
      <p className="hint-sm">
        {incidentName ? `Saved with the incident "${incidentName}".` : "Saved in this browser (no incident open)."}
      </p>
    </section>
  );
}

export default memo(EvacStatusPanel);
