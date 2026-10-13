/**
 * Fuel-grid vintage check (owner field test 2026-10-12, issue 6): the default fuel grids
 * (City LiDAR grid, Alberta Wildfire FBP grid) represent 2025. A historical scenario dated
 * before that runs on today's fuel: areas burned since may be mapped as grass or non-fuel
 * (the 2025 grid maps 31 % of 2011-15 burns and 43 % of 2016-18 burns as O-1; report
 * "Pre-fire fuel grids for historical replays", 2026-10-12). Date check only; the API sends
 * the same flag for the grid a run actually used (`fuel_vintage_warning`).
 */

/** Year the default City + Alberta fuel grids represent (docs/PROJECT_RECORD.md §4.3) */
export const DEFAULT_FUEL_GRID_YEAR = 2025;

/** Plain-language warning when the scenario starts before the grid's year, else null. */
export function fuelVintageWarning(gridYear: number | null, scenarioYear: number | null): string | null {
  if (gridYear === null || scenarioYear === null || scenarioYear >= gridYear) return null;
  return `Fuel grid represents ${gridYear}. Areas burned since ${scenarioYear} may be mapped as ` +
    "grass or non-fuel, not the fuel that burned. Historical replays overstate spread in old burn scars.";
}

/** Year of an instant on the America/Edmonton calendar. */
export function edmontonYear(ms: number): number {
  return Number(new Intl.DateTimeFormat("en-CA", { timeZone: "America/Edmonton", year: "numeric" }).format(new Date(ms)));
}
