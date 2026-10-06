import { describe, expect, it } from "vitest";
import { forecastStream } from "./api";

// 24 UTC hours from 2026-04-28T18:00Z (12:00 MDT)
const base = Date.parse("2026-04-28T18:00:00Z");
const hourly = {
  time: Array.from({ length: 24 }, (_, i) => new Date(base + i * 3600_000).toISOString().slice(0, 16)),
  temperature_2m: Array.from({ length: 24 }, (_, i) => 20 + i),
  relative_humidity_2m: Array(24).fill(30),
  wind_speed_10m: Array(24).fill(20),
  wind_direction_10m: Array.from({ length: 24 }, () => 270),
  precipitation: Array(24).fill(0),
};

describe("forecastStream", () => {
  it("starts with the hour containing a start 50 min into it, then fractional hours", () => {
    const start = base + 50 * 60_000; // 18:50Z
    const s = forecastStream(hourly, start, 3);
    expect(s[0]).toMatchObject({ hours_from_start: 0, temperature: 20 });
    expect(s[1].hours_from_start).toBeCloseTo(10 / 60);
    expect(s[1].temperature).toBe(21);
    expect(s.at(-1)!.hours_from_start).toBeLessThan(3);
    expect(s).toHaveLength(4); // 18:00 (from 0), 19:00, 20:00, 21:00
  });

  it("uses the forecast hours for a start 2 h ahead", () => {
    const s = forecastStream(hourly, base + 2 * 3600_000, 2);
    expect(s[0]).toMatchObject({ hours_from_start: 0, temperature: 22 });
    expect(s.map((r) => r.hours_from_start)).toEqual([0, 1]);
  });

  it("throws when the start is after the forecast", () => {
    expect(() => forecastStream(hourly, base + 30 * 3600_000, 1)).toThrow();
  });
});
