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

// ── Round-3 field test (Claude in Chrome tester, 2026-10-13) ─────────────────

import { buildFullIAPHTML, buildICS201HTML, buildICS202HTML, buildICS204HTML, weatherOutlookRows } from "./icsForms";
import { runParamsFor } from "./fwiCalc";

const run = (hfi: number, areaHa = 409.5) => ({
  incidentName: "Test",
  frames: [frame(4, square(0.01), { max_hfi_kw_m: hfi, head_ros_m_min: 12, area_ha: areaHa, fire_type: "ACTIVE_CROWN" })],
  runParams: runParamsFor(
    { wind_speed: 20, wind_direction: 270, temperature: 25, relative_humidity: 30, precipitation_24h: 0 },
    { ffmc: 71.7629123990164, dmc: 45, dc: 300 }, 4, "C2", null, false,
  ),
  ignitionPoint: { lat: 53.5, lng: -113.5 },
});

describe("IAP objectives and tactics (R4; round-2 M9)", () => {
  it("leaves objectives to Planning: ENTER lines, then model context with the shown area", () => {
    for (const html of [buildICS201HTML(run(6000)), buildICS202HTML(run(6000)), buildFullIAPHTML(run(6000))]) {
      expect(html).not.toMatch(/Contain fire within/);
      expect(html).not.toMatch(/491 ha/); // 409.5 x 1.2, the old generated objective
      expect(html).not.toMatch(/Execute .* tactics/);
      expect(html).toContain("ENTER: objective (set by Planning / IC)");
      expect(html).toContain("modelled area at the end of the run 409.5 ha, no suppression (model output, not an objective)");
    }
  });

  it("writes no command emphasis, strategy, withdrawal or resource order", () => {
    const html = buildFullIAPHTML(run(15000));
    expect(html).not.toMatch(/Aggressive initial attack authorized/);
    expect(html).not.toMatch(/Defensive posture/);
    expect(html).not.toMatch(/withdraw all resources/i);
    expect(html).not.toMatch(/Suppression Strategy/);
    expect(html).toContain("ENTER: command emphasis (IC)");
    expect(html).toContain("ENTER: resources assigned (Operations)");
    expect(html).toContain("Model context (not a resource order)");
    expect(buildICS204HTML(run(15000))).toContain("Model context (not a strategy)");
  });

  it("says when there is no run, without generated objectives", () => {
    const html = buildICS202HTML({ incidentName: "T", frames: [], runParams: null, ignitionPoint: null });
    expect(html).toContain("ENTER: objective (set by Planning / IC)");
    expect(html).not.toMatch(/Run simulation to generate/);
  });
});

describe("weather outlook from the run inputs (R5, R10)", () => {
  it("pre-fills the run's weather, labelled as run inputs, codes to one decimal", () => {
    const rows = weatherOutlookRows(run(600).runParams);
    expect(rows[0]).toEqual(["Source", "FireSim run inputs (not a forecast)"]);
    expect(rows).toContainEqual(["Wind", "20 km/h W"]);
    expect(rows).toContainEqual(["FFMC / DMC / DC", "71.8 / 45 / 300"]);
    expect(rows.at(-1)).toEqual(["Forecast synopsis", "ENTER (from the forecast)"]);
    const html = buildICS202HTML(run(600));
    expect(html).not.toMatch(/Weather not yet entered/);
    expect(html).not.toMatch(/71\.76/);
  });

  it("lists each day of a multi-day run", () => {
    const rp = {
      ...run(600).runParams,
      multi_day_days: [
        { wind_speed: 20, wind_direction: 270, temperature: 25, relative_humidity: 30, precipitation_24h: 0 },
        { wind_speed: 30, wind_direction: 260, temperature: 30, relative_humidity: 20, precipitation_24h: 0 },
      ],
    };
    const rows = weatherOutlookRows(rp);
    expect(rows).toContainEqual(["Day 2 (daily weather)", "30 km/h W · 30°C · RH 20% · 0 mm"]);
    expect(rows.find((r) => r[0] === "Starting FFMC / DMC / DC")?.[1]).toContain("carried over");
  });

  it("asks for the forecast when the period has no run", () => {
    expect(weatherOutlookRows(null)).toEqual([["Forecast synopsis", "ENTER (no FireSim run for this operational period)"]]);
  });
});

describe("modelled period beside the operational period (R11)", () => {
  it("adds the run's span to the incident block", () => {
    const html = buildICS202HTML({ ...run(600), operationalPeriod: "Day 1 · 2026-10-13 08:00–20:00", runSpan: "Tue, 13 Oct 04:57 to Fri, 16 Oct 04:57 MDT (72 h)" });
    expect(html).toContain("Modelled period (FireSim run)");
    expect(html).toContain("Fri, 16 Oct 04:57 MDT (72 h)");
  });
});
