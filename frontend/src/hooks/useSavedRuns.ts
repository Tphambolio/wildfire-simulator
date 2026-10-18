/**
 * Saved runs list state (services/savedRuns.ts): passphrase (sessionStorage only, cleared on
 * 401/403), filters ("near current map view", a date range in America/Edmonton), paging, and
 * rename / delete.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  deleteSavedRun,
  listSavedRuns,
  loadPassphrase,
  renameSavedRun,
  SavedRunsAuthError,
  SavedRunsRateLimitError,
  storePassphrase,
  type SavedRun,
} from "../services/savedRuns";
import { toDateTimeInputs, zonedWallTimeToMs } from "../utils/time";

export const PAGE_SIZE = 20;

export interface SavedRunsFilterState {
  nearView: boolean;
  /** "YYYY-MM-DD" in America/Edmonton, inclusive; "" = open */
  from: string;
  to: string;
}

/** The map's current view as [west, south, east, north] (MapView publishes it on its canvas). */
export function currentMapBox(doc: Document = document): [number, number, number, number] | null {
  const raw = doc.querySelector(".map-view-canvas")?.getAttribute("data-bounds");
  if (!raw) return null;
  try {
    const [[w, s], [e, n]] = JSON.parse(raw) as [[number, number], [number, number]];
    return [w, s, e, n].every(Number.isFinite) ? [w, s, e, n] : null;
  } catch {
    return null;
  }
}

/** Day range (Edmonton local dates, inclusive) as ISO instants for the API. */
export function dayRange(from: string, to: string): { since: string | null; until: string | null } {
  const start = from ? zonedWallTimeToMs(from, "00:00") : null;
  let until: string | null = null;
  if (to) {
    const end = zonedWallTimeToMs(to, "00:00");
    // the end day is included: until the next local midnight (≈ +24 h; DST-safe via a re-parse)
    if (end !== null) {
      const next = new Date(end + 36 * 3_600_000);
      const ymd = toDateTimeInputs(next.getTime()).date;
      const nextMidnight = zonedWallTimeToMs(ymd, "00:00");
      until = nextMidnight !== null ? new Date(nextMidnight).toISOString() : null;
    }
  }
  return { since: start !== null ? new Date(start).toISOString() : null, until };
}

export function useSavedRuns(active: boolean) {
  const [passphrase, setPassphrase] = useState<string | null>(() => loadPassphrase());
  const [runs, setRuns] = useState<SavedRun[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);
  const [filters, setFilters] = useState<SavedRunsFilterState>({ nearView: false, from: "", to: "" });
  const [archiveEnabled, setArchiveEnabled] = useState(true);
  const reqRef = useRef<AbortController | null>(null);
  // memory copy, so a blocked sessionStorage still keeps the passphrase for this page
  const passRef = useRef<string | null>(passphrase);

  const lock = useCallback((message: string | null = null) => {
    reqRef.current?.abort();
    passRef.current = null;
    storePassphrase(null);
    setPassphrase(null);
    setRuns([]);
    setTotal(0);
    setAuthError(message);
  }, []);

  const handle = useCallback((err: unknown) => {
    if (err instanceof DOMException && err.name === "AbortError") return;
    if (err instanceof SavedRunsAuthError) {
      lock(err.message);
      return;
    }
    if (err instanceof SavedRunsRateLimitError) {
      setError(err.message);
      return;
    }
    setError(err instanceof Error ? err.message : "Saved runs unavailable");
  }, [lock]);

  const load = useCallback(async (pass: string, at: number, f: SavedRunsFilterState) => {
    reqRef.current?.abort();
    const ctl = new AbortController();
    reqRef.current = ctl;
    setLoading(true);
    setError(null);
    try {
      const box = f.nearView ? currentMapBox() : null;
      const { since, until } = dayRange(f.from, f.to);
      const out = await listSavedRuns(pass, { box, since, until, limit: PAGE_SIZE, offset: at }, ctl.signal);
      if (ctl.signal.aborted) return;
      setRuns(out.runs);
      setTotal(out.total);
      setArchiveEnabled(out.archive_enabled);
      setAuthError(null);
    } catch (err) {
      handle(err);
    } finally {
      if (!ctl.signal.aborted) setLoading(false);
    }
  }, [handle]);

  const refresh = useCallback(() => {
    if (passRef.current) void load(passRef.current, offset, filters);
  }, [load, offset, filters]);

  // (Re)load when the panel is open and the passphrase, page or filters change
  useEffect(() => {
    if (active && passphrase) void load(passphrase, offset, filters);
    return () => reqRef.current?.abort();
  }, [active, passphrase, offset, filters, load]);

  const submitPassphrase = useCallback((value: string) => {
    const v = value.trim();
    if (!v) return;
    passRef.current = v;
    storePassphrase(v);
    setAuthError(null);
    setOffset(0);
    setPassphrase(v);
  }, []);

  const updateFilters = useCallback((patch: Partial<SavedRunsFilterState>) => {
    setOffset(0);
    setFilters((f) => ({ ...f, ...patch }));
  }, []);

  const rename = useCallback(async (id: string, label: string): Promise<boolean> => {
    const pass = passRef.current;
    if (!pass) return false;
    try {
      const out = await renameSavedRun(pass, id, label.trim() || null);
      setRuns((rs) => rs.map((r) => (r.id === id ? { ...r, label: out.label } : r)));
      return true;
    } catch (err) {
      handle(err);
      return false;
    }
  }, [handle]);

  const remove = useCallback(async (id: string): Promise<boolean> => {
    const pass = passRef.current;
    if (!pass) return false;
    try {
      await deleteSavedRun(pass, id);
      setRuns((rs) => rs.filter((r) => r.id !== id));
      setTotal((t) => Math.max(0, t - 1));
      return true;
    } catch (err) {
      handle(err);
      return false;
    }
  }, [handle]);

  return {
    unlocked: !!passphrase,
    runs, total, offset, loading, error, authError, filters, archiveEnabled,
    submitPassphrase, lock, refresh, setOffset, updateFilters, rename, remove,
  };
}
