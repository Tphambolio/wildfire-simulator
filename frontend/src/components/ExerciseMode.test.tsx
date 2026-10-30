/**
 * Exercise mode UI gating (docs/exercise-mode-spec.md PR0): with no flag nothing of exercise
 * mode is rendered; with it, the toggle, banner, "Exercise time" readout and inject log appear.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import TopBar from "./TopBar";
import TimeSlider from "./TimeSlider";
import ExerciseBanner from "./ExerciseBanner";
import InjectLogPanel from "./InjectLogPanel";
import ExercisePackPanel from "./ExercisePackPanel";
import { EXERCISE_CLOCK_OFF, exerciseClockReducer } from "../exercise/clock";
import { EMPTY_INJECT_LOG, addInject } from "../exercise/injects";

const START = Date.UTC(2026, 6, 15, 16, 0); // 10:00 MDT
const H = 3_600_000;
const frames = [0, 1, 2, 3].map((time_hours) => ({
  time_hours, perimeter: [], area_ha: time_hours * 10, head_ros_m_min: 1, max_hfi_kw_m: 100, fire_type: "SURFACE", flame_length_m: 0.5, fuel_breakdown: {},
}));

function topBar(exercise?: { on: boolean; onToggle: () => void }) {
  return render(
    <TopBar incidentName={null} incidentSub={null} activeTab="simulation" onTabChange={() => {}} status={null} exercise={exercise} />,
  );
}

describe("TopBar exercise toggle", () => {
  it("renders nothing of exercise mode without the prop (flag off)", () => {
    topBar();
    expect(screen.queryByTestId("exercise-toggle")).toBeNull();
    expect(screen.queryByText(/exercise/i)).toBeNull();
  });

  it("renders the toggle with the prop and reports its state", () => {
    const onToggle = vi.fn();
    topBar({ on: false, onToggle });
    const btn = screen.getByTestId("exercise-toggle");
    expect(btn).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(btn);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });
});

describe("TimeSlider in exercise mode", () => {
  it("shows the wall-clock readout and its own play controls by default", () => {
    render(<TimeSlider frames={frames} currentIndex={1} onIndexChange={() => {}} scenarioStart={new Date(START)} />);
    expect(screen.queryByTestId("ts-exercise-label")).toBeNull();
    expect(screen.getByRole("button", { name: "Play" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Playback speed" })).toBeInTheDocument();
  });

  it("shows 'Exercise time' at the exercise instant and hides its play controls", () => {
    const onIndexChange = vi.fn();
    render(
      <TimeSlider frames={frames} currentIndex={1} onIndexChange={onIndexChange} scenarioStart={new Date(START)} exerciseTimeMs={START + 1.5 * H} />,
    );
    expect(screen.getByTestId("ts-exercise-label")).toHaveTextContent("Exercise time");
    // 10:00 MDT + 1:30 = 11:30, between frames 1 and 2
    expect(screen.getByText("11:30")).toBeInTheDocument();
    expect(screen.getByText("T+1:30")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Play" })).toBeNull();
    expect(screen.queryByRole("group", { name: "Playback speed" })).toBeNull();
    const slider = screen.getByRole("slider", { name: "Timeline" });
    expect(slider).toHaveAttribute("aria-valuetext", expect.stringMatching(/^Exercise time 11:30 MDT, T\+1:30/));
    fireEvent.change(slider, { target: { value: "3" } });
    expect(onIndexChange).toHaveBeenCalledWith(3);
  });
});

describe("ExerciseBanner", () => {
  it("renders nothing while the clock is off", () => {
    const { container } = render(
      <ExerciseBanner state={EXERCISE_CLOCK_OFF} onTogglePlay={() => {}} onSetRate={() => {}} onNextPeriod={() => {}} onExit={() => {}} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the EXERCISE mark, the exercise time and the controls", () => {
    const state = exerciseClockReducer(EXERCISE_CLOCK_OFF, {
      type: "enable", startMs: START, endMs: START + 24 * H, periodBoundariesMs: [START + 12 * H], at: START + 2 * H,
    });
    const onSetRate = vi.fn();
    const onNextPeriod = vi.fn();
    const onTogglePlay = vi.fn();
    render(<ExerciseBanner state={state} onTogglePlay={onTogglePlay} onSetRate={onSetRate} onNextPeriod={onNextPeriod} onExit={() => {}} />);
    expect(screen.getByText("EXERCISE")).toBeInTheDocument();
    expect(screen.getByTestId("exercise-time")).toHaveTextContent("12:00");
    expect(screen.getByText("T+2:00")).toBeInTheDocument();
    expect(screen.getByText("Paused")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "12×" }));
    expect(onSetRate).toHaveBeenCalledWith(12);
    fireEvent.click(screen.getByRole("button", { name: /Next period/ }));
    expect(onNextPeriod).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Play exercise clock" }));
    expect(onTogglePlay).toHaveBeenCalledTimes(1);
  });

  it("shows the clock's span (a loaded pack's duration)", () => {
    const at = (h: number) => exerciseClockReducer(EXERCISE_CLOCK_OFF, { type: "enable", startMs: START, endMs: START + h * H });
    const { rerender } = render(<ExerciseBanner state={at(24)} onTogglePlay={() => {}} onSetRate={() => {}} onNextPeriod={() => {}} onExit={() => {}} />);
    expect(screen.getByTestId("exercise-span")).toHaveTextContent("of 24 h");
    rerender(<ExerciseBanner state={at(4.5)} onTogglePlay={() => {}} onSetRate={() => {}} onNextPeriod={() => {}} onExit={() => {}} />);
    expect(screen.getByTestId("exercise-span")).toHaveTextContent("of 4 h 30 min");
  });
});

describe("ExercisePackPanel", () => {
  it("lists the example packs, loads one, and marks everything EXERCISE", async () => {
    const pack = { schema: "firesim.exercise.pack/1", id: "example-x", marking: "EXERCISE", title: "EXERCISE: Test pack",
      sources: ["Open data (OGL)"], startIso: "2026-07-15T14:00:00-06:00", durationHours: 24,
      ignitions: [{ lat: 53.46, lng: -113.66, hoursFromStart: 0 }],
      weatherScript: [{ hoursFromStart: 0, windSpeedKmh: 20, windDirectionDeg: 225, temperatureC: 25, relativeHumidityPct: 30, precipitation24hMm: 0 }],
      startingCodes: { ffmc: 92, dmc: 40, dc: 300 }, fuel: { source: "city_lidar" },
      periods: [{ number: 1, startIso: "2026-07-15T14:00:00-06:00", endIso: "2026-07-16T02:00:00-06:00" }], scheduledInjects: [] };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ packs: [pack] }), { status: 200 }));
    const onLoad = vi.fn();
    try {
      render(<ExercisePackPanel loaded={null} notes={[]} onLoad={onLoad} onUnload={() => {}} />);
      expect(screen.getByText("EXERCISE")).toBeInTheDocument();
      fireEvent.click(await screen.findByRole("button", { name: "Load pack EXERCISE: Test pack" }));
      expect(onLoad).toHaveBeenCalledWith(pack);
      expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/exercise/examples");
    } finally {
      fetchMock.mockRestore();
    }
  });
});

describe("InjectLogPanel", () => {
  it("says so when empty and lists injects in exercise time otherwise", () => {
    const { rerender } = render(<InjectLogPanel log={EMPTY_INJECT_LOG} exerciseMs={START} />);
    expect(screen.getByTestId("inject-log")).toHaveTextContent("No injects yet");
    const log = addInject(EMPTY_INJECT_LOG, {
      exerciseTimeMs: START + 3 * H, title: "Wind shift to NW", payload: { kind: "wind_shift", windSpeedKmh: 30, windDirectionDeg: 315, hiddenFromForecast: true },
    });
    rerender(<InjectLogPanel log={log} exerciseMs={START} />);
    expect(screen.getByText("Wind shift to NW")).toBeInTheDocument();
    expect(screen.getByText("Wind shift")).toBeInTheDocument();
    expect(screen.getByText("13:00")).toBeInTheDocument();
    expect(screen.getByText("scheduled")).toBeInTheDocument();
  });
});
