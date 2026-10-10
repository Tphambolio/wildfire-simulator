/**
 * Structure step 3 UI (docs/structure-spread-spec.md §4.6, §6.2): the tree-bridged gaps option,
 * the combustible-roof scenario select (needs embers), the scenario label on the panel and the
 * map layer's roof flag. The roof scenario is always labelled "(not observed roofs)".
 */
import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import StructureSpreadPanel from "./StructureSpreadPanel";
import { frame, square } from "../test/frames";
import type { SimulationCreate, StructureSpreadSummary, StructureUnitDetail } from "../types/simulation";
import { caveatFor, roofScenarioLabel, structureUnitsGeoJSON } from "../utils/structureSpread";

vi.mock("../services/api", () => ({
  fetchCurrentWeather: vi.fn(async () => ({ available: false, message: "none" })),
  calculateFWI: vi.fn(async () => ({ ffmc: 91, dmc: 50, dc: 310 })),
  fetchHourlyForecast: vi.fn(async () => []),
}));

import WeatherPanel from "./WeatherPanel";

const LABEL = "illustrative — not validated in Canada";
const ROOF = {
  combustible_roof_share: 0.15, psi_factor: 0.375, seed: 3,
  label: "scenario: 15 % combustible roofs (not observed roofs)",
  units_combustible_roof_built: 40, units_involved_combustible_roof: 2,
};

function summary(extra: Partial<StructureSpreadSummary> = {}): StructureSpreadSummary {
  return {
    model: "hamada", label: LABEL, computed: true, units_in_run: 5000, units_built: 900,
    units_front_contact: 3, units_structure_to_structure: 4, units_involved: 9, units_ember: 2,
    embers: true, design_fire_kw_m2: 150, burnout: true, burnout_min: 66,
    units_burning: 9, units_burnt_out: 0,
    combustible_fraction: 1, neighbour_cutoff_m: 20, wildland_contact_m: 10, ...extra,
  };
}

const ring = (x: number) => [[[x, 53.5], [x + 0.0001, 53.5], [x + 0.0001, 53.5001], [x, 53.5001], [x, 53.5]]];
const DETAIL: StructureUnitDetail[] = [
  { id: 0, t_h: 0.5, mechanism: "front", source_id: null, polygon: ring(-113.5) },
  { id: 1, t_h: 1.2, mechanism: "ember", source_id: 0, polygon: ring(-113.4998), combustible_roof_scenario: true,
    veg: { cc_0_10: 0.2 } },
];

describe("structure step 3 utils", () => {
  it("flags scenario roofs on the map features", () => {
    const fc = structureUnitsGeoJSON(DETAIL);
    expect(fc.features.map((f) => f.properties?.roof)).toEqual([0, 1]);
  });

  it("labels the roof scenario and adds the bridge and roof notes to the caveat", () => {
    const s = summary({ roof_scenario: ROOF, vegetation_bridged_cutoff: true });
    expect(roofScenarioLabel(s)).toBe("Scenario: 15 % combustible roofs (not observed roofs)");
    const c = caveatFor(s);
    expect(c).toContain("up to 45 m apart where the gap has at least 20 % tree cover");
    expect(c).toContain("scenario: 15 % combustible roofs (not observed roofs)");
    expect(c).toContain("0.375");
    expect(roofScenarioLabel(summary())).toBeNull();
    const plain = caveatFor(summary({ neighbour_cutoff_m: 30 }));
    expect(plain).toContain("up to 30 m apart;");
    expect(plain).not.toContain("45 m");
  });
});

describe("StructureSpreadPanel roof scenario", () => {
  it("shows the scenario label and the outline toggle", () => {
    const onRoof = vi.fn();
    const frames = [frame(1, square(0.01), { structure_spread: summary({ roof_scenario: ROOF }), structure_spread_detail: DETAIL })];
    render(
      <StructureSpreadPanel frames={frames} frameIndex={0} mapVisible onMapVisible={() => {}} mapAvailable
        roofOutline onRoofOutline={onRoof} />,
    );
    expect(screen.getByTestId("structure-roof-scenario")).toHaveTextContent(
      "Scenario: 15 % combustible roofs (not observed roofs)",
    );
    // counts by mechanism unchanged
    expect(screen.getByText("Ember ignition")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Outline scenario roofs"));
    expect(onRoof).toHaveBeenCalledWith(false);
  });

  it("has no scenario label or toggle without a roof scenario", () => {
    const frames = [frame(1, square(0.01), { structure_spread: summary() })];
    render(<StructureSpreadPanel frames={frames} frameIndex={0} mapVisible onMapVisible={() => {}} mapAvailable
      onRoofOutline={() => {}} />);
    expect(screen.queryByTestId("structure-roof-scenario")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Outline scenario roofs")).not.toBeInTheDocument();
  });
});

describe("WeatherPanel structure step 3 options", () => {
  it("enables the options in order and sends them with the run", async () => {
    const onStart = vi.fn<(p: SimulationCreate) => void>();
    render(<WeatherPanel onStartSimulation={onStart} ignitionPoint={{ lat: 53.5, lng: -113.5 }} isRunning={false} />);
    await act(async () => {});
    const bridge = screen.getByTestId("structure-veg-bridge") as HTMLInputElement;
    const roof = screen.getByTestId("structure-roof-share") as HTMLSelectElement;
    expect(bridge.disabled).toBe(true);
    expect(roof.disabled).toBe(true);
    fireEvent.click(screen.getByLabelText("House-to-house spread"));
    expect(bridge.disabled).toBe(false);
    expect(roof.disabled).toBe(true); // needs ember ignition
    fireEvent.click(screen.getByLabelText("Ember ignition"));
    expect(roof.disabled).toBe(false);
    expect(Array.from(roof.options).map((o) => o.textContent)).toEqual(["None", "5 %", "15 %", "30 %"]);
    fireEvent.change(roof, { target: { value: "0.15" } });
    fireEvent.click(bridge);
    fireEvent.change(screen.getByLabelText(/^Grass curing/), { target: { value: "60" } });
    fireEvent.click(screen.getByRole("button", { name: "Run Simulation" }));
    await waitFor(() => expect(onStart).toHaveBeenCalled());
    const req = onStart.mock.calls[0][0];
    expect(req.structure_spread).toBe(true);
    expect(req.structure_embers).toBe(true);
    expect(req.structure_vegetation_bridge).toBe(true);
    expect(req.structure_combustible_roof_share).toBe(0.15);
  });

  it("sends no roof share when embers are off", async () => {
    const onStart = vi.fn<(p: SimulationCreate) => void>();
    render(<WeatherPanel onStartSimulation={onStart} ignitionPoint={{ lat: 53.5, lng: -113.5 }} isRunning={false} />);
    await act(async () => {});
    fireEvent.click(screen.getByLabelText("House-to-house spread"));
    fireEvent.click(screen.getByLabelText("Ember ignition"));
    fireEvent.change(screen.getByTestId("structure-roof-share"), { target: { value: "0.3" } });
    fireEvent.click(screen.getByLabelText("Ember ignition")); // embers off again
    fireEvent.change(screen.getByLabelText(/^Grass curing/), { target: { value: "60" } });
    fireEvent.click(screen.getByRole("button", { name: "Run Simulation" }));
    await waitFor(() => expect(onStart).toHaveBeenCalled());
    const req = onStart.mock.calls[0][0];
    expect(req.structure_embers).toBe(false);
    expect(req.structure_combustible_roof_share).toBe(0);
    expect(req.structure_vegetation_bridge).toBe(false);
  });
});
