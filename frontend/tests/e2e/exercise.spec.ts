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
 *
 * PR2 (scenario packs): loading the Terwillegar example pack sets Setup (start 15 May 14:00 MDT,
 * codes 92/40/300, the T+3 h wind rise as a weather change), opens an incident with its three
 * periods (08:00/20:00 boundaries), sets the clock to the pack's 24 h span at its start and schedules its two injects;
 * after Run (the mock replays the 4 h fixture) the banner's span is still the pack's 24 h.
 * Facilitator flow: the passphrase unlocks stored packs; save, list, open by ID, delete.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { E2E_EXERCISE_PASSPHRASE, mockApi } from "./mockApi";
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

const TERWILLEGAR = "EXERCISE: Terwillegar Park grass fire (Edmonton river valley)";

test("PR2: load the Terwillegar pack, Run: Setup from the pack, banner span = pack duration", async ({ page }) => {
  test.setTimeout(120_000);
  const api = await (async () => {
    await page.clock.install({ time: NOW });
    const state = await mockApi(page);
    await page.addInitScript(() => {
      if (!location.search.includes("exercise=1")) history.replaceState(null, "", "/?exercise=1");
    });
    await openApp(page);
    return state;
  })();
  // The Load pack section exists only in exercise mode
  await expect(page.getByTestId("exercise-packs")).toHaveCount(0);
  await page.getByTestId("exercise-toggle").click();
  const packs = page.getByTestId("exercise-packs");
  await expect(packs).toBeVisible();
  await packs.getByRole("button", { name: `Load pack ${TERWILLEGAR}` }).click();

  // Loaded: the clock at the pack start, paused, over the pack's 24 h; injects scheduled
  await expect(page.getByTestId("exercise-pack-loaded")).toContainText(TERWILLEGAR);
  await expect(page.getByTestId("exercise-pack-loaded")).toContainText("3 periods");
  await expect(exerciseTime(page)).toContainText("14:00");
  await expect(page.getByTestId("exercise-span")).toHaveText("of 24 h");
  await expect(banner(page)).toContainText("Paused");
  await expect(banner(page)).toContainText("T+0:00");
  await expect(page.locator(".setup-section").filter({ hasText: "Exercise injects" })).toContainText("2 injects");

  // Run: the request carries the pack's Setup
  await runToCompletion(page);
  const body = api.posts[api.posts.length - 1] as Record<string, unknown>;
  expect(body.start_time).toBe("2026-05-15T14:00:00-06:00");
  expect(body.ignition_lat).toBeCloseTo(53.4606, 4);
  expect(body.ignition_lng).toBeCloseTo(-113.6596, 4);
  expect(body.duration_hours).toBe(24);
  expect(body.fwi_overrides).toEqual({ ffmc: 92, dmc: 40, dc: 300 });
  expect(body.weather).toMatchObject({ wind_speed: 20, wind_direction: 225, temperature: 25, relative_humidity: 30 });
  expect(body.weather_schedule).toEqual([
    { hours_from_start: 3, wind_speed: 35, wind_direction: 225, temperature: 25, relative_humidity: 30, precipitation_mm_h: 0 },
  ]);
  expect(body.hourly_weather ?? null).toBeNull();

  // After the run (the mocked 4 h fixture) the span is still the pack's, not the run's
  await expect(page.getByTestId("exercise-span")).toHaveText("of 24 h");
  await expect(exerciseTime(page)).toContainText("14:00");
  // Next period jumps to the pack's period changes (20:00, T+6; then 08:00, T+18) and pauses
  await banner(page).getByRole("button", { name: /Next period/ }).click();
  await expect(exerciseTime(page)).toContainText("20:00");
  await expect(banner(page)).toContainText("T+6:00");
  await banner(page).getByRole("button", { name: /Next period/ }).click();
  await expect(exerciseTime(page)).toContainText("08:00");
  await expect(banner(page)).toContainText("T+18:00");
  await expect(page.getByTestId("exercise-span")).toHaveText("of 24 h");
});

test("PR2: facilitator passphrase, save the pack to the server, open by ID, delete", async ({ page }) => {
  test.setTimeout(90_000);
  await page.clock.install({ time: NOW });
  const api = await mockApi(page);
  await page.addInitScript(() => {
    if (!location.search.includes("exercise=1")) history.replaceState(null, "", "/?exercise=1");
  });
  await openApp(page);
  await page.getByTestId("exercise-toggle").click();
  const packs = page.getByTestId("exercise-packs");
  // A wrong passphrase is refused and asked again
  await packs.getByLabel("Facilitator passphrase").fill("wrong");
  await packs.getByRole("button", { name: "Show stored packs" }).click();
  await expect(packs.getByRole("alert")).toHaveText("Wrong passphrase");
  await packs.getByLabel("Facilitator passphrase").fill(E2E_EXERCISE_PASSPHRASE);
  await packs.getByRole("button", { name: "Show stored packs" }).click();
  await expect(packs).toContainText("No packs stored on this server.");

  await packs.getByRole("button", { name: `Load pack ${TERWILLEGAR}` }).click();
  await packs.getByRole("button", { name: "Save to server" }).click();
  await expect(packs.getByRole("status")).toContainText("Saved on the server as");
  expect(api.packs.size).toBe(1);
  const [id] = [...api.packs.keys()];
  expect(id).toMatch(/^[0-9a-f]{32}$/);
  await expect(packs.getByRole("list", { name: "Stored packs" })).toContainText("Terwillegar Park grass fire");

  // Open by ID (players' link; no passphrase needed by the API)
  await packs.getByRole("button", { name: "Unload" }).click();
  await expect(page.getByTestId("exercise-pack-loaded")).toHaveCount(0);
  await packs.getByLabel("Pack ID").fill(id);
  await packs.getByRole("button", { name: "Open", exact: true }).click();
  await expect(page.getByTestId("exercise-pack-loaded")).toContainText(TERWILLEGAR);
  await expect(page.getByTestId("exercise-span")).toHaveText("of 24 h");

  page.once("dialog", (d) => void d.accept());
  await packs.getByRole("button", { name: `Delete pack ${TERWILLEGAR}` }).click();
  await expect(packs).toContainText("No packs stored on this server.");
  expect(api.packs.size).toBe(0);
});
