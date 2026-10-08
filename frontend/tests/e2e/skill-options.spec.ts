/**
 * Spread-skill options (docs/validation.md, held-out results):
 * - Run options: "Burning period 10:00–20:00 (validated on Alberta fires)" on by default, the
 *   request carries it with start_time; bad hours block Run with a message; off = not sent;
 * - the timeline shades the hours outside the burning period and the Situation panel says when
 *   the selected time is outside it;
 * - evening FFMC spin-up needs hourly forecast weather; with it the request has
 *   ffmc_spin_up and hourly records back to 17:00 the evening before;
 * - RPAS perimeter: load the modelled perimeter, mark active edges by drawing on the map and
 *   by picking a side (keyboard), restart: the request has active_edges, the buffer, the
 *   observation time and the burning period; the map shows the edges;
 * - the "Click map to set ignition point" hint goes away when the ignition is typed;
 * - no serious/critical axe violations with the new controls open.
 * Screenshots go to $SHOT_DIR when set.
 */
import { mkdirSync } from "node:fs";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mockApi";
import { openApp, runButton, runToCompletion, setIgnitionAtMapCentre } from "./app";

const SHOT_DIR = process.env.SHOT_DIR;
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

async function shot(page: Page, name: string) {
  if (!SHOT_DIR) return;
  mkdirSync(SHOT_DIR, { recursive: true });
  await page.screenshot({ path: `${SHOT_DIR}/skillopts_${name}.png` });
}

async function seriousAxe(page: Page): Promise<string[]> {
  const res = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  return res.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(", ")}`);
}

async function setStart(page: Page, date: string, time: string) {
  await page.getByLabel("Date", { exact: true }).fill(date);
  await page.getByLabel(/^Time \(/).fill(time);
}

/** Open-Meteo stub: 72 h from 2026-07-14 00:00 UTC, temperature = UTC hour of day. */
async function stubForecast(page: Page) {
  await page.route(/api\.open-meteo\.com/, (route) => {
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
          time, temperature_2m: temp, relative_humidity_2m: Array(n).fill(30),
          wind_speed_10m: Array(n).fill(20), wind_direction_10m: Array(n).fill(270),
          precipitation: Array(n).fill(0),
        },
      }),
    });
  });
}

test("burning period: on by default, sent, validated, shaded on the timeline", async ({ page }) => {
  const api = await mockApi(page);
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  await setStart(page, "2026-07-15", "18:00");

  await page.locator(".setup-section-toggle", { hasText: "Run options" }).click();
  const bp = page.getByLabel(/^Burning period/);
  await expect(bp).toBeChecked();
  await expect(page.locator(".skill-options")).toContainText("Burning period 10:00–20:00 (validated on Alberta fires)");
  await expect(page.getByRole("link", { name: /Evidence: held-out validation/ })).toHaveAttribute("href", /docs\/validation\.md/);
  // Spin-up needs hourly weather: shown, but not available yet
  await expect(page.getByLabel("Evening FFMC spin-up")).toBeDisabled();
  await expect(page.locator(".skill-options")).toContainText("Needs hourly forecast weather");
  await page.locator(".skill-options").scrollIntoViewIfNeeded();
  await shot(page, "runoptions");

  // Bad hours: a field message, and Run is blocked with the reason
  await page.getByLabel("From (h)").fill("21");
  await expect(page.locator(".skill-options [role=alert]")).toHaveText("Start hour must be before the end hour");
  await expect(runButton(page)).toBeDisabled();
  await expect(page.locator("#run-bar-reason")).toContainText("Fix the burning period");
  await page.getByLabel("From (h)").fill("10");
  await expect(runButton(page)).toBeEnabled();
  expect(await seriousAxe(page)).toEqual([]);

  await runToCompletion(page);
  expect(api.posts[0].burning_period).toEqual({ start_hour: 10, end_hour: 20 });
  expect(api.posts[0].ffmc_spin_up).toBe(false);
  expect(api.posts[0].start_time).toMatch(/^2026-07-15T18:00:00-06:00$/);

  // 18:00 + 4 h: 20:00-22:00 is outside the period; the last frame (22:00) is selected
  await expect(page.locator(".ts-off-band")).toHaveCount(1);
  const band = await page.locator(".ts-off-band").evaluate((el) => (el as HTMLElement).style.left);
  // 20:00 is 2 h into the 4 h run: about half-way along the track (frames are spaced by index)
  expect(parseFloat(band)).toBeGreaterThan(45);
  expect(parseFloat(band)).toBeLessThan(60);
  await expect(page.locator(".ts-now-off")).toHaveText("No spread");
  await expect(page.locator(".situation-burning")).toContainText("Outside the burning period (10:00–20:00): no spread modelled until 10:00");
  await shot(page, "timeline_outside");
  await page.getByRole("slider", { name: "Timeline" }).focus();
  await page.keyboard.press("Home");
  await expect(page.locator(".situation-burning")).toContainText("fire spreading");
  await expect(page.locator(".ts-now-off")).toHaveCount(0);

  // Off: not sent, no shading
  await page.getByLabel(/^Burning period/).uncheck();
  await runToCompletion(page);
  expect(api.posts[1].burning_period ?? null).toBeNull();
  await expect(page.locator(".ts-off-band")).toHaveCount(0);
});

test("evening FFMC spin-up with hourly forecast: hours from 17:00 the evening before", async ({ page }) => {
  const api = await mockApi(page);
  await stubForecast(page);
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  await setStart(page, "2026-07-15", "06:00"); // 12:00 UTC; spin-up from 17:00 on the 14th
  await page.locator(".setup-section-toggle", { hasText: "Weather & FWI" }).click();
  await page.getByLabel("Use hourly forecast weather").check();
  await page.locator(".setup-section-toggle", { hasText: "Run options" }).click();
  const spin = page.getByLabel("Evening FFMC spin-up");
  await expect(spin).toBeEnabled();
  await expect(spin).toBeChecked();
  await runButton(page).click();
  await expect(page.locator(".status-badge.status-completed")).toHaveText("completed", { timeout: 30_000 });

  const post = api.posts[0];
  expect(post.ffmc_spin_up).toBe(true);
  const hourly = post.hourly_weather as Array<{ hours_from_start: number; temperature: number }>;
  expect(hourly[0]).toMatchObject({ hours_from_start: -13, temperature: 23 }); // 17:00 MDT = 23:00 UTC
  expect(hourly.filter((r) => r.hours_from_start >= 0).map((r) => r.hours_from_start)).toEqual([0, 1, 2, 3]);
});

test("RPAS perimeter: mark active edges on the map and by side, restart sends active_edges", async ({ page }) => {
  const api = await mockApi(page);
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  await setStart(page, "2026-07-15", "13:00");
  await runToCompletion(page);

  await page.locator(".setup-section-toggle", { hasText: "Observed perimeter (RPAS)" }).click();
  const panel = page.locator(".recon-panel");
  await expect(panel).toContainText("Restart the run at 17:00");
  await panel.getByRole("button", { name: /Use the modelled perimeter at 17:00/ }).click();
  const map = page.locator("[data-bounds]");
  await expect(map).toHaveAttribute("data-recon-perimeter", "1");

  await panel.getByLabel("Only marked edges active").check();
  await expect(panel.getByText("Edges not marked active are treated as burned out")).toBeVisible();
  const restart = panel.getByRole("button", { name: "Restart from observed perimeter" });
  await expect(restart).toBeDisabled(); // nothing marked yet

  // Draw a line on the map: three clicks, then Finish line
  await panel.getByRole("button", { name: "Draw active edge on map" }).click();
  await expect(page.locator(".recon-draw-hint")).toBeVisible();
  await expect(page.locator(".mcp-placement-hint", { hasText: "ignition" })).toHaveCount(0);
  const box = (await page.locator("canvas.maplibregl-canvas").first().boundingBox())!;
  for (const [dx, dy] of [[-60, -40], [0, -70], [60, -40]]) {
    await page.mouse.click(box.x + box.width / 2 + dx, box.y + box.height / 2 + dy);
  }
  await expect(map).toHaveAttribute("data-recon-draft", "3");
  await panel.getByRole("button", { name: /Finish line \(3 points\)/ }).click();
  await expect(map).toHaveAttribute("data-recon-active", "1");
  await expect(panel.getByText("Drawn edge 1 (3 points)")).toBeVisible();

  // Keyboard alternative: a whole side from the list
  const sides = panel.getByRole("group", { name: "Or mark whole sides of the perimeter" });
  const enabledSide = sides.locator("input[type=checkbox]:not([disabled])").first();
  await enabledSide.focus();
  await page.keyboard.press("Space");
  await expect(enabledSide).toBeChecked();
  await expect(map).not.toHaveAttribute("data-recon-active", "1");
  await panel.getByLabel("Buffer around active edges (m)").fill("60");
  expect(await seriousAxe(page)).toEqual([]);
  await shot(page, "rpas_edges");

  await expect(restart).toBeEnabled();
  await restart.click();
  await expect.poll(() => api.overridePosts.length).toBe(1);
  const req = api.overridePosts[0];
  const edges = req.active_edges as { type: string; coordinates: number[][][] };
  expect(edges.type).toBe("MultiLineString");
  expect(edges.coordinates.length).toBeGreaterThanOrEqual(2);
  expect(edges.coordinates[0]).toHaveLength(3);
  expect(req.active_edge_buffer_m).toBe(60);
  expect((req.perimeter_geojson as { type: string }).type).toBe("Polygon");
  expect(req.start_time).toBe("2026-07-15T17:00:00-06:00");
  expect(req.burning_period).toEqual({ start_hour: 10, end_hour: 20 });
  await expect(page.locator(".status-badge.status-completed")).toHaveText("completed", { timeout: 30_000 });
  await shot(page, "rpas_restarted");
});

test("the ignition hint goes away when the ignition is typed", async ({ page }) => {
  await mockApi(page);
  await openApp(page);
  const hint = page.locator(".mcp-placement-hint", { hasText: "Click map to set ignition point" });
  await expect(hint).toBeVisible();
  await page.getByLabel("Latitude", { exact: true }).fill("53.4606");
  await page.getByLabel("Longitude", { exact: true }).fill("-113.6597");
  await page.getByRole("button", { name: "Set ignition" }).click();
  await expect(runButton(page)).toBeEnabled();
  await expect(hint).toHaveCount(0);
  // The map control can still re-arm placement
  await page.locator(".mcp-ignite").click();
  await expect(hint).toBeVisible();
});
