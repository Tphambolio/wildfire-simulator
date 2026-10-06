/** Collapsible Setup section (design spec §2.2): a header with a one-line summary when closed. */

import { useId, useState, type ReactNode } from "react";

interface SetupSectionProps {
  num?: number;
  title: string;
  /** One-line summary shown in the header (always; it is the collapsed view). */
  summary?: ReactNode;
  /** Marks the summary as needing attention (validation errors, missing ignition). */
  attention?: boolean;
  defaultOpen?: boolean;
  children: ReactNode;
}

export default function SetupSection({ num, title, summary, attention, defaultOpen = false, children }: SetupSectionProps) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  return (
    <section className={`setup-section${open ? " open" : ""}`} aria-labelledby={`${id}-h`}>
      <h3 className="setup-section-h" id={`${id}-h`}>
        <button
          type="button"
          className="setup-section-toggle"
          aria-expanded={open}
          aria-controls={`${id}-body`}
          onClick={() => setOpen((v) => !v)}
        >
          <span className="setup-chev" aria-hidden="true">{open ? "▾" : "▸"}</span>
          {num !== undefined && <span className="setup-num">{num}</span>}
          <span className="setup-title">{title}</span>
          {summary && <span className={`setup-summary${attention ? " attention" : ""}`}>{summary}</span>}
        </button>
      </h3>
      <div id={`${id}-body`} className="setup-section-body" hidden={!open}>
        {children}
      </div>
    </section>
  );
}
