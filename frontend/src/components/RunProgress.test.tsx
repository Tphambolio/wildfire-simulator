import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import RunProgress, { PHASE_LABEL, formatElapsedSeconds } from "./RunProgress";
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
