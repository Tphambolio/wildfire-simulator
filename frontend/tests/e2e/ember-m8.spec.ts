/**
 * M8 spot-fire likelihood (v8 ember model, [R70]), mocked API:
 * - plain-ignition run: Setup → Run options shows "Spot-fire likelihood (M8)" disabled with
 *   "needs an observed active edge"; the run request carries no ember_m8;
 * - RPAS restart: the option stays disabled while the whole perimeter is active, is enabled
 *   once edges are classified, shows the approved sentence and "Relative only", and the restart
 *   posts ember_m8 with the active edges; the map draws the returned tiers with a legend
 *   ("Higher likelihood"), never a count, probability or percentage.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { mockApi } from "./mockApi";
import { openApp, runToCompletion, setIgnitionAtMapCentre } from "./app";

const SENTENCE = "ranks likely spot-fire locations near the active edge better than a fuel-and-distance baseline";

function cell(lng: number, lat: number, tier: "higher" | "middle") {
  const dx = 0.0057, dy = 0.0034;
  return { type: "Feature", properties: { tier, distance_km: 2.0 }, geometry: { type: "Polygon", coordinates: [[
    [lng, lat], [lng + dx, lat], [lng + dx, lat + dy], [lng, lat + dy], [lng, lat]]] } };
}

const EMBER_M8 = {
  model: "M8", model_version: "v8-2026-10-29", description: SENTENCE,
  caveat: "Relative only, not calibrated (no counts or probabilities); weaker at longer distances; no better than ELMFIRE; does not respond to wind speed (beta_w = 0). A post-hoc check that restricted Canadian fuel to hazard class left M8's margin intact ([R70] addendum).",
  display: "relative tiers only",
  tiers: { type: "FeatureCollection", features: [
    ...Array.from({ length: 6 }, (_, k) => cell(-113.60 + k * 0.006, 53.455, "higher")),
    ...Array.from({ length: 8 }, (_, k) => cell(-113.60 + k * 0.006, 53.452, "middle")),
  ] },
  n_candidate_cells: 70, n_by_tier: { higher: 6, middle: 8, lower: 56 },
};

test("M8: disabled on a plain ignition run, offered on an RPAS restart with an observed active edge", async ({ page }) => {
  const api = await mockApi(page, { emberM8: EMBER_M8 });
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  const runOpts = page.locator(".setup-section-toggle", { hasText: "Run options" });
  if ((await runOpts.getAttribute("aria-expanded")) !== "true") await runOpts.click();
  const setupOpt = page.getByTestId("setup-ember-m8");
  await expect(setupOpt).toContainText("Spot-fire likelihood (M8)");
  await expect(setupOpt).toContainText("needs an observed active edge");
  await expect(page.getByTestId("setup-ember-m8-toggle")).toBeDisabled();
  await runToCompletion(page);
  expect(api.posts[api.posts.length - 1]).not.toHaveProperty("ember_m8");

  // RPAS restart from the modelled perimeter
  await page.locator(".setup-section-toggle", { hasText: /RPAS|Observed perimeter/ }).first().click();
  const panel = page.locator(".recon-panel");
  await panel.getByRole("button", { name: /Use the modelled perimeter/ }).click();
  const box = panel.getByTestId("recon-ember-m8-toggle");
  await expect(box).toBeDisabled();
  await expect(panel.getByTestId("recon-ember-m8")).toContainText("needs an observed active edge");

  await panel.getByTestId("recon-hotspots-file").setInputFiles({
    name: "hotspots.geojson", mimeType: "application/geo+json",
    buffer: Buffer.from(JSON.stringify({ type: "FeatureCollection", features: [] })),
  });
  await expect(panel.getByTestId("recon-segments")).toContainText("Segments active 1 of 4");
  await expect(box).toBeEnabled();
  await expect(panel.getByTestId("recon-ember-m8")).toContainText(SENTENCE);
  await expect(panel.getByTestId("recon-ember-m8")).toContainText("Relative only");
  await box.check();
  const axe = await new AxeBuilder({ page }).include(".recon-panel").analyze();
  expect(axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id)).toEqual([]);

  await panel.getByRole("button", { name: /Restart at .* from observed/ }).click();
  await expect.poll(() => api.overridePosts.length).toBe(1);
  const req = api.overridePosts[0];
  expect(req.ember_m8).toBe(true);
  expect((req.active_edges as { type: string }).type).toBe("MultiLineString");
  await expect(page.locator(".status-badge.status-completed")).toHaveText("completed", { timeout: 30_000 });
  await expect(page.locator("[data-bounds]")).toHaveAttribute("data-ember-m8", "14");
  const legend = page.getByTestId("ember-m8-legend");
  await expect(legend).toContainText("Spot-fire likelihood (M8)");
  await expect(legend).toContainText("Higher likelihood");
  await expect(legend).toContainText("Relative only");
  await expect(legend).not.toContainText(/\d+ ?%|probability of|expected spots|spot count:/i);
});
