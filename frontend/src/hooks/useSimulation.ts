/** Hook for managing simulation state and WebSocket streaming. */

import { useCallback, useRef, useState } from "react";
import { createSimulation, createMultiDaySimulation, createObservedPerimeter, createPerimeterOverride, getSimulation, getWebSocketUrl } from "../services/api";
import type {
  ActiveSegmentsInfo,
  EmberM8Info,
  ObservedPerimeterRequest,
  SimulationCreate,
  MultiDaySimulationCreate,
  PerimeterOverrideRequest,
  RunPhase,
  SimulationFrame,
  SimulationStatus,
  WSEvent,
} from "../types/simulation";

/** Poll retries after transient errors before giving up (about 4 minutes in all). */
const POLL_RETRIES = 30;

/**
 * Synthetic T=0 frame, prepended only when a run's first frame is after the start, so the
 * scrubber always starts at ignition. The engine's own first frame is at T+0 (the ignition
 * cell, or the observed perimeter of a restart), so it is normally not needed: prepending it
 * anyway showed two start frames (owner field test 2026-10-12, bug 11).
 */
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
  /** Multi-day runs: the day being computed (1-based) and the number of days */
  day: number | null;
  nDays: number | null;
  startedAt: number | null;
  durationHours: number | null;
  /** The run was read from the server's run archive (it finished before the API restarted) */
  archived: boolean;
  /** Server flag: the scenario is dated before the run's fuel grid year (null = none) */
  fuelVintageWarning: string | null;
  /** Active-segment restarts (A1): segments active / inactive and the modelled static share */
  activeSegments: ActiveSegmentsInfo | null;
  /** Opt-in M8 spot-fire likelihood of a restart (relative tiers; [R70]) */
  emberM8: EmberM8Info | null;
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
  day: null,
  nDays: null,
  startedAt: null,
  durationHours: null,
  archived: false,
  fuelVintageWarning: null,
  activeSegments: null,
  emberM8: null,
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

  /**
   * Drop the previous run (socket, poll, frames, status) and show a new run as starting.
   * Returns the new run's token; anything still arriving for an older token is ignored.
   */
  const beginRun = useCallback((durationHours: number | null): number => {
    detachSocket();
    const token = ++runTokenRef.current;
    setState({
      ...IDLE,
      status: "running",
      isRunning: true,
      startedAt: Date.now(),
      durationHours,
    });
    return token;
  }, []);

  /**
   * A run was requested but its request is still being prepared (e.g. the hourly forecast is
   * being fetched): clear the previous run now, so it is not shown, polled or re-run meanwhile.
   */
  const prepareRun = useCallback((durationHours: number | null = null) => {
    beginRun(durationHours);
  }, [beginRun]);

  const _startWithCreateFn = useCallback(async (
    createFn: () => Promise<{ simulation_id: string }>,
    durationHours: number | null = null,
  ) => {
    const token = beginRun(durationHours);

    try {
      const resp = await createFn();
      if (token !== runTokenRef.current) return;
      const simId = resp.simulation_id;

      setState((prev) => ({ ...prev, simulationId: simId }));

      // Connect WebSocket for real-time frames
      const ws = new WebSocket(getWebSocketUrl(simId));
      wsRef.current = ws;

      let received = 0; // frames this socket delivered
      ws.onmessage = (event) => {
        if (token !== runTokenRef.current) return;
        const data: WSEvent = JSON.parse(event.data);
        // Belt and braces: only this run's events (the socket is per run)
        if (data.simulation_id && data.simulation_id !== simId) return;

        if (data.type === "simulation.status" && data.phase) {
          setState((prev) => ({
            ...prev,
            phase: data.phase!,
            progress: typeof data.progress === "number" ? Math.max(prev.progress ?? 0, data.progress) : prev.progress,
            day: typeof data.day === "number" ? data.day : prev.day,
            nDays: typeof data.n_days === "number" ? data.n_days : prev.nDays,
          }));
        } else if (data.type === "simulation.frame" && data.frame) {
          received += 1;
          setState((prev) => {
            const frame = withAllCells(data.frame!, prev.frames[prev.frames.length - 1]);
            // The first frame: prepend a synthetic T=0 only if the run's own starts later
            const newFrames = prev.frames.length === 0
              ? withStartFrame([frame])
              : [...prev.frames, frame];
            return {
              ...prev,
              frames: newFrames,
              currentFrameIndex: newFrames.length - 1,
              progress: frameProgress(prev.progress, frame.time_hours, prev.durationHours),
            };
          });
        } else if (data.type === "simulation.completed") {
          // The server says how many frames the run has: if any did not arrive (a dropped
          // connection), read the run once from the server rather than show a short run
          const expected = typeof data.n_frames === "number" ? data.n_frames : null;
          if (expected !== null && received < expected) {
            fallback();
          } else {
            setState((prev) => ({
              ...prev, status: "completed", isRunning: false, isPaused: false,
              fuelVintageWarning: data.fuel_vintage_warning ?? null,
              activeSegments: data.active_segments ?? prev.activeSegments,
              emberM8: data.ember_m8 ?? prev.emberM8,
            }));
          }
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
  // eslint-disable-next-line react-hooks/exhaustive-deps -- pollForResults is stable (no deps)
  }, [beginRun]);

  const startSimulation = useCallback(
    // Grid runs stream only newly burned cells; withAllCells rebuilds each frame's full list
    (params: SimulationCreate) =>
      _startWithCreateFn(() => createSimulation({ ...params, cells_mode: "incremental" }), params.duration_hours ?? null),
    [_startWithCreateFn]
  );

  const startMultiDaySimulation = useCallback(
    (params: MultiDaySimulationCreate) =>
      // Incremental cells as for single runs: a 3-day Edmonton grid run streams ~1.7 MB, not ~21 MB
      _startWithCreateFn(
        () => createMultiDaySimulation({ ...params, cells_mode: "incremental" }),
        (params.days?.length ?? 0) * 24 || null,
      ),
    [_startWithCreateFn]
  );

  const startPerimeterOverride = useCallback(
    (req: PerimeterOverrideRequest) => _startWithCreateFn(() => createPerimeterOverride(req), req.duration_hours ?? null),
    [_startWithCreateFn]
  );

  const startObservedPerimeter = useCallback(
    (req: ObservedPerimeterRequest) =>
      _startWithCreateFn(() => createObservedPerimeter({ ...req, cells_mode: "incremental" }), req.duration_hours ?? null),
    [_startWithCreateFn]
  );

  /** Clear the current results (frames, status) and stop listening to the run. */
  const clearResults = useCallback(() => {
    detachSocket();
    runTokenRef.current += 1;
    setState(IDLE);
  }, []);

  const pollForResults = useCallback(async (simId: string, token: number, lostMessage?: string) => {
    let failures = 0;
    const poll = async () => {
      if (token !== runTokenRef.current) return;
      try {
        const resp = await getSimulation(simId);
        if (token !== runTokenRef.current) return;
        failures = 0;
        const full: SimulationFrame[] = [];
        for (const f of resp.frames) full.push(withAllCells(f, full[full.length - 1]));
        const framesWithT0 = withStartFrame(full);
        setState((prev) => ({
          ...prev,
          frames: framesWithT0,
          status: resp.status,
          currentFrameIndex: framesWithT0.length - 1,
          isRunning: resp.status === "running",
          error: resp.error,
          archived: resp.archived === true,
          fuelVintageWarning: resp.fuel_vintage_warning ?? null,
          activeSegments: resp.active_segments ?? null,
          emberM8: resp.ember_m8 ?? null,
          phase: resp.phase ?? prev.phase,
          progress: typeof resp.progress === "number" ? Math.max(prev.progress ?? 0, resp.progress) : prev.progress,
          day: typeof resp.day === "number" ? resp.day : prev.day,
          nDays: typeof resp.n_days === "number" ? resp.n_days : prev.nDays,
        }));
        if (resp.status === "running" || resp.status === "paused" || resp.status === "pending") {
          // Each poll returns every frame so far: long runs are polled less often, so the
          // polls do not take the server's CPU from the run
          setTimeout(poll, resp.frames.length > 60 ? 3000 : 1000);
        }
      } catch (err) {
        if (token !== runTokenRef.current) return;
        // On 404 (simulation not found after machine restart), surface the error
        const msg = err instanceof Error ? err.message : "";
        if (msg.includes("not found") || msg.includes("404")) {
          setState((prev) => ({
            ...prev,
            status: "failed",
            error: lostMessage ?? "Simulation lost — the server restarted. Please run the simulation again.",
            isRunning: false,
          }));
          return;
        }
        // A transient failure (timeout, 502 while the server is busy): try again. Polling used
        // to stop here silently, leaving a run shown as running, or cut short (round-3 R6).
        failures += 1;
        if (failures <= POLL_RETRIES) {
          setTimeout(poll, Math.min(10_000, 1000 * 2 ** Math.min(failures, 4)));
        } else {
          setState((prev) => ({
            ...prev,
            error: "Lost contact with the simulation server while reading this run. Reload the run to try again.",
            isRunning: false,
            status: "failed",
          }));
        }
      }
    };
    poll();
  }, []);

  /**
   * Load a finished run from the server by ID (e.g. an operational period's run after a
   * reload). The server answers from memory or, after a restart or deploy, from its run archive
   * (`archived: true`). A run in neither (removed by the archive's retention, or finished before
   * the archive existed) ends "failed" with a message saying so. A run still computing is
   * polled until it completes (a reload during a run, field test 2026-10-18). `lostMessage`
   * replaces the "not found" error (a re-attach after a reload says it its own way).
   */
  const restoreRun = useCallback((
    simId: string,
    durationHours: number | null = null,
    startedAt: number | null = null,
    lostMessage?: string,
  ) => {
    const token = beginRun(durationHours);
    // A run re-attached after a page reload keeps its own start for the elapsed timer
    setState((prev) => ({ ...prev, simulationId: simId, startedAt: startedAt ?? prev.startedAt }));
    pollForResults(
      simId,
      token,
      lostMessage ??
        "This run is no longer on the server or in its run archive (removed after the retention " +
          "period, or run before the archive existed). The period keeps its summary and final " +
          "perimeter; run it again to see the full run.",
    );
  }, [beginRun, pollForResults]);

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
    startObservedPerimeter,
    prepareRun,
    clearResults,
    restoreRun,
    setFrameIndex,
    pauseSimulation,
    resumeSimulation,
    cancelSimulation,
  };
}

/** Frames with the synthetic T=0 frame in front when the first frame is after the start. */
export function withStartFrame(frames: SimulationFrame[]): SimulationFrame[] {
  if (frames.length === 0) return frames;
  return frames[0].time_hours > 1e-9 ? [T0_FRAME, ...frames] : frames;
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
