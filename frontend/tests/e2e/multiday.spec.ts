/**
 * Multi-day runs (QA 2026-10-11) with a 3-day run recorded from the engine
 * (fixtures/terwillegar_multiday_3d.json, record_fixture.py --multiday): the timeline spans all
 * days with day boundaries, each frame says its day and the FWI codes carried over to it (the
 * API's `day_fwi`), the EOC per-day table lists the days, a 2-day request gives 2 days, the
 * polling path keeps the day tags (GET used to drop them), and a run outside Edmonton uses
 * census subdivisions (CSD) as community units.
 */
import { expect, test, type Page } from "@playwright/test";
import { mockApi, MULTIDAY_ID, multidayFixture, recordedMultiDayFrames } from "./mockApi";
import { openApp, setIgnitionAtMapCentre } from "./app";

type DayFwi = { ffmc: number; dmc: number; dc: number; fwi: number };
const lastOfDay = (d: number) => {
  const fr = recordedMultiDayFrames(3).filter((f) => (f as { day?: number }).day === d);
  return fr[fr.length - 1] as typeof fr[number] & { day_fwi: DayFwi };
};

async function runMultiDay(page: Page, label = "Run 72h Scenario"): Promise<void> {
  await page.getByRole("button", { name: /Run options/ }).click();
  await page.getByRole("button", { name: "Multi-day", exact: true }).click();
  const run = page.locator("button.btn-primary", { hasText: label });
  await expect(run).toBeEnabled();
  await run.click();
  await expect(page.locator(".status-badge.status-completed")).toHaveText("completed", { timeout: 30_000 });
}

function pageErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  return errors;
}

test("3-day recorded run: 72 h timeline with day boundaries, carried-over FWI per day, per-day EOC table", async ({ page }) => {
  const errors = pageErrors(page);
  const api = await mockApi(page, { multidayRecorded: true });
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  await runMultiDay(page);

  expect((api.multidayPosts[0].days as unknown[]).length).toBe(3);
  const n = multidayFixture.frames.length + 1; // + the synthetic T=0 frame
  await expect(page.locator(".ts-frame-count")).toHaveText(`${n}/${n}`);
  await expect(page.locator(".ts-day-tick-label")).toHaveText(["D2", "D3"]);
  await expect(page.locator(".ts-tick.last .ts-tick-elapsed")).toHaveText(/72/);
  await expect(page.locator(".ts-now-elapsed, .ts-now-clock").last()).toHaveText(/^D3/);
  const area = page.locator(".kpi", { hasText: "Area" }).locator(".kpi-value");
  await expect(area).toHaveText(`${lastOfDay(3).area_ha.toFixed(1)} ha`);

  // Day 3 (6 mm rain): FWI codes carried over from days 1-2, as the engine computed them
  const d3 = lastOfDay(3).day_fwi;
  const fwiRow = page.getByTestId("day-fwi");
  await expect(fwiRow).toContainText("Day 3 FWI (carried over)");
  await expect(fwiRow).toContainText(`FFMC ${d3.ffmc.toFixed(1)}`);
  await expect(fwiRow).toContainText(`DC ${d3.dc.toFixed(0)}`);
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/fig_multiday_timeline.png` });

  // Step back across the day 3 / day 2 boundary: the day and its FWI follow the frame
  const day3Frames = recordedMultiDayFrames(3).filter((f) => (f as { day?: number }).day === 3).length;
  for (let i = 0; i < day3Frames; i++) await page.getByRole("button", { name: "Previous frame" }).click();
  await expect(page.locator(".ts-now-elapsed, .ts-now-clock").last()).toHaveText(/^D2/);
  const d2 = lastOfDay(2).day_fwi;
  await expect(fwiRow).toContainText("Day 2 FWI (carried over)");
  await expect(fwiRow).toContainText(`${d2.fwi.toFixed(1)} · FFMC ${d2.ffmc.toFixed(1)}`);
  // Day 2 is drier than day 1 and day 3 (rain) wetter: the carry-over moves the codes
  expect(d2.ffmc).toBeGreaterThan(lastOfDay(1).day_fwi.ffmc);
  expect(d3.ffmc).toBeLessThan(d2.ffmc);

  // EOC console: per-day progression with the carried-over FWI of each day
  await page.getByRole("button", { name: /EOC Console/ }).click();
  await page.getByRole("textbox", { name: "Incident name", exact: true }).fill("Multi-day check");
  await page.getByRole("button", { name: "Open EOC Console" }).click();
  for (const d of [1, 2, 3]) {
    const row = page.locator(".eoc-console").getByTestId(`day-row-${d}`);
    await expect(row).toContainText(`Day ${d}`);
    await expect(row).toContainText(lastOfDay(d).area_ha.toFixed(0));
    await expect(row).toContainText(lastOfDay(d).day_fwi.fwi.toFixed(1));
  }
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/fig_eoc_per_day.png` });
  expect(errors).toEqual([]);
});

test("polling fallback (WebSocket closed): frames from GET keep their day and carried-over FWI", async ({ page }) => {
  const errors = pageErrors(page);
  const api = await mockApi(page, { multidayRecorded: true, mode: "poll" });
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  await runMultiDay(page);
  expect(api.getIds).toContain(MULTIDAY_ID);
  await expect(page.locator(".ts-day-tick-label")).toHaveText(["D2", "D3"]);
  await expect(page.locator(".ts-now-elapsed, .ts-now-clock").last()).toHaveText(/^D3/);
  await expect(page.getByTestId("day-fwi")).toContainText("Day 3 FWI (carried over)");
  expect(errors).toEqual([]);
});

test("2-day request: only days 1-2, 48 h timeline; Clear results empties the timeline", async ({ page }) => {
  const errors = pageErrors(page);
  const api = await mockApi(page, { multidayRecorded: true });
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  await page.getByRole("button", { name: /Run options/ }).click();
  await page.getByRole("button", { name: "Multi-day", exact: true }).click();
  await page.getByTitle("Remove day").last().click();
  await page.locator("button.btn-primary", { hasText: "Run 48h Scenario" }).click();
  await expect(page.locator(".status-badge.status-completed")).toHaveText("completed", { timeout: 30_000 });

  expect((api.multidayPosts[0].days as unknown[]).length).toBe(2);
  const n = recordedMultiDayFrames(2).length + 1;
  await expect(page.locator(".ts-frame-count")).toHaveText(`${n}/${n}`);
  await expect(page.locator(".ts-day-tick-label")).toHaveText(["D2"]);
  await expect(page.locator(".ts-tick.last .ts-tick-elapsed")).toHaveText(/48/);
  await expect(page.getByTestId("day-fwi")).toContainText("Day 2 FWI");

  await page.getByRole("button", { name: "Clear results" }).click();
  await expect(page.locator(".time-slider-empty")).toBeVisible();
  await expect(page.getByTestId("day-fwi")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("outside Edmonton (Devon): community units are census subdivisions tagged CSD", async ({ page }) => {
  const errors = pageErrors(page);
  // The recorded run moved ~11 km south-west, onto the Town of Devon (Statistics Canada CSD)
  await mockApi(page, { multidayRecorded: true, translate: [-0.1, -0.07] });
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  await runMultiDay(page);
  const devon = page.locator(".evac-status .evac-arrival-table tbody tr", { hasText: "Devon" });
  await expect(devon).toBeVisible();
  await expect(devon.locator(".evac-source-tag")).toHaveText(/CSD/);
  // No Edmonton neighbourhood is reached by the moved fire
  await expect(page.locator(".evac-status .evac-arrival-table tbody tr", { hasText: "The Uplands" })).toHaveCount(0);
  expect(errors).toEqual([]);
});
