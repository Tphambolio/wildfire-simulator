/**
 * Observed-perimeter tools (backlog A1; docs/active-segments.md), standalone flow, mocked API:
 * - with no run yet, Setup → "Observed perimeter (RPAS)" offers a forecast from an observed
 *   perimeter: load a perimeter file, set the observation time T;
 * - "Classify from RPAS hotspots" posts the perimeter, T and the hotspots to
 *   /classify-perimeter; the panel reads "Segments active 1 of 4" ("rule not validated") and
 *   the map draws the active segment as an active edge;
 * - "Forecast from observed at T" posts /observed-perimeter with T, the classified active
 *   edges, Setup's weather and fuel, no ignition and no hold; the run replays to completed.
 * - no serious/critical axe violations in the panel.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { mockApi } from "./mockApi";
import { openApp } from "./app";

const D = 0.004;
const PERIMETER = {
  type: "Polygon",
  coordinates: [[[-113.66 - D, 53.46 - D], [-113.66 + D, 53.46 - D], [-113.66 + D, 53.46 + D],
    [-113.66 - D, 53.46 + D], [-113.66 - D, 53.46 - D]]],
};
const HOTSPOTS = {
  type: "FeatureCollection",
  features: [{ type: "Feature", properties: { time: "2026-07-15T14:30:00-06:00" },
    geometry: { type: "Point", coordinates: [-113.66 + D, 53.46] } }],
};

test("standalone forecast from an observed perimeter, classified from RPAS hotspots", async ({ page }) => {
  const api = await mockApi(page);
  await openApp(page);
  const toggle = page.locator(".setup-section-toggle", { hasText: "Observed perimeter (RPAS)" });
  await expect(toggle).toContainText("Forecast from an observed perimeter");
  await toggle.click();
  const panel = page.locator(".recon-panel");

  await panel.getByTestId("recon-file").setInputFiles({
    name: "trace.geojson", mimeType: "application/geo+json", buffer: Buffer.from(JSON.stringify(PERIMETER)),
  });
  await expect(panel.getByText("Loaded: trace.geojson")).toBeVisible();
  const map = page.locator("[data-bounds]");
  await expect(map).toHaveAttribute("data-recon-perimeter", "1");
  await panel.getByLabel(/Observed at \(T/).fill("2026-07-15T15:00");

  await panel.getByTestId("recon-hotspots-file").setInputFiles({
    name: "hotspots.geojson", mimeType: "application/geo+json", buffer: Buffer.from(JSON.stringify(HOTSPOTS)),
  });
  await expect(panel.getByTestId("recon-segments")).toContainText("Segments active 1 of 4");
  await expect(panel.getByTestId("recon-segments")).toContainText("Not active (burned out)");
  await expect(panel.getByTestId("recon-segments")).toContainText("rule not validated");
  expect(api.classifyPosts[0].observation_time).toBe("2026-07-15T15:00:00-06:00");
  expect(api.classifyPosts[0].hotspots_geojson).toEqual(HOTSPOTS);
  await expect(panel.getByLabel("Classified from detections")).toBeChecked();
  await expect(map).toHaveAttribute("data-recon-active", "1");
  await expect(panel.getByText(/Held edge|Hold held/)).toHaveCount(0);

  const axe = await new AxeBuilder({ page }).include(".recon-panel").analyze();
  expect(axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id)).toEqual([]);

  await panel.getByRole("button", { name: /Forecast from observed at 15:00/ }).click();
  await expect.poll(() => api.observedPosts.length).toBe(1);
  const req = api.observedPosts[0];
  expect(req.observation_time).toBe("2026-07-15T15:00:00-06:00");
  expect(req).not.toHaveProperty("hold_hours");
  expect((req.active_edges as { type: string }).type).toBe("MultiLineString");
  expect(req.perimeter_geojson).toEqual(PERIMETER);
  expect(req).not.toHaveProperty("ignition_lat");
  expect(req).not.toHaveProperty("start_time");
  expect(req.fuel_grid_path).toBeTruthy();
  expect(req.weather).toBeTruthy();
  expect(api.overridePosts).toHaveLength(0);
  await expect(page.locator(".status-badge.status-completed")).toHaveText("completed", { timeout: 30_000 });
});
