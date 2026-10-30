import { afterEach, describe, expect, it, vi } from "vitest";
import { FORECAST_MODEL, fetchHourlyForecast, forecastStream } from "./api";

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

  it("says so when the model has no value for a variable", () => {
    const gap = { ...hourly, relative_humidity_2m: hourly.relative_humidity_2m.map((v, i) => (i === 1 ? null : v)) };
    expect(() => forecastStream(gap as unknown as typeof hourly, base, 3)).toThrow(/GEM forecast has no relative_humidity_2m/);
  });
});

describe("fetchHourlyForecast", () => {
  afterEach(() => vi.unstubAllGlobals());

  const respond = (h: unknown, extra: Record<string, unknown> = {}) =>
    new Response(JSON.stringify({ available: true, message: "ok", hourly: h, src: "H", model_runs: {},
      weather_model: "eccc_gem_geomet", source: "ECCC GEM", attribution: "ECCC", ...extra }), { status: 200 });

  it("asks the FireSim API (ECCC GEM via GeoMet) for the scenario window, not a third party", async () => {
    const urls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      urls.push(url);
      return respond(hourly);
    });
    const s = await fetchHourlyForecast(53.5, -113.5, 2, base, base - 3 * 3600_000);
    expect(s.length).toBeGreaterThan(0);
    expect(FORECAST_MODEL).toBe("eccc_gem_geomet");
    const u = new URL(urls[0], "http://x");
    expect(u.pathname).toBe("/api/v1/weather/hourly");
    expect(u.searchParams.get("start")).toBe("2026-04-28T15:00:00.000Z");
    expect(u.searchParams.get("end")).toBe("2026-04-28T20:00:00.000Z");
    expect(urls.every((x) => !x.includes("open-meteo"))).toBe(true);
  });

  it("drops spin-up hours GeoMet no longer holds, so the spin-up shows as skipped", async () => {
    const gap = { ...hourly, temperature_2m: hourly.temperature_2m.map((v, i) => (i < 2 ? null : v)) };
    vi.stubGlobal("fetch", async () => respond(gap));
    const s = await fetchHourlyForecast(53.5, -113.5, 2, base + 3 * 3600_000, base);
    expect(s[0].hours_from_start).toBe(-1); // 20:00Z, the first covered hour
  });

  it("fails with the API's reason when there is no model weather", async () => {
    vi.stubGlobal("fetch", async () => respond(null, { available: false, message: "ECCC GeoMet unreachable" }));
    await expect(fetchHourlyForecast(53.5, -113.5, 2, base)).rejects.toThrow(/GeoMet unreachable/);
  });

  it("a gap at the start itself is an error, not a silent shift", async () => {
    const gap = { ...hourly, temperature_2m: hourly.temperature_2m.map((v, i) => (i === 0 ? null : v)) };
    vi.stubGlobal("fetch", async () => respond(gap));
    await expect(fetchHourlyForecast(53.5, -113.5, 2, base)).rejects.toThrow(/no temperature_2m/);
  });
});
