import { describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { EXERCISE_ENV_VAR, EXERCISE_QUERY_PARAM, exerciseFlagFrom } from "./flag";
import { TICK_MS, useExerciseClock } from "./useExerciseClock";

const START = Date.UTC(2026, 6, 15, 16, 0);
const END = START + 24 * 3_600_000;

describe("exercise feature flag", () => {
  it("is off by default (no env, no query)", () => {
    expect(exerciseFlagFrom(undefined, "")).toBe(false);
    expect(exerciseFlagFrom(null, null)).toBe(false);
    expect(exerciseFlagFrom("", "?other=1")).toBe(false);
  });

  it(`turns on with ${EXERCISE_ENV_VAR}=1 only`, () => {
    expect(exerciseFlagFrom("1", "")).toBe(true);
    expect(exerciseFlagFrom("0", "")).toBe(false);
    expect(exerciseFlagFrom("true", "")).toBe(false);
    expect(exerciseFlagFrom("yes", "")).toBe(false);
  });

  it(`turns on with ?${EXERCISE_QUERY_PARAM}=1 only`, () => {
    expect(exerciseFlagFrom(undefined, "?exercise=1")).toBe(true);
    expect(exerciseFlagFrom(undefined, "?foo=bar&exercise=1")).toBe(true);
    expect(exerciseFlagFrom(undefined, "?exercise=0")).toBe(false);
    expect(exerciseFlagFrom(undefined, "?exercise=true")).toBe(false);
    expect(exerciseFlagFrom(undefined, "?exercise")).toBe(false);
  });
});

describe("useExerciseClock gating and ticking", () => {
  it("with the flag off, enable and every other action are no-ops", () => {
    const { result } = renderHook(() => useExerciseClock(false));
    act(() => result.current.enable(START, END));
    expect(result.current.state.enabled).toBe(false);
    act(() => {
      result.current.play();
      result.current.seek(START);
      result.current.setRate(12);
    });
    expect(result.current.state.enabled).toBe(false);
    expect(result.current.state.rate).toBe(1);
  });

  it("with the flag on, enable then play advances exercise time with wall time x rate", () => {
    vi.useFakeTimers();
    try {
      let wall = 1_000_000;
      const now = () => wall;
      const { result } = renderHook(() => useExerciseClock(true, now));
      act(() => result.current.enable(START, END));
      expect(result.current.state.enabled).toBe(true);
      expect(result.current.state.exerciseMs).toBe(START);
      act(() => result.current.setRate(4));
      act(() => result.current.play());
      act(() => {
        wall += TICK_MS;
        vi.advanceTimersByTime(TICK_MS);
      });
      expect(result.current.state.exerciseMs).toBe(START + 4 * TICK_MS);
      act(() => result.current.pause());
      act(() => {
        wall += 10 * TICK_MS;
        vi.advanceTimersByTime(10 * TICK_MS);
      });
      expect(result.current.state.exerciseMs).toBe(START + 4 * TICK_MS);
      act(() => result.current.disable());
      expect(result.current.state.enabled).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });
});
