import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import {
  LAYOUT_KEY,
  initialLayout,
  isTypingTarget,
  layoutReducer,
  loadLayoutPrefs,
  useLayout,
  type LayoutState,
} from "./useLayout";

const wide = { narrow: false };
const fresh = (): LayoutState => initialLayout({});

function mockMatchMedia(narrow: boolean) {
  vi.stubGlobal("matchMedia", (q: string) => ({
    matches: narrow,
    media: q,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}

describe("layoutReducer (wide screens)", () => {
  it("starts with both sides open and no preference", () => {
    const s = fresh();
    expect(s.collapsed).toEqual({ setup: false, situation: false });
    expect(s.prefs).toEqual({});
  });

  it("toggling a side collapses it and records the preference", () => {
    let s = layoutReducer(fresh(), { type: "toggle", side: "setup", ...wide });
    expect(s.collapsed.setup).toBe(true);
    expect(s.prefs.setup).toBe("collapsed");
    s = layoutReducer(s, { type: "toggle", side: "setup", ...wide });
    expect(s.collapsed.setup).toBe(false);
    expect(s.prefs.setup).toBe("open");
    s = layoutReducer(s, { type: "set", side: "situation", collapsed: true, ...wide });
    expect(s.collapsed.situation).toBe(true);
    expect(s.prefs.situation).toBe("collapsed");
  });

  it("focus mode collapses both sides without saving, and restores them on exit", () => {
    let s = layoutReducer(fresh(), { type: "set", side: "situation", collapsed: true, ...wide });
    s = layoutReducer(s, { type: "focus", ...wide });
    expect(s.collapsed).toEqual({ setup: true, situation: true });
    expect(s.focusRestore).toEqual({ setup: false, situation: true });
    expect(s.prefs.setup).toBeUndefined();
    s = layoutReducer(s, { type: "escape", ...wide });
    expect(s.collapsed).toEqual({ setup: false, situation: true });
    expect(s.focusRestore).toBeNull();
    // Toggle twice = in and out
    s = layoutReducer(layoutReducer(s, { type: "focus", ...wide }), { type: "focus", ...wide });
    expect(s.collapsed).toEqual({ setup: false, situation: true });
  });

  it("toggling a side in focus mode leaves focus mode and keeps the other side collapsed", () => {
    let s = layoutReducer(fresh(), { type: "focus", ...wide });
    s = layoutReducer(s, { type: "toggle", side: "setup", ...wide });
    expect(s.focusRestore).toBeNull();
    expect(s.collapsed).toEqual({ setup: false, situation: true });
  });

  it("escape does nothing outside focus mode", () => {
    const s = fresh();
    expect(layoutReducer(s, { type: "escape", ...wide })).toBe(s);
  });

  it("auto-collapses Setup after the first run only, and shows the hint the first time ever", () => {
    let s = layoutReducer(fresh(), { type: "runCompleted", ...wide });
    expect(s.collapsed.setup).toBe(true);
    expect(s.hint).toBe(true);
    expect(s.prefs.hintSeen).toBe(true);
    // Not a saved preference
    expect(s.prefs.setup).toBeUndefined();
    s = layoutReducer(s, { type: "dismissHint" });
    expect(s.hint).toBe(false);
    // The user reopens Setup (now a preference); later runs leave it open
    s = layoutReducer(s, { type: "toggle", side: "setup", ...wide });
    s = layoutReducer(s, { type: "runCompleted", ...wide });
    expect(s.collapsed.setup).toBe(false);
  });

  it("auto-collapses only once per session even without a preference", () => {
    let s = layoutReducer(fresh(), { type: "runCompleted", ...wide });
    // Setup reopened by focus-mode exit or similar without a preference: no second collapse
    s = { ...s, collapsed: { ...s.collapsed, setup: false } };
    expect(layoutReducer(s, { type: "runCompleted", ...wide }).collapsed.setup).toBe(false);
  });

  it("never auto-collapses when the user has a Setup preference", () => {
    const s = initialLayout({ setup: "open" });
    expect(layoutReducer(s, { type: "runCompleted", ...wide })).toBe(s);
  });

  it("no hint after the first time", () => {
    const s = layoutReducer(initialLayout({ hintSeen: true }), { type: "runCompleted", ...wide });
    expect(s.collapsed.setup).toBe(true);
    expect(s.hint).toBe(false);
  });
});

describe("layoutReducer (narrow screens: drawers)", () => {
  const narrow = { narrow: true };
  it("opens one drawer at a time, without saving a preference", () => {
    let s = layoutReducer(fresh(), { type: "toggle", side: "setup", ...narrow });
    expect(s.drawer).toBe("setup");
    s = layoutReducer(s, { type: "toggle", side: "situation", ...narrow });
    expect(s.drawer).toBe("situation");
    expect(s.prefs).toEqual({});
    s = layoutReducer(s, { type: "escape", ...narrow });
    expect(s.drawer).toBeNull();
  });

  it("does not auto-collapse on narrow screens", () => {
    const s = fresh();
    expect(layoutReducer(s, { type: "runCompleted", ...narrow })).toBe(s);
  });
});

describe("useLayout", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.unstubAllGlobals());

  it("remembers side preferences in localStorage", () => {
    mockMatchMedia(false);
    const { result, unmount } = renderHook(() => useLayout());
    expect(result.current.setupCollapsed).toBe(false);
    act(() => result.current.toggle("situation"));
    expect(result.current.situationCollapsed).toBe(true);
    expect(JSON.parse(localStorage.getItem(LAYOUT_KEY)!)).toMatchObject({ situation: "collapsed" });
    unmount();
    const again = renderHook(() => useLayout());
    expect(again.result.current.situationCollapsed).toBe(true);
    expect(again.result.current.setupCollapsed).toBe(false);
  });

  it("focus mode via the hook; escape reports whether it acted", () => {
    mockMatchMedia(false);
    const { result } = renderHook(() => useLayout());
    let acted = true;
    act(() => { acted = result.current.escape(); });
    expect(acted).toBe(false);
    act(() => result.current.toggleFocus());
    expect(result.current.focusMode).toBe(true);
    expect(result.current.setupCollapsed && result.current.situationCollapsed).toBe(true);
    act(() => { acted = result.current.escape(); });
    expect(acted).toBe(true);
    expect(result.current.focusMode).toBe(false);
    expect(result.current.setupCollapsed).toBe(false);
  });

  it("narrow screens: both sides start as rails, saved wide preferences are ignored", () => {
    localStorage.setItem(LAYOUT_KEY, JSON.stringify({ setup: "open", situation: "open" }));
    mockMatchMedia(true);
    const { result } = renderHook(() => useLayout());
    expect(result.current.narrow).toBe(true);
    expect(result.current.setupCollapsed).toBe(true);
    expect(result.current.situationCollapsed).toBe(true);
    act(() => result.current.toggle("setup"));
    expect(result.current.drawer).toBe("setup");
    expect(result.current.setupCollapsed).toBe(false);
  });

  it("survives unreadable storage", () => {
    localStorage.setItem(LAYOUT_KEY, "{not json");
    expect(loadLayoutPrefs()).toEqual({});
    localStorage.setItem(LAYOUT_KEY, JSON.stringify({ setup: "sideways", hintSeen: "yes" }));
    expect(loadLayoutPrefs()).toEqual({ setup: undefined, situation: undefined, hintSeen: false });
  });
});

describe("isTypingTarget", () => {
  it("is true for text fields and false for buttons and checkboxes", () => {
    const input = document.createElement("input");
    expect(isTypingTarget(input)).toBe(true);
    const cb = document.createElement("input");
    cb.type = "checkbox";
    expect(isTypingTarget(cb)).toBe(false);
    expect(isTypingTarget(document.createElement("button"))).toBe(false);
    expect(isTypingTarget(document.createElement("select"))).toBe(true);
    expect(isTypingTarget(null)).toBe(false);
  });
});
