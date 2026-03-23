/** Simulation types matching the API schemas. */

export interface WeatherParams {
  wind_speed: number;
  wind_direction: number;
  temperature: number;
  relative_humidity: number;
  precipitation_24h: number;
}

export interface FWIOverrides {
  ffmc: number | null;
  dmc: number | null;
  dc: number | null;
}

export interface SimulationCreate {
  ignition_lat: number;
  ignition_lng: number;
  weather: WeatherParams;
  fwi_overrides?: FWIOverrides;
  duration_hours: number;
  snapshot_interval_minutes: number;
  fuel_type: string;
  fuel_grid_path?: string | null;
  water_path?: string | null;
  buildings_path?: string | null;
  wui_zones_path?: string | null;
  dem_path?: string | null;
  use_ca_mode?: boolean;
  enable_spotting?: boolean;
  spotting_intensity?: number;
}

export interface SimulationFrame {
  time_hours: number;
  perimeter: number[][]; // [[lat, lng], ...]
  area_ha: number;
  head_ros_m_min: number;
  max_hfi_kw_m: number;
  fire_type: string;
  flame_length_m: number;
  fuel_breakdown: Record<string, number>;
  spot_fires?: Array<{ lat: number; lng: number; distance_m: number; hfi_kw_m: number }> | null;
  num_fronts?: number;
  burned_cells?: Array<{ lat: number; lng: number; intensity: number; fuel: string; t?: number }> | null;
  day?: number | null; // Multi-day scenario: which day (1-based)
}

export interface MultiDayWeatherParams {
  wind_speed: number;
  wind_direction: number;
  temperature: number;
  relative_humidity: number;
  precipitation_24h: number;
}

export interface MultiDaySimulationCreate {
  ignition_lat: number;
  ignition_lng: number;
  days: MultiDayWeatherParams[];
  fwi_overrides?: FWIOverrides;
  month?: number;
  snapshot_interval_minutes?: number;
  fuel_type?: string;
  fuel_grid_path?: string | null;
  water_path?: string | null;
  buildings_path?: string | null;
  dem_path?: string | null;
}

export type SimulationStatus = "pending" | "running" | "paused" | "completed" | "cancelled" | "failed";

export interface SimulationResponse {
  simulation_id: string;
  status: SimulationStatus;
  config: SimulationCreate | null;
  frames: SimulationFrame[];
  error: string | null;
}

export interface WSEvent {
  type: "simulation.frame" | "simulation.completed" | "simulation.error" | "status";
  simulation_id?: string;
  frame?: SimulationFrame;
  error?: string;
  state?: "running" | "paused" | "cancelled";
}

export interface FWIResult {
  ffmc: number;
  dmc: number;
  dc: number;
  isi: number;
  bui: number;
  fwi: number;
  danger_rating: string;
}

export interface BurnProbabilityRequest {
  ignition_lat: number;
  ignition_lng: number;
  weather: WeatherParams;
  fwi_overrides?: FWIOverrides;
  duration_hours: number;
  n_iterations: number;
  jitter_m?: number;
  wind_speed_pct?: number;
  rh_abs?: number;
  base_seed?: number;
  fuel_grid_path?: string | null;
  water_path?: string | null;
  buildings_path?: string | null;
  dem_path?: string | null;
}

export interface BurnProbabilityResponse {
  burn_probability: number[][];  // 2D [rows][cols], values [0, 1]
  rows: number;
  cols: number;
  lat_min: number;
  lat_max: number;
  lng_min: number;
  lng_max: number;
  n_iterations: number;
  iterations_completed: number;
  cell_size_m: number;
}

export interface CurrentWeather {
  lat: number;
  lng: number;
  ffmc: number | null;
  dmc: number | null;
  dc: number | null;
  isi: number | null;
  bui: number | null;
  fwi: number | null;
  wind_speed: number | null;
  wind_direction: number | null;
  temperature: number | null;
  relative_humidity: number | null;
  source: string;
  available: boolean;
  message: string;
  data_timestamp: string | null;
}

export interface ScenarioConfig {
  id: string;
  name: string;
  description?: string;
  createdAt: string;
  ignitionPoint: { lat: number; lng: number } | null;
  weather: WeatherParams;
  fwi: FWIOverrides;
  fuelType: string;
  useEdmontonGrid: boolean;
  useSyntheticCA: boolean;
  enableSpotting: boolean;
  spottingIntensity: number;
  includeWater: boolean;
  includeBuildings: boolean;
  includeWUI: boolean;
  includeDEM: boolean;
  durationHours: number;
  snapshotMinutes: number;
  simMode: "single" | "multiday";
  multiDayDays: MultiDayWeatherParams[];
  mcIterations: number;
  lastRunStats?: {
    areaHa: number;
    timeHours: number;
  } | null;
}

export const FUEL_TYPES: Record<string, string> = {
  C1: "Spruce-Lichen Woodland",
  C2: "Boreal Spruce",
  C3: "Mature Jack/Lodgepole Pine",
  C4: "Immature Jack/Lodgepole Pine",
  C5: "Red/White Pine",
  C6: "Conifer Plantation",
  C7: "Ponderosa Pine/Douglas Fir",
  D1: "Leafless Aspen",
  M1: "Boreal Mixedwood (Leafless)",
  M2: "Boreal Mixedwood (Green)",
  M3: "Dead Balsam Fir Mixedwood (Leafless)",
  M4: "Dead Balsam Fir Mixedwood (Green)",
  O1a: "Matted Grass",
  O1b: "Standing Grass",
  S1: "Jack/Lodgepole Pine Slash",
  S2: "White Spruce/Balsam Slash",
  S3: "Coastal Cedar/Hemlock/Fir Slash",
};
