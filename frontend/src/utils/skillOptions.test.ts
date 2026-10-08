import { describe, expect, it } from "vitest";
import {
  coversSpinup,
  formatBurningPeriod,
  inBurningPeriod,
  localHour,
  offPeriods,
  skillRequestFields,
  spinupFromMs,
  validateBurningHours,
} from "./skillOptions";
import { forecastStream } from "../services/api";
import type { OffsetFn } from "./time";

const UTC6: OffsetFn = () => -360; // Alberta from November 2026 (and MDT before)
const at = (iso: string) => Date.parse(iso);

describe("burning-period hours", () => {
  it("accepts whole hours 0-24 with start before end", () => {
    expect(validateBurningHours(10, 20)).toBeNull();
    expect(validateBurningHours(0, 24)).toBeNull();
  });
  it("rejects bad hours with a message", () => {
    expect(validateBurningHours(20, 10)).toMatch(/before/);
    expect(validateBurningHours(10, 10)).toMatch(/before/);
    expect(validateBurningHours(-1, 10)).toMatch(/0–24/);
    expect(validateBurningHours(10, 25)).toMatch(/0–24/);
    expect(validateBurningHours(10.5, 20)).toMatch(/whole hour/);
    expect(validateBurningHours(Number.NaN, 20)).toMatch(/whole hour/);
  });
  it("formats", () => {
    expect(formatBurningPeriod({ start_hour: 10, end_hour: 20 })).toBe("10:00–20:00");
  });
});

describe("clock of the start", () => {
  it("local hour and the 17:00 spin-up start", () => {
    const start = at("2026-07-15T06:00:00-06:00");
    expect(localHour(start, UTC6)).toBe(6);
    expect(new Date(spinupFromMs(start, UTC6)).toISOString()).toBe("2026-07-14T23:00:00.000Z"); // 17:00 the day before
    const evening = at("2026-07-15T21:30:00-06:00");
    expect((evening - spinupFromMs(evening, UTC6)) / 3_600_000).toBe(4.5); // 17:00 the same day
    const five = at("2026-07-15T17:00:00-06:00");
    expect(spinupFromMs(five, UTC6)).toBe(five);
  });

  it("inside / outside the burning period and the shaded spans", () => {
    const bp = { start_hour: 10, end_hour: 20 };
    const start = at("2026-07-15T21:00:00-06:00");
    expect(inBurningPeriod(start, 0, bp, UTC6)).toBe(false);
    expect(inBurningPeriod(start, 12.99, bp, UTC6)).toBe(false);
    expect(inBurningPeriod(start, 13, bp, UTC6)).toBe(true); // 10:00
    expect(inBurningPeriod(start, 23, bp, UTC6)).toBe(false); // 20:00
    expect(offPeriods(start, 24, bp, UTC6)).toEqual([[0, 13], [23, 24]]);
    expect(offPeriods(at("2026-07-15T12:00:00-06:00"), 4, bp, UTC6)).toEqual([]);
    expect(offPeriods(at("2026-07-15T18:00:00-06:00"), 4, bp, UTC6)).toEqual([[2, 4]]);
    expect(offPeriods(start, 24, { start_hour: 0, end_hour: 24 }, UTC6)).toEqual([]);
  });
});

// Open-Meteo-like hours (UTC) from 2026-07-14T12:00Z for 48 h
const base = at("2026-07-14T12:00:00Z");
const hourly = {
  time: Array.from({ length: 48 }, (_, i) => new Date(base + i * 3600_000).toISOString().slice(0, 16)),
  temperature_2m: Array(48).fill(15),
  relative_humidity_2m: Array(48).fill(60),
  wind_speed_10m: Array(48).fill(10),
  wind_direction_10m: Array(48).fill(270),
  precipitation: Array(48).fill(0),
};

describe("request fields", () => {
  const start = at("2026-07-15T06:00:00-06:00");
  const on = { burningOn: true, startHour: 10, endHour: 20, spinUpOn: true };

  it("sends the burning period and the spin-up when the forecast reaches back to 17:00", () => {
    const stream = forecastStream(hourly, start, 4, spinupFromMs(start, UTC6));
    expect(stream[0].hours_from_start).toBe(-13);
    expect(stream.filter((r) => r.hours_from_start >= 0)).toHaveLength(4);
    expect(coversSpinup(stream, start, spinupFromMs(start, UTC6))).toBe(true);
    const f = skillRequestFields(on, stream, start);
    expect(f).toMatchObject({ burning_period: { start_hour: 10, end_hour: 20 }, ffmc_spin_up: true, spinUpSkipped: null });
  });

  it("drops the spin-up without hourly weather or without the evening hours", () => {
    expect(skillRequestFields(on, null, start)).toMatchObject({ ffmc_spin_up: false, spinUpSkipped: /hourly/ });
    const runOnly = forecastStream(hourly, start, 4);
    expect(skillRequestFields(on, runOnly, start)).toMatchObject({ ffmc_spin_up: false, spinUpSkipped: /17:00/ });
  });

  it("omits an invalid or switched-off burning period", () => {
    expect(skillRequestFields({ ...on, startHour: 20, endHour: 10 }, null, start).burning_period).toBeNull();
    expect(skillRequestFields({ ...on, burningOn: false }, null, start).burning_period).toBeNull();
    expect(skillRequestFields({ ...on, spinUpOn: false }, null, start)).toMatchObject({ ffmc_spin_up: false, spinUpSkipped: null });
  });
});
