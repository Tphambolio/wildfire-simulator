/**
 * Date-aware grass curing default (decision M1, 2026-10-10), mirroring the engine's
 * `firesim/fbp/curing.py` and the API rule: 95 % cured in the pre-green-up window (day of
 * year 60-149, about 1 March to 29 May; FireSim's choice), no default outside it.
 * Sources and reasoning: docs/model-card.md, docs/PROJECT_RECORD.md (M1).
 */

/** Day-of-year window [start, end) of the spring default. */
export const SPRING_WINDOW_DOY: readonly [number, number] = [60, 150];

/** Default degree of curing (%) inside the window. */
export const SPRING_CURING_DEFAULT = 95;

/** The window as people read it. */
export const SPRING_WINDOW_LABEL = "1 Mar–29 May";

export function inSpringCuringWindow(dayOfYear: number | null): boolean {
  return dayOfYear !== null && dayOfYear >= SPRING_WINDOW_DOY[0] && dayOfYear < SPRING_WINDOW_DOY[1];
}

/** 95 in the pre-green-up window, else null (the user must enter curing). */
export function defaultGrassCure(dayOfYear: number | null): number | null {
  return inSpringCuringWindow(dayOfYear) ? SPRING_CURING_DEFAULT : null;
}

/** FBP curing factor, Wotton et al. 2009 (GLC-X-10) eqs 35a/35b. */
export function curingFactor(c: number): number {
  return c < 58.8 ? 0.005 * (Math.exp(0.061 * c) - 1) : 0.176 + 0.02 * (c - 58.8);
}

/**
 * The curing a run uses: the user's entry if any, else the date's default (null = none).
 */
export function effectiveGrassCure(entered: number | null, dayOfYear: number | null): number | null {
  return entered ?? defaultGrassCure(dayOfYear);
}
