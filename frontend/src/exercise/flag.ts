/**
 * Exercise-mode feature flag (docs/exercise-mode-spec.md §10, PR0). Off by default: nothing
 * of exercise mode is rendered unless the build sets `VITE_FIRESIM_EXERCISE=1` or the page is
 * opened with `?exercise=1`. Pure, with injectable inputs so the gating is unit-testable.
 */

export const EXERCISE_ENV_VAR = "VITE_FIRESIM_EXERCISE";
export const EXERCISE_QUERY_PARAM = "exercise";

/** True when the env value or the query string turns exercise mode on ("1" only). */
export function exerciseFlagFrom(envValue: string | undefined | null, search: string | undefined | null): boolean {
  if (envValue === "1") return true;
  if (!search) return false;
  try {
    return new URLSearchParams(search).get(EXERCISE_QUERY_PARAM) === "1";
  } catch {
    return false;
  }
}

/** The flag for this page load (build env, then the page URL). */
export function isExerciseFlagOn(): boolean {
  const env = (import.meta.env as Record<string, string | undefined>)[EXERCISE_ENV_VAR];
  const search = typeof window !== "undefined" ? window.location?.search : "";
  return exerciseFlagFrom(env, search);
}
