/**
 * Layout metrics (design spec §6.3, §7).
 * On the main screen, before and after a run (enforced since PR 2):
 * - no visible text below 12 px;
 * - no visible interactive target (button, link, input, select, slider) below 24 x 24 px.
 * The counts and examples are logged and attached as small-text.json.
 */
import { expect, test } from "@playwright/test";
import { mockApi } from "./mockApi";
import { openApp, runToCompletion, setIgnitionAtMapCentre } from "./app";

interface SmallTextReport {
  textNodes: number;
  below12px: number;
  bySize: Record<string, number>;
  examples: Array<{ px: number; text: string; selector: string }>;
}

async function smallText(page: import("@playwright/test").Page): Promise<SmallTextReport> {
  return page.evaluate(() => {
    const report: SmallTextReport = { textNodes: 0, below12px: 0, bySize: {}, examples: [] };
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const seen = new Set<Element>();
    const selector = (el: Element) => {
      const parts: string[] = [];
      for (let e: Element | null = el; e && parts.length < 3; e = e.parentElement) {
        const cls = typeof e.className === "string" && e.className.trim() ? "." + e.className.trim().split(/\s+/).join(".") : "";
        parts.unshift(e.tagName.toLowerCase() + cls);
      }
      return parts.join(" > ");
    };
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const el = n.parentElement;
      if (!el || !n.textContent?.trim() || seen.has(el)) continue;
      const cs = getComputedStyle(el);
      if (cs.visibility === "hidden" || cs.display === "none" || Number(cs.opacity) === 0) continue;
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      if (r.bottom < 0 || r.right < 0 || r.top > innerHeight || r.left > innerWidth) continue;
      seen.add(el);
      const px = parseFloat(cs.fontSize);
      report.textNodes++;
      report.bySize[px + "px"] = (report.bySize[px + "px"] ?? 0) + 1;
      if (px < 12) {
        report.below12px++;
        if (report.examples.length < 25) report.examples.push({ px, text: n.textContent.trim().slice(0, 40), selector: selector(el) });
      }
    }
    return report;
  });
}

interface SmallTargetReport {
  targets: number;
  below24px: number;
  examples: Array<{ w: number; h: number; selector: string }>;
}

async function smallTargets(page: import("@playwright/test").Page): Promise<SmallTargetReport> {
  return page.evaluate(() => {
    const out: SmallTargetReport = { targets: 0, below24px: 0, examples: [] };
    const els = document.querySelectorAll<HTMLElement>(
      "button, a[href], input:not([type=hidden]), select, textarea, [role=button], [role=slider], [tabindex]:not([tabindex='-1'])",
    );
    for (const el of els) {
      const cs = getComputedStyle(el);
      if (cs.visibility === "hidden" || cs.display === "none") continue;
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      if (r.bottom < 0 || r.right < 0 || r.top > innerHeight || r.left > innerWidth) continue;
      out.targets++;
      if (r.width < 24 || r.height < 24) {
        out.below24px++;
        if (out.examples.length < 25) {
          const cls = typeof el.className === "string" && el.className.trim() ? "." + el.className.trim().split(/\s+/).join(".") : "";
          out.examples.push({ w: Math.round(r.width), h: Math.round(r.height), selector: el.tagName.toLowerCase() + cls });
        }
      }
    }
    return out;
  });
}

test.describe("layout metrics", () => {
  test("no visible text below 12 px and no target below 24 px", async ({ page }, testInfo) => {
    await mockApi(page);
    await openApp(page);
    const shell = await smallText(page);
    const shellTargets = await smallTargets(page);
    await setIgnitionAtMapCentre(page);
    await runToCompletion(page);
    const completed = await smallText(page);
    const completedTargets = await smallTargets(page);
    await testInfo.attach("small-text.json", {
      body: JSON.stringify({ shell, completed, shellTargets, completedTargets }, null, 2),
      contentType: "application/json",
    });
    for (const [name, r] of Object.entries({ shell, completed })) {
      console.log(`[layout] ${name}: ${r.below12px} of ${r.textNodes} visible text nodes are below 12 px`);
      testInfo.annotations.push({ type: `small-text-${name}`, description: `${r.below12px}/${r.textNodes} below 12 px` });
    }
    for (const [name, r] of Object.entries({ shell: shellTargets, completed: completedTargets })) {
      console.log(`[layout] ${name}: ${r.below24px} of ${r.targets} visible targets are below 24 px`, JSON.stringify(r.examples.slice(0, 8)));
    }
    expect(shell.examples, "visible text below 12 px before a run").toEqual([]);
    expect(completed.examples, "visible text below 12 px after a run").toEqual([]);
    expect(shellTargets.examples, "targets below 24 px before a run").toEqual([]);
    expect(completedTargets.examples, "targets below 24 px after a run").toEqual([]);
  });
});
