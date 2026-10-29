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

// ── Scenario packs and sessions (spec §9; stored server-side from PR2 on) ───────

export interface WeatherScriptStep {
  /** Hours from the exercise start at which this step takes over. */
  hoursFromStart: number;
  windSpeedKmh: number;
  windDirectionDeg: number;
  temperatureC: number;
  relativeHumidityPct: number;
  precipitation24hMm: number;
}

export interface ScenarioPackIgnition {
  lat: number;
  lng: number;
  /** Hours from the exercise start (0 = at the start). */
  hoursFromStart: number;
}

export interface ScenarioPackPeriod {
  /** 1-based operational period number. */
  number: number;
  /** Exercise-time start and end, ISO 8601 with offset. */
  startIso: string;
  endIso: string;
}

export interface ScenarioPack {
  /** Schema version of this file. */
  schema: "firesim.exercise.pack/1";
  id: string;
  title: string;
  /** Open data only; name every source and its licence. */
  sources: string[];
  /** The exercise's day-1 start (ISO 8601 with offset). */
  startIso: string;
  durationHours: number;
  ignitions: ScenarioPackIgnition[];
  /** Ground truth the engine runs on. */
  weatherScript: WeatherScriptStep[];
  /**
   * What the players are shown as the forecast; omitted = same as the script. When it differs,
   * the divergence is a deliberate exercise design choice and is revealed at the hot-wash.
   */
  playerForecast?: WeatherScriptStep[] | null;
  startingCodes: { ffmc: number | null; dmc: number | null; dc: number | null };
  fuel: { source: "city_lidar" | "ab_wildfire" | "uniform"; uniformFuelType?: string | null };
  periods: ScenarioPackPeriod[];
  /** Injects scheduled in advance; the controller can add more live. */
  scheduledInjects: Omit<Inject, "status" | "deliveredAtMs" | "createdAtIso">[];
  /** Pre-placed assets as a GeoJSON FeatureCollection in [lng, lat]; symbol keys of types/incident.ts. */
  prePlacedAssets?: unknown | null;
  notesForFacilitator?: string | null;
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
