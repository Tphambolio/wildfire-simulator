/**
 * Exercise clock (docs/exercise-mode-spec.md §4): a controller-driven replay clock decoupled
 * from wall time. It pauses, plays at 1x / 4x / 12x and jumps to the next operational period.
 * Players see it as "Exercise time"; the timeline and the map follow it instead of the frame
 * index. Pure reducer, tested in clock.test.ts; the ticking lives in useExerciseClock.ts.
 */

import type { ExerciseClockAction, ExerciseClockState, ExerciseRate } from "../types/exercise";
import { EXERCISE_RATES } from "../types/exercise";

export const DEFAULT_EXERCISE_HOURS = 72;
export const DEFAULT_RATE: ExerciseRate = 1;

export const EXERCISE_CLOCK_OFF: ExerciseClockState = {
  enabled: false,
  mode: "paused",
  rate: DEFAULT_RATE,
  exerciseMs: null,
  startMs: null,
  endMs: null,
  periodBoundariesMs: [],
};

function clamp(ms: number, start: number, end: number): number {
  return Math.min(end, Math.max(start, ms));
}

/** Boundaries strictly inside (start, end), sorted and unique. */
function cleanBoundaries(bounds: number[] | undefined, start: number, end: number): number[] {
  if (!bounds?.length) return [];
  return Array.from(new Set(bounds.filter((b) => Number.isFinite(b) && b > start && b < end))).sort((a, b) => a - b);
}

export function isExerciseRate(n: number): n is ExerciseRate {
  return (EXERCISE_RATES as readonly number[]).includes(n);
}

/** Pure state transitions. Every change is clamped to [startMs, endMs]; reaching the end pauses. */
export function exerciseClockReducer(s: ExerciseClockState, a: ExerciseClockAction): ExerciseClockState {
  switch (a.type) {
    case "enable": {
      if (!(Number.isFinite(a.startMs) && Number.isFinite(a.endMs)) || a.endMs <= a.startMs) return s;
      const at = a.at ?? a.startMs;
      return {
        enabled: true,
        mode: "paused",
        rate: s.rate,
        startMs: a.startMs,
        endMs: a.endMs,
        exerciseMs: clamp(at, a.startMs, a.endMs),
        periodBoundariesMs: cleanBoundaries(a.periodBoundariesMs, a.startMs, a.endMs),
      };
    }
    case "disable":
      return s.enabled ? { ...EXERCISE_CLOCK_OFF, rate: s.rate } : s;
    case "play": {
      if (!s.enabled || s.exerciseMs === null || s.endMs === null) return s;
      if (s.mode === "playing") return s;
      // Play at the end starts again from the beginning, as the timeline does
      const exerciseMs = s.exerciseMs >= s.endMs ? (s.startMs ?? s.exerciseMs) : s.exerciseMs;
      return { ...s, mode: "playing", exerciseMs };
    }
    case "pause":
      return s.enabled && s.mode === "playing" ? { ...s, mode: "paused" } : s;
    case "setRate":
      return isExerciseRate(a.rate) && a.rate !== s.rate ? { ...s, rate: a.rate } : s;
    case "tick": {
      if (!s.enabled || s.mode !== "playing" || s.exerciseMs === null || s.startMs === null || s.endMs === null) return s;
      if (!(a.wallDeltaMs > 0)) return s;
      const next = clamp(s.exerciseMs + a.wallDeltaMs * s.rate, s.startMs, s.endMs);
      return next >= s.endMs ? { ...s, exerciseMs: s.endMs, mode: "paused" } : { ...s, exerciseMs: next };
    }
    case "seek": {
      if (!s.enabled || s.startMs === null || s.endMs === null || !Number.isFinite(a.ms)) return s;
      const ms = clamp(a.ms, s.startMs, s.endMs);
      return ms === s.exerciseMs ? s : { ...s, exerciseMs: ms };
    }
    case "nextPeriod": {
      if (!s.enabled || s.exerciseMs === null || s.endMs === null) return s;
      const next = s.periodBoundariesMs.find((b) => b > (s.exerciseMs as number)) ?? s.endMs;
      return { ...s, exerciseMs: next, mode: "paused" };
    }
    case "setBounds": {
      if (!s.enabled || !(Number.isFinite(a.startMs) && Number.isFinite(a.endMs)) || a.endMs <= a.startMs) return s;
      const exerciseMs = clamp(s.exerciseMs ?? a.startMs, a.startMs, a.endMs);
      const periodBoundariesMs = cleanBoundaries(a.periodBoundariesMs ?? s.periodBoundariesMs, a.startMs, a.endMs);
      const sameBounds = s.startMs === a.startMs && s.endMs === a.endMs && s.exerciseMs === exerciseMs
        && periodBoundariesMs.length === s.periodBoundariesMs.length
        && periodBoundariesMs.every((b, i) => b === s.periodBoundariesMs[i]);
      // Unchanged bounds return the same state, so an effect that re-sends them cannot loop
      if (sameBounds) return s;
      return { ...s, startMs: a.startMs, endMs: a.endMs, exerciseMs, periodBoundariesMs };
    }
  }
}

/**
 * The frame shown at an exercise time: the last frame at or before it (the fire as modelled
 * up to then), the first frame before the run starts. -1 without frames or a start.
 */
export function frameIndexForExerciseTime(
  frames: ReadonlyArray<{ time_hours: number }>,
  scenarioStartMs: number | null,
  exerciseMs: number | null,
): number {
  if (!frames.length || scenarioStartMs === null || exerciseMs === null) return -1;
  const hours = (exerciseMs - scenarioStartMs) / 3_600_000;
  let idx = 0;
  for (let i = 0; i < frames.length; i++) {
    if (frames[i].time_hours <= hours + 1e-9) idx = i;
    else break;
  }
  return idx;
}

/** Operational-period boundaries every `hours` from the start, inside (start, end). */
export function regularBoundaries(startMs: number, endMs: number, hours = 12): number[] {
  const out: number[] = [];
  const step = hours * 3_600_000;
  if (!(step > 0)) return out;
  for (let t = startMs + step; t < endMs; t += step) out.push(t);
  return out;
}
