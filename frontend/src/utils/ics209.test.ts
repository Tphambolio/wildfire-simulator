import { describe, expect, it } from "vitest";
import type { SimulationFrame } from "../types/simulation";
import type { EnsembleGrids } from "./ensemble";
import { buildICS209HTML, frameAt, latLngToUtm, projectAreas, structuresBy } from "./ics209";

function frame(h: number, area: number, extra: Partial<SimulationFrame> = {}): SimulationFrame {
  return {
    time_hours: h,
    perimeter: [],
    area_ha: area,
    head_ros_m_min: 5,
    max_hfi_kw_m: 1500,
    fire_type: "surface",
    flame_length_m: 2,
    fuel_breakdown: { O1a: 0.75, C2: 0.25 },
    spot_fires: null,
    ...extra,
  } as SimulationFrame;
}

const exposure = (inside: number, w100: number) => ({
  inside_perimeter: inside, within_10m: 0, within_30m: 0, within_100m: w100, within_500m: w100,
  flux_over_12_5: 0, flux_over_25: 0, ftp_reached: 0,
});

/** 1 x 4 ensemble, 1 ha cells: P50 reaches cells at 60/600/1500/-1 min, P10 earlier. */
function ensemble(durationMinutes: number): EnsembleGrids {
  return {
    rows: 1, cols: 4, latMin: 53.5, latMax: 53.51, lngMin: -113.5, lngMax: -113.46,
    durationMinutes,
    p50: Int16Array.from([60, 600, 1500, -1]),
    p10: Int16Array.from([30, 300, 700, 1400]),
    p90: Int16Array.from([120, -1, -1, -1]),
    prob: Uint8Array.from([100, 80, 50, 10]),
    window: { r0: 0, r1: 0, c0: 0, c1: 3 },
    cellAreaHa: 1,
    areaHa: { min: 1, p50: 3, max: 4 },
    members: [],
    note: "",
  };
}

// 2026-07-01 12:00 MDT
const START = new Date(Date.UTC(2026, 6, 1, 18, 0));
const FRAMES = [
  frame(0, 0.25),
  frame(12, 10, { building_exposure: exposure(0, 3) }),
  frame(24, 40, { building_exposure: exposure(2, 9) }),
];

describe("UTM", () => {
  it("matches pyproj (EPSG:32612) within a metre", () => {
    // pyproj: Edmonton city hall -> (334960.27, 5935719.59)
    const u = latLngToUtm(53.5444, -113.4909);
    expect(u.zone).toBe(12);
    expect(u.band).toBe("U");
    expect(u.easting).toBeCloseTo(334960.27, 0);
    expect(u.northing).toBeCloseTo(5935719.59, 0);
    // pyproj EPSG:32611: (-120.0, 49.5) -> (282792.71, 5487366.69)
    const v = latLngToUtm(49.5, -120.0);
    expect(v.zone).toBe(11);
    expect(Math.abs(v.easting - 282792.71)).toBeLessThan(1);
    expect(Math.abs(v.northing - 5487366.69)).toBeLessThan(1);
  });
});

describe("projections", () => {
  it("frameAt picks the last frame at or before the horizon, null beyond the run", () => {
    expect(frameAt(FRAMES, 12)?.area_ha).toBe(10);
    expect(frameAt(FRAMES, 20)?.area_ha).toBe(10);
    expect(frameAt(FRAMES, 48)).toBeNull();
  });

  it("uses the ensemble P50/P10 within its duration and marks horizons beyond the run", () => {
    const p = projectAreas(FRAMES, ensemble(24 * 60), START);
    expect(p.map((x) => x.hours)).toEqual([12, 24, 48, 72]);
    expect(p[0]).toMatchObject({ modelled: true, singleHa: 10, p50Ha: 2, p10Ha: 3 });
    expect(p[1]).toMatchObject({ modelled: true, singleHa: 40, p50Ha: 2, p10Ha: 4 });
    expect(p[2]).toMatchObject({ modelled: false, singleHa: null, p50Ha: null, p10Ha: null });
    expect(p[0].at?.getTime()).toBe(START.getTime() + 12 * 3_600_000);
  });

  it("single run only without an ensemble", () => {
    const p = projectAreas(FRAMES, null, null);
    expect(p[1]).toMatchObject({ singleHa: 40, p50Ha: null, p10Ha: null, at: null });
  });

  it("structures threatened come from the single run's exposure", () => {
    expect(structuresBy(FRAMES, 24)).toEqual({ inside: 2, within100: 9 });
    expect(structuresBy([frame(1, 1)], 1)).toBeNull();
  });
});

describe("ICS Canada 209-WF report", () => {
  const html = buildICS209HTML({
    frames: FRAMES,
    burnProbData: null,
    runParams: null,
    ignitionPoint: { lat: 53.5444, lng: -113.4909 },
    run: { simulationId: "sim-123", start: START, ensemble: ensemble(24 * 60) },
    modelVersion: { version: "3.0.0", git_sha: "abc1234" },
    incidentName: "Test fire",
    generatedAt: START,
  });

  it("is the ICS Canada form, not NIMS", () => {
    expect(html).toContain("ICS Canada Form 209-WF");
    expect(html).toContain("Form-209-wf.pdf");
    expect(html).not.toMatch(/NIMS|FEMA|acres/);
  });

  it("stamps the run ID and model version", () => {
    expect(html).toContain("sim-123");
    expect(html).toContain("abc1234");
  });

  it("never fills the observed blocks 9 and 28", () => {
    const b9 = html.slice(html.indexOf("9. Status"), html.indexOf("10. Incident Complexity"));
    expect(b9).toContain("ENTER");
    expect(b9).not.toContain("checked");
    expect(b9).not.toContain("MODEL OUTPUT");
    const b28 = html.slice(html.indexOf("*28. Observed Fire Behaviour"), html.indexOf("29. Primary FBP"));
    expect(b28).toContain('contenteditable="true"></div>');
    expect(b28).not.toContain("MODEL OUTPUT");
  });

  it("labels model blocks and gives clock-time projections at 12/24/48/72 h", () => {
    for (const b of ["7. Current Incident Size", "27. Note Any Electronic Geospatial Data", "29. Primary FBP Fuel Type",
      "30. Damage Assessment", "36. Projected Incident Activity", "38. Current Incident Threat Summary", "42. Projected Final Incident Size"]) {
      const i = html.indexOf(b);
      expect(i, b).toBeGreaterThan(0);
      expect(html.slice(i, i + 300), b).toContain("MODEL OUTPUT");
    }
    // 12 h after 12:00 MDT on 1 July
    expect(html).toMatch(/(Jul 2|2 Jul) 00:00 MDT/);
    expect(html).toContain("Beyond the modelled period");
    expect(html).toContain("9 within 100 m (2 inside)");
    expect(html).toContain("O1a 75 %");
  });

  it("reports the end of a run shorter than 12 h", () => {
    const h = buildICS209HTML({
      frames: [frame(0, 0.25), frame(4, 12)], burnProbData: null, runParams: null, ignitionPoint: null,
      run: { simulationId: "s", start: START, ensemble: ensemble(240) },
    });
    expect(h).toContain("End of modelled period (T+4:00)");
    expect(h).toMatch(/16:00 MDT<\/td><td>1.0 ha<\/td><td>1.0 ha<\/td><td>12.0 ha<\/td>/);
  });

  it("says when the version endpoint was not reachable", () => {
    const h = buildICS209HTML({ frames: FRAMES, burnProbData: null, runParams: null, ignitionPoint: null, modelVersion: null });
    expect(h).toContain("unknown (version endpoint not reachable)");
    expect(h).toContain("single run (no ensemble)");
  });
});
