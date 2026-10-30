/** Persist incident sessions (multi-day operational periods) to localStorage. */

import type { RunParams } from "../components/WeatherPanel";
import { useState, useCallback, useSyncExternalStore } from "react";
import { fetchNearbyFacilities } from "../services/overpass";
import type { SimulationFrame } from "../types/simulation";
import type {
  IncidentSession,
  OperationalPeriod,
  IncidentAnnotation,
  EvacDecisionRecord,
  FrameSummary,
} from "../types/incident";
import { makeIncident, makeOperationalPeriod, type PeriodClock } from "../types/incident";
import { upsertTier, type EvacTier } from "../utils/evacZones";
import { loadIncidents, normaliseIncident, normaliseIncidentName, writeStored } from "../utils/storage";

const STORAGE_KEY = "firesim-v3-incidents";
const MAX_INCIDENTS = 20;

function loadFromStorage(): IncidentSession[] {
  // Defensive: corrupted or older-version incidents are normalised or dropped (utils/storage.ts)
  return loadIncidents(STORAGE_KEY);
}

// Result of the last write: false when the browser refused it (quota exceeded, storage blocked).
// A tiny external store (useSyncExternalStore): writes happen inside state updaters, so the
// listeners are told after the current render, not during it.
let lastSaveOk = true;
const saveListeners = new Set<() => void>();
const subscribeSave = (l: () => void) => {
  saveListeners.add(l);
  return () => {
    saveListeners.delete(l);
  };
};
const getSaveOk = () => lastSaveOk;

function saveToStorage(incidents: IncidentSession[]): void {
  const ok = writeStored(STORAGE_KEY, incidents);
  if (ok === lastSaveOk) return;
  lastSaveOk = ok;
  queueMicrotask(() => saveListeners.forEach((l) => l()));
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

  // Every change is written to localStorage; when the browser refuses (storage full), say so
  // rather than lose the incident silently on the next reload (QA 2026-10-11)
  const saveFailed = !useSyncExternalStore(subscribeSave, getSaveOk);

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
    (name: string, periods?: PeriodClock[]): IncidentSession => {
      const incident = makeIncident(name, periods);
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

  // Resuming a closed incident reopens it, so it is restored after a reload (only active
  // incidents are restored)
  const loadIncident = useCallback((id: string) => {
    setIncidents((prev) => {
      if (!prev.some((i) => i.id === id && i.status === "closed")) return prev;
      const next = prev.map((i) => (i.id === id ? { ...i, status: "active" as const, updatedAt: new Date().toISOString() } : i));
      saveToStorage(next);
      return next;
    });
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

  // The name follows the stored-incident rule (utils/storage.ts): trimmed, empty → "Untitled
  // incident", so what is kept agrees with what a reload reads back (E9, 2026-10-24)
  const updateIncidentField = useCallback(
    <K extends keyof IncidentSession>(key: K, value: IncidentSession[K]) => {
      const v = key === "name" ? (normaliseIncidentName(value) as IncidentSession[K]) : value;
      updateActiveIncident((i) => ({ ...i, [key]: v }));
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

  // ── OSM facility fetch ────────────────────────────────────────────────────

  const fetchAndPlaceFacilities = useCallback(
    async (lat: number, lng: number): Promise<number> => {
      const facilities = await fetchNearbyFacilities(lat, lng);
      if (facilities.length === 0) return 0;
      const existingOsmIds = new Set(
        (activePeriod?.annotations ?? []).map(a => a.properties.osm_id).filter(Boolean)
      );
      let added = 0;
      for (const f of facilities) {
        if (existingOsmIds.has(f.osmId)) continue;
        addAnnotation({
          id: crypto.randomUUID(),
          layer: f.layer,
          type: "symbol",
          symbolKey: f.symbolKey,
          coordinates: [[f.lng, f.lat]],
          label: f.name,
          properties: { ...f.properties, phone: f.phone, address: f.address, source: "osm", osm_id: f.osmId },
          operationalDay: activePeriod?.day ?? 1,
          createdAt: new Date().toISOString(),
        });
        added++;
      }
      return added;
    },
    [activePeriod, addAnnotation]
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

  // ── Evacuation status set by Planning (incident-wide, carried across periods) ──

  const setEvacTier = useCallback(
    (neighbourhood: string, tier: EvacTier | null) => {
      updateActiveIncident((i) => ({ ...i, evacTiers: upsertTier(i.evacTiers ?? [], neighbourhood, tier) }));
    },
    [updateActiveIncident]
  );

  // ── Frame data ────────────────────────────────────────────────────────────

  /**
   * Save a completed run into an operational period: by default the active one; `target` names
   * the incident and period the run was started for, so switching periods while it computes
   * does not file it under the wrong day.
   */
  const saveFrameData = useCallback(
    (
      frames: SimulationFrame[],
      simulationId: string,
      target?: { incidentId: string; periodIndex: number } | null,
      runStartIso?: string | null,
      runParams?: RunParams | null,
    ) => {
      const summaries = framestoSummaries(frames);
      const lastFrame = frames[frames.length - 1];
      const finalPerimeter: [number, number][] | null = lastFrame?.perimeter?.length
        ? (lastFrame.perimeter as [number, number][])
        : null;
      const apply = (p: OperationalPeriod): OperationalPeriod => ({
        ...p,
        simulationId,
        simulationStatus: "completed",
        frameSummaries: summaries,
        finalPerimeter,
        runStartIso: runStartIso ?? p.runStartIso ?? null,
        runParams: runParams ?? p.runParams ?? null,
      });
      if (!target) {
        updateActivePeriod(apply);
        return;
      }
      setIncidents((prev) => {
        const next = prev.map((i) => {
          if (i.id !== target.incidentId || !i.operationalPeriods[target.periodIndex]) return i;
          const periods = i.operationalPeriods.map((p, idx) => (idx === target.periodIndex ? apply(p) : p));
          return { ...i, operationalPeriods: periods, updatedAt: new Date().toISOString() };
        });
        saveToStorage(next);
        return next;
      });
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
        const periods = incident.operationalPeriods ?? [];
        // A stale activePeriodIndex (old stored incident) falls back to the last period
        const currentPeriod = periods[incident.activePeriodIndex] ?? periods[periods.length - 1];
        if (!currentPeriod) return incident;
        // The new period is appended after the last one (and becomes active)
        const nextDay = (periods[periods.length - 1]?.day ?? currentPeriod.day) + 1;
        const newPeriod: OperationalPeriod = {
          ...makeOperationalPeriod(nextDay, opts.date),
          weather: opts.weather ?? currentPeriod.weather,
          fwi: opts.fwi ?? currentPeriod.fwi,
          opPeriodStart: opts.opPeriodStart ?? "08:00",
          opPeriodEnd: opts.opPeriodEnd ?? "20:00",
          // Carry forward ignition point. Map annotations stay with their own period (each
          // period starts with none; the previous period's are on its own tab)
          ignitionPoint: currentPeriod.ignitionPoint,
        };
        return {
          ...incident,
          operationalPeriods: [...periods, newPeriod],
          activePeriodIndex: periods.length,
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
          // Same normalisation as stored incidents (older exports, missing fields)
          const normalised = parsed?.name && Array.isArray(parsed.operationalPeriods)
            ? normaliseIncident({ ...parsed, id: crypto.randomUUID() })
            : null;
          if (!normalised) {
            reject(new Error("Invalid incident file"));
            return;
          }
          const next: IncidentSession = {
            ...normalised,
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
    activeIncidentId,
    incidents,
    activePeriod,
    /** The last write to browser storage failed (e.g. storage full): changes are not saved */
    saveFailed,
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
    fetchAndPlaceFacilities,
    // Evac
    commitEvacDecision,
    setEvacTier,
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
