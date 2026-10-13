import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import FireMetrics from "./FireMetrics";
import StructureSpreadPanel from "./StructureSpreadPanel";
import { frame, square } from "../test/frames";
import type { StructureSpreadSummary } from "../types/simulation";

// Size rule (decision 2026-10-12, [R40]): outbuildings < 20 m² are shown on their own row and
// are not part of the building counts; nothing changes when the API sends no rule.

const LABEL = "illustrative — not validated in Canada";
const EXPOSURE = {
  inside_perimeter: 3, within_10m: 1, within_30m: 9, within_100m: 22, within_500m: 62,
  flux_over_12_5: 4, flux_over_25: 2, ftp_reached: 1,
};

function summary(extra: Partial<StructureSpreadSummary> = {}): StructureSpreadSummary {
  return {
    model: "hamada", label: LABEL, computed: true, units_in_run: 900, units_built: 300,
    units_front_contact: 4, units_structure_to_structure: 10, units_involved: 14,
    combustible_fraction: 1, neighbour_cutoff_m: 30, wildland_contact_m: 10, ...extra,
  };
}

describe("size rule rows", () => {
  it("shows outbuildings within 500 m apart from the exposure counts", () => {
    const f = frame(2, square(0.01), {
      building_exposure: { ...EXPOSURE, small_within_500m: 17, small_footprint_m2: 20 },
    });
    render(<FireMetrics frame={f} status="completed" totalFrames={3} />);
    expect(screen.getByText("Within 500 m").closest(".metric-row")).toHaveTextContent("62");
    const row = screen.getByTestId("exposure-small");
    expect(row).toHaveTextContent("Outbuildings < 20 m² within 500 m");
    expect(row).toHaveTextContent("17");
    expect(screen.getByRole("button", { name: "About outbuildings" })).toBeInTheDocument();
  });

  it("has no outbuildings row when the rule is off", () => {
    render(<FireMetrics frame={frame(2, square(0.01), { building_exposure: EXPOSURE })} status="completed" totalFrames={3} />);
    expect(screen.queryByTestId("exposure-small")).not.toBeInTheDocument();
  });

  it("shows outbuildings involved apart from buildings involved", () => {
    const s = summary({ small_footprint_m2: 20, small_footprints: "count_separately", units_small_involved: 6, units_involved_all: 20 });
    const frames = [frame(1, square(0.01), { structure_spread: s })];
    render(<StructureSpreadPanel frames={frames} frameIndex={0} mapVisible onMapVisible={() => {}} mapAvailable />);
    expect(screen.getByText("Buildings involved").closest(".metric-row")).toHaveTextContent("14");
    const row = screen.getByTestId("structure-small");
    expect(row).toHaveTextContent("Outbuildings < 20 m²");
    expect(row).toHaveTextContent("6");
  });

  it("has no outbuildings row in house-to-house spread without the rule", () => {
    const frames = [frame(1, square(0.01), { structure_spread: summary() })];
    render(<StructureSpreadPanel frames={frames} frameIndex={0} mapVisible onMapVisible={() => {}} mapAvailable />);
    expect(screen.queryByTestId("structure-small")).not.toBeInTheDocument();
  });
});
