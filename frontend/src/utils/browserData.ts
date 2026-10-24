/**
 * Everything FireSim keeps in this browser, in one place (E9, owner report 2026-10-24: an old
 * incident name kept coming back after hard reloads and only went away after clearing all site
 * data; incidents and the Setup panel live in localStorage, which Ctrl+Shift+R never touches,
 * and the app offered no reset).
 *
 * The list below is the canonical set of FireSim storage keys. Add a new key or prefix HERE
 * when a module starts storing something, so the inventory and the reset control stay complete.
 * `inventory()` says what is present (with sizes and, for incidents, names and statuses);
 * `clearAll()` removes exactly these keys and nothing else, and tolerates storage being blocked.
 * The server's run archive (Saved runs) is not touched: it is not in this browser.
 */

import type { IncidentSession } from "../types/incident";
import { normaliseIncident } from "./storage";

/** What the reset button reads; tooltips name it so users find it. */
export const RESET_BUTTON_LABEL = "Reset this browser's FireSim data";

export type StoreName = "local" | "session";

export interface BrowserDataEntry {
  id: string;
  /** Exact key, or every key starting with `prefix` */
  key?: string;
  prefix?: string;
  store: StoreName;
  /** Human label, singular ("Saved scenario") or a phrase that reads well on its own */
  label: string;
}

/** The canonical list. Every module that touches storage has its key(s) here. */
export const BROWSER_DATA_ENTRIES: readonly BrowserDataEntry[] = [
  // hooks/useIncident.ts
  { id: "incidents", key: "firesim-v3-incidents", store: "local", label: "Incidents" },
  // utils/storage.ts keeps an unreadable value aside under `<key>.unreadable`
  { id: "incidents-unreadable", key: "firesim-v3-incidents.unreadable", store: "local", label: "Unreadable incident data kept aside" },
  // hooks/useScenarios.ts
  { id: "scenarios", key: "firesim-v3-scenarios", store: "local", label: "Saved scenarios" },
  { id: "scenarios-unreadable", key: "firesim-v3-scenarios.unreadable", store: "local", label: "Unreadable scenario data kept aside" },
  // utils/setupStore.ts: Setup restored after a reload, per incident (or "scratch")
  { id: "setup", prefix: "firesim.setup.", store: "local", label: "Restored Setup" },
  // App.tsx: evacuation statuses set by Planning with no incident open
  { id: "evac-tiers", key: "firesim-v3-evac-tiers", store: "local", label: "Evacuation statuses set without an incident" },
  // components/LegendDock.tsx
  { id: "legends", key: "firesim-v3-legends", store: "local", label: "Legend settings" },
  // hooks/useLayout.ts
  { id: "layout", key: "firesim-v3-layout", store: "local", label: "Map layout" },
  // components/WeatherPanel.tsx
  { id: "grass-cure", key: "firesim.lastGrassCure", store: "local", label: "Last grass curing value" },
  // utils/liveRun.ts (this tab only)
  { id: "live-run", key: "firesim-v3-live-run", store: "session", label: "Run on screen (this tab)" },
  // services/savedRuns.ts (this tab only)
  { id: "saved-runs-passphrase", key: "firesim.savedRuns.passphrase", store: "session", label: "Saved-runs passphrase (this tab)" },
];

export interface InventoryItem {
  id: string;
  label: string;
  store: StoreName;
  /** The keys present for this entry (one for an exact key; any number for a prefix) */
  keys: string[];
  /** UTF-16 code units of the stored values (a close proxy for bytes) */
  bytes: number;
}

export interface IncidentSummary {
  id: string;
  name: string;
  status: "active" | "closed";
}

export interface BrowserDataInventory {
  /** Entries that have something stored, in list order */
  items: InventoryItem[];
  /** Stored incidents (normalised as the app reads them), for export before clearing */
  incidents: IncidentSession[];
  /** Setup restored for incidents, and whether a no-incident ("scratch") Setup is stored */
  setup: { incidentIds: string[]; scratch: boolean };
  totalBytes: number;
  /** localStorage or sessionStorage threw when read (blocked, private window) */
  blocked: boolean;
  empty: boolean;
}

function storageFor(name: StoreName): Storage | null {
  try {
    const s = name === "local" ? window.localStorage : window.sessionStorage;
    // Some browsers expose the object but throw on access
    void s.length;
    return s;
  } catch {
    return null;
  }
}

/** Every key in `storage` that starts with `prefix` (iteration tolerates a throwing storage). */
function keysWithPrefix(storage: Storage, prefix: string): string[] {
  const out: string[] = [];
  try {
    for (let i = 0; i < storage.length; i += 1) {
      const k = storage.key(i);
      if (k !== null && k.startsWith(prefix)) out.push(k);
    }
  } catch {
    // partial list: nothing better to do
  }
  return out;
}

function presentKeys(entry: BrowserDataEntry, storage: Storage): string[] {
  if (entry.prefix) return keysWithPrefix(storage, entry.prefix);
  if (!entry.key) return [];
  try {
    return storage.getItem(entry.key) === null ? [] : [entry.key];
  } catch {
    return [];
  }
}

function summariseIncidents(storage: Storage | null): IncidentSession[] {
  if (!storage) return [];
  let raw: string | null = null;
  try {
    raw = storage.getItem("firesim-v3-incidents");
  } catch {
    return [];
  }
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.map(normaliseIncident).filter((i): i is IncidentSession => i !== null);
  } catch {
    return [];
  }
}

/** What FireSim has stored in this browser right now. Never throws. */
export function inventory(): BrowserDataInventory {
  const stores: Record<StoreName, Storage | null> = { local: storageFor("local"), session: storageFor("session") };
  const blocked = stores.local === null || stores.session === null;
  const items: InventoryItem[] = [];
  let totalBytes = 0;
  for (const entry of BROWSER_DATA_ENTRIES) {
    const storage = stores[entry.store];
    if (!storage) continue;
    const keys = presentKeys(entry, storage);
    if (keys.length === 0) continue;
    let bytes = 0;
    for (const k of keys) {
      try {
        bytes += k.length + (storage.getItem(k)?.length ?? 0);
      } catch {
        // counted as 0
      }
    }
    totalBytes += bytes;
    items.push({ id: entry.id, label: entry.label, store: entry.store, keys, bytes });
  }
  const setupKeys = items.find((i) => i.id === "setup")?.keys ?? [];
  const setupIds = setupKeys.map((k) => k.slice("firesim.setup.".length));
  return {
    items,
    incidents: summariseIncidents(stores.local),
    setup: { incidentIds: setupIds.filter((id) => id !== "scratch"), scratch: setupIds.includes("scratch") },
    totalBytes,
    blocked,
    empty: items.length === 0,
  };
}

/** Short incident lines for the dialog: name and status. */
export function incidentSummaries(inv: BrowserDataInventory): IncidentSummary[] {
  return inv.incidents.map((i) => ({ id: i.id, name: i.name, status: i.status }));
}

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

function kb(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(bytes < 10_240 ? 1 : 0)} kB`;
}

/**
 * The inventory as plain lines, e.g. "2 incidents: Millcreek (active), TEST (closed)",
 * "Restored Setup for 2 incidents and for no incident", "Evacuation statuses set without an
 * incident", "Map layout". Empty inventory → [].
 */
export function describeInventory(inv: BrowserDataInventory): string[] {
  const lines: string[] = [];
  for (const item of inv.items) {
    switch (item.id) {
      case "incidents": {
        const n = inv.incidents.length;
        if (n === 0) {
          lines.push(`Incident list (${kb(item.bytes)}, no readable incidents)`);
          break;
        }
        const names = inv.incidents.map((i) => `${i.name} (${i.status})`).join(", ");
        lines.push(`${plural(n, "incident")}: ${names}`);
        break;
      }
      case "setup": {
        const n = inv.setup.incidentIds.length;
        const parts: string[] = [];
        if (n > 0) parts.push(`for ${plural(n, "incident")}`);
        if (inv.setup.scratch) parts.push("for no incident");
        lines.push(`Restored Setup ${parts.join(" and ")}`.trim());
        break;
      }
      case "scenarios": {
        let n = 0;
        try {
          const parsed: unknown = JSON.parse(localStorage.getItem(item.keys[0]) ?? "[]");
          n = Array.isArray(parsed) ? parsed.length : 0;
        } catch {
          n = 0;
        }
        lines.push(n > 0 ? plural(n, "saved scenario") : item.label);
        break;
      }
      default:
        lines.push(item.label);
    }
  }
  return lines;
}

export interface ClearResult {
  /** Keys removed, as "<store>:<key>" */
  removed: string[];
  /** Keys that could not be removed (storage threw) */
  failed: string[];
  /** A store could not be opened at all */
  blocked: boolean;
}

/**
 * Remove every FireSim key listed above from localStorage and sessionStorage, and nothing
 * else (other sites' or tools' keys on the same origin stay). Never throws: a blocked store or
 * a refused removal is reported in the result.
 */
export function clearAll(): ClearResult {
  const result: ClearResult = { removed: [], failed: [], blocked: false };
  for (const name of ["local", "session"] as const) {
    const storage = storageFor(name);
    if (!storage) {
      result.blocked = true;
      continue;
    }
    for (const entry of BROWSER_DATA_ENTRIES) {
      if (entry.store !== name) continue;
      for (const key of presentKeys(entry, storage)) {
        try {
          storage.removeItem(key);
          result.removed.push(`${name}:${key}`);
        } catch {
          result.failed.push(`${name}:${key}`);
        }
      }
    }
  }
  return result;
}
