/**
 * Layout metrics. The design spec (§7, WCAG 1.4.3 note) targets no visible text below 12 px;
 * the app does not meet that yet (PR 2 / PR 14), so this test REPORTS the count (console and
 * an attached JSON) and does not fail on it. Turn it into an assertion once PR 14 lands.
 */
import { test } from "@playwright/test";
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

test.describe("layout metrics (reported, not enforced)", () => {
  test("visible text below 12 px", async ({ page }, testInfo) => {
    await mockApi(page);
    await openApp(page);
    const shell = await smallText(page);
    await setIgnitionAtMapCentre(page);
    await runToCompletion(page);
    const completed = await smallText(page);
    await testInfo.attach("small-text.json", {
      body: JSON.stringify({ shell, completed }, null, 2),
      contentType: "application/json",
    });
    for (const [name, r] of Object.entries({ shell, completed })) {
      console.log(`[layout] ${name}: ${r.below12px} of ${r.textNodes} visible text nodes are below 12 px`);
      testInfo.annotations.push({ type: `small-text-${name}`, description: `${r.below12px}/${r.textNodes} below 12 px` });
    }
  });
});
