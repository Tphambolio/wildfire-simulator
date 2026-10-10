/**
 * The scenario saved before Setup has sent its first configuration snapshot (App → ScenarioPanel).
 * It must match the Setup defaults (WeatherPanel): in particular the unsourced WUI zone modifiers
 * and the OpenStreetMap water mask stay OFF (decisions 2026-10-06 and 2026-10-07). Until
 * 2026-10-10 this fallback had `includeWUI: true`, so a scenario saved before the first snapshot
 * would have turned the unsourced modifiers on when loaded.
 */
import type { ScenarioConfig } from "../types/simulation";

export type ScenarioDraft = Omit<ScenarioConfig, "id" | "createdAt" | "name" | "description">;

export function fallbackScenarioConfig(ignitionPoint: { lat: number; lng: number } | null): ScenarioDraft {
  return {
    ignitionPoint,
    weather: { wind_speed: 20, wind_direction: 270, temperature: 25, relative_humidity: 30, precipitation_24h: 0 },
    fwi: { ffmc: 90, dmc: 45, dc: 300 },
    fuelType: "C2",
    useEdmontonGrid: true,
    useSyntheticCA: false,
    enableSpotting: false,
    spottingIntensity: 1.0,
    includeWater: false,
    includeBuildings: true,
    includeWUI: false,
    includeDEM: true,
    durationHours: 4,
    snapshotMinutes: 30,
    simMode: "single",
    multiDayDays: [],
    mcIterations: 50,
  };
}
