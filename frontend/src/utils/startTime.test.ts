/**
 * Scenario start entry (design spec §2.3): wall clock in America/Edmonton <-> instant, and the
 * ISO 8601 `start_time` sent to the API with the offset in force at that instant.
 *
 * Alberta's clocks: with IANA tzdata before 2026c, MDT (UTC-6) / MST (UTC-7) with the usual
 * North American DST dates; from tzdata 2026c Alberta stays on UTC-6 all year after the
 * 2026-11-01 change. The functions take the offset from the platform (Intl), so each rule is
 * tested here with an injected offset function, and the platform is checked against whichever
 * of the two rules its tz data follows.
 */
import { describe, expect, it } from "vitest";
import {
  edmontonDayOfYear,
  edmontonOffsetMinutes,
  roundToMinute,
  toDateTimeInputs,
  toEdmontonIso,
  zonedWallTimeToMs,
  type OffsetFn,
} from "./time";

const H = 3_600_000;

/** nth Sunday of month (0-based month), as a UTC date at 00:00. */
function nthSunday(year: number, month: number, n: number): number {
  const first = new Date(Date.UTC(year, month, 1)).getUTCDay();
  return Date.UTC(year, month, 1 + ((7 - first) % 7) + 7 * (n - 1));
}

/** tzdata < 2026c: DST from the 2nd Sunday of March 02:00 MST to the 1st Sunday of November 02:00 MDT. */
const preRule: OffsetFn = (ms) => {
  const y = new Date(ms).getUTCFullYear();
  const start = nthSunday(y, 2, 2) + 9 * H; // 02:00 MST = 09:00 UTC
  const end = nthSunday(y, 10, 1) + 8 * H; // 02:00 MDT = 08:00 UTC
  return ms >= start && ms < end ? -360 : -420;
};

/** tzdata >= 2026c: as before until 2026-11-01 08:00 UTC, then UTC-6 all year. */
const FIXED_FROM = Date.UTC(2026, 10, 1, 8);
const rule2026c: OffsetFn = (ms) => (ms >= FIXED_FROM ? -360 : preRule(ms));

const platformIs2026c = edmontonOffsetMinutes(Date.parse("2026-12-01T18:00:00Z")) === -360;
const platformRule = platformIs2026c ? rule2026c : preRule;

describe("the platform tz data follows one of the two Alberta rules", () => {
  it(`matches ${platformIs2026c ? "tzdata 2026c (permanent UTC-6)" : "tzdata < 2026c (MST in winter)"} hour by hour, 2025-2028`, () => {
    for (let t = Date.UTC(2025, 0, 1); t < Date.UTC(2028, 0, 1); t += 7 * H) {
      expect(edmontonOffsetMinutes(t), new Date(t).toISOString()).toBe(platformRule(t));
    }
  });
});

describe.each([
  ["tzdata < 2026c", preRule],
  ["tzdata 2026c", rule2026c],
] as const)("start_time ISO offset (%s)", (name, rule) => {
  it("summer: MDT, -06:00", () => {
    expect(toEdmontonIso(Date.parse("2026-07-15T20:05:00Z"), rule)).toBe("2026-07-15T14:05:00-06:00");
  });

  it("winter before the 2026 change: MST, -07:00", () => {
    expect(toEdmontonIso(Date.parse("2026-01-15T19:40:00Z"), rule)).toBe("2026-01-15T12:40:00-07:00");
  });

  it("winter after 2026-11-01: depends on the tz rule", () => {
    const iso = toEdmontonIso(Date.parse("2026-12-01T18:00:00Z"), rule);
    if (name === "tzdata 2026c") expect(iso).toBe("2026-12-01T12:00:00-06:00");
    else expect(iso).toBe("2026-12-01T11:00:00-07:00");
  });

  it("wall clock -> instant -> ISO round-trips, summer and winter", () => {
    for (const [date, time] of [["2026-07-15", "14:05"], ["2026-01-15", "12:40"], ["2026-12-01", "12:00"], ["2027-07-01", "09:30"]]) {
      const ms = zonedWallTimeToMs(date, time, rule)!;
      expect(toEdmontonIso(ms, rule).slice(0, 16)).toBe(`${date}T${time}`);
      expect(toDateTimeInputs(ms, rule)).toEqual({ date, time });
      // The ISO string parses back to the same instant
      expect(Date.parse(toEdmontonIso(ms, rule))).toBe(ms);
    }
  });

  it("spring-forward 2026-03-08: 02:30 does not exist, resolves to 03:30 MDT", () => {
    const ms = zonedWallTimeToMs("2026-03-08", "02:30", rule)!;
    expect(toEdmontonIso(ms, rule)).toBe("2026-03-08T03:30:00-06:00");
    expect(toEdmontonIso(zonedWallTimeToMs("2026-03-08", "01:30", rule)!, rule)).toBe("2026-03-08T01:30:00-07:00");
  });

  it("2026-11-01 01:30: first occurrence (MDT); 03:00 is MST only with the old rule", () => {
    expect(toEdmontonIso(zonedWallTimeToMs("2026-11-01", "01:30", rule)!, rule)).toBe("2026-11-01T01:30:00-06:00");
    const three = toEdmontonIso(zonedWallTimeToMs("2026-11-01", "03:00", rule)!, rule);
    expect(three).toBe(name === "tzdata 2026c" ? "2026-11-01T03:00:00-06:00" : "2026-11-01T03:00:00-07:00");
  });
});

describe("platform tz data (what the app sends)", () => {
  it("summer start_time carries -06:00", () => {
    expect(toEdmontonIso(zonedWallTimeToMs("2026-07-15", "14:05")!)).toBe("2026-07-15T14:05:00-06:00");
  });
  it("winter start_time carries the offset the platform's rule gives", () => {
    expect(toEdmontonIso(zonedWallTimeToMs("2026-01-15", "12:40")!)).toBe("2026-01-15T12:40:00-07:00");
    expect(toEdmontonIso(zonedWallTimeToMs("2026-12-01", "12:00")!)).toBe(
      platformIs2026c ? "2026-12-01T12:00:00-06:00" : "2026-12-01T12:00:00-07:00",
    );
  });
});

describe("helpers", () => {
  it("rejects malformed date/time", () => {
    expect(zonedWallTimeToMs("2026-04-31", "10:00")).toBeNull();
    expect(zonedWallTimeToMs("2026-13-01", "10:00")).toBeNull();
    expect(zonedWallTimeToMs("2026-04-01", "24:00")).toBeNull();
    expect(zonedWallTimeToMs("", "10:00")).toBeNull();
    expect(zonedWallTimeToMs("2026-04-01", "")).toBeNull();
  });
  it("rounds to the minute", () => {
    expect(roundToMinute(Date.parse("2026-07-15T20:05:29.900Z"))).toBe(Date.parse("2026-07-15T20:05:00Z"));
    expect(roundToMinute(Date.parse("2026-07-15T20:05:30Z"))).toBe(Date.parse("2026-07-15T20:06:00Z"));
  });
  it("day of year uses the Edmonton date", () => {
    // 2026-07-16 03:00 UTC is still 15 July in Edmonton (day 196)
    expect(edmontonDayOfYear(Date.parse("2026-07-16T03:00:00Z"))).toBe(196);
    expect(edmontonDayOfYear(Date.parse("2026-01-01T12:00:00Z"))).toBe(1);
  });
});
