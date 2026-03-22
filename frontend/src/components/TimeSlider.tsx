/** Time slider with playback controls for animating fire progression. */

import { useEffect, useRef, useState } from "react";
import type { SimulationFrame } from "../types/simulation";

interface TimeSliderProps {
  frames: SimulationFrame[];
  currentIndex: number;
  onIndexChange: (index: number) => void;
}

const SPEEDS = [
  { label: "0.5×", ms: 1600 },
  { label: "1×",   ms: 800 },
  { label: "2×",   ms: 400 },
  { label: "4×",   ms: 200 },
];
const DEFAULT_SPEED_IDX = 1; // 1×

export default function TimeSlider({
  frames,
  currentIndex,
  onIndexChange,
}: TimeSliderProps) {
  const [isPlaying, setIsPlaying] = useState(false);
  const [speedIdx, setSpeedIdx] = useState(DEFAULT_SPEED_IDX);

  // Refs so the interval callback always sees the latest values
  const currentIndexRef = useRef(currentIndex);
  const framesLengthRef = useRef(frames.length);
  const onIndexChangeRef = useRef(onIndexChange);
  currentIndexRef.current = currentIndex;
  framesLengthRef.current = frames.length;
  onIndexChangeRef.current = onIndexChange;

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

  // Stop playback when frames disappear (new simulation started)
  useEffect(() => {
    if (frames.length < 2) setIsPlaying(false);
  }, [frames.length]);

  if (frames.length < 2) return null;

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

  return (
    <div className="time-slider">
      {/* Step back */}
      <button
        className="ts-btn ts-step"
        onClick={handleStepBack}
        disabled={currentIndex === 0}
        title="Previous frame"
      >
        &#9664;
      </button>

      {/* Play / Pause */}
      <button
        className={`ts-btn ts-play${isPlaying ? " playing" : ""}`}
        onClick={handlePlayPause}
        title={isPlaying ? "Pause" : "Play animation"}
      >
        {isPlaying ? "⏸" : "▶"}
      </button>

      {/* Step forward */}
      <button
        className="ts-btn ts-step"
        onClick={handleStepForward}
        disabled={currentIndex >= frames.length - 1}
        title="Next frame"
      >
        &#9654;
      </button>

      {/* Elapsed time */}
      <span className="time-label ts-current" title="Elapsed simulation time">
        T+{currentFrame?.time_hours.toFixed(1) ?? "0"}h
      </span>

      {/* Scrubber */}
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
        title={`Frame ${currentIndex + 1} of ${frames.length}`}
      />

      {/* Total duration */}
      <span className="time-label" title="Total simulation duration">
        {totalFrame?.time_hours.toFixed(1)}h
      </span>

      {/* Speed selector */}
      <div className="ts-speeds">
        {SPEEDS.map((s, i) => (
          <button
            key={s.label}
            className={`ts-btn ts-speed${speedIdx === i ? " active" : ""}`}
            onClick={() => setSpeedIdx(i)}
            title={`Playback speed: ${s.label}`}
          >
            {s.label}
          </button>
        ))}
      </div>

      {/* Frame counter */}
      <span className="time-label ts-frame-count">
        {currentIndex + 1}/{frames.length}
      </span>
    </div>
  );
}
