/**
 * ICS Canada 209-WF from the Situation panel after a run with an ensemble: the report opens in
 * a new window, is the ICS Canada form, is stamped with the run ID and the model version from
 * /api/v1/version, gives P50/P10 areas at clock times, and leaves observed blocks 9 and 28 empty.
 */
import { expect, test } from "@playwright/test";
import { fixture, mockApi } from "./mockApi";
import { openApp, runToCompletion, setIgnitionAtMapCentre } from "./app";

test("ICS 209-WF: ICS Canada layout, run stamp, ensemble projections, observed blocks empty", async ({ page }) => {
  await mockApi(page, { ensemble: true });
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  await runToCompletion(page);
  await expect(page.getByTestId("ensemble-p10-area")).toBeVisible({ timeout: 20_000 });

  const button = page.locator("#eoc-summary").getByRole("button", { name: "ICS 209-WF" });
  await button.scrollIntoViewIfNeeded();
  const [popup] = await Promise.all([page.waitForEvent("popup"), button.click()]);
  await expect(popup.locator("h1")).toHaveText("INCIDENT STATUS SUMMARY (ICS 209)", { timeout: 10_000 });
  const text = await popup.locator("body").innerText();
  if (process.env.SHOT_DIR) await popup.screenshot({ path: `${process.env.SHOT_DIR}/ics209wf.png`, fullPage: true });

  expect(text).toContain("ICS Canada Form 209-WF");
  expect(text).not.toMatch(/NIMS/);
  expect(text).toContain(fixture.simulation_id);
  expect(text).toContain("e2e0sha");
  expect(text).toContain("Area P10 (worst-credible)");
  expect(text).toMatch(/12 hours\s+\S.*\d{2}:\d{2}/);
  // A 4 h run: the end of the modelled period is reported, the 12-72 h horizons are not projected
  expect(text).toContain("End of modelled period (T+4:00)");
  expect(text).toContain("Beyond the modelled period");
  expect(await popup.locator(".block:has-text('9. Status') input:checked").count()).toBe(0);
  const b28 = popup.locator(".block:has-text('Observed Fire Behaviour')");
  await expect(b28.locator(".entry")).toHaveText("");
  await expect(b28).not.toContainText("MODEL OUTPUT");
});
