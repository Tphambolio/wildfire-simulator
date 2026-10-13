/**
 * Saved runs (run archive phase 2): the server's list of finished runs, shared behind a
 * passphrase. Asks for the passphrase once (kept in sessionStorage for this tab only, dropped
 * on 401/403), lists runs newest first with their time (America/Edmonton), label, ignition
 * point, kind, area, duration and fuel source, filters by the current map view and a date
 * range, and opens a run through the app's restoreRun (timeline, perimeter, exposure and
 * ensemble). Rename and delete are passphrase-gated on the server too. No addresses.
 */

import { memo, useId, useState, type FormEvent } from "react";
import { PAGE_SIZE, useSavedRuns } from "../hooks/useSavedRuns";
import { LABEL_MAX, type SavedRun } from "../services/savedRuns";
import { runSummary, runWhen } from "../utils/savedRuns";
import "../styles/savedRuns.css";

interface SavedRunsPanelProps {
  /** Reload this run into the app (restoreRun) */
  onOpen: (run: SavedRun) => void;
  /** The run on screen, highlighted in the list */
  currentRunId?: string | null;
  /** The run on screen came from the server's run archive */
  currentFromArchive?: boolean;
  /** Load the list (false while the section is hidden) */
  active?: boolean;
}

function SavedRunsPanel({ onOpen, currentRunId = null, currentFromArchive = false, active = true }: SavedRunsPanelProps) {
  const s = useSavedRuns(active);
  const id = useId();
  const [pass, setPass] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const unlock = (e: FormEvent) => {
    e.preventDefault();
    s.submitPassphrase(pass);
    setPass("");
  };

  if (!s.unlocked) {
    return (
      <form className="saved-runs saved-runs-lock" onSubmit={unlock} aria-label="Unlock saved runs">
        <label className="field" htmlFor={`${id}-pass`}>
          Passphrase
          <input
            id={`${id}-pass`}
            type="password"
            autoComplete="current-password"
            value={pass}
            onChange={(e) => setPass(e.target.value)}
            aria-describedby={s.authError ? `${id}-auth` : undefined}
          />
        </label>
        <button type="submit" className="btn-primary btn-inline" disabled={!pass.trim()}>
          Show saved runs
        </button>
        {s.authError && (
          <div id={`${id}-auth`} className="hint-sm text-danger" role="alert">{s.authError}</div>
        )}
      </form>
    );
  }

  const from = s.total === 0 ? 0 : s.offset + 1;
  const to = Math.min(s.offset + PAGE_SIZE, s.total);

  return (
    <div className="saved-runs">
      <fieldset className="saved-runs-filters">
        <legend className="visually-hidden">Filters</legend>
        <label className="saved-runs-check">
          <input
            type="checkbox"
            checked={s.filters.nearView}
            onChange={(e) => s.updateFilters({ nearView: e.target.checked })}
          />
          Near current map view
        </label>
        <div className="saved-runs-dates">
          <label className="field field-date">
            From
            <input type="date" value={s.filters.from} onChange={(e) => s.updateFilters({ from: e.target.value })} />
          </label>
          <label className="field field-date">
            To
            <input type="date" value={s.filters.to} onChange={(e) => s.updateFilters({ to: e.target.value })} />
          </label>
        </div>
        <div className="button-row">
          <button type="button" className="btn-secondary btn-inline btn-small" onClick={s.refresh} disabled={s.loading}>
            Refresh
          </button>
          <button type="button" className="btn-secondary btn-inline btn-small" onClick={() => s.lock(null)}>
            Lock
          </button>
        </div>
      </fieldset>

      {s.error && <div className="hint-sm text-danger" role="alert">{s.error}</div>}
      {!s.archiveEnabled && <div className="hint-sm">Run archive off on the server: in-memory runs only.</div>}

      <div className="saved-runs-count" aria-live="polite">
        {s.loading ? "Loading…" : s.total === 0 ? "No saved runs match" : `${from}–${to} of ${s.total}`}
      </div>

      <ul className="saved-runs-list" aria-label="Saved runs">
        {s.runs.map((r) => {
          const title = r.label || `Run ${r.id}`;
          const current = r.id === currentRunId;
          const openable = r.status === "completed" || r.status === "failed" || r.status === "cancelled";
          return (
            <li key={r.id} className={`saved-run${current ? " current" : ""}`} data-run-id={r.id}>
              {editing === r.id ? (
                <form
                  className="saved-run-rename"
                  onSubmit={async (e) => {
                    e.preventDefault();
                    if (await s.rename(r.id, draft)) setEditing(null);
                  }}
                >
                  <label className="visually-hidden" htmlFor={`${id}-ren-${r.id}`}>Label</label>
                  <input
                    id={`${id}-ren-${r.id}`}
                    className="scenario-name-input"
                    value={draft}
                    maxLength={LABEL_MAX}
                    onChange={(e) => setDraft(e.target.value)}
                    autoFocus
                  />
                  <button type="submit" className="btn-primary btn-inline btn-small">Save</button>
                  <button type="button" className="btn-secondary btn-inline btn-small" onClick={() => setEditing(null)}>
                    Cancel
                  </button>
                </form>
              ) : (
                <button
                  type="button"
                  className="saved-run-open"
                  onClick={() => onOpen(r)}
                  disabled={!openable}
                  aria-current={current ? "true" : undefined}
                  title={openable ? "Open this run" : "Still running"}
                >
                  <span className="saved-run-title">{title}</span>
                  <span className="saved-run-when">{runWhen(r.created_at)}</span>
                </button>
              )}
              <div className="saved-run-meta">{runSummary(r)}</div>
              <div className="saved-run-meta">
                {r.fuel_source ?? "Fuel source not recorded"}
                {r.ensemble && <span className="saved-run-tag">Ensemble</span>}
                {r.status !== "completed" && <span className="saved-run-tag warn">{r.status}</span>}
                {!r.archived && <span className="saved-run-tag">Not archived yet</span>}
                {current && <span className="saved-run-tag current">{currentFromArchive ? "On map · Loaded from archive" : "On map"}</span>}
              </div>
              {editing !== r.id && (
                <div className="scenario-item-actions">
                  <button
                    type="button"
                    className="btn-secondary btn-inline btn-small"
                    onClick={() => {
                      setDraft(r.label ?? "");
                      setEditing(r.id);
                    }}
                    aria-label={`Rename ${title}`}
                  >
                    Rename
                  </button>
                  <button
                    type="button"
                    className="btn-secondary btn-inline btn-small btn-danger-outline"
                    disabled={!openable}
                    onClick={() => {
                      if (confirm(`Delete "${title}" from the server for everyone? This cannot be undone.`)) {
                        void s.remove(r.id);
                      }
                    }}
                    aria-label={`Delete ${title}`}
                  >
                    Delete
                  </button>
                </div>
              )}
            </li>
          );
        })}
      </ul>

      {s.total > PAGE_SIZE && (
        <div className="button-row saved-runs-pager">
          <button
            type="button"
            className="btn-secondary btn-inline btn-small"
            disabled={s.offset === 0 || s.loading}
            onClick={() => s.setOffset(Math.max(0, s.offset - PAGE_SIZE))}
          >
            Newer
          </button>
          <button
            type="button"
            className="btn-secondary btn-inline btn-small"
            disabled={s.offset + PAGE_SIZE >= s.total || s.loading}
            onClick={() => s.setOffset(s.offset + PAGE_SIZE)}
          >
            Older
          </button>
        </div>
      )}
    </div>
  );
}

export default memo(SavedRunsPanel);
