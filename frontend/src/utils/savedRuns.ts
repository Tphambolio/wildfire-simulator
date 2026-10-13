/** Display helpers for the saved-runs list (SavedRunsPanel): Edmonton time, run summary. */

import type { SavedRun } from "../services/savedRuns";
import { formatClock, formatDate, zoneAbbrev } from "./time";

const KIND_LABEL: Record<string, string> = {
  single: "Single run",
  multiday: "Multi-day",
  perimeter_override: "RPAS restart",
};

export function runWhen(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (!Number.isFinite(d.getTime())) return "—";
  return `${formatDate(d)} ${formatClock(d)} ${zoneAbbrev(d)}`;
}

export function runSummary(r: SavedRun): string {
  const parts: string[] = [KIND_LABEL[r.kind ?? ""] ?? (r.kind || "Run")];
  if (r.ignition_lat !== null && r.ignition_lng !== null) {
    parts.push(`${r.ignition_lat.toFixed(3)}, ${r.ignition_lng.toFixed(3)}`);
  }
  if (r.area_ha !== null) parts.push(`${r.area_ha < 10 ? r.area_ha.toFixed(1) : Math.round(r.area_ha).toLocaleString("en-CA")} ha`);
  if (r.days && r.days > 1) parts.push(`${r.days} days`);
  else if (r.duration_hours !== null) parts.push(`${+r.duration_hours.toFixed(1)} h`);
  return parts.join(" · ");
}
