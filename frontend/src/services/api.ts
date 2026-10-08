/** API client for the FireSim backend. */

import type { SimulationCreate, MultiDaySimulationCreate, SimulationResponse, CurrentWeather, FWIResult, BurnProbabilityRequest, BurnProbabilityResponse, PerimeterOverrideRequest, HourlyWeatherParams } from "../types/simulation";
import type { EnsembleResponse } from "../utils/ensemble";

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

/** API version and deployed git SHA (GET /api/v1/version), stamped on briefings. */
export async function getVersion(): Promise<{ version: string; git_sha: string }> {
  const resp = await fetch(`${API_BASE}/api/v1/version`);
  if (!resp.ok) throw new Error(`Version request failed: ${resp.status}`);
  return resp.json();
}

/** Thrown by getEnsemble when the run has no ensemble (404). */
export class NoEnsembleError extends Error {}

/**
 * Ensemble progress, and once complete the P10/P50/P90 arrival and burn-probability rasters
 * (see utils/ensemble.ts). 404 = no ensemble for this run (NoEnsembleError).
 */
export async function getEnsemble(simId: string, signal?: AbortSignal): Promise<EnsembleResponse> {
  const resp = await fetch(`${API_BASE}/api/v1/simulations/${simId}/ensemble`, { signal });
  if (resp.status === 404) {
    const err = await resp.json().catch(() => ({ detail: "" }));
    throw new NoEnsembleError(err.detail || "No ensemble for this run");
  }
  if (!resp.ok) throw new Error(`Ensemble request failed: ${resp.status}`);
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
 * Hourly forecast at a point (Open-Meteo, no key) as an hourly weather stream for a scenario
 * that starts at ``startMs`` (default now) and runs ``hours`` hours. See forecastStream.
 */
export async function fetchHourlyForecast(
  lat: number,
  lng: number,
  hours: number,
  startMs: number = Date.now(),
  fromMs?: number,
): Promise<HourlyWeatherParams[]> {
  const url =
    `https://api.open-meteo.com/v1/forecast?latitude=${lat.toFixed(4)}&longitude=${lng.toFixed(4)}` +
    "&hourly=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,precipitation" +
    "&wind_speed_unit=kmh&timezone=UTC&past_days=1&forecast_days=3";
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`Forecast request failed: ${resp.status}`);
  const data = await resp.json();
  return forecastStream(data.hourly, startMs, hours, fromMs);
}

/**
 * Slice Open-Meteo hourly data (UTC hour starts) into records for a scenario starting at
 * ``startMs``: the hour containing the start applies from 0, and each later hour from its
 * own start (fractional hours after the scenario start), up to the end of the run.
 *
 * With ``fromMs`` (the FFMC spin-up start, before ``startMs``) the hours from the one
 * containing ``fromMs`` are included too, with negative ``hours_from_start`` (the first at
 * exactly ``fromMs``); the hour containing the start keeps its own (negative) start.
 */
export function forecastStream(
  h: {
    time: string[]; temperature_2m: number[]; relative_humidity_2m: number[];
    wind_speed_10m: number[]; wind_direction_10m: number[]; precipitation: Array<number | null>;
  },
  startMs: number,
  hours: number,
  fromMs?: number,
): HourlyWeatherParams[] {
  const t0 = h.time.map((t) => Date.parse(t.endsWith("Z") ? t : t + "Z"));
  const spin = fromMs !== undefined && fromMs < startMs;
  const from = spin ? fromMs : startMs;
  const first = t0.findIndex((t) => t + 3600_000 > from);
  if (first < 0) throw new Error("Forecast has no hours at or after the scenario start");
  const endMs = startMs + hours * 3600_000;
  const out: HourlyWeatherParams[] = [];
  for (let i = first; i < t0.length && t0[i] < endMs; i++) {
    out.push({
      hours_from_start: spin ? (Math.max(t0[i], from) - startMs) / 3600_000 : Math.max(0, (t0[i] - startMs) / 3600_000),
      temperature: h.temperature_2m[i],
      relative_humidity: h.relative_humidity_2m[i],
      wind_speed: h.wind_speed_10m[i],
      wind_direction: h.wind_direction_10m[i] % 360,
      precipitation: h.precipitation[i] ?? 0,
    });
  }
  return out;
}

export function getWebSocketUrl(simId: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const host = API_BASE || `${proto}//${window.location.host}`;
  const wsBase = host.replace(/^http/, "ws");
  return `${wsBase}/api/v1/simulations/ws/${simId}`;
}
