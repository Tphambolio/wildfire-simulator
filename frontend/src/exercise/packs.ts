/**
 * Scenario packs (docs/exercise-mode-spec.md §5, PR2): read a pack file and turn it into what
 * the app sets when the facilitator loads it. Pure functions, tested in packs.test.ts.
 *
 * Loading a pack sets Setup (ignition, start, starting codes, fuel source, weather and its
 * script), opens an incident with the pack's operational periods, sets the exercise clock to
 * the pack's span (start to start + duration, paused) with the period boundaries, and puts the
 * pack's injects in the inject log as scheduled (delivery is PR3). It never starts the run
 * unless the pack says `autoRun: true`. No live weather is used: the hourly forecast is off.
 *
 * Engine limits, stated rather than hidden (`PackLoadPlan.notes`): a single run is at most
 * 24 h, so a longer pack runs as a multi-day run, which holds one weather per 24 h day (the
 * step in force at each day's start); a step change inside a later day reaches the model at
 * the next day boundary (or with a restart, PR3).
 */

import type { ScenarioConfig } from "../types/simulation";
import type { Inject, InjectLog, ScenarioPack, WeatherScriptStep } from "../types/exercise";
import { PACK_MAX_BYTES, PACK_MAX_DURATION_HOURS, PACK_SCHEMA } from "../types/exercise";
import type { ScheduleRow } from "../utils/weatherSchedule";
import { toDateTimeInputs, toEdmontonIso } from "../utils/time";

const H = 3_600_000;
/** Longest single run the API accepts (SimulationCreate.duration_hours ≤ 24). */
export const SINGLE_RUN_MAX_HOURS = 24;

export type ParsedPack = { pack: ScenarioPack; error?: undefined } | { pack?: undefined; error: string };

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/**
 * A pack from a file's text: JSON, the schema version, the EXERCISE marking and the shape the
 * app needs. The server validates everything again on upload (schemas/exercise.py).
 */
export function parsePack(text: string): ParsedPack {
  if (new Blob([text]).size > PACK_MAX_BYTES) return { error: "The pack is larger than 2 MB" };
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return { error: "Not a JSON file" };
  }
  return checkPack(raw);
}

export function checkPack(raw: unknown): ParsedPack {
  if (!isObj(raw)) return { error: "Not a scenario pack" };
  if (raw.schema !== PACK_SCHEMA) return { error: `Not a scenario pack (schema must be ${PACK_SCHEMA})` };
  if (raw.marking !== "EXERCISE") return { error: "A pack must be marked EXERCISE" };
  if (typeof raw.title !== "string" || !raw.title.trim()) return { error: "The pack has no title" };
  if (typeof raw.startIso !== "string" || !Number.isFinite(Date.parse(raw.startIso)) || !/(Z|[+-]\d{2}:\d{2})$/.test(raw.startIso)) {
    return { error: "startIso must be an ISO time with an offset" };
  }
  if (!isNum(raw.durationHours) || raw.durationHours <= 0 || raw.durationHours > PACK_MAX_DURATION_HOURS) {
    return { error: `durationHours must be more than 0 and at most ${PACK_MAX_DURATION_HOURS}` };
  }
  const ign = raw.ignitions;
  if (!Array.isArray(ign) || !ign.some((i) => isObj(i) && isNum(i.lat) && isNum(i.lng) && i.hoursFromStart === 0)) {
    return { error: "The pack needs an ignition at hoursFromStart 0" };
  }
  const ws = raw.weatherScript;
  if (!Array.isArray(ws) || !ws.length || !isObj(ws[0]) || ws[0].hoursFromStart !== 0) {
    return { error: "weatherScript must start with a step at hoursFromStart 0" };
  }
  if (!isObj(raw.startingCodes) || !isObj(raw.fuel)) return { error: "The pack needs startingCodes and fuel" };
  if (!Array.isArray(raw.periods) || !raw.periods.length) return { error: "The pack needs at least one operational period" };
  if (!Array.isArray(raw.sources) || !raw.sources.length) return { error: "The pack must name its open-data sources" };
  if (!Array.isArray(raw.scheduledInjects)) return { error: "scheduledInjects must be a list" };
  return { pack: raw as unknown as ScenarioPack };
}

/** The script step in force `hours` after the start. */
export function stepAt(script: WeatherScriptStep[], hours: number): WeatherScriptStep {
  let s = script[0];
  for (const x of script) if (x.hoursFromStart <= hours + 1e-9) s = x;
  return s;
}

/** Rain (mm) the script puts down between two times (step rates × hours). */
export function rainBetween(script: WeatherScriptStep[], fromH: number, toH: number): number {
  let mm = 0;
  for (let i = 0; i < script.length; i++) {
    const a = Math.max(script[i].hoursFromStart, fromH);
    const b = Math.min(i + 1 < script.length ? script[i + 1].hoursFromStart : Infinity, toH);
    if (b > a && i > 0) mm += (script[i].precipitationMmH ?? 0) * (b - a);
  }
  return Math.round(mm * 10) / 10;
}

export interface PackClock {
  startMs: number;
  endMs: number;
  /** Operational-period starts and ends inside the span, ascending */
  boundaries: number[];
}

/** The exercise clock's span (start to start + duration) and period boundaries. */
export function packClock(pack: ScenarioPack): PackClock {
  const startMs = Date.parse(pack.startIso);
  const endMs = startMs + pack.durationHours * H;
  const b = new Set<number>();
  for (const p of pack.periods) {
    for (const t of [Date.parse(p.startIso), Date.parse(p.endIso)]) if (Number.isFinite(t) && t > startMs && t < endMs) b.add(t);
  }
  return { startMs, endMs, boundaries: [...b].sort((x, y) => x - y) };
}

export interface PackPeriod {
  day: number;
  /** Start date on the Edmonton clock (YYYY-MM-DD) */
  date: string;
  /** "HH:MM" on the Edmonton clock; an end at or before the start is the next day */
  opPeriodStart: string;
  opPeriodEnd: string;
}

/** The incident's operational periods (Edmonton wall times, as the EOC console keeps them). */
export function packIncidentPeriods(pack: ScenarioPack): PackPeriod[] {
  return pack.periods.map((p) => {
    const s = toDateTimeInputs(Date.parse(p.startIso));
    const e = toDateTimeInputs(Date.parse(p.endIso));
    return { day: p.number, date: s.date, opPeriodStart: s.time, opPeriodEnd: e.time };
  });
}

/** The pack's injects (and ignitions after 0 h, as spot fires) as a scheduled inject log. */
export function packInjectLog(pack: ScenarioPack, nowIso: string = new Date().toISOString()): InjectLog {
  const startMs = Date.parse(pack.startIso);
  const injects: Inject[] = pack.scheduledInjects.map((j) => {
    const exerciseTimeMs = startMs + j.hoursFromStart * H;
    const payload = j.payload.kind === "perimeter"
      ? { ...j.payload, observedAtIso: j.payload.observedAtIso ?? toEdmontonIso(exerciseTimeMs) }
      : j.payload;
    return {
      id: j.id, kind: j.kind, exerciseTimeMs, createdAtIso: nowIso, title: j.title,
      payload: payload as Inject["payload"], status: "scheduled", deliveredAtMs: null,
    };
  });
  pack.ignitions.forEach((ig, k) => {
    if (ig.hoursFromStart <= 0) return;
    injects.push({
      id: `ignition-${k + 1}`, kind: "spot_fire", exerciseTimeMs: startMs + ig.hoursFromStart * H, createdAtIso: nowIso,
      title: "Spot fire reported", payload: { kind: "spot_fire", lat: ig.lat, lng: ig.lng }, status: "scheduled", deliveredAtMs: null,
    });
  });
  injects.sort((a, b) => a.exerciseTimeMs - b.exerciseTimeMs);
  return { injects };
}

export interface PackLoadPlan {
  /** Setup as a scenario to load (WeatherPanel's scenarioToLoad) */
  config: ScenarioConfig;
  ignition: { lat: number; lng: number };
  clock: PackClock;
  periods: PackPeriod[];
  injectLog: InjectLog;
  /** Approximations the app had to make (shown on the panel) */
  notes: string[];
}

/** Everything loading the pack sets. `seq` makes the Setup load unique (re-loading reapplies it). */
export function packLoadPlan(pack: ScenarioPack, seq = 0, nowIso: string = new Date().toISOString()): PackLoadPlan {
  const startMs = Date.parse(pack.startIso);
  const script = pack.weatherScript;
  const s0 = script[0];
  const first = pack.ignitions.find((i) => i.hoursFromStart === 0) ?? pack.ignitions[0];
  const single = pack.durationHours <= SINGLE_RUN_MAX_HOURS;
  const notes: string[] = [];
  const weatherSchedule: ScheduleRow[] = single
    ? script.slice(1).map((s) => ({
        time: toDateTimeInputs(startMs + Math.round(s.hoursFromStart * 60) * 60_000).time,
        wind_speed: s.windSpeedKmh,
        wind_direction: s.windDirectionDeg,
        temperature: s.temperatureC,
        relative_humidity: s.relativeHumidityPct,
        precipitation_mm_h: s.precipitationMmH ?? 0,
      }))
    : [];
  const nDays = Math.ceil(pack.durationHours / 24 - 1e-9);
  const multiDayDays = single
    ? []
    : Array.from({ length: nDays }, (_, d) => {
        const s = stepAt(script, d * 24);
        return {
          wind_speed: s.windSpeedKmh,
          wind_direction: s.windDirectionDeg,
          temperature: s.temperatureC,
          relative_humidity: s.relativeHumidityPct,
          precipitation_24h: d === 0 ? s0.precipitation24hMm : rainBetween(script, (d - 1) * 24, d * 24),
        };
      });
  if (!single) {
    notes.push(`Runs as a ${nDays}-day run (${nDays * 24} h; a single run is at most ${SINGLE_RUN_MAX_HOURS} h); the clock stops at ${pack.durationHours} h.`);
    const late = script.filter((s) => s.hoursFromStart > 0 && Math.abs(s.hoursFromStart / 24 - Math.round(s.hoursFromStart / 24)) > 1e-9);
    for (const s of late) {
      const nextDay = Math.ceil(s.hoursFromStart / 24) * 24;
      notes.push(`The weather change at T+${fmtH(s.hoursFromStart)} reaches the model at T+${fmtH(nextDay)} (multi-day runs hold one weather per day).`);
    }
  }
  if (pack.ignitions.some((i) => i.hoursFromStart > 0)) notes.push("Later ignitions are scheduled as spot-fire injects.");
  const config: ScenarioConfig = {
    id: `exercise-pack:${pack.id}:${seq}`,
    name: pack.title,
    description: pack.summary ?? undefined,
    createdAt: nowIso,
    ignitionPoint: { lat: first.lat, lng: first.lng },
    weather: {
      wind_speed: s0.windSpeedKmh,
      wind_direction: s0.windDirectionDeg,
      temperature: s0.temperatureC,
      relative_humidity: s0.relativeHumidityPct,
      precipitation_24h: s0.precipitation24hMm,
    },
    fwi: { ffmc: pack.startingCodes.ffmc, dmc: pack.startingCodes.dmc, dc: pack.startingCodes.dc },
    fuelType: pack.fuel.source === "uniform" && pack.fuel.uniformFuelType ? pack.fuel.uniformFuelType : "C2",
    // The grid option: City LiDAR where mapped, then the Alberta grid (data/provincial.py)
    useEdmontonGrid: pack.fuel.source !== "uniform",
    useSyntheticCA: false,
    enableSpotting: false,
    spottingIntensity: 1.0,
    includeWater: false,
    includeBuildings: true,
    includeWUI: false,
    includeDEM: true,
    durationHours: single ? pack.durationHours : 24,
    snapshotMinutes: 30,
    simMode: single ? "single" : "multiday",
    multiDayDays,
    mcIterations: 50,
    ensembleMembers: null,
    ensembleOn: false,
    weatherSchedule,
    startIso: pack.startIso,
    grassCure: pack.grassCuringPct ?? null,
    // An exercise never mixes live forecast weather with a scripted fire (spec §1)
    useHourlyForecast: false,
  };
  return {
    config,
    ignition: { lat: first.lat, lng: first.lng },
    clock: packClock(pack),
    periods: packIncidentPeriods(pack),
    injectLog: packInjectLog(pack, nowIso),
    notes,
  };
}

function fmtH(h: number): string {
  const m = Math.round(h * 60);
  return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`;
}

/** Communities renamed for the exercise (a fictional name on real geography). */
export function applyPlaceAliases(
  fc: GeoJSON.FeatureCollection | null,
  aliases: ReadonlyArray<{ place: string; shownAs: string }> | null | undefined,
): GeoJSON.FeatureCollection | null {
  if (!fc || !aliases?.length) return fc;
  const map = new Map(aliases.map((a) => [a.place, a.shownAs]));
  let changed = false;
  const features = fc.features.map((f) => {
    const p = (f.properties ?? {}) as Record<string, unknown>;
    const shown = typeof p.place === "string" ? map.get(p.place) : undefined;
    if (!shown) return f;
    changed = true;
    return { ...f, properties: { ...p, name: shown, place: shown, parent: null } };
  });
  return changed ? { ...fc, features } : fc;
}

/** File name for a pack export, always marked EXERCISE. */
export function packFileName(pack: ScenarioPack): string {
  const slug = pack.title.toLowerCase().replace(/^exercise:\s*/, "").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60);
  return `EXERCISE-pack-${slug || "untitled"}.json`;
}
