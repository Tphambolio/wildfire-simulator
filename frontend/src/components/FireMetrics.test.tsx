import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import FireMetrics from "./FireMetrics";
import { frame, square } from "../test/frames";
import { fixture } from "../test/fixture";
import { TIPS } from "../content/explanations";

const EXPOSURE = {
  inside_perimeter: 3,
  within_10m: 1,
  within_30m: 9,
  within_100m: 22,
  within_500m: 62,
  flux_over_12_5: 4,
  flux_over_25: 2,
  ftp_reached: 1,
};

const HEAD = { lat: 53.5, lng: -113.5, ros: 25, raz: 92, hfi: 4000, cfb: 0, fuel: "O1a", t: 30, max_spot_distance_m: 140.4 };

describe("FireMetrics (run details)", () => {
  it("renders nothing before the first frame (the Situation header says what to do)", () => {
    const { container } = render(<FireMetrics frame={null} status={null} totalFrames={0} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("does not repeat the KPI tiles (area, head ROS, elapsed time, frames)", () => {
    render(<FireMetrics frame={frame(2, square(0.01), { area_ha: 12.34 })} status="completed" totalFrames={3} />);
    expect(screen.queryByText("12.3 ha")).not.toBeInTheDocument();
    expect(screen.queryByText("Head ROS")).not.toBeInTheDocument();
    expect(screen.queryByText("Time Elapsed")).not.toBeInTheDocument();
    expect(screen.queryByText("Frames")).not.toBeInTheDocument();
    expect(screen.queryByText("Building exposure")).not.toBeInTheDocument();
  });

  it("shows the building exposure panel with the 'Exposure, not ignition' badge and its tip", () => {
    const f = frame(2, square(0.01), { building_exposure: EXPOSURE, buildings_at_risk: 3 });
    render(<FireMetrics frame={f} status="completed" totalFrames={3} />);
    expect(screen.getByRole("heading", { name: "Building exposure" })).toBeInTheDocument();
    const row = screen.getByText("Within 500 m").closest(".metric-row")!;
    expect(row).toHaveTextContent("62");
    // The plain "Buildings inside perimeter" row is replaced by the exposure panel
    expect(screen.queryByText("Buildings inside perimeter")).not.toBeInTheDocument();
    const badge = screen.getByRole("button", { name: "Exposure, not ignition" });
    const tip = document.getElementById(badge.getAttribute("aria-describedby")!)!;
    expect(tip).toHaveAttribute("role", "tooltip");
    expect(tip).toHaveTextContent(TIPS.exposure);
    expect(tip).not.toHaveClass("open");
    fireEvent.focus(badge);
    expect(tip).toHaveClass("open");
  });

  it("shows buildings inside the perimeter without exposure data", () => {
    render(<FireMetrics frame={frame(2, square(0.01), { buildings_at_risk: 7 })} status="completed" totalFrames={3} />);
    expect(screen.getByText("Buildings inside perimeter").closest(".metric-row")).toHaveTextContent("7");
  });

  it("names the grid model and shows the snap notice", () => {
    const f = frame(1, square(0.01), { burned_cells: [], ignition_snapped_m: 120 });
    render(<FireMetrics frame={f} status="completed" totalFrames={2} />);
    expect(screen.getByRole("button", { name: "Grid model" })).toBeInTheDocument();
    expect(screen.getByText(/Ignition snapped 120 m/)).toBeInTheDocument();
  });

  it("adds the maximum HFI on the front only when the tiles show the head's intensity", () => {
    const { rerender } = render(
      <FireMetrics frame={frame(1, square(0.01), { burned_cells: [], head: HEAD, max_hfi_kw_m: 5200 })} status="completed" totalFrames={2} />,
    );
    expect(screen.getByText("Max HFI on the front").closest(".metric-row")).toHaveTextContent("5200 kW/m");
    rerender(<FireMetrics frame={frame(1, square(0.01), { burned_cells: [], max_hfi_kw_m: 5200 })} status="completed" totalFrames={2} />);
    expect(screen.queryByText("Max HFI on the front")).not.toBeInTheDocument();
  });

  it("renders the final frame of the recorded fixture", () => {
    const last = fixture.frames[fixture.frames.length - 1];
    render(<FireMetrics frame={last} status="completed" totalFrames={fixture.frames.length} />);
    expect(screen.getByRole("heading", { name: "Run details" })).toBeInTheDocument();
    expect(screen.queryAllByText("Building exposure")).toHaveLength(last.building_exposure ? 1 : 0);
  });
});
