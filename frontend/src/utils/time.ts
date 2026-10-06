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
