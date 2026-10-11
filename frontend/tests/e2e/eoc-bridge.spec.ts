/**
 * The bridge from a run to the EOC (QA 2026-10-11): runs are filed under operational periods,
 * the EOC forms describe the active period's own run (never another period's), a run made
 * before the incident was opened can be attached, a run is filed under the period it was
 * started for, Planning's evacuation statuses and the assets reached carry into the forms,
 * exports work, and after a reload the period's run is loaded back from the server (or the
 * app says it is gone).
 */
import { expect, test, type Page } from "@playwright/test";
import { fixture, mockApi } from "./mockApi";
import { openApp, runToCompletion, setIgnitionAtMapCentre } from "./app";

const A = fixture.simulation_id;
const B = `${A}-2`;

function pageErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  return errors;
}

const tab = (page: Page, name: RegExp) => page.getByRole("navigation", { name: "Workspace" }).getByRole("button", { name });
const panel = (page: Page) => page.locator(".op-period-panel");
const dayBtn = (page: Page, d: number) => panel(page).locator(".op-period-day-btn", { hasText: `Day ${d}` });

/** Open an ICS form in the EOC console and return its text. */
async function formText(page: Page, label: string): Promise<string> {
  await page.locator(".eoc-subtab", { hasText: "ICS Forms" }).click();
  await page.locator(".eoc-form-btn", { hasText: label }).click();
  const body = page.frameLocator("iframe.eoc-form-iframe").locator("body");
  await expect(body).toContainText(label.slice(0, 7).replace("-", " ")); // "ICS 201", "ICS 202"
  await expect(body).toContainText("Operational Period");
  return body.innerText();
}

test("attach → Day 2 has no run of its own → run Day 2 → switch periods → reload restores → server gone", async ({ page }) => {
  const errors = pageErrors(page);
  const api = await mockApi(page, { distinctRunIds: true });
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  await runToCompletion(page);

  // Incident opened after the run: the run is not filed yet; attach it to Day 1
  await tab(page, /EOC Console/).click();
  await page.getByRole("textbox", { name: "Incident name", exact: true }).fill("Bridge check");
  await page.getByRole("button", { name: "Open EOC Console" }).click();
  const runInfo = page.getByTestId("period-run");
  await expect(runInfo).toContainText("Current run · not yet filed under this period");
  await panel(page).getByRole("button", { name: "Attach current run to Day 1" }).click();
  await expect(runInfo).toHaveText(`Run ${A}`);
  await expect(dayBtn(page, 1)).toContainText("✓");
  let t = await formText(page, "ICS-201 Briefing");
  expect(t).toContain("Day 1 ·");
  expect(t).toContain(A);

  // Day 2: ghost of Day 1, and the forms no longer present Day 1's run as Day 2's
  await panel(page).getByRole("button", { name: "+ New Period" }).click();
  await panel(page).getByRole("button", { name: "Advance" }).click();
  await expect(panel(page).locator(".op-period-day-btn.active")).toContainText("Day 2");
  await expect(page.locator("path.eoc-ghost-perimeter")).toHaveCount(1);
  await expect(runInfo).toContainText("No run for this period (current run is Day 1's)");
  // The Day 1 form shown before is not kept in the viewer for Day 2
  await page.locator(".eoc-subtab", { hasText: "ICS Forms" }).click();
  await expect(page.locator(".eoc-forms-empty")).toBeVisible();
  await expect(page.locator("iframe.eoc-form-iframe")).toHaveCount(0);
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/fig_eoc_day2_ghost.png` });
  t = await formText(page, "ICS-201 Briefing");
  expect(t).toContain("Day 2 ·");
  expect(t).toContain("No run for this operational period");
  expect(t).not.toContain(A);

  // Run Day 2: filed under Day 2 with its own ID
  await tab(page, /Simulation/).click();
  await runToCompletion(page);
  await tab(page, /EOC Console/).click();
  await expect(dayBtn(page, 2)).toContainText("✓");
  await expect(runInfo).toHaveText(`Run ${B}`);
  t = await formText(page, "ICS-202 Objectives");
  expect(t).toContain(B);

  // Back to Day 1: its run is not the one loaded; load it from the server
  await dayBtn(page, 1).click();
  await expect(runInfo).toContainText(`Run ${A} · not loaded`);
  t = await formText(page, "ICS-201 Briefing");
  expect(t).toContain(`${A} (not loaded`);
  expect(t).not.toContain(B);
  await panel(page).getByRole("button", { name: "Load Day 1 run" }).click();
  await expect(runInfo).toHaveText(`Run ${A}`);
  expect(api.getIds).toContain(A);
  t = await formText(page, "ICS-201 Briefing");
  expect(t).toContain("Day 1 ·");
  expect(t).not.toContain("not loaded");

  // ICS 209-WF for Day 1 is stamped with Day 1's run
  const [popup] = await Promise.all([
    page.waitForEvent("popup"),
    page.locator(".eoc-form-btn", { hasText: "ICS 209-WF" }).click(),
  ]);
  await expect(popup.locator("h1")).toHaveText("INCIDENT STATUS SUMMARY (ICS 209)", { timeout: 10_000 });
  const t209 = await popup.locator("body").innerText();
  expect(t209).toContain(A);
  expect(t209).toContain("MODEL OUTPUT");
  if (process.env.SHOT_DIR) await popup.screenshot({ path: `${process.env.SHOT_DIR}/fig_ics209_day1.png`, fullPage: false });
  await popup.close();

  // Reload: the incident, both periods and their runs' IDs come back; the run is loaded on request
  await page.reload();
  await expect(page.locator("canvas.maplibregl-canvas").first()).toBeVisible();
  await tab(page, /EOC Console/).click();
  await expect(panel(page).locator(".op-period-day-btn")).toHaveCount(2);
  await expect(dayBtn(page, 1)).toContainText("✓");
  await expect(dayBtn(page, 2)).toContainText("✓");
  await expect(runInfo).toContainText(`Run ${A} · not loaded`);
  await expect(panel(page).locator(".op-period-stats")).toContainText("ha burned");
  await panel(page).getByRole("button", { name: "Load Day 1 run" }).click();
  await expect(runInfo).toHaveText(`Run ${A}`);

  // The server restarted (runs are in memory only): the app says so, nothing crashes
  api.gone.add(B);
  await dayBtn(page, 2).click();
  await panel(page).getByRole("button", { name: "Load Day 2 run" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "no longer on the server" })).toBeVisible();
  await expect(page.locator("path.eoc-ghost-perimeter")).toHaveCount(1);
  await expect(page.locator(".app-error-fallback")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("a run is filed under the period it was started for, even if Planning switches periods meanwhile", async ({ page }) => {
  const errors = pageErrors(page);
  await mockApi(page, { frameDelayMs: 250 });
  await openApp(page);
  await tab(page, /EOC Console/).click();
  await page.getByRole("textbox", { name: "Incident name", exact: true }).fill("Switch while running");
  await page.getByRole("button", { name: "Open EOC Console" }).click();
  await panel(page).getByRole("button", { name: "+ New Period" }).click();
  await panel(page).getByRole("button", { name: "Advance" }).click();
  await expect(panel(page).locator(".op-period-day-btn.active")).toContainText("Day 2");

  await tab(page, /Simulation/).click();
  await setIgnitionAtMapCentre(page);
  await page.locator("button.btn-primary", { hasText: "Run Simulation" }).click();
  await expect(page.locator(".status-badge.status-running")).toBeVisible();
  await tab(page, /EOC Console/).click();
  await dayBtn(page, 1).click();
  await expect(page.locator(".status-badge.status-completed")).toHaveText("completed", { timeout: 30_000 });

  await expect(dayBtn(page, 2)).toContainText("✓");
  await expect(dayBtn(page, 1)).not.toContainText("✓");
  await expect(page.getByTestId("period-run")).toContainText("No run for this period (current run is Day 2's)");
  expect(errors).toEqual([]);
});

test("Planning's statuses and the assets reached carry into the forms and Day 2; GeoJSON export", async ({ page }) => {
  const errors = pageErrors(page);
  await mockApi(page);
  await openApp(page);
  await tab(page, /EOC Console/).click();
  await page.getByRole("textbox", { name: "Incident name", exact: true }).fill("Carry check");
  await page.getByRole("button", { name: "Open EOC Console" }).click();
  await tab(page, /Simulation/).click();
  await setIgnitionAtMapCentre(page);
  await runToCompletion(page);
  await page.locator(".evac-status").getByLabel("Evacuation status for The Uplands", { exact: true }).selectOption("Alert");

  // Export GeoJSON (top bar): a FeatureCollection with the modelled perimeter
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Export GeoJSON" }).click()]);
  const gj = JSON.parse(await (await download.createReadStream()).toArray().then((b) => Buffer.concat(b).toString("utf8")));
  expect(gj.type).toBe("FeatureCollection");
  expect(gj.features.length).toBeGreaterThan(0);

  await tab(page, /EOC Console/).click();
  const t = await formText(page, "ICS-202 Objectives");
  expect(t).toContain("Riverview Substation");
  expect(t).toMatch(/Evacuation Alert \(set by Planning\): .*The Uplands/);

  // Statuses are incident-wide: still set on Day 2 (and no tier is ever suggested)
  await panel(page).getByRole("button", { name: "+ New Period" }).click();
  await panel(page).getByRole("button", { name: "Advance" }).click();
  const t2 = await formText(page, "ICS-202 Objectives");
  expect(t2).toMatch(/Evacuation Alert \(set by Planning\): .*The Uplands/);
  expect(t2).not.toContain("Riverview Substation"); // Day 2 has no run of its own
  expect(errors).toEqual([]);
});
