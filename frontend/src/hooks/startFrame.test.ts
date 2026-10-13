import { describe, expect, it } from "vitest";
import { withStartFrame } from "./useSimulation";
import { frame, square } from "../test/frames";

describe("start frame (owner bug 11: two 17:50 / T+0:00 frames)", () => {
  it("adds no synthetic T=0 frame when the run's own first frame is at T+0", () => {
    const frames = [frame(0, square(0.001)), frame(0.5, square(0.002))];
    expect(withStartFrame(frames)).toHaveLength(2);
  });
  it("adds one when the first frame is later", () => {
    const out = withStartFrame([frame(0.5, square(0.002))]);
    expect(out).toHaveLength(2);
    expect(out[0].time_hours).toBe(0);
    expect(out[0].area_ha).toBe(0);
  });
  it("leaves an empty list alone", () => {
    expect(withStartFrame([])).toEqual([]);
  });
});
