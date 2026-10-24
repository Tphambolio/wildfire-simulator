import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import SavedRunsPanel from "./SavedRunsPanel";
import { runSummary, runWhen } from "../utils/savedRuns";
import { dayRange } from "../hooks/useSavedRuns";
import { withRunLabel, type SavedRun } from "../services/savedRuns";

const TEST_PASS = "test-only-passphrase"; // not a real secret
const NET_DELAY_MS = 5;

function run(over: Partial<SavedRun> = {}): SavedRun {
  return {
    id: "abc12345", label: "West flank", kind: "single", status: "completed", archived: true,
    created_at: "2026-07-01T20:05:00Z", finished_at: "2026-07-01T20:06:00Z",
    start_time: "2026-07-01T13:00:00-06:00", ignition_lat: 53.512, ignition_lng: -113.5,
    area_ha: 42.4, duration_hours: 4, frame_count: 9, days: null,
    fuel_source: "City of Edmonton fuel grid", buildings_source: null,
    ensemble: true, ensemble_status: "completed", ensemble_members: 30, ...over,
  };
}

type Call = { url: string; init: RequestInit };
let calls: Call[];
let responder: (c: Call) => Response;

function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

beforeEach(() => {
  calls = [];
  sessionStorage.clear();
  responder = () => json({ runs: [run(), run({ id: "def67890", label: null, kind: "multiday", days: 3, ensemble: false })], total: 2, limit: 20, offset: 0, archive_enabled: true });
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const c = { url: String(url), init };
    calls.push(c);
    // A real network answer lands a macrotask (or several) later. Without this the mock answered
    // within React Testing Library's post-findBy flush on a fast machine, so tests that read rows
    // right after unlock() passed locally and failed on a loaded CI runner (G8, run 40080189720).
    await new Promise((r) => setTimeout(r, NET_DELAY_MS));
    return responder(c);
  }));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const header = (c: Call) => (c.init.headers as Record<string, string>)["X-FireSim-Passphrase"];

async function unlock() {
  fireEvent.change(screen.getByLabelText("Passphrase"), { target: { value: TEST_PASS } });
  fireEvent.click(screen.getByRole("button", { name: "Show saved runs" }));
  // The empty <ul> renders as soon as the panel unlocks, while the list request is still in
  // flight ("Loading…"); wait for the rows themselves, not just the list.
  const list = await screen.findByRole("list", { name: "Saved runs" });
  await within(list).findAllByRole("listitem");
  return list;
}

describe("SavedRunsPanel", () => {
  it("asks for the passphrase once, keeps it in sessionStorage and sends it as the header", async () => {
    render(<SavedRunsPanel onOpen={() => {}} />);
    expect(calls).toHaveLength(0);
    await unlock();
    expect(header(calls[0])).toBe(TEST_PASS);
    expect(calls[0].url).toContain("/api/v1/simulations?limit=20&offset=0");
    expect(sessionStorage.getItem("firesim.savedRuns.passphrase")).toBe(TEST_PASS);
    expect(localStorage.getItem("firesim.savedRuns.passphrase")).toBeNull();
    // a second mount reuses it: no prompt
    render(<SavedRunsPanel onOpen={() => {}} />);
    await waitFor(() => expect(screen.getAllByRole("list", { name: "Saved runs" })).toHaveLength(2));
  });

  it("lists runs with time (Edmonton), label, location, kind, area, duration and fuel", async () => {
    render(<SavedRunsPanel onOpen={() => {}} />);
    await unlock();
    const items = within(screen.getByRole("list", { name: "Saved runs" })).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("West flank");
    expect(items[0]).toHaveTextContent(/Wed, (1 Jul|Jul 1) 14:05 MDT/);
    expect(items[0]).toHaveTextContent("Single run · 53.512, -113.500 · 42 ha · 4 h");
    expect(items[0]).toHaveTextContent("City of Edmonton fuel grid");
    expect(items[0]).toHaveTextContent("Ensemble");
    expect(items[1]).toHaveTextContent("Run def67890");
    expect(items[1]).toHaveTextContent("Multi-day · 53.512, -113.500 · 42 ha · 3 days");
    expect(screen.getByText("1–2 of 2")).toBeInTheDocument();
  });

  it("opens a run on click", async () => {
    const onOpen = vi.fn();
    render(<SavedRunsPanel onOpen={onOpen} currentRunId="def67890" />);
    await unlock();
    fireEvent.click(screen.getByRole("button", { name: /^West flank/ }));
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ id: "abc12345", ensemble_members: 30 }));
    expect(screen.getByRole("button", { name: /^Run def67890/ })).toHaveAttribute("aria-current", "true");
  });

  it("clears the passphrase on 401 and shows the server's message", async () => {
    responder = () => json({ detail: "Wrong passphrase" }, 401);
    render(<SavedRunsPanel onOpen={() => {}} />);
    fireEvent.change(screen.getByLabelText("Passphrase"), { target: { value: "nope" } });
    fireEvent.click(screen.getByRole("button", { name: "Show saved runs" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Wrong passphrase");
    expect(sessionStorage.getItem("firesim.savedRuns.passphrase")).toBeNull();
    expect(screen.getByLabelText("Passphrase")).toBeInTheDocument();
  });

  it("clears it on 403 (list disabled on the server)", async () => {
    sessionStorage.setItem("firesim.savedRuns.passphrase", TEST_PASS);
    responder = () => json({ detail: "The saved-runs list is disabled on this server (FIRESIM_RUNS_PASSPHRASE is not set)." }, 403);
    render(<SavedRunsPanel onOpen={() => {}} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("disabled on this server");
    expect(sessionStorage.getItem("firesim.savedRuns.passphrase")).toBeNull();
  });

  it("keeps working when sessionStorage throws", async () => {
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    render(<SavedRunsPanel onOpen={() => {}} />);
    await unlock();
    expect(header(calls[0])).toBe(TEST_PASS);
    spy.mockRestore();
  });

  it("filters by the current map view and a date range", async () => {
    const canvas = document.createElement("div");
    canvas.className = "map-view-canvas";
    canvas.setAttribute("data-bounds", JSON.stringify([[-113.7, 53.4], [-113.3, 53.7]]));
    document.body.appendChild(canvas);
    render(<SavedRunsPanel onOpen={() => {}} />);
    await unlock();
    fireEvent.click(screen.getByLabelText("Near current map view"));
    await waitFor(() => expect(calls.at(-1)!.url).toContain("west=-113.7&south=53.4&east=-113.3&north=53.7"));
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-07-01" } });
    fireEvent.change(screen.getByLabelText("To"), { target: { value: "2026-07-02" } });
    await waitFor(() => {
      const u = new URL(calls.at(-1)!.url, "http://x");
      expect(u.searchParams.get("since")).toBe("2026-07-01T06:00:00.000Z");
      expect(u.searchParams.get("until")).toBe("2026-07-03T06:00:00.000Z");
    });
    canvas.remove();
  });

  it("renames with PATCH and deletes after a confirm", async () => {
    render(<SavedRunsPanel onOpen={() => {}} />);
    await unlock();
    responder = (c) => (c.init.method === "PATCH" ? json({ id: "abc12345", label: "Renamed" }) : json({ id: "abc12345", deleted: true }));
    fireEvent.click(screen.getByRole("button", { name: "Rename West flank" }));
    fireEvent.change(screen.getByLabelText("Label"), { target: { value: "Renamed" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByRole("button", { name: /^Renamed/ });
    const patch = calls.find((c) => c.init.method === "PATCH")!;
    expect(patch.url).toMatch(/\/api\/v1\/simulations\/abc12345$/);
    expect(JSON.parse(String(patch.init.body))).toEqual({ label: "Renamed" });
    expect(header(patch)).toBe(TEST_PASS);

    const confirm = vi.fn(() => false);
    vi.stubGlobal("confirm", confirm);
    fireEvent.click(screen.getByRole("button", { name: "Delete Renamed" }));
    expect(confirm).toHaveBeenCalled();
    expect(calls.some((c) => c.init.method === "DELETE")).toBe(false);
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "Delete Renamed" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: /^Renamed/ })).toBeNull());
    expect(calls.some((c) => c.init.method === "DELETE")).toBe(true);
  });
});

describe("saved-runs helpers", () => {
  it("formats times in America/Edmonton and summaries without addresses", () => {
    expect(runWhen("2026-01-15T18:30:00Z")).toMatch(/^Thu, (15 Jan|Jan 15) 11:30 MST$/);
    expect(runWhen(null)).toBe("—");
    expect(runSummary(run({ kind: "perimeter_override", area_ha: 3.25, duration_hours: 2.5 })))
      .toBe("RPAS restart · 53.512, -113.500 · 3.3 ha · 2.5 h");
  });

  it("day range is inclusive, in Edmonton local days", () => {
    expect(dayRange("", "")).toEqual({ since: null, until: null });
    expect(dayRange("2026-01-10", "2026-01-10")).toEqual({
      since: "2026-01-10T07:00:00.000Z", until: "2026-01-11T07:00:00.000Z",
    });
  });

  it("adds the run label only when set", () => {
    expect(withRunLabel({ a: 1 }, "  ")).toEqual({ a: 1 });
    expect(withRunLabel({ a: 1 }, " Day 2 ")).toEqual({ a: 1, label: "Day 2" });
    expect((withRunLabel({}, "x".repeat(90)) as { label: string }).label).toHaveLength(80);
  });
});
