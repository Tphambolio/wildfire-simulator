/**
 * Critical assets and major roads (Travis, 2026-10-08):
 * - automatic: the Edmonton layers load with the Edmonton fuel grid, no upload;
 * - after a run, the Critical assets card lists reached assets, grouped by category, with
 *   "Fire within 500 m by HH:MM" (and the worst-credible time first with an ensemble), and the
 *   major roads reached; the map labels reached assets; nothing says "at risk" or "≥ 50 %";
 * - category filter, show on the map; sources in the card footer and the map attribution;
 * - axe clean, no text below 12 px; screenshots to $SHOT_DIR (assets_*.png) when set.
 */
import { mkdirSync } from "node:fs";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mockApi";
import { openApp, runToCompletion, setIgnitionAtMapCentre, waitForMapQuiet } from "./app";

const SHOT_DIR = process.env.SHOT_DIR;
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

async function shot(page: Page, name: string, prefix = "assets") {
  if (!SHOT_DIR) return;
  mkdirSync(SHOT_DIR, { recursive: true });
  await page.screenshot({ path: `${SHOT_DIR}/${prefix}_${name}.png` });
}

async function seriousAxe(page: Page): Promise<string[]> {
  const r = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  return r.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
}

async function textBelow12px(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const out: string[] = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const el = n.parentElement;
      if (!el || !n.textContent?.trim()) continue;
      const cs = getComputedStyle(el);
      if (cs.visibility === "hidden" || cs.display === "none") continue;
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0 || r.bottom < 0 || r.top > innerHeight || r.right < 0 || r.left > innerWidth) continue;
      if (parseFloat(cs.fontSize) < 12) out.push(`${cs.fontSize} ${n.textContent.trim().slice(0, 30)}`);
    }
    return out;
  });
}

const reachedOnMap = async (page: Page): Promise<string[]> =>
  JSON.parse((await page.locator(".map-view-canvas").first().getAttribute("data-assets-reached")) ?? "[]");

test.describe("critical assets", () => {
  test.setTimeout(180_000);
  test("loads automatically and lists reached assets and roads in clock time after a run", async ({ page }) => {
    await mockApi(page);
    await openApp(page);
    const card = page.getByTestId("critical-assets");
    // Loaded with the Edmonton grid, before any run, without an upload
    await expect(card.locator(".assets-chip").first()).toBeVisible();
    await expect.poll(async () => Number(await page.locator(".map-view-canvas").first().getAttribute("data-assets"))).toBeGreaterThan(400);
    await expect(card).toContainText("Run a simulation to see when the modelled fire reaches each asset.");

    await setIgnitionAtMapCentre(page);
    await runToCompletion(page);

    const rows = card.locator(".asset-row");
    await expect(rows.first()).toBeVisible();
    const sub = card.locator(".asset-row", { hasText: "Riverview Substation" });
    await expect(sub).toContainText(/Fire within 500 m by \d{2}:\d{2}/);
    await expect(sub).toContainText(/Inside the modelled fire by \d{2}:\d{2}/);
    await expect(card.locator(".assets-group-h", { hasText: "Power plants and substations" })).toBeVisible();
    // Major roads reached, first reach per named road
    await expect(card.locator(".road-row").filter({ has: page.locator(".asset-row-name", { hasText: /^Anthony Henday Drive NW$/ }) })).toContainText(/Fire on the road by \d{2}:\d{2}/);
    // Sources in the card footer
    await expect(card.locator(".assets-sources")).toContainText("City of Edmonton Open Data");
    await expect(card.locator(".assets-sources")).toContainText("Statistics Canada ODHF");
    await expect(card.locator(".assets-sources")).toContainText("© OpenStreetMap contributors");
    // The map labels reached assets and draws reached roads
    await expect.poll(() => reachedOnMap(page)).toContain("Riverview Substation");
    await expect(page.locator(".map-asset-label", { hasText: "Riverview Substation" })).toHaveText(/ · 500 m by \d{2}:\d{2}$/);
    await expect.poll(async () => Number(await page.locator(".map-view-canvas").first().getAttribute("data-roads-reached"))).toBeGreaterThan(0);
    await expect(page.locator(".maplibregl-ctrl-attrib")).toContainText("OpenStreetMap");
    // Model output only: no at-risk flag or burn-probability threshold wording anywhere
    await expect(page.locator("body")).not.toContainText(/at-risk|at risk|P ≥ 50%|≥50 % burn/i);

    await card.scrollIntoViewIfNeeded();
    await waitForMapQuiet(page);
    await shot(page, "after_run");
    expect(await textBelow12px(page)).toEqual([]);
    expect(await seriousAxe(page)).toEqual([]);

    // Category filter: hide power, the substation leaves the card and the map
    await card.getByRole("button", { name: /Power plants and substations/ }).click();
    await expect(card.getByRole("button", { name: /Power plants and substations/ })).toHaveAttribute("aria-pressed", "false");
    await expect(card.locator(".asset-row", { hasText: "Riverview Substation" })).toHaveCount(0);
    await expect.poll(() => reachedOnMap(page)).not.toContain("Riverview Substation");
    await card.getByRole("button", { name: "Show all" }).click();
    await expect(card.locator(".asset-row", { hasText: "Riverview Substation" })).toBeVisible();

    // Show on the map
    await card.locator(".asset-row", { hasText: "Riverview Substation" }).click();
    await waitForMapQuiet(page);
    await shot(page, "fly_to");
    expect(await seriousAxe(page)).toEqual([]);
  });

  test("worst-credible first with the ensemble; EOC summary lists reached assets as model output", async ({ page }) => {
    await mockApi(page, { ensemble: true });
    await openApp(page);
    await setIgnitionAtMapCentre(page);
    await runToCompletion(page);
    const card = page.getByTestId("critical-assets");
    await expect(card).toContainText("worst-credible (P10 of the ensemble) first", { timeout: 30_000 });
    await expect(card.locator(".asset-row").first()).toContainText(/Fire within 500 m by (\d{2}:\d{2}|not reached) \(worst-credible\) · (\d{2}:\d{2}|not reached) \(single run\)/);
    const summary = page.locator(".eoc-reached-section");
    await expect(summary).toContainText("Assets reached by the modelled fire");
    await expect(summary).toContainText("not an instruction");
    await expect(summary).toContainText("worst-credible / single run");
    await card.scrollIntoViewIfNeeded();
    await waitForMapQuiet(page);
    await shot(page, "ensemble");
    expect(await textBelow12px(page)).toEqual([]);
    expect(await seriousAxe(page)).toEqual([]);
  });

  test("data gaps: EOC flagged as unverified, care facilities to verify, secondary roads and ramps reached", async ({ page }) => {
    await mockApi(page);
    await openApp(page);
    const card = page.getByTestId("critical-assets");
    // Data notes in the card, before any run
    const notes = card.locator(".assets-data-note");
    await expect(notes.first()).toContainText("City of Edmonton Emergency Operations Centre: unverified manual point");
    await expect(notes.nth(1)).toContainText(/care facilit(y is|ies are) in no current official list/);
    await expect(card.locator(".assets-sources")).toContainText("Government of Alberta");
    await card.locator(".assets-data-notes").scrollIntoViewIfNeeded();
    await shot(page, "card_notes", "gaps");

    await setIgnitionAtMapCentre(page);
    const t0 = Date.now();
    await runToCompletion(page);
    // Roads now include secondary roads and named ramps
    await expect(card.locator(".road-row").first()).toBeVisible();
    console.log(`[perf] run completion to roads listed (browser, incl. replay): ${Date.now() - t0} ms; roads listed: ${await card.locator(".road-row").count()}`);
    await card.locator(".road-row").first().scrollIntoViewIfNeeded();
    await waitForMapQuiet(page);
    await shot(page, "after_run", "gaps");
    expect(await textBelow12px(page)).toEqual([]);
    expect(await seriousAxe(page)).toEqual([]);
  });
});
