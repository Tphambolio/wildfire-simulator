/**
 * Accessible explanation pop-ups (toggletip pattern, WCAG 1.4.13): one core used by the small
 * "i" button (`InfoTip`) and by short status badges (`Badge.tsx`).
 *
 * - Opens on hover (after ~300 ms), on keyboard focus and on tap/click (a click pins it open).
 * - Esc or a click/tap outside closes it; it stays open while the pointer is over the trigger
 *   or the pop-up (dismissible, hoverable, persistent).
 * - Plain-text tips are `role="tooltip"` and the trigger points at them with
 *   `aria-describedby`, so a screen reader reads the label and then the explanation.
 *   Tips with links (`interactive`) have no tooltip role: the trigger carries `aria-expanded`
 *   and `aria-controls`, and Tab moves into the pop-up.
 * - The pop-up is a manual popover (top layer) with fixed positioning, so the scrolling
 *   Setup and Situation columns cannot clip it; it stays in the DOM next to its trigger.
 * - For a disabled control, put the tip on its label or an "i" next to it: disabled inputs get
 *   no events.
 */
import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type FocusEvent,
  type KeyboardEvent,
  type MouseEvent,
  type PointerEvent,
  type ReactNode,
  type Ref,
} from "react";

export const TIP_OPEN_DELAY_MS = 300;
const TIP_CLOSE_DELAY_MS = 200;
const GAP = 6;
const MARGIN = 8;

/** Props a tip spreads onto its trigger element (a button). */
export interface TipTriggerProps {
  ref: Ref<HTMLButtonElement>;
  "aria-describedby"?: string;
  "aria-expanded"?: boolean;
  "aria-controls"?: string;
  onPointerEnter: (e: PointerEvent) => void;
  onPointerLeave: (e: PointerEvent) => void;
  onFocus: () => void;
  onClick: (e: MouseEvent) => void;
  onKeyDown: (e: KeyboardEvent) => void;
}

interface TipCoreProps {
  content: ReactNode;
  /** The content has links or buttons: no tooltip role, focus can move into it */
  interactive?: boolean;
  renderTrigger: (props: TipTriggerProps, open: boolean) => ReactNode;
  className?: string;
  /** Called on click instead of pinning the tip (e.g. a badge that navigates) */
  onActivate?: () => void;
  testId?: string;
}

function place(trigger: HTMLElement, pop: HTMLElement) {
  const t = trigger.getBoundingClientRect();
  const w = pop.offsetWidth;
  const h = pop.offsetHeight;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  let top = t.bottom + GAP;
  if (top + h > vh - MARGIN && t.top - GAP - h >= MARGIN) top = t.top - GAP - h;
  const left = Math.max(MARGIN, Math.min(t.left, vw - w - MARGIN));
  pop.style.top = `${Math.round(Math.max(MARGIN, top))}px`;
  pop.style.left = `${Math.round(left)}px`;
}

export function TipCore({ content, interactive = false, renderTrigger, className = "", onActivate, testId }: TipCoreProps) {
  const id = useId();
  const tipId = `${id}-tip`;
  const [open, setOpen] = useState(false);
  const pinned = useRef(false);
  // Focus returned to the trigger by Escape must not reopen the tip
  const skipFocusOpen = useRef(false);
  const wrapRef = useRef<HTMLSpanElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const popRef = useRef<HTMLSpanElement | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearTimer = () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  };
  const show = useCallback(() => {
    clearTimer();
    setOpen(true);
  }, []);
  const hide = useCallback(() => {
    clearTimer();
    pinned.current = false;
    setOpen(false);
  }, []);
  const later = (fn: () => void, ms: number) => {
    clearTimer();
    timer.current = setTimeout(fn, ms);
  };
  useEffect(() => () => clearTimer(), []);

  // Top layer + position while open; follow scrolling columns and window resizes
  useLayoutEffect(() => {
    const pop = popRef.current;
    const trig = triggerRef.current;
    if (!pop || !trig) return;
    const supportsPopover = typeof pop.showPopover === "function";
    if (open) {
      if (supportsPopover && !pop.matches(":popover-open")) {
        try {
          pop.showPopover();
        } catch {
          /* not connected or already shown: the .open class still shows it */
        }
      }
      place(trig, pop);
      const onMove = () => place(trig, pop);
      window.addEventListener("scroll", onMove, true);
      window.addEventListener("resize", onMove);
      return () => {
        window.removeEventListener("scroll", onMove, true);
        window.removeEventListener("resize", onMove);
      };
    }
    if (supportsPopover && pop.matches(":popover-open")) {
      try {
        pop.hidePopover();
      } catch {
        /* already hidden */
      }
    }
    return undefined;
  }, [open]);

  // Esc and outside clicks close it while open
  useEffect(() => {
    if (!open) return;
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const inside = wrapRef.current?.contains(document.activeElement);
      hide();
      if (inside && document.activeElement !== triggerRef.current) {
        skipFocusOpen.current = true;
        triggerRef.current?.focus();
        skipFocusOpen.current = false;
      }
    };
    const onDown = (e: globalThis.PointerEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) hide();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown);
    };
  }, [open, hide]);

  const hoverable = (e: PointerEvent) => e.pointerType !== "touch";
  const triggerProps: TipTriggerProps = {
    ref: (el: HTMLButtonElement | null) => {
      triggerRef.current = el;
    },
    ...(interactive ? { "aria-expanded": open, "aria-controls": tipId } : { "aria-describedby": tipId }),
    onPointerEnter: (e) => {
      if (!hoverable(e)) return;
      if (open) clearTimer();
      else later(show, TIP_OPEN_DELAY_MS);
    },
    onPointerLeave: (e) => {
      if (!hoverable(e) || pinned.current) return;
      later(() => setOpen(false), TIP_CLOSE_DELAY_MS);
    },
    onFocus: () => {
      if (!skipFocusOpen.current) show();
    },
    onClick: (e) => {
      if (onActivate) {
        onActivate();
        return;
      }
      e.preventDefault();
      if (pinned.current && open) hide();
      else {
        pinned.current = true;
        show();
      }
    },
    onKeyDown: (e) => {
      if (e.key === "Escape" && open) {
        e.stopPropagation();
        hide();
      }
    },
  };

  const onBlurWithin = (e: FocusEvent) => {
    if (wrapRef.current?.contains(e.relatedTarget as Node | null)) return;
    hide();
  };

  return (
    <span ref={wrapRef} className={`tip-anchor ${className}`.trim()} onBlur={onBlurWithin} data-testid={testId}>
      {renderTrigger(triggerProps, open)}
      <span
        ref={popRef}
        id={tipId}
        role={interactive ? undefined : "tooltip"}
        popover="manual"
        className={`tip-pop info-tip-text${interactive ? " tip-pop-interactive" : ""}${open ? " open" : ""}`}
        onPointerEnter={(e) => hoverable(e) && clearTimer()}
        onPointerLeave={(e) => {
          if (!hoverable(e) || pinned.current) return;
          later(() => setOpen(false), TIP_CLOSE_DELAY_MS);
        }}
      >
        {content}
      </span>
    </span>
  );
}

/**
 * An action button whose explanation shows on hover or keyboard focus (replaces a native
 * `title`, which keyboard and touch users never see). Click runs the action.
 */
export function TipButton({
  tip,
  onClick,
  className = "",
  disabled = false,
  children,
}: {
  tip: string;
  onClick: () => void;
  className?: string;
  disabled?: boolean;
  children: ReactNode;
}) {
  return (
    <TipCore
      content={tip}
      onActivate={onClick}
      renderTrigger={(p) => (
        <button type="button" className={className} disabled={disabled} {...p}>
          {children}
        </button>
      )}
    />
  );
}

interface InfoTipProps {
  /** Tip text (or use children for rich content) */
  text?: string;
  children?: ReactNode;
  /** Accessible name of the button, e.g. "About house-to-house spread" */
  label: string;
  /** Content has links: no tooltip role, focus moves into it */
  interactive?: boolean;
  /** Kept for callers; placement is automatic (flips and stays inside the window) */
  alignRight?: boolean;
  className?: string;
}

/** Small "i" button whose explanation shows on hover, focus or tap. */
export default function InfoTip({ text, children, label, interactive = false, className = "" }: InfoTipProps) {
  return (
    <TipCore
      className={`info-tip ${className}`.trim()}
      interactive={interactive}
      content={children ?? text}
      renderTrigger={(p) => (
        <button type="button" className="info-tip-btn" aria-label={label} {...p}>
          i
        </button>
      )}
    />
  );
}
