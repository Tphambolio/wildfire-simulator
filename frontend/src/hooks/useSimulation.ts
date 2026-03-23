/** Hook for managing simulation state and WebSocket streaming. */

import { useCallback, useRef, useState } from "react";
import { createSimulation, createMultiDaySimulation, createPerimeterOverride, getSimulation, getWebSocketUrl } from "../services/api";
import type {
  SimulationCreate,
  MultiDaySimulationCreate,
  PerimeterOverrideRequest,
  SimulationFrame,
  SimulationStatus,
  WSEvent,
} from "../types/simulation";

interface SimulationState {
  simulationId: string | null;
  status: SimulationStatus | null;
  frames: SimulationFrame[];
  currentFrameIndex: number;
  error: string | null;
  isRunning: boolean;
  isPaused: boolean;
}

export function useSimulation() {
  const [state, setState] = useState<SimulationState>({
    simulationId: null,
    status: null,
    frames: [],
    currentFrameIndex: 0,
    error: null,
    isRunning: false,
    isPaused: false,
  });

  const wsRef = useRef<WebSocket | null>(null);

  const _startWithCreateFn = useCallback(async (
    createFn: () => Promise<{ simulation_id: string }>
  ) => {
    // Close existing WebSocket
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }

    setState({
      simulationId: null,
      status: "running",
      frames: [],
      currentFrameIndex: 0,
      error: null,
      isRunning: true,
      isPaused: false,
    });

    try {
      const resp = await createFn();
      const simId = resp.simulation_id;

      setState((prev) => ({ ...prev, simulationId: simId }));

      // Connect WebSocket for real-time frames
      const ws = new WebSocket(getWebSocketUrl(simId));
      wsRef.current = ws;

      ws.onmessage = (event) => {
        const data: WSEvent = JSON.parse(event.data);

        if (data.type === "simulation.frame" && data.frame) {
          setState((prev) => {
            const newFrames = [...prev.frames, data.frame!];
            return {
              ...prev,
              frames: newFrames,
              currentFrameIndex: newFrames.length - 1,
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

      ws.onerror = () => {
        // Fallback to polling if WebSocket fails
        pollForResults(simId);
      };

      ws.onclose = () => {
        wsRef.current = null;
        // If simulation is still running when WS closes (e.g. long data load),
        // fall back to polling so we still get results
        setState((prev) => {
          if (prev.isRunning) {
            pollForResults(simId);
          }
          return prev;
        });
      };
    } catch (err) {
      setState((prev) => ({
        ...prev,
        status: "failed",
        error: err instanceof Error ? err.message : "Failed to start simulation",
        isRunning: false,
      }));
    }
  }, []);

  const startSimulation = useCallback(
    (params: SimulationCreate) => _startWithCreateFn(() => createSimulation(params)),
    [_startWithCreateFn]
  );

  const startMultiDaySimulation = useCallback(
    (params: MultiDaySimulationCreate) => _startWithCreateFn(() => createMultiDaySimulation(params)),
    [_startWithCreateFn]
  );

  const startPerimeterOverride = useCallback(
    (req: PerimeterOverrideRequest) => _startWithCreateFn(() => createPerimeterOverride(req)),
    [_startWithCreateFn]
  );

  const pollForResults = useCallback(async (simId: string) => {
    const poll = async () => {
      try {
        const resp = await getSimulation(simId);
        setState((prev) => ({
          ...prev,
          frames: resp.frames,
          status: resp.status,
          currentFrameIndex: resp.frames.length - 1,
          isRunning: resp.status === "running",
          error: resp.error,
        }));
        if (resp.status === "running") {
          setTimeout(poll, 1000);
        }
      } catch {
        // Ignore polling errors
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
    setFrameIndex,
    pauseSimulation,
    resumeSimulation,
    cancelSimulation,
  };
}
