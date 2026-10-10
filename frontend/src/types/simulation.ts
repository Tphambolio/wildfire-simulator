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

/** FBP fuel and foliage inputs (ST-X-3 / Wotton et al. 2009). */
export interface FuelModifiers {
  grass_cure?: number; // degree of curing (%) for O-1a/O-1b; omitted: 95 % from 1 Mar to 29 May, else 422 if grass can burn (M1)
  grass_fuel_load?: number; // kg/m2
  percent_conifer?: number; // M-1/M-2
  percent_dead_fir?: number; // M-3/M-4
  fmc?: number | null; // foliar moisture (%); omitted = computed from date
  day_of_year?: number | null;
  elevation_m?: number | null;
}

/** Daily burning period: local clock hours (start_time's clock) between which fire spreads. */
export interface BurningPeriod {
  start_hour: number; // 0-24
  end_hour: number; // 0-24, after start_hour
}

/** One hour of weather for the hourly stream (applies until the next record). */
export interface HourlyWeatherParams {
  /** Hours after the start; negative (back to -24) = before it, used only for the FFMC spin-up */
  hours_from_start: number;
  temperature: number;
  relative_humidity: number;
  wind_speed: number; // km/h at 10 m
  wind_direction: number; // degrees FROM
  precipitation?: number; // mm in the hour
}

export interface SimulationCreate {
  ignition_lat: number;
  ignition_lng: number;
  weather: WeatherParams;
  // Grid runs: "incremental" streams only newly burned cells per frame
  cells_mode?: "cumulative" | "incremental";
  // Scenario start (ignition) time, ISO 8601 with offset; frame times are hours after it
  start_time?: string | null;
  fwi_overrides?: FWIOverrides;
  fuel_modifiers?: FuelModifiers;
  hourly_weather?: HourlyWeatherParams[] | null;
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
  /** Grid runs: run an ensemble after the deterministic run (GET /simulations/{id}/ensemble) */
  ensemble?: EnsembleParams | null;
  /** No spread outside these local hours (needs start_time). API default: off */
  burning_period?: BurningPeriod | null;
  /** Hourly FFMC from 17:00 local before the start (needs hourly_weather back to then). API default: off */
  ffmc_spin_up?: boolean;
  /**
   * Opt-in structure-to-structure spread (Hamada; illustrative — not validated in Canada).
   * Grid runs with building footprints. API default: off.
   */
  structure_spread?: boolean;
  /** With structure_spread: also ember ignition of buildings (spec §6). API default: off */
  structure_embers?: boolean;
  /** Building design fire, kW/m² (150 default, 400 scenario): ember HRR curve and burn-out time */
  structure_design_fire_kw_m2?: 150 | 400;
  /** Units stop passing fire when their design fire ends (spec §4.3). API default: on */
  structure_burnout?: boolean;
}

/**
 * Ensemble request (API EnsembleParams). Only n_members is set by the UI; the perturbation
 * sizes stay at the API defaults, calibrated on observed Alberta fires (docs/validation.md
 * "Ensemble calibration"; the range is still narrower than the real uncertainty).
 */
export interface EnsembleParams {
  n_members: number;
  seed?: number;
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
  spot_fires?: Array<{ lat: number; lng: number; distance_m: number; hfi_kw_m: number; source_lat?: number; source_lng?: number }> | null;
  num_fronts?: number;
  burned_cells?: Array<{
    lat: number; lng: number; intensity: number; fuel: string; fire_type?: string; t?: number;
    ros?: number; // m/min, front speed when the cell burned
    part?: "head" | "flank" | "back";
  }> | null;
  // Incremental streaming: number of earlier cells not repeated in burned_cells
  cells_offset?: number;
  // Grid runs: fastest head cell reached since the previous frame
  head?: {
    lat: number; lng: number; ros: number; raz: number; hfi: number; cfb: number;
    fuel: string; t: number; max_spot_distance_m: number;
  } | null;
  day?: number | null; // Multi-day scenario: which day (1-based)
  buildings_at_risk?: number; // building centroids inside the perimeter
  ignition_snapped_m?: number;
  // Exposure (not ignition probability), grid model with building footprints
  building_exposure?: BuildingExposureSummary | null;
  building_exposure_detail?: BuildingExposureDetail[] | null; // final frame only
  // Opt-in structure spread (request structure_spread): modelled involvement counts
  structure_spread?: StructureSpreadSummary | null;
  structure_spread_detail?: StructureUnitDetail[] | null; // final frame only
}

/**
 * Structure-to-structure spread counts by this frame (docs/structure-spread-spec.md).
 * Illustrative — not validated in Canada; not a prediction of which buildings burn. Any
 * display must show `label`.
 */
export interface StructureSpreadSummary {
  model: "hamada";
  label: string; // "illustrative — not validated in Canada"
  /** false when the OOM guard stopped the build (`note` says why; counts are null) */
  computed?: boolean;
  note?: string;
  units_in_run: number; // buildings in the run area (fuel grid box)
  units_built?: number; // units built: only the area the spread can reach
  units_front_contact: number | null; // reached by the wildland front (next to the building's grid cells)
  units_structure_to_structure: number | null; // reached building to building (Hamada)
  units_involved: number | null;
  /** Ember stage (request structure_embers); absent when off */
  embers?: boolean;
  units_ember?: number; // ignited by embers (from buildings or the wildland front)
  units_ember_from_wildland?: number; // of which the main ember source was the wildland front
  design_fire_kw_m2?: number;
  ember_generation_pcs_per_mw_s?: number;
  embers_from_wildland?: boolean;
  /** Burn-out (spec §4.3): units stop passing fire when their design fire ends */
  burnout?: boolean;
  burnout_min?: number | null; // involvement -> burn-out, minutes (66 at 150 kW/m², 70 at 400)
  units_burning?: number | null; // involved and still burning by this frame
  units_burnt_out?: number | null; // involved and burnt out by this frame (still counted as involved)
  combustible_fraction: number;
  neighbour_cutoff_m: number;
  wildland_contact_m: number;
  front_contact_rule?: string; // "building_cells": contact measured from the footprint's grid cells
  units_needed?: number; // guard: units the reachable box would need
  max_units?: number; // guard: the limit
}

/**
 * One involved building unit (final frame only; map display only, never exported).
 * Owner decision 2026-10-10 (D3 reversed): per-building display for exercises.
 */
export interface StructureUnitDetail {
  id: number; // involvement order
  t_h: number; // hours from the start
  t_out_h?: number | null; // burn-out, hours from the start (null when burn-out is off)
  mechanism: "front" | "b2b" | "ember"; // wildland front contact / building to building / embers
  source_id: number | null; // b2b or ember: id of the unit that passed the fire on (null: wildland embers)
  polygon: number[][][]; // footprint ring(s), [lng, lat]
}

export interface BuildingExposureSummary {
  inside_perimeter: number;
  within_10m: number;
  within_30m: number;
  within_100m: number;
  within_500m: number;
  flux_over_12_5: number; // peak radiant flux >= 12.5 kW/m2 (Cohen worst case)
  flux_over_25: number;
  ftp_reached: number; // Cohen (2004) flux-time criterion for piloted ignition of wood reached
}

export interface BuildingExposureDetail {
  lat: number;
  lng: number;
  min_distance_m: number;
  band: "flame_contact" | "radiant" | "short_range_ember" | "long_range_ember" | "none";
  first_within_30m_min: number | null;
  first_within_100m_min: number | null;
  peak_flux_kw_m2: number;
  minutes_over_12_5: number;
  ftp_index: number;
  ftp_index_high_emissive: number;
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
  fuel_modifiers?: FuelModifiers;
  month?: number;
  snapshot_interval_minutes?: number;
  fuel_type?: string;
  fuel_grid_path?: string | null;
  water_path?: string | null;
  buildings_path?: string | null;
  dem_path?: string | null;
  /** Scenario start (ISO with offset); each day runs 24 h from its clock time */
  start_time?: string | null;
  burning_period?: BurningPeriod | null;
}

export type SimulationStatus = "pending" | "running" | "paused" | "completed" | "cancelled" | "failed";

export interface SimulationResponse {
  simulation_id: string;
  status: SimulationStatus;
  config: SimulationCreate | null;
  frames: SimulationFrame[];
  error: string | null;
  /** Run phase while computing (absent from older API versions) */
  phase?: RunPhase | null;
  /** Fraction of the spread computed, 0-1 */
  progress?: number | null;
}

/** Server-side run phases (API `simulation.status`, docs/api-reference.md) */
export type RunPhase = "loading" | "buildings" | "spread" | "structures" | "finishing";

export interface WSEvent {
  type: "simulation.frame" | "simulation.completed" | "simulation.error" | "status" | "simulation.status";
  simulation_id?: string;
  frame?: SimulationFrame;
  error?: string;
  state?: "running" | "paused" | "cancelled";
  /** simulation.status: phase and spread fraction (0-1, null outside the spread) */
  phase?: RunPhase;
  progress?: number | null;
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
  fuel_modifiers?: FuelModifiers;
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
  station_name: string | null;
  distance_km: number | null;
  /** Date (YYYY-MM-DD, noon LST) the FWI codes are valid for */
  codes_date?: string | null;
  /** "today" | "yesterday" | "older" (station codes) | "estimate" (cold-start estimate) */
  codes_status?: "today" | "yesterday" | "older" | "estimate" | null;
  /** Age of the codes in words (also in `message`) */
  codes_label?: string | null;
  /** Open-Meteo model used for any value (null = station data only) */
  weather_model?: string | null;
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
  /** Range of outcomes (ensemble) members for grid runs; null = off (absent in older saves) */
  ensembleMembers?: number | null;
  /** Burning period (null = off); absent in older saves = the default 10-20 h */
  burningPeriod?: BurningPeriod | null;
  /** Evening FFMC spin-up (with hourly forecast weather); absent in older saves = on */
  ffmcSpinUp?: boolean;
  lastRunStats?: {
    areaHa: number;
    timeHours: number;
  } | null;
}

export interface PerimeterOverrideRequest {
  simulation_id: string;
  /** GeoJSON Polygon or MultiPolygon *geometry* (not Feature). Coords [lng, lat]. */
  perimeter_geojson: GeoJSON.Geometry;
  duration_hours?: number;
  snapshot_interval_minutes?: number;
  /** Where the fire is still active (lng/lat geometry); omitted = the whole perimeter */
  active_edges?: GeoJSON.Geometry | null;
  /** Distance (m) from active_edges counted active; omitted = one fuel-grid cell */
  active_edge_buffer_m?: number | null;
  /** Time of the observed perimeter (ISO with offset); default: the source run's start */
  start_time?: string | null;
  burning_period?: BurningPeriod | null;
  ffmc_spin_up?: boolean;
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
