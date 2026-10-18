/**
 * Shared time model (design spec §2.3). All displayed times are wall-clock times in
 * America/Edmonton.
 *
 * Alberta's clock rule (Official Time Act; in force 2026-06-18; IANA tzdata 2026c): MST (UTC-7)
 * / MDT (UTC-6) with the usual North American DST dates until 2026-11-01 02:00 MDT, then UTC-6
 * all year with no fall-back. Browsers ship their own tz data and many will still fall back to
 * UTC-7 on 2026-11-01 for a while, so the offset is NOT taken from Intl after the change: from
 * 2026-11-01 08:00 UTC it is the fixed UTC-6 of the law (dated rule), and before that it comes
 * from Intl (every tz data version agrees on the historical dates). All formatting goes through
 * that one offset, so labels, clock times and the `start_time` offset always agree.
 *
 * Labels: "MST"/"MDT" for instants before the change (historical replays, e.g. Slave Lake 2011,
 * keep their historical labels); "MT" from 2026-11-01 02:00 MDT on (owner's choice, 2026-10-18:
 * not the IANA abbreviation "CST", which reads as Central time to Albertans). Never the Intl
 * abbreviation, which varies by browser ("MDT", "GMT-6", "CST").
 */

export const TIME_ZONE = "America/Edmonton";

/** 2026-11-01 02:00 MDT (08:00 UTC): Alberta's last clock change; UTC-6 all year after. */
export const ALBERTA_FIXED_UTC6_FROM = Date.UTC(2026, 10, 1, 8, 0);
/** Alberta's offset from ALBERTA_FIXED_UTC6_FROM on, in minutes east of UTC. */
const ALBERTA_FIXED_OFFSET_MIN = -360;
/** Label for Alberta time from ALBERTA_FIXED_UTC6_FROM on (owner's choice). */
export const ALBERTA_TIME_LABEL = "MT";

/** Formats the fields of an already-shifted instant (local wall clock encoded as UTC). */
const clockFmt = new Intl.DateTimeFormat("en-CA", {
  timeZone: "UTC",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

const dateFmt = new Intl.DateTimeFormat("en-CA", {
  timeZone: "UTC",
  weekday: "short",
  day: "numeric",
  month: "short",
});

/** The instant shifted by its Edmonton offset: its UTC fields are the Edmonton wall clock. */
function shifted(d: Date): Date {
  const ms = d.getTime();
  return new Date(ms + edmontonOffsetMinutes(ms) * 60_000);
}

/** "14:05" — 24 h wall-clock time in America/Edmonton. */
export function formatClock(d: Date): string {
  const parts = clockFmt.formatToParts(shifted(d));
  const hh = parts.find((p) => p.type === "hour")?.value ?? "00";
  const mm = parts.find((p) => p.type === "minute")?.value ?? "00";
  return `${hh === "24" ? "00" : hh}:${mm}`;
}

/**
 * Zone label for the instant: "MST" or "MDT" before 2026-11-01 02:00 MDT, "MT" from then on.
 * Derived from the offset (above), not from Intl's abbreviation.
 */
export function zoneAbbrev(d: Date): string {
  const ms = d.getTime();
  if (ms >= ALBERTA_FIXED_UTC6_FROM) return ALBERTA_TIME_LABEL;
  const off = edmontonOffsetMinutes(ms);
  if (off === -420) return "MST";
  if (off === -360) return "MDT";
  const sign = off < 0 ? "-" : "+";
  const a = Math.abs(off);
  return `UTC${sign}${Math.floor(a / 60)}${a % 60 ? `:${String(a % 60).padStart(2, "0")}` : ""}`;
}

/** "Tue, 28 Apr" style date in America/Edmonton. */
export function formatDate(d: Date): string {
  return dateFmt.format(shifted(d));
}

/** The instant `hours` after the scenario start. */
export function clockAt(start: Date, hours: number): Date {
  return new Date(start.getTime() + hours * 3_600_000);
}

/** Calendar day of `d` counted from `start`'s date in America/Edmonton (1 = the start date). */
export function runDay(start: Date, d: Date): number {
  const a = wallParts(start.getTime());
  const b = wallParts(d.getTime());
  return Math.round((Date.UTC(b.y, b.mo - 1, b.d) - Date.UTC(a.y, a.mo - 1, a.d)) / 86_400_000) + 1;
}

/**
 * Wall-clock time `hours` after the scenario start, in America/Edmonton: "14:05" on the start
 * date, "D2 14:05" on a later calendar day (D2 = the day after the start date), and the date
 * for a time before the start date ("Mon, 12 Oct 22:00", e.g. an earlier run of a restart
 * chain). Without the day, a 3-day run's arrival at 16:25 on day 2 read as "16:25", and every
 * row looked like a same-day time (round-3 field test R7). With `multiDay`, day 1 reads "D1 14:05".
 */
export function formatClockAt(start: Date, hours: number, multiDay = false): string {
  const t = clockAt(start, hours);
  const day = runDay(start, t);
  // In a multi-day run day 1 is prefixed too, so "10:00" isn't read beside "D2 12:15" as a
  // time without a day (72 h rerun, 2026-10-17)
  if (day === 1) return multiDay ? `D1 ${formatClock(t)}` : formatClock(t);
  if (day > 1) return `D${day} ${formatClock(t)}`;
  return `${formatDate(t)} ${formatClock(t)}`;
}

/**
 * The span a run models, e.g. "Tue, 13 Oct 04:57 to Fri, 16 Oct 04:57 MDT (72 h)", for the
 * EOC console and the ICS forms beside the operational period (round-3 field test R11).
 */
export function formatRunSpan(start: Date, hours: number): string {
  const end = clockAt(start, hours);
  const h = Math.round(hours * 10) / 10;
  return `${formatDate(start)} ${formatClock(start)} to ${formatDate(end)} ${formatClock(end)} ${zoneAbbrev(end)} (${h} h)`;
}

/** True when frames reach a second operational day (a multi-day run). */
export function isMultiDayRun(frames: ReadonlyArray<{ day?: number | null }>): boolean {
  return frames.some((f) => (f.day ?? 1) > 1);
}

/**
 * Elapsed time for the timeline and the Situation panel: "T+5:30", and in a multi-day run the
 * operational day with the run time, "D3 · T+72:00". The timeline used to show the time within
 * the day ("D3 T+24:00") beside the panel's "T+72:00" (72 h rerun, 2026-10-17).
 */
export function formatRunElapsed(hours: number, day?: number | null): string {
  return day ? `D${day} · ${formatElapsed(hours)}` : formatElapsed(hours);
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

/** Wall-clock fields in America/Edmonton per the platform's tz data (Intl). */
function intlWallParts(ms: number): { y: number; mo: number; d: number; h: number; mi: number; s: number } {
  const p: Record<string, number> = {};
  for (const x of partsFmt.formatToParts(new Date(ms))) {
    if (x.type !== "literal") p[x.type] = Number(x.value);
  }
  return { y: p.year, mo: p.month, d: p.day, h: p.hour === 24 ? 0 : p.hour, mi: p.minute, s: p.second };
}

/** Wall-clock fields in America/Edmonton for an instant (Alberta's rule, see the header). */
function wallParts(ms: number): { y: number; mo: number; d: number; h: number; mi: number; s: number } {
  const l = new Date(Math.floor(ms / 1000) * 1000 + edmontonOffsetMinutes(ms) * 60_000);
  return {
    y: l.getUTCFullYear(), mo: l.getUTCMonth() + 1, d: l.getUTCDate(),
    h: l.getUTCHours(), mi: l.getUTCMinutes(), s: l.getUTCSeconds(),
  };
}

/** Offset of America/Edmonton at `ms` per the platform's tz data (Intl), in minutes. */
export const platformEdmontonOffsetMinutes: OffsetFn = (ms) => {
  const w = intlWallParts(ms);
  const asUtc = Date.UTC(w.y, w.mo - 1, w.d, w.h, w.mi, w.s);
  return Math.round((asUtc - Math.floor(ms / 1000) * 1000) / 60_000);
};

/**
 * UTC offset of America/Edmonton at `ms`, in minutes: UTC-6 from 2026-11-01 08:00 UTC (the
 * law, whatever the browser's tz data says), the platform's tz data before that.
 */
export const edmontonOffsetMinutes: OffsetFn = (ms) =>
  ms >= ALBERTA_FIXED_UTC6_FROM ? ALBERTA_FIXED_OFFSET_MIN : platformEdmontonOffsetMinutes(ms);

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

/**
 * The day after `date` ("YYYY-MM-DD"), for the next operational period; today's Edmonton date
 * when `date` is missing or not a valid date (an incident stored by an older version).
 */
export function nextPeriodDate(date: string | null | undefined, now: number = Date.now()): string {
  const m = typeof date === "string" ? date.match(/^(\d{4})-(\d{2})-(\d{2})$/) : null;
  if (m) {
    const t = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]) + 1);
    if (Number.isFinite(t)) return new Date(t).toISOString().slice(0, 10);
  }
  return toDateTimeInputs(now).date;
}
