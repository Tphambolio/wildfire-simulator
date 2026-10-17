/**
 * Re-running replaces the previous run completely (owner report 2026-10-11: a single run with
 * the ensemble, then a multi-day run, kept showing the old run and polling its ensemble).
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SimulationFrame } from "../types/simulation";

const api = vi.hoisted(() => ({
  createSimulation: vi.fn(),
  createMultiDaySimulation: vi.fn(),
  createPerimeterOverride: vi.fn(),
  getSimulation: vi.fn(),
  getEnsemble: vi.fn(),
}));
vi.mock("../services/api", async (orig) => ({
  ...(await orig<typeof import("../services/api")>()),
  ...api,
  getWebSocketUrl: (id: string) => `ws://test/${id}`,
}));

import { useSimulation } from "./useSimulation";
import { useEnsemble } from "./useEnsemble";

/** Minimal WebSocket stand-in: tests push messages with `emit`. */
class FakeSocket {
  static all: FakeSocket[] = [];
  url: string;
  readyState = 1;
  closed = false;
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(url: string) {
    this.url = url;
    FakeSocket.all.push(this);
  }
  send() {}
  close() {
    this.closed = true;
  }
  emit(msg: object) {
    this.onmessage?.({ data: JSON.stringify(msg) });
  }
}

const frame = (t: number, area: number): SimulationFrame => ({
  time_hours: t, perimeter: [[53.5, -113.5], [53.51, -113.5], [53.51, -113.49]], area_ha: area,
  head_ros_m_min: 1, max_hfi_kw_m: 100, fire_type: "SURFACE", flame_length_m: 1,
  fuel_breakdown: {}, spot_fires: null, burned_cells: null, num_fronts: 1,
});

beforeEach(() => {
  FakeSocket.all = [];
  vi.stubGlobal("WebSocket", FakeSocket);
  Object.values(api).forEach((f) => f.mockReset());
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("useSimulation: a new run replaces the previous one", () => {
  it("single run then multi-day: old frames go at once, the old socket is closed and ignored", async () => {
    api.createSimulation.mockResolvedValue({ simulation_id: "single1" });
    api.createMultiDaySimulation.mockResolvedValue({ simulation_id: "multi1" });
    const { result } = renderHook(() => useSimulation());

    await act(() => result.current.startSimulation({ ignition_lat: 53.5, ignition_lng: -113.5, duration_hours: 4 } as never));
    const old = FakeSocket.all[0];
    act(() => {
      old.emit({ type: "simulation.frame", simulation_id: "single1", frame: frame(4, 300) });
      old.emit({ type: "simulation.completed", simulation_id: "single1" });
    });
    expect(result.current.simulationId).toBe("single1");
    expect(result.current.frames.at(-1)!.area_ha).toBe(300);

    let pending!: Promise<void>;
    act(() => {
      pending = result.current.startMultiDaySimulation({ days: [{}, {}, {}] } as never);
    });
    // Before the POST returns: nothing of the old run is left, the new one is running
    expect(result.current.simulationId).toBeNull();
    expect(result.current.frames).toEqual([]);
    expect(result.current.isRunning).toBe(true);
    expect(result.current.durationHours).toBe(72);
    expect(old.closed).toBe(true);
    expect(old.onmessage).toBeNull();
    await act(() => pending);
    expect(api.createMultiDaySimulation).toHaveBeenCalledTimes(1);
    expect(api.createMultiDaySimulation.mock.calls[0][0].cells_mode).toBe("incremental");
    expect(result.current.simulationId).toBe("multi1");

    const md = FakeSocket.all[1];
    expect(md.url).toBe("ws://test/multi1");
    act(() => {
      // a message for another run on this socket is ignored
      md.emit({ type: "simulation.frame", simulation_id: "single1", frame: frame(4, 300) });
      md.emit({ type: "simulation.frame", simulation_id: "multi1", frame: frame(24, 900) });
      md.emit({ type: "simulation.frame", simulation_id: "multi1", frame: frame(72, 2000) });
      md.emit({ type: "simulation.completed", simulation_id: "multi1" });
    });
    expect(result.current.frames.map((f) => f.time_hours)).toEqual([0, 24, 72]);
    expect(result.current.status).toBe("completed");
  });

  it("multi-day incremental cells rebuild across a day boundary (frame sent whole)", async () => {
    api.createMultiDaySimulation.mockResolvedValue({ simulation_id: "multi1" });
    const { result } = renderHook(() => useSimulation());
    await act(() => result.current.startMultiDaySimulation({ days: [{}, {}] } as never));
    const c = (k: number) => ({ lat: 53.5 + k * 1e-3, lng: -113.5, t: k });
    const send = (t: number, cells: object[], offset: number) =>
      FakeSocket.all[0].emit({ type: "simulation.frame", simulation_id: "multi1",
        frame: { ...frame(t, t), burned_cells: cells, cells_offset: offset } });
    act(() => {
      send(12, [c(1), c(2)], 0);
      send(24, [c(3)], 2);
      send(36, [c(1), c(2), c(3), c(4)], 0); // day 2 starts: whole frame
      send(48, [c(5)], 4);
    });
    expect(result.current.frames.map((f) => f.burned_cells?.length ?? 0)).toEqual([0, 2, 3, 4, 5]);
    expect(result.current.frames.at(-1)!.burned_cells!.map((x) => x.t)).toEqual([1, 2, 3, 4, 5]);
  });

  it("multi-day then single: the single run starts clean", async () => {
    api.createMultiDaySimulation.mockResolvedValue({ simulation_id: "multi1" });
    api.createSimulation.mockResolvedValue({ simulation_id: "single2" });
    const { result } = renderHook(() => useSimulation());
    await act(() => result.current.startMultiDaySimulation({ days: [{}, {}] } as never));
    act(() => {
      FakeSocket.all[0].emit({ type: "simulation.frame", simulation_id: "multi1", frame: frame(48, 2000) });
      FakeSocket.all[0].emit({ type: "simulation.completed", simulation_id: "multi1" });
    });
    await act(() => result.current.startSimulation({ duration_hours: 4 } as never));
    expect(result.current.simulationId).toBe("single2");
    expect(result.current.frames).toEqual([]);
    expect(result.current.durationHours).toBe(4);
  });

  it("a late POST response from an older run is ignored", async () => {
    let resolveOld!: (v: { simulation_id: string }) => void;
    api.createSimulation.mockReturnValueOnce(new Promise((r) => (resolveOld = r)));
    api.createMultiDaySimulation.mockResolvedValue({ simulation_id: "multi1" });
    const { result } = renderHook(() => useSimulation());
    let first!: Promise<void>;
    act(() => {
      first = result.current.startSimulation({ duration_hours: 4 } as never);
    });
    await act(() => result.current.startMultiDaySimulation({ days: [{}] } as never));
    await act(async () => {
      resolveOld({ simulation_id: "single-late" });
      await first;
    });
    expect(result.current.simulationId).toBe("multi1");
    expect(FakeSocket.all.map((s) => s.url)).toEqual(["ws://test/multi1"]);
  });

  it("prepareRun (Run pressed, forecast still loading) clears the previous run at once", async () => {
    api.createSimulation.mockResolvedValue({ simulation_id: "single1" });
    const { result } = renderHook(() => useSimulation());
    await act(() => result.current.startSimulation({ duration_hours: 4 } as never));
    act(() => {
      FakeSocket.all[0].emit({ type: "simulation.frame", simulation_id: "single1", frame: frame(4, 300) });
      FakeSocket.all[0].emit({ type: "simulation.completed", simulation_id: "single1" });
    });
    act(() => result.current.prepareRun(6));
    expect(result.current.frames).toEqual([]);
    expect(result.current.simulationId).toBeNull();
    expect(result.current.isRunning).toBe(true);
    expect(result.current.durationHours).toBe(6);
    expect(FakeSocket.all[0].closed).toBe(true);
  });
});

describe("useEnsemble: the poll follows the current run only", () => {
  it("stops polling the old run when the run changes to one without an ensemble", async () => {
    api.getEnsemble.mockResolvedValue({ status: "running", done: 1, total: 30 });
    const { result, rerender } = renderHook(
      ({ id, n }: { id: string | null; n: number | null }) => useEnsemble(id, n, "completed"),
      { initialProps: { id: "single1", n: 30 } as { id: string | null; n: number | null } },
    );
    await waitFor(() => expect(result.current.phase).toBe("running"));
    const seen = api.getEnsemble.mock.calls.length;
    expect(api.getEnsemble.mock.calls.every(([id]) => id === "single1")).toBe(true);

    rerender({ id: null, n: null }); // multi-day starting: no ID yet, no ensemble
    expect(result.current.phase).toBe("off");
    rerender({ id: "multi1", n: null });
    await new Promise((r) => setTimeout(r, 2500));
    expect(api.getEnsemble.mock.calls.length).toBe(seen);
    expect(result.current.phase).toBe("off");
    // the in-flight request of the old poll was aborted
    const signal = api.getEnsemble.mock.calls.at(-1)![1] as AbortSignal;
    expect(signal.aborted).toBe(true);
  });

  it("a cancelled ensemble (new run started) turns the view off and stops polling", async () => {
    api.getEnsemble.mockResolvedValue({ status: "cancelled", done: 3, total: 30 });
    const { result } = renderHook(() => useEnsemble("single1", 30, "completed"));
    await waitFor(() => expect(api.getEnsemble).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(result.current.phase).toBe("off"));
    await new Promise((r) => setTimeout(r, 1500));
    expect(api.getEnsemble).toHaveBeenCalledTimes(1);
  });
});

// ── Round-3 field test (Claude in Chrome tester, 2026-10-13) ─────────────────

describe("useSimulation: multi-day progress and lost connections (R6, R12)", () => {
  it("keeps the day being computed from the status messages", async () => {
    api.createMultiDaySimulation.mockResolvedValue({ simulation_id: "md" });
    const { result } = renderHook(() => useSimulation());
    await act(() => result.current.startMultiDaySimulation({ days: [{}, {}, {}] } as never));
    act(() => FakeSocket.all[0].emit({ type: "simulation.status", simulation_id: "md", phase: "spread", progress: 0.45, day: 2, n_days: 3 }));
    expect(result.current.phase).toBe("spread");
    expect(result.current.progress).toBe(0.45);
    expect(result.current.day).toBe(2);
    expect(result.current.nDays).toBe(3);
    expect(result.current.durationHours).toBe(72);
  });

  it("keeps polling after a transient error when the socket drops (it used to stop silently)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.createMultiDaySimulation.mockResolvedValue({ simulation_id: "md" });
    api.getSimulation
      .mockRejectedValueOnce(new Error("502 Bad Gateway"))
      .mockResolvedValueOnce({ simulation_id: "md", status: "completed", frames: [frame(24, 10), frame(72, 30)], error: null });
    const { result } = renderHook(() => useSimulation());
    await act(() => result.current.startMultiDaySimulation({ days: [{}, {}, {}] } as never));
    act(() => FakeSocket.all[0].onclose?.());
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    await waitFor(() => expect(result.current.status).toBe("completed"));
    expect(api.getSimulation).toHaveBeenCalledTimes(2);
    expect(result.current.frames.at(-1)!.time_hours).toBe(72);
  });
});
