import { expect, test } from "@playwright/test";
import { fixture, mockApi } from "./mockApi";
import { finalFrame, openApp, runToCompletion, setIgnitionAtMapCentre } from "./app";

test.describe("smoke", () => {
  test("load, set ignition, run, and replay the fixture over the WebSocket to completed", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    const api = await mockApi(page);

    await openApp(page);
    await expect(page).toHaveTitle(/FireSim/);
    await expect(page.locator(".metrics-panel")).toContainText("Run a simulation to see metrics");

    await setIgnitionAtMapCentre(page);
    await runToCompletion(page);

    // The request the app sent: the map-centre ignition and the panel defaults
    expect(api.posts).toHaveLength(1);
    const body = api.posts[0];
    expect(body.ignition_lat).toBeCloseTo(53.55, 1);
    expect(body.ignition_lng).toBeCloseTo(-113.49, 1);
    expect(body).toHaveProperty("weather");
    // The app asks for incremental frames, the mode the fixture was recorded in
    expect(body.cells_mode).toBe("incremental");
    expect(api.wsConnections).toBe(1);
    expect(api.polls).toBe(0);

    // All frames arrived (plus the synthetic T=0 frame) and the metrics show the final frame
    const n = fixture.frames.length + 1;
    await expect(page.locator(".ts-frame-count")).toHaveText(`${n}/${n}`);
    const area = page.locator(".metric-row", { hasText: "Area Burned" }).locator(".metric-value");
    await expect(area).toHaveText(`${finalFrame.area_ha.toFixed(1)} ha`);
    await expect(page.locator(".metric-row", { hasText: "Time Elapsed" })).toContainText("T+4.0h");
    if (finalFrame.building_exposure) {
      await expect(page.getByRole("heading", { name: "Building exposure" })).toBeVisible();
    }

    expect(errors).toEqual([]);
  });

  test("falls back to polling GET /simulations/{id} when the WebSocket closes", async ({ page }) => {
    const api = await mockApi(page, { mode: "poll" });
    await openApp(page);
    await setIgnitionAtMapCentre(page);
    await runToCompletion(page);
    expect(api.polls).toBeGreaterThanOrEqual(1);
    const area = page.locator(".metric-row", { hasText: "Area Burned" }).locator(".metric-value");
    await expect(area).toHaveText(`${finalFrame.area_ha.toFixed(1)} ha`);
  });
});
