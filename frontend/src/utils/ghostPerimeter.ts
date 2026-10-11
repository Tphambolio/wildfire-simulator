/**
 * SVG path for the previous operational period's final perimeter ("ghost" on Day 2+ in the
 * EOC console).
 *
 * The stored perimeter is the last API frame's `perimeter`, which is [lat, lng] pairs
 * (types/simulation.ts SimulationFrame). It used to be projected as [lng, lat]; MapLibre's
 * `project` then threw "Invalid LngLat latitude value" (Edmonton's longitude, -113, read as a
 * latitude) during render and blacked out the app (bug 2026-10-11). Points that are not finite
 * or out of range are skipped, and a projection error drops the ghost instead of throwing.
 */

export type Project = (lngLat: [number, number]) => { x: number; y: number };

/** True for a [lat, lng] pair MapLibre can project. */
function isLatLng(p: unknown): p is [number, number] {
  if (!Array.isArray(p) || p.length < 2) return false;
  const [lat, lng] = p as [unknown, unknown];
  return (
    typeof lat === "number" && typeof lng === "number" &&
    Number.isFinite(lat) && Number.isFinite(lng) &&
    Math.abs(lat) <= 90 && Math.abs(lng) <= 180
  );
}

/**
 * Closed SVG path ("M x y L … Z") for a [lat, lng] ring, or "" when there is nothing drawable
 * (fewer than three valid points, or the projection fails).
 */
export function ghostPerimeterPath(perimeter: unknown, project: Project): string {
  if (!Array.isArray(perimeter)) return "";
  const pts = perimeter.filter(isLatLng);
  if (pts.length < 3) return "";
  try {
    const parts: string[] = [];
    for (const [lat, lng] of pts) {
      const { x, y } = project([lng, lat]);
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
      parts.push(`${parts.length === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`);
    }
    return parts.length >= 3 ? `${parts.join(" ")} Z` : "";
  } catch (err) {
    console.warn("[FireSim] ghost perimeter not drawn:", err);
    return "";
  }
}
