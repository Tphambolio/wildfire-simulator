import { describe, expect, it } from "vitest";
import type { WeatherParams } from "../types/simulation";
import {
  newRow, rowTimeMs, scheduleErrors, scheduleForRestart, scheduleLines, scheduleRequest,
  timedSteps, weatherAt, type ScheduleRow,
} from "./weatherSchedule";
import { zonedWallTimeToMs } from "./time";
import { buildOverrideRequest } from "./activeEdges";
import { DEFAULT_FUEL_GRID_YEAR, edmontonYear, fuelVintageWarning } from "./fuelVintage";

const BASE: WeatherParams = { wind_speed: 47, wind_direction: 135, temperature: 20, relative_humidity: 15, precipitation_24h: 0 };
const START = zonedWallTimeToMs("2011-05-14", "17:50")!;
const row = (time: string, wind: number, rain = 0): ScheduleRow => ({
  time, wind_speed: wind, wind_direction: 135, temperature: 20, relative_humidity: 15, precipitation_mm_h: rain,
});
const H = 3_600_000;

describe("manual weather schedule (owner two-stage SWF-065 case)", () => {
  it("turns local clock rows into hours after the start", () => {
    expect(scheduleRequest([row("20:00", 20)], START)).toEqual([{
      hours_from_start: 2.1667, wind_speed: 20, wind_direction: 135, temperature: 20,
      relative_humidity: 15, precipitation_mm_h: 0,
    }]);
    expect(scheduleRequest([], START)).toBeNull();
  });

  it("an earlier clock time is the next day", () => {
    expect((rowTimeMs("01:00", START)! - START) / H).toBeCloseTo(7.1667, 3);
  });

  it("flags rows at the start, after the end, duplicated or out of range", () => {
    expect(scheduleErrors([row("20:00", 20)], START, 6)).toEqual([]);
    expect(scheduleErrors([row("17:50", 20)], START, 6)[0]).toMatch(/at the start/);
    expect(scheduleErrors([row("20:00", 20)], START, 2)[0]).toMatch(/after the run ends/);
    expect(scheduleErrors([row("20:00", 20), row("20:00", 10)], START, 6).join()).toMatch(/Two weather changes/);
    expect(scheduleErrors([row("20:00", 120)], START, 6).join()).toMatch(/0–100 km\/h/);
    expect(scheduleErrors([row("2x:00", 20)], START, 6).join()).toMatch(/not a time/);
  });

  it("a restart takes the step in force at its time and the later ones", () => {
    const steps = timedSteps([row("20:00", 20), row("22:00", 10)], START);
    const at21 = scheduleForRestart(BASE, steps, START + 3.1667 * H, 4);
    expect(at21.weather.wind_speed).toBe(20);
    expect(at21.schedule).toEqual([expect.objectContaining({ hours_from_start: 1, wind_speed: 10 })]);
    const before = scheduleForRestart(BASE, steps, START + H, 1);
    expect(before.weather.wind_speed).toBe(47);
    expect(before.schedule).toBeNull(); // 20:00 is after this 1 h restart
  });

  it("weather in force at a time, and report lines", () => {
    const sched = scheduleRequest([row("20:00", 20, 0.5)], START);
    expect(weatherAt(BASE, sched, 2).wind_speed).toBe(47);
    expect(weatherAt(BASE, sched, 2.2).wind_speed).toBe(20);
    expect(scheduleLines(BASE, sched, new Date(START))).toEqual([
      "17:50 47 km/h from 135°, 20 °C, RH 15 %",
      "20:00 20 km/h from 135°, 20 °C, RH 15 %, rain 0.5 mm/h",
    ]);
    expect(scheduleLines(BASE, null, new Date(START))).toEqual([]);
  });

  it("a new row copies the last one, an hour later", () => {
    const r = newRow(BASE, [row("20:00", 20)], START);
    expect(r.time).toBe("21:00");
    expect(r.wind_speed).toBe(20);
  });

  it("the restart request carries Setup's weather, codes and later steps", () => {
    const req = buildOverrideRequest({
      simulationId: "abc", perimeter: { type: "Polygon", coordinates: [] }, durationHours: 4,
      snapshotMinutes: 30, mode: "whole", lines: [], bufferM: null, startTime: "2011-05-14T19:50:00-06:00",
      burningPeriod: null, ffmcSpinUp: false, weather: { ...BASE, wind_speed: 20 },
      fwi: { ffmc: 90, dmc: 15, dc: 193 }, schedule: [{ hours_from_start: 1, wind_speed: 10, wind_direction: 135, temperature: 20, relative_humidity: 15 }],
    });
    expect(req.weather?.wind_speed).toBe(20);
    expect(req.fwi_overrides?.ffmc).toBe(90);
    expect(req.weather_schedule).toHaveLength(1);
    expect(req.duration_hours).toBe(4);
  });
});

describe("fuel-grid vintage (issue 6)", () => {
  it("warns for scenarios dated before the grid year only", () => {
    expect(fuelVintageWarning(DEFAULT_FUEL_GRID_YEAR, edmontonYear(START))).toMatch(
      /^Fuel grid represents 2025\. Areas burned since 2011 may be mapped as grass or non-fuel/);
    expect(fuelVintageWarning(2025, 2025)).toBeNull();
    expect(fuelVintageWarning(null, 2011)).toBeNull();
  });
});
