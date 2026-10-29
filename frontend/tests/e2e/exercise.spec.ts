/**
 * Exercise mode PR1 (docs/exercise-mode-spec.md §12): end-to-end checks of PR0 behind the flag.
 *
 * - Flag off (no `?exercise=1`, build without VITE_FIRESIM_EXERCISE): no toggle, no banner, the
 *   timeline keeps its own play controls.
 * - Flag on: the toggle shows the EXERCISE banner, which stays on all three workspace tabs; the
 *   clock plays at 12x and the map frame follows it; "Next period" jumps to the open incident's
 *   operational-period end (20:00) and pauses; "Exit exercise" restores the normal timeline.
 * - axe: the banner adds no serious/critical violations (its own region is scanned, so the
 *   shell baseline in axe-baseline.json is unchanged).
 *
 * The browser clock is installed at a fixed instant (15 Jul 2026 17:00 MDT, inside the default
 * 08:00-20:00 period) so the run starts at 17:00, ends at 21:00 (the fixture is 4 h), and the
 * period end falls inside it. The exercise clock measures the wall time that really passed
 * between ticks (spec §4), so `clock.fastForward` stands in for a minute of real playback.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mockApi";
import { openApp, runToCompletion, setIgnitionAtMapCentre } from "./app";

const NOW = new Date("2026-07-15T17:00:00-06:00");

async function openWithFlag(page: Page): Promise<void> {
  await page.clock.install({ time: NOW });
  await mockApi(page);
  // openApp navigates to "/"; the flag comes from the query string, so re-open with it
  await page.addInitScript(() => {
    if (!location.search.includes("exercise=1")) history.replaceState(null, "", "/?exercise=1");
  });
  await openApp(page);
}

async function openIncident(page: Page, name: string): Promise<void> {
  await page.getByRole("navigation", { name: "Workspace" }).getByRole("button", { name: /EOC Console/ }).click();
  await page.getByRole("textbox", { name: "Incident name", exact: true }).fill(name);
  await page.getByRole("button", { name: "Open EOC Console" }).click();
  await expect(page.locator(".op-period-panel")).toBeVisible();
  await page.getByRole("navigation", { name: "Workspace" }).getByRole("button", { name: /Simulation/ }).click();
}

const banner = (page: Page) => page.getByTestId("exercise-banner");
const exerciseTime = (page: Page) => page.getByTestId("exercise-time");
/** The timeline slider's frame index, i.e. the frame shown on the map and in the panels */
const frameShown = async (page: Page) => Number(await page.getByRole("slider", { name: "Timeline" }).inputValue());

test("flag off: nothing of exercise mode is in the DOM", async ({ page }) => {
  await mockApi(page);
  await openApp(page);
  await expect(page.getByTestId("exercise-toggle")).toHaveCount(0);
  await expect(page.getByTestId("exercise-banner")).toHaveCount(0);
  await setIgnitionAtMapCentre(page);
  await runToCompletion(page);
  await expect(page.locator(".time-slider .ts-play")).toBeVisible();
  await expect(page.getByTestId("ts-exercise-label")).toHaveCount(0);
});

test("flag on: banner on every tab, 12x playback moves the map frame, next period, exit", async ({ page }) => {
  test.setTimeout(120_000); // incident + run + three tabs + playback + jump + exit
  await openWithFlag(page);
  await openIncident(page, "Exercise E2E");
  await setIgnitionAtMapCentre(page);
  await runToCompletion(page);
  await expect(page.locator(".time-slider .ts-play")).toBeVisible();

  // Toggle on: the banner, the timeline's "Exercise time" readout, its own play controls hidden
  const toggle = page.getByTestId("exercise-toggle");
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  await expect(banner(page)).toBeVisible();
  await expect(banner(page)).toContainText("EXERCISE");
  await expect(exerciseTime(page)).toContainText("17:00");
  await expect(banner(page)).toContainText("Paused");
  await expect(page.getByTestId("ts-exercise-label")).toBeVisible();
  await expect(page.locator(".time-slider .ts-play")).toHaveCount(0);
  await expect(page.locator(".time-slider .ts-speeds")).toHaveCount(0);
  expect(await frameShown(page)).toBe(0);

  // The banner stays on all three workspace tabs
  const nav = page.getByRole("navigation", { name: "Workspace" });
  for (const tab of [/EOC Console/, /About & sources/, /Simulation/]) {
    await nav.getByRole("button", { name: tab }).click();
    await expect(banner(page)).toBeVisible();
  }

  // 12x: one minute of wall time = 12 exercise minutes; 80 s = 16 min, past the 17:15 frame
  await banner(page).getByRole("button", { name: "12×" }).click();
  await expect(banner(page).getByRole("button", { name: "12×" })).toHaveAttribute("aria-pressed", "true");
  await banner(page).getByRole("button", { name: "Play exercise clock" }).click();
  await expect(banner(page)).toContainText("Running 12×");
  await page.clock.fastForward(80_000);
  await expect(exerciseTime(page)).toContainText("17:16");
  await expect.poll(() => frameShown(page)).toBe(1);

  // Next period: the incident's period ends at 20:00 (inside the 17:00-21:00 run); the clock pauses
  await banner(page).getByRole("button", { name: /Next period/ }).click();
  await expect(exerciseTime(page)).toContainText("20:00");
  await expect(banner(page)).toContainText("Paused");
  await expect.poll(() => frameShown(page)).toBe(12);
  // After the last boundary the button jumps to the end of the run
  await expect(banner(page).getByRole("button", { name: /End/ })).toBeVisible();

  // Exit: banner gone, the normal timeline back, the frame where the exercise left it
  await banner(page).getByRole("button", { name: "Exit exercise" }).click();
  await expect(banner(page)).toHaveCount(0);
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByTestId("ts-exercise-label")).toHaveCount(0);
  await expect(page.locator(".time-slider .ts-play")).toBeVisible();
  await expect(page.locator(".time-slider .ts-speeds")).toBeVisible();
  expect(await frameShown(page)).toBe(12);
});

test("flag on: the exercise banner has no serious/critical axe violations", async ({ page }) => {
  await openWithFlag(page);
  await setIgnitionAtMapCentre(page);
  await runToCompletion(page);
  await page.getByTestId("exercise-toggle").click();
  await expect(banner(page)).toBeVisible();
  for (const state of ["paused", "playing"] as const) {
    if (state === "playing") await banner(page).getByRole("button", { name: "Play exercise clock" }).click();
    const results = await new AxeBuilder({ page })
      .include('[data-testid="exercise-banner"]')
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
      .analyze();
    const gated = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(gated.map((v) => `${v.id}: ${v.help} -> ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`), `banner (${state})`).toEqual([]);
  }
});
