/** API client for the FireSim backend. */

import type { SimulationCreate, MultiDaySimulationCreate, SimulationResponse, CurrentWeather, FWIResult, BurnProbabilityRequest, BurnProbabilityResponse, PerimeterOverrideRequest, HourlyWeatherParams } from "../types/simulation";

const API_BASE = import.meta.env.VITE_API_URL || "";

export async function createSimulation(
  params: SimulationCreate
): Promise<SimulationResponse> {
  const resp = await fetch(`${API_BASE}/api/v1/simulations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(err.detail || "Failed to create simulation");
  }
  return resp.json();
}

export async function createMultiDaySimulation(
  params: MultiDaySimulationCreate
): Promise<SimulationResponse> {
  const resp = await fetch(`${API_BASE}/api/v1/simulations/multiday`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(err.detail || "Failed to start multi-day simulation");
  }
  return resp.json();
}

export async function getSimulation(
  simId: string
): Promise<SimulationResponse> {
  const resp = await fetch(`${API_BASE}/api/v1/simulations/${simId}`);
  if (!resp.ok) {
    throw new Error(`Simulation ${simId} not found`);
  }
  return resp.json();
}

export async function fetchCurrentWeather(
  lat: number,
  lng: number
): Promise<CurrentWeather> {
  const resp = await fetch(
    `${API_BASE}/api/v1/weather/current?lat=${lat}&lng=${lng}`
  );
  if (!resp.ok) {
    throw new Error(`Weather fetch failed: ${resp.statusText}`);
  }
  return resp.json();
}

export async function calculateFWI(params: {
  temperature: number;
  relative_humidity: number;
  wind_speed: number;
  precipitation_24h?: number;
  month?: number;
  ffmc_prev?: number;
  dmc_prev?: number;
  dc_prev?: number;
}): Promise<FWIResult> {
  const resp = await fetch(`${API_BASE}/api/v1/fwi/calculate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(err.detail || "FWI calculation failed");
  }
  return resp.json();
}

export async function computeBurnProbability(
  params: BurnProbabilityRequest
): Promise<BurnProbabilityResponse> {
  const resp = await fetch(`${API_BASE}/api/v1/simulations/burn-probability`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(err.detail || "Burn probability computation failed");
  }
  return resp.json();
}

export async function createPerimeterOverride(
  req: PerimeterOverrideRequest
): Promise<SimulationResponse> {
  const resp = await fetch(`${API_BASE}/api/v1/simulations/perimeter-override`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(err.detail || "Perimeter override request failed");
  }
  return resp.json();
}

export async function fetchFuelGridImage(fuelGridPath: string): Promise<{ image: string; bounds: [number, number, number, number]; legend?: Array<{ fuel: string; color: string }> }> {
  const params = new URLSearchParams({ fuel_grid_path: fuelGridPath });
  const res = await fetch(`${API_BASE}/api/v1/simulations/fuel-grid-image?${params}`);
  if (!res.ok) throw new Error(`Fuel grid image failed: ${res.status}`);
  return res.json();
}

/**
 * Hourly forecast for the next `hours` hours at a point (Open-Meteo, no key), as an hourly
 * weather stream starting at the current hour.
 */
export async function fetchHourlyForecast(
  lat: number,
  lng: number,
  hours: number,
): Promise<HourlyWeatherParams[]> {
  const url =
    `https://api.open-meteo.com/v1/forecast?latitude=${lat.toFixed(4)}&longitude=${lng.toFixed(4)}` +
    "&hourly=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,precipitation" +
    "&wind_speed_unit=kmh&timezone=UTC&forecast_days=3";
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`Forecast request failed: ${resp.status}`);
  const data = await resp.json();
  const h = data.hourly;
  const now = Date.now();
  const start = h.time.findIndex((t: string) => Date.parse(t + "Z") + 3600_000 > now);
  if (start < 0) throw new Error("Forecast has no hours ahead of now");
  const n = Math.min(Math.ceil(hours), h.time.length - start);
  return Array.from({ length: n }, (_, k) => ({
    hours_from_start: k,
    temperature: h.temperature_2m[start + k],
    relative_humidity: h.relative_humidity_2m[start + k],
    wind_speed: h.wind_speed_10m[start + k],
    wind_direction: h.wind_direction_10m[start + k] % 360,
    precipitation: h.precipitation[start + k] ?? 0,
  }));
}

export function getWebSocketUrl(simId: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const host = API_BASE || `${proto}//${window.location.host}`;
  const wsBase = host.replace(/^http/, "ws");
  return `${wsBase}/api/v1/simulations/ws/${simId}`;
}
