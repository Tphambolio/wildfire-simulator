import { describe, expect, it } from "vitest";
import { clockAt, formatClock, formatClockAt, formatElapsed, zoneAbbrev } from "./time";

describe("formatClock (America/Edmonton)", () => {
  it("formats 24 h HH:MM", () => {
    expect(formatClock(new Date("2026-04-28T19:40:00Z"))).toBe("13:40"); // MDT, UTC-6
    expect(formatClock(new Date("2026-01-15T06:05:00Z"))).toBe("23:05"); // MST, UTC-7
    expect(formatClock(new Date("2026-07-01T06:00:00Z"))).toBe("00:00"); // midnight is 00, not 24
  });

  it("names the zone", () => {
    expect(zoneAbbrev(new Date("2026-04-28T19:40:00Z"))).toBe("MDT");
    expect(zoneAbbrev(new Date("2026-01-15T19:40:00Z"))).toBe("MST");
  });
});

// Alberta's last clock change: 2026-11-01 02:00 MDT. With tzdata before 2026c the clocks fall
// back to MST (UTC-7); from tzdata 2026c Alberta stays on UTC-6 all year and nothing changes on
// the wall clock. The labels must follow whichever rule the platform's tz data has, never a
// hard-coded offset, so the test detects the rule from an independent instant first.
const permanentUtc6 = formatClock(new Date("2026-11-02T12:00:00Z")) === "06:00";

describe(`scenario clock across 2026-11-01 (tz data: ${permanentUtc6 ? "Alberta permanent UTC-6" : "fall-back to MST"})`, () => {
  // Scenario starts 2026-11-01 00:30 MDT = 06:30 UTC
  const start = new Date("2026-11-01T06:30:00Z");

  it("labels each hour with the wall clock", () => {
    expect(formatClockAt(start, 0)).toBe("00:30");
    expect(formatClockAt(start, 1)).toBe("01:30"); // 07:30 UTC, still MDT either way
    if (permanentUtc6) {
      expect(formatClockAt(start, 2)).toBe("02:30"); // 08:30 UTC, still UTC-6
      expect(formatClockAt(start, 3)).toBe("03:30");
      expect(formatClockAt(start, 4.25)).toBe("04:45");
    } else {
      expect(formatClockAt(start, 2)).toBe("01:30"); // 08:30 UTC, fell back to MST
      expect(formatClockAt(start, 3)).toBe("02:30");
      expect(formatClockAt(start, 4.25)).toBe("03:45");
    }
  });

  it("names the zone on each side of the change", () => {
    expect(zoneAbbrev(clockAt(start, 1))).toBe("MDT");
    const after = zoneAbbrev(clockAt(start, 2));
    if (permanentUtc6) expect(after).not.toBe("MST");
    else expect(after).toBe("MST");
  });

  it("T+ elapsed time is unaffected by the clock change", () => {
    expect(formatElapsed(2)).toBe("T+2:00");
  });
});

describe("scenario clock across the 2026-03-08 DST change (02:00 MST -> 03:00 MDT)", () => {
  // 01:30 MST = 08:30 UTC
  const start = new Date("2026-03-08T08:30:00Z");
  it("skips the missing hour", () => {
    expect(formatClockAt(start, 0)).toBe("01:30");
    expect(formatClockAt(start, 1)).toBe("03:30");
    expect(zoneAbbrev(clockAt(start, 0))).toBe("MST");
    expect(zoneAbbrev(clockAt(start, 1))).toBe("MDT");
  });
});

describe("formatElapsed", () => {
  it.each([
    [0, "T+0:00"],
    [0.25, "T+0:15"],
    [1.0833333, "T+1:05"],
    [4, "T+4:00"],
    [26.5, "T+26:30"],
    [-1, "T+0:00"],
  ])("%s h -> %s", (h, s) => {
    expect(formatElapsed(h)).toBe(s);
  });

  it("matches the timeline label pattern for clock labels", () => {
    const start = new Date("2026-04-28T19:40:00Z");
    for (const h of [0, 0.5, 1, 2.75, 4]) expect(formatClockAt(start, h)).toMatch(/^\d{2}:\d{2}$/);
  });
});
