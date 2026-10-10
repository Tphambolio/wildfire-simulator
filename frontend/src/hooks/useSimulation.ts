/** Hook for managing simulation state and WebSocket streaming. */

import { useCallback, useRef, useState } from "react";
import { createSimulation, createMultiDaySimulation, createPerimeterOverride, getSimulation, getWebSocketUrl } from "../services/api";
import type {
  SimulationCreate,
  MultiDaySimulationCreate,
  PerimeterOverrideRequest,
  RunPhase,
  SimulationFrame,
  SimulationStatus,
  WSEvent,
} from "../types/simulation";

/** Synthetic T=0 frame prepended to every simulation so the scrubber always starts at ignition. */
const T0_FRAME: SimulationFrame = {
  time_hours: 0,
  perimeter: [],
  area_ha: 0,
  head_ros_m_min: 0,
  max_hfi_kw_m: 0,
  fire_type: "SURFACE",
  flame_length_m: 0,
  fuel_breakdown: {},
  spot_fires: null,
  burned_cells: null,
  num_fronts: 0,
};

interface SimulationState {
  simulationId: string | null;
  status: SimulationStatus | null;
  frames: SimulationFrame[];
  currentFrameIndex: number;
  error: string | null;
  isRunning: boolean;
  isPaused: boolean;
  /** Run progress for display (RunProgress): server phase, fraction 0-1, start time */
  phase: RunPhase | null;
  progress: number | null;
  startedAt: number | null;
  durationHours: number | null;
}

const IDLE: SimulationState = {
  simulationId: null,
  status: null,
  frames: [],
  currentFrameIndex: 0,
  error: null,
  isRunning: false,
  isPaused: false,
  phase: null,
  progress: null,
  startedAt: null,
  durationHours: null,
};

/** Progress from a frame's time (Huygens runs stream frames as they compute). */
export function frameProgress(prev: number | null, timeHours: number, durationHours: number | null): number | null {
  if (!durationHours || durationHours <= 0) return prev;
  const f = Math.min(1, Math.max(0, timeHours / durationHours));
  return prev === null ? f : Math.max(prev, f);
}

export function useSimulation() {
  const [state, setState] = useState<SimulationState>(IDLE);

  const wsRef = useRef<WebSocket | null>(null);
  // Each run gets a token; messages and polls from an older run are ignored, so a new run
  // started after a completed one is never overwritten by the old run's socket or poll
  const runTokenRef = useRef(0);

  /** Close the current run's socket without letting its close handler start a poll. */
  const detachSocket = () => {
    const ws = wsRef.current;
    wsRef.current = null;
    if (ws) {
      ws.onmessage = null;
      ws.onerror = null;
      ws.onclose = null;
      ws.close();
    }
  };

  const _startWithCreateFn = useCallback(async (
    createFn: () => Promise<{ simulation_id: string }>,
    durationHours: number | null = null,
  ) => {
    detachSocket();
    const token = ++runTokenRef.current;

    setState({
      ...IDLE,
      status: "running",
      isRunning: true,
      startedAt: Date.now(),
      durationHours,
    });

    try {
      const resp = await createFn();
      if (token !== runTokenRef.current) return;
      const simId = resp.simulation_id;

      setState((prev) => ({ ...prev, simulationId: simId }));

      // Connect WebSocket for real-time frames
      const ws = new WebSocket(getWebSocketUrl(simId));
      wsRef.current = ws;

      ws.onmessage = (event) => {
        if (token !== runTokenRef.current) return;
        const data: WSEvent = JSON.parse(event.data);

        if (data.type === "simulation.status" && data.phase) {
          setState((prev) => ({
            ...prev,
            phase: data.phase!,
            progress: typeof data.progress === "number" ? Math.max(prev.progress ?? 0, data.progress) : prev.progress,
          }));
        } else if (data.type === "simulation.frame" && data.frame) {
          setState((prev) => {
            const frame = withAllCells(data.frame!, prev.frames[prev.frames.length - 1]);
            // Prepend a synthetic T=0 frame on the very first real frame so the
            // scrubber always starts at the ignition state (nothing burning).
            const newFrames = prev.frames.length === 0
              ? [T0_FRAME, frame]
              : [...prev.frames, frame];
            return {
              ...prev,
              frames: newFrames,
              currentFrameIndex: newFrames.length - 1,
              progress: frameProgress(prev.progress, frame.time_hours, prev.durationHours),
            };
          });
        } else if (data.type === "simulation.completed") {
          setState((prev) => ({
            ...prev,
            status: "completed",
            isRunning: false,
            isPaused: false,
          }));
        } else if (data.type === "simulation.error") {
          setState((prev) => ({
            ...prev,
            status: "failed",
            error: data.error || "Unknown error",
            isRunning: false,
            isPaused: false,
          }));
        } else if (data.type === "status") {
          if (data.state === "paused") {
            setState((prev) => ({ ...prev, status: "paused", isPaused: true }));
          } else if (data.state === "running") {
            setState((prev) => ({ ...prev, status: "running", isPaused: false }));
          } else if (data.state === "cancelled") {
            setState((prev) => ({
              ...prev,
              status: "cancelled",
              isRunning: false,
              isPaused: false,
            }));
          }
        }
      };

      let polling = false;
      const fallback = () => {
        if (polling || token !== runTokenRef.current) return;
        polling = true;
        pollForResults(simId, token);
      };
      ws.onerror = () => {
        // Fallback to polling if WebSocket fails
        fallback();
      };

      ws.onclose = () => {
        if (wsRef.current === ws) wsRef.current = null;
        // If simulation is still running when WS closes (e.g. long data load),
        // fall back to polling so we still get results
        setState((prev) => {
          if (prev.isRunning) fallback();
          return prev;
        });
      };
    } catch (err) {
      if (token !== runTokenRef.current) return;
      setState((prev) => ({
        ...prev,
        status: "failed",
        error: err instanceof Error ? err.message : "Failed to start simulation",
        isRunning: false,
      }));
    }
  }, []);

  const startSimulation = useCallback(
    // Grid runs stream only newly burned cells; withAllCells rebuilds each frame's full list
    (params: SimulationCreate) =>
      _startWithCreateFn(() => createSimulation({ ...params, cells_mode: "incremental" }), params.duration_hours ?? null),
    [_startWithCreateFn]
  );

  const startMultiDaySimulation = useCallback(
    (params: MultiDaySimulationCreate) =>
      _startWithCreateFn(() => createMultiDaySimulation(params), (params.days?.length ?? 0) * 24 || null),
    [_startWithCreateFn]
  );

  const startPerimeterOverride = useCallback(
    (req: PerimeterOverrideRequest) => _startWithCreateFn(() => createPerimeterOverride(req), req.duration_hours ?? null),
    [_startWithCreateFn]
  );

  /** Clear the current results (frames, status) and stop listening to the run. */
  const clearResults = useCallback(() => {
    detachSocket();
    runTokenRef.current += 1;
    setState(IDLE);
  }, []);

  const pollForResults = useCallback(async (simId: string, token: number) => {
    const poll = async () => {
      if (token !== runTokenRef.current) return;
      try {
        const resp = await getSimulation(simId);
        if (token !== runTokenRef.current) return;
        const full: SimulationFrame[] = [];
        for (const f of resp.frames) full.push(withAllCells(f, full[full.length - 1]));
        const framesWithT0 = full.length > 0 ? [T0_FRAME, ...full] : [];
        setState((prev) => ({
          ...prev,
          frames: framesWithT0,
          status: resp.status,
          currentFrameIndex: framesWithT0.length - 1,
          isRunning: resp.status === "running",
          error: resp.error,
          phase: resp.phase ?? prev.phase,
          progress: typeof resp.progress === "number" ? Math.max(prev.progress ?? 0, resp.progress) : prev.progress,
        }));
        if (resp.status === "running") {
          setTimeout(poll, 1000);
        }
      } catch (err) {
        if (token !== runTokenRef.current) return;
        // On 404 (simulation not found after machine restart), surface the error
        const msg = err instanceof Error ? err.message : "";
        if (msg.includes("not found") || msg.includes("404")) {
          setState((prev) => ({
            ...prev,
            status: "failed",
            error: "Simulation lost — the server restarted. Please run the simulation again.",
            isRunning: false,
          }));
        }
        // Other transient network errors are ignored (retry next tick)
      }
    };
    poll();
  }, []);

  const setFrameIndex = useCallback((index: number) => {
    setState((prev) => ({
      ...prev,
      currentFrameIndex: Math.max(0, Math.min(index, prev.frames.length - 1)),
    }));
  }, []);

  const pauseSimulation = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ action: "pause" }));
    }
  }, []);

  const resumeSimulation = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ action: "resume" }));
    }
  }, []);

  const cancelSimulation = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ action: "cancel" }));
    }
  }, []);

  const currentFrame =
    state.frames.length > 0 ? state.frames[state.currentFrameIndex] : null;

  return {
    ...state,
    currentFrame,
    startSimulation,
    startMultiDaySimulation,
    startPerimeterOverride,
    clearResults,
    setFrameIndex,
    pauseSimulation,
    resumeSimulation,
    cancelSimulation,
  };
}

/**
 * A frame with all its burned cells. In incremental mode the server sends only the cells
 * burned since the previous frame, and ``cells_offset`` says how many earlier cells (the
 * previous frame's) come first.
 */
export function withAllCells(frame: SimulationFrame, prev: SimulationFrame | undefined): SimulationFrame {
  const offset = frame.cells_offset ?? 0;
  if (offset <= 0) return frame;
  const earlier = (prev?.burned_cells ?? []).slice(0, offset);
  return { ...frame, burned_cells: earlier.concat(frame.burned_cells ?? []), cells_offset: 0 };
}
