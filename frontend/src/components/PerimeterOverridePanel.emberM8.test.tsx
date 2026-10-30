/**
 * M8 spot-fire likelihood option in the RPAS restart panel ([R70]):
 * - disabled with "needs an observed active edge" while the whole perimeter is active;
 * - enabled once edges are classified (or marked); the restart then sends `ember_m8: true`
 *   with the active edges; the approved sentence and "Relative only" are shown;
 * - off by default: no `ember_m8` in the request.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import PerimeterOverridePanel from "./PerimeterOverridePanel";
import { useRecon } from "../hooks/useRecon";
import type { PerimeterOverrideRequest } from "../types/simulation";
import { EMBER_M8_SENTENCE } from "../content/explanations";
import { buildOverrideRequest } from "../utils/activeEdges";

const SQUARE: GeoJSON.Polygon = {
  type: "Polygon",
  coordinates: [[[-113.51, 53.49], [-113.49, 53.49], [-113.49, 53.51], [-113.51, 53.51], [-113.51, 53.49]]],
};
const T_MS = Date.parse("2026-07-15T15:00:00-06:00");
const CLASSIFIED = {
  summary: { source: "rpas_hotspots", n_segments: 4, n_active: 1, n_inactive: 3, n_detections_read: 1, n_detections_used: 1, radius_m: 1000, window_h: 12 },
  active_edges: { type: "MultiLineString", coordinates: [[[-113.49, 53.49], [-113.49, 53.51]]] },
  segments: {
    type: "FeatureCollection", features: [
      { type: "Feature", properties: { status: "active" }, geometry: { type: "LineString", coordinates: [[-113.49, 53.49], [-113.49, 53.51]] } },
    ],
  },
};

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(CLASSIFIED), {
    status: 200, headers: { "Content-Type": "application/json" } })));
});
afterEach(() => vi.unstubAllGlobals());

function setup() {
  const { result, rerender: rerenderHook } = renderHook(() => useRecon());
  act(() => result.current.setPerimeter(SQUARE, "trace.geojson"));
  const onOverrideStart = vi.fn<(r: PerimeterOverrideRequest) => void>();
  const el = () => (
    <PerimeterOverridePanel simulationId="run1" onOverrideStart={onOverrideStart} isRunning={false}
      recon={result.current} observedAtMs={T_MS} />
  );
  const view = render(el());
  const refresh = () => { rerenderHook(); view.rerender(el()); };
  return { recon: result, onOverrideStart, refresh };
}

describe("M8 spot-fire likelihood option", () => {
  it("is disabled without an observed active edge and off by default", () => {
    const { onOverrideStart } = setup();
    const box = screen.getByTestId("recon-ember-m8-toggle") as HTMLInputElement;
    expect(box.disabled).toBe(true);
    expect(box.checked).toBe(false);
    expect(screen.getByTestId("recon-ember-m8")).toHaveTextContent("needs an observed active edge");
    fireEvent.click(screen.getByRole("button", { name: /Restart at .* from observed/ }));
    expect(onOverrideStart.mock.calls[0][0].ember_m8).toBeUndefined();
  });

  it("is offered once edges are classified and sends ember_m8 with the active edges", async () => {
    const { recon, onOverrideStart, refresh } = setup();
    const file = new File([JSON.stringify({ type: "FeatureCollection", features: [] })], "hot.geojson");
    fireEvent.change(screen.getByTestId("recon-hotspots-file"), { target: { files: [file] } });
    await waitFor(() => expect(recon.current.state.classified).not.toBeNull());
    refresh();
    const box = screen.getByTestId("recon-ember-m8-toggle") as HTMLInputElement;
    expect(box.disabled).toBe(false);
    const section = screen.getByTestId("recon-ember-m8");
    expect(section).toHaveTextContent(EMBER_M8_SENTENCE);
    expect(section).toHaveTextContent("Relative only");
    expect(section).not.toHaveTextContent(/%|probabilit(y|ies) of|expected/i);
    fireEvent.click(box);
    fireEvent.click(screen.getByRole("button", { name: /Restart at .* from observed/ }));
    const req = onOverrideStart.mock.calls[0][0];
    expect(req.ember_m8).toBe(true);
    expect(req.active_edges).toEqual(CLASSIFIED.active_edges);
  });

  it("buildOverrideRequest refuses ember_m8 without active edges", () => {
    expect(() => buildOverrideRequest({
      simulationId: "a", perimeter: SQUARE, durationHours: 4, snapshotMinutes: 30, mode: "whole",
      lines: [], bufferM: null, startTime: null, burningPeriod: null, ffmcSpinUp: false, emberM8: true,
    })).toThrow(/needs an observed active edge/);
  });
});
