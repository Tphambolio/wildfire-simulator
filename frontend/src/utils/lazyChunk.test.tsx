import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Suspense } from "react";
import { STALE_BUILD_MESSAGE, importWithRetry, lazyChunk } from "./lazyChunk";

describe("lazyChunk: tabs after a redeploy", () => {
  it("retries a failed import once", async () => {
    let n = 0;
    const v = await importWithRetry(async () => {
      n += 1;
      if (n === 1) throw new TypeError("Failed to fetch dynamically imported module");
      return 42;
    }, 1, 1);
    expect(v).toBe(42);
    expect(n).toBe(2);
  });

  it("shows a reload notice instead of crashing when the chunk is gone", async () => {
    const Missing = lazyChunk<Record<string, never>>(() =>
      Promise.reject(new TypeError("Failed to fetch dynamically imported module")),
    );
    render(
      <Suspense fallback={<p>loading</p>}>
        <Missing />
      </Suspense>,
    );
    expect(await screen.findByText(STALE_BUILD_MESSAGE, {}, { timeout: 3000 })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reload" })).toBeTruthy();
  });
});
