/**
 * Timeline (design spec §2.3): scrub and play the run by wall-clock time in America/Edmonton,
 * with T+ elapsed time as the secondary label.
 */

import { useEffect, useRef, useState } from "react";
import type { BurningPeriod, SimulationFrame } from "../types/simulation";
import { clockAt, formatClock, formatElapsed, zoneAbbrev } from "../utils/time";
import { formatBurningPeriod, inBurningPeriod, offPeriods } from "../utils/skillOptions";
import Badge from "./Badge";

interface TimeSliderProps {
  frames: SimulationFrame[];
  currentIndex: number;
  onIndexChange: (index: number) => void;
  /** Scenario start (when the run was started). Without it, only T+ labels are shown. */
  scenarioStart?: Date | null;
  /** Burning period of the run: the hours outside it are shaded (no spread modelled) */
  burningPeriod?: BurningPeriod | null;
}

const SPEEDS = [
  { label: "0.5×", ms: 1600 },
  { label: "1×",   ms: 800 },
  { label: "2×",   ms: 400 },
  { label: "4×",   ms: 200 },
];
const DEFAULT_SPEED_IDX = 1; // 1×
const MAX_TICKS = 6;

/** Position (0-100 %) of `hours` along the track, which spaces frames evenly by index. */
function hoursToPct(frames: SimulationFrame[], hours: number): number {
  const n = frames.length;
  if (n < 2) return 0;
  if (hours <= frames[0].time_hours) return 0;
  for (let i = 1; i < n; i++) {
    const a = frames[i - 1].time_hours;
    const b = frames[i].time_hours;
    if (hours <= b) return ((i - 1 + (b > a ? (hours - a) / (b - a) : 1)) / (n - 1)) * 100;
  }
  return 100;
}

/** Frame indices to label along the track: first, last and evenly spaced ones between. */
function tickIndices(n: number, maxTicks = MAX_TICKS): number[] {
  if (n < 2) return n === 1 ? [0] : [];
  const step = Math.max(1, Math.ceil((n - 1) / (maxTicks - 1)));
  const out: number[] = [];
  for (let i = 0; i < n - 1; i += step) out.push(i);
  // keep the last tick clear of the one before it
  if (out.length > 1 && n - 1 - out[out.length - 1] < step / 2) out.pop();
  out.push(n - 1);
  return out;
}

export default function TimeSlider({
  frames,
  currentIndex,
  onIndexChange,
  scenarioStart = null,
  burningPeriod = null,
}: TimeSliderProps) {
  const [isPlaying, setIsPlaying] = useState(false);
  const [speedIdx, setSpeedIdx] = useState(DEFAULT_SPEED_IDX);

  // Refs so the interval callback always sees the latest values
  const currentIndexRef = useRef(currentIndex);
  const framesLengthRef = useRef(frames.length);
  const onIndexChangeRef = useRef(onIndexChange);
  useEffect(() => {
    currentIndexRef.current = currentIndex;
    framesLengthRef.current = frames.length;
    onIndexChangeRef.current = onIndexChange;
  });

  // Playback interval
  useEffect(() => {
    if (!isPlaying) return;

    const id = setInterval(() => {
      const next = currentIndexRef.current + 1;
      if (next >= framesLengthRef.current) {
        setIsPlaying(false);
      } else {
        onIndexChangeRef.current(next);
      }
    }, SPEEDS[speedIdx].ms);

    return () => clearInterval(id);
  }, [isPlaying, speedIdx]);

  // Stop playback when frames disappear (new simulation started); adjusting state during
  // render is React's recommended pattern for this (no extra effect pass)
  const [prevFrameCount, setPrevFrameCount] = useState(frames.length);
  if (frames.length !== prevFrameCount) {
    setPrevFrameCount(frames.length);
    if (frames.length < 2 && isPlaying) setIsPlaying(false);
  }

  if (frames.length < 2) {
    return (
      <div className="time-slider time-slider-empty">
        <span className="hint-sm">Timeline appears after a run</span>
      </div>
    );
  }

  const currentFrame = frames[currentIndex];
  const totalFrame = frames[frames.length - 1];
  const pct = frames.length > 1 ? (currentIndex / (frames.length - 1)) * 100 : 0;

  const handlePlayPause = () => {
    if (isPlaying) {
      setIsPlaying(false);
    } else {
      // Restart from beginning if at the end
      if (currentIndex >= frames.length - 1) onIndexChange(0);
      setIsPlaying(true);
    }
  };

  const handleStepBack = () => {
    setIsPlaying(false);
    onIndexChange(Math.max(0, currentIndex - 1));
  };

  const handleStepForward = () => {
    setIsPlaying(false);
    onIndexChange(Math.min(frames.length - 1, currentIndex + 1));
  };

  // Day boundary markers for multi-day scenarios
  const maxHours = totalFrame?.time_hours ?? 0;
  const dayBoundaries: number[] = [];
  if (maxHours > 24) {
    for (let d = 24; d < maxHours; d += 24) {
      dayBoundaries.push(d);
    }
  }

  const hours = currentFrame?.time_hours ?? 0;
  const now = scenarioStart ? clockAt(scenarioStart, hours) : null;
  const elapsed = maxHours > 24 && currentFrame?.day
    ? `D${currentFrame.day} ${formatElapsed(hours - (currentFrame.day - 1) * 24)}`
    : formatElapsed(hours);
  const valueText = now ? `${formatClock(now)} ${zoneAbbrev(now)}, ${formatElapsed(hours)}` : formatElapsed(hours);
  const ticks = tickIndices(frames.length);
  // Hours outside the burning period (no spread modelled), shaded on the track
  const bpLabel = burningPeriod ? formatBurningPeriod(burningPeriod) : "";
  const offBands = scenarioStart && burningPeriod
    ? offPeriods(scenarioStart.getTime(), maxHours, burningPeriod)
    : [];
  const outside = !!(scenarioStart && burningPeriod && !inBurningPeriod(scenarioStart.getTime(), hours, burningPeriod));

  return (
    <div className="time-slider">
      <div className="ts-controls">
        <button
          className="ts-btn ts-step"
          onClick={handleStepBack}
          disabled={currentIndex === 0}
          title="Previous frame"
          aria-label="Previous frame"
        >
          &#9664;
        </button>
        <button
          className={`ts-btn ts-play${isPlaying ? " playing" : ""}`}
          onClick={handlePlayPause}
          title={isPlaying ? "Pause" : "Play animation"}
          aria-label={isPlaying ? "Pause" : "Play"}
        >
          {isPlaying ? "⏸" : "▶"}
        </button>
        <button
          className="ts-btn ts-step"
          onClick={handleStepForward}
          disabled={currentIndex >= frames.length - 1}
          title="Next frame"
          aria-label="Next frame"
        >
          &#9654;
        </button>
      </div>

      {/* Current time: clock first, T+ second */}
      <div className="ts-now" title="Time of the frame shown on the map">
        {now ? (
          <span className="ts-now-clock">
            {formatClock(now)}
            <small>{zoneAbbrev(now)}</small>
          </span>
        ) : null}
        <span className={now ? "ts-now-elapsed" : "ts-now-clock"}>{elapsed}</span>
        {outside && (
          <Badge tone="warn" className="ts-now-off" tip={`Outside the burning period ${bpLabel}: no spread is modelled.`}>
            No spread
          </Badge>
        )}
      </div>

      <div className="ts-range-wrap">
        <input
          type="range"
          min={0}
          max={frames.length - 1}
          value={currentIndex}
          onChange={(e) => {
            setIsPlaying(false);
            onIndexChange(Number(e.target.value));
          }}
          className="ts-range"
          style={{ "--pct": `${pct}%` } as React.CSSProperties}
          aria-label="Timeline"
          aria-valuetext={outside ? `${valueText}, no spread (outside the burning period ${bpLabel})` : valueText}
          title={`Frame ${currentIndex + 1} of ${frames.length}`}
        />
        {offBands.map(([a, b]) => {
          const left = hoursToPct(frames, a);
          const width = hoursToPct(frames, b) - left;
          return (
            <div
              key={`off-${a}`}
              className="ts-off-band"
              style={{ left: `${left}%`, width: `${width}%` }}
              title={`Outside the burning period ${bpLabel}: no spread`}
              aria-hidden="true"
            />
          );
        })}
        {offBands.length > 0 && (
          <span className="visually-hidden">
            {`Burning period ${bpLabel}: no spread is modelled outside it (shaded).`}
          </span>
        )}
        {dayBoundaries.map((d) => {
          const tickPct = (d / maxHours) * 100;
          return (
            <div
              key={d}
              className="ts-day-tick"
              style={{ left: `${tickPct}%` }}
              title={`Day ${d / 24 + 1} starts`}
            >
              <span className="ts-day-tick-label">D{d / 24 + 1}</span>
            </div>
          );
        })}
        <div className="ts-ticks" aria-hidden="true">
          {ticks.map((i, k) => {
            const h = frames[i].time_hours;
            const edge = k === 0 ? " first" : k === ticks.length - 1 ? " last" : "";
            return (
              <span
                key={i}
                className={`ts-tick${edge}`}
                style={{ left: `${(i / (frames.length - 1)) * 100}%` }}
              >
                {scenarioStart && <span className="ts-tick-clock">{formatClock(clockAt(scenarioStart, h))}</span>}
                <span className="ts-tick-elapsed">{formatElapsed(h)}</span>
              </span>
            );
          })}
        </div>
      </div>

      <div className="ts-speeds" role="group" aria-label="Playback speed">
        {SPEEDS.map((s, i) => (
          <button
            key={s.label}
            className={`ts-btn ts-speed${speedIdx === i ? " active" : ""}`}
            aria-pressed={speedIdx === i}
            onClick={() => setSpeedIdx(i)}
            title={`Playback speed: ${s.label}`}
          >
            {s.label}
          </button>
        ))}
      </div>

      <span className="time-label ts-frame-count" title="Frame">
        {currentIndex + 1}/{frames.length}
      </span>
    </div>
  );
}
