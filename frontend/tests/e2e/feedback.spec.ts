/**
 * Feedback dialog and usage counting (docs/privacy.md). The e2e build sets no telemetry
 * provider, so: the dialog opens from the top bar on every tab, a rating says it was not
 * counted, the GitHub issue link carries the tab, rating and note and nothing about the run,
 * no request ever leaves for a counter or the Cloudflare beacon, and axe finds no serious or
 * critical violations in the dialog.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { mockApi } from "./mockApi";
import { finalFrame, openApp, runToCompletion, setIgnitionAtMapCentre } from "./app";

test("feedback dialog: rating, prefilled issue without run data, no counting requests, axe clean", async ({ page }) => {
  const counted: string[] = [];
  page.on("request", (r) => {
    if (/cloudflareinsights|sendBeacon/.test(r.url()) || r.resourceType() === "beacon" || r.resourceType() === "ping") counted.push(r.url());
  });
  await mockApi(page);
  await openApp(page);
  await setIgnitionAtMapCentre(page);
  await runToCompletion(page);

  await page.getByRole("navigation", { name: "Workspace" }).getByRole("button", { name: /EOC Console/ }).click();
  await page.getByTestId("feedback-badge").click();
  const dialog = page.getByRole("dialog", { name: "Was this useful today?" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "1 of 5" })).toBeFocused();

  await dialog.getByRole("button", { name: "4 of 5" }).click();
  await expect(dialog.getByRole("button", { name: "4 of 5" })).toHaveAttribute("aria-pressed", "true");
  await expect(dialog.getByRole("status")).toContainText("open an issue");
  await dialog.getByTestId("feedback-text").fill("Please add a 24x exercise rate");
  await expect(dialog.getByTestId("feedback-email")).toHaveCount(0);

  const href = (await dialog.getByTestId("feedback-issue").getAttribute("href"))!;
  const url = new URL(href);
  expect(url.origin + url.pathname).toBe("https://github.com/Tphambolio/wildfire-simulator/issues/new");
  const body = url.searchParams.get("body")!;
  expect(body).toContain("Tab:** eoc");
  expect(body).toContain("4/5");
  expect(body).toContain("Please add a 24x exercise rate");
  // Nothing about the run: no ignition or perimeter coordinates, no run ID
  const ring = finalFrame.perimeter as number[][];
  expect(body).not.toContain(String(ring[0][0]).slice(0, 7));
  expect(body).not.toContain("53.46");
  expect(body).not.toContain("-113.6");

  const results = await new AxeBuilder({ page })
    .include('[data-testid="feedback-dialog"]')
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  const gated = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(gated.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);

  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(page.getByTestId("feedback-badge")).toBeFocused();
  expect(counted).toEqual([]);
});
