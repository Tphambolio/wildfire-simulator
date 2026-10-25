/**
 * Active-segment restart in the perimeter panel (backlog A1, docs/active-segments.md):
 * - without a run, the panel offers the standalone forecast from an observed perimeter at T;
 * - "Classify from RPAS hotspots" posts the perimeter, T and the hotspots to
 *   /classify-perimeter and shows "Segments active M of N" with "Active edge" / "Not active
 *   (burned out)" and "rule not validated";
 * - the restart then sends the classified active edges as `active_edges` (today's marking) and
 *   no hold (the held-edge barrier stays in #100, unmerged);
 * - a classification with no active segment blocks the restart with a message;
 * - the shown run's readout gives the modelled static share and the segment counts.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import PerimeterOverridePanel from "./PerimeterOverridePanel";
import { useRecon } from "../hooks/useRecon";
import type { PerimeterOverrideRequest } from "../types/simulation";

const SQUARE: GeoJSON.Polygon = {
  type: "Polygon",
  coordinates: [[[-113.51, 53.49], [-113.49, 53.49], [-113.49, 53.51], [-113.51, 53.51], [-113.51, 53.49]]],
};
const T_MS = Date.parse("2026-07-15T15:00:00-06:00");
const CLASSIFIED = {
  summary: { source: "rpas_hotspots", n_segments: 4, n_active: 1, n_inactive: 3, n_detections_read: 2, n_detections_used: 1, radius_m: 1000, window_h: 12 },
  active_edges: { type: "MultiLineString", coordinates: [[[-113.49, 53.49], [-113.49, 53.51]]] },
  segments: {
    type: "FeatureCollection", features: [
      { type: "Feature", properties: { status: "active" }, geometry: { type: "LineString", coordinates: [[-113.49, 53.49], [-113.49, 53.51]] } },
      { type: "Feature", properties: { status: "inactive" }, geometry: { type: "LineString", coordinates: [[-113.49, 53.51], [-113.51, 53.51]] } },
    ],
  },
};

let posts: Array<{ url: string; body: Record<string, unknown> }>;
beforeEach(() => {
  posts = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    posts.push({ url: String(url), body: JSON.parse(String(init.body ?? "{}")) });
    return new Response(JSON.stringify(CLASSIFIED), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
});
afterEach(() => vi.unstubAllGlobals());

function setup(props: Partial<Parameters<typeof PerimeterOverridePanel>[0]> = {}) {
  const { result, rerender: rerenderHook } = renderHook(() => useRecon());
  act(() => result.current.setPerimeter(SQUARE, "trace.geojson"));
  const onOverrideStart = vi.fn<(r: PerimeterOverrideRequest) => void>();
  const view = render(
    <PerimeterOverridePanel simulationId="run1" onOverrideStart={onOverrideStart} isRunning={false}
      recon={result.current} observedAtMs={T_MS} {...props} />,
  );
  const refresh = () => {
    rerenderHook();
    view.rerender(
      <PerimeterOverridePanel simulationId="run1" onOverrideStart={onOverrideStart} isRunning={false}
        recon={result.current} observedAtMs={T_MS} {...props} />,
    );
  };
  return { recon: result, onOverrideStart, refresh };
}

describe("PerimeterOverridePanel active segments", () => {
  it("classifies from hotspots, shows active / not active and sends the edges (no hold)", async () => {
    const { recon, onOverrideStart, refresh } = setup();
    const file = new File([JSON.stringify({ type: "FeatureCollection", features: [] })], "hot.geojson");
    fireEvent.change(screen.getByTestId("recon-hotspots-file"), { target: { files: [file] } });
    await waitFor(() => expect(recon.current.state.classified).not.toBeNull());
    expect(posts[0].url).toMatch(/\/api\/v1\/simulations\/classify-perimeter$/);
    expect(posts[0].body.observation_time).toBe("2026-07-15T15:00:00-06:00");
    expect(posts[0].body.hotspots_geojson).toEqual({ type: "FeatureCollection", features: [] });
    refresh();
    const readout = screen.getByTestId("recon-segments");
    expect(readout).toHaveTextContent("Active edge · Not active (burned out) · Segments active 1 of 4");
    expect(readout).toHaveTextContent("rule not validated");
    expect(readout).not.toHaveTextContent(/held|hold/i);
    expect(recon.current.lines).toHaveLength(1);
    expect(screen.queryByLabelText(/Hold/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Restart at .* from observed/ }));
    const req = onOverrideStart.mock.calls[0][0];
    expect(req.active_edges).toEqual(CLASSIFIED.active_edges);
    expect(req).not.toHaveProperty("hold_hours");
    expect(req).not.toHaveProperty("hold_band_m");
    expect(req.start_time).toBe("2026-07-15T15:00:00-06:00");
  });

  it("no active segment: the restart is blocked with a message", async () => {
    const { recon, onOverrideStart, refresh } = setup();
    act(() => recon.current.setClassified({
      source: "rpas_hotspots", activeEdges: null, summary: { source: "rpas_hotspots", n_segments: 4, n_active: 0, n_inactive: 4 },
      segments: { type: "FeatureCollection", features: [] },
    }));
    refresh();
    expect(screen.getByTestId("recon-no-active")).toHaveTextContent("nothing would spread");
    expect(screen.getByRole("button", { name: /Restart at .* from observed/ })).toBeDisabled();
    expect(onOverrideStart).not.toHaveBeenCalled();
  });

  it("whole perimeter: the request is today's (no active_edges, no new fields)", () => {
    const { onOverrideStart } = setup();
    fireEvent.click(screen.getByRole("button", { name: /Restart at .* from observed/ }));
    const req = onOverrideStart.mock.calls[0][0];
    expect(req.active_edges).toBeUndefined();
    for (const k of ["hold_hours", "hotspots_geojson", "firms_csv"]) expect(req).not.toHaveProperty(k);
  });

  it("observation time field overrides the timeline time", () => {
    const { recon, onOverrideStart, refresh } = setup();
    fireEvent.change(screen.getByLabelText(/Observed at \(T/), { target: { value: "2026-07-15T09:30" } });
    refresh();
    expect(recon.current.state.observedMs).toBe(Date.parse("2026-07-15T09:30:00-06:00"));
    fireEvent.click(screen.getByRole("button", { name: /Restart at .* from observed/ }));
    expect(onOverrideStart.mock.calls[0][0].start_time).toBe("2026-07-15T09:30:00-06:00");
  });

  it("standalone: no run, the button forecasts from the observed perimeter", () => {
    const onObservedStart = vi.fn();
    const { result } = renderHook(() => useRecon());
    act(() => result.current.setPerimeter(SQUARE, "trace.geojson"));
    render(<PerimeterOverridePanel simulationId={null} onOverrideStart={vi.fn()} isRunning={false}
      recon={result.current} observedAtMs={T_MS} onObservedStart={onObservedStart} />);
    expect(screen.getByRole("button", { name: /Use the modelled perimeter/ })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /Forecast from observed at/ }));
    expect(onObservedStart).toHaveBeenCalledTimes(1);
    expect(onObservedStart.mock.calls[0][1]).toBe("2026-07-15T15:00:00-06:00");
    expect(onObservedStart.mock.calls[0][0].perimeter).toEqual(SQUARE);
  });

  it("without a run and without the standalone option the panel stays empty", () => {
    const { result } = renderHook(() => useRecon());
    const { container } = render(<PerimeterOverridePanel simulationId={null} onOverrideStart={vi.fn()}
      isRunning={false} recon={result.current} />);
    expect(container.querySelector("button")).toBeNull();
  });

  it("run readout: modelled static share and segment counts", () => {
    setup({ runActiveSegments: { source: "rpas_hotspots", n_segments: 4, n_active: 1, n_inactive: 3, static_share_model: 0.87 } });
    expect(screen.getByTestId("recon-run-readout")).toHaveTextContent(
      "This run: modelled static share 87 % · Segments active 1 of 4");
  });
});
