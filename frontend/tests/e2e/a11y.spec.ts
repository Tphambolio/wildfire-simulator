/**
 * axe-core accessibility check of the main shell (WCAG 2.0/2.1/2.2 A and AA rules).
 *
 * The app does not pass yet (design spec §7, PR 2 and PR 14 fix it), so the serious and
 * critical violations found today are recorded in axe-baseline.json and the test fails only on
 * NEW ones: a rule that is not in the baseline, or more nodes for a baseline rule than recorded.
 * When a PR fixes violations, lower the baseline by re-recording it:
 *
 *     UPDATE_AXE_BASELINE=1 npx playwright test a11y
 *
 * and commit tests/e2e/axe-baseline.json (the diff shows what was fixed).
 */
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mockApi";
import { openApp, runToCompletion, setIgnitionAtMapCentre } from "./app";

const BASELINE_PATH = fileURLToPath(new URL("./axe-baseline.json", import.meta.url));
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];
const GATED_IMPACTS = new Set(["serious", "critical"]);

interface RuleEntry {
  impact: string;
  nodes: number;
  help: string;
  targets: string[];
}
type Baseline = Record<string, Record<string, RuleEntry>>;

const readBaseline = (): Baseline => (existsSync(BASELINE_PATH) ? JSON.parse(readFileSync(BASELINE_PATH, "utf8")) : {});

async function scan(page: Page): Promise<Record<string, RuleEntry>> {
  const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  const out: Record<string, RuleEntry> = {};
  for (const v of results.violations) {
    if (!v.impact || !GATED_IMPACTS.has(v.impact)) continue;
    out[v.id] = {
      impact: v.impact,
      nodes: v.nodes.length,
      help: v.help,
      targets: v.nodes.map((n) => n.target.join(" ")).slice(0, 20),
    };
  }
  return out;
}

function compare(screen: string, found: Record<string, RuleEntry>) {
  const base = readBaseline()[screen] ?? {};
  const regressions: string[] = [];
  for (const [id, entry] of Object.entries(found)) {
    const b = base[id];
    if (!b) regressions.push(`new ${entry.impact} rule "${id}" (${entry.nodes} nodes): ${entry.help} -> ${entry.targets.slice(0, 5).join(", ")}`);
    else if (entry.nodes > b.nodes) regressions.push(`"${id}": ${entry.nodes} nodes, baseline ${b.nodes}: ${entry.help}`);
  }
  const fixed = Object.keys(base).filter((id) => !found[id] || found[id].nodes < base[id].nodes);
  return { regressions, fixed };
}

async function check(page: Page, screen: string, testInfo: import("@playwright/test").TestInfo) {
  const found = await scan(page);
  await testInfo.attach(`axe-${screen}.json`, { body: JSON.stringify(found, null, 2), contentType: "application/json" });
  const total = Object.values(found).reduce((s, e) => s + e.nodes, 0);
  console.log(`[axe] ${screen}: ${Object.keys(found).length} serious/critical rules, ${total} nodes`);
  if (process.env.UPDATE_AXE_BASELINE) {
    const all = readBaseline();
    all[screen] = found;
    writeFileSync(BASELINE_PATH, JSON.stringify(all, null, 2) + "\n");
    return;
  }
  const { regressions, fixed } = compare(screen, found);
  if (fixed.length) console.log(`[axe] ${screen}: improved vs baseline (${fixed.join(", ")}); re-record with UPDATE_AXE_BASELINE=1`);
  expect(regressions, `new serious/critical axe violations on "${screen}"`).toEqual([]);
}

test.describe("accessibility (axe)", () => {
  test("main shell has no new serious/critical violations", async ({ page }, testInfo) => {
    await mockApi(page);
    await openApp(page);
    await check(page, "shell", testInfo);
  });

  test("shell after a completed run has no new serious/critical violations", async ({ page }, testInfo) => {
    await mockApi(page);
    await openApp(page);
    await setIgnitionAtMapCentre(page);
    await runToCompletion(page);
    await check(page, "completed", testInfo);
  });

  test("range of outcomes (ensemble, burn probability on) has no new serious/critical violations", async ({ page }, testInfo) => {
    await mockApi(page, { ensemble: true });
    await openApp(page);
    await setIgnitionAtMapCentre(page);
    await runToCompletion(page);
    await expect(page.getByTestId("ensemble-p10-area")).toBeVisible({ timeout: 20_000 });
    await page.getByTestId("ensemble-card").getByLabel("Burn probability").check();
    await page.getByTestId("ensemble-card").getByLabel(/P90/).check();
    await check(page, "ensemble", testInfo);
  });
});
