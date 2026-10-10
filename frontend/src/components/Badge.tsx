/**
 * Short status badge (four words or fewer) for what must stay on screen: "Model output",
 * "Range too narrow", "Illustrative", "Unsourced"... With a `tip`, the badge is a button that
 * shows the full explanation on hover, focus or tap (InfoTip.tsx); without one it is a plain chip.
 */
import type { CSSProperties, ReactNode } from "react";
import { TipCore } from "./InfoTip";

export type BadgeTone = "warn" | "info" | "danger" | "neutral" | "ok";

interface BadgeProps {
  children: ReactNode;
  /** Explanation shown in the pop-up */
  tip?: ReactNode;
  tone?: BadgeTone;
  /** The tip has links (no tooltip role; focus moves into it) */
  interactive?: boolean;
  /** Click runs this instead of pinning the tip (e.g. open the About tab) */
  onActivate?: () => void;
  className?: string;
  style?: CSSProperties;
  testId?: string;
}

export default function Badge({ children, tip, tone = "info", interactive = false, onActivate, className = "", style, testId }: BadgeProps) {
  const cls = `ui-badge ui-badge-${tone} ${className}`.trim();
  if (tip === undefined || tip === null || tip === "") {
    return (
      <span className={cls} style={style} data-testid={testId}>
        {children}
      </span>
    );
  }
  return (
    <TipCore
      className="badge-anchor"
      content={tip}
      interactive={interactive}
      onActivate={onActivate}
      renderTrigger={(p) => (
        <button type="button" className={`${cls} ui-badge-tip`} style={style} data-testid={testId} {...p}>
          {children}
        </button>
      )}
    />
  );
}
