import { describe, expect, it } from "vitest";
import {
  clockAt, formatClock, formatClockAt, formatDate, formatElapsed, formatRunElapsed, formatRunSpan, isMultiDayRun, runDay,
  zoneAbbrev,
} from "./time";

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

// Alberta's last clock change: 2026-11-01 02:00 MDT. From then on Alberta stays on UTC-6 all year
// (Official Time Act; IANA tzdata 2026c). The app applies that rule itself after the change, so
// these hold whatever tz data the browser (or Node's ICU) has.
describe("scenario clock across 2026-11-01 (Alberta permanent UTC-6)", () => {
  // Scenario starts 2026-11-01 00:30 MDT = 06:30 UTC
  const start = new Date("2026-11-01T06:30:00Z");

  it("labels each hour with the wall clock: no fall-back", () => {
    expect(formatClockAt(start, 0)).toBe("00:30");
    expect(formatClockAt(start, 1)).toBe("01:30"); // 07:30 UTC, MDT
    expect(formatClockAt(start, 2)).toBe("02:30"); // 08:30 UTC, UTC-6
    expect(formatClockAt(start, 3)).toBe("03:30");
    expect(formatClockAt(start, 4.25)).toBe("04:45");
  });

  it("labels MDT before the change and MT from 02:00 MDT on", () => {
    expect(zoneAbbrev(clockAt(start, 1))).toBe("MDT");
    expect(zoneAbbrev(new Date("2026-11-01T07:59:59Z"))).toBe("MDT");
    expect(zoneAbbrev(new Date("2026-11-01T08:00:00Z"))).toBe("MT");
    expect(zoneAbbrev(clockAt(start, 2))).toBe("MT");
  });

  it("T+ elapsed time is unaffected by the clock change", () => {
    expect(formatElapsed(2)).toBe("T+2:00");
  });
});

describe("dates either side of the change (owner's labels: MT from 2026-11-01)", () => {
  it.each([
    // [instant, clock, label]
    ["2011-05-15T20:00:00Z", "14:00", "MDT"], // Slave Lake 2011 replay keeps its historical label
    ["2011-01-15T19:00:00Z", "12:00", "MST"],
    ["2026-10-31T18:00:00Z", "12:00", "MDT"],
    ["2026-11-02T18:00:00Z", "12:00", "MT"], // would be 11:00 MST under the old rule
    ["2026-12-21T18:00:00Z", "12:00", "MT"],
    ["2027-03-15T18:00:00Z", "12:00", "MT"], // no spring-forward in 2027
    ["2027-07-15T18:00:00Z", "12:00", "MT"],
  ])("%s -> %s %s", (iso, clock, label) => {
    expect(formatClock(new Date(iso))).toBe(clock);
    expect(zoneAbbrev(new Date(iso))).toBe(label);
  });

  it("formatDate and runDay follow the Alberta clock after the change", () => {
    // 2026-11-02 06:30 UTC is 00:30 on the 2nd under UTC-6 (23:30 on the 1st under the old rule)
    expect(formatDate(new Date("2026-11-02T06:30:00Z"))).toMatch(/(2 Nov|Nov 2)/);
    expect(runDay(new Date("2026-11-01T18:00:00Z"), new Date("2026-11-02T06:30:00Z"))).toBe(2);
  });

  it("run span across the change ends in MT", () => {
    expect(formatRunSpan(new Date("2026-10-31T18:00:00Z"), 48)).toMatch(/12:00 to .* 12:00 MT \(48 h\)$/);
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

describe("day of a later clock time (round-3 field test R7, R11)", () => {
  const start = new Date("2026-10-13T04:57:00-06:00");
  it("adds Dn for a later calendar day and the date for an earlier one", () => {
    expect(runDay(start, clockAt(start, 0))).toBe(1);
    expect(runDay(start, clockAt(start, 19))).toBe(1); // 23:57
    expect(runDay(start, clockAt(start, 19.1))).toBe(2); // 00:03 next day
    expect(formatClockAt(start, 5.05)).toBe("10:00");
    expect(formatClockAt(start, 35.4667)).toBe("D2 16:25");
    expect(formatClockAt(start, 67.5)).toBe("D4 00:27");
    expect(formatClockAt(start, -6)).toBe(`${formatDate(clockAt(start, -6))} 22:57`);
  });

  it("formats the span a run models", () => {
    expect(formatRunSpan(start, 72)).toBe(`${formatDate(start)} 04:57 to ${formatDate(clockAt(start, 72))} 04:57 MDT (72 h)`);
  });
});

describe("multi-day labels (72 h rerun, 2026-10-17)", () => {
  const start = new Date("2026-10-13T04:57:00-06:00");

  it("prefixes day 1 only in a multi-day run", () => {
    expect(formatClockAt(start, 5)).toBe("09:57");
    expect(formatClockAt(start, 5, true)).toBe("D1 09:57");
    expect(formatClockAt(start, 24 + 685 / 60, true)).toBe("D2 16:22");
  });

  it("shows the operational day with the run time, never the time within the day", () => {
    expect(formatRunElapsed(72, 3)).toBe("D3 · T+72:00");
    expect(formatRunElapsed(5.5, null)).toBe("T+5:30");
    expect(formatRunElapsed(5.5)).toBe("T+5:30");
  });

  it("detects a multi-day run from its frames", () => {
    expect(isMultiDayRun([{ day: 1 }, { day: 2 }])).toBe(true);
    expect(isMultiDayRun([{ day: 1 }, { day: null }, {}])).toBe(false);
    expect(isMultiDayRun([])).toBe(false);
  });
});
