/**
 * Shell layout state (owner request 2026-10-11, "the viewable map is tiny"): collapsible Setup
 * (left) and Situation (right) columns, a map focus mode, and overlay drawers on narrow screens.
 *
 * Wide screens (>= 1280 px):
 * - each side is open or collapsed to a slim rail; an explicit toggle (button, `[` / `]`) is the
 *   user's preference and is remembered in localStorage;
 * - focus mode collapses both sides for the moment (not saved); leaving it restores the sides
 *   as they were; toggling one side while in focus mode leaves focus mode;
 * - after a run completes, Setup collapses once per page session, only when the user has never
 *   set a Setup preference; the first time ever, a hint says how to reopen it.
 * Narrow screens (< 1280 px): both sides are rails; one at a time opens as an overlay drawer over
 * the map (not saved). The saved wide-screen preferences are kept for when the window widens.
 */

import { useCallback, useEffect, useState } from "react";

export type Side = "setup" | "situation";
export type SideState = "open" | "collapsed";

export interface LayoutPrefs {
  setup?: SideState;
  situation?: SideState;
  /** The auto-collapse hint has been shown once */
  hintSeen?: boolean;
}

export const LAYOUT_KEY = "firesim-v3-layout";
/** Below this width the side columns become overlay drawers */
export const NARROW_QUERY = "(max-width: 1279.98px)";

export function loadLayoutPrefs(): LayoutPrefs {
  try {
    const raw = localStorage.getItem(LAYOUT_KEY);
    const p = raw ? (JSON.parse(raw) as unknown) : null;
    if (!p || typeof p !== "object") return {};
    const o = p as Record<string, unknown>;
    const side = (v: unknown): SideState | undefined => (v === "open" || v === "collapsed" ? v : undefined);
    return { setup: side(o.setup), situation: side(o.situation), hintSeen: o.hintSeen === true };
  } catch {
    return {};
  }
}

function saveLayoutPrefs(p: LayoutPrefs): void {
  try {
    localStorage.setItem(LAYOUT_KEY, JSON.stringify(p));
  } catch {
    // storage full or blocked: the layout still works for this session
  }
}

interface Collapsed {
  setup: boolean;
  situation: boolean;
}

export interface LayoutState {
  /** Wide-screen side states */
  collapsed: Collapsed;
  /** Focus mode: the side states to restore when it ends (null = not in focus mode) */
  focusRestore: Collapsed | null;
  /** Narrow screens: the open drawer */
  drawer: Side | null;
  /** Setup was auto-collapsed after a run in this page session */
  autoCollapsed: boolean;
  /** Show the one-time "Setup minimized" hint */
  hint: boolean;
  prefs: LayoutPrefs;
}

export function initialLayout(prefs: LayoutPrefs): LayoutState {
  return {
    collapsed: { setup: prefs.setup === "collapsed", situation: prefs.situation === "collapsed" },
    focusRestore: null,
    drawer: null,
    autoCollapsed: false,
    hint: false,
    prefs,
  };
}

export type LayoutAction =
  | { type: "toggle"; side: Side; narrow: boolean }
  | { type: "set"; side: Side; collapsed: boolean; narrow: boolean }
  | { type: "focus"; narrow: boolean }
  | { type: "escape"; narrow: boolean }
  | { type: "runCompleted"; narrow: boolean }
  | { type: "dismissHint" };

/** Pure state transitions (tested in useLayout.test.ts). Prefs changes are returned in `prefs`. */
export function layoutReducer(s: LayoutState, a: LayoutAction): LayoutState {
  switch (a.type) {
    case "toggle":
    case "set": {
      if (a.narrow) {
        const open = a.type === "toggle" ? s.drawer !== a.side : !a.collapsed;
        return { ...s, drawer: open ? a.side : s.drawer === a.side ? null : s.drawer };
      }
      const value = a.type === "toggle" ? !s.collapsed[a.side] : a.collapsed;
      const prefs = { ...s.prefs, [a.side]: value ? "collapsed" : "open" } as LayoutPrefs;
      // Toggling a side in focus mode leaves focus mode; the other side stays as shown
      return { ...s, collapsed: { ...s.collapsed, [a.side]: value }, focusRestore: null, prefs };
    }
    case "focus":
      if (a.narrow) return { ...s, drawer: null };
      if (s.focusRestore) return { ...s, collapsed: s.focusRestore, focusRestore: null };
      return { ...s, focusRestore: s.collapsed, collapsed: { setup: true, situation: true } };
    case "escape":
      if (a.narrow) return s.drawer ? { ...s, drawer: null } : s;
      return s.focusRestore ? { ...s, collapsed: s.focusRestore, focusRestore: null } : s;
    case "runCompleted": {
      if (a.narrow || s.autoCollapsed || s.prefs.setup !== undefined || s.collapsed.setup || s.focusRestore) return s;
      const hint = !s.prefs.hintSeen;
      return {
        ...s,
        collapsed: { ...s.collapsed, setup: true },
        autoCollapsed: true,
        hint,
        prefs: hint ? { ...s.prefs, hintSeen: true } : s.prefs,
      };
    }
    case "dismissHint":
      return s.hint ? { ...s, hint: false } : s;
  }
}

function useMediaQuery(query: string): boolean {
  const get = () => typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia(query).matches;
  const [matches, setMatches] = useState(get);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(query);
    const on = () => setMatches(mq.matches);
    on();
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, [query]);
  return matches;
}

export interface Layout {
  narrow: boolean;
  setupCollapsed: boolean;
  situationCollapsed: boolean;
  focusMode: boolean;
  drawer: Side | null;
  hint: boolean;
  toggle: (side: Side) => void;
  setCollapsed: (side: Side, collapsed: boolean) => void;
  toggleFocus: () => void;
  /** Esc: close the drawer (narrow) or leave focus mode; returns whether it did anything */
  escape: () => boolean;
  runCompleted: () => void;
  dismissHint: () => void;
}

export function useLayout(): Layout {
  const narrow = useMediaQuery(NARROW_QUERY);
  const [s, setS] = useState<LayoutState>(() => initialLayout(loadLayoutPrefs()));

  // Persist preference changes
  useEffect(() => {
    saveLayoutPrefs(s.prefs);
  }, [s.prefs]);

  const dispatch = useCallback((a: LayoutAction) => setS((prev) => layoutReducer(prev, a)), []);
  const toggle = useCallback((side: Side) => dispatch({ type: "toggle", side, narrow }), [dispatch, narrow]);
  const setCollapsed = useCallback(
    (side: Side, collapsed: boolean) => dispatch({ type: "set", side, collapsed, narrow }),
    [dispatch, narrow],
  );
  const toggleFocus = useCallback(() => dispatch({ type: "focus", narrow }), [dispatch, narrow]);
  const escape = useCallback(() => {
    const active = narrow ? s.drawer !== null : s.focusRestore !== null;
    if (active) dispatch({ type: "escape", narrow });
    return active;
  }, [dispatch, narrow, s.drawer, s.focusRestore]);
  const runCompleted = useCallback(() => dispatch({ type: "runCompleted", narrow }), [dispatch, narrow]);
  const dismissHint = useCallback(() => dispatch({ type: "dismissHint" }), [dispatch]);

  return {
    narrow,
    setupCollapsed: narrow ? s.drawer !== "setup" : s.collapsed.setup,
    situationCollapsed: narrow ? s.drawer !== "situation" : s.collapsed.situation,
    focusMode: !narrow && s.focusRestore !== null,
    drawer: narrow ? s.drawer : null,
    hint: s.hint && !narrow,
    toggle,
    setCollapsed,
    toggleFocus,
    escape,
    runCompleted,
    dismissHint,
  };
}

/** Keys that should not trigger layout shortcuts (typing in a field). */
export function isTypingTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  if (t.isContentEditable) return true;
  const tag = t.tagName;
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag === "INPUT") {
    const type = (t as HTMLInputElement).type;
    return !["checkbox", "radio", "button", "submit", "reset", "range", "color", "file"].includes(type);
  }
  return false;
}
