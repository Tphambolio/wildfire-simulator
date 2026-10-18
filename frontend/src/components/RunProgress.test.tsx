import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import RunProgress, { PHASE_LABEL, etaFromSamples, etaText, formatElapsedSeconds } from "./RunProgress";
import { frameProgress } from "../hooks/useSimulation";

describe("RunProgress", () => {
  it("is indeterminate with a phase label before the spread starts", () => {
    render(<RunProgress phase="loading" progress={null} startedAt={Date.now()} />);
    const bar = screen.getByRole("progressbar", { name: "Simulation run" });
    expect(bar).not.toHaveAttribute("aria-valuenow");
    expect(bar).toHaveAttribute("aria-valuetext", "Loading fuel grid…");
    expect(screen.getByTestId("run-progress")).toHaveTextContent("Loading fuel grid…");
    expect(bar).toHaveClass("run-progress-indeterminate");
  });

  it("shows the spread fraction as aria-valuenow and text", () => {
    render(<RunProgress phase="spread" progress={0.42} startedAt={Date.now()} />);
    const bar = screen.getByRole("progressbar", { name: "Simulation run" });
    expect(bar).toHaveAttribute("aria-valuenow", "42");
    expect(screen.getByTestId("run-progress")).toHaveTextContent("Running spread… 42 %");
  });

  it("announces phase changes politely, not every percent", () => {
    const { rerender } = render(<RunProgress phase="spread" progress={0.1} startedAt={null} />);
    const live = document.querySelector("[aria-live=polite]")!;
    expect(live).toHaveTextContent(PHASE_LABEL.spread);
    rerender(<RunProgress phase="spread" progress={0.5} startedAt={null} />);
    expect(live).toHaveTextContent(PHASE_LABEL.spread);
    rerender(<RunProgress phase="structures" progress={1} startedAt={null} />);
    expect(live).toHaveTextContent("House-to-house spread…");
  });

  it("shows elapsed time and cancels", () => {
    const cancel = vi.fn();
    render(<RunProgress phase={null} progress={null} startedAt={Date.now() - 75_000} onCancel={cancel} />);
    expect(screen.getByTestId("run-progress")).toHaveTextContent("1:15");
    expect(screen.getByTestId("run-progress")).toHaveTextContent("Starting…");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(cancel).toHaveBeenCalled();
  });

  it("formats elapsed seconds and derives progress from frame times", () => {
    expect(formatElapsedSeconds(5)).toBe("0:05");
    expect(formatElapsedSeconds(125)).toBe("2:05");
    expect(frameProgress(null, 1, 4)).toBe(0.25);
    expect(frameProgress(0.5, 1, 4)).toBe(0.5); // never goes back
    expect(frameProgress(null, 9, 4)).toBe(1);
    expect(frameProgress(0.3, 1, null)).toBe(0.3);
  });
});

describe("RunProgress for multi-day runs (round-3 field test R12)", () => {
  it("shows the day being computed and a rough time left", () => {
    vi.useFakeTimers();
    try {
      render(<RunProgress phase="spread" progress={0.5} startedAt={Date.now() - 600_000} day={2} nDays={3} />);
      const box = screen.getByTestId("run-progress");
      expect(box).toHaveTextContent("Day 2 of 3 · Running spread… 50 %");
      act(() => { vi.advanceTimersByTime(1000); });
      expect(screen.getByTestId("run-progress-eta")).toHaveTextContent("about 10 min left");
    } finally {
      vi.useRealTimers();
    }
  });

  it("estimates nothing too early", () => {
    expect(etaText(10, 0.5)).toBeNull();
    expect(etaText(100, 0.02)).toBeNull();
    expect(etaText(100, 1)).toBeNull();
    expect(etaText(600, 0.95)).toBe("about a minute left");
  });
});

describe("Time left from the recent rate (field test 2026-10-18, Issue 4)", () => {
  // The live day-3 pattern: 74 % at 1:15, then about 1.4 % a minute; the run ended at 10-12 min
  const s = (min: number, f: number) => ({ t: min * 60_000, f });
  const live = [s(0, 0), s(0.25, 0.2), s(0.57, 0.46), s(1.25, 0.74), s(2.25, 0.76), s(3.25, 0.77), s(4.25, 0.78)];

  it("uses the last two minutes, not the average since the start", () => {
    // At 4:15 the start average would say "under a minute"; the recent rate is 1 % a minute
    expect(etaFromSamples(live, 4.25 * 60_000)).toBe("roughly 20 min left");
    // At 1:15, just after a fast stretch, the estimate is short
    expect(etaFromSamples(live.slice(0, 4), 1.25 * 60_000)).toBe("about a minute left");
  });

  it("grows while progress stalls, and says nothing without progress in the window", () => {
    const stalled = [s(0, 0), s(1, 0.5)];
    expect(etaFromSamples(stalled, 1 * 60_000)).toBe("about a minute left");
    expect(etaFromSamples(stalled, 2 * 60_000)).toBe("about 2 min left");
    expect(etaFromSamples(stalled, 3.5 * 60_000)).toBeNull(); // no gain in the last 2 min
  });

  it("estimates nothing at the start, at the end, or from under 20 s", () => {
    expect(etaFromSamples([], 0)).toBeNull();
    expect(etaFromSamples([s(0, 0), s(0.1, 0.3)], 0.2 * 60_000)).toBeNull();
    expect(etaFromSamples([s(0, 0), s(1, 1)], 60_000)).toBeNull();
    expect(etaFromSamples([s(0, 0), s(1, 0.03)], 60_000)).toBeNull();
  });
});
