import { expect, test } from "@playwright/test";
import { DRAW_COUNTER_INIT, fixture, mockApi } from "./mockApi";
import { drawCallsWhileIdle, openApp, runToCompletion, setIgnitionAtMapCentre, waitForMapQuiet } from "./app";

// Budget: an idle map with no spot fires must not redraw (0 WebGL draw calls in 3 s).
// Draw calls are counted by wrapping WebGL drawElements/drawArrays in an init script.
test("performance budget: 0 WebGL draw calls over 3 s idle, before and after a run", async ({ page }, testInfo) => {
  expect(fixture.frames.every((f) => !f.spot_fires || (f.spot_fires as unknown[]).length === 0)).toBe(true);

  await page.addInitScript(DRAW_COUNTER_INIT);
  await mockApi(page);
  await openApp(page);

  expect(await waitForMapQuiet(page), "map never stopped redrawing after load").toBe(true);
  // Sanity: the wrapper sees the map's draws (otherwise 0 below would prove nothing)
  expect(await page.evaluate(() => window.__draws)).toBeGreaterThan(0);
  const before = await drawCallsWhileIdle(page, 3000);

  await setIgnitionAtMapCentre(page);
  await runToCompletion(page);
  expect(await waitForMapQuiet(page), "map never stopped redrawing after the run").toBe(true);
  const after = await drawCallsWhileIdle(page, 3000);

  await testInfo.attach("idle-draw-calls.json", {
    body: JSON.stringify({ before_run_3s: before, after_run_3s: after }, null, 2),
    contentType: "application/json",
  });
  console.log(`[perf] idle WebGL draw calls in 3 s: before run ${before}, after run ${after}`);
  expect(before).toBe(0);
  expect(after).toBe(0);
});
