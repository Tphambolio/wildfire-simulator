import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import FireMetrics from "./FireMetrics";
import { frame, square } from "../test/frames";
import { fixture } from "../test/fixture";

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

describe("FireMetrics", () => {
  it("shows a hint before the first frame", () => {
    render(<FireMetrics frame={null} status={null} totalFrames={0} />);
    expect(screen.getByText("Run a simulation to see metrics")).toBeInTheDocument();
  });

  it("shows a waiting hint while running", () => {
    render(<FireMetrics frame={null} status="running" totalFrames={0} />);
    expect(screen.getByText("Waiting for first frame...")).toBeInTheDocument();
  });

  it("has no building exposure panel when building_exposure is not set", () => {
    render(<FireMetrics frame={frame(2, square(0.01), { area_ha: 12.34 })} status="completed" totalFrames={3} />);
    expect(screen.queryByText("Building exposure")).not.toBeInTheDocument();
    expect(screen.getByText("12.3 ha")).toBeInTheDocument();
    expect(screen.getByText("Head ROS")).toBeInTheDocument();
  });

  it("shows the building exposure panel when building_exposure is set", () => {
    const f = frame(2, square(0.01), { building_exposure: EXPOSURE, buildings_at_risk: 3 });
    render(<FireMetrics frame={f} status="completed" totalFrames={3} />);
    expect(screen.getByRole("heading", { name: "Building exposure" })).toBeInTheDocument();
    const row = screen.getByText("Within 500 m").closest(".metric-row")!;
    expect(row).toHaveTextContent("62");
    // The plain "Buildings inside perimeter" row is replaced by the exposure panel
    expect(screen.queryByText("Buildings inside perimeter")).not.toBeInTheDocument();
  });

  it("shows buildings inside the perimeter without exposure data", () => {
    render(<FireMetrics frame={frame(2, square(0.01), { buildings_at_risk: 7 })} status="completed" totalFrames={3} />);
    expect(screen.getByText("Buildings inside perimeter").closest(".metric-row")).toHaveTextContent("7");
  });

  it("labels the grid model's rate as Mean ROS and shows the snap notice", () => {
    const f = frame(1, square(0.01), { burned_cells: [], ignition_snapped_m: 120 });
    render(<FireMetrics frame={f} status="completed" totalFrames={2} />);
    expect(screen.getByText("Mean ROS")).toBeInTheDocument();
    expect(screen.getByText("CA Grid")).toBeInTheDocument();
    expect(screen.getByText(/Ignition snapped 120m/)).toBeInTheDocument();
  });

  it("renders the final frame of the recorded fixture", () => {
    const last = fixture.frames[fixture.frames.length - 1];
    render(<FireMetrics frame={last} status="completed" totalFrames={fixture.frames.length} />);
    expect(screen.getByText(`${last.area_ha.toFixed(1)} ha`)).toBeInTheDocument();
    expect(screen.queryAllByText("Building exposure")).toHaveLength(last.building_exposure ? 1 : 0);
  });
});
