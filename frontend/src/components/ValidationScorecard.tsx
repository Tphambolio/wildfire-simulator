/**
 * "Validation" (top bar): a compact read-only dialog listing the latest validation scorecard
 * (GET /api/v1/scorecard; docs/validation-scorecard.md). Each metric shows its value, 95 % CI
 * where the report gives one, and a one-line plain-English gloss, with the scorecard's date and
 * commit. The framing never changes: FireSim is a preparedness and training tool, not for
 * real-time operational prediction. Offline, the framing and a pointer to the docs remain.
 */
import { useEffect, useId, useRef, useState } from "react";
import { getScorecard } from "../services/api";
import { doc } from "../content/explanations";
import type { Scorecard, ScorecardMetric, ScorecardStatus } from "../types/scorecard";
import Badge, { type BadgeTone } from "./Badge";

export const FRAMING =
  "FireSim is a preparedness and training tool for EOC Planning sections. It is not an operational " +
  "fire-growth forecast and is not for real-time operational prediction; the lead agency's prediction is authoritative.";

export const STATUS_LABEL: Record<ScorecardStatus, string> = {
  validated: "Validated",
  partly_validated: "Partly validated",
  not_validated: "Not validated",
  failed_test: "Failed test",
  pass_opt_in: "Pass (opt-in)",
  pending: "Pending",
};

const STATUS_TONE: Record<ScorecardStatus, BadgeTone> = {
  validated: "ok",
  partly_validated: "warn",
  not_validated: "neutral",
  failed_test: "danger",
  pass_opt_in: "info",
  pending: "neutral",
};

/** Short names for the panel (the JSON label is the full definition, kept as the tooltip). */
const SHORT_LABEL: Record<string, string> = {
  fbp_cffdrs_conformance: "FBP equations vs cffdrs",
  heldout_f1_17h: "Front position skill vs observed fires (held-out F1)",
  area_bias_17h: "Area bias (held-out)",
  ensemble_coverage_p10_p90: "Ensemble P10-P90 coverage",
  ensemble_crps_skill: "Ensemble CRPS skill vs single run",
  ensemble_spread_skill: "Ensemble spread / skill",
  ensemble_p10_holds_90pct: "P10 held ≥ 90 % of growth",
  burn_probability_reliability_top_bin: "Burn probability at ~95 %: share burned",
  ensemble_b2_postprocessing: "B2 log-area post-processing (opt-in)",
  structure_hamada_jasper_kappa: "House-to-house spread at Jasper 2024",
  long_range_embers: "Long-range embers",
};

/** "[0.38, 0.60]" or "[38, 60 %]" in the metric's own unit. */
export function formatCi(m: ScorecardMetric): string | null {
  const ci = m.ci95 ?? (m.ci95_derived ? [m.ci95_derived.lo, m.ci95_derived.hi] : null);
  if (!ci) return null;
  const [lo, hi] = ci;
  const pct = /%/.test(m.display) || /share/.test(m.unit);
  const nd = /\d\.\d{3}/.test(m.display) ? 3 : 2;
  const text = pct ? `${Math.round(lo * 100)} to ${Math.round(hi * 100)} %` : `${lo.toFixed(nd)} to ${hi.toFixed(nd)}`;
  return `95 % CI ${text}${m.ci95 ? "" : " (derived)"}`;
}

export function shortLabel(m: ScorecardMetric): string {
  return SHORT_LABEL[m.id] ?? m.label;
}

function MetricRow({ m }: { m: ScorecardMetric }) {
  const ci = formatCi(m);
  const ref = m.reference?.note ? m.reference.note : null;
  return (
    <li className="vsc-row" data-testid={`vsc-${m.id}`}>
      <div className="vsc-row-head">
        <span className="vsc-label" title={m.label}>{shortLabel(m)}</span>
        <Badge tone={STATUS_TONE[m.status] ?? "neutral"} className="vsc-status">{STATUS_LABEL[m.status] ?? m.status}</Badge>
      </div>
      <div className="vsc-value">
        <strong>{m.display}</strong>
        {ci && <span className="vsc-ci"> · {ci}</span>}
        {ref && m.status !== "validated" && <span className="vsc-ref"> · {ref}</span>}
      </div>
      <p className="vsc-gloss">
        {m.gloss} <span className="vsc-src">{m.source.report}</span>
      </p>
      {m.versions && (
        <ul className="vsc-versions">
          {m.versions.map((v) => (
            <li key={v.version}>
              <strong>{v.version}</strong> {v.verdict}: {v.detail} <span className="vsc-src">{v.source.report}</span>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

interface Props {
  onClose: () => void;
}

export default function ValidationScorecardDialog({ onClose }: Props) {
  const id = useId();
  const [card, setCard] = useState<Scorecard | null>(null);
  const [failed, setFailed] = useState(false);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    let alive = true;
    getScorecard()
      .then((c) => alive && setCard(c))
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, []);

  // Focus moves into the dialog on open and back to the opener on close
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    return () => opener?.focus?.();
  }, []);

  // Esc closes; Tab stays inside (wrap-around trap, as BrowserDataReset)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>("button:not([disabled]), [href], [tabindex]:not([tabindex='-1'])"),
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  return (
    <div className="bdr-backdrop" onPointerDown={(e) => e.target === e.currentTarget && onClose()} data-testid="validation-dialog">
      <div
        ref={dialogRef}
        className="bdr-dialog vsc-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-title`}
        aria-describedby={`${id}-desc`}
      >
        <h3 id={`${id}-title`} className="bdr-title">Validation scorecard</h3>
        <p id={`${id}-desc`} className="bdr-text vsc-framing">{card?.framing ?? FRAMING}</p>

        {card && (
          <p className="vsc-meta" data-testid="vsc-meta">
            Scorecard {card.date} · commit <code>{card.git_commit.slice(0, 7)}</code> · engine {card.engine_version}
            {card.report && <> · report {card.report}</>}
            {card.fuel_grid?.scope && <span className="vsc-scope"> · {card.fuel_grid.scope}</span>}
          </p>
        )}
        {!card && !failed && <p className="bdr-text" data-testid="vsc-loading">Loading the scorecard…</p>}
        {failed && (
          <p className="bdr-text" role="status" data-testid="vsc-offline">
            The scorecard could not be loaded (the API did not answer). The latest published numbers are in the
            repository: <a href={doc("scorecard/README.md")} target="_blank" rel="noopener noreferrer">docs/scorecard</a>.
            On held-out Alberta fires one-day skill is low and the ensemble range is too narrow (About &amp; sources ▸ Limits).
          </p>
        )}

        {card && (
          <ul className="vsc-list">
            {card.metrics.map((m) => <MetricRow key={m.id} m={m} />)}
          </ul>
        )}

        <p className="bdr-note">
          Every number is traced to a report number and a result file in the scorecard JSON. How it is
          made and the release rule:{" "}
          <a href={doc("validation-scorecard.md")} target="_blank" rel="noopener noreferrer">docs/validation-scorecard.md</a>
          {" · "}
          <a href={doc("validation.md")} target="_blank" rel="noopener noreferrer">docs/validation.md</a>
        </p>

        <div className="bdr-actions">
          <button ref={closeRef} type="button" className="btn-control" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
