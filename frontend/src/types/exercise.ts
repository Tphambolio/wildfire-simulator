/**
 * Exercise mode data model (docs/exercise-mode-spec.md §9). PR0 ships the types and the
 * client-side clock and inject log only; nothing here is sent to the API yet.
 *
 * Framing rules (owner's hard rules, spec §8): an exercise never suggests an evacuation tier,
 * never shows per-building results or addresses, and every export carries an EXERCISE mark.
 */

// ── Exercise clock ────────────────────────────────────────────────────────────

/** Replay rates offered to the controller (exercise seconds per wall second). */
export const EXERCISE_RATES = [1, 4, 12] as const;
export type ExerciseRate = (typeof EXERCISE_RATES)[number];

export type ExerciseClockMode = "paused" | "playing";

export interface ExerciseClockState {
  /** Exercise mode is on (the banner is shown, the timeline follows the exercise clock). */
  enabled: boolean;
  mode: ExerciseClockMode;
  rate: ExerciseRate;
  /** Exercise time (ms since epoch, an instant on the run's own timeline); null when off. */
  exerciseMs: number | null;
  /** First and last instant the clock can show (the run's span, or a 72 h default). */
  startMs: number | null;
  endMs: number | null;
  /** Operational-period boundaries (ms), ascending, for "Next period". */
  periodBoundariesMs: number[];
}

export type ExerciseClockAction =
  | { type: "enable"; startMs: number; endMs: number; periodBoundariesMs?: number[]; at?: number }
  | { type: "disable" }
  | { type: "play" }
  | { type: "pause" }
  | { type: "setRate"; rate: ExerciseRate }
  /** Wall-clock time elapsed since the last tick; advances by `rate × wallDeltaMs` when playing. */
  | { type: "tick"; wallDeltaMs: number }
  | { type: "seek"; ms: number }
  | { type: "nextPeriod" }
  | { type: "setBounds"; startMs: number; endMs: number; periodBoundariesMs?: number[] };

// ── Injects ───────────────────────────────────────────────────────────────────

/**
 * What the controller can push into a running exercise. Each mirrors something the app can
 * already do by hand: a perimeter inject is an RPAS-perimeter restart, a wind shift is a
 * weather-schedule step, a spot fire is a new ignition, a resource inject is text for the 209.
 */
export type InjectKind = "perimeter" | "spot_fire" | "wind_shift" | "resource" | "note";

export const INJECT_KIND_LABELS: Record<InjectKind, string> = {
  perimeter: "Observed perimeter",
  spot_fire: "Spot fire",
  wind_shift: "Wind shift",
  resource: "Resource availability",
  note: "Controller note",
};

export type InjectStatus = "scheduled" | "delivered" | "withdrawn";

/** GeoJSON geometry in [lng, lat], as the perimeter-override API takes it. */
export interface PerimeterInjectPayload {
  kind: "perimeter";
  /**
   * "drawn": `perimeterGeojson` is the polygon; "truth_run": the truth run's own perimeter at
   * the inject time (a pack's default; delivered from PR3). Absent = "drawn".
   */
  perimeterSource?: "drawn" | "truth_run";
  perimeterGeojson: unknown;
  /** Optional active edges (same format as the perimeter-override request). */
  activeEdges?: unknown | null;
  /** Observation time the players are told (exercise time, ISO 8601 with offset). */
  observedAtIso: string;
}

export interface SpotFireInjectPayload {
  kind: "spot_fire";
  lat: number;
  lng: number;
}

export interface WindShiftInjectPayload {
  kind: "wind_shift";
  windSpeedKmh: number;
  windDirectionDeg: number;
  /** Hidden from the players' forecast until the inject is delivered. */
  hiddenFromForecast: boolean;
}

export interface ResourceInjectPayload {
  kind: "resource";
  /** Plain text, e.g. "Two engines released to a neighbouring county until 18:00". */
  text: string;
}

export interface NoteInjectPayload {
  kind: "note";
  text: string;
}

export type InjectPayload =
  | PerimeterInjectPayload
  | SpotFireInjectPayload
  | WindShiftInjectPayload
  | ResourceInjectPayload
  | NoteInjectPayload;

export interface Inject {
  id: string;
  kind: InjectKind;
  /** When the players receive it, in exercise time (ms since epoch). */
  exerciseTimeMs: number;
  /** When the controller created it, wall clock (ISO 8601). */
  createdAtIso: string;
  title: string;
  payload: InjectPayload;
  status: InjectStatus;
  /** Exercise time at which it was delivered (ms), once it is. */
  deliveredAtMs?: number | null;
}

export interface InjectLog {
  injects: Inject[];
}

// ── Scenario packs (spec §5; stored server-side from PR2) ─────────────────────
// Mirrors api/src/firesim_api/schemas/exercise.py (the file format is camelCase); keep the
// two and docs/api-reference.md ("Exercise packs") in step.

export const PACK_SCHEMA = "firesim.exercise.pack/1";
/** Owner defaults 2026-10-30 (Q12) */
export const PACK_MAX_DURATION_HOURS = 72;
export const PACK_DEFAULT_FORECAST_HORIZON_HOURS = 36;
export const PACK_MAX_BYTES = 2 * 1024 * 1024;

export interface WeatherScriptStep {
  /** Hours from the exercise start at which this step takes over (the first step is at 0). */
  hoursFromStart: number;
  windSpeedKmh: number;
  /** Direction the wind blows from (degrees). */
  windDirectionDeg: number;
  temperatureC: number;
  relativeHumidityPct: number;
  /** First step: 24 h rain before the start (Setup's weather, an FWI input). Later steps: ignored. */
  precipitation24hMm: number;
  /** Rain rate during a later step (mm/h). */
  precipitationMmH?: number;
}

export interface ScenarioPackIgnition {
  lat: number;
  lng: number;
  /** Hours from the exercise start (0 = at the start; later ones are spot-fire injects). */
  hoursFromStart: number;
}

export interface ScenarioPackPeriod {
  /** 1-based operational period number. */
  number: number;
  /** Exercise-time start and end, ISO 8601 with offset. */
  startIso: string;
  endIso: string;
}

/** A pack's scheduled inject: timed from the exercise start (the log gets absolute times). */
export interface ScenarioPackInject {
  id: string;
  kind: InjectKind;
  hoursFromStart: number;
  title: string;
  payload:
    | (Omit<PerimeterInjectPayload, "observedAtIso"> & { observedAtIso?: string })
    | SpotFireInjectPayload
    | WindShiftInjectPayload
    | ResourceInjectPayload
    | NoteInjectPayload;
}

export interface ScenarioPack {
  /** Schema version of this file. */
  schema: typeof PACK_SCHEMA;
  /** 32-hex once stored on a server; any [A-Za-z0-9_-]{1,64} in a file (examples: "example-…"). */
  id: string;
  /** Always "EXERCISE" (owner's hard rule: every exercise artefact is marked). */
  marking: "EXERCISE";
  title: string;
  summary?: string | null;
  /** Open data only; name every source and its licence. */
  sources: string[];
  /** Limits stated on the pack (fuel-grid accuracy, footprint coverage, scripted values...). */
  caveats?: string[];
  /** The exercise's day-1 start (ISO 8601 with offset). */
  startIso: string;
  /** Length of the exercise, ≤ 72 h. */
  durationHours: number;
  /** Players' forecast shows steps up to exercise time plus this (default 36 h). */
  forecastHorizonHours?: number;
  /** Start the run when the pack is loaded (default false: the facilitator presses Run). */
  autoRun?: boolean;
  ignitions: ScenarioPackIgnition[];
  /** Ground truth the engine runs on. */
  weatherScript: WeatherScriptStep[];
  /**
   * What the players are shown as the forecast; omitted = same as the script. When it differs,
   * the divergence is a deliberate exercise design choice and is revealed at the hot-wash.
   */
  playerForecast?: WeatherScriptStep[] | null;
  startingCodes: { ffmc: number | null; dmc: number | null; dc: number | null };
  /** Grass curing (%); null = the app's date-aware default. */
  grassCuringPct?: number | null;
  fuel: { source: "city_lidar" | "ab_wildfire" | "uniform"; uniformFuelType?: string | null };
  periods: ScenarioPackPeriod[];
  /** Injects scheduled in advance; the controller can add more live (PR3). */
  scheduledInjects: ScenarioPackInject[];
  /** Communities shown under another name in this exercise (e.g. a fictional name). */
  placeAliases?: Array<{ place: string; shownAs: string }>;
  /** Pre-placed assets as a GeoJSON FeatureCollection in [lng, lat]; symbol keys of types/incident.ts. */
  prePlacedAssets?: unknown | null;
  notesForFacilitator?: string | null;
}

/** A stored pack's row in the facilitator's list (GET /api/v1/exercise/packs). */
export interface PackSummary {
  id: string;
  title: string;
  startIso: string | null;
  durationHours: number | null;
  sources: string[];
  createdAt: string;
}

export interface ExerciseSession {
  schema: "firesim.exercise.session/1";
  id: string;
  packId: string;
  title: string;
  createdAtIso: string;
  /** Exercise clock at the last save. */
  clock: Pick<ExerciseClockState, "exerciseMs" | "startMs" | "endMs" | "periodBoundariesMs">;
  /** Run IDs produced during the exercise, in order (run archive IDs). */
  runIds: string[];
  injectLog: InjectLog;
  /** 209-WF snapshots per operational period (HTML, with the EXERCISE mark). */
  snapshots209: Array<{ period: number; takenAtExerciseIso: string; html: string }>;
  /** Player decisions the evaluator records (free text, timestamped in exercise time). */
  decisions: Array<{ atExerciseIso: string; role: string; text: string }>;
}
