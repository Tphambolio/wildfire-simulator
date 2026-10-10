/**
 * Decluttered UI (owner request 2026-10-10): explanations in accessible tooltips, literature in
 * the About & sources tab, short badges for what must stay visible; the main-run progress bar;
 * re-running at a new ignition without a reload (the user's weather kept).
 * Screenshots go to $SHOT_DIR (declutter_*.png) when set.
 */
import { mkdirSync } from "node:fs";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page } from "@playwright/test";
import { fixture, mockApi } from "./mockApi";
import { openApp, runButton, runToCompletion, setIgnitionAtMapCentre, waitForMapQuiet } from "./app";

const SHOT_DIR = process.env.SHOT_DIR;
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

async function shot(page: Page, name: string) {
  if (!SHOT_DIR) return;
  mkdirSync(SHOT_DIR, { recursive: true });
  await page.screenshot({ path: `${SHOT_DIR}/declutter_${name}.png` });
}

async function seriousAxe(page: Page): Promise<string[]> {
  const res = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  return res.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(", ")}`);
}

const popOf = async (page: Page, trigger: Locator) =>
  page.locator(`[id="${(await trigger.getAttribute("aria-describedby")) ?? (await trigger.getAttribute("aria-controls"))}"]`);

const bounds = async (page: Page) => page.locator(".map-view-canvas").first().getAttribute("data-bounds");

test("About & sources tab: eight sections, references on GitHub, version; the low-skill badge opens Limits", async ({ page }) => {
  await mockApi(page);
  await openApp(page);
  const badge = page.getByTestId("limits-badge");
  await expect(badge).toHaveText("Low one-day skill");
  // Hover shows the explanation; the pop-up stays while the pointer moves onto it (WCAG 1.4.13)
  await badge.hover();
  const pop = await popOf(page, badge);
  await expect(pop).toBeVisible();
  await expect(pop).toContainText("not an operational forecast");
  await pop.hover();
  await page.waitForTimeout(500);
  await expect(pop).toBeVisible();
  await page.mouse.move(600, 600);
  await expect(pop).toBeHidden();

  await badge.click();
  const about = page.getByTestId("about-panel");
  await expect(about).toBeVisible();
  await expect(page.getByRole("button", { name: /About & sources/ })).toHaveAttribute("aria-current", "page");
  await expect(page.locator("#about-limits")).toBeFocused();
  await expect(about.getByRole("heading", { level: 3 })).toHaveCount(8);
  await expect(about).toContainText("143 Alberta fire-days");
  await expect(about).toContainText("Van Wagner & Pickett (1985)");
  await expect(about).toContainText("FireSim never recommends Order, Alert or Watch");
  await expect(about.getByRole("link", { name: "§4.2 references" })).toHaveAttribute("href", /github\.com\/Tphambolio\/wildfire-simulator\/blob\/master\/docs\/PROJECT_RECORD\.md#42-references/);
  await expect(page.getByTestId("about-version")).toContainText("3.0.0 · e2e0sha");
  await expect(about).not.toContainText(/~\/dev|\/home\//);
  await shot(page, "about");
  expect(await seriousAxe(page)).toEqual([]);

  // Back to the map: still there (never unmounted)
  await page.getByRole("button", { name: /Simulation/ }).first().click();
  await expect(page.locator("canvas.maplibregl-canvas").first()).toBeVisible();
});

test("tooltips work from the keyboard (focus opens, Esc closes) and axe stays clean with one open", async ({ page }) => {
  await mockApi(page);
  await openApp(page);
  await page.locator(".setup-section-toggle", { hasText: "Fuel & landscape" }).click();
  // Wording fixes and badges that stay visible
  await expect(page.getByLabel("Use Edmonton fuel grid")).toBeChecked();
  await expect(page.locator(".setup-panel")).not.toContainText("FBP 10");
  await expect(page.getByLabel(/^Buildings \(346,238 footprints\)/)).toBeVisible();
  const wui = page.getByTestId("wui-unsourced");
  await expect(wui).toHaveText("Unsourced");
  // Tab from the WUI checkbox reaches the badge; focus opens its tooltip
  await page.getByLabel("WUI zone modifiers").focus();
  await page.keyboard.press("Tab");
  await expect(wui).toBeFocused();
  const tip = await popOf(page, wui);
  await expect(tip).toBeVisible();
  await expect(tip).toHaveAttribute("role", "tooltip");
  await expect(tip).toContainText("no documented source");
  // The tooltip is in the top layer: not clipped by the scrolling Setup column
  const inTop = await tip.evaluate((el) => (el as HTMLElement).matches(":popover-open"));
  expect(inTop).toBe(true);
  await shot(page, "tooltip_open");
  await page.keyboard.press("Escape");
  await expect(tip).toBeHidden();
  await expect(wui).toBeFocused();

  // axe with a tooltip open (one that covers no other control: an open pop-up over
  // checkboxes is reported as obscuring their targets, which is what a pop-up does)
  const limits = page.getByTestId("limits-badge");
  await limits.focus();
  await expect(await popOf(page, limits)).toBeVisible();
  expect(await seriousAxe(page)).toEqual([]);
  await page.keyboard.press("Escape");

  // Grid tip: the real resolution
  const grid = page.getByRole("button", { name: "About the Edmonton fuel grid" });
  await grid.focus();
  await expect(await popOf(page, grid)).toContainText("20 m cells");
  await expect(await popOf(page, grid)).toContainText("50 m cells for the run");
});

test("main-run progress: phase labels, an advancing bar, gone on completion", async ({ page }) => {
  await mockApi(page, { phases: true, phaseDelayMs: 350, frameDelayMs: 60 });
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  await runButton(page).click();
  const prog = page.getByTestId("run-progress");
  await expect(prog).toBeVisible();
  const bar = prog.getByRole("progressbar", { name: "Simulation run" });
  await expect(prog).toContainText(/Loading fuel grid…|Loading buildings…|Starting…/);
  await expect(bar).not.toHaveAttribute("aria-valuenow", /.+/); // indeterminate before the spread
  await expect(prog).toContainText("Running spread…", { timeout: 10_000 });
  await expect(bar).toHaveAttribute("aria-valuenow", "25", { timeout: 10_000 });
  await shot(page, "run_progress");
  await expect(bar).toHaveAttribute("aria-valuenow", /^(50|75|100)$/, { timeout: 10_000 }); // advancing
  await expect(prog).toContainText(/\d:\d\d/); // elapsed time
  expect(await seriousAxe(page)).toEqual([]);
  await expect(page.locator(".status-badge.status-completed")).toHaveText("completed", { timeout: 30_000 });
  await expect(prog).toHaveCount(0);
});

test("re-run at a new ignition without reloading: weather kept, old results replaced, view kept", async ({ page }) => {
  const api = await mockApi(page);
  await openApp(page);
  // A non-default wind and FFMC set by the user
  await page.locator(".setup-section-toggle", { hasText: "Weather & FWI" }).click();
  await page.getByLabel(/^Wind Speed/).fill("37");
  await page.getByLabel(/^FFMC/).fill("93");
  await setIgnitionAtMapCentre(page);
  await runToCompletion(page);
  await waitForMapQuiet(page);
  const n = fixture.frames.length + 1;
  await expect(page.locator(".ts-frame-count")).toHaveText(`${n}/${n}`);
  const viewAfterFirst = await bounds(page);

  // 1) "New ignition" next to Run, then a map click
  const newIgn = page.locator(".run-bar").getByRole("button", { name: "New ignition" });
  await expect(newIgn).toBeVisible();
  await newIgn.click();
  await expect(page.locator(".mcp-placement-hint")).toBeVisible();
  const canvas = page.locator("canvas.maplibregl-canvas").first();
  const box = (await canvas.boundingBox())!;
  await page.mouse.click(box.x + box.width * 0.35, box.y + box.height * 0.65);
  await shot(page, "new_ignition");
  await runButton(page).click();
  // The old results are gone as the new run starts, then the new frames render
  await expect(page.locator(".status-badge.status-completed")).toHaveText("completed", { timeout: 30_000 });
  await expect(page.locator(".ts-frame-count")).toHaveText(`${n}/${n}`);
  expect(api.posts).toHaveLength(2);
  expect(api.posts[1].ignition_lng as number).toBeLessThan(api.posts[0].ignition_lng as number);
  expect(api.posts[1].ignition_lat as number).toBeLessThan(api.posts[0].ignition_lat as number);
  expect((api.posts[1].weather as { wind_speed: number }).wind_speed).toBe(37);
  expect((api.posts[1].fwi_overrides as { ffmc: number }).ffmc).toBe(93);
  await waitForMapQuiet(page);
  expect(await bounds(page)).toBe(viewAfterFirst); // the map view is kept (no re-fit)

  // 2) The relabelled map control: "Move ignition", then a map click
  const move = page.getByRole("button", { name: /Move ignition/ });
  await expect(move).toBeVisible();
  await move.click();
  await page.mouse.click(box.x + box.width * 0.6, box.y + box.height * 0.4);
  await runButton(page).click();
  await expect(page.locator(".status-badge.status-completed")).toHaveText("completed", { timeout: 30_000 });
  expect(api.posts).toHaveLength(3);
  expect(api.posts[2].ignition_lng as number).toBeGreaterThan(api.posts[1].ignition_lng as number);
  expect((api.posts[2].weather as { wind_speed: number }).wind_speed).toBe(37);
  expect(api.wsConnections).toBe(3);
  await waitForMapQuiet(page);
  expect(await bounds(page)).toBe(viewAfterFirst);

  // Clear results: back to the empty state, inputs kept
  await page.getByRole("button", { name: "Clear results" }).click();
  await expect(page.locator(".situation-status")).toContainText("No run yet");
  await expect(page.locator(".ts-frame-count")).toHaveCount(0);
  await expect(runButton(page)).toBeEnabled();
});
