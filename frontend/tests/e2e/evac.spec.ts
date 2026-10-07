/**
 * Neighbourhoods and evacuation status (design spec §3.6, §6.2; Travis's decisions 2026-10-06):
 * - FireSim suggests no evacuation tiers: after a run, only modelled arrival ("Fire within 500 m
 *   by HH:MM") is shown, and no Order / Alert / Watch appears until a person sets one;
 * - setting a status by clicking a neighbourhood on the map draws the labelled outline
 *   ("ORDER · set by Planning") and lists it in the Neighbourhoods card;
 * - the status survives a reload (saved in this browser when no incident is open);
 * - setting from the arrival table and the neighbourhood picker (keyboard path); axe clean.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mockApi";
import { openApp, runToCompletion, setIgnitionAtMapCentre } from "./app";

const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

async function seriousAxe(page: Page): Promise<string[]> {
  const r = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  return r.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
}

async function drawnTiers(page: Page): Promise<Array<[string, string]>> {
  const raw = await page.locator(".map-view-canvas").first().getAttribute("data-evac-tiers");
  return raw ? JSON.parse(raw) : [];
}

test.describe("neighbourhoods and evacuation status", () => {
  test("no tiers after a run until Planning sets one; the labelled outline survives reload", async ({ page }) => {
    await mockApi(page);
    await openApp(page);
    await setIgnitionAtMapCentre(page);
    await runToCompletion(page);

    // Modelled arrival only
    const card = page.locator(".evac-status");
    await expect(card.locator(".evac-arrival-table tbody tr").first()).toBeVisible();
    await expect(card.locator(".evac-arrival-table tbody tr", { hasText: "The Uplands" })).toContainText(/\d{2}:\d{2}/);
    await expect(page.locator(".map-nbhd-label-arrival").first()).toHaveText(/^Fire within 500 m by \d{2}:\d{2}$/);
    // ...and no evacuation tier anywhere until a person sets one
    await expect(card.getByTestId("evac-none")).toBeVisible();
    expect(await drawnTiers(page)).toEqual([]);
    await expect(page.locator(".map-nbhd-label-tier")).toHaveCount(0);
    await expect(page.locator("body")).not.toContainText(/LEAVE NOW|Evacuation Trigger|Evacuation Zones/i);
    for (const sel of await card.locator("select.evac-tier-select").all()) await expect(sel).toHaveValue("");

    // Click a neighbourhood on the map (the fitted map is centred on the fire)
    const canvas = page.locator("canvas.maplibregl-canvas").first();
    const box = (await canvas.boundingBox())!;
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    const popup = page.locator(".evac-popup");
    await expect(popup).toBeVisible();
    await expect(popup).toContainText("Evacuation status (set by Planning)");
    const name = (await popup.locator(".map-popup-title").textContent())!.trim();
    expect(name.length).toBeGreaterThan(0);
    await expect(popup.getByRole("button", { name: "None" })).toHaveAttribute("aria-pressed", "true");
    await popup.getByRole("button", { name: "Order" }).click();
    await expect(popup.getByRole("button", { name: "Order" })).toHaveAttribute("aria-pressed", "true");

    // Drawn as a labelled outline and listed as set by Planning
    await expect.poll(() => drawnTiers(page)).toEqual([[name, "ORDER"]]);
    await expect(page.locator(".map-nbhd-label", { hasText: name }).locator(".map-nbhd-label-tier")).toHaveText("ORDER · set by Planning");
    await expect(page.locator(".map-nbhd-label-solid", { hasText: name })).toHaveCount(1);
    const item = card.locator(".evac-set-item", { hasText: name });
    await expect(item).toContainText("ORDER");
    await expect(card.getByTestId("evac-none")).toHaveCount(0);
    expect(await seriousAxe(page)).toEqual([]);

    // Survives a reload (no incident open: saved in this browser)
    await page.reload();
    await expect(page.locator("canvas.maplibregl-canvas").first()).toBeVisible();
    await expect(page.locator(".evac-set-item", { hasText: name })).toContainText("ORDER");
    await expect.poll(() => drawnTiers(page), { timeout: 15_000 }).toEqual([[name, "ORDER"]]);
    await expect(page.locator(".map-nbhd-label", { hasText: name }).locator(".map-nbhd-label-tier")).toHaveText("ORDER · set by Planning");
    await expect(page.locator(".evac-status")).toContainText("Saved in this browser");
    expect(await seriousAxe(page)).toEqual([]);
  });

  test("keyboard path: set from the arrival table and the neighbourhood picker, then clear", async ({ page }) => {
    await mockApi(page);
    await openApp(page);
    await setIgnitionAtMapCentre(page);
    await runToCompletion(page);
    const card = page.locator(".evac-status");

    await card.getByLabel("Evacuation status for The Uplands", { exact: true }).selectOption("Alert");
    await card.getByRole("combobox", { name: /^Neighbourhood/ }).selectOption("Edgemont");
    await card.getByRole("combobox", { name: /^Status/ }).selectOption("Watch");
    await card.getByRole("button", { name: "Set", exact: true }).click();

    await expect(card.locator(".evac-set-item")).toHaveText([/ALERT\s*The Uplands/, /WATCH\s*Edgemont/]);
    await expect.poll(() => drawnTiers(page)).toEqual([["The Uplands", "ALERT"], ["Edgemont", "WATCH"]]);
    await expect(page.locator(".map-nbhd-label-dashed", { hasText: "The Uplands" })).toHaveCount(1);
    await expect(page.locator(".map-nbhd-label-dotted", { hasText: "Edgemont" })).toHaveCount(1);

    await card.getByRole("button", { name: "Clear evacuation status for Edgemont" }).click();
    await expect(card.locator(".evac-set-item")).toHaveCount(1);
    await expect.poll(() => drawnTiers(page)).toEqual([["The Uplands", "ALERT"]]);

    // Hiding the status outlines removes the labels from the map
    await card.getByLabel("Show evacuation status on the map").uncheck();
    await expect(page.locator(".map-nbhd-label-tier")).toHaveCount(0);
    expect(await seriousAxe(page)).toEqual([]);
  });
});
