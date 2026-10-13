/**
 * Re-running replaces the previous run completely (owner report 2026-10-11): a single run
 * with the ensemble on, then Setup → Run options → Multi-day → Run, used to keep showing the
 * old 4 h run while the app polled the old run's /ensemble. Starting any new run must:
 * - send the new request (here POST /simulations/multiday);
 * - stop polling the old run's ensemble, and ask the server to stop computing it
 *   (DELETE /simulations/{id}/ensemble);
 * - drop the old frames, so the timeline spans the new run (72 h for 3 days);
 * and the reverse (multi-day → single) must also start clean.
 */
import { expect, test } from "@playwright/test";
import { fixture, mockApi, MULTIDAY_ID, multiDayFrames } from "./mockApi";
import { openApp, runButton, runToCompletion, setIgnitionAtMapCentre } from "./app";

const OLD = fixture.simulation_id;

test("single run with ensemble → multi-day: new request, old ensemble poll stops, 72 h timeline", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const api = await mockApi(page, { ensemble: true, ensembleNeverCompletes: true });
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  await runToCompletion(page);
  expect(api.posts[0].ensemble).toEqual({ n_members: 30 });
  const n1 = fixture.frames.length; // the fixture starts at T+0: no synthetic start frame (owner bug 11)
  await expect(page.locator(".ts-frame-count")).toHaveText(`${n1}/${n1}`);
  // The ensemble of the single run is being polled
  await expect.poll(() => api.ensemblePollIds.filter((id) => id === OLD).length).toBeGreaterThanOrEqual(2);

  // Setup → Run options → Multi-day → Run
  await page.getByRole("button", { name: /Run options/ }).click();
  await page.getByRole("button", { name: "Multi-day", exact: true }).click();
  const mdRun = page.locator("button.btn-primary", { hasText: "Run 72h Scenario" });
  await expect(mdRun).toBeEnabled();
  await mdRun.click();

  // The multi-day request is sent, and the app asks the server to stop the old ensemble
  await expect.poll(() => api.multidayPosts.length).toBe(1);
  expect((api.multidayPosts[0].days as unknown[]).length).toBe(3);
  expect(api.multidayPosts[0].cells_mode).toBe("incremental");
  expect(api.ensembleCancels).toEqual([OLD]);

  // It completes, the timeline spans 72 h and holds only the multi-day frames
  await expect(page.locator(".status-badge.status-completed")).toHaveText("completed", { timeout: 30_000 });
  const md = multiDayFrames(3);
  const n2 = md.length + 1;
  await expect(page.locator(".ts-frame-count")).toHaveText(`${n2}/${n2}`);
  await expect(page.locator(".ts-tick.last .ts-tick-elapsed")).toHaveText(/72/);
  const area = page.locator(".kpi", { hasText: "Area" }).locator(".kpi-value");
  await expect(area).toHaveText(`${md[md.length - 1].area_ha.toFixed(1)} ha`);
  // No ensemble card for the multi-day run
  await expect(page.getByTestId("ensemble-card")).toHaveCount(0);

  // The old ensemble poll stopped: no request for the old ID after the multi-day POST
  const before = api.ensemblePollIds.filter((id) => id === OLD).length;
  await page.waitForTimeout(3000);
  expect(api.ensemblePollIds.filter((id) => id === OLD).length).toBe(before);
  expect(api.ensemblePollIds.filter((id) => id === MULTIDAY_ID).length).toBe(0);
  expect(errors).toEqual([]);
});

test("multi-day → single: the single run replaces the multi-day frames", async ({ page }) => {
  const api = await mockApi(page);
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  await page.getByRole("button", { name: /Run options/ }).click();
  await page.getByRole("button", { name: "Multi-day", exact: true }).click();
  await page.locator("button.btn-primary", { hasText: "Run 72h Scenario" }).click();
  await expect(page.locator(".status-badge.status-completed")).toHaveText("completed", { timeout: 30_000 });
  const n2 = multiDayFrames(3).length + 1;
  await expect(page.locator(".ts-frame-count")).toHaveText(`${n2}/${n2}`);

  await page.getByRole("button", { name: "Single Event", exact: true }).click();
  await runToCompletion(page);
  expect(api.posts).toHaveLength(1);
  const n1 = fixture.frames.length; // the fixture starts at T+0: no synthetic start frame (owner bug 11)
  await expect(page.locator(".ts-frame-count")).toHaveText(`${n1}/${n1}`);
  await expect(page.locator(".ts-day-tick")).toHaveCount(0);
  await expect(runButton(page)).toBeEnabled();
  expect(api.ensembleCancels).toEqual([]); // the multi-day run had no ensemble to stop
});
