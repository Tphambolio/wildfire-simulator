/**
 * Exercise banner (docs/exercise-mode-spec.md §4, §8): always visible while exercise mode is on.
 * It carries the EXERCISE mark, the exercise clock ("Exercise time"), and the controller's clock
 * controls: pause / play, 1x / 4x / 12x, next operational period, exit. Players read the time;
 * the facilitator drives it. Nothing here suggests an evacuation tier or shows a building.
 */

import type { ExerciseClockState, ExerciseRate } from "../types/exercise";
import { EXERCISE_RATES } from "../types/exercise";
import { formatClock, formatDate, formatElapsed, zoneAbbrev } from "../utils/time";

export interface ExerciseBannerProps {
  state: ExerciseClockState;
  onTogglePlay: () => void;
  onSetRate: (rate: ExerciseRate) => void;
  onNextPeriod: () => void;
  onExit: () => void;
}

export default function ExerciseBanner({ state, onTogglePlay, onSetRate, onNextPeriod, onExit }: ExerciseBannerProps) {
  if (!state.enabled || state.exerciseMs === null || state.startMs === null || state.endMs === null) return null;
  const t = new Date(state.exerciseMs);
  const elapsedH = (state.exerciseMs - state.startMs) / 3_600_000;
  const atEnd = state.exerciseMs >= state.endMs;
  const playing = state.mode === "playing";
  const nextLabel = state.periodBoundariesMs.some((b) => b > (state.exerciseMs as number)) ? "Next period" : "End";
  return (
    <div className="exercise-banner" role="region" aria-label="Exercise mode" data-testid="exercise-banner">
      <span className="exercise-mark" aria-label="This is an exercise">EXERCISE</span>
      <span className="exercise-time" aria-live="off" title="Exercise time: the scenario clock, not the wall clock">
        <span className="exercise-time-label">Exercise time</span>
        <span className="exercise-time-clock" data-testid="exercise-time">
          {formatClock(t)}
          <small>{zoneAbbrev(t)}</small>
        </span>
        <span className="exercise-time-date">{formatDate(t)}</span>
        <span className="exercise-time-elapsed">{formatElapsed(elapsedH)}</span>
        <span className={`exercise-state${playing ? " playing" : ""}`}>{playing ? `Running ${state.rate}×` : atEnd ? "At end" : "Paused"}</span>
      </span>
      <span className="exercise-controls" role="group" aria-label="Exercise clock controls">
        <button type="button" className="btn-control exercise-btn" onClick={onTogglePlay} aria-label={playing ? "Pause exercise clock" : "Play exercise clock"}>
          {playing ? "Pause" : "Play"}
        </button>
        <span className="exercise-rates" role="group" aria-label="Exercise clock speed">
          {EXERCISE_RATES.map((r) => (
            <button
              key={r}
              type="button"
              className={`btn-control exercise-btn exercise-rate${state.rate === r ? " active" : ""}`}
              aria-pressed={state.rate === r}
              onClick={() => onSetRate(r)}
              title={`${r} exercise second${r === 1 ? "" : "s"} per real second`}
            >
              {r}×
            </button>
          ))}
        </span>
        <button type="button" className="btn-control exercise-btn" onClick={onNextPeriod} disabled={atEnd} title="Jump the exercise clock to the next operational period boundary">
          {nextLabel} ▸
        </button>
        <button type="button" className="btn-control exercise-btn exercise-exit" onClick={onExit} title="Leave exercise mode (the run and its timeline stay)">
          Exit exercise
        </button>
      </span>
    </div>
  );
}
