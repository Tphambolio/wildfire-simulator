/**
 * Browser storage for incidents and saved scenarios: read defensively, write with a result.
 *
 * Incidents and scenarios live in localStorage across app versions, so what comes back may be
 * corrupted (hand-edited, truncated by a full disk) or written by an older version without
 * today's fields. Reading normalises every entry (drops what cannot be used, fills missing
 * fields with defaults) so the app never renders a half-formed record (QA 2026-10-11: a
 * non-array value or an incident without operationalPeriods threw on load). An unreadable
 * value is copied to `<key>.unreadable` once before the app overwrites it.
 */

import type { IncidentSession, OperationalPeriod } from "../types/incident";
import { makeOperationalPeriod } from "../types/incident";
import type { ScenarioConfig } from "../types/simulation";
import { fallbackScenarioConfig } from "./scenarioDefaults";

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const arr = <T>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);
const str = (v: unknown, fallback: string): string => (typeof v === "string" ? v : fallback);
const num = (v: unknown, fallback: number): number => (typeof v === "number" && Number.isFinite(v) ? v : fallback);

/** The stored array under `key`, or [] (unreadable values are kept aside under `<key>.unreadable`). */
export function readStoredArray(key: string): unknown[] {
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(key);
  } catch {
    return [];
  }
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (Array.isArray(parsed)) return parsed;
  } catch {
    // fall through: keep a copy of what could not be read
  }
  try {
    if (localStorage.getItem(`${key}.unreadable`) === null) localStorage.setItem(`${key}.unreadable`, raw);
  } catch {
    // storage full or blocked: nothing more to do
  }
  return [];
}

/** Write `value` as JSON; false when the browser refused (quota exceeded, storage blocked). */
export function writeStored(key: string, value: unknown): boolean {
  try {
    localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}

function normalisePeriod(raw: unknown, index: number): OperationalPeriod | null {
  if (!isObj(raw)) return null;
  const day = num(raw.day, index + 1);
  const base = makeOperationalPeriod(day, str(raw.date, new Date().toISOString().slice(0, 10)));
  const perimeter = Array.isArray(raw.finalPerimeter) ? (raw.finalPerimeter as [number, number][]) : null;
  return {
    ...base,
    ...raw,
    day,
    date: base.date,
    opPeriodStart: str(raw.opPeriodStart, base.opPeriodStart),
    opPeriodEnd: str(raw.opPeriodEnd, base.opPeriodEnd),
    weather: isObj(raw.weather) ? (raw.weather as unknown as OperationalPeriod["weather"]) : base.weather,
    fwi: isObj(raw.fwi) ? (raw.fwi as unknown as OperationalPeriod["fwi"]) : base.fwi,
    ignitionPoint: isObj(raw.ignitionPoint) ? (raw.ignitionPoint as OperationalPeriod["ignitionPoint"]) : null,
    simulationId: typeof raw.simulationId === "string" ? raw.simulationId : null,
    simulationStatus: typeof raw.simulationStatus === "string" ? raw.simulationStatus : null,
    durationHours: num(raw.durationHours, base.durationHours),
    frameSummaries: arr<OperationalPeriod["frameSummaries"][number]>(raw.frameSummaries).filter(isObj),
    finalPerimeter: perimeter,
    annotations: arr<OperationalPeriod["annotations"][number]>(raw.annotations).filter(
      (a) => isObj(a) && Array.isArray((a as { coordinates?: unknown }).coordinates),
    ),
    evacuationDecisions: arr<OperationalPeriod["evacuationDecisions"][number]>(raw.evacuationDecisions).filter(isObj),
    objectives: arr<unknown>(raw.objectives).filter((o): o is string => typeof o === "string"),
    situationNarrative: str(raw.situationNarrative, ""),
    runStartIso: typeof raw.runStartIso === "string" ? raw.runStartIso : null,
  };
}

/** The name an incident gets when its name is erased (E9, 2026-10-24). */
export const UNTITLED_INCIDENT = "Untitled incident";

/**
 * The name to store for what was typed: trimmed, and an empty or blank name becomes
 * "Untitled incident" explicitly. One rule for stored incidents (`normaliseIncident`), the
 * incident store (`updateIncidentField("name", …)`) and the EOC name editors, so erasing the
 * name in the EOC panel never snaps back to the old one.
 */
export function normaliseIncidentName(raw: unknown): string {
  const s = typeof raw === "string" ? raw.trim() : "";
  return s || UNTITLED_INCIDENT;
}

/** A usable incident from a stored value, or null (no id). Missing fields get defaults. */
export function normaliseIncident(raw: unknown): IncidentSession | null {
  if (!isObj(raw) || typeof raw.id !== "string" || !raw.id) return null;
  const now = new Date().toISOString();
  let periods = arr<unknown>(raw.operationalPeriods)
    .map(normalisePeriod)
    .filter((p): p is OperationalPeriod => p !== null);
  if (periods.length === 0) periods = [makeOperationalPeriod(1, now.slice(0, 10))];
  const idx = num(raw.activePeriodIndex, periods.length - 1);
  return {
    ...(raw as unknown as IncidentSession),
    id: raw.id,
    name: normaliseIncidentName(raw.name),
    incidentNumber: str(raw.incidentNumber, ""),
    incidentCommanderName: str(raw.incidentCommanderName, ""),
    status: raw.status === "closed" ? "closed" : "active",
    createdAt: str(raw.createdAt, now),
    updatedAt: str(raw.updatedAt, now),
    operationalPeriods: periods,
    activePeriodIndex: Number.isInteger(idx) && idx >= 0 && idx < periods.length ? idx : periods.length - 1,
    evacTiers: arr<NonNullable<IncidentSession["evacTiers"]>[number]>(raw.evacTiers).filter(isObj),
  };
}

export function loadIncidents(key: string): IncidentSession[] {
  return readStoredArray(key).map(normaliseIncident).filter((i): i is IncidentSession => i !== null);
}

const DEFAULT_DAY = { wind_speed: 20, wind_direction: 270, temperature: 25, relative_humidity: 30, precipitation_24h: 0 };

/**
 * A usable saved scenario, or null (no id, name or weather). Fields an older save lacks get the
 * Setup defaults, so loading it cannot leave Setup without e.g. FWI codes or multi-day days.
 */
export function normaliseScenario(raw: unknown): ScenarioConfig | null {
  if (!isObj(raw) || typeof raw.id !== "string" || typeof raw.name !== "string" || !isObj(raw.weather)) return null;
  const d = fallbackScenarioConfig(isObj(raw.ignitionPoint) ? (raw.ignitionPoint as ScenarioConfig["ignitionPoint"]) : null);
  const days = arr<unknown>(raw.multiDayDays).filter(isObj);
  return {
    ...d,
    ...(raw as unknown as ScenarioConfig),
    fwi: isObj(raw.fwi) ? (raw.fwi as unknown as ScenarioConfig["fwi"]) : d.fwi,
    simMode: raw.simMode === "multiday" ? "multiday" : "single",
    multiDayDays: (days.length ? days : [DEFAULT_DAY]) as ScenarioConfig["multiDayDays"],
    durationHours: num(raw.durationHours, d.durationHours),
    snapshotMinutes: num(raw.snapshotMinutes, d.snapshotMinutes),
    mcIterations: num(raw.mcIterations, d.mcIterations),
    createdAt: str(raw.createdAt, new Date().toISOString()),
  };
}

export function loadScenarios(key: string): ScenarioConfig[] {
  return readStoredArray(key).map(normaliseScenario).filter((s): s is ScenarioConfig => s !== null);
}
