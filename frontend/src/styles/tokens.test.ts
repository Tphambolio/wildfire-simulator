// @vitest-environment node
/// <reference types="node" />
/**
 * Contrast of the design-token pairs (WCAG 2.2: 1.4.3 text >= 4.5:1, 1.4.11 UI >= 3:1),
 * read straight from tokens.css for the dark default and the light (print/export) set.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { contrastRatio } from "../utils/contrast";
import { HFI_CLASSES } from "../utils/fireClasses";

const css = readFileSync(fileURLToPath(new URL("./tokens.css", import.meta.url)), "utf8");
/** Hex custom properties declared in the first block whose selector matches `selector`. */
function tokens(selector: RegExp): Record<string, string> {
  const m = css.match(new RegExp(selector.source + String.raw`\s*\{([^}]*)\}`));
  if (!m) throw new Error(`no block ${selector}`);
  const out: Record<string, string> = {};
  for (const [, name, value] of m[1].matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{3,6})\b/g)) out[name] = value;
  return out;
}

const dark = tokens(/:root/);
const light = { ...dark, ...tokens(/\.theme-light/) };

const SURFACES = ["bg", "surface", "surface-2", "surface-3"];
const TEXT = ["text", "text-2", "text-3", "accent-text", "danger-text", "warning-text", "success-text"];
const UI = ["border-strong", "focus"]; // control borders and the focus ring (non-text, 3:1)

describe.each([
  ["dark (default)", dark],
  ["light (print/export)", light],
])("%s tokens", (_name, t) => {
  for (const fg of TEXT) {
    for (const bg of SURFACES) {
      it(`--${fg} on --${bg} >= 4.5:1`, () => {
        expect(contrastRatio(t[fg], t[bg])).toBeGreaterThanOrEqual(4.5);
      });
    }
  }
  for (const fg of UI) {
    for (const bg of SURFACES) {
      it(`--${fg} on --${bg} >= 3:1`, () => {
        expect(contrastRatio(t[fg], t[bg])).toBeGreaterThanOrEqual(3);
      });
    }
  }
  it("--on-accent on --accent >= 4.5:1", () => {
    expect(contrastRatio(t["on-accent"], t.accent)).toBeGreaterThanOrEqual(4.5);
  });
  it("--on-danger on --danger >= 4.5:1", () => {
    expect(contrastRatio(t["on-danger"], t.danger)).toBeGreaterThanOrEqual(4.5);
  });
  it("chrome text on --chrome >= 4.5:1", () => {
    expect(contrastRatio(t["chrome-text"], t.chrome)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(t["chrome-text-2"], t.chrome)).toBeGreaterThanOrEqual(4.5);
  });
});

describe("map colour tokens", () => {
  it("fire classes match utils/fireClasses.ts", () => {
    for (const c of HFI_CLASSES) expect(dark[`fire-${c.num}`]?.toLowerCase()).toBe(c.color.toLowerCase());
  });

  it("HFI chip text meets 4.5:1 on every class colour", () => {
    for (const c of HFI_CLASSES) expect(contrastRatio(c.textColor, c.color)).toBeGreaterThanOrEqual(4.5);
  });
});
