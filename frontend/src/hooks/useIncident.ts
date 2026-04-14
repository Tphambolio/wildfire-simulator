/** Persist incident sessions (multi-day operational periods) to localStorage. */

import { useState, useCallback, useEffect, useRef } from "react";
import { fetchNearbyFacilities } from "../services/overpass";
import { createCloudIncident, getCloudIncident, putCloudIncident, isCloudSyncAvailable } from "../services/cloudSync";
import type {
  IncidentSession,
  OperationalPeriod,
  IncidentAnnotation,
  EvacDecisionRecord,
  HazardZone,
  IncidentResource,
  ICSFormId,
  FormRecord,
  FormStatus,
  ResourceRequest,
} from "../types/incident";
import { makeIncident, makeOperationalPeriod } from "../types/incident";

const STORAGE_KEY = "aims-console-incidents";
const MAX_INCIDENTS = 20;

/** Migrate resources saved before icsSection field was added. */
function normalizeResources(resources: IncidentResource[]): IncidentResource[] {
  return resources.map((r) => (r.icsSection ? r : { ...r, icsSection: "other" as const }));
}

function loadFromStorage(): IncidentSession[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const sessions = JSON.parse(raw) as IncidentSession[];
    return sessions.map((s) => ({ ...s, resources: normalizeResources(s.resources ?? []) }));
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
  // Always start without an active incident — user explicitly opens or creates one each session
  const [activeIncidentId, setActiveIncidentId] = useState<string | null>(null);

  const activeIncident = incidents.find((i) => i.id === activeIncidentId) ?? null;
  const activePeriod = activeIncident
    ? activeIncident.operationalPeriods[activeIncident.activePeriodIndex] ?? null
    : null;

  // ── Ref kept in sync with incidents state (avoids stale closures in intervals) ─
  const incidentsRef = useRef<IncidentSession[]>(incidents);
  useEffect(() => {
    incidentsRef.current = incidents;
  }, [incidents]);

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

      // Auto-cloud: fire-and-forget — store share code when ready
      if (isCloudSyncAvailable()) {
        createCloudIncident(incident).then((code) => {
          if (!code) return;
          setIncidents((prev) => {
            const next = prev.map((i) =>
              i.id === incident.id
                ? { ...i, shareCode: code, syncedAt: new Date().toISOString() }
                : i
            );
            saveToStorage(next);
            return next;
          });
        }).catch(() => {/* offline — local-only until next sync */});
      }

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

  // ── Resources ─────────────────────────────────────────────────────────────

  const addResource = useCallback(
    (resource: IncidentResource) => {
      updateActiveIncident((i) => ({
        ...i,
        resources: [...(i.resources ?? []), resource],
      }));
    },
    [updateActiveIncident]
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

  // ── IAP form records ─────────────────────────────────────────────────────

  const updateFormRecord = useCallback(
    (periodIndex: number, formId: ICSFormId, patch: Partial<FormRecord>) => {
      updateActiveIncident((incident) => {
        const periods = incident.operationalPeriods.map((p, idx) => {
          if (idx !== periodIndex) return p;
          const existing: FormRecord = p.formRecords?.[formId] ?? { status: "empty" as FormStatus };
          return {
            ...p,
            formRecords: {
              ...p.formRecords,
              [formId]: { ...existing, ...patch },
            },
          };
        });
        return { ...incident, operationalPeriods: periods };
      });
    },
    [updateActiveIncident]
  );

  const addResourceRequest = useCallback(
    (req: Omit<ResourceRequest, "id" | "requestNumber" | "createdAt">) => {
      updateActiveIncident((incident) => {
        const existing = incident.resourceRequests ?? [];
        const requestNumber = "RR-" + String(existing.length + 1).padStart(3, "0");
        const newReq: ResourceRequest = {
          ...req,
          id: crypto.randomUUID(),
          requestNumber,
          createdAt: new Date().toISOString(),
        };
        return { ...incident, resourceRequests: [...existing, newReq] };
      });
    },
    [updateActiveIncident]
  );

  const updateResourceRequest = useCallback(
    (id: string, patch: Partial<ResourceRequest>) => {
      updateActiveIncident((incident) => ({
        ...incident,
        resourceRequests: (incident.resourceRequests ?? []).map((r) =>
          r.id === id ? { ...r, ...patch } : r
        ),
      }));
    },
    [updateActiveIncident]
  );

  const removeResourceRequest = useCallback(
    (id: string) => {
      updateActiveIncident((incident) => ({
        ...incident,
        resourceRequests: (incident.resourceRequests ?? []).filter((r) => r.id !== id),
      }));
    },
    [updateActiveIncident]
  );

  const approveIAP = useCallback(
    (periodIndex: number, approverName: string, approverPosition: string) => {
      updateActiveIncident((incident) => {
        const periods = incident.operationalPeriods.map((p, idx) =>
          idx !== periodIndex ? p : {
            ...p,
            iapApprovedAt: new Date().toISOString(),
            iapApprovedBy: approverName,
            iapApprovedByPosition: approverPosition,
          }
        );
        return { ...incident, operationalPeriods: periods };
      });
    },
    [updateActiveIncident]
  );

  // ── Cloud sync ────────────────────────────────────────────────────────────

  // Reconnect catch-up: when browser goes online, push any incidents missing a shareCode
  useEffect(() => {
    if (!isCloudSyncAvailable()) return;
    const handleOnline = () => {
      const unsynced = incidentsRef.current.filter((i) => !i.shareCode);
      for (const incident of unsynced) {
        createCloudIncident(incident).then((code) => {
          if (!code) return;
          setIncidents((prev) => {
            const next = prev.map((i) =>
              i.id === incident.id
                ? { ...i, shareCode: code, syncedAt: new Date().toISOString() }
                : i
            );
            saveToStorage(next);
            return next;
          });
        }).catch(() => {});
      }
    };
    window.addEventListener("online", handleOnline);
    return () => window.removeEventListener("online", handleOnline);
  }, []);

  // Outbound sync: debounced 2s — push to cloud when active incident has shareCode
  useEffect(() => {
    if (!activeIncidentId) return;
    const timer = setTimeout(() => {
      const current = incidentsRef.current.find((i) => i.id === activeIncidentId);
      if (current?.shareCode) {
        putCloudIncident(current.shareCode, current).catch(() => {/* silent */});
      }
    }, 2000);
    return () => clearTimeout(timer);
  }, [incidents, activeIncidentId]);

  // Inbound poll: every 10s — if cloud is newer than local, replace local
  useEffect(() => {
    if (!activeIncidentId) return;
    const interval = setInterval(async () => {
      const current = incidentsRef.current.find((i) => i.id === activeIncidentId);
      if (!current?.shareCode) return;
      try {
        const remote = await getCloudIncident(current.shareCode);
        if (!remote) return;
        if (remote.updatedAt > current.updatedAt) {
          setIncidents((prev) => {
            const next = prev.map((i) =>
              i.id === activeIncidentId ? { ...remote, id: i.id } : i
            );
            saveToStorage(next);
            return next;
          });
        }
      } catch {
        // silent
      }
    }, 10000);
    return () => clearInterval(interval);
  }, [activeIncidentId]);

  /** Upload incident to cloud sync and store the returned share code. */
  const shareIncident = useCallback(async (): Promise<string> => {
    const current = incidentsRef.current.find((i) => i.id === activeIncidentId);
    if (!current) return "";
    if (current.shareCode) return current.shareCode;
    const code = await createCloudIncident(current);
    if (!code) return "";
    setIncidents((prev) => {
      const next = prev.map((i) =>
        i.id === activeIncidentId
          ? { ...i, shareCode: code, syncedAt: new Date().toISOString(), updatedAt: new Date().toISOString() }
          : i
      );
      saveToStorage(next);
      return next;
    });
    return code;
  }, [activeIncidentId]);

  /** Join an existing cloud incident by share code, create local copy and activate it. */
  const joinIncident = useCallback(async (shareCode: string): Promise<IncidentSession | null> => {
    const remote = await getCloudIncident(shareCode);
    if (!remote) return null;
    const localCopy: IncidentSession = {
      ...remote,
      id: crypto.randomUUID(),
      shareCode,
    };
    setIncidents((prev) => {
      const next = [localCopy, ...prev].slice(0, MAX_INCIDENTS);
      saveToStorage(next);
      return next;
    });
    setActiveIncidentId(localCopy.id);
    return localCopy;
  }, []);

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
            resources: normalizeResources(parsed.resources ?? []),
          };
          setIncidents((prev) => {
            const updated = [next, ...prev].slice(0, MAX_INCIDENTS);
            saveToStorage(updated);
            return updated;
          });
          setActiveIncidentId(next.id);
          // Auto-cloud imported incidents too (strip any old shareCode — get a fresh one)
          if (isCloudSyncAvailable()) {
            const toSync = { ...next, shareCode: undefined };
            createCloudIncident(toSync).then((code) => {
              if (!code) return;
              setIncidents((prev) => {
                const updated = prev.map((i) =>
                  i.id === next.id
                    ? { ...i, shareCode: code, syncedAt: new Date().toISOString() }
                    : i
                );
                saveToStorage(updated);
                return updated;
              });
            }).catch(() => {});
          }
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
    // Resources
    addResource,
    // Evac
    commitEvacDecision,
    // Hazard zones
    addHazardZone,
    removeHazardZone,
    clearHazardZones,
    // Multi-day
    advancePeriod,
    setActivePeriodIndex,
    // IAP form records
    updateFormRecord,
    addResourceRequest,
    updateResourceRequest,
    removeResourceRequest,
    approveIAP,
    // Export
    exportIncident,
    importIncident,
    // Cloud sync
    shareIncident,
    joinIncident,
  };
}
