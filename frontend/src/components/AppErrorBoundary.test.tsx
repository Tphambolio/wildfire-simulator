import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import AppErrorBoundary from "./AppErrorBoundary";

function Boom({ fail }: { fail: boolean }) {
  if (fail) throw new Error("Invalid LngLat latitude value: must be between -90 and 90");
  return <p>EOC content</p>;
}

describe("AppErrorBoundary", () => {
  let errorSpy: ReturnType<typeof vi.spyOn>;
  beforeEach(() => { errorSpy = vi.spyOn(console, "error").mockImplementation(() => {}); });
  afterEach(() => errorSpy.mockRestore());

  it("renders its children when nothing throws", () => {
    render(<AppErrorBoundary area="EOC console"><Boom fail={false} /></AppErrorBoundary>);
    expect(screen.getByText("EOC content")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a recoverable panel (not a blank screen) and logs the error when a child throws", () => {
    const onReset = vi.fn();
    render(<AppErrorBoundary area="EOC console" onReset={onReset}><Boom fail /></AppErrorBoundary>);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("The EOC console hit an error");
    expect(alert).toHaveTextContent("Invalid LngLat latitude value");
    expect(errorSpy.mock.calls.some((c: unknown[]) => String(c[0]).includes("[FireSim] EOC console crashed"))).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Return to map" }));
    expect(onReset).toHaveBeenCalledTimes(1);
  });

  it("clears the error when resetKey changes", () => {
    const { rerender } = render(<AppErrorBoundary resetKey="eoc"><Boom fail /></AppErrorBoundary>);
    expect(screen.getByRole("alert")).toHaveTextContent("FireSim hit an error");
    rerender(<AppErrorBoundary resetKey="simulation"><Boom fail={false} /></AppErrorBoundary>);
    expect(screen.getByText("EOC content")).toBeInTheDocument();
  });

  it("top level without onReset reloads the page", () => {
    const reload = vi.fn();
    const orig = window.location;
    Object.defineProperty(window, "location", { configurable: true, value: { ...orig, reload } });
    try {
      render(<AppErrorBoundary><Boom fail /></AppErrorBoundary>);
      fireEvent.click(screen.getByRole("button", { name: "Return to map" }));
      expect(reload).toHaveBeenCalledTimes(1);
    } finally {
      Object.defineProperty(window, "location", { configurable: true, value: orig });
    }
  });
});
