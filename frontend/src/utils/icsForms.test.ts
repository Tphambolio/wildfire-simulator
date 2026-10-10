import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { buildICS203HTML } from "./icsForms";
import { frame, square } from "../test/frames";

const opts = (hfi: number) => ({
  incidentName: "Test",
  frames: [frame(1, square(0.01), { max_hfi_kw_m: hfi, head_ros_m_min: 5, area_ha: 10 })],
  runParams: null,
  ignitionPoint: { lat: 53.5, lng: -113.5 },
});

describe("ICS 203 organization list", () => {
  it("does not size the organization from the modelled intensity (no source for such a mapping)", () => {
    const low = buildICS203HTML(opts(50)); // class 2
    const extreme = buildICS203HTML(opts(15000)); // class 6
    for (const html of [low, extreme]) {
      expect(html).toContain("Operations Section Chief");
      expect(html).toContain("Planning (SITL)");
      expect(html).not.toContain("Finance / Admin Section Chief</td>");
      expect(html).not.toMatch(/scaled to Intensity Class/);
      expect(html).toContain("Add Logistics, Finance / Admin and other positions");
    }
    // Same positions whatever the class
    const rows = (h: string) => h.match(/<tr><td>[^<]+<\/td>/g);
    expect(rows(low)).toEqual(rows(extreme));
  });

  it("has no dead check on the old I-V intensity classes", () => {
    const src = readFileSync("src/utils/icsForms.ts", "utf8");
    expect(src).not.toMatch(/\["III",\s*"IV",\s*"V"\]/);
  });
});
