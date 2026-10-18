/**
 * Scenario start entry (design spec §2.3): wall clock in America/Edmonton <-> instant, and the
 * ISO 8601 `start_time` sent to the API with the offset in force at that instant.
 *
 * Alberta's clocks: MDT (UTC-6) / MST (UTC-7) with the usual North American DST dates until
 * 2026-11-01 02:00 MDT, then UTC-6 all year (Official Time Act; IANA tzdata 2026c). The app's
 * offset (`edmontonOffsetMinutes`) applies that rule itself after the change, whatever the
 * platform's tz data says, and uses the platform's tz data before it. The conversion functions
 * take an injectable offset, so the old (pre-2026c) rule is still exercised for comparison.
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

describe("the app's Edmonton offset follows the Alberta rule (tzdata 2026c)", () => {
  it("matches hour by hour, 2025-2028, whatever the platform's tz data", () => {
    for (let t = Date.UTC(2025, 0, 1); t < Date.UTC(2028, 0, 1); t += 7 * H) {
      expect(edmontonOffsetMinutes(t), new Date(t).toISOString()).toBe(rule2026c(t));
    }
  });
  it("Slave Lake 2011 (historical): MDT, -06:00 in May; MST in January", () => {
    expect(edmontonOffsetMinutes(Date.parse("2011-05-15T20:00:00Z"))).toBe(-360);
    expect(edmontonOffsetMinutes(Date.parse("2011-01-15T20:00:00Z"))).toBe(-420);
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

describe("what the app sends (Alberta rule)", () => {
  it("summer start_time carries -06:00", () => {
    expect(toEdmontonIso(zonedWallTimeToMs("2026-07-15", "14:05")!)).toBe("2026-07-15T14:05:00-06:00");
  });
  it("winter start_time: -07:00 before the change, -06:00 after", () => {
    expect(toEdmontonIso(zonedWallTimeToMs("2026-01-15", "12:40")!)).toBe("2026-01-15T12:40:00-07:00");
    expect(toEdmontonIso(zonedWallTimeToMs("2026-12-01", "12:00")!)).toBe("2026-12-01T12:00:00-06:00");
  });
  it.each([
    ["2011-05-15", "14:00", "2011-05-15T14:00:00-06:00"],
    ["2026-10-31", "12:00", "2026-10-31T12:00:00-06:00"],
    ["2026-11-01", "03:00", "2026-11-01T03:00:00-06:00"],
    ["2026-11-02", "12:00", "2026-11-02T12:00:00-06:00"],
    ["2027-03-15", "12:00", "2027-03-15T12:00:00-06:00"],
  ])("%s %s -> %s", (date, time, iso) => {
    expect(toEdmontonIso(zonedWallTimeToMs(date, time)!)).toBe(iso);
    expect(toDateTimeInputs(Date.parse(iso))).toEqual({ date, time });
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

describe("Edmonton calendar after the change (stale browser tz data must not move the date)", () => {
  it("2027-01-01 00:30 UTC-6 is in 2027 (23:30 on 31 Dec under the old rule)", async () => {
    const { edmontonYear } = await import("./fuelVintage");
    expect(edmontonYear(Date.parse("2027-01-01T06:30:00Z"))).toBe(2027);
    expect(edmontonYear(Date.parse("2011-05-15T20:00:00Z"))).toBe(2011);
  });
});
