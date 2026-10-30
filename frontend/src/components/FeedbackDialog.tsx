/**
 * "Feedback" (top bar): "Was this useful today?" 1-5, an optional note, and links that open a
 * prefilled GitHub issue (or an e-mail when the build sets an address). Nothing is posted until
 * the user sends the issue or e-mail themselves; the rating is counted only when the build turns
 * usage counting on (provider "post"; docs/privacy.md). No run, place or incident data is added.
 */
import { useEffect, useId, useRef, useState } from "react";
import { doc } from "../content/explanations";
import { issueURL, mailURL, sendRating, type PageContext, type TelemetryConfig } from "../telemetry/telemetry";

interface Props {
  cfg: TelemetryConfig;
  ctx: PageContext;
  onClose: () => void;
}

export default function FeedbackDialog({ cfg, ctx, onClose }: Props) {
  const id = useId();
  const [rating, setRating] = useState<number | null>(null);
  const [thanks, setThanks] = useState("");
  const [text, setText] = useState("");
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const firstRef = useRef<HTMLButtonElement | null>(null);

  // Focus moves into the dialog on open and back to the opener on close
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    firstRef.current?.focus();
    return () => opener?.focus?.();
  }, []);

  // Esc closes; Tab stays inside (wrap-around trap, as ValidationScorecard)
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
        dialogRef.current.querySelectorAll<HTMLElement>("button:not([disabled]), [href], textarea, [tabindex]:not([tabindex='-1'])"),
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

  const rate = (n: number) => {
    setRating(n);
    setThanks(sendRating(cfg, ctx, n) ? "Thanks, recorded." : "Thanks. Add a note below and open an issue to send it.");
  };
  const mail = mailURL(cfg, ctx, text, rating);

  return (
    <div className="bdr-backdrop" onPointerDown={(e) => e.target === e.currentTarget && onClose()} data-testid="feedback-dialog">
      <div ref={dialogRef} className="bdr-dialog fbk-dialog" role="dialog" aria-modal="true" aria-labelledby={`${id}-title`}>
        <h3 id={`${id}-title`} className="bdr-title">Was this useful today?</h3>
        <div className="fbk-rating" role="group" aria-label="Rate 1 to 5">
          {[1, 2, 3, 4, 5].map((n) => (
            <button
              key={n}
              ref={n === 1 ? firstRef : undefined}
              type="button"
              className="btn-control fbk-rate"
              aria-pressed={rating === n}
              aria-label={`${n} of 5`}
              onClick={() => rate(n)}
            >
              {n}
            </button>
          ))}
        </div>
        <p className="fbk-thanks" role="status" aria-live="polite">{thanks}</p>
        <label htmlFor={`${id}-text`} className="fbk-label">What would make FireSim more useful for your exercises or planning?</label>
        <textarea
          id={`${id}-text`}
          className="fbk-text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Optional. Please leave out addresses and anything operationally sensitive."
          data-testid="feedback-text"
        />
        <div className="bdr-actions fbk-actions">
          <a className="btn-control fbk-link" href={issueURL(ctx, text, rating)} target="_blank" rel="noopener noreferrer" data-testid="feedback-issue">
            Open a GitHub issue
          </a>
          {mail && (
            <a className="btn-control fbk-link" href={mail} data-testid="feedback-email">
              Email
            </a>
          )}
          <button type="button" className="btn-control" onClick={onClose}>Close</button>
        </div>
        <p className="bdr-note">
          The issue or e-mail opens with the tab, build and your note filled in; nothing is posted until you send it.
          No ignition point, perimeter, run or incident is included.{" "}
          <a href={doc("privacy.md")} target="_blank" rel="noopener noreferrer">Privacy</a>
        </p>
      </div>
    </div>
  );
}
