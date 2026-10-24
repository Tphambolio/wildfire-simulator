/**
 * Browser storage of incidents and saved scenarios (QA 2026-10-11): they persist across a
 * reload and can be deleted; corrupted or older-version saved data loads without crashing (no
 * error boundary); a refused write (storage full) is reported instead of losing the incident
 * silently; and what one incident with a 3-day run costs in localStorage is measured.
 */
import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mockApi";
import { openApp, runToCompletion, setIgnitionAtMapCentre } from "./app";

const INCIDENTS = "firesim-v3-incidents";
const SCENARIOS = "firesim-v3-scenarios";

function pageErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  return errors;
}

async function openStorageSection(page: Page): Promise<void> {
  await page.locator(".setup-section-toggle", { hasText: "Incidents & saved scenarios" }).click();
}

test("incident and scenario survive a reload; both can be deleted", async ({ page }) => {
  const errors = pageErrors(page);
  page.on("dialog", (d) => d.accept());
  await mockApi(page);
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  await openStorageSection(page);
  await page.getByLabel("New incident name").fill("Persist check");
  await page.getByRole("button", { name: "Start Incident" }).click();
  await page.locator(".scenario-panel .panel-collapse-btn").click();
  await page.getByLabel("Scenario name").fill("Saved config");
  await page.getByRole("button", { name: "Save Scenario" }).click();
  await runToCompletion(page);

  await page.reload();
  await expect(page.locator("canvas.maplibregl-canvas").first()).toBeVisible();
  await expect(page.locator(".setup-section-toggle", { hasText: "Incidents & saved scenarios" })).toContainText("1 incident · 1 scenario");
  await page.getByRole("button", { name: /EOC Console/ }).click();
  await expect(page.locator(".op-period-panel")).toContainText("Persist check");
  await expect(page.locator(".op-period-day-btn", { hasText: "Day 1" })).toContainText("✓");

  await page.getByRole("navigation", { name: "Workspace" }).getByRole("button", { name: /Simulation/ }).click();
  await openStorageSection(page);
  await page.locator(".incident-panel").getByRole("button", { name: "Delete" }).click();
  await page.locator(".scenario-panel .panel-collapse-btn").click();
  await page.locator(".scenario-panel").getByRole("button", { name: "Delete" }).click();
  await expect(page.locator(".setup-section-toggle", { hasText: "Incidents & saved scenarios" })).toContainText("0 incidents · 0 scenarios");
  expect(await page.evaluate(([a, b]) => [localStorage.getItem(a), localStorage.getItem(b)], [INCIDENTS, SCENARIOS])).toEqual(["[]", "[]"]);
  expect(errors).toEqual([]);
});

const CORRUPT: Array<[string, string, string]> = [
  ["not an array", JSON.stringify({ incidents: "oops" }), JSON.stringify({ x: 1 })],
  ["truncated JSON", '[{"id":"x","name":"Trunc', '[{"id":'],
  [
    "older version (no periods, no multi-day days)",
    JSON.stringify([{ id: "old-1", name: "Old incident", status: "active" }, { name: "no id" }, null]),
    JSON.stringify([{ id: "s-old", name: "Old scenario", weather: { wind_speed: 15, wind_direction: 200, temperature: 20, relative_humidity: 40, precipitation_24h: 0 } }]),
  ],
];

for (const [label, incidents, scenarios] of CORRUPT) {
  test(`saved data ${label}: the app and the EOC console open without the error boundary`, async ({ page }) => {
    const errors = pageErrors(page);
    await page.addInitScript(
      ([a, b, ia, sb]) => {
        if (!sessionStorage.getItem("seeded")) {
          localStorage.setItem(a, ia);
          localStorage.setItem(b, sb);
          sessionStorage.setItem("seeded", "1");
        }
      },
      [INCIDENTS, SCENARIOS, incidents, scenarios],
    );
    await mockApi(page);
    await openApp(page);
    await page.getByRole("button", { name: /EOC Console/ }).click();
    if (label.startsWith("older")) {
      await expect(page.locator(".op-period-panel")).toContainText("Old incident");
      await expect(page.locator(".op-period-day-btn")).toHaveCount(1);
      await page.getByRole("button", { name: "+ New Period" }).click();
      await page.getByRole("button", { name: "Advance" }).click();
      await expect(page.locator(".op-period-day-btn.active")).toContainText("Day 2");
      // The old scenario loads (missing fields get Setup defaults) and Multi-day still has a day
      await page.getByRole("navigation", { name: "Workspace" }).getByRole("button", { name: /Simulation/ }).click();
      await openStorageSection(page);
      await page.locator(".scenario-panel .panel-collapse-btn").click();
      await page.locator(".scenario-panel").getByRole("button", { name: "Load" }).click();
      await page.getByRole("button", { name: /Run options/ }).click();
      await page.getByRole("button", { name: "Multi-day", exact: true }).click();
      await expect(page.locator(".md-day")).toHaveCount(1);
    } else {
      // Unreadable: start clean, and keep the raw value aside for recovery
      await expect(page.getByRole("textbox", { name: "Incident name", exact: true })).toBeVisible();
      expect(await page.evaluate((k) => localStorage.getItem(`${k}.unreadable`), INCIDENTS)).toBe(incidents);
    }
    await expect(page.locator(".app-error-fallback")).toHaveCount(0);
    expect(errors).toEqual([]);
  });
}

test("storage full: the incident is kept for the session and the console says it is not saved", async ({ page }) => {
  const errors = pageErrors(page);
  await page.addInitScript((key) => {
    const orig = Storage.prototype.setItem;
    Storage.prototype.setItem = function (k: string, v: string) {
      if (k === key) throw new DOMException("The quota has been exceeded.", "QuotaExceededError");
      return orig.call(this, k, v);
    };
  }, INCIDENTS);
  await mockApi(page);
  await openApp(page);
  await page.getByRole("button", { name: /EOC Console/ }).click();
  await page.getByRole("textbox", { name: "Incident name", exact: true }).fill("Quota check");
  await page.getByRole("button", { name: "Open EOC Console" }).click();
  await expect(page.locator(".op-period-panel")).toContainText("Quota check");
  await expect(page.locator(".op-period-panel").getByRole("alert")).toContainText("Not saved");
  expect(errors).toEqual([]);
});

test("size: one incident with a completed 3-day run in localStorage", async ({ page }, info) => {
  await mockApi(page, { multidayRecorded: true });
  await openApp(page);
  await page.getByRole("button", { name: /EOC Console/ }).click();
  await page.getByRole("textbox", { name: "Incident name", exact: true }).fill("Size check");
  await page.getByRole("button", { name: "Open EOC Console" }).click();
  await page.getByRole("navigation", { name: "Workspace" }).getByRole("button", { name: /Simulation/ }).click();
  await setIgnitionAtMapCentre(page);
  await page.getByRole("button", { name: /Run options/ }).click();
  await page.getByRole("button", { name: "Multi-day", exact: true }).click();
  await page.locator("button.btn-primary", { hasText: "Run 72h Scenario" }).click();
  await expect(page.locator(".status-badge.status-completed")).toHaveText("completed", { timeout: 30_000 });
  await expect.poll(() => page.evaluate((k) => (localStorage.getItem(k) ?? "").includes("fixture-multiday"), INCIDENTS)).toBe(true);
  const bytes = await page.evaluate((k) => (localStorage.getItem(k) ?? "").length, INCIDENTS);
  info.annotations.push({ type: "incident-bytes", description: String(bytes) });
  console.log(`incident with a 3-day run: ${bytes} characters in localStorage`);
  // Summaries and the final perimeter only (no burned cells): far below a 5 MB origin quota
  expect(bytes).toBeLessThan(200_000);
});

// Round-3 field test R8 (Claude in Chrome tester, 2026-10-13): after a reload the ignition,
// start time and grass curing were gone; the incident's setup now comes back
test("the incident's setup (ignition, start, curing) survives a reload", async ({ page }) => {
  const errors = pageErrors(page);
  await mockApi(page);
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  await openStorageSection(page);
  await page.getByLabel("New incident name").fill("Setup check");
  await page.getByRole("button", { name: "Start Incident" }).click();
  await page.getByLabel("Date").fill("2026-10-13");
  await page.getByLabel(/^Time/).fill("04:57");
  const lat = await page.getByLabel("Latitude").inputValue();
  expect(lat).not.toBe("");
  await page.locator(".setup-section-toggle", { hasText: "Fuel & landscape" }).click();
  await page.getByLabel(/^Grass curing/).fill("80");
  // the start is outside the 10-20 h burning period: said before the run (R3)
  await expect(page.getByTestId("start-outside-burning")).toContainText("No spread until 10:00");
  await page.waitForTimeout(700); // debounced save

  await page.reload();
  await expect(page.locator("canvas.maplibregl-canvas").first()).toBeVisible();
  await expect(page.getByLabel("Latitude")).toHaveValue(lat);
  await expect(page.getByLabel("Date")).toHaveValue("2026-10-13");
  await expect(page.getByLabel(/^Time/)).toHaveValue("04:57");
  await page.locator(".setup-section-toggle", { hasText: "Fuel & landscape" }).click();
  await expect(page.getByLabel(/^Grass curing/)).toHaveValue("80");
  expect(errors).toEqual([]);
});

test("Reset this browser's FireSim data: inventory, export, clear and reload (E9, 2026-10-24)", async ({ page }) => {
  const errors = pageErrors(page);
  await mockApi(page);
  await openApp(page);
  await openStorageSection(page);
  await page.getByLabel("New incident name").fill("Old reappearing name");
  await page.getByRole("button", { name: "Start Incident" }).click();
  await page.evaluate(() => localStorage.setItem("not-firesim", "keep"));

  // The link in the Incidents section opens About at the reset section
  await page.locator(".incident-storage-note").getByRole("button", { name: "Reset this browser's FireSim data" }).click();
  await expect(page.locator("#about-browser-data")).toBeVisible();
  await page.getByTestId("browser-data-reset").click();
  const dialog = page.getByRole("dialog", { name: "Reset this browser's FireSim data" });
  await expect(dialog.getByTestId("bdr-inventory")).toContainText("1 incident: Old reappearing name (active)");
  await expect(dialog).toContainText("Runs kept on the server");
  const download = page.waitForEvent("download");
  await dialog.getByRole("button", { name: "Export incident Old reappearing name as JSON" }).click();
  expect((await download).suggestedFilename()).toBe("firesim-incident-old-reappearing-name.json");

  await dialog.getByRole("button", { name: "Clear and reload" }).click();
  await page.waitForLoadState("load");
  await expect(page.locator("canvas.maplibregl-canvas").first()).toBeVisible();
  await expect(page.locator(".setup-section-toggle", { hasText: "Incidents & saved scenarios" })).toContainText("0 incidents");
  // A hard reload keeps it gone; unrelated keys survive
  await page.reload();
  await expect(page.locator(".setup-section-toggle", { hasText: "Incidents & saved scenarios" })).toContainText("0 incidents");
  const left = await page.evaluate(() => ({
    incidents: localStorage.getItem("firesim-v3-incidents"),
    other: localStorage.getItem("not-firesim"),
  }));
  expect(left.other).toBe("keep");
  expect(left.incidents === null || left.incidents === "[]").toBe(true);
  expect(errors).toEqual([]);
});
