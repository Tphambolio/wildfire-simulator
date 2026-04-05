/** Persist incident sessions (multi-day operational periods) to localStorage. */

import { useState, useCallback } from "react";
import type { SimulationFrame } from "../types/simulation";
import type {
  IncidentSession,
  OperationalPeriod,
  IncidentAnnotation,
  EvacDecisionRecord,
  FrameSummary,
} from "../types/incident";
import { makeIncident, makeOperationalPeriod } from "../types/incident";

const STORAGE_KEY = "firesim-v3-incidents";
const MAX_INCIDENTS = 20;

function loadFromStorage(): IncidentSession[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    return JSON.parse(raw) as IncidentSession[];
  } catch {
    return [];
  }
}

function saveToStorage(incidents: IncidentSession[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(incidents));
  } catch {
    // quota exceeded — silently fail
  }
}

function framestoSummaries(frames: SimulationFrame[]): FrameSummary[] {
  return frames.map((f) => ({
    timeHours: f.time_hours,
    areaHa: f.area_ha,
    headRosMMin: f.head_ros_m_min,
    maxHfiKwM: f.max_hfi_kw_m,
    fireType: typeof f.fire_type === "string" ? f.fire_type : String(f.fire_type),
    flameLengthM: f.flame_length_m,
    day: f.day ?? undefined,
  }));
}

export function useIncident() {
  const [incidents, setIncidents] = useState<IncidentSession[]>(loadFromStorage);
  const [activeIncidentId, setActiveIncidentId] = useState<string | null>(() => {
    // Auto-restore the most recently updated active incident
    const all = loadFromStorage();
    const active = all.find((i) => i.status === "active");
    return active?.id ?? null;
  });

  const activeIncident = incidents.find((i) => i.id === activeIncidentId) ?? null;
  const activePeriod = activeIncident
    ? activeIncident.operationalPeriods[activeIncident.activePeriodIndex] ?? null
    : null;

  // ── Persist helpers ────────────────────────────────────────────────────────

  const updateActiveIncident = useCallback(
    (updater: (incident: IncidentSession) => IncidentSession) => {
      setIncidents((prev) => {
        const next = prev.map((i) =>
          i.id === activeIncidentId
            ? { ...updater(i), updatedAt: new Date().toISOString() }
            : i
        );
        saveToStorage(next);
        return next;
      });
    },
    [activeIncidentId]
  );

  const updateActivePeriod = useCallback(
    (updater: (period: OperationalPeriod) => OperationalPeriod) => {
      updateActiveIncident((incident) => {
        const periods = incident.operationalPeriods.map((p, idx) =>
          idx === incident.activePeriodIndex ? updater(p) : p
        );
        return { ...incident, operationalPeriods: periods };
      });
    },
    [updateActiveIncident]
  );

  // ── CRUD ──────────────────────────────────────────────────────────────────

  const createIncident = useCallback(
    (name: string): IncidentSession => {
      const incident = makeIncident(name);
      setIncidents((prev) => {
        const next = [incident, ...prev].slice(0, MAX_INCIDENTS);
        saveToStorage(next);
        return next;
      });
      setActiveIncidentId(incident.id);
      return incident;
    },
    []
  );

  const loadIncident = useCallback((id: string) => {
    setActiveIncidentId(id);
  }, []);

  const closeIncident = useCallback(() => {
    updateActiveIncident((i) => ({ ...i, status: "closed" }));
    setActiveIncidentId(null);
  }, [updateActiveIncident]);

  const deleteIncident = useCallback((id: string) => {
    setIncidents((prev) => {
      const next = prev.filter((i) => i.id !== id);
      saveToStorage(next);
      return next;
    });
    if (activeIncidentId === id) setActiveIncidentId(null);
  }, [activeIncidentId]);

  const updateIncidentField = useCallback(
    <K extends keyof IncidentSession>(key: K, value: IncidentSession[K]) => {
      updateActiveIncident((i) => ({ ...i, [key]: value }));
    },
    [updateActiveIncident]
  );

  // ── Annotations ──────────────────────────────────────────────────────────

  const addAnnotation = useCallback(
    (annotation: IncidentAnnotation) => {
      updateActivePeriod((p) => ({
        ...p,
        annotations: [...p.annotations, annotation],
      }));
    },
    [updateActivePeriod]
  );

  const removeAnnotation = useCallback(
    (id: string) => {
      updateActivePeriod((p) => ({
        ...p,
        annotations: p.annotations.filter((a) => a.id !== id),
      }));
    },
    [updateActivePeriod]
  );

  const clearLayerAnnotations = useCallback(
    (layer: IncidentAnnotation["layer"]) => {
      updateActivePeriod((p) => ({
        ...p,
        annotations: p.annotations.filter((a) => a.layer !== layer),
      }));
    },
    [updateActivePeriod]
  );

  // ── Evac decisions ────────────────────────────────────────────────────────

  const commitEvacDecision = useCallback(
    (decision: Omit<EvacDecisionRecord, "id" | "timestamp">) => {
      const record: EvacDecisionRecord = {
        ...decision,
        id: crypto.randomUUID(),
        timestamp: new Date().toISOString(),
      };
      updateActivePeriod((p) => ({
        ...p,
        evacuationDecisions: [...p.evacuationDecisions, record],
      }));
    },
    [updateActivePeriod]
  );

  // ── Frame data ────────────────────────────────────────────────────────────

  const saveFrameData = useCallback(
    (frames: SimulationFrame[], simulationId: string) => {
      const summaries = framestoSummaries(frames);
      const lastFrame = frames[frames.length - 1];
      const finalPerimeter: [number, number][] | null = lastFrame?.perimeter
        ? (lastFrame.perimeter as [number, number][])
        : null;
      updateActivePeriod((p) => ({
        ...p,
        simulationId,
        simulationStatus: "completed",
        frameSummaries: summaries,
        finalPerimeter,
      }));
    },
    [updateActivePeriod]
  );

  const setSimulationId = useCallback(
    (simulationId: string, status: string) => {
      updateActivePeriod((p) => ({ ...p, simulationId, simulationStatus: status }));
    },
    [updateActivePeriod]
  );

  const updatePeriodField = useCallback(
    <K extends keyof OperationalPeriod>(key: K, value: OperationalPeriod[K]) => {
      updateActivePeriod((p) => ({ ...p, [key]: value }));
    },
    [updateActivePeriod]
  );

  // ── Multi-day: advance to next operational period ─────────────────────────

  const advancePeriod = useCallback(
    (opts: {
      date: string;
      opPeriodStart?: string;
      opPeriodEnd?: string;
      weather?: OperationalPeriod["weather"];
      fwi?: OperationalPeriod["fwi"];
    }) => {
      updateActiveIncident((incident) => {
        const currentPeriod = incident.operationalPeriods[incident.activePeriodIndex];
        const nextDay = currentPeriod.day + 1;
        const newPeriod: OperationalPeriod = {
          ...makeOperationalPeriod(nextDay, opts.date),
          weather: opts.weather ?? currentPeriod.weather,
          fwi: opts.fwi ?? currentPeriod.fwi,
          opPeriodStart: opts.opPeriodStart ?? "08:00",
          opPeriodEnd: opts.opPeriodEnd ?? "20:00",
          // Carry forward ignition point
          ignitionPoint: currentPeriod.ignitionPoint,
          // Day 1 annotations carry forward (each annotation has operationalDay tag)
        };
        return {
          ...incident,
          operationalPeriods: [...incident.operationalPeriods, newPeriod],
          activePeriodIndex: incident.activePeriodIndex + 1,
        };
      });
    },
    [updateActiveIncident]
  );

  const setActivePeriodIndex = useCallback(
    (index: number) => {
      updateActiveIncident((i) => {
        if (index < 0 || index >= i.operationalPeriods.length) return i;
        return { ...i, activePeriodIndex: index };
      });
    },
    [updateActiveIncident]
  );

  // ── Export / Import ───────────────────────────────────────────────────────

  const exportIncident = useCallback(
    (incident: IncidentSession) => {
      const blob = new Blob([JSON.stringify(incident, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `firesim-incident-${incident.name.replace(/\s+/g, "-").toLowerCase()}.json`;
      a.click();
      URL.revokeObjectURL(url);
    },
    []
  );

  const importIncident = useCallback((file: File): Promise<IncidentSession> => {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = (e) => {
        try {
          const parsed = JSON.parse(e.target?.result as string) as IncidentSession;
          if (!parsed.name || !parsed.operationalPeriods) {
            reject(new Error("Invalid incident file"));
            return;
          }
          const next: IncidentSession = {
            ...parsed,
            id: crypto.randomUUID(),
            createdAt: new Date().toISOString(),
            updatedAt: new Date().toISOString(),
          };
          setIncidents((prev) => {
            const updated = [next, ...prev].slice(0, MAX_INCIDENTS);
            saveToStorage(updated);
            return updated;
          });
          setActiveIncidentId(next.id);
          resolve(next);
        } catch {
          reject(new Error("Could not parse incident file"));
        }
      };
      reader.onerror = () => reject(new Error("Could not read file"));
      reader.readAsText(file);
    });
  }, []);

  return {
    // State
    incident: activeIncident,
    incidents,
    activePeriod,
    // CRUD
    createIncident,
    loadIncident,
    closeIncident,
    deleteIncident,
    updateIncidentField,
    updatePeriodField,
    // Annotations
    addAnnotation,
    removeAnnotation,
    clearLayerAnnotations,
    // Evac
    commitEvacDecision,
    // Frames
    saveFrameData,
    setSimulationId,
    // Multi-day
    advancePeriod,
    setActivePeriodIndex,
    // Export
    exportIncident,
    importIncident,
  };
}
