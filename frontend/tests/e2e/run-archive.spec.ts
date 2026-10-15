/**
 * Run archive, phase 1 (docs/run-archive.md): after the API restarts or is redeployed, an
 * operational period's run is loaded back from the server's run archive ("Loaded from
 * archive"); a run in neither memory nor the archive gets the "no longer on the server" notice.
 */
import { expect, test, type Page } from "@playwright/test";
import { fixture, mockApi } from "./mockApi";
import { openApp, runToCompletion, setIgnitionAtMapCentre } from "./app";

const A = fixture.simulation_id;
const B = `${A}-2`;

const tab = (page: Page, name: RegExp) => page.getByRole("navigation", { name: "Workspace" }).getByRole("button", { name });
const panel = (page: Page) => page.locator(".op-period-panel");
const dayBtn = (page: Page, d: number) => panel(page).locator(".op-period-day-btn", { hasText: `Day ${d}` });

test("Load Day N run after a server restart: from the archive, or the run is gone", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const api = await mockApi(page, { distinctRunIds: true });
  await openApp(page);
  await setIgnitionAtMapCentre(page);

  // Day 1: open the incident, run, file the run under Day 1
  await tab(page, /EOC Console/).click();
  await page.getByRole("textbox", { name: "Incident name", exact: true }).fill("Archive check");
  await page.getByRole("button", { name: "Open EOC Console" }).click();
  await tab(page, /Simulation/).click();
  await runToCompletion(page);
  await tab(page, /EOC Console/).click();
  const runInfo = page.getByTestId("period-run");
  await expect(runInfo).toHaveText(`Run ${A}`);

  // Day 2 with its own run
  await panel(page).getByRole("button", { name: "+ New Period" }).click();
  await panel(page).getByRole("button", { name: "Advance" }).click();
  await tab(page, /Simulation/).click();
  await runToCompletion(page);
  await tab(page, /EOC Console/).click();
  await expect(runInfo).toHaveText(`Run ${B}`);

  // The API restarts (deploy): Day 1's run is now served from the archive, Day 2's is gone
  api.archived.add(A);
  api.gone.add(B);
  await page.reload();
  await expect(page.locator("canvas.maplibregl-canvas").first()).toBeVisible();
  await tab(page, /EOC Console/).click();
  await dayBtn(page, 1).click();
  await expect(runInfo).toContainText(`Run ${A} · not loaded`);
  await panel(page).getByRole("button", { name: "Load Day 1 run" }).click();
  await expect(runInfo).toHaveText(`Run ${A} · Loaded from archive`);
  expect(api.getIds).toContain(A);
  await expect(page.getByRole("alert").filter({ hasText: "no longer on the server" })).toHaveCount(0);

  await dayBtn(page, 2).click();
  await panel(page).getByRole("button", { name: "Load Day 2 run" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "no longer on the server or in its run archive" })).toBeVisible();
  await expect(page.locator(".app-error-fallback")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("Load Day N run with both run ID formats: 8-hex (before 2026-10-13) and 32-hex (since)", async ({ page }) => {
  // api services/run_ids.py: new runs get 32 hex characters; incidents saved before keep 8
  const LEGACY = "1a2b3c4d";
  const LONG = "0123456789abcdef0123456789abcdef";
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const api = await mockApi(page, { runIds: [LEGACY, LONG] });
  await openApp(page);
  await setIgnitionAtMapCentre(page);

  await tab(page, /EOC Console/).click();
  await page.getByRole("textbox", { name: "Incident name", exact: true }).fill("Run ID check");
  await page.getByRole("button", { name: "Open EOC Console" }).click();
  await tab(page, /Simulation/).click();
  await runToCompletion(page);
  await tab(page, /EOC Console/).click();
  const runInfo = page.getByTestId("period-run");
  await expect(runInfo).toHaveText(`Run ${LEGACY}`);

  await panel(page).getByRole("button", { name: "+ New Period" }).click();
  await panel(page).getByRole("button", { name: "Advance" }).click();
  await tab(page, /Simulation/).click();
  await runToCompletion(page);
  await tab(page, /EOC Console/).click();
  await expect(runInfo).toHaveText(`Run ${LONG}`);

  // Reload (incident from localStorage): Day 1's run from the archive, Day 2's from memory
  api.archived.add(LEGACY);
  await page.reload();
  await expect(page.locator("canvas.maplibregl-canvas").first()).toBeVisible();
  await tab(page, /EOC Console/).click();
  await dayBtn(page, 1).click();
  await expect(runInfo).toContainText(`Run ${LEGACY} · not loaded`);
  await panel(page).getByRole("button", { name: "Load Day 1 run" }).click();
  await expect(runInfo).toHaveText(`Run ${LEGACY} · Loaded from archive`);

  await dayBtn(page, 2).click();
  await expect(runInfo).toContainText(`Run ${LONG} · not loaded`);
  await panel(page).getByRole("button", { name: "Load Day 2 run" }).click();
  await expect(runInfo).toHaveText(`Run ${LONG}`);
  expect(api.getIds).toEqual(expect.arrayContaining([LEGACY, LONG]));
  await expect(page.getByRole("alert").filter({ hasText: "no longer on the server" })).toHaveCount(0);
  expect(errors).toEqual([]);
});
