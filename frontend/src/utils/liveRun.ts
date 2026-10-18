/**
 * The run on screen, kept in sessionStorage so a reload of the tab can re-attach to it
 * (field test 2026-10-18, Issue 2: reloading during a 72 h run showed "No run yet" while the
 * server finished and archived the run, recoverable only through the passphrase-protected
 * Saved runs list).
 *
 * Only this tab's own run is kept (sessionStorage is per tab and cleared with it). The app
 * re-reads the run with GET /api/v1/simulations/{id}, which needs only the run ID the server
 * gave this tab; the saved-runs list and its passphrase are not involved. Nothing here is sent
 * anywhere. A record older than LIVE_RUN_MAX_AGE_MS is ignored.
 */

import type { BurningPeriod } from "../types/simulation";
import type { RunParams } from "../components/WeatherPanel";

export const LIVE_RUN_KEY = "firesim-v3-live-run";
/** A run started more than this long ago is not re-attached after a reload. */
export const LIVE_RUN_MAX_AGE_MS = 12 * 3_600_000;

export interface LiveRunRecord {
  /** API simulation ID */
  id: string;
  /** Scenario start (ISO), for clock times */
  startIso: string | null;
  /** Hours the run was asked for (progress, "shown to" check) */
  durationHours: number | null;
  /** Wall-clock time the run was started (ms), for the elapsed timer */
  startedAt: number | null;
  /** Ensemble members requested with the run (null = none) */
  ensembleMembers: number | null;
  burningPeriod: BurningPeriod | null;
  /** The run's inputs (header chips, EOC forms) */
  runParams: RunParams | null;
  /** The incident and operational period the run is filed under when it completes */
  runTarget: { incidentId: string; periodIndex: number } | null;
  /** When this record was written (ms) */
  savedAt: number;
}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const numOrNull = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

export function saveLiveRun(rec: LiveRunRecord): void {
  try {
    sessionStorage.setItem(LIVE_RUN_KEY, JSON.stringify(rec));
  } catch {
    // storage blocked or full: a reload then shows no run, as before
  }
}

export function clearLiveRun(): void {
  try {
    sessionStorage.removeItem(LIVE_RUN_KEY);
  } catch {
    // storage blocked
  }
}

/** The stored run, or null (none, unreadable, or older than LIVE_RUN_MAX_AGE_MS). */
export function readLiveRun(now: number = Date.now()): LiveRunRecord | null {
  let raw: string | null = null;
  try {
    raw = sessionStorage.getItem(LIVE_RUN_KEY);
  } catch {
    return null;
  }
  if (!raw) return null;
  let v: unknown;
  try {
    v = JSON.parse(raw);
  } catch {
    clearLiveRun();
    return null;
  }
  if (!isObj(v) || typeof v.id !== "string" || !v.id) return null;
  const savedAt = numOrNull(v.savedAt);
  const startedAt = numOrNull(v.startedAt);
  const since = startedAt ?? savedAt;
  if (since === null || now - since > LIVE_RUN_MAX_AGE_MS) {
    clearLiveRun();
    return null;
  }
  const target = isObj(v.runTarget) && typeof v.runTarget.incidentId === "string" && typeof v.runTarget.periodIndex === "number"
    ? { incidentId: v.runTarget.incidentId, periodIndex: v.runTarget.periodIndex }
    : null;
  return {
    id: v.id,
    startIso: typeof v.startIso === "string" ? v.startIso : null,
    durationHours: numOrNull(v.durationHours),
    startedAt,
    ensembleMembers: numOrNull(v.ensembleMembers),
    burningPeriod: isObj(v.burningPeriod) ? (v.burningPeriod as unknown as BurningPeriod) : null,
    runParams: isObj(v.runParams) ? (v.runParams as unknown as RunParams) : null,
    runTarget: target,
    savedAt: savedAt ?? since,
  };
}
