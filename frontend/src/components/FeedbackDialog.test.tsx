import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import FeedbackDialog from "./FeedbackDialog";
import type { TelemetryConfig } from "../telemetry/telemetry";

const NONE: TelemetryConfig = { provider: "none", token: "", endpoint: "", feedbackEmail: "" };
const CTX = { page: "simulation", exercise: false };

describe("FeedbackDialog", () => {
  it("rates, prefills the issue with the note, and hides Email without an address", () => {
    render(<FeedbackDialog cfg={NONE} ctx={CTX} onClose={() => {}} />);
    expect(screen.getByRole("dialog", { name: "Was this useful today?" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "4 of 5" }));
    expect(screen.getByRole("button", { name: "4 of 5" }).getAttribute("aria-pressed")).toBe("true");
    // Counting is off: say so honestly rather than claim it was recorded
    expect(screen.getByRole("status").textContent).toMatch(/open an issue/);
    fireEvent.change(screen.getByTestId("feedback-text"), { target: { value: "More fuel types" } });
    const href = screen.getByTestId("feedback-issue").getAttribute("href")!;
    expect(decodeURIComponent(href)).toContain("More fuel types");
    expect(decodeURIComponent(href)).toContain("4/5");
    expect(screen.queryByTestId("feedback-email")).toBeNull();
  });

  it("shows Email when the build sets an address; Esc closes", () => {
    const onClose = vi.fn();
    render(<FeedbackDialog cfg={{ ...NONE, feedbackEmail: "a@b.org" }} ctx={CTX} onClose={onClose} />);
    expect(screen.getByTestId("feedback-email").getAttribute("href")).toMatch(/^mailto:a@b\.org/);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});
