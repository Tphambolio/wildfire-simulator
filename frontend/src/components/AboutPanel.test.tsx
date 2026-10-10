import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";

vi.mock("../services/api", () => ({
  getVersion: vi.fn(async () => ({ version: "3.0.0", git_sha: "abc1234" })),
}));

import AboutPanel, { ABOUT_SECTIONS } from "./AboutPanel";
import { TIPS } from "../content/explanations";

describe("AboutPanel (About & sources tab)", () => {
  it("has the eight sections, in order, with a table of contents", () => {
    render(<AboutPanel />);
    const headings = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(headings).toEqual([
      "1. What FireSim is for",
      "2. Limits and validation",
      "3. Methods and references",
      "4. Data sources and licences",
      "5. Responsible use",
      "6. ICS Canada forms",
      "7. Using the app",
      "8. Version",
    ]);
    const toc = screen.getByRole("navigation", { name: "About sections" });
    expect(within(toc).getAllByRole("link")).toHaveLength(ABOUT_SECTIONS.length);
  });

  it("shows the model version from the API", async () => {
    render(<AboutPanel />);
    await waitFor(() => expect(screen.getByTestId("about-version")).toHaveTextContent("3.0.0 · abc1234"));
  });

  it("carries the moved explanations and the key references", () => {
    render(<AboutPanel />);
    const text = document.body.textContent ?? "";
    expect(text).toContain("FireSim never recommends Order, Alert or Watch");
    expect(text).toContain(TIPS.rangeTooNarrow);
    expect(text).toContain("143 Alberta fire-days");
    for (const ref of ["ST-X-3", "Van Wagner & Pickett (1985)", "Albini (1979", "Alexander (2010)", "Lawson, Armitage & Hoskins (1996)", "Cohen (2004)", "Cole & Alexander (1995)"]) {
      expect(text).toContain(ref);
    }
    expect(text).toContain("346,238");
    expect(text).toContain("20 m cells");
    expect(text).toContain("no documented source");
    expect(text).toContain("fire authority's authorization");
    expect(text).toContain("Zotero");
  });

  it("links the references and docs on GitHub, never local paths or SFOC numbers", () => {
    render(<AboutPanel />);
    const hrefs = screen.getAllByRole("link").map((a) => a.getAttribute("href") ?? "");
    expect(hrefs.some((h) => h.endsWith("docs/PROJECT_RECORD.md#42-references"))).toBe(true);
    expect(hrefs.some((h) => h.includes("docs/validation.md"))).toBe(true);
    expect(hrefs.some((h) => h.includes("docs/data-sources.md"))).toBe(true);
    for (const h of hrefs.filter((x) => !x.startsWith("#"))) expect(h).toMatch(/^https:\/\/github\.com\/Tphambolio\/wildfire-simulator/);
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/~\/|\/home\/|SFOC[- ]?\d|ATS-\d/);
  });

  it("opens at the requested section (the low-skill badge opens Limits)", () => {
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    render(<AboutPanel section="about-limits" />);
    expect(scroll).toHaveBeenCalled();
    expect(document.activeElement?.id).toBe("about-limits");
  });
});
