import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { gunzipSync } from "node:zlib";
import { describe, expect, it } from "vitest";
import { fixture } from "../test/fixture";
import type { SimulationFrame } from "../types/simulation";
import {
  ASSET_CATEGORIES,
  assetDataNotes,
  assetLabel,
  assetReach,
  assetReachPhrases,
  assetRows,
  assetsFromGeoJSON,
  assetsToMapGeoJSON,
  CATEGORY_ORDER,
  criticalReachLines,
  fireFromEnsemble,
  fireFromFrames,
  insideToleranceM,
  roadReach,
  roadRows,
  timePair,
  type Asset,
} from "./assets";
import { decodeEnsemble, type EnsembleResponse } from "./ensemble";
import { buildICS209HTML } from "./ics209";

const pub = (f: string) => JSON.parse(readFileSync(resolve(__dirname, "../../public/edmonton", f), "utf8"));
const edmontonAssets = pub("assets.geojson") as GeoJSON.FeatureCollection & { metadata: Record<string, unknown> };
const edmontonRoads = pub("roads.geojson") as GeoJSON.FeatureCollection;
const frames = fixture.frames as SimulationFrame[];
const start = new Date("2026-07-15T14:00:00-06:00");

const ensemble = decodeEnsemble(
  JSON.parse(gunzipSync(readFileSync(resolve(__dirname, "../../tests/fixtures/ensemble.json.gz"))).toString("utf8")) as EnsembleResponse,
)!;

const assets = assetsFromGeoJSON(edmontonAssets, { idPrefix: "edm:" });

describe("bundled assets.geojson (scripts/build_edmonton_assets.py output)", () => {
  it("has the common schema on every feature, from open sources", () => {
    const licences = new Set<string>();
    for (const f of edmontonAssets.features) {
      const p = f.properties as Record<string, unknown>;
      for (const k of ["name", "category", "source", "source_id", "licence", "fetched_at"]) {
        expect(typeof p[k], `${k} of ${JSON.stringify(p)}`).toBe("string");
        expect(String(p[k]).length).toBeGreaterThan(0);
      }
      expect(CATEGORY_ORDER).toContain(p.category);
      expect(p.fetched_at).toMatch(/^\d{4}-\d{2}-\d{2}$/);
      licences.add(String(p.licence));
    }
    expect([...licences].sort()).toEqual([
      "ODbL 1.0 (c) OpenStreetMap contributors",
      "Open Government Licence - Alberta",
      "Open Government Licence - Canada",
      "Open Government Licence - City of Edmonton",
    ]);
    // Source ids are unique, so assets keep their identity
    const ids = edmontonAssets.features.map((f) => f.properties?.source_id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("records every source per feature, the primary source first", () => {
    const known = new Set(Object.values(edmontonAssets.metadata.sources as Record<string, { source?: string }>).map((s) => s.source).filter(Boolean));
    known.add("City of Edmonton Open Data, Parcel Addresses (ut27-nrpn)");
    let multi = 0;
    for (const f of edmontonAssets.features) {
      const p = f.properties as Record<string, unknown>;
      const sources = p.sources as Array<{ source: string; source_id: string }>;
      expect(Array.isArray(sources), String(p.name)).toBe(true);
      expect(sources.length).toBeGreaterThan(0);
      expect(sources[0]).toMatchObject({ source: p.source, source_id: p.source_id });
      for (const s of sources) {
        expect(typeof s.source_id).toBe("string");
        expect(known.has(s.source), s.source).toBe(true);
      }
      // No source record twice on one feature
      expect(new Set(sources.map((s) => s.source_id)).size).toBe(sources.length);
      if (new Set(sources.map((s) => s.source)).size > 1) multi++;
    }
    expect(multi).toBeGreaterThan(50); // care facilities confirmed by several sources
    // The parsed assets carry the source names (primary first) and the flag
    const norwood = assets.find((a) => a.name === "Gene Zwozdesky Centre at Norwood")!;
    expect(norwood.sources).toEqual(expect.arrayContaining(["Government of Alberta continuing care list", "Statistics Canada ODHF v1.1"]));
    expect(norwood.sources[0]).toBe("Government of Alberta continuing care list");
  });

  it("care facilities: current Alberta list geocoded with City address points, ODHF and OSM merged, unconfirmed ones flagged", () => {
    const care = edmontonAssets.features.filter((f) => f.properties?.category === "hospital");
    expect(care.length).toBeGreaterThan(100);
    const meta = edmontonAssets.metadata.sources as Record<string, Record<string, unknown>>;
    const geo = meta.odhf.no_coordinates_geocoding as { records: number; in_scope: number; geocoded: number; records_detail: Array<{ outcome: string }> };
    expect(geo.records).toBe(43);
    expect(geo.geocoded / geo.in_scope).toBeGreaterThan(0.9);
    // Every ODHF record without coordinates has an outcome with its reason
    for (const r of geo.records_detail) expect(r.outcome).toMatch(/^(geocoded|unmatched|left out): /);
    // Alberta sites are positioned by a City address point, recorded as a source
    const fromAlberta = care.filter((x) => x.properties?.source === "Government of Alberta continuing care list");
    expect(fromAlberta.length).toBeGreaterThan(90);
    for (const f of fromAlberta) {
      const roles = (f.properties?.sources as Array<{ source: string }>).map((s) => s.source);
      expect(roles, String(f.properties?.name)).toContain("City of Edmonton Open Data, Parcel Addresses (ut27-nrpn)");
    }
    // Flags: in ODHF (2020) only -> possibly closed; OSM only -> verify
    for (const f of care) {
      const p = f.properties as Record<string, unknown>;
      const srcs = new Set((p.sources as Array<{ source: string }>).map((s) => s.source));
      const official = srcs.has("Government of Alberta continuing care list") || (srcs.has("Statistics Canada ODHF v1.1") && srcs.has("OpenStreetMap"));
      if (official) expect(p.verify, String(p.name)).toBeUndefined();
      else expect(String(p.verify), String(p.name)).toMatch(/^(Possibly closed, verify|OpenStreetMap only)/);
    }
    // Private day-surgery clinics are not care facilities
    expect(care.some((f) => /oral surgery|laser|dental/i.test(String(f.properties?.name)))).toBe(false);
  });

  it("has no two assets of one category with the same name within 300 m (generic de-duplication)", () => {
    const words = (s: string) => s.toLowerCase().replace(/[’']/g, "'").split(/[\s,.()/\-&:;"—–]+/).filter(Boolean);
    const initials = (s: string) => words(s).filter((w) => !["of", "the", "and", "for", "de", "la"].includes(w)).map((w) => w[0]).join("");
    const sameName = (a: string, b: string) =>
      words(a).join(" ") === words(b).join(" ") ||
      [[a, b], [b, a]].some(([x, y]) => x.split(/\s+/).some((w) => w.length >= 3 && /^[A-Z]+$/.test(w) && w.toLowerCase() === initials(y).slice(0, w.length)));
    const dup: string[] = [];
    for (let i = 0; i < assets.length; i++) {
      for (let j = i + 1; j < assets.length; j++) {
        const a = assets[i], b = assets[j];
        if (a.category !== b.category) continue;
        const kx = 111_320 * Math.cos((a.anchor[1] * Math.PI) / 180);
        const d = Math.hypot((a.anchor[0] - b.anchor[0]) * kx, (a.anchor[1] - b.anchor[1]) * 111_320);
        if (d <= 300 && sameName(a.name, b.name)) dup.push(`${a.name} / ${b.name} (${d.toFixed(0)} m)`);
      }
    }
    expect(dup).toEqual([]);
    // The RCMP K Division, listed under two names at two neighbouring addresses, is one asset
    const rcmp = assets.filter((a) => /RCMP|Royal Canadian Mounted Police/.test(`${a.name} ${a.detail}`));
    expect(rcmp).toHaveLength(1);
    expect(rcmp[0].detail).toContain("also listed as: RCMP K Division");
    // ... and the merges are logged in the metadata
    const log = (edmontonAssets.metadata.sources as Record<string, { log?: string[] }>).dedupe_same_name.log!;
    expect(log.some((l) => l.includes("Royal Canadian Mounted Police"))).toBe(true);
  });

  it("lies within the Edmonton fuel grid and is mostly points", () => {
    const b = edmontonAssets.metadata.bounds_wgs84 as { west: number; south: number; east: number; north: number };
    for (const a of assets) {
      expect(a.anchor[0]).toBeGreaterThanOrEqual(b.west);
      expect(a.anchor[0]).toBeLessThanOrEqual(b.east);
      expect(a.anchor[1]).toBeGreaterThanOrEqual(b.south);
      expect(a.anchor[1]).toBeLessThanOrEqual(b.north);
    }
    const polys = edmontonAssets.features.filter((f) => f.geometry.type !== "Point");
    expect(polys.length).toBeLessThan(10); // only the large plant sites keep their outline
    expect(assets.length).toBe(edmontonAssets.features.length);
  });

  it("covers the categories Planning asked for, with no manual points (EOC left out)", () => {
    const by = (c: string) => assets.filter((a) => a.category === c);
    for (const c of ["hospital", "fire_station", "police", "reception", "seniors", "school", "water", "power", "transit"]) {
      expect(by(c).length, c).toBeGreaterThan(0);
    }
    // The EOC is left out: no public source gives its location (owner decision 2026-10-08)
    expect(by("eoc")).toHaveLength(0);
    expect(assets.some((a) => a.source === "City of Edmonton public information")).toBe(false);
    expect(assetDataNotes(assets)[0]).toMatch(/care facilit(y is|ies are) in no current official list/);
    expect(by("water").map((a) => a.name)).toEqual(
      expect.arrayContaining(["E.L. Smith Water Treatment Plant", "Rossdale Water Treatment Plant", "Gold Bar Wastewater Treatment Plant"]),
    );
    expect(by("water").some((a) => /storm/i.test(a.name))).toBe(false);
  });
});

describe("user layers", () => {
  it("normalises points and polygons; lines are left to roads", () => {
    const fc: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      features: [
        { type: "Feature", properties: { NAME: "Depot" }, geometry: { type: "Point", coordinates: [-113.6, 53.5] } },
        { type: "Feature", properties: { name: "Yard", category: "water" }, geometry: { type: "Polygon", coordinates: [[[-113.6, 53.5], [-113.59, 53.5], [-113.59, 53.51], [-113.6, 53.51], [-113.6, 53.5]]] } },
        { type: "Feature", properties: { name: "Road" }, geometry: { type: "LineString", coordinates: [[-113.6, 53.5], [-113.5, 53.5]] } },
      ],
    };
    const a = assetsFromGeoJSON(fc, { idPrefix: "u:", category: "custom", source: "mine.geojson" });
    expect(a.map((x) => [x.name, x.category, x.source])).toEqual([
      ["Depot", "custom", "mine.geojson"],
      ["Yard", "water", "mine.geojson"],
    ]);
    expect(a[1].anchor[0]).toBeCloseTo(-113.595, 3);
  });
});

describe("asset arrival on the recorded run", () => {
  const single = fireFromFrames(frames)!;

  it("derives the cell size and the inside tolerance from the run", () => {
    expect(single.cellM).toBeCloseTo(50, 0);
    expect(insideToleranceM(single)).toBeCloseTo(35.4, 0);
  });

  it("gives the first time within 500 m and inside, consistent with the burned cells", () => {
    const reach = assetReach(single, assets);
    expect(reach.size).toBeGreaterThan(0);
    const riverview = assets.find((a) => a.name === "Riverview Substation")!;
    const r = reach.get(riverview.id)!;
    expect(r.near).not.toBeNull();
    expect(r.inside).not.toBeNull();
    expect(r.near!).toBeLessThanOrEqual(r.inside!);
    // Brute force: the earliest cell within 500 m of the substation
    const [lng, lat] = riverview.anchor;
    const kx = 111_320 * Math.cos((lat * Math.PI) / 180);
    let best = Infinity;
    for (const p of single.pts) {
      if (Math.hypot((p.lat - lat) * 111_320, (p.lng - lng) * kx) <= 500) best = Math.min(best, p.h);
    }
    expect(r.near).toBeCloseTo(best, 6);
    // Far from the fire: nothing (the asset farthest from Riverview)
    const far = assets.reduce((best, a) =>
      Math.hypot(a.anchor[0] - lng, a.anchor[1] - lat) > Math.hypot(best.anchor[0] - lng, best.anchor[1] - lat) ? a : best);
    expect(reach.has(far.id)).toBe(false);
  });

  it("measures plant sites from their outline, not their centre", () => {
    const reach = assetReach(single, assets);
    const elSmith = assets.find((a) => a.name === "E.L. Smith Water Treatment Plant")!;
    expect(elSmith.geometry.type).not.toBe("Point");
    const asPoint: Asset = { ...elSmith, id: "pt", geometry: { type: "Point", coordinates: elSmith.anchor } };
    const viaPoint = assetReach(single, [asPoint]).get("pt");
    const viaOutline = reach.get(elSmith.id);
    if (viaOutline && viaPoint) expect(viaOutline.near!).toBeLessThanOrEqual(viaPoint.near!);
    if (viaPoint) expect(viaOutline).toBeDefined();
  });

  it("perimeter-only runs count an asset swept over between frames as inside", () => {
    const ring = (h: number, d: number): SimulationFrame => ({
      time_hours: h, area_ha: 1, head_ros_m_min: 1, max_hfi_kw_m: 1, fire_type: "surface", flame_length_m: 1, fuel_breakdown: {},
      perimeter: [[53.5 - d, -113.6 - d], [53.5 - d, -113.6 + d], [53.5 + d, -113.6 + d], [53.5 + d, -113.6 - d]],
    });
    const fire = fireFromFrames([ring(0, 0.001), ring(1, 0.05)])!;
    const a: Asset = { id: "x", name: "X", category: "custom", detail: null, source: "", sources: [], licence: "", verify: null, anchor: [-113.6 + 0.02, 53.5], geometry: { type: "Point", coordinates: [-113.6 + 0.02, 53.5] } };
    expect(assetReach(fire, [a]).get("x")).toEqual({ near: 1, inside: 1 });
  });
});

describe("ensemble P10 (early) arrival", () => {
  const single = fireFromFrames(frames)!;
  const worst = fireFromEnsemble(ensemble, "p10")!;
  const median = fireFromEnsemble(ensemble, "p50")!;

  it("is no later than the median's and covers its assets", () => {
    const p10 = assetReach(worst, assets);
    const p50 = assetReach(median, assets);
    expect(p50.size).toBeGreaterThan(0);
    for (const [id, r] of p50) {
      expect(p10.has(id)).toBe(true);
      expect(p10.get(id)!.near!).toBeLessThanOrEqual(r.near! + 1e-9);
    }
  });

  it("rows lead with the P10 (early) time, the single run beside it, in clock time", () => {
    const rows = assetRows(assets, assetReach(single, assets), assetReach(worst, assets));
    expect(rows.length).toBeGreaterThan(0);
    for (let i = 1; i < rows.length; i++) expect(rows[i].first).toBeGreaterThanOrEqual(rows[i - 1].first);
    const phrases = assetReachPhrases(rows[0], start, true);
    expect(phrases[0]).toMatch(/^Fire within 500 m by (\d{2}:\d{2}|not reached) \(P10\) · (\d{2}:\d{2}|not reached) \(single run\)$/);
    // Never phrased as an instruction
    const text = criticalReachLines({ assets: rows, roads: [], hasEnsemble: true, start, bufferM: 500 }).join("\n");
    expect(text).toContain("not an instruction");
    expect(text).not.toMatch(/at.risk|protect|evacuate|≥ ?50 ?%/i);
  });

  it("single-run only: one time, and 'nothing' when not reached", () => {
    expect(timePair(null, 1.5, start, false)).toBe("15:30");
    expect(timePair(null, null, start, false)).toBeNull();
    expect(timePair(1, null, start, true)).toBe("15:00 (P10) · not reached (single run)");
  });
});

describe("major roads", () => {
  it("first reach per named road and the reached stretches", () => {
    const single = fireFromFrames(frames)!;
    const r = roadReach(single, edmontonRoads);
    expect(r.first.size).toBeGreaterThan(0);
    expect([...r.first.keys()]).toContain("Anthony Henday Drive NW");
    for (const p of r.pieces) {
      expect(r.first.get(p.name)!).toBeLessThanOrEqual(p.h);
      const [[x1, y1], [x2, y2]] = p.coords;
      const kx = 111_320 * Math.cos((y1 * Math.PI) / 180);
      expect(Math.hypot((x2 - x1) * kx, (y2 - y1) * 111_320)).toBeLessThanOrEqual(51);
    }
    // Every reached stretch has a burned cell on it
    const tol = insideToleranceM(single);
    for (const p of r.pieces.slice(0, 20)) {
      const [lng, lat] = p.coords[0];
      const kx = 111_320 * Math.cos((lat * Math.PI) / 180);
      const near = single.pts.some((q) => Math.hypot((q.lat - lat) * 111_320, (q.lng - lng) * kx) <= tol + 51);
      expect(near).toBe(true);
    }
    const rows = roadRows(r.first, null);
    expect(rows[0].first).toBe(Math.min(...r.first.values()));
  });

  it("bundled roads.geojson (scripts/build_edmonton_roads.py): motorway to secondary with ramps, names kept, ODbL", () => {
    const meta = (edmontonRoads as unknown as { metadata: Record<string, unknown> }).metadata;
    expect(meta.attribution).toMatch(/OpenStreetMap contributors, ODbL/);
    const classes = new Set(edmontonRoads.features.map((f) => f.properties?.highway));
    expect([...classes].sort()).toEqual(["motorway", "primary", "secondary", "trunk"]);
    expect(edmontonRoads.features.some((f) => f.properties?.link === 1)).toBe(true);
    // Named (or ref'd) except a few ramps joined to no named road
    const unnamed = edmontonRoads.features.filter((f) => !f.properties?.name && !f.properties?.ref);
    expect(unnamed.length).toBeLessThan(10);
    expect(edmontonRoads.features.map((f) => f.properties?.name)).toEqual(expect.arrayContaining(["Anthony Henday Drive NW", "Whitemud Drive NW"]));
  });

  it("road first-reach stays fast with the full roads file (recorded run, single + P10)", () => {
    const single = fireFromFrames(frames)!;
    const worst = fireFromEnsemble(ensemble, "p10")!;
    roadReach(single, edmontonRoads); // warm-up (JIT)
    // Median of 5 runs each
    const time = (fire: typeof single) => {
      const ms: number[] = [];
      let r = roadReach(fire, edmontonRoads);
      for (let i = 0; i < 5; i++) {
        const t = performance.now();
        r = roadReach(fire, edmontonRoads);
        ms.push(performance.now() - t);
      }
      return { ms: ms.sort((x, y) => x - y)[2], roads: r.first.size };
    };
    const a = time(single);
    const b = time(worst);
    console.log(`[perf] roadReach on ${edmontonRoads.features.length} road features (median of 5): single run ${a.ms.toFixed(1)} ms (${a.roads} roads), P10 ${b.ms.toFixed(1)} ms (${b.roads} roads)`);
    // Budget: well under a frame-blocking second, with headroom for slow CI runners
    expect(a.ms).toBeLessThan(500);
    expect(b.ms).toBeLessThan(500);
    expect(a.roads).toBeGreaterThan(0);
  });

  it("skips minor OSM classes but keeps unclassified user lines", () => {
    const single = fireFromFrames(frames)!;
    const [lng, lat] = [single.pts[0].lng, single.pts[0].lat];
    const line = (props: Record<string, unknown>): GeoJSON.Feature => ({
      type: "Feature", properties: props, geometry: { type: "LineString", coordinates: [[lng - 0.001, lat], [lng + 0.001, lat]] },
    });
    const fc: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [line({ name: "Lane", highway: "residential" }), line({ name: "Mine" }), line({ name: "Sec", highway: "secondary" })] };
    expect([...roadReach(single, fc).first.keys()].sort()).toEqual(["Mine", "Sec"]);
  });
});

describe("ICS-209: burn probability labelled as model output; flagged assets labelled", () => {
  it("labels the P >= 25/50/75 % table and keeps data-quality flags", () => {
    const flagged = assets.find((a) => a.verify)!;
    const row = { asset: flagged, single: { near: 1, inside: null }, worst: null, first: 1 };
    const html = buildICS209HTML({
      frames,
      burnProbData: {
        burn_probability: [[0.8, 0.6], [0.3, 0]], rows: 2, cols: 2, lat_min: 53.4, lat_max: 53.41, lng_min: -113.6, lng_max: -113.59,
        n_iterations: 50, iterations_completed: 50, cell_size_m: 100,
      },
      runParams: null,
      ignitionPoint: null,
      criticalReach: { assets: [row], roads: [], hasEnsemble: false, start, bufferM: 500 },
    });
    expect(html).toContain("BURN PROBABILITY (MONTE CARLO/ENSEMBLE), MODEL OUTPUT");
    expect(html).toContain("P ≥ 50% (Probable)"); // the table itself is unchanged
    expect(html).toContain(assetLabel(flagged));
  });
});

describe("map data", () => {
  it("one symbol per shown asset, labelled only when reached", () => {
    const single = fireFromFrames(frames)!;
    const rows = assetRows(assets, assetReach(single, assets), null);
    const fc = assetsToMapGeoJSON(assets, rows, new Set(CATEGORY_ORDER), start, false);
    expect(fc.features).toHaveLength(assets.length);
    const labelled = fc.features.filter((f) => f.properties?.label);
    expect(labelled).toHaveLength(rows.length);
    for (const f of labelled) {
      expect(f.properties?.reached).toBe(1);
      expect(f.properties?.icon).toMatch(/-reached$/);
      expect(String(f.properties?.label)).toMatch(/ · 500 m by \d{2}:\d{2}$/);
    }
    const onlySchools = assetsToMapGeoJSON(assets, rows, new Set(["school"] as const), start, false);
    expect(onlySchools.features.every((f) => f.properties?.category === "school")).toBe(true);
  });

  it("every category has a distinct shape + letter symbol", () => {
    const keys = CATEGORY_ORDER.map((c) => `${ASSET_CATEGORIES[c].shape}:${ASSET_CATEGORIES[c].letter}`);
    expect(new Set(keys).size).toBe(keys.length);
  });
});
