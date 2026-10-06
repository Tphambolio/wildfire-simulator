import { describe, expect, it } from "vitest";
import { withAllCells } from "./useSimulation";
import type { SimulationFrame } from "../types/simulation";
import { fixture } from "../test/fixture";

const rebuild = (frames: SimulationFrame[]) => {
  const out: SimulationFrame[] = [];
  for (const f of frames) out.push(withAllCells(f, out[out.length - 1]));
  return out;
};

describe("withAllCells on the recorded incremental fixture", () => {
  const full = rebuild(fixture.frames);

  it("was recorded in incremental mode", () => {
    expect(fixture.frames.slice(1).some((f) => (f.cells_offset ?? 0) > 0)).toBe(true);
  });

  it("rebuilds cumulative cell lists in which each frame extends the previous one", () => {
    for (let i = 1; i < full.length; i++) {
      const prev = full[i - 1].burned_cells!;
      const cur = full[i].burned_cells!;
      expect(cur.length).toBe((fixture.frames[i].cells_offset ?? 0) + fixture.frames[i].burned_cells!.length);
      expect(cur.slice(0, prev.length)).toEqual(prev);
    }
  });

  it("ends with every burned cell, arrival times within the run", () => {
    const cells = full[full.length - 1].burned_cells!;
    const sent = fixture.frames.reduce((n, f) => n + (f.burned_cells?.length ?? 0), 0);
    expect(cells.length).toBe(sent);
    expect(Math.max(...cells.map((c) => c.t ?? 0))).toBeLessThanOrEqual(4 * 60);
  });

  it("leaves cumulative frames unchanged", () => {
    const f = { ...fixture.frames[0], cells_offset: 0 };
    expect(withAllCells(f, undefined)).toBe(f);
  });
});
