/**
 * Exercise mode PR2 (docs/exercise-mode-spec.md §5, §12): scenario pack → Setup mapping,
 * operational-period boundaries, injects, aliases and file checks, on the repository's two
 * example packs (data/exercise-packs/, owner values 2026-10-30).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { ScenarioPack } from "../types/exercise";
import { applyPlaceAliases, checkPack, packClock, packFileName, packIncidentPeriods, packInjectLog, packLoadPlan, parsePack, rainBetween, stepAt } from "./packs";
import { scheduleRequest } from "../utils/weatherSchedule";
import { exerciseClockReducer, EXERCISE_CLOCK_OFF } from "./clock";

const H = 3_600_000;
const read = (f: string) => readFileSync(resolve(__dirname, "../../../data/exercise-packs", f), "utf8");
const terwillegar = (): ScenarioPack => JSON.parse(read("edmonton-terwillegar.json"));
const ridgewater = (): ScenarioPack => JSON.parse(read("ab-ridgewater.json"));
const NOW = "2026-10-30T12:00:00.000Z";

describe("pack → Setup mapping", () => {
  it("Terwillegar: ignition, start, codes, curing, City grid, single 24 h run with the scripted wind rise", () => {
    const plan = packLoadPlan(terwillegar(), 1, NOW);
    const c = plan.config;
    expect(plan.ignition).toEqual({ lat: 53.4606, lng: -113.6596 });
    expect(c.ignitionPoint).toEqual(plan.ignition);
    expect(c.startIso).toBe("2026-05-15T14:00:00-06:00");
    expect(c.fwi).toEqual({ ffmc: 92, dmc: 40, dc: 300 });
    expect(c.grassCure).toBe(80);
    expect(c.weather).toEqual({ wind_speed: 20, wind_direction: 225, temperature: 25, relative_humidity: 30, precipitation_24h: 0 });
    expect(c.useEdmontonGrid).toBe(true);
    expect(c.useSyntheticCA).toBe(false);
    expect(c.simMode).toBe("single");
    expect(c.durationHours).toBe(24);
    // never live forecast weather in an exercise; ensembles and the unsourced WUI layer off
    expect(c.useHourlyForecast).toBe(false);
    expect(c.ensembleOn).toBe(false);
    expect(c.includeWUI).toBe(false);
    // the script's T+3 h rise to 35 km/h is a Setup weather change at 17:00 ...
    expect(c.weatherSchedule).toEqual([
      { time: "17:00", wind_speed: 35, wind_direction: 225, temperature: 25, relative_humidity: 30, precipitation_mm_h: 0 },
    ]);
    // ... which the app sends as weather_schedule hours_from_start 3
    expect(scheduleRequest(c.weatherSchedule!, Date.parse(c.startIso!))).toEqual([
      { hours_from_start: 3, wind_speed: 35, wind_direction: 225, temperature: 25, relative_humidity: 30, precipitation_mm_h: 0 },
    ]);
    expect(c.id).toBe("exercise-pack:example-edmonton-terwillegar:1");
    expect(plan.notes).toEqual([]);
  });

  it("Ridgewater: 36 h runs as 2 days; the T+24 h shift falls on the day boundary, so day 2 has it at its time", () => {
    const plan = packLoadPlan(ridgewater(), 2, NOW);
    const c = plan.config;
    expect(plan.ignition).toEqual({ lat: 55.2368, lng: -112.5389 });
    expect(c.fwi).toEqual({ ffmc: 90, dmc: 25, dc: 150 });
    expect(c.grassCure).toBeNull(); // the date-aware default
    expect(c.useEdmontonGrid).toBe(true); // City where mapped → Alberta grid (none here)
    expect(c.simMode).toBe("multiday");
    expect(c.weatherSchedule).toEqual([]);
    expect(c.multiDayDays).toEqual([
      { wind_speed: 15, wind_direction: 135, temperature: 22, relative_humidity: 25, precipitation_24h: 0 },
      { wind_speed: 30, wind_direction: 315, temperature: 22, relative_humidity: 20, precipitation_24h: 0 },
    ]);
    expect(plan.notes.join(" ")).toMatch(/2-day run/);
    expect(plan.notes.join(" ")).not.toMatch(/reaches the model/);
    // a step inside a later day would be reported
    const off = ridgewater();
    off.weatherScript[1].hoursFromStart = 20;
    expect(packLoadPlan(off).notes.join(" ")).toMatch(/T\+20:00 reaches the model at T\+24:00/);
  });

  it("uniform fuel turns the grid off and sets the fuel type", () => {
    const p = terwillegar();
    p.fuel = { source: "uniform", uniformFuelType: "O1b" };
    const c = packLoadPlan(p).config;
    expect(c.useEdmontonGrid).toBe(false);
    expect(c.fuelType).toBe("O1b");
  });

  it("step lookup and rain over a span", () => {
    const s = ridgewater().weatherScript;
    expect(stepAt(s, 23.99).windDirectionDeg).toBe(135);
    expect(stepAt(s, 24).windDirectionDeg).toBe(315);
    const wet = [
      { ...s[0], precipitationMmH: 9 },
      { ...s[1], hoursFromStart: 10, precipitationMmH: 2 },
    ];
    expect(rainBetween(wet, 0, 24)).toBe(28); // the first step's rate is not rain (its 24 h total is)
  });
});

describe("period boundaries and the clock span", () => {
  it("Terwillegar: span 24 h, boundaries at 20:00 and 08:00 (T+6 h, T+18 h)", () => {
    const c = packClock(terwillegar());
    expect(c.startMs).toBe(Date.parse("2026-05-15T20:00:00Z"));
    expect(c.endMs - c.startMs).toBe(24 * H);
    expect(c.boundaries.map((b) => (b - c.startMs) / H)).toEqual([6, 18]);
  });

  it("Ridgewater: span 36 h, two inner boundaries every 12 h", () => {
    const c = packClock(ridgewater());
    expect(c.endMs - c.startMs).toBe(36 * H);
    expect(c.boundaries.map((b) => (b - c.startMs) / H)).toEqual([12, 24]);
  });

  it("incident periods in Edmonton wall time (an end before the start is the next day)", () => {
    expect(packIncidentPeriods(terwillegar())).toEqual([
      { day: 1, date: "2026-05-15", opPeriodStart: "14:00", opPeriodEnd: "20:00" },
      { day: 2, date: "2026-05-15", opPeriodStart: "20:00", opPeriodEnd: "08:00" },
      { day: 3, date: "2026-05-16", opPeriodStart: "08:00", opPeriodEnd: "14:00" },
    ]);
    expect(packIncidentPeriods(ridgewater()).map((p) => `${p.date} ${p.opPeriodStart}-${p.opPeriodEnd}`)).toEqual([
      "2026-05-05 13:00-01:00", "2026-05-06 01:00-13:00", "2026-05-06 13:00-01:00",
    ]);
  });

  it("the clock enabled from the plan starts paused at the pack start; Next period stops at each boundary, then the end", () => {
    const { clock } = packLoadPlan(ridgewater());
    let s = exerciseClockReducer(EXERCISE_CLOCK_OFF, { type: "enable", startMs: clock.startMs, endMs: clock.endMs, periodBoundariesMs: clock.boundaries });
    expect(s.mode).toBe("paused");
    expect(s.exerciseMs).toBe(clock.startMs);
    const stops: number[] = [];
    for (let i = 0; i < 3; i++) {
      s = exerciseClockReducer(s, { type: "nextPeriod" });
      stops.push((s.exerciseMs! - clock.startMs) / H);
    }
    expect(stops).toEqual([12, 24, 36]);
  });
});

describe("scheduled injects", () => {
  it("absolute exercise times, scheduled, in order; a perimeter inject carries its observed time", () => {
    const log = packInjectLog(ridgewater(), NOW);
    const start = Date.parse("2026-05-05T13:00:00-06:00");
    expect(log.injects.map((i) => [i.kind, (i.exerciseTimeMs - start) / H, i.status])).toEqual([
      ["wind_shift", 24, "scheduled"], ["spot_fire", 26, "scheduled"], ["perimeter", 30, "scheduled"],
    ]);
    const per = log.injects[2].payload;
    expect(per.kind === "perimeter" && per.observedAtIso).toBe("2026-05-06T19:00:00-06:00");
    expect(log.injects.every((i) => i.createdAtIso === NOW && i.deliveredAtMs === null)).toBe(true);
  });

  it("an ignition after 0 h becomes a spot-fire inject", () => {
    const p = terwillegar();
    p.ignitions.push({ lat: 53.47, lng: -113.65, hoursFromStart: 5 });
    const log = packInjectLog(p, NOW);
    expect(log.injects.map((i) => i.kind)).toEqual(["perimeter", "resource", "spot_fire"]);
    expect(packLoadPlan(p).notes).toContain("Later ignitions are scheduled as spot-fire injects.");
  });

  it("no inject can carry an evacuation tier (closed kinds)", () => {
    for (const p of [terwillegar(), ridgewater()]) {
      for (const i of packInjectLog(p).injects) {
        expect(["perimeter", "spot_fire", "wind_shift", "resource", "note"]).toContain(i.kind);
        expect(JSON.stringify(i).toLowerCase()).not.toMatch(/tier|order|alert|watch|address/);
      }
    }
  });
});

describe("files, marking and aliases", () => {
  it("parses the example files; refuses non-packs, unmarked packs and oversize text", () => {
    expect(parsePack(read("edmonton-terwillegar.json")).pack?.title).toMatch(/^EXERCISE/);
    expect(parsePack("{").error).toBe("Not a JSON file");
    expect(checkPack({ ...terwillegar(), schema: "x" }).error).toMatch(/schema/);
    expect(checkPack({ ...terwillegar(), marking: "LIVE" }).error).toMatch(/EXERCISE/);
    expect(checkPack({ ...terwillegar(), durationHours: 80 }).error).toMatch(/72/);
    expect(checkPack({ ...terwillegar(), startIso: "2026-07-15T14:00:00" }).error).toMatch(/offset/);
    expect(checkPack({ ...terwillegar(), ignitions: [{ lat: 53, lng: -113, hoursFromStart: 1 }] }).error).toMatch(/ignition/);
    expect(parsePack(JSON.stringify({ ...terwillegar(), pad: "x".repeat(2 * 1024 * 1024) })).error).toMatch(/2 MB/);
  });

  it("export file names are marked EXERCISE", () => {
    expect(packFileName(terwillegar())).toBe("EXERCISE-pack-terwillegar-park-grass-fire-edmonton-river-valley.json");
  });

  it("a pack alias renames the community (fictional name on real geography) and drops its parent", () => {
    const fc: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      features: [
        { type: "Feature", geometry: null as unknown as GeoJSON.Geometry, properties: { name: "Wandering River (hamlet, Athabasca County)", place: "Wandering River", parent: "Athabasca County" } },
        { type: "Feature", geometry: null as unknown as GeoJSON.Geometry, properties: { name: "Athabasca County", level: "csd" } },
      ],
    };
    const out = applyPlaceAliases(fc, ridgewater().placeAliases)!;
    expect(out.features[0].properties).toMatchObject({ name: "Hamlet of Ridgewater (fictional)", parent: null });
    expect(out.features[1]).toBe(fc.features[1]);
    expect(applyPlaceAliases(fc, [])).toBe(fc);
  });
});
