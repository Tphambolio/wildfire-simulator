/** API client for the FireSim backend. */

import type { SimulationCreate, MultiDaySimulationCreate, SimulationResponse, CurrentWeather, FWIResult, BurnProbabilityRequest, BurnProbabilityResponse, PerimeterOverrideRequest } from "../types/simulation";

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

export function getWebSocketUrl(simId: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const host = API_BASE || `${proto}//${window.location.host}`;
  const wsBase = host.replace(/^http/, "ws");
  return `${wsBase}/api/v1/simulations/ws/${simId}`;
}
