/** Client-side FWI indices (ISI, BUI, FWI) and run parameters for the header and reports. */
import type { FWIOverrides, WeatherChange, WeatherParams } from "../types/simulation";
import type { RunParams } from "../components/WeatherPanel";
import { fwiClass } from "./fwiClass";

// ── Client-side CFFDRS FWI computation (Forestry Canada 1992, ST-X-3) ──────────
// FFMC coefficient 147.2 as printed (Van Wagner 1987 eq 2b; ST-X-3 eq 46), the same as the
// engine (engine/src/firesim/fwi/calculator.py FFMC_COEFFICIENT); cffdrs uses 147.27723.
export function computeISI(ffmc: number, windSpeedKmh: number): number {
  const m = 147.2 * (101 - ffmc) / (59.5 + ffmc);
  const fW = Math.exp(0.05039 * windSpeedKmh);
  const fF = 91.9 * Math.exp(-0.1386 * m) * (1 + Math.pow(m, 5.31) / 49300000);
  return 0.208 * fW * fF;
}

export function computeBUI(dmc: number, dc: number): number {
  if (dmc + 0.4 * dc === 0) return 0;
  if (dmc <= 0.4 * dc) {
    return 0.8 * dmc * dc / (dmc + 0.4 * dc);
  }
  return dmc - (1 - 0.8 * dc / (dmc + 0.4 * dc)) * (0.92 + Math.pow(0.0114 * dmc, 1.7));
}

export function computeFWI(isi: number, bui: number): number {
  const fD = bui <= 80
    ? 0.626 * Math.pow(bui, 0.809) + 2
    : 1000 / (25 + 108.64 * Math.exp(-0.023 * bui));
  const B = 0.1 * isi * fD;
  return B > 1
    ? Math.exp(2.72 * Math.pow(0.434 * Math.log(B), 0.647))
    : B;
}


/** Run parameters (header chips, reports) for a run with this weather and these FWI codes,
 * e.g. a perimeter restart with Setup's current values. */
export function runParamsFor(
  weather: WeatherParams,
  fwi: FWIOverrides,
  durationHours: number,
  fuelType: string | undefined,
  schedule: WeatherChange[] | null,
  hourlyForecast: boolean,
  startTime: string | null = null,
): RunParams {
  const isi = computeISI(fwi.ffmc ?? 85, weather.wind_speed);
  const bui = computeBUI(fwi.dmc ?? 6, fwi.dc ?? 15);
  const value = computeFWI(isi, bui);
  return {
    weather, fwi, isi, bui, fwi_value: value, danger_rating: fwiClass(value), n_iterations: 1,
    duration_hours: durationHours, fuel_type: fuelType, weather_schedule: schedule,
    hourly_forecast: hourlyForecast, start_time: startTime,
  };
}

