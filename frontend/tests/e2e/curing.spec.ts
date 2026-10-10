/**
 * Grass curing (decision M1, 2026-10-10): 95 % by default from 1 Mar to 29 May (before
 * green-up); outside that window the field is required and Run waits for it.
 */
import { expect, test } from "@playwright/test";
import { mockApi } from "./mockApi";
import { openApp, runButton } from "./app";

async function setStart(page: import("@playwright/test").Page, date: string) {
  await page.getByLabel("Latitude", { exact: true }).fill("53.4606");
  await page.getByLabel("Longitude", { exact: true }).fill("-113.6597");
  await page.getByRole("button", { name: "Set ignition" }).click();
  await page.getByLabel("Date", { exact: true }).fill(date);
  await page.getByLabel(/^Time \(/).fill("14:05");
}

test("summer: curing is required before Run, and the entry is sent", async ({ page }) => {
  const api = await mockApi(page);
  await openApp(page, { curing: null });
  await setStart(page, "2026-07-15");
  await expect(runButton(page)).toBeDisabled();
  await expect(page.locator(".run-bar-reason")).toContainText("Enter grass curing");
  await page.locator(".setup-section-toggle", { hasText: "Fuel & landscape" }).click();
  const field = page.getByLabel(/^Grass curing/);
  await expect(field).toHaveValue("");
  await expect(field).toHaveAttribute("required", "");
  await expect(page.getByText("Grass curing (%) · required")).toBeVisible();
  await field.fill("70");
  await expect(runButton(page)).toBeEnabled();
  await runButton(page).click();
  await expect(page.locator(".status-badge.status-completed")).toHaveText("completed", { timeout: 30_000 });
  expect((api.posts[0].fuel_modifiers as { grass_cure: number }).grass_cure).toBe(70);
});

test("spring: 95 % is pre-filled and sent", async ({ page }) => {
  const api = await mockApi(page);
  await openApp(page, { curing: null });
  await setStart(page, "2026-04-20");
  await page.locator(".setup-section-toggle", { hasText: "Fuel & landscape" }).click();
  await expect(page.getByLabel(/^Grass curing/)).toHaveValue("95");
  await expect(page.getByText("Grass curing (%) · spring default")).toBeVisible();
  await expect(runButton(page)).toBeEnabled();
  await runButton(page).click();
  await expect(page.locator(".status-badge.status-completed")).toHaveText("completed", { timeout: 30_000 });
  const mods = api.posts[0].fuel_modifiers as { grass_cure: number; day_of_year: number };
  expect(mods.grass_cure).toBe(95);
  expect(mods.day_of_year).toBe(110);
});
