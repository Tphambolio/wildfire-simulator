/** EOC summary wording (round-3 field test, Claude in Chrome tester, 2026-10-13: R4, R11). */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import EOCSummary, { runHours, spottingText } from "./EOCSummary";
import { runParamsFor } from "../utils/fwiCalc";
import { frame, square } from "../test/frames";

const day = { wind_speed: 20, wind_direction: 270, temperature: 25, relative_humidity: 30, precipitation_24h: 0 };
const multiDay = {
  ...runParamsFor(day, { ffmc: 90, dmc: 45, dc: 300 }, 72, "C2", null, false),
  multi_day_days: [day, day, day],
  spotting: false,
};
const frames = [
  frame(24, square(0.01), { day: 1, area_ha: 100, max_hfi_kw_m: 15000 }),
  frame(72, square(0.02), { day: 3, area_ha: 409.5, max_hfi_kw_m: 15000 }),
];

describe("EOC summary for a multi-day run (R11)", () => {
  it("names the run's hours and says spotting was not modelled", () => {
    render(<EOCSummary frames={frames} burnProbData={null} runParams={multiDay} ignitionPoint={null} />);
    expect(screen.getByText("Fire Spread · 72h")).toBeInTheDocument();
    expect(screen.getByText("Not modelled (multi-day runs)")).toBeInTheDocument();
    expect(screen.getByText("Wind (day 1)")).toBeInTheDocument();
  });

  it("falls back to the frames' hours without run parameters", () => {
    expect(runHours(null, frames)).toBe(72);
    expect(runHours(null, [])).toBeNull();
    expect(spottingText(0, 0, { ...multiDay, multi_day_days: null, spotting: false })).toBe("Off (not modelled)");
    expect(spottingText(0, 0, null)).toBe("None");
    expect(spottingText(3, 420, null)).toBe("3 events · max 420 m");
  });
});

describe("HFI class, not a tactic (R4)", () => {
  it("labels the class interpretation and gives no withdrawal order or strategy", () => {
    const { container } = render(<EOCSummary frames={frames} burnProbData={null} runParams={multiDay} ignitionPoint={null} />);
    const text = container.textContent ?? "";
    expect(text).toContain("Head fire intensity · Class 6");
    expect(text).toContain("Generally not possible");
    expect(text).toContain("Equipment for this class (not a resource order)");
    expect(text).not.toMatch(/withdraw/i);
    expect(text).not.toMatch(/Strategy/);
  });
});
