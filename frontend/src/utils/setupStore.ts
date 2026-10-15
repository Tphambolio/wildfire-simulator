/**
 * Setup kept across a page reload, per incident (round-3 field test R8, Claude in Chrome
 * tester, 2026-10-13): after a reload the incident and its evacuation statuses came back but
 * the ignition point, start time, weather and grass curing did not; the start followed the
 * clock again and a 72 h run started at 04:57 instead of the time that had been set.
 *
 * The Setup column's state (the saved-scenario fields plus the start, curing, conifer,
 * weather source and structure options) is written to this browser's localStorage under the
 * active incident's id, or "scratch" with no incident open, and read back on load and when an
 * incident is opened. Nothing is sent to the server.
 */
import type { ScenarioConfig } from "../types/simulation";

/** Setup as WeatherPanel reports it (onConfigSnapshot): a scenario without its identity. */
export type SetupSnapshot = Omit<ScenarioConfig, "id" | "createdAt" | "name" | "description">;

const PREFIX = "firesim.setup.";

/** Storage key for an incident's setup ("scratch" when no incident is open). */
export function setupKey(incidentId: string | null | undefined): string {
  return `${PREFIX}${incidentId || "scratch"}`;
}

export function saveSetup(incidentId: string | null | undefined, snap: SetupSnapshot, storage: Storage = localStorage): void {
  try {
    storage.setItem(setupKey(incidentId), JSON.stringify({ v: 1, savedAt: new Date().toISOString(), setup: snap }));
  } catch {
    // storage full or blocked: the setup stays for this session only
  }
}

/** The stored setup, or null when none (or unreadable, or not the expected shape). */
export function loadSetup(incidentId: string | null | undefined, storage: Storage = localStorage): SetupSnapshot | null {
  try {
    const raw = storage.getItem(setupKey(incidentId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { v?: number; setup?: SetupSnapshot };
    const s = parsed?.setup;
    if (!s || typeof s !== "object" || !s.weather || !s.fwi || !Array.isArray(s.multiDayDays)) return null;
    return s;
  } catch {
    return null;
  }
}

export function deleteSetup(incidentId: string | null | undefined, storage: Storage = localStorage): void {
  try {
    storage.removeItem(setupKey(incidentId));
  } catch {
    // nothing to do
  }
}
