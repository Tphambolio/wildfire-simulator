/** Cloudflare KV cloud sync client for AIMS Console incidents. */

import type { IncidentSession } from "../types/incident";

const SYNC_URL = import.meta.env.VITE_SYNC_URL ?? "";

export const isCloudSyncAvailable = (): boolean => !!SYNC_URL;

/** Upload a new incident to cloud sync. Returns the generated share code. */
export async function createCloudIncident(incident: IncidentSession): Promise<string> {
  if (!SYNC_URL) return "";
  const res = await fetch(`${SYNC_URL}/api/incidents`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(incident),
  });
  if (!res.ok) throw new Error(`Cloud sync upload failed: ${res.status}`);
  const data = await res.json() as { shareCode: string };
  return data.shareCode;
}

/** Fetch an incident from cloud sync by share code. Returns null if not found. */
export async function getCloudIncident(shareCode: string): Promise<IncidentSession | null> {
  if (!SYNC_URL) return null;
  try {
    const res = await fetch(`${SYNC_URL}/api/incidents/${shareCode}`);
    if (res.status === 404) return null;
    if (!res.ok) throw new Error(`Cloud fetch failed: ${res.status}`);
    return (await res.json()) as IncidentSession;
  } catch {
    return null;
  }
}

/** Replace the full incident blob in cloud sync (refreshes 30-day TTL). */
export async function putCloudIncident(shareCode: string, incident: IncidentSession): Promise<void> {
  if (!SYNC_URL) return;
  try {
    await fetch(`${SYNC_URL}/api/incidents/${shareCode}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(incident),
    });
  } catch {
    // silent — sync is best-effort
  }
}
