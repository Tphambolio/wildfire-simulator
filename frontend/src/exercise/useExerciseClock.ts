/**
 * Exercise clock hook (docs/exercise-mode-spec.md §4, PR0): wraps exerciseClockReducer and
 * ticks it from a wall-clock interval while playing. `flagOn` false makes every action a no-op,
 * so with the feature flag off the clock can never be enabled (tested in flag.test.ts).
 */

import { useCallback, useEffect, useReducer, useRef } from "react";
import type { ExerciseClockAction, ExerciseClockState, ExerciseRate } from "../types/exercise";
import { EXERCISE_CLOCK_OFF, exerciseClockReducer } from "./clock";

/** Wall-clock tick period while playing (ms). 250 ms at 12x moves exercise time 3 s a tick. */
export const TICK_MS = 250;

export interface ExerciseClock {
  state: ExerciseClockState;
  enable: (startMs: number, endMs: number, periodBoundariesMs?: number[], at?: number) => void;
  disable: () => void;
  play: () => void;
  pause: () => void;
  togglePlay: () => void;
  setRate: (rate: ExerciseRate) => void;
  seek: (ms: number) => void;
  nextPeriod: () => void;
  setBounds: (startMs: number, endMs: number, periodBoundariesMs?: number[]) => void;
}

export function useExerciseClock(flagOn: boolean, now: () => number = Date.now): ExerciseClock {
  const [state, rawDispatch] = useReducer(exerciseClockReducer, EXERCISE_CLOCK_OFF);
  // The flag is read once per page load, so these callbacks are stable in practice
  const dispatch = useCallback((a: ExerciseClockAction) => {
    if (flagOn) rawDispatch(a);
  }, [flagOn]);

  // Ticking: one interval while playing; the delta is measured, not assumed, so a slow tab
  // (throttled timers) still advances exercise time by the wall time that really passed.
  const lastTickRef = useRef<number | null>(null);
  const playing = state.enabled && state.mode === "playing";
  useEffect(() => {
    if (!playing) {
      lastTickRef.current = null;
      return;
    }
    lastTickRef.current = now();
    const id = setInterval(() => {
      const t = now();
      const last = lastTickRef.current ?? t;
      lastTickRef.current = t;
      rawDispatch({ type: "tick", wallDeltaMs: t - last });
    }, TICK_MS);
    return () => clearInterval(id);
  }, [playing, now]);

  const enable = useCallback(
    (startMs: number, endMs: number, periodBoundariesMs?: number[], at?: number) =>
      dispatch({ type: "enable", startMs, endMs, periodBoundariesMs, at }),
    [dispatch],
  );
  const disable = useCallback(() => dispatch({ type: "disable" }), [dispatch]);
  const play = useCallback(() => dispatch({ type: "play" }), [dispatch]);
  const pause = useCallback(() => dispatch({ type: "pause" }), [dispatch]);
  const togglePlay = useCallback(
    () => dispatch({ type: state.mode === "playing" ? "pause" : "play" }),
    [dispatch, state.mode],
  );
  const setRate = useCallback((rate: ExerciseRate) => dispatch({ type: "setRate", rate }), [dispatch]);
  const seek = useCallback((ms: number) => dispatch({ type: "seek", ms }), [dispatch]);
  const nextPeriod = useCallback(() => dispatch({ type: "nextPeriod" }), [dispatch]);
  const setBounds = useCallback(
    (startMs: number, endMs: number, periodBoundariesMs?: number[]) =>
      dispatch({ type: "setBounds", startMs, endMs, periodBoundariesMs }),
    [dispatch],
  );

  return { state, enable, disable, play, pause, togglePlay, setRate, seek, nextPeriod, setBounds };
}
