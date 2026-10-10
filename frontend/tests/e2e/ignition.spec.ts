/**
 * Ignition and scenario start (design spec §2.3, §4.1, §7 2.1.1; §9 PR 4):
 * - keyboard only: Tab to the map, arrow keys move the crosshair, Enter sets the ignition,
 *   Ctrl+Enter runs; the POST carries the ignition and start_time;
 * - typed coordinates (DMS) and a typed start date/time: start_time has the Edmonton offset, the
 *   timeline and Situation panel count from it;
 * - a pasted coordinate pair fills both fields; bad input gets a field-level message;
 * - the hourly forecast is sliced from the scenario start, not from now.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mockApi";
import { openApp, runButton } from "./app";

const ISO_WITH_OFFSET = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00[-+]\d{2}:\d{2}$/;

async function tabToMap(page: Page): Promise<void> {
  for (let i = 0; i < 200; i++) {
    await page.keyboard.press("Tab");
    const onMap = await page.evaluate(() => document.activeElement?.classList.contains("maplibregl-canvas") ?? false);
    if (onMap) return;
  }
  throw new Error("Tab never reached the map canvas");
}

async function crosshairCoords(page: Page): Promise<[number, number]> {
  const text = (await page.locator(".kb-crosshair-label").textContent()) ?? "";
  const [lat, lng] = text.split(",").map((s) => Number(s.trim()));
  return [lat, lng];
}

/** The America/Edmonton UTC offset ("-06:00") the browser's tz data gives for an ISO instant. */
async function browserOffset(page: Page, iso: string): Promise<string> {
  return page.evaluate((s) => {
    const name = new Intl.DateTimeFormat("en-CA", { timeZone: "America/Edmonton", timeZoneName: "longOffset" })
      .formatToParts(new Date(s))
      .find((p) => p.type === "timeZoneName")!.value; // "GMT-06:00"
    return name.replace("GMT", "");
  }, iso);
}

test.describe("ignition and start time", () => {
  test("keyboard only: Tab to the map, arrows, Enter sets the ignition, Ctrl+Enter runs", async ({ page }) => {
    const api = await mockApi(page);
    await openApp(page);
    await expect(runButton(page)).toBeDisabled();

    await tabToMap(page);
    await expect(page.locator(".kb-crosshair")).toBeVisible();
    await expect(page.locator(".kb-hint")).toContainText("Enter sets the ignition");
    // The crosshair overlay adds no serious/critical accessibility violations
    const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
    expect(axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id)).toEqual([]);
    const [lat0, lng0] = await crosshairCoords(page);

    // Right 3 x 10 px, down 1 x 50 px (Shift)
    for (let i = 0; i < 3; i++) await page.keyboard.press("ArrowRight");
    await page.keyboard.press("Shift+ArrowDown");
    const [lat1, lng1] = await crosshairCoords(page);
    expect(lng1).toBeGreaterThan(lng0);
    expect(lat1).toBeLessThan(lat0);

    await page.keyboard.press("Enter");
    await expect(page.getByRole("status").filter({ hasText: "Ignition set" })).toHaveText(
      `Ignition set ${lat1.toFixed(4)}, ${lng1.toFixed(4)}`,
    );
    await expect(runButton(page)).toBeEnabled();
    // The Setup fields follow the map
    await expect(page.getByLabel("Latitude", { exact: true })).toHaveValue(lat1.toFixed(4));
    await expect(page.getByLabel("Longitude", { exact: true })).toHaveValue(lng1.toFixed(4));
    // Ctrl+Enter while the map still has focus
    await expect(page.locator("canvas.maplibregl-canvas")).toBeFocused();
    const before = Date.now();
    await page.keyboard.press("Control+Enter");
    await expect(page.locator(".status-badge.status-completed")).toHaveText("completed", { timeout: 30_000 });

    expect(api.posts).toHaveLength(1);
    const body = api.posts[0];
    expect(body.ignition_lat as number).toBeCloseTo(lat1, 3);
    expect(body.ignition_lng as number).toBeCloseTo(lng1, 3);
    // Start "now" (to the minute), with the offset the tz data gives for that instant
    const start = body.start_time as string;
    expect(start).toMatch(ISO_WITH_OFFSET);
    expect(Math.abs(Date.parse(start) - before)).toBeLessThan(90_000);
    expect(start.slice(19)).toBe(await browserOffset(page, start));
  });

  test("typed DMS coordinates and start date/time: start_time sent, timeline counts from it", async ({ page }) => {
    const api = await mockApi(page);
    await openApp(page);

    const lat = page.getByLabel("Latitude", { exact: true });
    const lng = page.getByLabel("Longitude", { exact: true });
    // Field-level validation
    await lat.fill("95");
    await lng.fill("-113.66");
    await lat.press("Enter");
    await expect(page.locator(".input-error")).toContainText("Latitude must be between -90 and 90");
    await expect(runButton(page)).toBeDisabled();
    await lat.fill("53.46");
    await lng.fill("113.66"); // forgot the W
    await page.getByRole("button", { name: "Set ignition" }).click();
    await expect(page.locator(".input-error")).toContainText("west is negative");

    await lat.fill(`53°27'38"N`);
    await lng.fill(`113°39'35"W`);
    await lng.press("Enter");
    await expect(page.locator(".input-error")).toHaveCount(0);
    await expect(runButton(page)).toBeEnabled();
    await expect(lat).toHaveValue("53.4606");
    await expect(lng).toHaveValue("-113.6597");

    // Scenario start: 15 Jul 2026 14:05 in Edmonton (MDT, UTC-6 under every tz rule)
    await page.getByLabel("Date", { exact: true }).fill("2026-07-15");
    await page.getByLabel(/^Time \(/).fill("14:05");
    await expect(page.getByRole("button", { name: "Now" })).toHaveAttribute("aria-pressed", "false");
    const summary = page.locator(".setup-section-toggle", { hasText: "Ignition & time" });
    await expect(summary).toContainText(/53\.4606, -113\.6597 · Wed, (Jul 15|15 Jul) 14:05 (MDT|GMT-6)/);
    await expect(page.locator(".run-bar-reason")).toContainText("from 14:05");

    await runButton(page).click();
    await expect(page.locator(".status-badge.status-completed")).toHaveText("completed", { timeout: 30_000 });
    const body = api.posts[0];
    expect(body.start_time).toBe("2026-07-15T14:05:00-06:00");
    expect(body.ignition_lat as number).toBeCloseTo(53 + 27 / 60 + 38 / 3600, 6);
    expect(body.ignition_lng as number).toBeCloseTo(-(113 + 39 / 60 + 35 / 3600), 6);
    expect((body.fuel_modifiers as { day_of_year: number }).day_of_year).toBe(196);

    // The 4 h fixture ends at 18:05; the timeline starts at 14:05
    await expect(page.locator(".situation-title")).toHaveText(/^At 18:05 /);
    await expect(page.locator(".ts-tick-clock").first()).toHaveText("14:05");

    // "Now" returns to following the clock
    await page.getByRole("button", { name: "Now" }).click();
    await expect(page.getByRole("button", { name: "Now" })).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator(".run-bar-reason")).toContainText("from now");
  });

  test("a pasted coordinate pair fills both fields and sets the ignition", async ({ page }) => {
    await mockApi(page);
    await openApp(page);
    const lat = page.getByLabel("Latitude", { exact: true });
    await lat.focus();
    await lat.evaluate((el) => {
      const dt = new DataTransfer();
      dt.setData("text/plain", `53°27'38"N 113°39'35"W`);
      el.dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
    });
    await expect(lat).toHaveValue("53.4606");
    await expect(page.getByLabel("Longitude", { exact: true })).toHaveValue("-113.6597");
    await expect(runButton(page)).toBeEnabled();
  });

  test("hourly forecast is taken from the scenario start, not from now", async ({ page }) => {
    const api = await mockApi(page);
    // Open-Meteo: 3 days of hours around the start, temperature = UTC hour of day
    const forecastUrls: string[] = [];
    await page.route(/api\.open-meteo\.com/, (route) => {
      forecastUrls.push(route.request().url());
      const t0 = Date.UTC(2026, 6, 14, 0);
      const time: string[] = [];
      const temp: number[] = [];
      for (let i = 0; i < 72; i++) {
        const d = new Date(t0 + i * 3_600_000);
        time.push(d.toISOString().slice(0, 16));
        temp.push(d.getUTCHours());
      }
      const n = time.length;
      return route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          hourly: {
            time,
            temperature_2m: temp,
            relative_humidity_2m: Array(n).fill(30),
            wind_speed_10m: Array(n).fill(20),
            wind_direction_10m: Array(n).fill(270),
            precipitation: Array(n).fill(0),
          },
        }),
      });
    });
    await openApp(page);
    await page.getByLabel("Latitude", { exact: true }).fill("53.4606");
    await page.getByLabel("Longitude", { exact: true }).fill("-113.6597");
    await page.getByRole("button", { name: "Set ignition" }).click();
    await page.getByLabel("Date", { exact: true }).fill("2026-07-15");
    await page.getByLabel(/^Time \(/).fill("14:05"); // 20:05 UTC
    await page.locator(".setup-section-toggle", { hasText: "Weather & FWI" }).click();
    await page.getByLabel("Use hourly forecast weather").check();
    // Without the evening FFMC spin-up (on by default; skill-options.spec covers it), the
    // stream starts at the scenario start
    await page.locator(".setup-section-toggle", { hasText: "Run options" }).click();
    await page.getByLabel("Evening FFMC spin-up", { exact: true }).uncheck();
    await runButton(page).click();
    await expect(page.locator(".status-badge.status-completed")).toHaveText("completed", { timeout: 30_000 });

    expect(forecastUrls).toHaveLength(1);
    const hourly = api.posts[0].hourly_weather as Array<{ hours_from_start: number; temperature: number }>;
    // The hour containing 20:05 UTC applies from 0, then 21:00 UTC from 55 min, ... to 4 h
    expect(hourly[0]).toMatchObject({ hours_from_start: 0, temperature: 20 });
    expect(hourly[1].temperature).toBe(21);
    expect(hourly[1].hours_from_start).toBeCloseTo(55 / 60, 6);
    expect(hourly).toHaveLength(5);
  });
});
