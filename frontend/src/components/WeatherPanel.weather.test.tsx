/**
 * Station weather auto-fill (owner bug 2026-10-10): moving the ignition must not overwrite
 * weather or FWI values the user, a preset or a loaded scenario set.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { CurrentWeather, ScenarioConfig, SimulationCreate } from "../types/simulation";

const station = (over: Partial<CurrentWeather> = {}): CurrentWeather => ({
  lat: 53.5, lng: -113.5, ffmc: 88, dmc: 30, dc: 200, isi: null, bui: null, fwi: null,
  wind_speed: 11, wind_direction: 200, temperature: 18, relative_humidity: 40,
  source: "CWFIS", available: true, message: "Loaded from CWFIS", data_timestamp: "2026-10-10",
  station_name: "Edmonton Stony Plain", distance_km: 12,
  ...over,
} as CurrentWeather);

const fetchCurrentWeather = vi.fn(async () => station());
vi.mock("../services/api", () => ({
  fetchCurrentWeather: (...a: unknown[]) => fetchCurrentWeather(...(a as [])),
  calculateFWI: vi.fn(async () => ({ ffmc: 91, dmc: 50, dc: 310 })),
  fetchHourlyForecast: vi.fn(async () => []),
}));

import WeatherPanel from "./WeatherPanel";

const A = { lat: 53.5, lng: -113.5 };
const B = { lat: 53.52, lng: -113.52 }; // ~2.6 km away
const FAR = { lat: 53.9, lng: -113.5 }; // ~44 km away

function setup(point = A, extra: Partial<React.ComponentProps<typeof WeatherPanel>> = {}) {
  const onStart = vi.fn<(p: SimulationCreate) => void>();
  const props = { onStartSimulation: onStart, ignitionPoint: point, isRunning: false, ...extra };
  const utils = render(<WeatherPanel {...props} />);
  return { onStart, rerender: (p: typeof point, more = {}) => utils.rerender(<WeatherPanel {...props} ignitionPoint={p} {...more} />) };
}

const windSlider = () => screen.getByLabelText(/^Wind Speed/) as HTMLInputElement;
const ffmcSlider = () => screen.getByLabelText(/^FFMC/) as HTMLInputElement;

beforeEach(() => {
  fetchCurrentWeather.mockClear();
  fetchCurrentWeather.mockImplementation(async () => station());
});

describe("WeatherPanel station weather", () => {
  it("auto-fills weather and FWI from the station on first placement", async () => {
    setup();
    await waitFor(() => expect(windSlider().value).toBe("11"));
    expect(ffmcSlider().value).toBe("88");
    expect(fetchCurrentWeather).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("weather-source")).toHaveTextContent("Edmonton Stony Plain");
  });

  it("keeps user edits when the ignition moves, and says so when the move is far", async () => {
    const { rerender, onStart } = setup();
    await waitFor(() => expect(windSlider().value).toBe("11"));
    fireEvent.change(windSlider(), { target: { value: "35" } });
    fireEvent.change(ffmcSlider(), { target: { value: "93" } });
    expect(screen.getByTestId("weather-source")).toHaveTextContent("Manual");
    rerender(B);
    await act(async () => {});
    expect(fetchCurrentWeather).toHaveBeenCalledTimes(1); // no refetch over the user's values
    expect(windSlider().value).toBe("35");
    expect(ffmcSlider().value).toBe("93");
    expect(screen.queryByTestId("weather-far")).not.toBeInTheDocument();
    rerender(FAR);
    await act(async () => {});
    expect(screen.getByTestId("weather-far")).toHaveTextContent(/Weather set 4\d km away/);
    // The run carries the user's values at the new point
    fireEvent.click(screen.getByRole("button", { name: "Run Simulation" }));
    await waitFor(() => expect(onStart).toHaveBeenCalled());
    const req = onStart.mock.calls[0][0];
    expect(req.weather.wind_speed).toBe(35);
    expect(req.fwi_overrides?.ffmc).toBe(93);
    expect(req.ignition_lat).toBe(FAR.lat);
  });

  it("'Update weather for this location' refetches and replaces the values", async () => {
    const { rerender } = setup();
    await waitFor(() => expect(windSlider().value).toBe("11"));
    fireEvent.change(windSlider(), { target: { value: "35" } });
    rerender(B);
    fetchCurrentWeather.mockImplementation(async () => station({ wind_speed: 22, station_name: "Edmonton Namao" }));
    fireEvent.click(screen.getByRole("button", { name: /Weather & FWI/ }));
    fireEvent.click(screen.getByRole("button", { name: "Update weather for this location" }));
    await waitFor(() => expect(windSlider().value).toBe("22"));
    expect(fetchCurrentWeather).toHaveBeenLastCalledWith(B.lat, B.lng);
    expect(screen.getByTestId("weather-source")).toHaveTextContent("Edmonton Namao");
  });

  it("an edit made while the station request is out is not overwritten", async () => {
    let resolve: (w: CurrentWeather) => void = () => {};
    fetchCurrentWeather.mockImplementation(() => new Promise<CurrentWeather>((r) => { resolve = r; }));
    setup();
    fireEvent.change(windSlider(), { target: { value: "40" } });
    await act(async () => resolve(station()));
    expect(windSlider().value).toBe("40");
  });

  it("a preset counts as user-set", async () => {
    const { rerender } = setup();
    await waitFor(() => expect(windSlider().value).toBe("11"));
    fireEvent.change(screen.getByLabelText(/Start from a preset/), { target: { value: "spring-grass" } });
    expect(windSlider().value).toBe("20");
    rerender(B);
    await act(async () => {});
    expect(fetchCurrentWeather).toHaveBeenCalledTimes(1);
    expect(windSlider().value).toBe("20");
  });

  it("a loaded scenario counts as user-set", async () => {
    const scenario = {
      id: "s1", name: "x", createdAt: "2026-10-10", ignitionPoint: A,
      weather: { wind_speed: 33, wind_direction: 90, temperature: 20, relative_humidity: 25, precipitation_24h: 0 },
      fwi: { ffmc: 92, dmc: 40, dc: 250 }, fuelType: "C2", useEdmontonGrid: true, useSyntheticCA: false,
      enableSpotting: false, spottingIntensity: 1, includeWater: false, includeBuildings: true, includeWUI: false,
      includeDEM: true, durationHours: 4, snapshotMinutes: 30, simMode: "single", multiDayDays: [], mcIterations: 50,
    } as ScenarioConfig;
    const { rerender } = setup(A);
    await waitFor(() => expect(windSlider().value).toBe("11"));
    rerender(B, { scenarioToLoad: scenario });
    await act(async () => {});
    expect(windSlider().value).toBe("33");
    expect(fetchCurrentWeather).toHaveBeenCalledTimes(1);
  });
});
