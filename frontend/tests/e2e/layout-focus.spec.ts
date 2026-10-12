/**
 * Map-focus layout (owner request 2026-10-11, "the actual viewable map is tiny"):
 * - Setup and Situation collapse to slim rails (header chevron, rail button, `[` / `]`), the map
 *   canvas grows, the view is kept, the timeline stays full width, no horizontal scroll;
 * - focus mode by the map button and by `F`; Esc and the button restore the sides;
 * - after a run, Setup collapses once only for a user without a saved preference (with a
 *   one-time hint), and the rail's Run button still runs;
 * - the legend dock: per-legend minimise to a chip and back, "Hide all", remembered;
 * - below 1280 px the sides are overlay drawers;
 * - axe: no serious/critical violations in the new controls.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mockApi";
import { enterGrassCuring, openApp, runButton, runToCompletion, setIgnitionAtMapCentre } from "./app";

const VIEWPORTS = [
  { width: 1366, height: 768 },
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
];
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

const canvas = (page: Page) => page.locator("canvas.maplibregl-canvas").first();

/** Map canvas size once the column transition has finished (width stable for 3 polls). */
async function canvasSize(page: Page) {
  let last = -1;
  let stable = 0;
  for (let i = 0; i < 40 && stable < 3; i++) {
    const w = (await canvas(page).boundingBox())!.width;
    stable = Math.abs(w - last) < 0.5 ? stable + 1 : 0;
    last = w;
    await page.waitForTimeout(80);
  }
  const b = (await canvas(page).boundingBox())!;
  return { w: b.width, h: b.height };
}

async function expectNoHorizontalScroll(page: Page) {
  const m = await page.evaluate(() => ({
    docW: document.documentElement.scrollWidth,
    bodyW: document.body.scrollWidth,
    w: innerWidth,
    scrollX,
  }));
  expect(m.docW, "no horizontal scroll (document)").toBeLessThanOrEqual(m.w);
  expect(m.bodyW, "no horizontal scroll (body)").toBeLessThanOrEqual(m.w);
  expect(m.scrollX).toBe(0);
}

async function expectTimelineFullWidth(page: Page) {
  const b = (await page.locator(".bottom-bar").boundingBox())!;
  expect(b.x).toBeLessThanOrEqual(0.5);
  expect(b.width).toBeGreaterThanOrEqual(page.viewportSize()!.width - 1);
}

/** Centre of the map view from MapView's data-bounds attribute. */
async function viewCentre(page: Page): Promise<[number, number]> {
  const raw = await page.locator(".map-view-canvas").first().getAttribute("data-bounds");
  const [[w, s], [e, n]] = JSON.parse(raw!) as number[][];
  return [(w + e) / 2, (s + n) / 2];
}

const setupRail = (page: Page) => page.getByTestId("setup-rail");
const situationRail = (page: Page) => page.getByTestId("situation-rail");

test.describe("collapsible side columns", () => {
  for (const vp of VIEWPORTS) {
    test(`${vp.width}x${vp.height}: collapse and expand each side; the map grows, the timeline stays full width`, async ({ page }) => {
      await page.setViewportSize(vp);
      await mockApi(page);
      await openApp(page);
      const w0 = (await canvasSize(page)).w;
      const c0 = await viewCentre(page);

      // Setup: header chevron -> rail
      const collapseSetup = page.getByRole("button", { name: "Collapse Setup" });
      await expect(collapseSetup).toHaveAttribute("aria-expanded", "true");
      await collapseSetup.click();
      await expect(setupRail(page)).toBeVisible();
      await expect(page.locator("#setup-content")).toBeHidden();
      const expandSetup = page.getByRole("button", { name: "Expand Setup" });
      await expect(expandSetup).toHaveAttribute("aria-expanded", "false");
      await expect(expandSetup).toBeFocused(); // focus follows to the rail
      const w1 = (await canvasSize(page)).w;
      expect(w1, "map wider with Setup collapsed").toBeGreaterThan(w0 + 200);

      // Situation: header chevron -> rail
      await page.getByRole("button", { name: "Collapse Situation" }).click();
      await expect(situationRail(page)).toBeVisible();
      const w2 = (await canvasSize(page)).w;
      expect(w2, "map wider with both collapsed").toBeGreaterThan(w1 + 200);
      const railW = (await setupRail(page).boundingBox())!.width;
      expect(railW).toBeLessThanOrEqual(48);
      await expectTimelineFullWidth(page);
      await expectNoHorizontalScroll(page);

      // The view is kept (MapLibre resize keeps the centre and zoom)
      await page.waitForTimeout(300);
      const c1 = await viewCentre(page);
      expect(Math.abs(c1[0] - c0[0])).toBeLessThan(1e-3);
      expect(Math.abs(c1[1] - c0[1])).toBeLessThan(1e-3);

      // Axe: rails, headers, focus button, legend dock
      const axe = await new AxeBuilder({ page })
        .withTags(TAGS)
        .include('[data-testid="setup-rail"]')
        .include('[data-testid="situation-rail"]')
        .include(".map-focus-btn")
        .analyze();
      expect(axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id)).toEqual([]);

      // Expand both from the rails
      await page.getByRole("button", { name: "Expand Setup" }).click();
      await page.getByRole("button", { name: "Expand Situation" }).click();
      await expect(page.locator("#setup-content")).toBeVisible();
      await expect(page.locator("#situation-content")).toBeVisible();
      expect(Math.abs((await canvasSize(page)).w - w0)).toBeLessThan(2);
      await expectNoHorizontalScroll(page);
    });
  }

  test("[ and ] toggle the sides; the choice is remembered after a reload", async ({ page }) => {
    await mockApi(page);
    await openApp(page);
    await page.locator("body").press("[");
    await expect(setupRail(page)).toBeVisible();
    await page.locator("body").press("]");
    await expect(situationRail(page)).toBeVisible();
    // Not while typing in a field
    await page.getByRole("button", { name: "Expand Setup" }).click();
    const search = page.getByPlaceholder("Search location...");
    await search.fill("[");
    await expect(page.locator("#setup-content")).toBeVisible();
    await search.fill("");
    await page.reload();
    await expect(page.locator("canvas.maplibregl-canvas").first()).toBeVisible();
    await expect(page.locator("#setup-content")).toBeVisible();
    await expect(situationRail(page)).toBeVisible();
  });
});

test.describe("map focus mode", () => {
  test("by the map button and by F; Esc and the button restore the sides", async ({ page }) => {
    await mockApi(page);
    await openApp(page);
    const w0 = (await canvasSize(page)).w;
    const focusBtn = page.locator(".map-focus-btn");
    await expect(focusBtn).toHaveAttribute("aria-pressed", "false");
    await focusBtn.click();
    await expect(focusBtn).toHaveAttribute("aria-pressed", "true");
    await expect(setupRail(page)).toBeVisible();
    await expect(situationRail(page)).toBeVisible();
    const wf = (await canvasSize(page)).w;
    const vp = page.viewportSize()!;
    expect(wf, "focus mode: map spans all but two rails").toBeGreaterThanOrEqual(vp.width - 2 * 48);
    await expectTimelineFullWidth(page);
    await focusBtn.click();
    await expect(page.locator("#setup-content")).toBeVisible();
    await expect(page.locator("#situation-content")).toBeVisible();
    expect(Math.abs((await canvasSize(page)).w - w0)).toBeLessThan(2);

    // Keyboard: F in, Esc out
    await page.locator("body").press("f");
    await expect(focusBtn).toHaveAttribute("aria-pressed", "true");
    await page.keyboard.press("Escape");
    await expect(focusBtn).toHaveAttribute("aria-pressed", "false");
    await expect(page.locator("#setup-content")).toBeVisible();
    // F again toggles out
    await page.locator("body").press("f");
    await page.locator("body").press("f");
    await expect(page.locator("#situation-content")).toBeVisible();

    // Focus mode is not a saved preference
    await page.locator("body").press("f");
    await page.reload();
    await expect(page.locator("canvas.maplibregl-canvas").first()).toBeVisible();
    await expect(page.locator("#setup-content")).toBeVisible();
    await expect(page.locator("#situation-content")).toBeVisible();
  });
});

test.describe("auto-collapse after a run", () => {
  test("first run without a saved preference collapses Setup once, with a hint; the rail runs", async ({ page }) => {
    const api = await mockApi(page);
    await openApp(page, { pinLayout: false });
    const w0 = (await canvasSize(page)).w;
    await setIgnitionAtMapCentre(page);
    await runToCompletion(page);
    await expect(setupRail(page)).toBeVisible();
    await expect(page.locator("#setup-content")).toBeHidden();
    await expect(page.locator(".layout-hint")).toContainText("Setup is minimized");
    expect((await canvasSize(page)).w).toBeGreaterThan(w0 + 200);
    // The Situation panel still gets focus after the run
    await expect(page.locator(".situation-panel")).toBeFocused();
    await page.getByRole("button", { name: "Got it" }).click();
    await expect(page.locator(".layout-hint")).toHaveCount(0);

    // The rail's Run button runs again (same checks as the Run bar)
    const run = setupRail(page).getByRole("button", { name: "Run Simulation" });
    await expect(run).toBeEnabled();
    await expect(setupRail(page).getByRole("button", { name: "New ignition" })).toBeVisible();
    await run.click();
    await expect(page.locator(".status-badge.status-completed")).toHaveText("completed", { timeout: 30_000 });
    expect(api.posts).toHaveLength(2);

    // Reopening Setup is a preference: the next run leaves it open, and no hint again
    await page.getByRole("button", { name: "Expand Setup" }).click();
    await runButton(page).click();
    await expect(page.locator(".status-badge.status-completed")).toHaveText("completed", { timeout: 30_000 });
    await expect(page.locator("#setup-content")).toBeVisible();
    await expect(page.locator(".layout-hint")).toHaveCount(0);
  });

  test("a saved Setup preference is never overridden", async ({ page }) => {
    await mockApi(page);
    await openApp(page); // pinned: Setup "open"
    await setIgnitionAtMapCentre(page);
    await runToCompletion(page);
    await expect(page.locator("#setup-content")).toBeVisible();
    await expect(runButton(page)).toBeVisible();
    await expect(page.locator(".layout-hint")).toHaveCount(0);
  });
});

test.describe("legend dock", () => {
  test("minimise a legend to a chip and back, hide all, remembered after a reload", async ({ page }) => {
    await mockApi(page);
    await openApp(page);
    await setIgnitionAtMapCentre(page);
    await runToCompletion(page);
    const dock = page.locator(".legend-dock");
    await expect(dock).toBeVisible();
    const fuel = page.getByTestId("fuel-legend");
    await expect(fuel).toContainText("C2 Boreal spruce");
    const h0 = (await dock.boundingBox())!.height;

    // Minimise the fuel legend: a chip, the dock gets shorter
    await fuel.getByRole("button", { name: "Minimize legend: FBP fuel type" }).click();
    const chip = page.getByRole("button", { name: "Expand legend: FBP fuel type" });
    await expect(chip).toBeVisible();
    await expect(chip).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByText("C2 Boreal spruce")).toHaveCount(0);
    expect((await dock.boundingBox())!.height).toBeLessThan(h0 - 50);

    // Hide all, then show again; the minimised state is kept
    await page.getByRole("button", { name: "Hide all legends" }).click();
    await expect(page.locator(".legend-card")).toHaveCount(0);
    const show = page.getByRole("button", { name: /^Show legends \(\d+\)$/ });
    await expect(show).toBeVisible();

    const axe = await new AxeBuilder({ page }).withTags(TAGS).include(".legend-dock").analyze();
    expect(axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id)).toEqual([]);

    await page.reload();
    await expect(page.locator("canvas.maplibregl-canvas").first()).toBeVisible();
    await page.waitForLoadState("networkidle");
    await enterGrassCuring(page, 60);
    await setIgnitionAtMapCentre(page);
    await runToCompletion(page);
    await expect(page.getByRole("button", { name: /^Show legends/ })).toBeVisible();
    await page.getByRole("button", { name: /^Show legends/ }).click();
    await expect(page.getByRole("button", { name: "Expand legend: FBP fuel type" })).toBeVisible();
    await page.getByRole("button", { name: "Expand legend: FBP fuel type" }).click();
    await expect(page.getByTestId("fuel-legend")).toContainText("C2 Boreal spruce");

    const axe2 = await new AxeBuilder({ page }).withTags(TAGS).include(".legend-dock").analyze();
    expect(axe2.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id)).toEqual([]);
  });

  test("a legend shows only while its layer is visible", async ({ page }) => {
    await mockApi(page);
    await openApp(page);
    await expect(page.getByTestId("fuel-legend")).toBeVisible();
    // Modelled-arrival outlines: listed after a run, gone when the layer is turned off
    await setIgnitionAtMapCentre(page);
    await runToCompletion(page);
    const evac = page.getByTestId("evac-legend");
    await expect(evac).toBeVisible();
    await page.getByLabel("Show modelled arrival outlines on the map").uncheck();
    await expect(evac).toHaveCount(0);
  });
});

test.describe("narrow screens (< 1280 px)", () => {
  test("sides are rails; one opens as a drawer over the map; Esc closes it", async ({ page }) => {
    await page.setViewportSize({ width: 1180, height: 800 });
    await mockApi(page);
    await openApp(page, { curing: null }); // Setup is a rail here: leave curing as the app opens it
    await expect(setupRail(page)).toBeVisible();
    await expect(situationRail(page)).toBeVisible();
    await expect(page.locator(".map-focus-btn")).toHaveCount(0);
    const w0 = (await canvasSize(page)).w;
    expect(w0).toBeGreaterThanOrEqual(1180 - 2 * 48);

    await page.getByRole("button", { name: "Expand Setup" }).click();
    const setup = page.locator(".setup-panel");
    await expect(setup).toHaveClass(/is-drawer/);
    await expect(runButton(page)).toBeVisible();
    // Overlay: the map keeps its size
    expect(Math.abs((await canvasSize(page)).w - w0)).toBeLessThan(2);
    // One drawer at a time
    await page.getByRole("button", { name: "Expand Situation" }).click();
    await expect(page.locator(".situation-panel")).toHaveClass(/is-drawer/);
    await expect(setupRail(page)).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(situationRail(page)).toBeVisible();
    await expectNoHorizontalScroll(page);
  });
});
