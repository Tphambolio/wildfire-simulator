/**
 * Reload re-attach (backlog E1, field-test 2026-10-18 Issue 2; first fix PR #91 [R56]).
 * The run on screen is kept in sessionStorage ("firesim-v3-live-run", this tab only) from the
 * moment the server gives its ID; after a reload the app reads it back with
 * GET /simulations/{id}: still computing → polled to the end ("Run continued after reload",
 * with per-day progress for multi-day runs), finished → shown again ("Run restored after
 * reload"), gone (server restarted, run removed) → one plain sentence and the record is
 * forgotten. Covers single runs with their ensemble, multi-day runs reloaded on day 2,
 * perimeter restarts, a reload after completion, and the "Reload run" flag of a run that
 * ended short. The multi-day reload of the tester's 72 h case is also in field-test-r4.spec.
 */
import { expect, test, type Page } from "@playwright/test";
import { fixture, mockApi, MULTIDAY_ID, denseMultiDayFrames } from "./mockApi";
import { openApp, runButton, runToCompletion, setIgnitionAtMapCentre } from "./app";

const KEY = "firesim-v3-live-run";
const SINGLE = fixture.simulation_id;
const N = fixture.frames.length; // the fixture starts at T+0: no synthetic start frame

function pageErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  return errors;
}
const recordId = (page: Page) =>
  page.evaluate((k) => (JSON.parse(sessionStorage.getItem(k) ?? "null") as { id?: string } | null)?.id ?? null, KEY);
async function reloadApp(page: Page): Promise<void> {
  await page.reload();
  await expect(page.locator("canvas.maplibregl-canvas").first()).toBeVisible();
}

test("single run with ensemble: reload mid-run re-attaches, follows the run and resumes the ensemble", async ({ page }) => {
  const errors = pageErrors(page);
  const api = await mockApi(page, { ensemble: true, ensembleStep: 10, wsStopAfter: 3, singleRunningPolls: 2, frameDelayMs: 20 });
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  await runButton(page).click();
  await expect(page.locator(".ts-frame-count")).toHaveText("3/3", { timeout: 20_000 });
  await expect(page.locator(".status-badge.status-running")).toBeVisible();
  // Written from the server's answer to the POST, before the run finishes
  expect(await recordId(page)).toBe(SINGLE);
  expect(api.posts[0].ensemble).toEqual({ n_members: 30 });
  const ensBefore = api.ensemblePollIds.length;

  await reloadApp(page);
  const notice = page.getByTestId("run-reattached");
  await expect(notice).toContainText("continued after reload", { timeout: 10_000 });
  await expect(page.locator(".situation-status")).not.toContainText("No run yet");
  await expect(page.locator(".status-badge.status-completed")).toHaveText("completed", { timeout: 30_000 });
  await expect(notice).toContainText("restored after reload");
  await expect(page.locator(".ts-frame-count")).toHaveText(`${N}/${N}`);
  expect(api.posts).toHaveLength(1); // re-read, not re-run
  // The range of outcomes is polled again for the same run and completes
  await expect.poll(() => api.ensemblePollIds.length).toBeGreaterThan(ensBefore);
  expect(new Set(api.ensemblePollIds)).toEqual(new Set([SINGLE]));
  await expect(page.getByTestId("ensemble-card")).toBeVisible({ timeout: 30_000 });
  expect(await recordId(page)).toBe(SINGLE);
  expect(errors).toEqual([]);
});

test("multi-day run reloaded on day 2: re-attached with 'Day 2 of 3' progress, then completes", async ({ page }) => {
  const errors = pageErrors(page);
  const api = await mockApi(page, { multidayDense: true, frameDelayMs: 10, multidayWsStopAfter: 60, multidayRunningPolls: 2 });
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  await page.getByRole("button", { name: /Run options/ }).click();
  await page.getByRole("button", { name: "Multi-day", exact: true }).click();
  await page.locator("button.btn-primary", { hasText: "Run 72h Scenario" }).click();
  await expect(page.locator(".ts-frame-count")).toHaveText("60/60", { timeout: 20_000 });
  expect(await recordId(page)).toBe(MULTIDAY_ID);

  await reloadApp(page);
  await expect(page.getByTestId("run-reattached")).toContainText("continued after reload", { timeout: 10_000 });
  // The server's day comes back with the polled status (the mock reaches day 2 on its 2nd answer)
  await expect(page.getByTestId("run-progress")).toContainText("Day 2 of 3", { timeout: 15_000 });
  await expect(page.locator(".status-badge.status-completed")).toHaveText("completed", { timeout: 30_000 });
  const n = denseMultiDayFrames(3).length;
  await expect(page.locator(".ts-frame-count")).toHaveText(`${n}/${n}`);
  await expect(page.locator(".situation-sub")).toContainText("D3 · T+72:00");
  expect(api.multidayPosts).toHaveLength(1);
  expect(errors).toEqual([]);
});

test("reload after completion: the finished run is shown again without re-running it", async ({ page }) => {
  const errors = pageErrors(page);
  const api = await mockApi(page);
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  await runToCompletion(page);
  const gets = api.getIds.length;

  await reloadApp(page);
  await expect(page.getByTestId("run-reattached")).toContainText("restored after reload", { timeout: 10_000 });
  await expect(page.locator(".status-badge.status-completed")).toHaveText("completed");
  await expect(page.locator(".ts-frame-count")).toHaveText(`${N}/${N}`);
  expect(api.getIds.length).toBeGreaterThan(gets);
  expect(api.posts).toHaveLength(1);
  expect(errors).toEqual([]);
});

test("reload when the server no longer has the run: one plain sentence, the record is forgotten", async ({ page }) => {
  const errors = pageErrors(page);
  const api = await mockApi(page, { wsStopAfter: 3, frameDelayMs: 20 });
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  await runButton(page).click();
  await expect(page.locator(".ts-frame-count")).toHaveText("3/3", { timeout: 20_000 });
  api.gone.add(SINGLE); // the API restarted: runs in progress are lost

  await reloadApp(page);
  const lost = page.getByTestId("run-reattach-lost");
  await expect(lost).toHaveText(
    "The run that was on screen before the reload is no longer on the server (it restarted or the run was removed). Run it again.",
    { timeout: 10_000 },
  );
  await expect(page.getByTestId("run-reattached")).toHaveCount(0);
  await expect(page.locator(".error-toast")).toHaveCount(0); // said once, not twice
  expect(await recordId(page)).toBeNull();
  await expect(runButton(page)).toBeEnabled();

  // The next reload starts clean
  await reloadApp(page);
  await page.waitForTimeout(1_500);
  await expect(page.locator(".situation-status")).toContainText("No run yet");
  await expect(lost).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("perimeter restart: a reload re-attaches the restart with its own start and weather", async ({ page }) => {
  const errors = pageErrors(page);
  const api = await mockApi(page, { runIds: ["first-run-0001"] });
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  await runToCompletion(page);
  await page.locator(".setup-section-toggle", { hasText: "Weather & FWI" }).click();
  await page.getByLabel(/^Wind Speed/).fill("33");
  await page.locator(".setup-section-toggle", { hasText: "Observed perimeter (RPAS)" }).click();
  const panel = page.locator(".recon-panel");
  await panel.getByRole("button", { name: /Use the modelled perimeter at/ }).click();
  await panel.getByRole("button", { name: /Restart at .* from observed/ }).click();
  await expect.poll(() => api.overridePosts.length).toBe(1);
  await expect(page.locator(".status-badge.status-completed")).toHaveText("completed", { timeout: 30_000 });
  expect(await recordId(page)).toBe(SINGLE); // the restart's ID (the mock answers the fixture's)
  const chips = await page.getByTestId("run-chips").innerText();
  expect(chips).toContain("33");

  await reloadApp(page);
  await expect(page.getByTestId("run-reattached")).toContainText("restored after reload", { timeout: 10_000 });
  expect(api.getIds[api.getIds.length - 1]).toBe(SINGLE);
  await expect(page.getByTestId("run-chips")).toContainText("33");
  expect(api.posts).toHaveLength(1);
  expect(api.overridePosts).toHaveLength(1);
  expect(errors).toEqual([]);
});

test("a run that ends short is flagged and Reload run reads it again from the server", async ({ page }) => {
  const errors = pageErrors(page);
  const api = await mockApi(page, { wsCompleteAfter: 4, frameDelayMs: 20 });
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  await runToCompletion(page);
  const short = page.getByTestId("run-short");
  await expect(short).toContainText("Shown to");
  await expect(page.locator(".ts-frame-count")).toHaveText("4/4");
  const gets = api.getIds.length;
  await short.getByRole("button", { name: "Reload run" }).click();
  await expect(page.locator(".ts-frame-count")).toHaveText(`${N}/${N}`, { timeout: 15_000 });
  await expect(short).toHaveCount(0);
  expect(api.getIds.length).toBeGreaterThan(gets);
  expect(errors).toEqual([]);
});
