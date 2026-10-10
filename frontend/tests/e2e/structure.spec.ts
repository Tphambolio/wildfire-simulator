/**
 * House-to-house (structure) spread, opt-in and illustrative (docs/structure-spread-spec.md §9):
 * - Setup → Fuel & landscape: "House-to-house spread", off by default, only with the Edmonton
 *   grid and buildings; on = the request carries structure_spread: true; "Ember ignition"
 *   under it (off, enabled only with it) adds structure_embers: true;
 * - Situation card: counts at the selected time, "Illustrative" badge, caveat in a tooltip on
 *   hover/focus (not a paragraph), stacked chart with the timeline cursor;
 * - map layer: involved footprints by mechanism, legend with the badge and caveat tooltip,
 *   toggled from the card; hovering a footprint shows its involvement time and mechanism;
 * - no serious/critical axe violations in the new card and legend.
 * Replays tests/fixtures/structure_spread_4h.json (record_fixture.py --structure).
 * Screenshots go to $SHOT_DIR when set.
 */
import { mkdirSync } from "node:fs";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { DRAW_COUNTER_INIT, mockApi, structureFixture } from "./mockApi";
import { openApp, runToCompletion, setIgnitionAtMapCentre, waitForMapQuiet } from "./app";

const SHOT_DIR = process.env.SHOT_DIR;
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

interface Unit {
  id: number;
  t_h: number;
  mechanism: "front" | "b2b" | "ember";
  polygon: number[][][];
}
interface Counts {
  units_involved: number;
  units_front_contact: number;
  units_structure_to_structure: number;
  units_ember: number;
}
const last = structureFixture.frames[structureFixture.frames.length - 1] as unknown as {
  structure_spread: Counts;
  structure_spread_detail: Unit[];
};

async function shot(page: Page, name: string) {
  if (!SHOT_DIR) return;
  mkdirSync(SHOT_DIR, { recursive: true });
  await page.screenshot({ path: `${SHOT_DIR}/structure_${name}.png` });
}

async function enableStructureSpread(page: Page) {
  await page.locator(".setup-section-toggle", { hasText: "Fuel & landscape" }).click();
  const box = page.getByLabel("House-to-house spread");
  await expect(box).not.toBeChecked();
  await expect(box).toBeEnabled();
  // Needs the buildings: disabled without them
  await page.getByLabel(/^Buildings/).uncheck();
  await expect(box).toBeDisabled();
  await page.getByLabel(/^Buildings/).check();
  // Ember ignition: under house-to-house spread, off, enabled only with it
  const embers = page.getByLabel("Ember ignition");
  await expect(embers).not.toBeChecked();
  await expect(embers).toBeDisabled();
  await box.check();
  await expect(embers).toBeEnabled();
  await embers.check();
}

/** Pixel of a [lng, lat] point in the map canvas, from the bounds MapView publishes (Mercator). */
async function toPixel(page: Page, lng: number, lat: number) {
  const canvas = page.locator(".map-view-canvas");
  const bounds = JSON.parse((await canvas.getAttribute("data-bounds"))!) as [[number, number], [number, number]];
  const box = (await canvas.boundingBox())!;
  const merc = (la: number) => Math.log(Math.tan(Math.PI / 4 + (la * Math.PI) / 360));
  const [[w, s], [e, n]] = bounds;
  return {
    x: box.x + ((lng - w) / (e - w)) * box.width,
    y: box.y + ((merc(n) - merc(lat)) / (merc(n) - merc(s))) * box.height,
    inside: lng > w && lng < e && lat > s && lat < n,
  };
}

test("house-to-house spread: opt-in, counts, chart, map layer with tooltip", async ({ page }) => {
  await page.addInitScript(DRAW_COUNTER_INIT);
  const api = await mockApi(page, { structure: true });
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  // The fixture's start (record_fixture.py STRUCT_REQUEST), so clock times and the burning
  // period shading on the timeline match the replayed run
  await page.getByLabel("Date", { exact: true }).fill("2026-08-08");
  await page.getByLabel(/^Time \(/).fill("13:00");
  await enableStructureSpread(page);
  await runToCompletion(page);
  expect(api.posts[0].structure_spread).toBe(true);
  expect(api.posts[0].structure_embers).toBe(true);
  expect(api.posts[0].start_time).toBe(structureFixture.config!.start_time);
  expect(api.posts[0].burning_period).toEqual(structureFixture.config!.burning_period);
  // 13:00-17:00 is inside the burning period: no "No spread" marker on the timeline
  await expect(page.locator(".ts-now-off")).toHaveCount(0);

  // Situation card: final frame selected
  const card = page.getByTestId("structure-panel");
  await card.scrollIntoViewIfNeeded();
  await expect(card.getByRole("heading", { name: /House-to-house spread/ })).toBeVisible();
  await expect(card.locator(".struct-badge")).toHaveText("Illustrative");
  const row = (label: string) => card.locator(".metric-row", { hasText: label });
  await expect(row("Buildings involved")).toContainText(String(last.structure_spread.units_involved));
  await expect(row("Front contact")).toContainText(String(last.structure_spread.units_front_contact));
  await expect(row("Building to building")).toContainText(String(last.structure_spread.units_structure_to_structure));
  // The fixture run had embers on (none ignited a building there): the ember row is shown
  await expect(row("Ember ignition")).toContainText(String(last.structure_spread.units_ember));
  await expect(card.getByTestId("structure-chart")).toBeVisible();
  // The caveat is not a paragraph: hidden until hover or focus
  const cardTip = card.getByRole("tooltip");
  await expect(cardTip).toBeHidden();
  await card.getByRole("button", { name: "About house-to-house spread" }).hover();
  await expect(cardTip).toBeVisible();
  await expect(cardTip).toContainText("Illustrative — not validated in Canada");
  await expect(cardTip).toContainText("not a prediction of which buildings will burn");
  await expect(cardTip).toContainText("150 kW/m² design fire");
  await page.mouse.move(5, 5);
  await expect(cardTip).toBeHidden();

  // Map legend: badge, caveat on keyboard focus
  const legend = page.getByTestId("structure-legend");
  await expect(legend).toBeVisible();
  await expect(legend.locator(".struct-badge")).toHaveText("Illustrative");
  await expect(legend).toContainText("Front contact");
  await expect(legend).toContainText("Building to building");
  await legend.getByRole("button", { name: "About the house-to-house layer" }).focus();
  const legendTip = legend.getByRole("tooltip");
  await expect(legendTip).toBeVisible();
  await expect(legendTip).toContainText("30 m");
  await expect(legendTip).toContainText("~50 m");
  await page.keyboard.press("Escape");
  await expect(legendTip).toBeHidden();

  // Accessibility of the new card and legend
  const axe = await new AxeBuilder({ page })
    .withTags(TAGS)
    .include('[data-testid="structure-panel"]')
    .include('[data-testid="structure-legend"]')
    .analyze();
  expect(axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id)).toEqual([]);

  // Hover an involved footprint: tooltip with mechanism, time and the label
  await waitForMapQuiet(page);
  const units = [...last.structure_spread_detail].sort((a, b) => a.t_h - b.t_h);
  let hovered = false;
  for (const u of units) {
    const ring = u.polygon[0];
    const lng = ring.slice(0, -1).reduce((a, p) => a + p[0], 0) / (ring.length - 1);
    const lat = ring.slice(0, -1).reduce((a, p) => a + p[1], 0) / (ring.length - 1);
    const px = await toPixel(page, lng, lat);
    if (!px.inside) continue;
    await page.mouse.move(px.x, px.y);
    const popup = page.getByTestId("structure-popup");
    if (await popup.isVisible().catch(() => false) || (await popup.waitFor({ timeout: 800 }).then(() => true).catch(() => false))) {
      await expect(popup).toContainText(/Front contact|Building to building|Ember ignition/);
      // Clock time on the run's own clock: 13:00 + t_h, within the 4 h run
      await expect(popup).toContainText(/involved at 1[3-7]:\d\d /);
      await expect(popup).toContainText("Illustrative — not validated in Canada");
      hovered = true;
      break;
    }
  }
  expect(hovered).toBe(true);
  await shot(page, "layer");
  await page.mouse.move(5, 5);

  // Toggle the layer off and on from the card (keyboard)
  const toggle = card.getByLabel("Show on map");
  await expect(toggle).toBeChecked();
  await toggle.focus();
  await page.keyboard.press("Space");
  await expect(toggle).not.toBeChecked();
  await expect(legend).toBeHidden();
  await page.keyboard.press("Space");
  await expect(legend).toBeVisible();

  // Timeline: the first frame has fewer involved units; the chart cursor moves
  const cursorX = async () => Number(await card.getByTestId("structure-chart-cursor").getAttribute("x1"));
  const xEnd = await cursorX();
  await page.getByRole("slider", { name: "Timeline" }).focus();
  await page.keyboard.press("Home");
  expect(await cursorX()).toBeLessThan(xEnd);
  // The app's first frame is its synthetic ignition frame (no counts); the next is the run's first
  await expect(row("Buildings involved")).toContainText("—");
  await page.keyboard.press("ArrowRight");
  const first = (structureFixture.frames[0] as unknown as { structure_spread: Counts }).structure_spread;
  await expect(row("Buildings involved")).toContainText(String(first.units_involved));
});

test("house-to-house spread is off by default: not sent, no card or layer", async ({ page }) => {
  const api = await mockApi(page);
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  await runToCompletion(page);
  expect(api.posts[0].structure_spread).toBe(false);
  expect(api.posts[0].structure_embers).toBe(false);
  await expect(page.getByTestId("structure-panel")).toHaveCount(0);
  await expect(page.getByTestId("structure-legend")).toHaveCount(0);
});
