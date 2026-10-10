/**
 * Situation panel card for the opt-in house-to-house (structure) spread: counts at the selected
 * time, a stacked chart of involved buildings over the run (front contact / building to
 * building) with the timeline cursor, and the map-layer toggle. Minimal text: the caveat is in
 * the info tooltip. Illustrative — not validated in Canada (docs/structure-spread-spec.md §9).
 */
import type { SimulationFrame } from "../types/simulation";
import { STRUCT_B2B, STRUCT_EMBER, STRUCT_FRONT, structureCaveat, structureSeries } from "../utils/structureSpread";
import InfoTip from "./InfoTip";
import Badge from "./Badge";
import { BADGES } from "../content/explanations";

interface StructureSpreadPanelProps {
  frames: SimulationFrame[];
  frameIndex: number;
  mapVisible: boolean;
  onMapVisible: (v: boolean) => void;
  /** The map layer has data (the run's final frame has arrived) */
  mapAvailable: boolean;
}

const W = 280;
const H = 64;

export function StructureChart({ frames, frameIndex }: { frames: SimulationFrame[]; frameIndex: number }) {
  const series = structureSeries(frames);
  if (series.length === 0) return null;
  const tMax = Math.max(...series.map((p) => p.t), 1e-6);
  const nMax = Math.max(...series.map((p) => p.front + p.b2b + p.ember), 1);
  const bw = Math.max(2, Math.min(18, (W - 4) / series.length - 2));
  const x = (t: number) => 2 + (t / tMax) * (W - bw - 4);
  const y = (n: number) => H - 2 - (n / nMax) * (H - 6);
  const cur = frames[frameIndex]?.time_hours ?? 0;
  const last = series[series.length - 1];
  return (
    <svg
      className="struct-chart"
      viewBox={`0 0 ${W} ${H}`}
      width="100%"
      height={H}
      role="img"
      aria-label={`Buildings involved over the run: ${last.front + last.b2b + last.ember} by ${last.t.toFixed(1)} h (${last.front} front contact, ${last.b2b} building to building${last.ember ? `, ${last.ember} ember ignition` : ""})`}
      data-testid="structure-chart"
    >
      <line x1={0} x2={W} y1={H - 2} y2={H - 2} className="struct-chart-axis" />
      {series.map((p) => (
        <g key={p.t} opacity={p.t <= cur + 1e-9 ? 1 : 0.35}>
          <rect x={x(p.t)} width={bw} y={y(p.front)} height={H - 2 - y(p.front)} fill={STRUCT_FRONT} />
          <rect x={x(p.t)} width={bw} y={y(p.front + p.b2b)} height={y(p.front) - y(p.front + p.b2b)} fill={STRUCT_B2B} />
          {p.ember > 0 && (
            <rect
              x={x(p.t)}
              width={bw}
              y={y(p.front + p.b2b + p.ember)}
              height={y(p.front + p.b2b) - y(p.front + p.b2b + p.ember)}
              fill={STRUCT_EMBER}
            />
          )}
        </g>
      ))}
      <line
        x1={x(Math.min(cur, tMax)) + bw / 2}
        x2={x(Math.min(cur, tMax)) + bw / 2}
        y1={0}
        y2={H}
        className="struct-chart-cursor"
        data-testid="structure-chart-cursor"
      />
    </svg>
  );
}

export default function StructureSpreadPanel({ frames, frameIndex, mapVisible, onMapVisible, mapAvailable }: StructureSpreadPanelProps) {
  const frame = frames[frameIndex] ?? null;
  const s = frame?.structure_spread ?? frames.find((f) => f.structure_spread)?.structure_spread ?? null;
  if (!s) return null;
  const caveat = structureCaveat(s.label, s.neighbour_cutoff_m ?? 30, 50, s.embers ? s.design_fire_kw_m2 : undefined);
  const computed = s.computed !== false && s.units_involved != null;
  const cur = frame?.structure_spread;
  return (
    <section className="panel struct-panel" aria-labelledby="struct-title" data-testid="structure-panel">
      <div className="fuel-breakdown">
        <h4 id="struct-title" className="struct-title">
          House-to-house spread <Badge tone="warn" className="struct-badge">{BADGES.illustrative}</Badge>
          <InfoTip text={caveat} label="About house-to-house spread" />
        </h4>
        {!computed ? (
          <div className="hint-sm" role="note">
            {s.note ? s.note.charAt(0).toUpperCase() + s.note.slice(1) : "Not computed"}
          </div>
        ) : (
          <>
            {(
              [
                ["Buildings involved", cur?.units_involved, null],
                ["Front contact", cur?.units_front_contact, STRUCT_FRONT],
                ["Building to building", cur?.units_structure_to_structure, STRUCT_B2B],
                ...(s.embers ? ([["Ember ignition", cur?.units_ember, STRUCT_EMBER]] as const) : []),
              ] as const
            ).map(([label, n, color]) => (
              <div key={label} className="metric-row">
                <span className="metric-label">
                  {color && <span className="struct-swatch" style={{ background: color }} aria-hidden="true" />}
                  {label}
                </span>
                <span className="metric-value">{n ?? "—"}</span>
              </div>
            ))}
            <StructureChart frames={frames} frameIndex={frameIndex} />
            <label className="struct-toggle">
              <input
                type="checkbox"
                checked={mapVisible}
                disabled={!mapAvailable}
                onChange={(e) => onMapVisible(e.target.checked)}
              />
              Show on map
            </label>
          </>
        )}
      </div>
    </section>
  );
}
