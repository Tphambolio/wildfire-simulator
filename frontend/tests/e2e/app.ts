/** Shared app flows for the e2e tests. */
import { expect, type Page } from "@playwright/test";
import { fixture } from "./mockApi";

declare global {
  interface Window {
    __draws: number;
  }
}

/**
 * Open the app and wait for the map to load. Grass curing has no default outside the spring
 * window (decision M1), so by default the tests enter 60 % (the old fixed default, which the
 * fixture was recorded with) to stay independent of today's date; `curing: null` leaves the
 * field as the app opens it (tests/e2e/curing.spec.ts).
 */
export async function openApp(page: Page, opts: { curing?: number | null; pinLayout?: boolean } = {}): Promise<void> {
  // Specs other than layout-focus.spec.ts test their own features with both side columns
  // open: pin the layout preference (unless the test stored one) so the one-time Setup
  // auto-collapse after a run does not move the Run bar. `pinLayout: false` = first-run user.
  if (opts.pinLayout !== false) {
    await page.addInitScript(() => {
      try {
        if (!localStorage.getItem("firesim-v3-layout")) {
          localStorage.setItem("firesim-v3-layout", JSON.stringify({ setup: "open", situation: "open", hintSeen: true }));
        }
      } catch {
        // storage blocked
      }
    });
  }
  await page.goto("/");
  await expect(page.locator("canvas.maplibregl-canvas").first()).toBeVisible();
  await page.waitForLoadState("networkidle");
  const curing = opts.curing === undefined ? 60 : opts.curing;
  if (curing !== null) await enterGrassCuring(page, curing);
}

/** Enter grass curing in Setup → Fuel & landscape, leaving the section as it was (closed). */
export async function enterGrassCuring(page: Page, value: number): Promise<void> {
  const toggle = page.locator(".setup-section-toggle", { hasText: "Fuel & landscape" });
  await toggle.click();
  await page.getByLabel(/^Grass curing/).fill(String(value));
  await toggle.click();
}

/** Click the centre of the map to set the ignition point; resolves when Run is enabled. */
export async function setIgnitionAtMapCentre(page: Page): Promise<void> {
  const canvas = page.locator("canvas.maplibregl-canvas").first();
  const box = (await canvas.boundingBox())!;
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  await expect(runButton(page)).toBeEnabled();
}

export function runButton(page: Page) {
  return page.locator("button.btn-primary", { hasText: "Run Simulation" });
}

/** The map's current view as MapView publishes it on moveend ("[[w, s], [e, n]]"). */
export async function mapBounds(page: Page): Promise<string | null> {
  return page.locator(".map-view-canvas").first().getAttribute("data-bounds");
}

/**
 * Run, and wait for the mocked frames to replay to "completed". `waitForFit`: also wait for the
 * map's one-time ease to the fire after the first completed run (MapView fitFrame, 600 ms, far
 * longer on a slow software-GL CI runner) to end, so a following map click lands on the fitted
 * view, not on wherever the camera is mid-flight (G8: evac.spec.ts clicked the map centre during
 * the ease and sometimes hit a critical-asset symbol or another neighbourhood).
 */
export async function runToCompletion(page: Page, opts: { waitForFit?: boolean } = {}): Promise<void> {
  const before = opts.waitForFit ? await mapBounds(page) : null;
  await runButton(page).click();
  await expect(page.locator(".status-badge.status-completed")).toHaveText("completed", { timeout: 30_000 });
  if (opts.waitForFit) {
    // data-bounds is rewritten on moveend: a new value means the fit has come to rest
    await expect.poll(() => mapBounds(page), { timeout: 15_000 }).not.toBe(before);
  }
}

export const finalFrame = fixture.frames[fixture.frames.length - 1];

/**
 * Wait until the WebGL draw-call counter stops changing for `quietMs` (map tiles, fades and
 * fly-to animations finished), up to `timeoutMs`. Returns false if it never settles.
 */
export async function waitForMapQuiet(page: Page, quietMs = 1000, timeoutMs = 15_000): Promise<boolean> {
  const t0 = Date.now();
  let last = await page.evaluate(() => window.__draws);
  let quietSince = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    await page.waitForTimeout(200);
    const now = await page.evaluate(() => window.__draws);
    if (now !== last) {
      last = now;
      quietSince = Date.now();
    } else if (Date.now() - quietSince >= quietMs) {
      return true;
    }
  }
  return false;
}

/** WebGL draw calls issued during `ms` of idle. */
export async function drawCallsWhileIdle(page: Page, ms: number): Promise<number> {
  const d0 = await page.evaluate(() => window.__draws);
  await page.waitForTimeout(ms);
  return (await page.evaluate(() => window.__draws)) - d0;
}
