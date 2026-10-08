/**
 * Range of outcomes (ensemble) view, against the recorded 30-member ensemble
 * (tests/fixtures/ensemble.json.gz, mockApi `ensemble: true`):
 * - the Run options ask for a 30-member ensemble by default (grid runs) and say it is uncalibrated;
 * - after the single run, the Situation panel shows "Ensemble n/30" progress, then the
 *   worst-credible (P10) extent, the member area range and the uncalibrated caveat, with the
 *   single run captioned "Single run (P50-like)";
 * - the map draws P10 arrival lines labelled in clock time ("14:30"); scrubbing the timeline
 *   moves lines between drawn (up to the selected time) and projected;
 * - burn probability toggles on with its legend; the Neighbourhoods card leads with the
 *   worst-credible time;
 * - no text below 12 px; screenshots go to $SHOT_DIR when set.
 */
import { mkdirSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";
import { ensembleFixture, mockApi } from "./mockApi";
import { openApp, runToCompletion, setIgnitionAtMapCentre } from "./app";

const SHOT_DIR = process.env.SHOT_DIR;

async function shot(page: Page, name: string) {
  if (!SHOT_DIR) return;
  mkdirSync(SHOT_DIR, { recursive: true });
  await page.screenshot({ path: `${SHOT_DIR}/ensemble_${name}.png` });
}

/** Visible text nodes below 12 px (design spec §6.3). */
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

const fmt = (ha: number) => (ha >= 100 ? Math.round(ha).toLocaleString("en-CA") : ha >= 10 ? ha.toFixed(0) : ha.toFixed(1));

test("range of outcomes: progress, P10 clock-time lines, burn probability, caveat", async ({ page }) => {
  const api = await mockApi(page, { ensemble: true });
  await openApp(page);

  // Run options: the ensemble option is on by default, 30 members, with the cost and caveat
  await page.getByRole("button", { name: /Run options/ }).click();
  const option = page.getByLabel("Range of outcomes (ensemble)");
  await expect(option).toBeChecked();
  await expect(page.locator(".ensemble-option")).toContainText("about 1 s per member");
  await expect(page.locator(".ensemble-option")).toContainText("uncalibrated");

  await setIgnitionAtMapCentre(page);
  await runToCompletion(page);
  expect(api.posts[0].ensemble).toEqual({ n_members: 30 });

  // Progress while the members run
  const card = page.getByTestId("ensemble-card");
  await expect(card).toContainText(/Ensemble \d+\/30/);
  await expect(card.getByRole("progressbar")).toBeVisible();
  await shot(page, "progress");

  // Complete: worst-credible extent, area range across members and the caveat
  await expect(page.getByTestId("ensemble-p10-area")).toBeVisible({ timeout: 20_000 });
  await expect(card).toContainText("Worst-credible extent by");
  const range = page.getByTestId("ensemble-area-range");
  await expect(range).toContainText(fmt(ensembleFixture.area_ha.min));
  await expect(range).toContainText(fmt(ensembleFixture.area_ha.p50));
  await expect(range).toContainText(`${fmt(ensembleFixture.area_ha.max)} ha`);
  await expect(page.getByTestId("ensemble-caveat")).toContainText("Uncalibrated range");
  await expect(page.getByTestId("ensemble-caveat")).toContainText("over-predicts");
  await expect(page.locator(".situation-kpi-caption")).toHaveText("Single run (P50-like)");

  // P10 lines labelled in clock time; at the end of the run every line is drawn (not projected)
  const labels = page.locator(".map-iso-label");
  await expect.poll(() => labels.count()).toBeGreaterThanOrEqual(4);
  for (const t of await labels.allTextContents()) expect(t).toMatch(/^\d{2}:\d{2}$/);
  const drawn = async () => JSON.parse((await page.locator(".map-view-canvas").getAttribute("data-ens-lines")) ?? "[]") as Array<[string, string, number]>;
  expect((await drawn()).length).toBe(8);
  expect((await drawn()).every(([, phase]) => phase === "past")).toBe(true);
  await expect(page.getByTestId("ensemble-legend")).toContainText("Worst-credible arrival (P10)");

  // Neighbourhoods: worst-credible time first, single run beside it
  await expect(page.locator(".evac-arrival-table thead")).toContainText("worst-credible");
  await expect(page.locator(".evac-arrival-table thead")).toContainText("Single run");
  await expect(page.locator(".evac-arrival-worst").first()).toHaveText(/^\d{2}:\d{2}$/);
  await page.waitForTimeout(500);
  await shot(page, "complete");

  // Scrub back: lines after the selected time become projected, the P10 extent shrinks
  const p10End = await page.getByTestId("ensemble-p10-area").textContent();
  const slider = page.getByRole("slider", { name: "Timeline" });
  await slider.focus();
  await page.keyboard.press("Home");
  for (let i = 0; i < 6; i++) await page.keyboard.press("ArrowRight");
  await expect.poll(async () => (await drawn()).filter(([, phase]) => phase === "future").length).toBeGreaterThan(0);
  await expect(page.locator(".map-iso-label-future").first()).toBeAttached();
  await expect(page.getByTestId("ensemble-p10-area")).not.toHaveText(p10End ?? "");
  await page.waitForTimeout(300);
  await shot(page, "scrubbed");

  // Burn probability on: legend ramp shown
  await card.getByLabel("Burn probability").check();
  await expect(page.getByTestId("ensemble-legend")).toContainText("Burn probability, share of members");
  await page.waitForTimeout(500);
  await shot(page, "burn_probability");

  expect(await textBelow12px(page)).toEqual([]);
});

test("without an ensemble the single run stays the headline", async ({ page }) => {
  await mockApi(page);
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  await runToCompletion(page);
  // The API answers 404 for a run without an ensemble: no card, no caption, no lines
  await expect(page.getByTestId("ensemble-card")).toHaveCount(0, { timeout: 5_000 });
  await expect(page.locator(".situation-kpi-caption")).toHaveCount(0);
  await expect(page.locator(".map-iso-label")).toHaveCount(0);
});
