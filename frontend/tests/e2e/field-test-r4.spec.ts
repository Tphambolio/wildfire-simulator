/**
 * Live Chrome field test of 2026-10-18 (multi-day labels #86 rerun), fixes:
 * - Issue 1: the ICS 209-WF froze the tab after a 72 h run of 145 frames. It opened a pop-up
 *   that called window.print() on load (a print dialog blocks the app's tab), and every ICS
 *   form waited for a map repaint that never comes while the page draws no frames (screen
 *   locked). The 209 is now previewed in the console, nothing prints by itself, and the map
 *   snapshot waits at most 1.5 s.
 * - Issue 2: a reload during a multi-day run dropped it; the run is re-attached by its ID.
 * - Issue 3: a run past midnight: the timeline and Situation clocks carry the day ("D2 03:32").
 * - Issues 6 and 8: the fuel line names the grid's burned fuels; Clear results clears the
 *   header chips.
 */
import { expect, test, type Page } from "@playwright/test";
import { fixture, mockApi, MULTIDAY_ID, denseMultiDayFrames } from "./mockApi";
import { openApp, runToCompletion, setIgnitionAtMapCentre } from "./app";

declare global {
  interface Window {
    __prints: number;
    __noFrames: boolean;
  }
}

function pageErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  return errors;
}

/** Count window.print() calls (in the app and any window it opens) instead of printing. */
async function countPrints(page: Page): Promise<void> {
  await page.context().addInitScript(() => {
    window.__prints = 0;
    window.print = () => {
      window.__prints++;
    };
  });
}

async function startMultiDay(page: Page): Promise<void> {
  await page.getByRole("button", { name: /Run options/ }).click();
  await page.getByRole("button", { name: "Multi-day", exact: true }).click();
  await page.locator("button.btn-primary", { hasText: "Run 72h Scenario" }).click();
}

async function openConsole(page: Page): Promise<void> {
  await page.getByRole("button", { name: /EOC Console/ }).click();
  await page.getByRole("textbox", { name: "Incident name", exact: true }).fill("Field test r4");
  await page.getByRole("button", { name: "Open EOC Console" }).click();
}

const formBody = (page: Page) => page.frameLocator("iframe.eoc-form-iframe").locator("body");

test("ICS 209-WF after a 145-frame 72 h run: previewed in the console at once, no pop-up, no print", async ({ page }) => {
  const errors = pageErrors(page);
  await countPrints(page);
  await mockApi(page, { multidayDense: true, frameDelayMs: 5 });
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  await startMultiDay(page);
  await expect(page.locator(".status-badge.status-completed")).toHaveText("completed", { timeout: 60_000 });
  expect(denseMultiDayFrames(3)).toHaveLength(145);
  await expect(page.locator(".ts-frame-count")).toHaveText("145/145");
  await openConsole(page);

  let popups = 0;
  page.on("popup", () => popups++);
  const t0 = Date.now();
  await page.locator(".eoc-console-header").getByRole("button", { name: "ICS 209-WF" }).click();
  await expect(formBody(page).locator("h1")).toHaveText("INCIDENT STATUS SUMMARY (ICS 209)", { timeout: 10_000 });
  const openMs = Date.now() - t0;
  expect(openMs).toBeLessThan(5_000);
  await expect(page.locator(".eoc-form-viewer-name")).toHaveText("ICS 209-WF Status Summary");
  const text = await formBody(page).innerText();
  expect(text).toContain(MULTIDAY_ID);
  expect(text).toContain("ICS Canada Form 209-WF");
  // In the preview the form's own Print button is hidden (the viewer has Print)
  await expect(formBody(page).locator("#print-bar")).toBeHidden();
  // The page stays responsive
  const t1 = Date.now();
  await page.evaluate(() => 1);
  expect(Date.now() - t1).toBeLessThan(1_000);

  // ICS Forms → the 209 entry gives the same preview
  await page.locator(".eoc-form-btn", { hasText: "ICS 209-WF Status Summary" }).click();
  await expect(formBody(page).locator("h1")).toHaveText("INCIDENT STATUS SUMMARY (ICS 209)");
  await page.waitForTimeout(1_000);
  expect(popups).toBe(0);
  expect(await page.evaluate(() => window.__prints)).toBe(0);

  // New Window: the form in its own window, with a Print button and no automatic print
  const [win] = await Promise.all([page.waitForEvent("popup"), page.getByRole("button", { name: "↗ New Window" }).click()]);
  await expect(win.locator("h1")).toHaveText("INCIDENT STATUS SUMMARY (ICS 209)");
  await expect(win.locator("#print-bar button")).toBeVisible();
  await win.waitForTimeout(800);
  expect(await win.evaluate(() => window.__prints)).toBe(0);
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/ics209_preview_145frames.png` });
  expect(errors).toEqual([]);
});

test("ICS forms open while the page draws no frames (locked screen) and still embed the map", async ({ page }) => {
  const errors = pageErrors(page);
  // requestAnimationFrame callbacks stop when __noFrames is set, as in an occluded window
  await page.addInitScript(() => {
    const raf = window.requestAnimationFrame.bind(window);
    window.__noFrames = false;
    window.requestAnimationFrame = (cb) => (window.__noFrames ? 0 : raf(cb));
  });
  await mockApi(page, { multidayDense: true, frameDelayMs: 5 });
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  await startMultiDay(page);
  await expect(page.locator(".status-badge.status-completed")).toHaveText("completed", { timeout: 60_000 });
  await openConsole(page);
  await page.locator(".eoc-subtab", { hasText: "ICS Forms" }).click();
  await page.waitForTimeout(1_500); // the console map has drawn
  await page.evaluate(() => { window.__noFrames = true; });

  const t0 = Date.now();
  await page.locator(".eoc-form-btn", { hasText: "ICS-201" }).click();
  await expect(formBody(page)).toContainText("ICS 201", { timeout: 10_000 });
  expect(Date.now() - t0).toBeLessThan(6_000);
  // The snapshot comes from the last drawn frame (preserved drawing buffer)
  const img = formBody(page).locator("img").first();
  await expect(img).toHaveAttribute("src", /^data:image\/png;base64,/);
  expect(((await img.getAttribute("src")) ?? "").length).toBeGreaterThan(5_000);
  await expect(formBody(page)).not.toContainText("not yet captured");

  // And the 209 (no map in it)
  await page.locator(".eoc-console-header").getByRole("button", { name: "ICS 209-WF" }).click();
  await expect(formBody(page).locator("h1")).toHaveText("INCIDENT STATUS SUMMARY (ICS 209)", { timeout: 10_000 });

  // A hidden page does not wait at all
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" });
  });
  const t1 = Date.now();
  await page.locator(".eoc-form-btn", { hasText: "ICS-202" }).click();
  await expect(formBody(page)).toContainText("ICS 202", { timeout: 10_000 });
  expect(Date.now() - t1).toBeLessThan(3_000);
  expect(errors).toEqual([]);
});

test("reload during a multi-day run: the run is followed to the end without the saved-runs passphrase", async ({ page }) => {
  const errors = pageErrors(page);
  const api = await mockApi(page, { multidayDense: true, frameDelayMs: 20, multidayWsStopAfter: 40, multidayRunningPolls: 2 });
  const passphraseCalls: string[] = [];
  page.on("request", (r) => {
    const u = new URL(r.url());
    if (r.headers()["x-firesim-passphrase"] !== undefined || (u.pathname === "/api/v1/simulations" && r.method() === "GET")) {
      passphraseCalls.push(`${r.method()} ${u.pathname}`);
    }
  });
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  await startMultiDay(page);
  await expect(page.locator(".ts-frame-count")).toHaveText("40/40", { timeout: 20_000 });
  await expect(page.locator(".status-badge.status-running")).toBeVisible();

  await page.reload();
  await expect(page.locator("canvas.maplibregl-canvas").first()).toBeVisible();
  const notice = page.getByTestId("run-reattached");
  await expect(notice).toBeVisible({ timeout: 10_000 });
  await expect(page.locator(".situation-status")).not.toContainText("No run yet");
  await expect(page.locator(".status-badge.status-completed")).toHaveText("completed", { timeout: 30_000 });
  await expect(notice).toContainText("restored after reload");
  await expect(page.locator(".ts-frame-count")).toHaveText("145/145");
  await expect(page.locator(".situation-sub")).toContainText("D3 · T+72:00");
  expect(api.getIds.filter((x) => x === MULTIDAY_ID).length).toBeGreaterThanOrEqual(3);
  // The run's inputs came back too: the header chips show its days' weather
  await expect(page.getByTestId("run-chips")).toContainText("Run wind day 3");
  expect(passphraseCalls).toEqual([]);

  // A finished run is shown again after another reload; Clear results forgets it
  await page.reload();
  await expect(page.locator(".ts-frame-count")).toHaveText("145/145", { timeout: 20_000 });
  await page.getByRole("button", { name: "Clear results" }).click();
  await expect(page.getByTestId("run-chips")).toHaveCount(0);
  await page.reload();
  await expect(page.locator("canvas.maplibregl-canvas").first()).toBeVisible();
  await page.waitForTimeout(1_500);
  await expect(page.locator(".situation-status")).toContainText("No run yet");
  await expect(page.getByTestId("run-reattached")).toHaveCount(0);
  expect(passphraseCalls).toEqual([]);
  expect(errors).toEqual([]);
});

test("a single run past local midnight: the clocks carry the day like the map labels", async ({ page }) => {
  const errors = pageErrors(page);
  await mockApi(page);
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  const dateField = page.getByLabel("Date", { exact: true });
  if (!(await dateField.isVisible())) await page.locator(".setup-section-toggle", { hasText: "Ignition & time" }).click();
  // 15 Jul 2026 23:32 MDT; the 4 h fixture ends 03:32 the next day
  await dateField.fill("2026-07-15");
  await page.getByLabel(/^Time \(/).fill("23:32");
  await runToCompletion(page);

  await expect(page.locator(".situation-title")).toHaveText(/^At D2 03:32 (MDT|GMT-6)$/);
  await expect(page.locator(".situation-status")).toContainText("T+4:00 to D2 03:32");
  await expect(page.locator(".ts-now-clock")).toHaveText(/^D2 03:32(MDT|GMT-6)$/);
  await expect(page.locator(".ts-now-elapsed")).toHaveText("T+4:00");
  await expect(page.getByRole("slider", { name: "Timeline" })).toHaveAttribute("aria-valuetext", /^D2 03:32 (MDT|GMT-6), T\+4:00/);
  const ticks = await page.locator(".ts-tick-clock").allTextContents();
  expect(ticks[0]).toBe("23:32");
  expect(ticks[ticks.length - 1]).toBe("D2 03:32");
  // Before midnight: no day
  await page.getByRole("slider", { name: "Timeline" }).focus();
  await page.keyboard.press("Home");
  await expect(page.locator(".ts-now-clock")).toHaveText(/^23:32(MDT|GMT-6)$/);
  expect(errors).toEqual([]);
});

test("fuel line names the grid's burned fuels; Clear results clears the header chips", async ({ page }) => {
  const errors = pageErrors(page);
  await mockApi(page);
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  await runToCompletion(page);
  const fb = (fixture.frames[fixture.frames.length - 1] as unknown as { fuel_breakdown: Record<string, number> }).fuel_breakdown;
  const top = Object.entries(fb).sort((a, b) => b[1] - a[1])[0];
  // The EOC console's summary (Situation tab) has the Conditions block with the fuel line
  await openConsole(page);
  const fuel = page.locator(".eoc-console .eoc-fuel");
  await expect(fuel).toContainText(`Fuel grid, burned: ${top[0]} ${Math.round(top[1] * 100)} %`);
  await expect(fuel).not.toContainText("Boreal Spruce");

  await page.getByRole("navigation", { name: "Workspace" }).getByRole("button", { name: /Simulation/ }).click();
  await expect(page.getByTestId("run-chips")).toBeVisible();
  await page.getByRole("button", { name: "Clear results" }).click();
  await expect(page.getByTestId("run-chips")).toHaveCount(0);
  await expect(page.locator(".situation-status")).toContainText("No run yet");
  expect(errors).toEqual([]);
});
