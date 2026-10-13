/**
 * Saved runs (run archive phase 2, docs/run-archive.md): open the panel, enter the passphrase
 * (a test value, E2E_RUNS_PASSPHRASE or the default below; never a real one), list the
 * server's runs, click one, and the run is restored (timeline, Situation, "Loaded from
 * archive"). The run label typed in Setup goes with the next run's POST.
 */
import { expect, test, type Page, type Route } from "@playwright/test";
import { fixture, mockApi } from "./mockApi";
import { openApp, runToCompletion, setIgnitionAtMapCentre } from "./app";

const TEST_PASS = process.env.E2E_RUNS_PASSPHRASE || "e2e-test-passphrase";
const SAVED = `${fixture.simulation_id}-saved`;

function savedRun(id: string, label: string | null, createdAt: string) {
  return {
    id, label, kind: "single", status: "completed", archived: true,
    created_at: createdAt, finished_at: createdAt, start_time: fixture.config?.start_time ?? null,
    ignition_lat: 53.468, ignition_lng: -113.617, area_ha: 12.5, duration_hours: 4, frame_count: 9,
    days: null, fuel_source: "City of Edmonton fuel grid", buildings_source: null,
    ensemble: false, ensemble_status: null, ensemble_members: null,
  };
}

interface ListMock {
  listCalls: Array<{ passphrase: string | null; query: string }>;
}

/** GET /api/v1/simulations (the list), behind the test passphrase; registered after mockApi. */
async function mockList(page: Page): Promise<ListMock> {
  const state: ListMock = { listCalls: [] };
  await page.route((url) => url.pathname === "/api/v1/simulations", async (route: Route) => {
    const req = route.request();
    if (req.method() !== "GET") return route.fallback();
    const passphrase = req.headers()["x-firesim-passphrase"] ?? null;
    state.listCalls.push({ passphrase, query: new URL(req.url()).search });
    if (passphrase !== TEST_PASS) {
      return route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ detail: "Wrong passphrase" }) });
    }
    const runs = [
      savedRun(SAVED, "Terwillegar grass, west wind", "2026-07-02T21:10:00Z"),
      savedRun(`${fixture.simulation_id}-older`, null, "2026-07-01T18:00:00Z"),
    ];
    return route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ runs, total: runs.length, limit: 20, offset: 0, archive_enabled: true }),
    });
  });
  return state;
}

const section = (page: Page) => page.locator(".setup-section-toggle", { hasText: "Saved runs" });
const list = (page: Page) => page.getByRole("list", { name: "Saved runs" });

test("Saved runs: passphrase, list, click a run, run restored", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const api = await mockApi(page, { distinctRunIds: true });
  const lists = await mockList(page);
  api.archived.add(SAVED);
  await openApp(page);

  await section(page).click();
  // a wrong passphrase is refused and not kept
  await page.getByLabel("Passphrase", { exact: true }).fill("not-it");
  await page.getByRole("button", { name: "Show saved runs" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Wrong passphrase" })).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem("firesim.savedRuns.passphrase"))).toBeNull();

  await page.getByLabel("Passphrase", { exact: true }).fill(TEST_PASS);
  await page.getByRole("button", { name: "Show saved runs" }).click();
  await expect(list(page).getByRole("listitem")).toHaveCount(2);
  const first = list(page).getByRole("listitem").first();
  await expect(first).toContainText("Terwillegar grass, west wind");
  await expect(first).toContainText(/Thu, (2 Jul|Jul 2) 15:10 MDT/);
  await expect(first).toContainText("Single run · 53.468, -113.617 · 13 ha · 4 h");
  await expect(first).toContainText("City of Edmonton fuel grid");
  expect(lists.listCalls.at(-1)!.passphrase).toBe(TEST_PASS);
  // kept for this tab only
  expect(await page.evaluate(() => sessionStorage.getItem("firesim.savedRuns.passphrase"))).toBe(TEST_PASS);
  expect(await page.evaluate(() => localStorage.getItem("firesim.savedRuns.passphrase"))).toBeNull();

  // "near current map view" sends the map's box
  await page.getByLabel("Near current map view").check();
  await expect.poll(() => lists.listCalls.at(-1)!.query).toMatch(/west=.*south=.*east=.*north=/);

  // click: the run comes back from the server's archive
  await first.getByRole("button", { name: /^Terwillegar grass, west wind/ }).click();
  await expect(page.locator(".status-badge.status-completed")).toHaveText("completed", { timeout: 30_000 });
  expect(api.getIds).toContain(SAVED);
  await expect(first).toContainText("On map · Loaded from archive");
  await expect(first.getByRole("button", { name: /^Terwillegar grass/ })).toHaveAttribute("aria-current", "true");
  // timeline and Situation show the restored run
  await expect(page.getByRole("slider").first()).toBeVisible();
  await expect(page.getByText("Loaded from archive")).toBeVisible();
  expect(errors).toEqual([]);
});

test("Run label goes with the next run", async ({ page }) => {
  const api = await mockApi(page);
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  await page.getByLabel("Run label (optional)").fill("Ridge drill, day 1");
  await runToCompletion(page);
  expect(api.posts.at(-1)!.label).toBe("Ridge drill, day 1");
});
