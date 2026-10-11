/**
 * Long-range embers UI (docs/structure-spread-spec.md §6.3): the opt-in checkbox, the request
 * fields, the roof scenario allowed with long-range embers alone, and the per-building
 * ignition probability on the map features and popup text. Illustrative, map display only.
 */
import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { SimulationCreate, StructureUnitDetail } from "../types/simulation";
import { describeProbability, structureUnitsGeoJSON } from "../utils/structureSpread";

vi.mock("../services/api", () => ({
  fetchCurrentWeather: vi.fn(async () => ({ available: false, message: "none" })),
  calculateFWI: vi.fn(async () => ({ ffmc: 91, dmc: 50, dc: 310 })),
  fetchHourlyForecast: vi.fn(async () => []),
}));

import WeatherPanel from "./WeatherPanel";

const ring = (x: number) => [[[x, 53.5], [x + 0.0001, 53.5], [x + 0.0001, 53.5001], [x, 53.5001], [x, 53.5]]];

describe("long-range ember utils", () => {
  it("carries the ignition probability on map features (-1 without one)", () => {
    const detail: StructureUnitDetail[] = [
      { id: 0, t_h: 1, mechanism: "ember", source_id: null, polygon: ring(-113.5), p_involved: 0.42, p_ember: 0.4 },
      { id: 1, t_h: 1, mechanism: "b2b", source_id: null, polygon: ring(-113.49) },
    ];
    const fc = structureUnitsGeoJSON(detail);
    expect(fc.features[0].properties?.p).toBe(0.42);
    expect(fc.features[1].properties?.p).toBe(-1);
  });

  it("describes the probability as illustrative", () => {
    expect(describeProbability(0.426)).toBe("ignition probability 43 % (illustrative)");
    expect(describeProbability(-1)).toBeNull();
    expect(describeProbability(undefined)).toBeNull();
  });
});

describe("WeatherPanel long-range embers option", () => {
  it("needs house-to-house spread, enables roofs on its own and is sent with the run", async () => {
    const onStart = vi.fn<(p: SimulationCreate) => void>();
    render(<WeatherPanel onStartSimulation={onStart} ignitionPoint={{ lat: 53.5, lng: -113.5 }} isRunning={false} />);
    await act(async () => {});
    const lr = screen.getByTestId("structure-long-range") as HTMLInputElement;
    const roof = screen.getByTestId("structure-roof-share") as HTMLSelectElement;
    expect(lr.disabled).toBe(true);
    expect(screen.getByLabelText("About long-range embers")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("House-to-house spread"));
    expect(lr.disabled).toBe(false);
    fireEvent.click(lr);
    expect(roof.disabled).toBe(false);
    fireEvent.change(roof, { target: { value: "0.05" } });
    fireEvent.change(screen.getByLabelText(/^Grass curing/), { target: { value: "60" } });
    fireEvent.click(screen.getByRole("button", { name: "Run Simulation" }));
    await waitFor(() => expect(onStart).toHaveBeenCalled());
    const req = onStart.mock.calls[0][0];
    expect(req.structure_long_range_embers).toBe(true);
    expect(req.structure_embers).toBe(false);
    expect(req.structure_combustible_roof_share).toBe(0.05);
  });
});
