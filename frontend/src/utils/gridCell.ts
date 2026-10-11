import type { GridInfo } from "../types/simulation";

/** Cell size (m) the fire heatmap's weights were tuned on: the engine's 50 m grid. */
export const REFERENCE_CELL_M = 50;

/**
 * Heatmap weight of one burned cell relative to a 50 m cell: its area ratio. Runs near
 * buildings can use the fuel grid's native 20 m cells (frame `grid`, mechanics decision M5);
 * 20 m cells are 6.25 per 50 m cell, so without this the same fire would look 6.25x denser.
 */
export function cellAreaWeight(grid?: GridInfo | null): number {
  const c = grid?.cell_m;
  if (c == null || !Number.isFinite(c) || c <= 0) return 1;
  return (c / REFERENCE_CELL_M) ** 2;
}
