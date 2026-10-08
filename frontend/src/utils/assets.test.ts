import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { gunzipSync } from "node:zlib";
import { describe, expect, it } from "vitest";
import { fixture } from "../test/fixture";
import type { SimulationFrame } from "../types/simulation";
import {
  ASSET_CATEGORIES,
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
      "Open Government Licence - Canada",
      "Open Government Licence - City of Edmonton",
      "Public information (no dataset)",
    ]);
    // Source ids are unique, so assets keep their identity
    const ids = edmontonAssets.features.map((f) => f.properties?.source_id);
    expect(new Set(ids).size).toBe(ids.length);
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

  it("covers the categories Planning asked for, with the EOC as the one manual point", () => {
    const by = (c: string) => assets.filter((a) => a.category === c);
    for (const c of ["hospital", "fire_station", "police", "reception", "seniors", "school", "water", "power", "transit"]) {
      expect(by(c).length, c).toBeGreaterThan(0);
    }
    expect(by("eoc")).toHaveLength(1);
    expect(by("eoc")[0].source).toBe("City of Edmonton public information");
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
    // Far from the fire: nothing
    const far = assets.find((a) => a.category === "eoc")!;
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
    const a: Asset = { id: "x", name: "X", category: "custom", detail: null, source: "", licence: "", anchor: [-113.6 + 0.02, 53.5], geometry: { type: "Point", coordinates: [-113.6 + 0.02, 53.5] } };
    expect(assetReach(fire, [a]).get("x")).toEqual({ near: 1, inside: 1 });
  });
});

describe("worst-credible (ensemble P10) arrival", () => {
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

  it("rows lead with the worst-credible time, the single run beside it, in clock time", () => {
    const rows = assetRows(assets, assetReach(single, assets), assetReach(worst, assets));
    expect(rows.length).toBeGreaterThan(0);
    for (let i = 1; i < rows.length; i++) expect(rows[i].first).toBeGreaterThanOrEqual(rows[i - 1].first);
    const phrases = assetReachPhrases(rows[0], start, true);
    expect(phrases[0]).toMatch(/^Fire within 500 m by (\d{2}:\d{2}|not reached) \(worst-credible\) · (\d{2}:\d{2}|not reached) \(single run\)$/);
    // Never phrased as an instruction
    const text = criticalReachLines({ assets: rows, roads: [], hasEnsemble: true, start, bufferM: 500 }).join("\n");
    expect(text).toContain("not an instruction");
    expect(text).not.toMatch(/at.risk|protect|evacuate|≥ ?50 ?%/i);
  });

  it("single-run only: one time, and 'nothing' when not reached", () => {
    expect(timePair(null, 1.5, start, false)).toBe("15:30");
    expect(timePair(null, null, start, false)).toBeNull();
    expect(timePair(1, null, start, true)).toBe("15:00 (worst-credible) · not reached (single run)");
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
