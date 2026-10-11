import { describe, expect, it, vi } from "vitest";
import { ghostPerimeterPath, type Project } from "./ghostPerimeter";
import { fixture } from "../test/fixture";

/** Behaves like maplibregl.Map.project: LngLat throws on an out-of-range latitude. */
const project: Project = ([lng, lat]) => {
  if (lat > 90 || lat < -90) throw new Error("Invalid LngLat latitude value: must be between -90 and 90");
  return { x: (lng + 114) * 1000, y: (54 - lat) * 1000 };
};

// The stored Day 1 finalPerimeter is the last frame's perimeter: [lat, lng] pairs
const finalPerimeter = fixture.frames[fixture.frames.length - 1].perimeter as [number, number][];

describe("ghostPerimeterPath (EOC Day 2+ ghost of the previous period)", () => {
  it("the stored perimeter is [lat, lng]; reading it as [lng, lat] throws like the 2026-10-11 crash", () => {
    const [lat, lng] = finalPerimeter[0];
    expect(lat).toBeGreaterThan(53);
    expect(lng).toBeLessThan(-113);
    // The old EOCConsole code: ghostPerimeter.map(([lng, lat]) => project([lng, lat]))
    expect(() => finalPerimeter.map(([a, b]) => project([a, b]))).toThrow(/Invalid LngLat latitude/);
  });

  it("projects the recorded final perimeter as [lat, lng] into a closed path", () => {
    const d = ghostPerimeterPath(finalPerimeter, project);
    expect(d).toMatch(/^M -?[\d.]+ -?[\d.]+ L /);
    expect(d.endsWith(" Z")).toBe(true);
    expect(d).not.toContain("NaN");
    expect(d.match(/[ML] /g)).toHaveLength(finalPerimeter.length);
  });

  it("skips invalid points and returns '' for nothing drawable, never throws", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(ghostPerimeterPath(null, project)).toBe("");
    expect(ghostPerimeterPath([], project)).toBe("");
    expect(ghostPerimeterPath([[53.5, -113.5], [53.6, -113.5]], project)).toBe("");
    const mixed = [[53.5, -113.5], [Number.NaN, 1], [200, -113], [53.6, -113.4], null, [53.6, -113.6], [53.5]];
    expect(ghostPerimeterPath(mixed, project).match(/[ML] /g)).toHaveLength(3);
    // A projection error drops the ghost instead of crashing the render
    const boom: Project = () => { throw new Error("map not ready"); };
    expect(ghostPerimeterPath(finalPerimeter, boom)).toBe("");
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });
});
