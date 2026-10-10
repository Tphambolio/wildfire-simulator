import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import StructureSpreadPanel from "./StructureSpreadPanel";
import InfoTip from "./InfoTip";
import { frame, square } from "../test/frames";
import type { StructureSpreadSummary, StructureUnitDetail } from "../types/simulation";
import {
  STRUCT_B2B,
  STRUCT_FRONT,
  describeUnit,
  structureCaveat,
  structureDetail,
  structureSeries,
  structureUnitsGeoJSON,
} from "../utils/structureSpread";

const LABEL = "illustrative — not validated in Canada";

function summary(front: number, b2b: number, extra: Partial<StructureSpreadSummary> = {}): StructureSpreadSummary {
  return {
    model: "hamada", label: LABEL, computed: true, units_in_run: 334213, units_built: 1600,
    units_front_contact: front, units_structure_to_structure: b2b, units_involved: front + b2b,
    combustible_fraction: 1, neighbour_cutoff_m: 30, wildland_contact_m: 10, ...extra,
  };
}

const ring = (x: number) => [[[x, 53.5], [x + 0.0001, 53.5], [x + 0.0001, 53.5001], [x, 53.5001], [x, 53.5]]];
const DETAIL: StructureUnitDetail[] = [
  { id: 0, t_h: 0.5, mechanism: "front", source_id: null, polygon: ring(-113.5) },
  { id: 1, t_h: 1.2, mechanism: "b2b", source_id: 0, polygon: ring(-113.4998) },
  { id: 2, t_h: 2.0, mechanism: "b2b", source_id: 1, polygon: ring(-113.4996) },
];

const FRAMES = [
  frame(1, square(0.01), { structure_spread: summary(1, 0) }),
  frame(2, square(0.01), { structure_spread: summary(1, 2) }),
  frame(3, square(0.01), { structure_spread: summary(3, 9), structure_spread_detail: DETAIL }),
];

describe("structureSpread utils", () => {
  it("builds the stacked series from computed frames only", () => {
    const skipped = frame(4, square(0.01), {
      structure_spread: summary(0, 0, { computed: false, units_involved: null, units_front_contact: null, units_structure_to_structure: null }),
    });
    expect(structureSeries([...FRAMES, skipped])).toEqual([
      { t: 1, front: 1, b2b: 0 },
      { t: 2, front: 1, b2b: 2 },
      { t: 3, front: 3, b2b: 9 },
    ]);
  });

  it("turns the final-frame detail into polygons with time and mechanism only", () => {
    expect(structureDetail(FRAMES)).toBe(DETAIL);
    const fc = structureUnitsGeoJSON(DETAIL);
    expect(fc.features).toHaveLength(3);
    expect(fc.features[1].geometry.type).toBe("Polygon");
    expect(fc.features[1].properties).toEqual({ id: 1, t_h: 1.2, mechanism: "b2b" });
    expect(structureUnitsGeoJSON(null).features).toEqual([]);
  });

  it("describes a unit by mechanism and involvement time", () => {
    expect(describeUnit(1.25, "b2b")).toBe("Building to building · involved at +1 h 15 min");
    expect(describeUnit(0.5, "front", "15:32 MDT")).toBe("Front contact · involved at 15:32 MDT (+0 h 30 min)");
  });

  it("caveat carries the label, the not-a-prediction note, the cutoff and the grid contact", () => {
    const c = structureCaveat(LABEL, 30);
    expect(c).toContain("Illustrative — not validated in Canada");
    expect(c).toContain("not a prediction of which buildings will burn");
    expect(c).toContain("30 m");
    expect(c).toContain("~50 m");
  });

  it("map colours match the design tokens", () => {
    const css = readFileSync("src/styles/tokens.css", "utf8");
    expect(css).toContain(`--struct-front: ${STRUCT_FRONT};`);
    expect(css).toContain(`--struct-b2b: ${STRUCT_B2B};`);
  });
});

describe("StructureSpreadPanel", () => {
  it("shows the counts of the selected frame, the badge, and the caveat only in the tooltip", () => {
    render(<StructureSpreadPanel frames={FRAMES} frameIndex={1} mapVisible onMapVisible={() => {}} mapAvailable />);
    expect(screen.getByRole("heading", { name: /House-to-house spread/ })).toBeInTheDocument();
    expect(screen.getByText("Illustrative")).toBeInTheDocument();
    const row = (label: string) => screen.getByText(label).closest(".metric-row")!;
    expect(row("Buildings involved")).toHaveTextContent("3");
    expect(row("Front contact")).toHaveTextContent("1");
    expect(row("Building to building")).toHaveTextContent("2");
    // Caveat: in a tooltip linked to the info button, hidden until focus
    const btn = screen.getByRole("button", { name: "About house-to-house spread" });
    const tip = screen.getByRole("tooltip", { hidden: true });
    expect(btn).toHaveAttribute("aria-describedby", tip.id);
    expect(tip).not.toHaveClass("open");
    expect(tip).toHaveTextContent("not a prediction of which buildings will burn");
    fireEvent.focus(btn);
    expect(tip).toHaveClass("open");
    fireEvent.keyDown(btn, { key: "Escape" });
    expect(tip).not.toHaveClass("open");
  });

  it("draws the chart with the cursor at the selected frame and toggles the map layer", () => {
    const onMap = vi.fn();
    const { rerender } = render(
      <StructureSpreadPanel frames={FRAMES} frameIndex={0} mapVisible={false} onMapVisible={onMap} mapAvailable />,
    );
    const chart = screen.getByTestId("structure-chart");
    expect(chart).toHaveAttribute("aria-label", expect.stringContaining("12 by 3.0 h (3 front contact, 9 building to building)"));
    const x0 = Number(screen.getByTestId("structure-chart-cursor").getAttribute("x1"));
    rerender(<StructureSpreadPanel frames={FRAMES} frameIndex={2} mapVisible={false} onMapVisible={onMap} mapAvailable />);
    const x2 = Number(screen.getByTestId("structure-chart-cursor").getAttribute("x1"));
    expect(x2).toBeGreaterThan(x0);
    fireEvent.click(screen.getByLabelText("Show on map"));
    expect(onMap).toHaveBeenCalledWith(true);
  });

  it("says why there are no counts when the guard stopped the build", () => {
    const f = frame(1, square(0.01), {
      structure_spread: summary(0, 0, {
        computed: false, note: "not computed: too many buildings in run area",
        units_involved: null, units_front_contact: null, units_structure_to_structure: null,
      }),
    });
    render(<StructureSpreadPanel frames={[f]} frameIndex={0} mapVisible onMapVisible={() => {}} mapAvailable={false} />);
    expect(screen.getByRole("note")).toHaveTextContent("Not computed: too many buildings in run area");
    expect(screen.queryByTestId("structure-chart")).not.toBeInTheDocument();
  });

  it("renders nothing for a run without structure spread", () => {
    const { container } = render(
      <StructureSpreadPanel frames={[frame(1, square(0.01))]} frameIndex={0} mapVisible onMapVisible={() => {}} mapAvailable={false} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});

describe("InfoTip (structure caveat)", () => {
  it("opens on keyboard focus and closes on Escape", () => {
    render(<InfoTip text="Caveat text" label="About X" />);
    const tip = screen.getByRole("tooltip", { hidden: true });
    fireEvent.focus(screen.getByRole("button", { name: "About X" }));
    expect(tip).toHaveClass("open");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(tip).not.toHaveClass("open");
  });
});
