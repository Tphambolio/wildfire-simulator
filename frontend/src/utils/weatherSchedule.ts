/**
 * Manual weather schedule (Setup → Weather & FWI): step changes of wind, temperature, RH and
 * rain at set clock times during one run, e.g. 47 km/h from the start, then 20 km/h from
 * 20:00. Sent as the API's `weather_schedule` (hours after the run's start_time); the API
 * turns it into an hourly stream, so FFMC (and ISI) are recomputed hourly from the starting
 * codes, as for forecast weather (docs/api-reference.md).
 */
import type { WeatherChange, WeatherParams } from "../types/simulation";
import type { RunParams } from "../components/WeatherPanel";
import { formatClock, toDateTimeInputs, zonedWallTimeToMs } from "./time";

/** A row of the Setup table: this weather from `time` (local HH:MM) until the next row. */
export interface ScheduleRow {
  /** "HH:MM" on the America/Edmonton clock: the first such time after the run's start */
  time: string;
  wind_speed: number;
  wind_direction: number;
  temperature: number;
  relative_humidity: number;
  /** Rain rate while the row is in force (mm/h) */
  precipitation_mm_h: number;
}

/** A step with its absolute time (ms), e.g. for a restart at another time. */
export interface TimedStep extends Omit<ScheduleRow, "time"> {
  atMs: number;
}

const H = 3_600_000;

/** New row: the base weather (or the last row's), one hour after the last row (or two after the start). */
export function newRow(base: WeatherParams, rows: ScheduleRow[], startMs: number | null): ScheduleRow {
  const lastMs = rows.length && startMs !== null ? rowTimeMs(rows[rows.length - 1].time, startMs) : null;
  const at = lastMs !== null ? lastMs + H : startMs !== null ? startMs + 2 * H : null;
  const time = at !== null ? toDateTimeInputs(at).time : "20:00";
  const src = rows.length ? rows[rows.length - 1] : base;
  return {
    time,
    wind_speed: src.wind_speed,
    wind_direction: src.wind_direction,
    temperature: src.temperature,
    relative_humidity: src.relative_humidity,
    precipitation_mm_h: 0,
  };
}

/** The first time at or after `startMs` the local clock shows `time` (null if unparseable). */
export function rowTimeMs(time: string, startMs: number): number | null {
  if (!/^\d{1,2}:\d{2}$/.test(time)) return null;
  const day = toDateTimeInputs(startMs).date;
  const ms = zonedWallTimeToMs(day, time);
  if (ms === null) return null;
  return ms >= startMs ? ms : ms + 24 * H; // earlier clock time = the next day (DST ignored)
}

/** Rows as absolute steps, in time order (rows with a bad time are left out). */
export function timedSteps(rows: ScheduleRow[], startMs: number): TimedStep[] {
  const out: TimedStep[] = [];
  for (const r of rows) {
    const atMs = rowTimeMs(r.time, startMs);
    if (atMs === null) continue;
    const { time: _t, ...w } = r;
    void _t;
    out.push({ ...w, atMs });
  }
  return out.sort((a, b) => a.atMs - b.atMs);
}

/** Problems with the table for a run from `startMs` of `durationHours` (empty = fine). */
export function scheduleErrors(rows: ScheduleRow[], startMs: number | null, durationHours: number): string[] {
  const errs: string[] = [];
  if (rows.length === 0 || startMs === null) return errs;
  const seen = new Set<number>();
  for (const r of rows) {
    const at = rowTimeMs(r.time, startMs);
    if (at === null) {
      errs.push(`Weather change time "${r.time}" is not a time`);
      continue;
    }
    const h = (at - startMs) / H;
    if (h <= 0) errs.push(`Weather change at ${r.time} is at the start: set the start weather above`);
    else if (h >= durationHours) errs.push(`Weather change at ${r.time} is after the run ends`);
    if (seen.has(at)) errs.push(`Two weather changes at ${r.time}`);
    seen.add(at);
    if (r.wind_speed < 0 || r.wind_speed > 100) errs.push(`Wind at ${r.time} must be 0–100 km/h`);
    if (r.wind_direction < 0 || r.wind_direction >= 360) errs.push(`Direction at ${r.time} must be 0–359°`);
    if (r.relative_humidity < 1 || r.relative_humidity > 100) errs.push(`RH at ${r.time} must be 1–100 %`);
    if (r.temperature < -40 || r.temperature > 50) errs.push(`Temperature at ${r.time} must be −40 to 50 °C`);
    if (r.precipitation_mm_h < 0 || r.precipitation_mm_h > 50) errs.push(`Rain at ${r.time} must be 0–50 mm/h`);
  }
  return errs;
}

function change(s: TimedStep, fromMs: number): WeatherChange {
  return {
    hours_from_start: Math.round(((s.atMs - fromMs) / H) * 1e4) / 1e4,
    wind_speed: s.wind_speed,
    wind_direction: s.wind_direction,
    temperature: s.temperature,
    relative_humidity: s.relative_humidity,
    precipitation_mm_h: s.precipitation_mm_h,
  };
}

/** API `weather_schedule` for a run from `startMs` (null when the table is empty). */
export function scheduleRequest(rows: ScheduleRow[], startMs: number): WeatherChange[] | null {
  const steps = timedSteps(rows, startMs).filter((s) => s.atMs > startMs);
  return steps.length ? steps.map((s) => change(s, startMs)) : null;
}

/**
 * Weather and schedule for a restart at `restartMs` (a perimeter restart from the timeline):
 * the step in force then becomes the restart's weather, the later steps keep their clock
 * times. `steps` are absolute (`timedSteps` of Setup's table against Setup's start).
 */
export function scheduleForRestart(
  base: WeatherParams,
  steps: TimedStep[],
  restartMs: number,
  durationHours: number,
): { weather: WeatherParams; schedule: WeatherChange[] | null } {
  let weather = base;
  const later: WeatherChange[] = [];
  for (const s of [...steps].sort((a, b) => a.atMs - b.atMs)) {
    if (s.atMs <= restartMs) {
      weather = {
        wind_speed: s.wind_speed,
        wind_direction: s.wind_direction,
        temperature: s.temperature,
        relative_humidity: s.relative_humidity,
        precipitation_24h: base.precipitation_24h,
      };
    } else if ((s.atMs - restartMs) / H < durationHours) {
      later.push(change(s, restartMs));
    }
  }
  return { weather, schedule: later.length ? later : null };
}

/** Weather in force `hours` after the start of a run with `schedule`. */
export function weatherAt(base: WeatherParams, schedule: WeatherChange[] | null | undefined, hours: number): WeatherParams {
  let w = base;
  for (const c of [...(schedule ?? [])].sort((a, b) => a.hours_from_start - b.hours_from_start)) {
    if (c.hours_from_start <= hours + 1e-9) {
      w = { ...base, wind_speed: c.wind_speed, wind_direction: c.wind_direction, temperature: c.temperature, relative_humidity: c.relative_humidity };
    }
  }
  return w;
}

/**
 * One line per step for reports and exports: "17:50 47 km/h from 135°, 20 °C, RH 15 %" (clock
 * times with a start, else "T+2:10"). The first line is the start weather.
 */
export function scheduleLines(base: WeatherParams, schedule: WeatherChange[] | null | undefined, start: Date | null): string[] {
  if (!schedule?.length) return [];
  const when = (h: number) => {
    if (start) return formatClock(new Date(start.getTime() + h * H));
    const m = Math.round(h * 60);
    return `T+${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`;
  };
  const line = (h: number, w: { wind_speed: number; wind_direction: number; temperature: number; relative_humidity: number }, rain?: number) =>
    `${when(h)} ${w.wind_speed} km/h from ${w.wind_direction}°, ${w.temperature} °C, RH ${w.relative_humidity} %${rain ? `, rain ${rain} mm/h` : ""}`;
  return [
    line(0, base),
    ...[...schedule].sort((a, b) => a.hours_from_start - b.hours_from_start)
      .map((c) => line(c.hours_from_start, c, c.precipitation_mm_h)),
  ];
}

/** The run's weather changes as text lines (empty without any), with clock times. */
export function runScheduleLines(p: RunParams | null | undefined): string[] {
  if (!p?.weather_schedule?.length) return [];
  const at = p.start_time ? Date.parse(p.start_time) : NaN;
  return scheduleLines(p.weather, p.weather_schedule, Number.isFinite(at) ? new Date(at) : null);
}
