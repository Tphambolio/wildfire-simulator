/**
 * Collapsible side columns (owner request 2026-10-11): the collapse chevron in a panel header,
 * and the slim rail a collapsed panel leaves behind (icon, vertical label, reopen button, and
 * optional compact actions such as Run).
 */

import { forwardRef, type ReactNode } from "react";
import type { Side } from "../hooks/useLayout";

const NAMES: Record<Side, string> = { setup: "Setup", situation: "Situation" };
const KEYS: Record<Side, string> = { setup: "[", situation: "]" };

/** Chevron pointing toward the edge the panel collapses to (left for Setup, right for Situation). */
function Chevron({ dir }: { dir: "left" | "right" }) {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path
        d={dir === "left" ? "M10 3.5 5.5 8l4.5 4.5" : "M6 3.5 10.5 8 6 12.5"}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Setup: input sliders. Situation: a pulse line (what the fire is doing now). */
export function SideIcon({ side }: { side: Side }) {
  return side === "setup" ? (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true" focusable="false">
      <g fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
        <path d="M3 4.5h12M3 9h12M3 13.5h12" opacity="0.55" />
        <circle cx="6.5" cy="4.5" r="1.8" fill="var(--surface)" />
        <circle cx="11.5" cy="9" r="1.8" fill="var(--surface)" />
        <circle cx="8" cy="13.5" r="1.8" fill="var(--surface)" />
      </g>
    </svg>
  ) : (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true" focusable="false">
      <path
        d="M2 10h3l2-5 3.5 9 2-4H16"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

interface CollapseButtonProps {
  side: Side;
  controls: string;
  onCollapse: () => void;
}

/** The chevron in the panel header: collapses the panel to its rail. */
export const CollapseButton = forwardRef<HTMLButtonElement, CollapseButtonProps>(function CollapseButton(
  { side, controls, onCollapse },
  ref,
) {
  const name = NAMES[side];
  return (
    <button
      ref={ref}
      type="button"
      className="side-collapse-btn"
      aria-expanded={true}
      aria-controls={controls}
      aria-label={`Collapse ${name}`}
      aria-keyshortcuts={KEYS[side]}
      title={`Collapse ${name} (${KEYS[side]})`}
      onClick={onCollapse}
    >
      <Chevron dir={side === "setup" ? "left" : "right"} />
    </button>
  );
});

interface SideRailProps {
  side: Side;
  controls: string;
  onExpand: () => void;
  /** Compact actions under the label (Run, New ignition) or a small status */
  children?: ReactNode;
}

/** What a collapsed panel leaves behind: one large reopen target, then compact actions. */
export const SideRail = forwardRef<HTMLButtonElement, SideRailProps>(function SideRail(
  { side, controls, onExpand, children },
  ref,
) {
  const name = NAMES[side];
  return (
    <div className={`side-rail side-rail-${side}`} data-testid={`${side}-rail`}>
      <button
        ref={ref}
        type="button"
        className="side-rail-open"
        aria-expanded={false}
        aria-controls={controls}
        aria-label={`Expand ${name}`}
        aria-keyshortcuts={KEYS[side]}
        title={`Expand ${name} (${KEYS[side]})`}
        onClick={onExpand}
      >
        <Chevron dir={side === "setup" ? "right" : "left"} />
        <SideIcon side={side} />
        <span className="side-rail-label" aria-hidden="true">{name}</span>
      </button>
      {children && <div className="side-rail-actions">{children}</div>}
    </div>
  );
});
