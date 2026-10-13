/**
 * Owner field tests 2026-10-12 (Slave Lake SWF-065):
 * - Setup → Weather & FWI: weather changes during the run go in the request as
 *   `weather_schedule` (hours after the start); the header chips are labelled as run values;
 * - the RPAS restart sends Setup's current weather and FWI codes, with the later weather
 *   changes counted from the restart time, and names the source run of the loaded perimeter,
 *   which Clear results removes;
 * - a scenario dated before the fuel grid's year (2025) shows the vintage warning in Setup.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mockApi";
import { openApp, runToCompletion, setIgnitionAtMapCentre } from "./app";

async function setStart(page: Page, date: string, time: string) {
  await page.getByLabel("Date", { exact: true }).fill(date);
  await page.getByLabel(/^Time \(/).fill(time);
}

test("weather changes are sent as a schedule, and the restart uses Setup's weather", async ({ page }) => {
  const api = await mockApi(page);
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  await setStart(page, "2026-07-15", "13:00");
  await page.locator(".setup-section-toggle", { hasText: "Run options" }).click();
  await page.getByLabel(/^Simulation:/).fill("6");

  await page.locator(".setup-section-toggle", { hasText: "Weather & FWI" }).click();
  const sched = page.getByTestId("weather-schedule");
  await sched.getByRole("button", { name: "Add a weather change" }).click();
  await sched.getByLabel("Change 1: from (local time)").fill("18:00");
  await sched.getByLabel("Change 1: Wind (km/h)").fill("5");
  await expect(page.locator(".setup-section-toggle", { hasText: "Weather & FWI" })).toContainText("1 weather change");
  const axe = await new AxeBuilder({ page }).include("[data-testid=weather-schedule]").analyze();
  expect(axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual([]);

  await runToCompletion(page);
  const post = api.posts[api.posts.length - 1];
  expect(post.weather_schedule).toEqual([
    expect.objectContaining({ hours_from_start: 5, wind_speed: 5, wind_direction: 270 }),
  ]);
  await expect(page.getByTestId("run-chips")).toContainText("Run wind");

  // Change Setup's wind after the run: the chips keep the run's value
  await page.getByLabel(/^Wind Speed/).fill("33");
  await expect(page.getByTestId("run-chips")).not.toContainText("33");

  await page.locator(".setup-section-toggle", { hasText: "Observed perimeter (RPAS)" }).click();
  const panel = page.locator(".recon-panel");
  await panel.getByRole("button", { name: /Use the modelled perimeter at 17:00/ }).click();
  await expect(panel.locator(".recon-loaded")).toContainText("of run fixture-terwillegar-4h");
  await expect(panel.getByTestId("recon-weather")).toContainText("33 km/h");
  await expect(panel.getByTestId("recon-weather")).toContainText("1 later change");
  await panel.getByRole("button", { name: /Restart at .* from observed/ }).click();
  await expect.poll(() => api.overridePosts.length).toBe(1);
  const req = api.overridePosts[0];
  expect((req.weather as { wind_speed: number }).wind_speed).toBe(33);
  expect(req.fwi_overrides).toMatchObject({ ffmc: 90 });
  expect(req.weather_schedule).toEqual([expect.objectContaining({ hours_from_start: 1, wind_speed: 5 })]);
  expect(req.duration_hours).toBe(4);
  await expect(page.locator(".status-badge.status-completed")).toHaveText("completed", { timeout: 30_000 });
  // The chips show the restart's weather in force at the timeline's time: the 18:00 step
  // (5 km/h) at the end, Setup's 33 km/h at the restart time
  await expect(page.getByTestId("run-chips")).toContainText("Run wind now5 km/h");
  await page.getByLabel("Timeline").fill("0");
  await expect(page.getByTestId("run-chips")).toContainText("Run wind now33 km/h");

  // Clear results removes the loaded perimeter too (bug 9)
  await page.getByRole("button", { name: "Clear results" }).click();
  await expect(panel.locator(".recon-loaded")).toHaveCount(0);
});

test("a scenario dated before the fuel grid's year warns in Setup", async ({ page }) => {
  await mockApi(page);
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  await setStart(page, "2011-05-14", "17:50");
  await expect(page.getByTestId("fuel-vintage-start")).toContainText("Before the fuel grid's year (2025)");
  await page.locator(".setup-section-toggle", { hasText: "Fuel & landscape" }).click();
  await expect(page.getByTestId("fuel-vintage-setup")).toContainText(
    "Fuel grid represents 2025. Areas burned since 2011 may be mapped as grass or non-fuel");
  await setStart(page, "2026-05-14", "17:50");
  await expect(page.getByTestId("fuel-vintage-setup")).toHaveCount(0);
});
