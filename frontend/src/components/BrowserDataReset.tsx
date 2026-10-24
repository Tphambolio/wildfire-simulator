/**
 * "Reset this browser's FireSim data" (E9, owner report 2026-10-24): a button that opens an
 * in-app dialog listing what FireSim has stored in this browser (utils/browserData.ts:
 * incidents with their names and statuses, restored Setup, evacuation statuses, legends,
 * layout, the tab's run and saved-runs passphrase), offers "Export incident JSON" for each
 * stored incident, and clears exactly those keys after an explicit confirm. The server's run
 * archive is not touched and the dialog says so. What happens after the clear is the caller's
 * (App reloads the page so every module starts from defaults).
 */
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import type { IncidentSession } from "../types/incident";
import {
  clearAll,
  describeInventory,
  inventory,
  RESET_BUTTON_LABEL,
  type BrowserDataInventory,
  type ClearResult,
} from "../utils/browserData";

export { RESET_BUTTON_LABEL };

interface DialogProps {
  onClose: () => void;
  /** Reuse the incident store's export (a JSON download) */
  onExportIncident?: (incident: IncidentSession) => void;
  /** Called once the keys are removed (App reloads the page) */
  onCleared: (result: ClearResult) => void;
}

function sizeLabel(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(bytes < 10_240 ? 1 : 0)} kB`;
}

export function BrowserDataResetDialog({ onClose, onExportIncident, onCleared }: DialogProps) {
  const id = useId();
  const [inv, setInv] = useState<BrowserDataInventory>(() => inventory());
  const [failed, setFailed] = useState<string[] | null>(null);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const cancelRef = useRef<HTMLButtonElement | null>(null);

  // Focus moves into the dialog on open and back to the opener on close (the caller's button
  // regains focus because it is still the document's previously focused element)
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    cancelRef.current?.focus();
    return () => opener?.focus?.();
  }, []);

  // Esc closes; Tab stays inside (small dialog: a simple wrap-around trap)
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
        dialogRef.current.querySelectorAll<HTMLElement>("button:not([disabled]), [href], input, [tabindex]:not([tabindex='-1'])"),
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

  const lines = describeInventory(inv);
  const canClear = !inv.blocked && !inv.empty;

  const handleClear = () => {
    const result = clearAll();
    if (result.failed.length > 0 || (result.blocked && result.removed.length === 0)) {
      setFailed(result.failed.length > 0 ? result.failed : ["storage is blocked"]);
      setInv(inventory());
      return;
    }
    onCleared(result);
  };

  return (
    <div className="bdr-backdrop" onPointerDown={(e) => e.target === e.currentTarget && onClose()} data-testid="browser-data-dialog">
      <div
        ref={dialogRef}
        className="bdr-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-title`}
        aria-describedby={`${id}-desc`}
      >
        <h3 id={`${id}-title`} className="bdr-title">{RESET_BUTTON_LABEL}</h3>
        <p id={`${id}-desc`} className="bdr-text">
          FireSim keeps incidents, the Setup panel and a few preferences in this browser only (not on
          the server). A reload, including a hard reload (Ctrl+Shift+R), does not clear them. This
          control does.
        </p>

        {inv.blocked && (
          <p className="bdr-text text-danger" role="alert">
            This browser blocks site storage, so nothing can be read or cleared here.
          </p>
        )}

        {!inv.blocked && inv.empty && (
          <p className="bdr-text" data-testid="bdr-empty">Nothing is stored in this browser.</p>
        )}

        {!inv.blocked && !inv.empty && (
          <>
            <h4 className="bdr-subtitle">Stored in this browser ({sizeLabel(inv.totalBytes)})</h4>
            <ul className="bdr-list" data-testid="bdr-inventory">
              {lines.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </>
        )}

        {inv.incidents.length > 0 && (
          <>
            <h4 className="bdr-subtitle">Export incident JSON first</h4>
            <p className="bdr-text">
              Each export is a file you can import again under Incidents &amp; saved scenarios.
            </p>
            <ul className="bdr-export-list">
              {inv.incidents.map((inc) => (
                <li key={inc.id}>
                  <span className="bdr-incident-name">{inc.name}</span>
                  <span className="bdr-incident-status">{inc.status}</span>
                  <button
                    type="button"
                    className="btn-secondary btn-inline btn-small"
                    onClick={() => onExportIncident?.(inc)}
                    disabled={!onExportIncident}
                    aria-label={`Export incident ${inc.name} as JSON`}
                  >
                    Export
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}

        <p className="bdr-text bdr-note">
          Runs kept on the server (Setup → Saved runs) are not affected. The saved-runs passphrase
          for this tab is forgotten and asked for again. The page reloads after clearing.
        </p>

        {failed && (
          <p className="bdr-text text-danger" role="alert">
            Could not remove: {failed.join(", ")}. The rest was cleared.
          </p>
        )}

        <div className="bdr-actions">
          <button ref={cancelRef} type="button" className="btn-secondary btn-inline" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-secondary btn-inline btn-danger-outline"
            onClick={handleClear}
            disabled={!canClear}
          >
            Clear and reload
          </button>
        </div>
      </div>
    </div>
  );
}

interface ResetProps {
  onExportIncident?: (incident: IncidentSession) => void;
  onCleared: (result: ClearResult) => void;
  className?: string;
  children?: ReactNode;
}

/** The button (About tab) that opens the dialog. */
export default function BrowserDataReset({ onExportIncident, onCleared, className = "btn-secondary btn-inline btn-danger-outline", children }: ResetProps) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className={className} onClick={() => setOpen(true)} data-testid="browser-data-reset">
        {children ?? `${RESET_BUTTON_LABEL}…`}
      </button>
      {open && (
        <BrowserDataResetDialog
          onClose={() => setOpen(false)}
          onExportIncident={onExportIncident}
          onCleared={onCleared}
        />
      )}
    </>
  );
}
