/**
 * Poll the ensemble of a run (GET /simulations/{id}/ensemble) until it completes, then decode
 * its rasters. The API starts the ensemble after the deterministic frames, so the hook reports
 * "waiting" while the single run streams, then "running" with done/total.
 */

import { useEffect, useState } from "react";
import { getEnsemble, NoEnsembleError } from "../services/api";
import { decodeEnsemble, type EnsembleGrids } from "../utils/ensemble";

export type EnsemblePhase = "off" | "waiting" | "running" | "completed" | "failed";

export interface EnsembleState {
  phase: EnsemblePhase;
  done: number;
  total: number;
  grids: EnsembleGrids | null;
  error: string | null;
}

const OFF: EnsembleState = { phase: "off", done: 0, total: 0, grids: null, error: null };
const POLL_MS = 1000;
/** Consecutive network errors tolerated before giving up */
const MAX_ERRORS = 10;

/**
 * @param simulationId  the run (null before the POST returns)
 * @param members       ensemble size requested with the run (null = none requested)
 * @param runStatus     the deterministic run's status; a failed or cancelled run has no ensemble
 */
export function useEnsemble(
  simulationId: string | null,
  members: number | null,
  runStatus: string | null,
): EnsembleState {
  const active = !!simulationId && !!members && runStatus !== "failed" && runStatus !== "cancelled";
  const key = active ? `${simulationId}:${members}` : null;
  // State is tagged with the run it belongs to, so a new run starts from "waiting" at once
  const [state, setState] = useState<{ key: string | null; value: EnsembleState }>({ key: null, value: OFF });

  useEffect(() => {
    if (!key || !simulationId) return;
    const ctl = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let errors = 0;
    const set = (value: EnsembleState) => {
      if (!ctl.signal.aborted) setState({ key, value });
    };
    const poll = async () => {
      try {
        const resp = await getEnsemble(simulationId, ctl.signal);
        errors = 0;
        if (resp.status === "completed") {
          const grids = decodeEnsemble(resp);
          set({ phase: grids ? "completed" : "failed", done: resp.done, total: resp.total, grids, error: grids ? null : "Ensemble result incomplete" });
          return;
        }
        if (resp.status === "failed") {
          set({ phase: "failed", done: resp.done, total: resp.total, grids: null, error: resp.error ?? "Ensemble failed" });
          return;
        }
        set({ phase: resp.status === "running" ? "running" : "waiting", done: resp.done, total: resp.total || members!, grids: null, error: null });
      } catch (err) {
        if (ctl.signal.aborted) return;
        if (err instanceof NoEnsembleError) {
          set(OFF);
          return;
        }
        if (++errors >= MAX_ERRORS) {
          set({ phase: "failed", done: 0, total: members!, grids: null, error: err instanceof Error ? err.message : "Ensemble unavailable" });
          return;
        }
      }
      timer = setTimeout(poll, POLL_MS);
    };
    void poll();
    return () => {
      ctl.abort();
      clearTimeout(timer);
    };
  }, [key, simulationId, members]);

  if (!key) return OFF;
  if (state.key !== key) return { ...OFF, phase: "waiting", total: members! };
  return state.value;
}
