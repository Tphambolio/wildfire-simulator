/**
 * Exercise scenario packs API (docs/exercise-mode-spec.md §11; PR2). The facilitator
 * passphrase (`X-FireSim-Exercise-Passphrase`, server secret FIRESIM_EXERCISE_PASSPHRASE) is
 * separate from the saved-runs one; it is kept in sessionStorage only (this tab) and dropped
 * on 401/403. Example packs and a pack by its 32-hex ID need no passphrase.
 */

import type { PackSummary, ScenarioPack } from "../types/exercise";

const API_BASE = import.meta.env.VITE_API_URL || "";
const BASE = `${API_BASE}/api/v1/exercise`;

export const EXERCISE_PASSPHRASE_HEADER = "X-FireSim-Exercise-Passphrase";
const STORAGE_KEY = "firesim.exercise.passphrase";

/** 401/403: the passphrase is missing or wrong, or packs are disabled on this server. */
export class ExerciseAuthError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export function getExercisePassphrase(): string | null {
  try {
    return sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function setExercisePassphrase(value: string | null): void {
  try {
    if (value) sessionStorage.setItem(STORAGE_KEY, value);
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // storage blocked: the passphrase is asked again
  }
}

async function detail(resp: Response): Promise<string> {
  const body = await resp.json().catch(() => null);
  const d = body?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d) && d.length) {
    return d.slice(0, 3).map((e: { loc?: unknown[]; msg?: string }) => `${(e.loc ?? []).slice(1).join(".")}: ${e.msg ?? ""}`).join("; ");
  }
  return resp.statusText || `HTTP ${resp.status}`;
}

async function gated(path: string, init: RequestInit = {}): Promise<Response> {
  const pass = getExercisePassphrase();
  const resp = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { ...(init.headers ?? {}), ...(pass ? { [EXERCISE_PASSPHRASE_HEADER]: pass } : {}) },
  });
  if (resp.status === 401 || resp.status === 403) {
    setExercisePassphrase(null);
    throw new ExerciseAuthError(await detail(resp), resp.status);
  }
  if (resp.status === 429) throw new Error(await detail(resp));
  return resp;
}

/** The repository's public example packs. */
export async function fetchExamplePacks(): Promise<ScenarioPack[]> {
  const resp = await fetch(`${BASE}/examples`);
  if (!resp.ok) throw new Error(await detail(resp));
  return ((await resp.json()) as { packs: ScenarioPack[] }).packs;
}

export async function listPacks(): Promise<PackSummary[]> {
  const resp = await gated("/packs");
  if (!resp.ok) throw new Error(await detail(resp));
  return ((await resp.json()) as { packs: PackSummary[] }).packs;
}

/** Store a pack on the server; it gets a new 32-hex ID. */
export async function savePack(pack: ScenarioPack): Promise<{ id: string; pack: ScenarioPack }> {
  const resp = await gated("/packs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(pack),
  });
  if (!resp.ok) throw new Error(await detail(resp));
  return resp.json();
}

/** A stored pack by its ID (no passphrase). */
export async function fetchPack(id: string): Promise<ScenarioPack> {
  const resp = await fetch(`${BASE}/packs/${encodeURIComponent(id)}`);
  if (!resp.ok) throw new Error(resp.status === 404 ? "No pack with that ID on this server" : await detail(resp));
  return resp.json();
}

export async function deletePack(id: string): Promise<void> {
  const resp = await gated(`/packs/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!resp.ok) throw new Error(await detail(resp));
}
