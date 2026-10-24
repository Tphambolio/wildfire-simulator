import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BROWSER_DATA_ENTRIES, clearAll, describeInventory, inventory } from "./browserData";
import { makeIncident } from "../types/incident";

function seed() {
  const a = { ...makeIncident("Millcreek"), id: "inc-a" };
  const b = { ...makeIncident("TEST old name"), id: "inc-b", status: "closed" as const };
  localStorage.setItem("firesim-v3-incidents", JSON.stringify([a, b]));
  localStorage.setItem("firesim.setup.inc-a", "{}");
  localStorage.setItem("firesim.setup.inc-b", "{}");
  localStorage.setItem("firesim.setup.scratch", "{}");
  localStorage.setItem("firesim-v3-evac-tiers", "[]");
  localStorage.setItem("firesim-v3-layout", "{}");
  localStorage.setItem("firesim-v3-legends", "{}");
  localStorage.setItem("firesim.lastGrassCure", "60");
  localStorage.setItem("firesim-v3-scenarios", JSON.stringify([{ id: "s" }]));
  sessionStorage.setItem("firesim-v3-live-run", "{}");
  sessionStorage.setItem("firesim.savedRuns.passphrase", "secret");
  // Not FireSim's: must survive a reset
  localStorage.setItem("other-app-key", "keep");
  localStorage.setItem("firesimulator-unrelated", "keep");
  sessionStorage.setItem("other-session", "keep");
}

describe("browserData (E9, owner report 2026-10-24)", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });
  afterEach(() => vi.restoreAllMocks());

  it("lists every FireSim key the app writes, each with a label", () => {
    const keys = BROWSER_DATA_ENTRIES.map((e) => e.key ?? e.prefix);
    for (const k of [
      "firesim-v3-incidents", "firesim-v3-scenarios", "firesim.setup.", "firesim-v3-evac-tiers",
      "firesim-v3-legends", "firesim-v3-layout", "firesim.lastGrassCure", "firesim-v3-live-run",
      "firesim.savedRuns.passphrase",
    ]) expect(keys).toContain(k);
    for (const e of BROWSER_DATA_ENTRIES) expect(e.label.length).toBeGreaterThan(0);
  });

  it("inventory: incidents with names and statuses, Setup per incident, sizes", () => {
    seed();
    const inv = inventory();
    expect(inv.empty).toBe(false);
    expect(inv.blocked).toBe(false);
    expect(inv.incidents.map((i) => [i.name, i.status])).toEqual([["Millcreek", "active"], ["TEST old name", "closed"]]);
    expect(inv.setup).toEqual({ incidentIds: ["inc-a", "inc-b"], scratch: true });
    expect(inv.totalBytes).toBeGreaterThan(0);
    expect(inv.items.find((i) => i.id === "saved-runs-passphrase")?.store).toBe("session");
    const lines = describeInventory(inv);
    expect(lines[0]).toBe("2 incidents: Millcreek (active), TEST old name (closed)");
    expect(lines).toContain("Restored Setup for 2 incidents and for no incident");
    expect(lines).toContain("1 saved scenario");
    expect(lines).toContain("Evacuation statuses set without an incident");
    expect(lines).toContain("Map layout");
    expect(lines.join(" ")).not.toContain("other-app");
  });

  it("clearAll removes exactly the FireSim keys and reports them", () => {
    seed();
    const r = clearAll();
    expect(r.failed).toEqual([]);
    expect(r.blocked).toBe(false);
    expect(r.removed).toContain("local:firesim-v3-incidents");
    expect(r.removed).toContain("local:firesim.setup.scratch");
    expect(r.removed).toContain("session:firesim.savedRuns.passphrase");
    expect(r.removed).toHaveLength(11);
    expect(localStorage.length).toBe(2);
    expect(localStorage.getItem("other-app-key")).toBe("keep");
    expect(localStorage.getItem("firesimulator-unrelated")).toBe("keep");
    expect(sessionStorage.getItem("other-session")).toBe("keep");
    expect(sessionStorage.getItem("firesim-v3-live-run")).toBeNull();
    expect(inventory().empty).toBe(true);
  });

  it("tolerates blocked storage (never throws)", () => {
    seed();
    const spy = vi.spyOn(window, "localStorage", "get").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    const inv = inventory();
    expect(inv.blocked).toBe(true);
    expect(inv.incidents).toEqual([]);
    const r = clearAll();
    expect(r.blocked).toBe(true);
    // sessionStorage still cleared
    expect(r.removed).toEqual(["session:firesim-v3-live-run", "session:firesim.savedRuns.passphrase"]);
    spy.mockRestore();
    expect(localStorage.getItem("firesim-v3-incidents")).not.toBeNull();
  });

  it("reports a removal the browser refuses", () => {
    localStorage.setItem("firesim-v3-layout", "{}");
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new Error("refused");
    });
    const r = clearAll();
    expect(r.failed).toContain("local:firesim-v3-layout");
    expect(r.removed).toEqual([]);
  });
});
