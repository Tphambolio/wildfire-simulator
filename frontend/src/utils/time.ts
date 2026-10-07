/**
 * Shared time model (design spec §2.3). All displayed times are wall-clock times in
 * America/Edmonton. Conversions go through Intl.DateTimeFormat with that time zone, never a
 * hard-coded UTC offset, so the DST changes (2026-03-08, 2026-11-01) are handled by the
 * platform's time-zone data.
 */

export const TIME_ZONE = "America/Edmonton";

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

/** "MDT" or "MST" for the given instant. */
export function zoneAbbrev(d: Date): string {
  const name = zoneFmt.formatToParts(d).find((p) => p.type === "timeZoneName")?.value ?? "";
  // Some ICU builds give "GMT-6"/"GMT-7" instead of the abbreviation
  if (name === "GMT-6") return "MDT";
  if (name === "GMT-7") return "MST";
  return name;
}

/** "Tue, 28 Apr" style date in America/Edmonton. */
export function formatDate(d: Date): string {
  return dateFmt.format(d);
}
