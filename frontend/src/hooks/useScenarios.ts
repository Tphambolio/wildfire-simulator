/** Persist named simulation scenarios to localStorage. */

import { useState, useCallback } from "react";
import type { ScenarioConfig } from "../types/simulation";
import { loadScenarios, writeStored } from "../utils/storage";

const STORAGE_KEY = "firesim-v3-scenarios";
const MAX_SCENARIOS = 10;

function loadFromStorage(): ScenarioConfig[] {
  // Defensive: older or corrupted saves are normalised or dropped (utils/storage.ts)
  return loadScenarios(STORAGE_KEY);
}

function saveToStorage(scenarios: ScenarioConfig[]): void {
  writeStored(STORAGE_KEY, scenarios); // quota exceeded: kept for this session only
}

export function useScenarios() {
  const [scenarios, setScenarios] = useState<ScenarioConfig[]>(loadFromStorage);

  const saveScenario = useCallback((config: Omit<ScenarioConfig, "id" | "createdAt">) => {
    const next: ScenarioConfig = {
      ...config,
      id: crypto.randomUUID(),
      createdAt: new Date().toISOString(),
    };
    setScenarios((prev) => {
      const updated = [next, ...prev].slice(0, MAX_SCENARIOS);
      saveToStorage(updated);
      return updated;
    });
    return next;
  }, []);

  const deleteScenario = useCallback((id: string) => {
    setScenarios((prev) => {
      const updated = prev.filter((s) => s.id !== id);
      saveToStorage(updated);
      return updated;
    });
  }, []);

  const updateLastRunStats = useCallback((id: string, stats: ScenarioConfig["lastRunStats"]) => {
    setScenarios((prev) => {
      const updated = prev.map((s) => s.id === id ? { ...s, lastRunStats: stats } : s);
      saveToStorage(updated);
      return updated;
    });
  }, []);

  const exportScenario = useCallback((scenario: ScenarioConfig) => {
    const blob = new Blob([JSON.stringify(scenario, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `firesim-scenario-${scenario.name.replace(/\s+/g, "-").toLowerCase()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }, []);

  const importScenario = useCallback((file: File): Promise<ScenarioConfig> => {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = (e) => {
        try {
          const parsed = JSON.parse(e.target?.result as string) as ScenarioConfig;
          if (!parsed.name || !parsed.weather) {
            reject(new Error("Invalid scenario file"));
            return;
          }
          const next: ScenarioConfig = {
            ...parsed,
            id: crypto.randomUUID(),
            createdAt: new Date().toISOString(),
          };
          setScenarios((prev) => {
            const updated = [next, ...prev].slice(0, MAX_SCENARIOS);
            saveToStorage(updated);
            return updated;
          });
          resolve(next);
        } catch {
          reject(new Error("Could not parse scenario file"));
        }
      };
      reader.onerror = () => reject(new Error("Could not read file"));
      reader.readAsText(file);
    });
  }, []);

  return {
    scenarios,
    saveScenario,
    deleteScenario,
    updateLastRunStats,
    exportScenario,
    importScenario,
  };
}
