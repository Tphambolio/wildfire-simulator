/**
 * Lazy tab components that survive a redeploy.
 *
 * Each deploy renames the code-split chunks (content hashes). A tab still open on the previous
 * build then asks for a chunk that no longer exists and React.lazy throws "Failed to fetch
 * dynamically imported module". `lazyChunk` retries the import once and, if it still fails,
 * renders a short notice with a Reload button instead of crashing the app (a run in progress is
 * not reloaded behind the user's back).
 */
import { lazy, type ComponentType } from "react";

export const STALE_BUILD_MESSAGE = "FireSim was updated since this page was opened. Reload to open this tab.";

export function StaleBuildNotice() {
  return (
    <div className="stale-build-notice" role="alert">
      <p>{STALE_BUILD_MESSAGE}</p>
      <button type="button" className="btn" onClick={() => window.location.reload()}>
        Reload
      </button>
    </div>
  );
}

export async function importWithRetry<T>(load: () => Promise<T>, retries = 1, waitMs = 400): Promise<T> {
  try {
    return await load();
  } catch (err) {
    if (retries <= 0) throw err;
    await new Promise((r) => setTimeout(r, waitMs));
    return importWithRetry(load, retries - 1, waitMs);
  }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function lazyChunk<P extends Record<string, any>>(load: () => Promise<{ default: ComponentType<P> }>) {
  return lazy(() =>
    importWithRetry(load).catch(() => ({ default: StaleBuildNotice as unknown as ComponentType<P> })),
  );
}
