/**
 * Shared time model (design spec §2.3). All displayed times are wall-clock times in
 * America/Edmonton. Conversions go through Intl.DateTimeFormat with that time zone, never a
 * hard-coded UTC offset, so clock changes come from the platform's time-zone data: the
 * 2026-03-08 spring-forward, and on 2026-11-01 either the usual fall-back (tzdata before
 * 2026c) or no change, since Alberta stays on UTC-6 year-round from then (tzdata 2026c+).
 */

export const TIME_ZONE = "America/Edmonton";

/** 2026-11-01 02:00 MDT: Alberta's last clock change; UTC-6 all year after (tzdata 2026c). */
const ALBERTA_FIXED_UTC6_FROM = Date.UTC(2026, 10, 1, 8, 0);

const clockFmt = new Intl.DateTimeFormat("en-CA", {
  timeZone: TIME_ZONE,
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

const zoneFmt = new Intl.DateTimeFormat("en-CA", {
  timeZone: TIME_ZONE,
  timeZoneName: "short",
});

const dateFmt = new Intl.DateTimeFormat("en-CA", {
  timeZone: TIME_ZONE,
  weekday: "short",
  day: "numeric",
  month: "short",
});

/** "14:05" — 24 h wall-clock time in America/Edmonton. */
export function formatClock(d: Date): string {
  const parts = clockFmt.formatToParts(d);
  const hh = parts.find((p) => p.type === "hour")?.value ?? "00";
  const mm = parts.find((p) => p.type === "minute")?.value ?? "00";
  return `${hh === "24" ? "00" : hh}:${mm}`;
}

/** Short zone name for the instant, e.g. "MDT", "MST" (or "CST" after Alberta's Nov 2026 change). */
export function zoneAbbrev(d: Date): string {
  const name = zoneFmt.formatToParts(d).find((p) => p.type === "timeZoneName")?.value ?? "";
  // Some ICU builds give "GMT-7"/"GMT-6" instead of an abbreviation. Before November 2026
  // GMT-6 is MDT; from 2026-11-01 Alberta stays on UTC-6 all year (IANA tzdata 2026c names
  // it CST), so the offset is shown as-is there rather than guessed.
  if (name === "GMT-7") return "MST";
  if (name === "GMT-6" && d.getTime() < ALBERTA_FIXED_UTC6_FROM) return "MDT";
  return name;
}

/** "Tue, 28 Apr" style date in America/Edmonton. */
export function formatDate(d: Date): string {
  return dateFmt.format(d);
}

/** The instant `hours` after the scenario start. */
export function clockAt(start: Date, hours: number): Date {
  return new Date(start.getTime() + hours * 3_600_000);
}

/** "14:05" wall-clock time `hours` after the scenario start, in America/Edmonton. */
export function formatClockAt(start: Date, hours: number): string {
  return formatClock(clockAt(start, hours));
}

/** Elapsed time as "T+1:05" (hours:minutes, rounded to the minute; hours may exceed 24). */
export function formatElapsed(hours: number): string {
  const totalMin = Math.max(0, Math.round(hours * 60));
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  return `T+${h}:${String(m).padStart(2, "0")}`;
}

// ── Scenario start entry (design spec §2.3, §4.1) ────────────────────────────

/** Minutes east of UTC for an instant (e.g. -360 for UTC-6). */
export type OffsetFn = (ms: number) => number;

const partsFmt = new Intl.DateTimeFormat("en-CA", {
  timeZone: TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

/** Wall-clock fields in America/Edmonton for an instant. */
function wallParts(ms: number): { y: number; mo: number; d: number; h: number; mi: number; s: number } {
  const p: Record<string, number> = {};
  for (const x of partsFmt.formatToParts(new Date(ms))) {
    if (x.type !== "literal") p[x.type] = Number(x.value);
  }
  return { y: p.year, mo: p.month, d: p.day, h: p.hour === 24 ? 0 : p.hour, mi: p.minute, s: p.second };
}

/** UTC offset of America/Edmonton at `ms`, in minutes, from the platform's tz data (Intl). */
export const edmontonOffsetMinutes: OffsetFn = (ms) => {
  const w = wallParts(ms);
  const asUtc = Date.UTC(w.y, w.mo - 1, w.d, w.h, w.mi, w.s);
  return Math.round((asUtc - Math.floor(ms / 1000) * 1000) / 60_000);
};

/**
 * The instant at wall-clock `date` ("YYYY-MM-DD") `time` ("HH:MM") in America/Edmonton, or
 * null if malformed. A repeated time (fall-back) resolves to its first occurrence; a time
 * skipped by a spring-forward resolves to the instant an hour later on the wall clock.
 * `offsetAt` defaults to the platform tz data (injectable for tests of other tz rules).
 */
export function zonedWallTimeToMs(date: string, time: string, offsetAt: OffsetFn = edmontonOffsetMinutes): number | null {
  const dm = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  const tm = /^(\d{2}):(\d{2})$/.exec(time);
  if (!dm || !tm) return null;
  const [y, mo, d, h, mi] = [+dm[1], +dm[2], +dm[3], +tm[1], +tm[2]];
  if (mo < 1 || mo > 12 || d < 1 || h > 23 || mi > 59) return null;
  const wall = Date.UTC(y, mo - 1, d, h, mi);
  if (new Date(wall).getUTCDate() !== d) return null; // e.g. 31 April
  // The offsets in force half a day either side; the earliest consistent instant wins
  const candidates = [offsetAt(wall - 12 * 3_600_000), offsetAt(wall + 12 * 3_600_000)];
  const consistent = candidates
    .map((o) => wall - o * 60_000)
    .filter((t) => offsetAt(t) === (wall - t) / 60_000);
  if (consistent.length) return Math.min(...consistent);
  // In a spring-forward gap: apply the offset from before the change (lands after the gap)
  return wall - candidates[0] * 60_000;
}

const pad2 = (n: number) => String(Math.abs(n)).padStart(2, "0");

/**
 * ISO 8601 with the America/Edmonton offset in force at that instant, to the second, e.g.
 * "2026-07-15T14:05:00-06:00" (the API's `start_time`). The offset comes from the tz data,
 * never a hard-coded -06/-07.
 */
export function toEdmontonIso(ms: number, offsetAt: OffsetFn = edmontonOffsetMinutes): string {
  const off = offsetAt(ms);
  const local = new Date(Math.floor(ms / 1000) * 1000 + off * 60_000);
  const sign = off < 0 ? "-" : "+";
  return (
    `${local.getUTCFullYear()}-${pad2(local.getUTCMonth() + 1)}-${pad2(local.getUTCDate())}` +
    `T${pad2(local.getUTCHours())}:${pad2(local.getUTCMinutes())}:${pad2(local.getUTCSeconds())}` +
    `${sign}${pad2(Math.trunc(off / 60))}:${pad2(off % 60)}`
  );
}

/** {date: "YYYY-MM-DD", time: "HH:MM"}: the wall clock in America/Edmonton, for date/time inputs. */
export function toDateTimeInputs(ms: number, offsetAt: OffsetFn = edmontonOffsetMinutes): { date: string; time: string } {
  const iso = toEdmontonIso(ms, offsetAt);
  return { date: iso.slice(0, 10), time: iso.slice(11, 16) };
}

/** The instant rounded to the nearest minute. */
export function roundToMinute(ms: number = Date.now()): number {
  return Math.round(ms / 60_000) * 60_000;
}

/** Day of year (1-366) of the wall-clock date in America/Edmonton (for foliar moisture). */
export function edmontonDayOfYear(ms: number): number {
  const w = wallParts(ms);
  return Math.round((Date.UTC(w.y, w.mo - 1, w.d) - Date.UTC(w.y, 0, 0)) / 86_400_000);
}
