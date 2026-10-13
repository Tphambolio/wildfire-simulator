/**
 * Saved runs (run archive phase 2, docs/run-archive.md): the server's list of runs, behind a
 * shared passphrase sent as `X-FireSim-Passphrase`. The passphrase is kept in sessionStorage
 * only (this tab, until it closes) and dropped when the server answers 401 or 403.
 */

const API_BASE = import.meta.env.VITE_API_URL || "";
const URL = `${API_BASE}/api/v1/simulations`;

export const PASSPHRASE_HEADER = "X-FireSim-Passphrase";
const STORAGE_KEY = "firesim.savedRuns.passphrase";
export const LABEL_MAX = 80;

export interface SavedRun {
  id: string;
  label: string | null;
  kind: "single" | "multiday" | "perimeter_override" | string | null;
  status: string;
  archived: boolean;
  created_at: string | null;
  finished_at: string | null;
  start_time: string | null;
  ignition_lat: number | null;
  ignition_lng: number | null;
  area_ha: number | null;
  duration_hours: number | null;
  frame_count: number | null;
  days: number | null;
  fuel_source: string | null;
  buildings_source: string | null;
  ensemble: boolean;
  ensemble_status: string | null;
  ensemble_members: number | null;
}

export interface SavedRunList {
  runs: SavedRun[];
  total: number;
  limit: number;
  offset: number;
  archive_enabled: boolean;
}

export interface SavedRunFilters {
  /** [west, south, east, north] around the ignition point */
  box?: [number, number, number, number] | null;
  /** ISO 8601 times (created at or after / before) */
  since?: string | null;
  until?: string | null;
  kind?: string | null;
  limit?: number;
  offset?: number;
}

/** 401/403: the passphrase is missing, wrong, or the list is disabled on the server. */
export class SavedRunsAuthError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

/** 429: too many wrong passphrases from this address; retry after `retryAfter` s. */
export class SavedRunsRateLimitError extends Error {
  readonly retryAfter: number;
  constructor(message: string, retryAfter: number) {
    super(message);
    this.retryAfter = retryAfter;
  }
}

export function loadPassphrase(): string | null {
  try {
    return sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function storePassphrase(value: string | null): void {
  try {
    if (value) sessionStorage.setItem(STORAGE_KEY, value);
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // storage blocked (private mode, policy): the passphrase lives in memory only
  }
}

async function call(path: string, passphrase: string, init: RequestInit = {}, signal?: AbortSignal): Promise<Response> {
  const resp = await fetch(`${URL}${path}`, {
    ...init,
    signal,
    headers: { ...(init.headers ?? {}), [PASSPHRASE_HEADER]: passphrase },
  });
  if (resp.ok) return resp;
  const err = await resp.json().catch(() => ({ detail: resp.statusText }));
  const detail = typeof err.detail === "string" ? err.detail : `Request failed (${resp.status})`;
  if (resp.status === 401 || resp.status === 403) throw new SavedRunsAuthError(detail, resp.status);
  if (resp.status === 429) {
    throw new SavedRunsRateLimitError(detail, Number(resp.headers.get("Retry-After")) || 1);
  }
  throw new Error(detail);
}

export async function listSavedRuns(passphrase: string, f: SavedRunFilters = {}, signal?: AbortSignal): Promise<SavedRunList> {
  const q = new URLSearchParams();
  q.set("limit", String(f.limit ?? 50));
  q.set("offset", String(f.offset ?? 0));
  if (f.box) {
    const [w, s, e, n] = f.box;
    q.set("west", String(w));
    q.set("south", String(s));
    q.set("east", String(e));
    q.set("north", String(n));
  }
  if (f.since) q.set("since", f.since);
  if (f.until) q.set("until", f.until);
  if (f.kind) q.set("kind", f.kind);
  return (await call(`?${q.toString()}`, passphrase, {}, signal)).json();
}

export async function renameSavedRun(passphrase: string, id: string, label: string | null): Promise<{ id: string; label: string | null }> {
  const resp = await call(`/${encodeURIComponent(id)}`, passphrase, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label }),
  });
  return resp.json();
}

export async function deleteSavedRun(passphrase: string, id: string): Promise<void> {
  await call(`/${encodeURIComponent(id)}`, passphrase, { method: "DELETE" });
}

/** A run request with the optional run label (trimmed; none when empty). */
export function withRunLabel<T extends object>(params: T, label: string): T {
  const v = label.trim().slice(0, LABEL_MAX);
  return v ? { ...params, label: v } : params;
}
