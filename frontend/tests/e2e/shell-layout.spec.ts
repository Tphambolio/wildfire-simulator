/**
 * Map-first layout (design spec §2.1-2.3, §9 PR 3 acceptance):
 * - at 1366x768, 1440x900 and 1920x1080 the Run button is in the viewport without scrolling,
 *   the Situation panel is visible and the page does not scroll horizontally;
 * - after the fixture run completes the map is fitted to the fire (its bounds contain the
 *   final perimeter) and focus moves to the Situation panel;
 * - timeline labels are wall-clock HH:MM (America/Edmonton) with T+ as the secondary label.
 */
import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mockApi";
import { finalFrame, openApp, runButton, runToCompletion, setIgnitionAtMapCentre } from "./app";

const VIEWPORTS = [
  { width: 1366, height: 768 },
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
];

async function expectInViewport(page: Page, selector: string) {
  const box = await page.locator(selector).first().boundingBox();
  expect(box, `${selector} is rendered`).not.toBeNull();
  const vp = page.viewportSize()!;
  expect(box!.x, `${selector} left edge`).toBeGreaterThanOrEqual(0);
  expect(box!.y, `${selector} top edge`).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width, `${selector} right edge`).toBeLessThanOrEqual(vp.width + 0.5);
  expect(box!.y + box!.height, `${selector} bottom edge`).toBeLessThanOrEqual(vp.height + 0.5);
}

async function expectNoPageScroll(page: Page) {
  const m = await page.evaluate(() => ({
    docW: document.documentElement.scrollWidth,
    bodyW: document.body.scrollWidth,
    docH: document.documentElement.scrollHeight,
    w: innerWidth,
    h: innerHeight,
    scrollX,
    scrollY,
  }));
  expect(m.docW, "no horizontal scroll (document)").toBeLessThanOrEqual(m.w);
  expect(m.bodyW, "no horizontal scroll (body)").toBeLessThanOrEqual(m.w);
  expect(m.docH, "no page scroll (panels scroll on their own)").toBeLessThanOrEqual(m.h);
  expect(m.scrollX).toBe(0);
  expect(m.scrollY).toBe(0);
}

test.describe("map-first layout", () => {
  for (const vp of VIEWPORTS) {
    test(`${vp.width}x${vp.height}: Run bar and Situation panel in view, no horizontal scroll`, async ({ page }) => {
      await page.setViewportSize(vp);
      await mockApi(page);
      await openApp(page);

      // Before an ignition is set the Run button says why it is disabled
      await expect(runButton(page)).toBeDisabled();
      await expect(page.locator(".run-bar-reason")).toContainText("Set an ignition point");
      await expectInViewport(page, "button.run-button");
      await expectInViewport(page, ".situation-panel");
      await expect(page.locator(".situation-panel")).toBeVisible();
      await expectNoPageScroll(page);

      // Opening every Setup section must not push the Run bar out of view
      for (const t of await page.locator(".setup-section-toggle").all()) {
        if ((await t.getAttribute("aria-expanded")) === "false") await t.click();
      }
      await expect(page.locator(".setup-section-toggle[aria-expanded=false]")).toHaveCount(0);
      await expectInViewport(page, "button.run-button");
      await expectNoPageScroll(page);

      await setIgnitionAtMapCentre(page);
      await expect(page.locator(".run-bar-reason")).not.toContainText("Set an ignition point");
      await runToCompletion(page);
      await expectInViewport(page, "button.run-button");
      await expectInViewport(page, ".situation-panel");
      await expectNoPageScroll(page);
    });
  }

  test("after the run: map fitted to the fire, Situation panel focused, clock-time timeline", async ({ page }) => {
    await mockApi(page);
    await openApp(page);
    await setIgnitionAtMapCentre(page);
    await runToCompletion(page);

    // Map bounds ([[west, south], [east, north]]) contain the final perimeter
    const perim = finalFrame.perimeter as number[][];
    expect(perim.length).toBeGreaterThan(2);
    const lats = perim.map(([lat]) => lat);
    const lngs = perim.map(([, lng]) => lng);
    await expect
      .poll(async () => {
        const raw = await page.locator(".map-view-canvas").first().getAttribute("data-bounds");
        if (!raw) return false;
        const [[w, s], [e, n]] = JSON.parse(raw) as number[][];
        return Math.min(...lngs) >= w && Math.max(...lngs) <= e && Math.min(...lats) >= s && Math.max(...lats) <= n
          // and actually zoomed in: the view is no more than ~20x the fire's extent
          && (e - w) < 20 * (Math.max(...lngs) - Math.min(...lngs));
      }, { message: "map bounds contain the final perimeter", timeout: 10_000 })
      .toBe(true);

    await expect(page.locator(".situation-panel")).toBeFocused();
    await expect(page.locator(".situation-title")).toHaveText(/^At \d{2}:\d{2} M[DS]T$/);
    await expect(page.locator(".situation-status")).toContainText("Run complete");

    // Timeline: clock labels HH:MM, T+ secondary, slider value text has both
    const clocks = await page.locator(".ts-tick-clock").allTextContents();
    expect(clocks.length).toBeGreaterThanOrEqual(2);
    for (const c of clocks) expect(c).toMatch(/^\d{2}:\d{2}$/);
    const elapsed = await page.locator(".ts-tick-elapsed").allTextContents();
    expect(elapsed[0]).toBe("T+0:00");
    expect(elapsed[elapsed.length - 1]).toBe("T+4:00");
    await expect(page.locator(".ts-now-clock")).toHaveText(/^\d{2}:\d{2}M[DS]T$/);
    await expect(page.getByRole("slider", { name: "Timeline" })).toHaveAttribute(
      "aria-valuetext",
      /^\d{2}:\d{2} M[DS]T, T\+4:00$/,
    );
  });

  test("Ctrl+Enter runs once an ignition is set", async ({ page }) => {
    const api = await mockApi(page);
    await openApp(page);
    await page.keyboard.press("Control+Enter");
    expect(api.posts).toHaveLength(0);
    await setIgnitionAtMapCentre(page);
    await page.keyboard.press("Control+Enter");
    await expect(page.locator(".status-badge.status-completed")).toHaveText("completed", { timeout: 30_000 });
    expect(api.posts).toHaveLength(1);
  });
});
