import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

// A plain function (not vi.fn): Vitest's spy would attach its own settled-result handler to a
// rejected promise and report the rejection as unhandled.
let scorecardImpl: () => Promise<unknown> = () => Promise.resolve(null);
vi.mock("../services/api", () => ({ getScorecard: () => scorecardImpl() }));

import ValidationScorecardDialog, { FRAMING, formatCi } from "./ValidationScorecard";
import TopBar from "./TopBar";
import type { Scorecard, ScorecardMetric } from "../types/scorecard";

const src = { report: "[R45]", kind: "file" as const, file: "~/dev/wildfire/reports/data/x/results.json", path: "a.b", sha256: "0".repeat(64) };

const f1: ScorecardMetric = {
  id: "heldout_f1_17h", group: "spread", label: "Held-out one-day F1 (operational set-up)", status: "partly_validated",
  kind: "validation", value: 0.2457, display: "0.246", unit: "F1 (0-1)", ci95: null,
  ci95_derived: { lo: 0.2136, hi: 0.2793, method: "whole-fire bootstrap", file: "per_fireday.csv" },
  reference: { label: "W.I.S.E.", value: [0.26, 0.54], note: "level with W.I.S.E. defaults, short of tuned W.I.S.E." },
  gloss: "Front position skill vs observed fires: level with W.I.S.E. defaults, short of tuned W.I.S.E.", source: src,
};
const cov: ScorecardMetric = {
  id: "ensemble_coverage_p10_p90", group: "ensemble", label: "Ensemble coverage", status: "partly_validated", kind: "validation",
  value: 0.4923, display: "49 %", unit: "share of fire-days", ci95: [0.3803, 0.597],
  reference: { label: "calibrated ensemble", value: 0.8, note: "about 80 % for a P10-P90 range" },
  gloss: "The modelled range is too narrow.", source: { ...src, report: "[R49]" },
};
const embers: ScorecardMetric = {
  id: "long_range_embers", group: "embers", label: "Long-range embers", status: "failed_test", kind: "validation",
  value: 0, display: "v4-v7 all FAILED their pre-registered tests; v8 design pending owner review", unit: "tests passed", ci95: null,
  reference: { label: "pass rule", value: null, note: "none met" },
  gloss: "Every version failed its held-out test.", source: { ...src, report: "[R48]" },
  versions: [
    { version: "v7", test: "PREREG_LR7", verdict: "FAIL", detail: "embers vs calibrated front +0.31 h", passed: false, source: { ...src, report: "[R48]" } },
    { version: "v8", test: "design", verdict: "pending", detail: "owner review", passed: null, source: { report: "[R54]", kind: "report text", file: "docs" } },
  ],
};
const fbp: ScorecardMetric = {
  id: "fbp_cffdrs_conformance", group: "equations", label: "FBP vs cffdrs", status: "validated", kind: "verification",
  value: 3e-10, display: "relative difference ≤ 3e-10", unit: "relative difference", ci95: null,
  reference: { label: "cffdrs", value: 0, note: "float-precision match" }, gloss: "Equations match the reference.",
  source: { report: "verification", kind: "doc", file: "docs/verification.md" },
};

const card: Scorecard = {
  schema_version: 1, title: "FireSim validation scorecard", date: "2026-10-25", git_commit: "0020dfc5abcdef", engine_version: "3.0.0",
  report: "[R69]", framing: FRAMING,
  fuel_grid: { spread_and_ensemble: "back-cast", scope: "historical Alberta replays only" }, data: {},
  metrics: [fbp, f1, cov, embers], unsourced_from_files: [], file: "2026-10-25.json", available_dates: ["2026-10-25"],
};

beforeEach(() => { scorecardImpl = () => Promise.resolve(null); });

describe("ValidationScorecardDialog", () => {
  it("lists the metrics with value, CI, gloss, status and the date + commit", async () => {
    scorecardImpl = () => Promise.resolve(card);
    render(<ValidationScorecardDialog onClose={() => {}} />);
    expect(screen.getByRole("dialog", { name: "Validation scorecard" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("vsc-meta")).toHaveTextContent("Scorecard 2026-10-25 · commit 0020dfc · engine 3.0.0 · report [R69]"));
    expect(screen.getByTestId("vsc-meta")).toHaveTextContent("historical Alberta replays only");

    const row = screen.getByTestId("vsc-heldout_f1_17h");
    expect(row).toHaveTextContent("Front position skill vs observed fires (held-out F1)");
    expect(row).toHaveTextContent("0.246");
    expect(row).toHaveTextContent("95 % CI 0.214 to 0.279 (derived)");
    expect(row).toHaveTextContent("level with W.I.S.E. defaults, short of tuned W.I.S.E.");
    expect(row).toHaveTextContent("Partly validated");
    expect(row).toHaveTextContent("[R45]");

    const c = screen.getByTestId("vsc-ensemble_coverage_p10_p90");
    expect(c).toHaveTextContent("49 %");
    expect(c).toHaveTextContent("95 % CI 38 to 60 %");
    expect(c).not.toHaveTextContent("derived");
    expect(c).toHaveTextContent("about 80 % for a P10-P90 range");

    const e = screen.getByTestId("vsc-long_range_embers");
    expect(e).toHaveTextContent("Failed test");
    expect(e).toHaveTextContent("v7 FAIL: embers vs calibrated front +0.31 h");
    expect(e).toHaveTextContent("v8 pending");
    expect(screen.getByTestId("vsc-fbp_cffdrs_conformance")).toHaveTextContent("Validated");
  });

  it("always carries the preparedness/training framing and links the docs on GitHub", async () => {
    scorecardImpl = () => Promise.resolve(card);
    render(<ValidationScorecardDialog onClose={() => {}} />);
    const text = document.body.textContent ?? "";
    expect(text).toContain("preparedness and training tool");
    expect(text).toContain("not for real-time operational prediction");
    const hrefs = screen.getAllByRole("link").map((a) => a.getAttribute("href") ?? "");
    expect(hrefs.some((h) => h.endsWith("docs/validation-scorecard.md"))).toBe(true);
    for (const h of hrefs) expect(h).toMatch(/^https:\/\/github\.com\/Tphambolio\/wildfire-simulator/);
    expect(text).not.toMatch(/\/home\/|SFOC[- ]?\d|ATS-\d/);
    await waitFor(() => expect(screen.getByTestId("vsc-meta")).toBeInTheDocument());
  });

  it("falls back to plain text when the API does not answer", async () => {
    scorecardImpl = () => Promise.reject(new Error("offline"));
    render(<ValidationScorecardDialog onClose={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("vsc-offline")).toBeInTheDocument());
    expect(screen.getByTestId("vsc-offline")).toHaveTextContent("could not be loaded");
    expect(screen.getByRole("link", { name: "docs/scorecard" })).toHaveAttribute("href", expect.stringContaining("docs/scorecard/README.md"));
    expect(document.body.textContent).toContain("not for real-time operational prediction");
    expect(screen.queryByTestId("vsc-meta")).toBeNull();
  });

  it("closes on Escape and on the Close button, and focuses Close on open", async () => {
    scorecardImpl = () => Promise.resolve(card);
    const onClose = vi.fn();
    render(<ValidationScorecardDialog onClose={onClose} />);
    expect(screen.getByRole("button", { name: "Close" })).toHaveFocus();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(screen.getByTestId("vsc-meta")).toBeInTheDocument());
  });
});

describe("formatCi", () => {
  it("formats shares as percentages and levels in the display's precision", () => {
    expect(formatCi(cov)).toBe("95 % CI 38 to 60 %");
    expect(formatCi(f1)).toBe("95 % CI 0.214 to 0.279 (derived)");
    expect(formatCi({ ...cov, id: "x", display: "1.34", unit: "ratio", ci95: [1.1274, 1.5947] })).toBe("95 % CI 1.13 to 1.59");
    expect(formatCi(embers)).toBeNull();
  });
});

describe("TopBar Validation entry", () => {
  it("shows a Validation button that opens the scorecard", () => {
    const onOpenValidation = vi.fn();
    render(<TopBar incidentName={null} incidentSub="x" activeTab="simulation" onTabChange={() => {}} status={null} onOpenValidation={onOpenValidation} />);
    const btn = screen.getByTestId("validation-badge");
    expect(btn).toHaveTextContent("Validation");
    expect(btn).toHaveAttribute("aria-haspopup", "dialog");
    fireEvent.click(btn);
    expect(onOpenValidation).toHaveBeenCalledTimes(1);
  });

  it("is absent without a handler", () => {
    render(<TopBar incidentName={null} incidentSub="x" activeTab="simulation" onTabChange={() => {}} status={null} />);
    expect(screen.queryByTestId("validation-badge")).toBeNull();
  });
});
