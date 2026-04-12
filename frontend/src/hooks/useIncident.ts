/** Persist incident sessions (multi-day operational periods) to localStorage. */

import { useState, useCallback } from "react";
import { fetchNearbyFacilities } from "../services/overpass";
import type {
  IncidentSession,
  OperationalPeriod,
  IncidentAnnotation,
  EvacDecisionRecord,
  HazardZone,
} from "../types/incident";
import { makeIncident, makeOperationalPeriod } from "../types/incident";

const STORAGE_KEY = "aims-console-incidents";
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

  const updatePeriodField = useCallback(
    <K extends keyof OperationalPeriod>(key: K, value: OperationalPeriod[K]) => {
      updateActivePeriod((p) => ({ ...p, [key]: value }));
    },
    [updateActivePeriod]
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

  // ── Hazard zones ──────────────────────────────────────────────────────────

  const addHazardZone = useCallback(
    (zone: HazardZone) => {
      updateActivePeriod((p) => ({
        ...p,
        hazardZones: [...p.hazardZones, zone],
      }));
    },
    [updateActivePeriod]
  );

  const removeHazardZone = useCallback(
    (id: string) => {
      updateActivePeriod((p) => ({
        ...p,
        hazardZones: p.hazardZones.filter((z) => z.id !== id),
      }));
    },
    [updateActivePeriod]
  );

  const clearHazardZones = useCallback(() => {
    updateActivePeriod((p) => ({ ...p, hazardZones: [] }));
  }, [updateActivePeriod]);

  // ── Multi-day: advance to next operational period ─────────────────────────

  const advancePeriod = useCallback(
    (opts: {
      date: string;
      opPeriodStart?: string;
      opPeriodEnd?: string;
      weather?: OperationalPeriod["weather"];
    }) => {
      updateActiveIncident((incident) => {
        const currentPeriod = incident.operationalPeriods[incident.activePeriodIndex];
        const nextDay = currentPeriod.day + 1;
        const newPeriod: OperationalPeriod = {
          ...makeOperationalPeriod(nextDay, opts.date),
          weather: opts.weather ?? currentPeriod.weather,
          opPeriodStart: opts.opPeriodStart ?? "08:00",
          opPeriodEnd: opts.opPeriodEnd ?? "20:00",
          ignitionPoint: currentPeriod.ignitionPoint,
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
      a.download = `aims-incident-${incident.name.replace(/\s+/g, "-").toLowerCase()}.json`;
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
    activeIncidentId,
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
    fetchAndPlaceFacilities,
    // Evac
    commitEvacDecision,
    // Hazard zones
    addHazardZone,
    removeHazardZone,
    clearHazardZones,
    // Multi-day
    advancePeriod,
    setActivePeriodIndex,
    // Export
    exportIncident,
    importIncident,
  };
}
