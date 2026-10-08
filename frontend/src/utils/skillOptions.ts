/**
 * Spread-skill run options (docs/validation.md, "Skill improvements and held-out results"):
 * the daily burning period and the evening FFMC spin-up.
 *
 * Clock hours follow the API: they are on the clock of the run's start_time, i.e. its
 * America/Edmonton offset at the start (Alberta is on UTC-6 year-round from November 2026).
 */
import type { BurningPeriod, HourlyWeatherParams } from "../types/simulation";
import { edmontonOffsetMinutes, type OffsetFn } from "./time";

/** The burning period chosen on calibration fires and tested on held-out Alberta fires. */
export const DEFAULT_BURNING_PERIOD: BurningPeriod = { start_hour: 10, end_hour: 20 };

/** The hourly FFMC spin-up starts at this local hour (daily FFMC ~ 16:00 LST; Lawson et al. 1996). */
export const SPINUP_FROM_HOUR = 17;

/** Where the evidence is written up. */
export const VALIDATION_DOC_URL =
  "https://github.com/Tphambolio/wildfire-simulator/blob/master/docs/validation.md#skill-improvements-and-held-out-results-second-round-2026-10-07";

/** "10:00" for 10, "20:30" for 20.5. */
export function formatHour(h: number): string {
  const total = Math.round(h * 60);
  const hh = Math.floor(total / 60);
  const mm = total % 60;
  return `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
}

/** "10:00–20:00" */
export function formatBurningPeriod(bp: BurningPeriod): string {
  return `${formatHour(bp.start_hour)}–${formatHour(bp.end_hour)}`;
}

/**
 * Validate burning-period hours as typed: whole hours 0-24, start before end (the API's rule).
 * Returns an error message, or null when valid.
 */
export function validateBurningHours(start: number, end: number): string | null {
  for (const [name, v] of [["Start", start], ["End", end]] as const) {
    if (!Number.isFinite(v) || !Number.isInteger(v)) return `${name} hour must be a whole hour`;
    if (v < 0 || v > 24) return `${name} hour must be 0–24`;
  }
  if (start >= end) return "Start hour must be before the end hour";
  return null;
}

/** Local clock hour of an instant (13.5 for 13:30), on the clock of its own Edmonton offset. */
export function localHour(ms: number, offsetAt: OffsetFn = edmontonOffsetMinutes): number {
  const local = new Date(Math.floor(ms / 1000) * 1000 + offsetAt(ms) * 60_000);
  return local.getUTCHours() + local.getUTCMinutes() / 60 + local.getUTCSeconds() / 3600;
}

/**
 * The instant the FFMC spin-up starts for a run starting at `startMs`: the most recent
 * 17:00 at or before the start, on the start's clock (06:00 → 17:00 the day before).
 */
export function spinupFromMs(startMs: number, offsetAt: OffsetFn = edmontonOffsetMinutes): number {
  const back = (((localHour(startMs, offsetAt) - SPINUP_FROM_HOUR) % 24) + 24) % 24;
  return startMs - Math.round(back * 3600) * 1000;
}

/** True if `hoursFromStart` falls inside the burning period ([start, end) each day). */
export function inBurningPeriod(
  startMs: number,
  hoursFromStart: number,
  bp: BurningPeriod,
  offsetAt: OffsetFn = edmontonOffsetMinutes,
): boolean {
  const clock = localHour(startMs, offsetAt) + hoursFromStart;
  const rel = (((clock - bp.start_hour) % 24) + 24) % 24;
  return rel < bp.end_hour - bp.start_hour - 1e-9;
}

/**
 * Spans of the run outside the burning period, as [from, to] hours after the start, clipped
 * to [0, durationHours] (for shading the timeline).
 */
export function offPeriods(
  startMs: number,
  durationHours: number,
  bp: BurningPeriod,
  offsetAt: OffsetFn = edmontonOffsetMinutes,
): Array<[number, number]> {
  if (bp.end_hour - bp.start_hour >= 24 || durationHours <= 0) return [];
  const h0 = localHour(startMs, offsetAt);
  const edges = new Set<number>([0, durationHours]);
  for (let day = -1; day <= Math.ceil(durationHours / 24) + 1; day++) {
    for (const h of [bp.start_hour, bp.end_hour]) {
      const t = h - h0 + 24 * day;
      if (t > 0 && t < durationHours) edges.add(t);
    }
  }
  const pts = [...edges].sort((a, b) => a - b);
  const out: Array<[number, number]> = [];
  for (let i = 0; i + 1 < pts.length; i++) {
    const mid = (pts[i] + pts[i + 1]) / 2;
    if (inBurningPeriod(startMs, mid, bp, offsetAt)) continue;
    const last = out[out.length - 1];
    if (last && Math.abs(last[1] - pts[i]) < 1e-9) last[1] = pts[i + 1];
    else out.push([pts[i], pts[i + 1]]);
  }
  return out;
}

/** True if an hourly stream reaches back to the spin-up start (records with negative hours). */
export function coversSpinup(records: HourlyWeatherParams[] | null, startMs: number, fromMs: number): boolean {
  if (!records?.length) return false;
  const need = (fromMs - startMs) / 3_600_000;
  const first = Math.min(...records.map((r) => r.hours_from_start));
  const last = Math.max(...records.map((r) => r.hours_from_start));
  return first <= need + 0.05 && last + 1 > 0;
}

export interface SkillRunOptions {
  burningOn: boolean;
  startHour: number;
  endHour: number;
  spinUpOn: boolean;
}

/**
 * The request fields for the skill options: `burning_period` when on and valid, and
 * `ffmc_spin_up` only when the hourly stream covers the spin-up hours (else false, with the
 * reason). The API defaults both to off.
 */
export function skillRequestFields(
  opts: SkillRunOptions,
  hourly: HourlyWeatherParams[] | null,
  startMs: number,
): { burning_period: BurningPeriod | null; ffmc_spin_up: boolean; spinUpSkipped: string | null } {
  const bpValid = opts.burningOn && validateBurningHours(opts.startHour, opts.endHour) === null;
  let ffmc_spin_up = false;
  let spinUpSkipped: string | null = null;
  if (opts.spinUpOn) {
    if (!hourly?.length) spinUpSkipped = "needs hourly forecast weather";
    else if (!coversSpinup(hourly, startMs, spinupFromMs(startMs)))
      spinUpSkipped = "the forecast does not reach back to 17:00 before the start";
    else ffmc_spin_up = true;
  }
  return {
    burning_period: bpValid ? { start_hour: opts.startHour, end_hour: opts.endHour } : null,
    ffmc_spin_up,
    spinUpSkipped,
  };
}
