/**
 * Inject log (docs/exercise-mode-spec.md §6): what the controller pushed into the exercise and
 * when, in exercise time. PR0 keeps the log in memory and shows it in an empty panel; the
 * delivery of each inject kind (perimeter restart, spot fire, wind shift, resource text) and
 * server storage come in later PRs. Pure functions, tested in injects.test.ts.
 */

import type { Inject, InjectLog, InjectPayload } from "../types/exercise";

export const EMPTY_INJECT_LOG: InjectLog = { injects: [] };

export interface NewInject {
  exerciseTimeMs: number;
  title: string;
  payload: InjectPayload;
}

function newId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `inj-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/** Adds a scheduled inject; the log stays sorted by exercise time (ties keep creation order). */
export function addInject(log: InjectLog, n: NewInject, nowIso: string = new Date().toISOString()): InjectLog {
  const inject: Inject = {
    id: newId(),
    kind: n.payload.kind,
    exerciseTimeMs: n.exerciseTimeMs,
    createdAtIso: nowIso,
    title: n.title.trim() || defaultTitle(n.payload),
    payload: n.payload,
    status: "scheduled",
    deliveredAtMs: null,
  };
  const injects = [...log.injects, inject].sort((a, b) => a.exerciseTimeMs - b.exerciseTimeMs);
  return { injects };
}

export function withdrawInject(log: InjectLog, id: string): InjectLog {
  let changed = false;
  const injects = log.injects.map((i) => {
    if (i.id !== id || i.status !== "scheduled") return i;
    changed = true;
    return { ...i, status: "withdrawn" as const };
  });
  return changed ? { injects } : log;
}

/** Scheduled injects whose exercise time has come (at or before `exerciseMs`), in order. */
export function dueInjects(log: InjectLog, exerciseMs: number): Inject[] {
  return log.injects.filter((i) => i.status === "scheduled" && i.exerciseTimeMs <= exerciseMs);
}

export function markDelivered(log: InjectLog, id: string, exerciseMs: number): InjectLog {
  let changed = false;
  const injects = log.injects.map((i) => {
    if (i.id !== id || i.status !== "scheduled") return i;
    changed = true;
    return { ...i, status: "delivered" as const, deliveredAtMs: exerciseMs };
  });
  return changed ? { injects } : log;
}

function defaultTitle(p: InjectPayload): string {
  switch (p.kind) {
    case "perimeter": return "Observed perimeter";
    case "spot_fire": return "Spot fire reported";
    case "wind_shift": return `Wind ${Math.round(p.windSpeedKmh)} km/h from ${Math.round(p.windDirectionDeg)}°`;
    case "resource": return "Resource availability";
    case "note": return "Controller note";
  }
}
