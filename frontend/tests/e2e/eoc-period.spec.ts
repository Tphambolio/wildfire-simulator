/**
 * Owner-reported crash (2026-10-11): with an incident open, run a simulation, open the EOC
 * Console and start a new operational period -> the whole screen went black. Day 2 draws the
 * previous period's final perimeter as a ghost; the stored perimeter is [lat, lng] (API frame
 * order) but was projected as [lng, lat], so MapLibre threw "Invalid LngLat latitude value"
 * during render and, with no error boundary, React unmounted the whole app.
 */
import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mockApi";
import { finalFrame, openApp, runToCompletion, setIgnitionAtMapCentre } from "./app";

function collectPageErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(`${e.message}\n${e.stack ?? ""}`));
  return errors;
}

async function openIncident(page: Page, name: string): Promise<void> {
  await page.getByRole("button", { name: /EOC Console/ }).click();
  await page.getByRole("textbox", { name: "Incident name", exact: true }).fill(name);
  await page.getByRole("button", { name: "Open EOC Console" }).click();
  await expect(page.locator(".op-period-panel")).toBeVisible();
}

test("run -> EOC -> new operational period: no crash, Day 2 renders with the Day 1 ghost perimeter", async ({ page }) => {
  const errors = collectPageErrors(page);
  await mockApi(page);
  await openApp(page);
  // Incident open before the run, so the completed run is saved into Day 1 (finalPerimeter)
  await openIncident(page, "Period check");
  await page.getByRole("navigation", { name: "Workspace" }).getByRole("button", { name: /Simulation/ }).click();
  await setIgnitionAtMapCentre(page);
  await runToCompletion(page);

  await page.getByRole("button", { name: /EOC Console/ }).click();
  const panel = page.locator(".op-period-panel");
  await expect(panel).toBeVisible();
  await expect(panel.locator(".op-period-day-btn", { hasText: "Day 1" })).toContainText("✓");

  await panel.getByRole("button", { name: "+ New Period" }).click();
  await panel.getByRole("button", { name: "Advance" }).click();

  await expect(panel.locator(".op-period-day-btn.active")).toContainText("Day 2");
  await expect(panel.locator(".op-period-info")).toContainText("Day 2");
  await expect(page.locator(".eoc-console")).toBeVisible();
  await expect(page.locator("path.eoc-ghost-perimeter")).toHaveCount(1);
  const d = await page.locator("path.eoc-ghost-perimeter").getAttribute("d");
  expect(d).toMatch(/^M [\d.-]+ [\d.-]+ L/);
  expect(d).not.toContain("NaN");

  // Back to Day 1 and forward again: switching periods must not crash either
  await panel.locator(".op-period-day-btn", { hasText: "Day 1" }).click();
  await panel.locator(".op-period-day-btn", { hasText: "Day 2" }).click();
  await expect(page.locator(".app-error-fallback")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("an incident saved on Day 2 (stored [lat, lng] ghost perimeter) opens in the EOC console", async ({ page }) => {
  const errors = collectPageErrors(page);
  const ring = finalFrame.perimeter as number[][];
  const period = (day: number, date: string, finalPerimeter: number[][] | null) => ({
    day, date, opPeriodStart: "08:00", opPeriodEnd: "20:00",
    weather: { wind_speed: 20, wind_direction: 180, temperature: 25, relative_humidity: 30, precipitation_24h: 0 },
    fwi: { ffmc: null, dmc: null, dc: null }, ignitionPoint: null, simulationId: null,
    simulationStatus: finalPerimeter ? "completed" : null, durationHours: 12,
    frameSummaries: [], finalPerimeter, annotations: [], evacuationDecisions: [], objectives: [], situationNarrative: "",
  });
  const stored = [{
    id: "e2e-day2", name: "Stored Day 2", incidentNumber: "", incidentCommanderName: "", status: "active",
    createdAt: "2026-10-10T15:00:00Z", updatedAt: "2026-10-11T15:00:00Z", activePeriodIndex: 1,
    operationalPeriods: [period(1, "2026-10-10", ring), period(2, "2026-10-11", null)],
    useEdmontonGrid: true, fuelType: "C2", enableSpotting: false, spottingIntensity: 1, includeWater: false,
    includeBuildings: true, includeWUI: true, includeDEM: true, snapshotMinutes: 30,
  }];
  await page.addInitScript((v) => localStorage.setItem("firesim-v3-incidents", v), JSON.stringify(stored));
  await mockApi(page);
  await openApp(page);
  await page.getByRole("button", { name: /EOC Console/ }).click();
  await expect(page.locator(".op-period-day-btn.active")).toContainText("Day 2");
  await expect(page.locator(".eoc-console")).toBeVisible();
  await expect(page.locator("path.eoc-ghost-perimeter")).toHaveCount(1);
  expect(errors).toEqual([]);
});
