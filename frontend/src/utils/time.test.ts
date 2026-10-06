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

describe("scenario clock across the 2026-11-01 DST change (02:00 MDT -> 01:00 MST)", () => {
  // Scenario starts 2026-11-01 00:30 MDT = 06:30 UTC
  const start = new Date("2026-11-01T06:30:00Z");

  it("labels each hour with the wall clock, repeating 01:30 after the fall-back", () => {
    expect(formatClockAt(start, 0)).toBe("00:30");
    expect(formatClockAt(start, 1)).toBe("01:30"); // 07:30 UTC, still MDT
    expect(formatClockAt(start, 2)).toBe("01:30"); // 08:30 UTC, now MST
    expect(formatClockAt(start, 3)).toBe("02:30");
    expect(formatClockAt(start, 4.25)).toBe("03:45");
  });

  it("switches the zone name at the change", () => {
    expect(zoneAbbrev(clockAt(start, 1))).toBe("MDT");
    expect(zoneAbbrev(clockAt(start, 2))).toBe("MST");
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
