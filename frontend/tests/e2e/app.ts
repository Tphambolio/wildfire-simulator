/** Shared app flows for the e2e tests. */
import { expect, type Page } from "@playwright/test";
import { fixture } from "./mockApi";

declare global {
  interface Window {
    __draws: number;
  }
}

/** Open the app and wait for the map to load. */
export async function openApp(page: Page): Promise<void> {
  await page.goto("/");
  await expect(page.locator("canvas.maplibregl-canvas").first()).toBeVisible();
  await page.waitForLoadState("networkidle");
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

/** Run, and wait for the mocked frames to replay to "completed". */
export async function runToCompletion(page: Page): Promise<void> {
  await runButton(page).click();
  await expect(page.locator(".status-badge.status-completed")).toHaveText("completed", { timeout: 30_000 });
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
