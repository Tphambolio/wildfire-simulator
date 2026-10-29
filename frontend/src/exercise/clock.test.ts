import { describe, expect, it } from "vitest";
import {
  DEFAULT_EXERCISE_HOURS,
  EXERCISE_CLOCK_OFF,
  exerciseClockReducer as r,
  frameIndexForExerciseTime,
  regularBoundaries,
} from "./clock";
import type { ExerciseClockState } from "../types/exercise";

const H = 3_600_000;
const START = Date.UTC(2026, 6, 15, 16, 0); // 2026-07-15 10:00 MDT
const END = START + 72 * H;
const BOUNDS = [START + 12 * H, START + 24 * H, START + 36 * H];

function on(at = START): ExerciseClockState {
  return r(EXERCISE_CLOCK_OFF, { type: "enable", startMs: START, endMs: END, periodBoundariesMs: BOUNDS, at });
}

describe("exerciseClockReducer", () => {
  it("is off by default and ignores play, tick, seek and nextPeriod while off", () => {
    expect(EXERCISE_CLOCK_OFF.enabled).toBe(false);
    expect(r(EXERCISE_CLOCK_OFF, { type: "play" })).toBe(EXERCISE_CLOCK_OFF);
    expect(r(EXERCISE_CLOCK_OFF, { type: "tick", wallDeltaMs: 1000 })).toBe(EXERCISE_CLOCK_OFF);
    expect(r(EXERCISE_CLOCK_OFF, { type: "seek", ms: START })).toBe(EXERCISE_CLOCK_OFF);
    expect(r(EXERCISE_CLOCK_OFF, { type: "nextPeriod" })).toBe(EXERCISE_CLOCK_OFF);
    expect(r(EXERCISE_CLOCK_OFF, { type: "setBounds", startMs: START, endMs: END })).toBe(EXERCISE_CLOCK_OFF);
  });

  it("enable starts paused at the start, with boundaries sorted and inside the span", () => {
    const s = r(EXERCISE_CLOCK_OFF, {
      type: "enable", startMs: START, endMs: END,
      periodBoundariesMs: [START + 36 * H, START, START + 12 * H, END, START + 12 * H, START + 200 * H],
    });
    expect(s.enabled).toBe(true);
    expect(s.mode).toBe("paused");
    expect(s.exerciseMs).toBe(START);
    expect(s.periodBoundariesMs).toEqual([START + 12 * H, START + 36 * H]);
  });

  it("enable with a bad span is ignored; `at` is clamped into the span", () => {
    expect(r(EXERCISE_CLOCK_OFF, { type: "enable", startMs: START, endMs: START })).toBe(EXERCISE_CLOCK_OFF);
    expect(r(EXERCISE_CLOCK_OFF, { type: "enable", startMs: START, endMs: END, at: END + H }).exerciseMs).toBe(END);
    expect(r(EXERCISE_CLOCK_OFF, { type: "enable", startMs: START, endMs: END, at: START - H }).exerciseMs).toBe(START);
  });

  it("play then tick advances exercise time by rate x wall time; pause stops it", () => {
    let s = r(on(), { type: "play" });
    expect(s.mode).toBe("playing");
    s = r(s, { type: "tick", wallDeltaMs: 1000 });
    expect(s.exerciseMs).toBe(START + 1000);
    s = r(s, { type: "setRate", rate: 12 });
    s = r(s, { type: "tick", wallDeltaMs: 1000 });
    expect(s.exerciseMs).toBe(START + 13_000);
    s = r(s, { type: "pause" });
    expect(s.mode).toBe("paused");
    expect(r(s, { type: "tick", wallDeltaMs: 5000 })).toBe(s);
  });

  it("a tick never moves the clock backwards or past the end, and the end pauses it", () => {
    let s = r(on(END - 1000), { type: "play" });
    expect(r(s, { type: "tick", wallDeltaMs: 0 })).toBe(s);
    expect(r(s, { type: "tick", wallDeltaMs: -500 })).toBe(s);
    s = r(s, { type: "tick", wallDeltaMs: 60_000 });
    expect(s.exerciseMs).toBe(END);
    expect(s.mode).toBe("paused");
    // Play at the end restarts from the beginning (as the timeline does)
    expect(r(s, { type: "play" }).exerciseMs).toBe(START);
  });

  it("setRate accepts only 1, 4 or 12 and keeps the rate across disable/enable", () => {
    let s = r(on(), { type: "setRate", rate: 4 });
    expect(s.rate).toBe(4);
    expect(r(s, { type: "setRate", rate: 7 as unknown as 4 })).toBe(s);
    s = r(s, { type: "disable" });
    expect(s.enabled).toBe(false);
    expect(s.exerciseMs).toBeNull();
    expect(s.rate).toBe(4);
    expect(r(s, { type: "disable" })).toBe(s);
  });

  it("seek clamps into the span and returns the same state for a no-op", () => {
    const s = on(START + 5 * H);
    expect(r(s, { type: "seek", ms: START + 10 * H }).exerciseMs).toBe(START + 10 * H);
    expect(r(s, { type: "seek", ms: START - 10 * H }).exerciseMs).toBe(START);
    expect(r(s, { type: "seek", ms: END + 10 * H }).exerciseMs).toBe(END);
    expect(r(s, { type: "seek", ms: START + 5 * H })).toBe(s);
    expect(r(s, { type: "seek", ms: NaN })).toBe(s);
  });

  it("nextPeriod jumps to the next boundary after now, then to the end, and pauses", () => {
    let s = r(on(START + 11 * H), { type: "play" });
    s = r(s, { type: "nextPeriod" });
    expect(s.exerciseMs).toBe(START + 12 * H);
    expect(s.mode).toBe("paused");
    // Exactly on a boundary goes to the one after it
    s = r(s, { type: "nextPeriod" });
    expect(s.exerciseMs).toBe(START + 24 * H);
    s = r(s, { type: "nextPeriod" });
    s = r(s, { type: "nextPeriod" });
    expect(s.exerciseMs).toBe(END);
    expect(r(s, { type: "nextPeriod" }).exerciseMs).toBe(END);
  });

  it("setBounds keeps the exercise time, clamps it into the new span and is a no-op when unchanged", () => {
    const s = on(START + 30 * H);
    const same = r(s, { type: "setBounds", startMs: START, endMs: END, periodBoundariesMs: BOUNDS });
    expect(same).toBe(s);
    const shorter = r(s, { type: "setBounds", startMs: START, endMs: START + 24 * H });
    expect(shorter.exerciseMs).toBe(START + 24 * H);
    expect(shorter.periodBoundariesMs).toEqual([START + 12 * H]);
    expect(r(s, { type: "setBounds", startMs: END, endMs: START })).toBe(s);
  });
});

describe("frameIndexForExerciseTime", () => {
  const frames = [0, 0.5, 1, 1.5, 2].map((time_hours) => ({ time_hours }));
  it("returns the last frame at or before the exercise time", () => {
    expect(frameIndexForExerciseTime(frames, START, START)).toBe(0);
    expect(frameIndexForExerciseTime(frames, START, START + 0.75 * H)).toBe(1);
    expect(frameIndexForExerciseTime(frames, START, START + 1 * H)).toBe(2);
    expect(frameIndexForExerciseTime(frames, START, START + 10 * H)).toBe(4);
  });
  it("shows the first frame before the run starts and -1 without frames or a start", () => {
    expect(frameIndexForExerciseTime(frames, START, START - H)).toBe(0);
    expect(frameIndexForExerciseTime([], START, START)).toBe(-1);
    expect(frameIndexForExerciseTime(frames, null, START)).toBe(-1);
    expect(frameIndexForExerciseTime(frames, START, null)).toBe(-1);
  });
});

describe("regularBoundaries", () => {
  it("places boundaries every N hours strictly inside the span", () => {
    expect(regularBoundaries(START, START + 36 * H, 12)).toEqual([START + 12 * H, START + 24 * H]);
    expect(regularBoundaries(START, START + 12 * H, 12)).toEqual([]);
    expect(regularBoundaries(START, END, 0)).toEqual([]);
    expect(DEFAULT_EXERCISE_HOURS).toBe(72);
  });
});
